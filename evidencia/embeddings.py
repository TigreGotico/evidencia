"""Embedders for hybrid retrieval: the built-in model2vec one, or any OPM text embeddings plugin.

Each is an ``Embed``: ``embedder(texts)`` returns one L2-normalised float32 row per text. ``id`` names the model
and its settings, so vectors kept on disk are reused only by the embedder that made them. ``load()`` makes the
model ready, so the service loads it at start and a model that cannot load stops the start.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Sequence

from ovos_plugin_manager.embeddings import load_text_embeddings_plugin


def _normalised(vectors):
    import numpy as np
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class Embedder:
    """A model2vec static embedding model, loaded by ``load()`` or on first use."""

    def __init__(self, model: str):
        self.model_name = model
        self.id = f"model2vec:{model}"
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                from model2vec import StaticModel
                self._model = StaticModel.from_pretrained(self.model_name)

    def __call__(self, texts: Sequence[str]):
        self.load()
        return _normalised(self._model.encode(list(texts)))


class PluginEmbedder:
    """An OPM ``TextEmbedder`` plugin, loaded by its entry point name at start."""

    def __init__(self, name: str, config: dict[str, Any]):
        plugin = load_text_embeddings_plugin(name)
        if plugin is None:
            raise ValueError(f"text embeddings plugin {name!r} is not installed")
        self.engine = plugin(config=config)
        settings = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
        self.id = f"{name}:{settings}"

    def load(self) -> None:
        """The plugin is built in ``__init__``; nothing is left to load."""

    def __call__(self, texts: Sequence[str]):
        return _normalised(self.engine.get_embeddings_batch(list(texts)))
