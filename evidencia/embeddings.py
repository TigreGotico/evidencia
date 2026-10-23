"""Embedders for hybrid retrieval: the built-in model2vec one, or any OPM text embeddings plugin.

Each is an ``Embed``: ``embedder(texts)`` returns one L2-normalised float32 row per text. ``id`` names the model
and its settings, so vectors kept on disk are reused only by the embedder that made them. ``load()`` makes the
model ready and returns the vector width. The service calls it at start, so a model that cannot load or embed
stops the start.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Sequence

from ovos_plugin_manager.embeddings import load_text_embeddings_plugin

from evidencia.observability import EMBED_CALLS, EMBED_TEXTS

# settings that change no vector: a rotated key must not make every collection embed again
SECRETS = frozenset({"key", "api_key", "token", "password"})


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

    def _ready(self):
        with self._lock:
            if self._model is None:
                from model2vec import StaticModel
                self._model = StaticModel.from_pretrained(self.model_name)
        return self._model

    def load(self) -> int:
        return self(["evidencia"]).shape[1]

    def __call__(self, texts: Sequence[str]):
        EMBED_CALLS.inc()
        EMBED_TEXTS.inc(len(texts))
        return _normalised(self._ready().encode(list(texts)))


class PluginEmbedder:
    """An OPM ``TextEmbedder`` plugin, loaded by its entry point name at start."""

    def __init__(self, name: str, config: dict[str, Any]):
        plugin = load_text_embeddings_plugin(name)
        if plugin is None:
            raise ValueError(f"text embeddings plugin {name!r} is not installed")
        self.engine = plugin(config=config)
        kept = {k: v for k, v in config.items() if k not in SECRETS}
        settings = hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()[:12]
        self.id = f"{name}:{settings}"

    def load(self) -> int:
        """Embed one text: a plugin may load its model, or reach its server, only on the first batch."""
        return self(["evidencia"]).shape[1]

    def __call__(self, texts: Sequence[str]):
        EMBED_CALLS.inc()
        EMBED_TEXTS.inc(len(texts))
        return _normalised(self.engine.get_embeddings_batch(list(texts)))
