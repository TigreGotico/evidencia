"""Static embeddings for hybrid retrieval, loaded on first use."""
from __future__ import annotations

import threading
from typing import Sequence


class Embedder:
    """A model2vec static embedding model. ``embedder(texts)`` returns one L2-normalised row per text."""

    def __init__(self, model: str):
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    def __call__(self, texts: Sequence[str]):
        import numpy as np
        with self._lock:
            if self._model is None:
                from model2vec import StaticModel
                self._model = StaticModel.from_pretrained(self.model_name)
        vectors = np.asarray(self._model.encode(list(texts)), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)
