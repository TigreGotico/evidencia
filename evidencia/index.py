"""Document collections: chunking and BM25 retrieval."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable, Iterator

from ovos_bm25_solver import BM25CorpusSolver
from ovos_document_chunkers import RegexParagraphSplitter
from ovos_document_chunkers.files import PDFParagraphSplitter

FORMATS = ("text", "markdown", "html", "pdf")
# a passage longer than this is split at word boundaries, so one paragraph cannot overflow the decision model
MAX_PASSAGE_CHARS = 1500


@dataclass(frozen=True)
class Passage:
    id: str
    text: str
    source: str
    score: float = 0.0


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
    name: str
    lang: str = "pt-PT"
    passages: list[Passage] = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.Lock()
        self._bm25: BM25CorpusSolver | None = None

    def add(self, content: str | bytes, source: str, fmt: str = "text") -> list[Passage]:
        """Chunk a document, add its passages and rebuild the index. Re-adding a source replaces it."""
        new = [Passage(id=hashlib.sha256(f"{source}\0{i}\0{c}".encode()).hexdigest()[:16], text=c, source=source)
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
            self._bm25 = None
            return
        bm25 = BM25CorpusSolver({"lang": self.lang, "method": None, "idf_method": None})
        bm25.load_corpus([p.text for p in self.passages])
        self._bm25 = bm25

    def search(self, query: str, k: int) -> list[Passage]:
        """The top ``k`` passages by BM25 score, best first. Passages scoring zero share no term with the query."""
        with self._lock:
            if self._bm25 is None or k <= 0:
                return []
            k = min(k, len(self.passages))
            # the index returns texts; equal texts from different sources score the same and each comes back once
            by_text: dict[str, list[Passage]] = defaultdict(list)
            for p in self.passages:
                by_text[p.text].append(p)
            hits = [(by_text[text].pop(0), float(score)) for text, score in self._bm25.query(query, lang=self.lang, k=k)]
        return [Passage(p.id, p.text, p.source, score) for p, score in hits if score > 0]
