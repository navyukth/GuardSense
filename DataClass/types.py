from dataclasses import dataclass
import numpy as np


@dataclass
class Frame:
    camera_id:str
    timestamp:float
    frame : np.ndarray

@dataclass
class Detection:
    bbox: tuple[int, int, int, int]
    class_id: int
    confidence: float
    


@dataclass
class DetectionResult:
    frame:Frame
    detections: list[Detection]
