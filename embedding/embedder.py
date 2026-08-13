from abc import ABC, abstractmethod
from DataClass.types import TrackingResult, EmbeddingResult


class Embedder(ABC):

    @abstractmethod
    def extract(self, trackingResult: TrackingResult) -> EmbeddingResult:
        ...