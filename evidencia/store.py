"""Collections on disk: one JSON file per collection, rewritten whole after every change."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.parse import quote

from evidencia.index import Collection, Embed

MAX_STEM = 200     # leaves room for ".json.tmp" in a 255-byte file name


class Store:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file(self, name: str) -> Path:
        # quote() leaves no "/" in the file name, so a collection name cannot reach outside the directory
        stem = quote(name, safe="")
        if len(stem) > MAX_STEM:
            # file names are limited to 255 bytes; the name itself is inside the file
            stem = f"{stem[:MAX_STEM - 17]}-{hashlib.sha256(name.encode()).hexdigest()[:16]}"
        return self.path / f"{stem}.json"

    def save(self, collection: Collection) -> None:
        with self._lock:
            # the state is read inside the lock, so the last write always holds the newest state
            data = json.dumps(collection.as_dict(), ensure_ascii=False)
            target = self._file(collection.name)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, target)

    def delete(self, name: str) -> None:
        with self._lock:
            self._file(name).unlink(missing_ok=True)

    def load(self, embed: Embed | None = None) -> dict[str, Collection]:
        collections = {}
        for path in sorted(self.path.glob("*.json")):
            collection = Collection.from_dict(json.loads(path.read_text(encoding="utf-8")), embed)
            collections[collection.name] = collection
        return collections
