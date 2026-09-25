"""Locally generated embeddings; search remains usable if the model is unavailable."""

from collections.abc import Sequence
from functools import cached_property


class LocalEmbeddings:
    def __init__(self, model: str, cache_dir: str | None = None):
        self.model = model
        self.cache_dir = cache_dir

    @cached_property
    def _encoder(self):
        from fastembed import TextEmbedding

        return TextEmbedding(model_name=self.model, cache_dir=self.cache_dir)

    def embed(self, text: str) -> list[float]:
        vector: Sequence[float] = next(iter(self._encoder.embed([text])))
        return [float(value) for value in vector]
