"""Collections on disk: one JSON file per collection, rewritten whole after every change."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import quote

from evidencia.index import Collection, Embed, Passage

LOG = logging.getLogger("evidencia")

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
            collections[name] = Collection(name, lang, passages, embed)
        return collections
