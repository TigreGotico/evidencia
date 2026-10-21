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

# texts in, one L2-normalised row per text out
Embed = Callable[[Sequence[str]], "numpy.ndarray"]


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
    """A named set of documents. With ``embed``, search fuses BM25 and embedding similarity by rank."""
    name: str
    lang: str = "pt-PT"
    passages: list[Passage] = field(default_factory=list)
    embed: Embed | None = None

    def __post_init__(self):
        self._lock = threading.Lock()
        self._bm25: BM25CorpusSolver | None = None
        self._vectors = None
        if self.passages:
            self._reindex()

    def add(self, content: str | bytes, source: str, fmt: str = "text", url: str = "") -> list[Passage]:
        """Chunk a document, add its passages and rebuild the index. Re-adding a source replaces it."""
        new = [Passage(id=hashlib.sha256(f"{source}\0{i}\0{c}".encode()).hexdigest()[:16], text=c, source=source,
                       url=url, origin=self.name)
               for i, c in enumerate(chunk(content, fmt))]
        if not new:
            raise ValueError(f"{source}: no text found")
        with self._lock:
            self.passages = [p for p in self.passages if p.source != source] + new
            self._reindex()
        return new

    def remove(self, source: str) -> int:
        with self._lock:
            before = len(self.passages)
            self.passages = [p for p in self.passages if p.source != source]
            self._reindex()
            return before - len(self.passages)

    def _reindex(self):
        if not self.passages:
            self._bm25 = self._vectors = None
            return
        bm25 = BM25CorpusSolver({"lang": self.lang, "method": None, "idf_method": None})
        bm25.load_corpus([p.text for p in self.passages])
        self._bm25 = bm25
        self._vectors = self.embed([p.text for p in self.passages]) if self.embed else None

    def search(self, query: str, k: int) -> list[Passage]:
        """The top ``k`` passages, best first.

        BM25 alone returns only passages that share a term with the query. Hybrid search also returns passages
        that are only similar in meaning, and its score is the fused rank score, not a BM25 score.
        """
        with self._lock:
            if self._bm25 is None or k <= 0:
                return []
            n = len(self.passages)
            # the index returns texts; equal texts from different sources score the same and each comes back once
            by_text: dict[str, list[int]] = defaultdict(list)
            for i, p in enumerate(self.passages):
                by_text[p.text].append(i)
            ranked = [(by_text[text].pop(0), float(score))
                      for text, score in self._bm25.query(query, lang=self.lang, k=n if self.embed else min(k, n))]
            ranked = [(i, score) for i, score in ranked if score > 0]
            if self._vectors is not None:
                fused: dict[int, float] = defaultdict(float)
                for rank, (i, _) in enumerate(ranked):
                    fused[i] += 1 / (RRF_K + rank + 1)
                similarity = self._vectors @ self.embed([query])[0]
                for rank, i in enumerate(similarity.argsort()[::-1]):
                    fused[int(i)] += 1 / (RRF_K + rank + 1)
                ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
            hits = [(self.passages[i], score) for i, score in ranked[:k]]
        return [Passage(p.id, p.text, p.source, score, p.url, p.origin) for p, score in hits]

    def as_dict(self) -> dict:
        return {"name": self.name, "lang": self.lang, "passages": [asdict(p) for p in self.passages]}

    @classmethod
    def from_dict(cls, data: dict, embed: Embed | None = None) -> Collection:
        passages = [Passage(**{**p, "score": 0.0}) for p in data["passages"]]
        return cls(data["name"], data.get("lang", "pt-PT"), passages, embed)
