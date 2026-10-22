"""Collections on disk: one JSON file per collection, rewritten whole after every change.

With hybrid retrieval, ``<stem>.vectors.npz`` next to it holds one embedding row per passage, the embedder id and a
digest of the passage ids. A restart loads the rows only when the embedder, the digest of the JSON's passages
and the vector width all match, so a crash between the two writes cannot pair passages with other rows.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import quote

from evidencia.index import Collection, Embed, Passage, db_collection_name

LOG = logging.getLogger("evidencia")

MAX_STEM = 200     # leaves room for ".vectors.npz.tmp" in a 255-byte file name


class Store:
    def __init__(self, path: str | os.PathLike, embedder_id: str | None = None, width: int | None = None):
        self.path = Path(path)
        self.embedder_id = embedder_id
        self.width = width
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file(self, name: str) -> Path:
        # quote() leaves no "/" in the file name, so a collection name cannot reach outside the directory
        stem = quote(name, safe="")
        if len(stem) > MAX_STEM:
            # file names are limited to 255 bytes; the name itself is inside the file
            stem = f"{stem[:MAX_STEM - 17]}-{hashlib.sha256(name.encode()).hexdigest()[:16]}"
        return self.path / f"{stem}.json"

    def _vectors_file(self, name: str) -> Path:
        return self._file(name).with_suffix(".vectors.npz")

    def save(self, collection: Collection) -> None:
        import numpy as np
        with self._lock:
            # the state is read inside the lock, so the last write always holds the newest state
            passages, vectors = collection.state()
            data = collection.as_dict(passages)
            target, vectors_file = self._file(collection.name), self._vectors_file(collection.name)
            if vectors is not None and self.embedder_id:
                tmp = vectors_file.with_name(vectors_file.name + ".tmp")
                with open(tmp, "wb") as f:
                    np.savez(f, vectors=vectors, embedder=np.array(self.embedder_id), ids=np.array(_digest(passages)))
                os.replace(tmp, vectors_file)
            else:
                vectors_file.unlink(missing_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)

    def delete(self, name: str) -> None:
        with self._lock:
            self._file(name).unlink(missing_ok=True)
            self._vectors_file(name).unlink(missing_ok=True)

    def load(self, embed: Embed | None = None, db=None) -> dict[str, Collection]:
        """Every collection on disk. With ``db``, the rows live in that vector database, not in vector files."""
        collections = {}
        for path in sorted(self.path.glob("*.json")):
            # only a file that cannot be read is set aside; an index that cannot be built stops the start
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                name, lang = data["name"], data.get("lang", "pt-PT")
                passages = [Passage(**{**p, "score": 0.0}) for p in data["passages"]]
            except (OSError, ValueError, KeyError, TypeError) as e:
                aside = path.with_suffix(".json.corrupt")
                LOG.error("collection file %s cannot be read (%s); moved to %s", path, e, aside)
                path.replace(aside)
                continue
            stored = self._stored_vectors(name, passages) if embed and db is None else None
            db_name = db_collection_name(name, self.embedder_id) if db is not None else ""
            collections[name] = col = Collection(name, lang, passages, embed, stored, db, db_name)
            if stored is None and col.vectors is not None:
                self.save(col)      # embedded at this start; the next start reads the rows
        return collections

    def _stored_vectors(self, name: str, passages: list[Passage]):
        """The saved rows, when the same embedder made them for these passages; otherwise None."""
        import numpy as np
        path = self._vectors_file(name)
        if not path.exists():
            return None
        try:
            with np.load(path) as stored:
                if str(stored["embedder"]) != self.embedder_id or str(stored["ids"]) != _digest(passages):
                    return None
                vectors = stored["vectors"]
        except (OSError, ValueError, KeyError) as e:
            LOG.warning("vectors of %s cannot be read (%s); embedding the passages again", path, e)
            return None
        if vectors.ndim != 2 or len(vectors) != len(passages) or (self.width and vectors.shape[1] != self.width):
            return None
        return vectors


def _digest(passages: list[Passage]) -> str:
    return hashlib.sha256("\0".join(p.id for p in passages).encode()).hexdigest()
