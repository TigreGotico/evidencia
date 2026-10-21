"""Remote knowledge sources: public Portuguese services searched at question time."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import requests

from evidencia.index import Collection, Embed, Passage

LOG = logging.getLogger("evidencia")
MAX_REMOTE_BYTES = 2_000_000    # the most text read from one remote document
CACHE_SIZE = 128                # questions whose fetched documents are kept
NAME = re.compile(r"[A-ZÀ-Ý][\w-]*(?:\s+(?:(?:d[aeo]s?)\s+)?[A-ZÀ-Ý][\w-]*)*")  # a run of capitalised words, joined by de, da, do and their plurals
CACHE_TTL = 600.0               # seconds a fetched document set is reused


class Source(Protocol):
    """Anything evidencia can retrieve passages from. ``Collection`` is one."""
    name: str

    def search(self, query: str, k: int) -> list[Passage]: ...


class SourceUnavailable(RuntimeError):
    """A remote source could not be reached or returned something unreadable."""


@dataclass(frozen=True)
class Document:
    key: str        # stable remote id, such as a page id
    title: str
    url: str
    text: str
    fmt: str = "text"


class RemoteSource:
    """A remote service that returns documents for a question.

    The source fetches a fixed number of documents for the question, chunks them, and ranks the chunks with the
    same retrieval as a local collection. A wider round asks for more passages from the same documents, so the
    passages of the round before stay in front and keep their ids.
    """
    name = ""
    title = ""
    homepage = ""
    licence = ""
    commercial = True
    description = ""
    pool = 4        # documents fetched per question

    def __init__(self, http: requests.Session, stopwords: frozenset[str] = frozenset(), lang: str = "pt-PT",
                 timeout: float = 10.0, embed: Embed | None = None):
        self.http = http
        self.stopwords = stopwords
        self.lang = lang
        self.timeout = timeout
        self.embed = embed
        self._cache: OrderedDict[str, tuple[float, Collection]] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "title": cls.title, "homepage": cls.homepage, "licence": cls.licence,
                "commercial": cls.commercial, "description": cls.description}

    def terms(self, question: str) -> list[str]:
        """The content words of the question, in order, for a keyword search API."""
        words = dict.fromkeys(w for w in re.findall(r"\w+", question.lower()) if w not in self.stopwords)
        return list(words) or re.findall(r"\w+", question.lower())

    def names(self, question: str) -> list[str]:
        """The names in the question, such as "Torre de Belém": runs of capitalised words, joined by de, da or do."""
        found = []
        for match in NAME.finditer(question):
            words = match[0].split()
            while words and words[0].lower() in self.stopwords:
                words.pop(0)    # the capitalised first word of a question, such as "Quando"
            if words:
                found.append(" ".join(words))
        return list(dict.fromkeys(found))

    def fetch(self, question: str) -> list[Document]:
        raise NotImplementedError

    def search(self, query: str, k: int) -> list[Passage]:
        """The top ``k`` passages, with repeated texts dropped: one page often repeats a line in two sections."""
        seen: set[str] = set()
        unique = []
        for p in self._documents(query).search(query, 2 * k):
            if p.text not in seen:
                seen.add(p.text)
                unique.append(p)
        return unique[:k]

    def _documents(self, question: str) -> Collection:
        with self._lock:
            hit = self._cache.get(question)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                self._cache.move_to_end(question)
                return hit[1]
        try:
            docs = self.fetch(question)
        except (requests.RequestException, ValueError, KeyError, TypeError, ET.ParseError) as e:
            raise SourceUnavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        col = Collection(self.name, lang=self.lang, embed=self.embed)
        titles: set[str] = set()
        for doc in docs:
            # re-adding a source name replaces that document, so a repeated title gets the remote id
            source = f"{doc.title} ({doc.key})" if doc.title in titles else doc.title
            titles.add(doc.title)
            try:
                col.add(doc.text, source, doc.fmt, doc.url)
            except ValueError:
                continue    # a document with no text adds nothing
        with self._lock:
            self._cache[question] = (time.monotonic(), col)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return col

    def get(self, url: str, **params) -> bytes:
        """The body of a GET, cut at MAX_REMOTE_BYTES. The whole request must end within the source timeout."""
        deadline = time.monotonic() + self.timeout
        with self.http.get(url, params=params, timeout=self.timeout, stream=True) as resp:
            resp.raise_for_status()
            body = bytearray()
            for block in resp.iter_content(64 * 1024):
                body += block
                if len(body) >= MAX_REMOTE_BYTES:
                    break
                if time.monotonic() > deadline:
                    raise requests.Timeout(f"{url}: no complete reply in {self.timeout} s")
        return bytes(body[:MAX_REMOTE_BYTES])

    def get_json(self, url: str, **params):
        return json.loads(self.get(url, **params))


class MediaWiki(RemoteSource):
    """A Wikimedia wiki: full-text search, then the plain text of each page found."""
    host = ""
    homepage = ""
    licence = "CC BY-SA 4.0"

    @property
    def api(self) -> str:
        return f"https://{self.host}/w/api.php"

    def fetch(self, question: str) -> list[Document]:
        # a keyword search needs every word in the page, so the page named in the question comes first
        ids = []
        if names := self.names(question):
            titled = self.get_json(self.api, action="query", titles="|".join(names[:self.pool]), redirects=1,
                                   format="json", formatversion=2)
            ids = [page["pageid"] for page in titled["query"]["pages"] if "pageid" in page]
        found = self.get_json(self.api, action="query", list="search", srsearch=" ".join(self.terms(question)),
                         srlimit=self.pool, srprop="", format="json", formatversion=2)
        ids = list(dict.fromkeys(ids + [hit["pageid"] for hit in found["query"]["search"]]))[:self.pool]
        if not ids:
            return []
        with ThreadPoolExecutor(len(ids)) as pool:
            return [doc for doc in pool.map(self.page, ids) if doc]

    def page(self, pageid: int) -> Document | None:
        data = self.get_json(self.api, action="query", prop="extracts|info", inprop="url", explaintext=1,
                        exsectionformat="wiki", pageids=pageid, format="json", formatversion=2)
        page = data["query"]["pages"][0]
        text = page.get("extract") or ""
        if not text.strip():
            return None
        title = page["title"]
        # each section heading carries the page title, so every passage says which page it is from
        body = re.sub(r"^(=+)\s*(.*?)\s*=+\s*$", lambda m: f"{'#' * len(m[1])} {title}: {m[2]}", text, flags=re.M)
        return Document(str(pageid), title, page.get("fullurl", ""), f"# {title}\n\n{body}", "markdown")


class Wikipedia(MediaWiki):
    name = "pt.wikipedia"
    title = "Wikipédia em português"
    host = "pt.wikipedia.org"
    homepage = "https://pt.wikipedia.org"
    description = "Encyclopedia articles. Articles are in European or Brazilian Portuguese."


class Wikisource(MediaWiki):
    name = "pt.wikisource"
    title = "Wikisource em português"
    host = "pt.wikisource.org"
    homepage = "https://pt.wikisource.org"
    description = "Public-domain texts: literature, laws and historical documents."


class Wiktionary(MediaWiki):
    name = "pt.wiktionary"
    title = "Wikcionário"
    host = "pt.wiktionary.org"
    homepage = "https://pt.wiktionary.org"
    description = "Dictionary entries: meanings, grammar and pronunciation."


class Wikidata(RemoteSource):
    """Structured facts. Each item found becomes sentences of the form "property: value"."""
    name = "wikidata"
    title = "Wikidata"
    homepage = "https://www.wikidata.org"
    licence = "CC0 1.0"
    description = "Structured facts about people, places and things, with Portuguese labels."
    api = "https://www.wikidata.org/w/api.php"
    max_properties = 30
    max_values = 3
    skipped_types = {"external-id", "commonsMedia", "url", "globe-coordinate", "geo-shape", "tabular-data",
                     "math", "musical-notation", "wikibase-lexeme", "wikibase-form", "wikibase-sense"}

    def fetch(self, question: str) -> list[Document]:
        found = self.get_json(self.api, action="query", list="search", srsearch=" ".join(self.terms(question)),
                         srlimit=self.pool, srprop="", format="json", formatversion=2)
        ids = [hit["title"] for hit in found["query"]["search"] if re.fullmatch(r"Q\d+", hit["title"])]
        if names := self.names(question):
            # the item of the Wikipédia article with a name in the question, as for MediaWiki above
            titled = self.get_json(self.api, action="wbgetentities", sites="ptwiki", titles="|".join(names[:self.pool]),
                                   props="info", format="json")
            ids = [qid for qid in titled.get("entities", {}) if re.fullmatch(r"Q\d+", qid)] + ids
        ids = list(dict.fromkeys(ids))[:self.pool]
        if not ids:
            return []
        entities = self.entities(ids, "labels|descriptions|claims")
        referenced = {ref for e in entities.values() for ref in self._references(e)}
        labels = {k: self._label(v) for k, v in self.entities(sorted(referenced)[:200], "labels").items()}
        return [doc for qid in ids if qid in entities and (doc := self.document(entities[qid], labels))]

    def entities(self, ids: list[str], props: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for start in range(0, len(ids), 50):
            data = self.get_json(self.api, action="wbgetentities", ids="|".join(ids[start:start + 50]), props=props,
                            languages="pt|en", format="json")
            out.update({k: v for k, v in data.get("entities", {}).items() if "missing" not in v})
        return out

    def _label(self, entity: dict, field: str = "labels") -> str:
        values = entity.get(field, {})
        return (values.get("pt") or values.get("en") or {}).get("value", "")

    def _values(self, entity: dict):
        for prop, claims in list(entity.get("claims", {}).items())[:self.max_properties]:
            values = [c["mainsnak"] for c in claims if c["mainsnak"].get("snaktype") == "value"
                      and c["mainsnak"].get("datatype") not in self.skipped_types]
            if values:
                yield prop, values[:self.max_values]

    def _references(self, entity: dict):
        for prop, snaks in self._values(entity):
            yield prop
            for snak in snaks:
                value = snak["datavalue"]["value"]
                if snak["datavalue"]["type"] == "wikibase-entityid":
                    yield value["id"]
                elif snak["datavalue"]["type"] == "quantity" and value.get("unit", "1") != "1":
                    yield value["unit"].rsplit("/", 1)[-1]

    def _render(self, snak: dict, labels: dict[str, str]) -> str:
        kind, value = snak["datavalue"]["type"], snak["datavalue"]["value"]
        if kind == "wikibase-entityid":
            return labels.get(value["id"], "")
        if kind == "quantity":
            amount = value["amount"].lstrip("+")
            unit = labels.get(value.get("unit", "1").rsplit("/", 1)[-1], "") if value.get("unit", "1") != "1" else ""
            return f"{amount} {unit}".strip()
        if kind == "time":
            # precision 9 or less is a year or coarser, 10 a month, 11 a day
            date = value["time"].lstrip("+").split("T")[0]
            if date.startswith("-"):
                return date
            precision = value.get("precision", 11)
            return date[:4] if precision <= 9 else date[:7] if precision == 10 else date
        if kind == "monolingualtext":
            return value["text"]
        return value if isinstance(value, str) else ""

    def document(self, entity: dict, labels: dict[str, str]) -> Document | None:
        label = self._label(entity)
        if not label:
            return None
        description = self._label(entity, "descriptions")
        head = f"{label}: {description}." if description else f"{label}."
        facts = [f"{label}, {labels[prop]}: {', '.join(v for v in map(lambda s: self._render(s, labels), snaks) if v)}."
                 for prop, snaks in self._values(entity) if labels.get(prop)]
        facts = [f for f in facts if not f.endswith(": .")]
        # groups of eight facts, each group a paragraph that starts with what the item is
        paragraphs = [head + "\n" + "\n".join(facts[i:i + 8]) for i in range(0, len(facts), 8)] or [head]
        return Document(entity["id"], label, f"https://www.wikidata.org/wiki/{entity['id']}", "\n\n".join(paragraphs))


class DicionarioAberto(RemoteSource):
    """Dictionary definitions for the content words of the question."""
    name = "dicionario-aberto"
    title = "Dicionário Aberto"
    homepage = "https://dicionario-aberto.net"
    licence = "CC BY-SA 2.5 PT"
    description = "Word definitions from a public-domain dictionary, in the spelling before the 1990 reform."
    api = "https://api.dicionario-aberto.net"
    pool = 3

    def fetch(self, question: str) -> list[Document]:
        words = [w for w in self.terms(question) if len(w) > 2][:self.pool]
        with ThreadPoolExecutor(max(len(words), 1)) as pool:
            return [doc for docs in pool.map(self.word, words) for doc in docs]

    def word(self, word: str) -> list[Document]:
        docs = []
        for entry in self.get_json(f"{self.api}/word/{requests.utils.quote(word)}") or []:
            root = ET.fromstring(entry["xml"])
            orth = (root.findtext(".//orth") or word).strip()
            senses = [" ".join("".join(d.itertext()).split()) for d in root.iter("def")]
            senses = [s for s in senses if s]
            if senses:
                text = f"{orth}: " + " ".join(f"({n}) {s}" for n, s in enumerate(senses, 1)) if len(senses) > 1 \
                    else f"{orth}: {senses[0]}"
                docs.append(Document(str(entry.get("word_id", orth)), orth,
                                     f"{self.homepage}/search/{requests.utils.quote(orth.lower())}", text))
        return docs


class ArquivoPt(RemoteSource):
    """The Portuguese web archive: full-text search over archived pages."""
    name = "arquivo.pt"
    title = "Arquivo.pt"
    homepage = "https://arquivo.pt"
    licence = "Arquivo.pt terms of use, non-commercial"
    commercial = False
    description = "Archived Portuguese web pages since 1996. Page text includes menus and other page furniture."
    api = "https://arquivo.pt/textsearch"

    def fetch(self, question: str) -> list[Document]:
        data = self.get_json(self.api, q=" ".join(self.terms(question)), maxItems=self.pool, prettyPrint="false")
        hits = data.get("response_items", [])
        with ThreadPoolExecutor(max(len(hits), 1)) as pool:
            return [doc for doc in pool.map(self.page, hits) if doc]

    def page(self, hit: dict) -> Document | None:
        link = hit["linkToExtractedText"]
        if not link.startswith(self.homepage + "/"):
            # the search reply names the link; reading a host it names would let it point the service anywhere
            LOG.warning("arquivo.pt: extracted text outside %s ignored: %s", self.homepage, link)
            return None
        try:
            text = self.get(link).decode("utf-8", errors="replace")
        except requests.RequestException as e:
            LOG.warning("arquivo.pt: could not read %s: %s", hit.get("linkToArchive"), e)
            return None
        # the extracted text has no paragraph breaks; one sentence per line lets the chunker split it
        text = re.sub(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", "\n\n", text)
        key = hashlib.sha256(hit["linkToArchive"].encode()).hexdigest()[:12]
        stamp = str(hit.get("tstamp", ""))[:8]
        title = f"{hit.get('title') or hit.get('originalURL')}, {stamp[:4]}-{stamp[4:6]}-{stamp[6:]}" if stamp \
            else hit.get("title") or hit.get("originalURL", "")
        return Document(key, title, hit["linkToArchive"], text)


CATALOG: dict[str, type[RemoteSource]] = {cls.name: cls for cls in
                                          (Wikipedia, Wikisource, Wiktionary, Wikidata, DicionarioAberto, ArquivoPt)}


def build_sources(names: list[str], user_agent: str, stopwords: frozenset[str], lang: str, timeout: float,
                  embed: Embed | None = None) -> dict[str, RemoteSource]:
    unknown = set(names) - set(CATALOG)
    if unknown:
        raise ValueError(f"unknown sources {sorted(unknown)}; known: {sorted(CATALOG)}")
    http = requests.Session()
    http.headers["User-Agent"] = user_agent
    return {name: CATALOG[name](http, stopwords, lang, timeout, embed) for name in names}
