"""Document collections: chunking, and BM25 or hybrid retrieval."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterable, Iterator, Sequence

from ovos_bm25_solver import BM25CorpusSolver
from ovos_document_chunkers import RegexParagraphSplitter
from ovos_document_chunkers.files import PDFParagraphSplitter

FORMATS = ("text", "markdown", "html", "pdf")
# a passage longer than this is split at word boundaries, so one paragraph cannot overflow the decision model
MAX_PASSAGE_CHARS = 1500
# reciprocal-rank-fusion constant: a passage ranked r by one retriever adds 1 / (RRF_K + r) to its score
RRF_K = 60

# the embedding hits a search asks a vector database for; RRF adds less than 1 / 260 for a rank below this
DB_TOP_K = 200

# texts in, one L2-normalised row per text out
Embed = Callable[[Sequence[str]], "numpy.ndarray"]


def db_collection_name(name: str, embedder_id: str) -> str:
    """The vector-database collection of a collection: database names are short and plain, and rows from another
    embedder are never mixed in."""
    return "evidencia-" + hashlib.sha256(f"{name}\0{embedder_id}".encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Passage:
    id: str
    text: str
    source: str
    score: float = 0.0
    url: str = ""       # a citable address for the document, when it has one
    origin: str = ""    # the collection or remote source that returned the passage


class _HTMLText(HTMLParser):
    """The visible text of an HTML document, with a blank line at every block boundary."""
    BLOCKS = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "footer",
              "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
              "table", "td", "th", "title", "tr", "ul"}
    HIDDEN = {"script", "style", "noscript", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _html(text: str) -> Iterable[str]:
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    return RegexParagraphSplitter().chunk("".join(parser.parts))


def _bounded(text: str) -> Iterator[str]:
    """``text`` in pieces of at most MAX_PASSAGE_CHARS, split between words."""
    if len(text) <= MAX_PASSAGE_CHARS:
        yield text
        return
    piece = ""
    for word in text.split():
        for start in range(0, len(word), MAX_PASSAGE_CHARS):
            part = word[start:start + MAX_PASSAGE_CHARS]
            if piece and len(piece) + 1 + len(part) > MAX_PASSAGE_CHARS:
                yield piece
                piece = part
            else:
                piece = f"{piece} {part}" if piece else part
    if piece:
        yield piece


def _markdown(text: str) -> Iterable[str]:
    """Paragraphs of a Markdown document, each prefixed with the heading it sits under; nothing is dropped."""
    heading = ""
    for para in RegexParagraphSplitter().chunk(text):
        lines = para.strip().splitlines()
        while lines and lines[0].lstrip().startswith("#"):
            heading = lines.pop(0).lstrip("# ").strip()
        body = "\n".join(lines).strip()
        if body:
            yield f"{heading}\n{body}" if heading else body


def chunk(content: str | bytes, fmt: str) -> list[str]:
    """Split one document into paragraphs. ``content`` is the document itself, never a path or URL."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {FORMATS}")
    if fmt == "pdf":
        if not isinstance(content, bytes):
            raise ValueError("pdf content must be bytes")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(content)
        try:
            chunks: Iterable[str] = list(PDFParagraphSplitter().chunk(f.name))
        except Exception as e:
            raise ValueError(f"could not read the pdf: {type(e).__name__}") from e
        finally:
            os.unlink(f.name)
    else:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        if fmt == "markdown":
            chunks = _markdown(text)
        elif fmt == "html":
            chunks = _html(text)
        else:
            chunks = RegexParagraphSplitter().chunk(text)
    return [piece for c in chunks if c and c.strip() for piece in _bounded(c.strip())]


