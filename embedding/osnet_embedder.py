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

        embeddings = []

        if crops:

            features = self.extractor(crops)
            features = features.cpu().numpy()

            for track, vector in zip(valid_tracks, features):

                embeddings.append(
                    Embedding(
                        track_id=track.track_id,
                        vector=vector,
                        crop = crop
                    )
                )

        return EmbeddingResult(
            frame=trackingResult.frame,
            embeddings=embeddings
        )