import numpy as np
from torchreid.utils import FeatureExtractor

from embedding.embedder import Embedder
from DataClass.types import TrackingResult, EmbeddingResult, Embedding


class OSNetEmbedder(Embedder):

    def __init__(self, model_name="osnet_x1_0", model_path="", device="cpu"):

        self.extractor = FeatureExtractor(
            model_name=model_name,
            model_path=model_path,   # empty string = auto-download pretrained weights
            device=device
        )

    def extract(self, trackingResult: TrackingResult) -> EmbeddingResult:
        return self.extract_batch([trackingResult])[0]

    @staticmethod
    def _crops_for(trackingResult: TrackingResult):

        frame_image = trackingResult.frame.frame

        crops = []
        valid_tracks = []

        for track in trackingResult.tracks:

            x1, y1, x2, y2 = map(int, track.detection.bbox)

            x1, y1 = max(x1, 0), max(y1, 0)
            x2 = min(x2, frame_image.shape[1])
            y2 = min(y2, frame_image.shape[0])

            crop = frame_image[y1:y2, x1:x2]

            if crop.size > 0:
                crops.append(crop)
                valid_tracks.append(track)

        return crops, valid_tracks

    def extract_batch(self, trackingResults: list[TrackingResult]) -> list[EmbeddingResult]:
        """
        Pools person crops from every camera's TrackingResult into ONE
        extractor() call instead of one call per camera, then splits the
        features back out per source frame.
        """

        if not trackingResults:
            return []

        all_crops = []
        # owner[i] = index into trackingResults that all_crops[i] belongs to
        owner = []
        per_source_tracks = []

        for i, trackingResult in enumerate(trackingResults):

            crops, valid_tracks = self._crops_for(trackingResult)
            per_source_tracks.append(valid_tracks)

            all_crops.extend(crops)
            owner.extend([i] * len(crops))

        results = [
            EmbeddingResult(frame=tr.frame, embeddings=[])
            for tr in trackingResults
        ]

        if not all_crops:
            return results

        features = self.extractor(all_crops)
        features = features.cpu().numpy()

        # owner is non-decreasing (crops were appended source by source),
        # so walking it in order pairs each feature with the right track
        # from its source's valid_tracks list.
        cursor = {i: 0 for i in range(len(trackingResults))}

        for crop, source_idx, vector in zip(all_crops, owner, features):

            track = per_source_tracks[source_idx][cursor[source_idx]]
            cursor[source_idx] += 1

            results[source_idx].embeddings.append(
                Embedding(
                    track_id=track.track_id,
                    vector=vector,
                    crop=crop
                )
            )

        return results