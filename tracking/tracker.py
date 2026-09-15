from abc import ABC,abstractmethod

from DataClass.types import TrackingResult,DetectionResult


class Tracker(ABC):

    @abstractmethod
    def update(self,detectionResult: DetectionResult) -> TrackingResult:
        pass
    