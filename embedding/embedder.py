from abc import ABC, abstractmethod
from DataClass.types import TrackingResult, EmbeddingResult


class Embedder(ABC):

    @abstractmethod
    def extract(self, trackingResult: TrackingResult) -> EmbeddingResult:
        ...

    @abstractmethod
    def extract_batch(self, trackingResults: list[TrackingResult]) -> list[EmbeddingResult]:
        ...