@dataclass
class Collection:
    """A named set of documents. With ``embed``, search fuses BM25 and embedding similarity by rank.

    ``vectors`` holds one embedding row per passage, in order; rows given at construction are used when they
    match the passages, so a restart does not embed again. With ``db``, an OPM ``EmbeddingsDB`` keeps the rows in
    its collection ``db_name`` instead, keyed by passage id, and ``vectors`` stays empty. An add embeds only its own
    passages, and builds the new index before it takes the lock that searches hold.
    """
    name: str
    lang: str = "pt-PT"
    passages: list[Passage] = field(default_factory=list)
    embed: Embed | None = None
    vectors: "numpy.ndarray | None" = field(default=None, repr=False, compare=False)
    db: "EmbeddingsDB | None" = field(default=None, repr=False, compare=False)
    db_name: str = field(default="", repr=False, compare=False)

    def __post_init__(self):
        self._lock = threading.Lock()           # held by searches and by the swap to a new index
        self._write_lock = threading.Lock()     # one add or remove at a time
        self._bm25: BM25CorpusSolver | None = None
        if self.embed is None or self.db is not None:
            self.vectors = None
            if self.embed is not None and self.passages:
                self._fill_db()
        elif self.vectors is None or len(self.vectors) != len(self.passages):
            self.vectors = self.embed([p.text for p in self.passages]) if self.passages else None
        if self.passages:
            self._bm25 = self._bm25_for(self.passages)

    def add(self, content: str | bytes, source: str, fmt: str = "text", url: str = "") -> list[Passage]:
        """Chunk a document, add its passages and rebuild the index. Re-adding a source replaces it."""
        new = [Passage(id=hashlib.sha256(f"{source}\0{i}\0{c}".encode()).hexdigest()[:16], text=c, source=source,
                       url=url, origin=self.name)
               for i, c in enumerate(chunk(content, fmt))]
        if not new:
            raise ValueError(f"{source}: no text found")
        with self._write_lock:
            keep = [i for i, p in enumerate(self.passages) if p.source != source]
            added = self.embed([p.text for p in new]) if self.embed else None
            if added is not None and len(added) != len(new):
                raise ValueError(f"{source}: the embedder returned {len(added)} rows for {len(new)} passages")
            if self.db is not None and added is not None:
                # written before the swap: a search may see a row whose passage it does not have yet, and skips it
                self.db.add_embeddings_batch([p.id for p in new], list(added), [{"source": source}] * len(new),
                                             collection_name=self.db_name)
            ids = {p.id for p in new}
            stale = [p.id for p in self.passages if p.source == source and p.id not in ids]
            self._swap([self.passages[i] for i in keep] + new, keep, added)
            self._forget(stale)
        return new

    def remove(self, source: str) -> int:
        with self._write_lock:
            keep = [i for i, p in enumerate(self.passages) if p.source != source]
            removed = len(self.passages) - len(keep)
            if removed:
                stale = [p.id for p in self.passages if p.source == source]
                self._swap([self.passages[i] for i in keep], keep, None)
                self._forget(stale)
            return removed

    def drop(self) -> None:
        """Delete the collection's rows from the vector database."""
        if self.db is not None and self.embed is not None:
            self.db.delete_collection(self.db_name)

    def _forget(self, ids: list[str]) -> None:
        if ids and self.db is not None and self.embed is not None:
            self.db.delete_embeddings_batch(ids, collection_name=self.db_name)

    def _fill_db(self) -> None:
        """Embed the passages whose rows the database does not hold, after a restart or a lost write."""
        held = set()
        for start in range(0, len(self.passages), 1000):
            keys = [p.id for p in self.passages[start:start + 1000]]
            held |= {hit[0] for hit in self.db.get_embeddings_batch(keys, collection_name=self.db_name)}
        missing = [p for p in self.passages if p.id not in held]
        if missing:
            vectors = self.embed([p.text for p in missing])
            if len(vectors) != len(missing):
                raise ValueError(f"{self.name}: the embedder returned {len(vectors)} rows for {len(missing)} passages")
            self.db.add_embeddings_batch([p.id for p in missing], list(vectors),
                                         [{"source": p.source} for p in missing], collection_name=self.db_name)

    def _swap(self, passages: list[Passage], keep: list[int], added) -> None:
        """Build the index of ``passages``: the kept rows of the old vectors, then ``added``; then swap it in."""
        vectors = None
        if self.embed is not None and self.db is None and passages:
            import numpy as np
            parts = [self.vectors[keep]] if keep else []
            vectors = np.concatenate(parts + ([added] if added is not None else []))
        bm25 = self._bm25_for(passages) if passages else None
        with self._lock:
            self.passages, self.vectors, self._bm25 = passages, vectors, bm25

    def _bm25_for(self, passages: list[Passage]) -> BM25CorpusSolver:
        bm25 = BM25CorpusSolver({"lang": self.lang, "method": None, "idf_method": None})
        bm25.load_corpus([p.text for p in passages])
        return bm25

    def search(self, query: str, k: int) -> list[Passage]:
        """The top ``k`` passages, best first.

        BM25 alone returns only passages that share a term with the query. Hybrid search also returns passages
        that are only similar in meaning, and its score is the fused rank score, not a BM25 score.
        """
        query_vector = self.embed([query])[0] if self.embed is not None and k > 0 else None
        with self._lock:
            passages, vectors, bm25 = self.passages, self.vectors, self._bm25
        if bm25 is None or k <= 0:
            return []
        n = len(passages)
        # the index returns texts; equal texts from different sources score the same and each comes back once
        by_text: dict[str, list[int]] = defaultdict(list)
        for i, p in enumerate(passages):
            by_text[p.text].append(i)
        ranked = [(by_text[text].pop(0), float(score))
                  for text, score in bm25.query(query, lang=self.lang, k=n if self.embed else min(k, n))]
        ranked = [(i, score) for i, score in ranked if score > 0]
        similar = None
        if vectors is not None:
            similar = [int(i) for i in (vectors @ query_vector).argsort()[::-1]]
        elif self.db is not None and query_vector is not None:
            position = {p.id: i for i, p in enumerate(passages)}
            # a fixed size, so rows of deleted passages cannot push out the passages of a small collection
            hits = self.db.query(query_vector, top_k=DB_TOP_K, collection_name=self.db_name)
            similar = [position[hit[0]] for hit in hits if hit[0] in position]
        if similar is not None:
            fused: dict[int, float] = defaultdict(float)
            for rank, (i, _) in enumerate(ranked):
                fused[i] += 1 / (RRF_K + rank + 1)
            for rank, i in enumerate(similar):
                fused[i] += 1 / (RRF_K + rank + 1)
            ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        return [Passage(p.id, p.text, p.source, score, p.url, p.origin)
                for p, score in ((passages[i], score) for i, score in ranked[:k])]

    def state(self) -> tuple[list[Passage], "numpy.ndarray | None"]:
        """The passages and their vectors, read together."""
        with self._lock:
            return self.passages, self.vectors

    def as_dict(self, passages: list[Passage] | None = None) -> dict:
        passages = self.passages if passages is None else passages
        return {"name": self.name, "lang": self.lang, "passages": [asdict(p) for p in passages]}

