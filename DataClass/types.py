from dataclasses import dataclass
import numpy as np


@dataclass
class Frame:
    camera_id: str
    timestamp: float
    frame: np.ndarray

@dataclass
class Detection:
    bbox: tuple[int, int, int, int]
    class_id: int
    confidence: float

@dataclass
class DetectionResult:
    frame: Frame
    detections: list[Detection]

@dataclass
class Track:
    track_id: int
    detection: Detection

@dataclass
class TrackingResult:
    frame: Frame
    tracks: list[Track]

@dataclass
class Embedding:
    track_id: int
    vector: np.ndarray
    crop: np.ndarray

@dataclass
class EmbeddingResult:
    frame: Frame
    embeddings: list[Embedding]