
from abc import ABC,abstractmethod

from DataClass.types import Frame
from DataClass.types import DetectionResult

class Detector(ABC):

    @abstractmethod
    def detect(self,frame: Frame) -> DetectionResult:
        pass