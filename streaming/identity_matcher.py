"""
Laptop-side live re-id: periodically pulls known people + their mean
OSNet embedding from the Pi5 relay's SQLite-backed identity store (over
plain HTTP - the DB itself never leaves the Pi5, see
streaming/identity_store.py), and matches new track embeddings against
them by cosine similarity. A match means capture_node.py can put a real
name on an alert instead of "Person #<track_id>", and can tag the crop
it sends up as already-belonging-to that person instead of dropping it
in the unassigned review queue every time.
"""

import logging
import time

import numpy as np
import requests

logger = logging.getLogger("guardsense.reid")


class IdentityMatcher:

    def __init__(self, relay_http_url, ingest_token, threshold=0.6, refresh_interval=15.0):
        self.embeddings_url = f"{relay_http_url}/api/internal/people-embeddings"
        self.ingest_token = ingest_token
        self.threshold = threshold
        self.refresh_interval = refresh_interval

        self.people = []  # [{"id", "name", "embedding": np.ndarray}]
        self.last_refresh_at = 0.0

    def refresh(self):
        try:
            response = requests.get(
                self.embeddings_url,
                params={"token": self.ingest_token},
                timeout=5,
            )
            response.raise_for_status()
            people = response.json()["people"]

            self.people = [
                {"id": p["id"], "name": p["name"], "embedding": np.asarray(p["embedding"], dtype=np.float32)}
                for p in people
            ]
        except Exception as e:
            logger.warning("refresh failed (%s), keeping last known %d people", e, len(self.people))
        finally:
            self.last_refresh_at = time.time()

    def refresh_if_due(self):
        if time.time() - self.last_refresh_at >= self.refresh_interval:
            self.refresh()

    def match(self, vector):
        """Returns (person_id, name) for the best match above threshold,
        or (None, None) if nobody known matches closely enough."""

        if not self.people:
            return None, None

        vector = np.asarray(vector, dtype=np.float32)
        vector_norm = np.linalg.norm(vector)
        if vector_norm == 0:
            return None, None

        best_id, best_name, best_sim = None, None, self.threshold

        for person in self.people:
            other = person["embedding"]
            other_norm = np.linalg.norm(other)
            if other_norm == 0:
                continue

            similarity = float(np.dot(vector, other) / (vector_norm * other_norm))

            if similarity > best_sim:
                best_id, best_name, best_sim = person["id"], person["name"], similarity

        return best_id, best_name
