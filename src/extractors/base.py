"""Abstract base interface that all extractor backends must implement."""

from typing import Protocol

from src.schemas import ExtractionResult, GateResult


class LabelExtractor(Protocol):
    """Interface for a model that can gate and extract label images.

    Swapping cloud for a local model means writing one new class here —
    nothing else in the codebase changes.
    """

    def gate(self, image_bytes: bytes) -> GateResult:
        ...

    def extract(self, image_bytes: bytes, language: str) -> ExtractionResult:
        ...
