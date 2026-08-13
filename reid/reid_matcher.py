from collections import Counter

import numpy as np

from DataClass.types import EmbeddingResult
from database.db import GuardSenseDB


class ReIDMatcher:

    # How many embeddings to collect for a brand-new track before
    # committing to an identity. A single noisy/occluded frame can't
    # lock in the wrong person anymore — it has to be outvoted.
    VOTE_BUFFER_SIZE = 3

    # Minimum agreement needed among the buffered candidates to treat
    # them as "the same known person" rather than a new person.
    VOTE_MAJORITY_THRESHOLD = 2

    def __init__(self, db: GuardSenseDB, threshold: float = 0.65):

        self.db = db
        self.threshold = threshold

        # In-memory gallery: person_id -> running average ("centroid") vector
        self.centroids = {}

        # How many embeddings have gone into each centroid so far
        self.counts = {}

        # Per-track voting buffers, held until VOTE_BUFFER_SIZE is reached:
        # track_id -> list of {vector, crop, camera_id, ts, candidate, score}
        self.pending = {}

        self._load_gallery()

    def _load_gallery(self):

        self.centroids = {}
        self.counts = {}

        sums = {}

        for person_id, vector in self.db.get_all_embeddings():

            if person_id not in sums:
                sums[person_id] = np.zeros_like(vector)
                self.counts[person_id] = 0

            sums[person_id] += vector
            self.counts[person_id] += 1

        for person_id, total in sums.items():
            self.centroids[person_id] = total / self.counts[person_id]

        print(
            f"ReIDMatcher: loaded {len(self.centroids)} known person(s), "
            f"{sum(self.counts.values())} total embeddings"
        )

    @staticmethod
    def _cosine_sim(a, b):
        a_norm = a / (np.linalg.norm(a) + 1e-8)
        b_norm = b / (np.linalg.norm(b) + 1e-8)
        return float(np.dot(a_norm, b_norm))

    def _best_match(self, vector):

        best_person_id = None
        best_score = -1.0

        for person_id, centroid in self.centroids.items():

            score = self._cosine_sim(vector, centroid)

            if score > best_score:
                best_score = score
                best_person_id = person_id

        return best_person_id, best_score

    def _update_centroid(self, person_id, vector):

        if person_id not in self.centroids:
            self.centroids[person_id] = vector.copy()
            self.counts[person_id] = 1
            return

        n = self.counts[person_id]
        current = self.centroids[person_id]

        updated = (current * n + vector) / (n + 1)

        self.centroids[person_id] = updated
        self.counts[person_id] = n + 1

    def _commit_buffer(self, track_id):
        """
        Called once a track's voting buffer is full. Decides the identity
        by majority vote across the buffered candidates, then writes all
        buffered embeddings/crops to the DB under that decision.
        """

        buffer = self.pending.pop(track_id)

        candidates = [entry["candidate"] for entry in buffer if entry["candidate"] is not None]

        vote_counts = Counter(candidates)

        top_person_id = None
        top_count = 0

        if vote_counts:
            top_person_id, top_count = vote_counts.most_common(1)[0]

        if top_person_id is not None and top_count >= self.VOTE_MAJORITY_THRESHOLD:

            person_id = top_person_id

            print(
                f"REID: buffer resolved by majority vote — track_id={track_id} "
                f"-> person_id={person_id} ({top_count}/{len(buffer)} agreed)"
            )

        else:

            # No clear agreement — treat as a new person. Using all
            # buffered samples (not just one) makes the resulting
            # centroid more stable from the start.
            person_id = self.db.create_person(buffer[-1]["ts"])

            print(
                f"REID: buffer had no majority — track_id={track_id} "
                f"-> new person_id={person_id} "
                f"(candidates were: {[c for c in candidates] or 'none above threshold'})"
            )

        for entry in buffer:

            self.db.add_embedding(
                person_id=person_id,
                track_id=track_id,
                camera_id=entry["camera_id"],
                vector=entry["vector"],
                crop=entry["crop"],
                ts=entry["ts"]
            )

            self._update_centroid(person_id, entry["vector"])

        return person_id

    def match(self, embeddingResult: EmbeddingResult, track_hints: dict = None) -> dict:
        """
        Matches every embedding in this frame.

        - If a track_id already has a hint (resolved earlier in this same
          continuous track), reuse that person_id directly — no re-search.
        - Otherwise, the embedding goes into a per-track voting buffer.
          Once VOTE_BUFFER_SIZE samples have been collected for that
          track, identity is decided by majority vote and committed to
          the DB all at once. Until the buffer fills, that track_id is
          NOT included in the returned dict (caller should show it as
          "still identifying").

        Returns {track_id: person_id} — only for tracks that are either
        sticky-known or just got their buffer committed this call.
        """

        track_hints = track_hints or {}

        ts = embeddingResult.frame.timestamp
        camera_id = embeddingResult.frame.camera_id

        result = {}

        for embedding in embeddingResult.embeddings:

            hinted_person_id = track_hints.get(embedding.track_id)

            if hinted_person_id is not None:

                person_id = hinted_person_id

                self.db.add_embedding(
                    person_id=person_id,
                    track_id=embedding.track_id,
                    camera_id=camera_id,
                    vector=embedding.vector,
                    crop=embedding.crop,
                    ts=ts
                )

                self._update_centroid(person_id, embedding.vector)

                print(
                    f"REID [{camera_id}]: sticky — track_id={embedding.track_id} "
                    f"stays person_id={person_id}"
                )

                result[embedding.track_id] = person_id

                continue

            # Not yet identified — goes into the voting buffer
            candidate_person_id, score = self._best_match(embedding.vector)

            candidate = candidate_person_id if score >= self.threshold else None

            buffer = self.pending.setdefault(embedding.track_id, [])

            buffer.append({
                "vector": embedding.vector,
                "crop": embedding.crop,
                "camera_id": camera_id,
                "ts": ts,
                "candidate": candidate,
                "score": score
            })

            print(
                f"REID [{camera_id}]: buffering — track_id={embedding.track_id} "
                f"sample {len(buffer)}/{self.VOTE_BUFFER_SIZE} "
                f"(candidate={candidate}, score={score:.3f})"
            )

            if len(buffer) >= self.VOTE_BUFFER_SIZE:

                person_id = self._commit_buffer(embedding.track_id)
                result[embedding.track_id] = person_id

        return result

    def merge(self, source_person_id, target_person_id):
        """
        Call this whenever the frontend confirms two person_ids are actually
        the same person. Merges the DB rows AND the in-memory centroids, so
        the fix takes effect immediately without needing a restart.
        """

        if source_person_id == target_person_id:
            return

        moved = self.db.merge_persons(source_person_id, target_person_id)

        source_centroid = self.centroids.pop(source_person_id, None)
        source_count = self.counts.pop(source_person_id, 0)

        if source_centroid is not None:

            target_centroid = self.centroids.get(target_person_id)
            target_count = self.counts.get(target_person_id, 0)

            if target_centroid is None:

                self.centroids[target_person_id] = source_centroid
                self.counts[target_person_id] = source_count

            else:

                total_count = target_count + source_count

                merged = (
                    (target_centroid * target_count) +
                    (source_centroid * source_count)
                ) / total_count

                self.centroids[target_person_id] = merged
                self.counts[target_person_id] = total_count

        print(
            f"REID: merged person_id={source_person_id} into "
            f"person_id={target_person_id} ({moved} embeddings moved)"
        )