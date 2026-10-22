"""Remote knowledge sources: public Portuguese services searched at question time."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from functools import cache, cached_property
from html import unescape
from importlib import resources
from typing import Protocol
from urllib.parse import quote, urlencode, urljoin
from zoneinfo import ZoneInfo

import requests
from unblock_requests import CloudflareSession

from evidencia.index import Collection, Embed, Passage

LOG = logging.getLogger("evidencia")
MAX_REMOTE_BYTES = 2_000_000    # the most text read from one remote document
CACHE_SIZE = 128                # questions whose fetched documents are kept
NAME = re.compile(r"[A-ZÀ-Ý][\w-]*(?:\s+(?:(?:d[aeo]s?)\s+)?[A-ZÀ-Ý][\w-]*)*")  # a run of capitalised words, joined by de, da, do and their plurals
CACHE_TTL = 600.0               # seconds a fetched document set is reused
BREAKER_FAILURES = 3            # failed fetches in a row that pause a source
BREAKER_PAUSE = 60.0            # seconds a paused source answers SourceUnavailable without a request
MAX_QUERY = 300                 # the longest keyword search: CirrusSearch refuses a longer one
POSTAL_CODE = re.compile(r"\b\d{4}-\d{3}\b")
NIF = re.compile(r"(?<![\d-])[1-9]\d{8}(?![\d-])")
# a phone number also has 9 digits, so a number is a NIF only in a question about a tax number
NIF_CONTEXT = re.compile(r"\b(?:nifs?|nipcs?|contribuintes?|iva|vat|fiscal|colec?tiva)\b|\b(?:pt|nif|nipc)\d{9}\b", re.I)
LISBON = ZoneInfo("Europe/Lisbon")
PAGE_PRE = re.compile(rb"\A\s*<html.*?<pre[^>]*>(.*?)</pre>", re.S | re.I)   # a JSON reply as a browser shows it
DOMAIN = re.compile(r"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+pt\b", re.I)


def fold(text: str) -> str:
    """The text in lower case and without accents: "Évora", "evora" and "ÉVORA" fold the same."""
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c))


@cache
def gazetteer() -> tuple[dict[str, str], re.Pattern]:
    """The places a question can name in lower case and without accents: each folded name with its spelling, and
    a pattern that finds them in a folded question, longest name first. A place that is also a common word, such
    as Tomar, is left out of the pattern: a question names it with a capital letter."""
    data = json.loads((resources.files("evidencia") / "locale" / "pt-places.json").read_text(encoding="utf-8"))
    spelling = {fold(name): name for name in data["names"]}
    common = {fold(word) for word in data["capitalised_only"]}
    keys = sorted((key for key in spelling if key not in common), key=len, reverse=True)
    return spelling, re.compile(rf"\b(?:{'|'.join(map(re.escape, keys))})\b")


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

# the text before the first word of a sentence: "", "Olá! ", "1. ", "Bom dia, " or "«"
SENTENCE_START = re.compile(r"(?:^|[.!?:;,\n-])[\s¿¡\"'«(]*$")

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
    max_bytes = MAX_REMOTE_BYTES

    def __init__(self, http: requests.Session, stopwords: frozenset[str] = frozenset(), lang: str = "pt-PT",
                 timeout: float = 10.0, embed: Embed | None = None):
        self.http = http
        self.stopwords = stopwords
        self.lang = lang
        self.timeout = timeout
        self.embed = embed
        self._cache: OrderedDict[str, tuple[float, Collection]] = OrderedDict()
        self._fetching: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._fetches = self._failures = self._in_a_row = 0
        self._paused_until = 0.0
        self._trying = False
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._last_latency_ms: int | None = None

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "title": cls.title, "homepage": cls.homepage, "licence": cls.licence,
                "commercial": cls.commercial, "description": cls.description}

    def health(self) -> dict:
        """The fetches since start, the failures, the last error and how long the source stays paused."""
        with self._lock:
            return {"fetches": self._fetches, "failures": self._failures, "consecutive_failures": self._in_a_row,
                    "last_error": self._last_error, "last_error_at": self._last_error_at,
                    "last_latency_ms": self._last_latency_ms,
                    "paused_for_s": max(0, round(self._paused_until - time.monotonic()))}

    def query(self, question: str) -> str:
        """The content words of the question as one keyword search, cut at a word to MAX_QUERY characters."""
        query = " ".join(self.terms(question))
        if len(query) <= MAX_QUERY:
            return query
        cut = query[:MAX_QUERY + 1]
        return cut.rsplit(" ", 1)[0] if " " in cut else cut[:MAX_QUERY]

    def terms(self, question: str) -> list[str]:
        """The content words of the question, in order, for a keyword search API."""
        words = dict.fromkeys(w for w in re.findall(r"\w+", question.lower()) if w not in self.stopwords)
        return list(words) or re.findall(r"\w+", question.lower())

    def names(self, question: str) -> list[str]:
        """The names in the question, such as "Torre de Belém": runs of capitalised words, joined by de, da or do,
        then the places in the gazetteer that the question names without capitals or accents. A name that folds to
        a place takes the spelling of the place, so "Evora" is Évora."""
        spelling, places = gazetteer()
        found = []
        for match in NAME.finditer(question):
            words = match[0].split()
            # the capitalised first word of a sentence, such as "Quando"; inside a sentence "São" starts a name
            if SENTENCE_START.search(question[:match.start()]):
                while words and words[0].lower() in self.stopwords and fold(" ".join(words)) not in spelling:
                    words.pop(0)
            if words:
                found.append(" ".join(words))
        found = [spelling.get(fold(name), name) for name in found]
        # a question with a capitalised word after its first word is written with capitals, so its places have
        # them too; an acronym such as INE does not count
        if not re.search(r"(?<=\s)[A-ZÀ-Ý][a-zà-ÿ]", question.strip()):
            found += [spelling[match[0]] for match in places.finditer(fold(question))]
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
        """The documents for the question, from the cache when they are fresh. Requests for one question at the
        same time share one fetch. After BREAKER_FAILURES failures of the service in a row the source is paused.
        After the pause one request tries the service, and a failure pauses it again."""
        key = " ".join(question.split())
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                self._cache.move_to_end(key)
                return hit[1]
            if pending := self._fetching.get(key):
                leader = False
            else:
                if self._in_a_row >= BREAKER_FAILURES:
                    wait = self._paused_until - time.monotonic()
                    if wait > 0 or self._trying:
                        raise SourceUnavailable(f"{self.name}: failed {self._in_a_row} times in a row; "
                                                f"paused for {max(0, round(wait))} s")
                    self._trying = trial = True
                else:
                    trial = False
                pending = self._fetching[key] = Future()
                leader = True
        if not leader:
            return pending.result()
        try:
            col = self._fetch(question)
        except Exception as e:
            with self._lock:
                del self._fetching[key]
            pending.set_exception(e)
            raise
        except BaseException:
            with self._lock:
                del self._fetching[key]
            pending.set_exception(SourceUnavailable(f"{self.name}: the fetch was interrupted"))
            raise
        finally:
            if trial:
                with self._lock:
                    self._trying = False
        with self._lock:
            self._cache[key] = (time.monotonic(), col)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            del self._fetching[key]
        pending.set_result(col)
        return col

    def _fetch(self, question: str) -> Collection:
        start = time.monotonic()
        try:
            docs = self.fetch(question)
        except (NotImplementedError, RecursionError):
            raise
        except (SourceUnavailable, requests.RequestException, OSError, ValueError, KeyError, TypeError, AttributeError,
                IndexError, ET.ParseError, RuntimeError) as e:   # RuntimeError: a challenge or a solver that failed
            self._failed(e, time.monotonic() - start)
            if isinstance(e, SourceUnavailable):
                raise
            raise SourceUnavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        with self._lock:
            self._fetches += 1
            self._in_a_row = 0
            self._paused_until = 0.0
            self._last_latency_ms = round((time.monotonic() - start) * 1000)
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
        return col

    def _failed(self, error: Exception, seconds: float):
        """Count a failed fetch. Only a failure of the service counts towards a pause: a reply that evidencia
        cannot read is the fault of one question, so it does not pause the source for every question."""
        status = error.response.status_code if isinstance(error, requests.HTTPError) and error.response is not None \
            else None
        if isinstance(error, requests.HTTPError):
            of_service = status is None or status >= 500 or status == 429
        else:
            # a requests error is also an OSError, so the requests errors that are not of the service go first
            of_service = isinstance(error, (SourceUnavailable, requests.ConnectionError, requests.Timeout, RuntimeError)) \
                or isinstance(error, OSError) and not isinstance(error, requests.RequestException)
        with self._lock:
            self._fetches += 1
            self._failures += 1
            if of_service:
                self._in_a_row += 1
                if self._in_a_row >= BREAKER_FAILURES:
                    self._paused_until = time.monotonic() + BREAKER_PAUSE
            # the type and status only: the message of an HTTP error holds the URL, and the URL holds the question
            self._last_error = f"{self.name}: {type(error).__name__}" + (f" {status}" if status else "")
            self._last_error_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            self._last_latency_ms = round(seconds * 1000)

    def get(self, url: str, **params) -> bytes:
        """The body of a GET, cut at ``max_bytes``. The whole request must end within the source timeout."""
        return self._read(self.http.get, url, params=params)

    def get_json(self, url: str, **params):
        body = self.get(url, **params)
        # a reply that FlareSolverr solved is the page a browser shows, with the JSON inside its <pre>
        if page := PAGE_PRE.match(body):
            body = unescape(page[1].decode("utf-8")).encode("utf-8")
        return json.loads(body)

    def post_json(self, url: str, body: dict):
        return json.loads(self._read(self.http.post, url, json=body))

    def _read(self, send, url: str, **kwargs) -> bytes:
        with send(url, timeout=self.timeout, stream=True, **kwargs) as resp:
            # curl_cffi and FlareSolverr return the whole reply within their own timeout; this bounds a slow stream
            deadline = time.monotonic() + self.timeout
            resp.raise_for_status()
            body = bytearray()
            for block in resp.iter_content(64 * 1024):
                body += block
                if len(body) >= self.max_bytes:
                    break
                if time.monotonic() > deadline:
                    raise requests.Timeout(f"{url}: no complete reply in {self.timeout} s")
        return bytes(body[:self.max_bytes])


class MediaWiki(RemoteSource):
    """A Wikimedia wiki: full-text search, then the plain text of each page found."""
    host = ""
    homepage = ""
    licence = "CC BY-SA 4.0"

    @property
    def api(self) -> str:
        return f"https://{self.host}/w/api.php"

    def titles(self, question: str) -> list[str]:
        """The page titles to look up before the keyword search."""
        return self.names(question)

    def fetch(self, question: str) -> list[Document]:
        # a keyword search needs every word in the page, so the page named in the question comes first
        ids = []
        if names := self.titles(question):
            titled = self.get_json(self.api, action="query", titles="|".join(names[:self.pool]), redirects=1,
                                   format="json", formatversion=2)
            ids = [page["pageid"] for page in titled["query"]["pages"] if "pageid" in page]
        found = self.get_json(self.api, action="query", list="search", srsearch=self.query(question),
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


# the words a question uses to ask about a word, such as "Como se pronuncia a palavra cônjuge?"
WORD_TALK = {"palavra", "palavras", "termo", "expressão", "origem", "etimologia", "pronuncia", "pronúncia", "pronunciar",
             "escreve", "escrever", "plural", "feminino", "masculino", "sinónimo", "sinónimos", "antónimo", "quer", "dizer",
             "quê"}


class Wiktionary(MediaWiki):
    name = "pt.wiktionary"
    title = "Wikcionário"
    host = "pt.wiktionary.org"
    homepage = "https://pt.wiktionary.org"
    description = "Dictionary entries: meanings, grammar and pronunciation."

    def titles(self, question: str) -> list[str]:
        # an entry title is the word in lower case, as the question asks it; two lookups leave room for the search
        # the first word of a question has a capital whether or not its entry has one: "Saudade significa o quê?"
        first = question.split()[0].strip(".,;:!?¿¡«»\"'()") if question.split() else ""
        names = self.names(question)
        lead = [first.lower(), first] if first in names else []
        names = [name for name in names if name != first]
        in_names = {w for name in names + lead for w in re.findall(r"\w+", name.lower())}
        words = [w for w in self.terms(question) if w not in WORD_TALK and w not in in_names]
        return list(dict.fromkeys(lead + words + names))[:2]


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
        found = self.get_json(self.api, action="query", list="search", srsearch=self.query(question),
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
                            languages="pt|pt-br|mul", format="json")
            out.update({k: v for k, v in data.get("entities", {}).items() if "missing" not in v})
        return out

    def _label(self, entity: dict, field: str = "labels") -> str:
        values = entity.get(field, {})
        # "mul" is a label that every language shares, such as the name of a person
        return (values.get("pt") or values.get("pt-br") or values.get("mul") or {}).get("value", "")

    def _values(self, entity: dict):
        """The properties with a value to state, each with its values, the preferred one first. A deprecated value
        is wrong or out of date, so it is left out."""
        kept = []
        for prop, claims in entity.get("claims", {}).items():
            claims = [c for c in claims if c.get("rank") != "deprecated" and c["mainsnak"].get("snaktype") == "value"
                      and c["mainsnak"].get("datatype") not in self.skipped_types]
            if claims:
                claims.sort(key=lambda c: c.get("rank") != "preferred")
                kept.append((prop, [c["mainsnak"] for c in claims[:self.max_values]]))
        return kept[:self.max_properties]

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
        except (requests.RequestException, OSError, RuntimeError) as e:
            LOG.warning("arquivo.pt: could not read %s: %s", hit.get("linkToArchive"), e)
            return None
        # the extracted text has no paragraph breaks; one sentence per line lets the chunker split it
        text = re.sub(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", "\n\n", text)
        key = hashlib.sha256(hit["linkToArchive"].encode()).hexdigest()[:12]
        stamp = str(hit.get("tstamp", ""))[:8]
        title = f"{hit.get('title') or hit.get('originalURL')}, {stamp[:4]}-{stamp[4:6]}-{stamp[6:]}" if stamp \
            else hit.get("title") or hit.get("originalURL", "")
        return Document(key, title, hit["linkToArchive"], text)


def tails(name: str) -> list[str]:
    """The name, then each part of it after "de", "da" or "do": "Câmara Municipal de Vila Nova de Gaia" gives
    "Vila Nova de Gaia" and "Gaia" after it."""
    return [name] + [name[m.end():] for m in re.finditer(r"\s+d[aeo]s?\s+", name)]


def plain(text: str) -> str:
    """Text without HTML tags or Markdown links, one paragraph per block."""
    text = unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)</p>|<br\s*/?>", "\n\n", text)))
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip())


def number(value: float, decimals: int = 0) -> str:
    """A number in the pt-PT form: a space between thousands and a decimal comma."""
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


class GovPt(RemoteSource):
    """The public services portal: services, practical guides and news, found by the search of the portal."""
    name = "gov.pt"
    title = "gov.pt"
    homepage = "https://www.gov.pt"
    licence = "gov.pt terms of use; no reuse licence stated"
    commercial = False
    description = ("Public services for citizens and companies, practical guides, news, public bodies, and service "
                   "desks such as employment centres and Lojas do Cidadão, with address and opening hours.")
    api = "https://www.gov.pt/api/post-search-result"
    pool = 6
    searches = 6

    def fetch(self, question: str) -> list[Document]:
        # the portal finds only pages with every word, and a question often holds a word that no page has, such as
        # "peço" or "horário"; so the search also runs on each run of adjacent words, longest first
        terms = self.terms(question)
        runs = [terms[i:i + size] for size in range(len(terms), 0, -1) for i in range(len(terms) - size + 1)]
        queries = [" ".join(run) for run in runs][:self.searches]
        if not queries:
            return []
        with ThreadPoolExecutor(len(queries)) as pool:
            hits = {hit["_id"]: hit for found in pool.map(self.query, queries) for hit in found}
        return [doc for hit in hits.values() if (doc := self.document(hit))]

    def query(self, value: str) -> list[dict]:
        # the request of the search box on www.gov.pt; the portal publishes no contract for it
        params = {"size": str(self.pool), "start": "0", "hasThemes": False, "hasSubThemes": False, "value": value,
                  "category": ""}
        data = self.post_json(self.api, {"method": "POST", "body": {"id": "geral_search_template", "params": params}})
        return data["hits"]["hits"]

    def document(self, hit: dict) -> Document | None:
        found = hit["_source"]
        title = (found.get("title") or "").strip()
        if not title:
            return None
        paragraphs = []
        if address := found.get("mainAddress") or {}:
            # a service desk: its description is the same text for every desk of the body that runs it
            place = ", ".join(" ".join(str(address[k]).split()) for k in ("address", "postalCode") if address.get(k))
            hours = " ".join((found.get("openingHours") or "").split()).rstrip(".")
            # one paragraph, so the passage that says where a desk is also says when it is open
            paragraphs.append(" ".join(filter(None, (f"{title} fica em {place}." if place else "",
                                                     f"Horário de {title}: {hours}." if hours else ""))))
        elif found.get("category") == "Entidades":
            facts = {c.get("name"): c.get("value") for c in found.get("categories") or []}
            area = f" da área {facts['Areas']}" if facts.get("Areas") else ""
            district = f", no distrito de {facts['Distritos']}" if facts.get("Distritos") else ""
            paragraphs.append(f"{title} é uma entidade pública{area}{district}.")
            if found.get("website"):
                paragraphs.append(f"O sítio de {title} é {found['website']}.")
        else:
            paragraphs.append(plain(found.get("description") or ""))
        text = "\n\n".join(p for p in paragraphs if p)
        if not text:
            return None
        published = (found.get("publicationDate") or "")[:10]
        # a news item says "today" and "this year", so its title carries the day it was published
        heading = f"{title}, {published}" if published else title
        url = urljoin(self.homepage + "/", found.get("url") or "")
        return Document(hit["_id"], heading, url, f"# {heading}\n\n{text}", "markdown")


def contacts(data: dict) -> str:
    found = [f"telefone {data['telefone']}" if data.get("telefone") else "",
             f"correio eletrónico {data['email']}" if data.get("email") else "",
             f"sítio {data['sitio']}" if data.get("sitio") else ""]
    found = [c for c in found if c]
    return f" Contactos: {', '.join(found)}." if found else ""


class GeoApiPt(RemoteSource):
    """The municipalities, parishes and postal codes named in the question, each fact a sentence."""
    name = "geoapi.pt"
    title = "GEO API PT"
    homepage = "https://geoapi.pt"
    licence = "geoapi.pt terms; data from DGT (CAOP), INE (Censos) and CTT (postal codes)"
    commercial = False
    description = ("Municipalities: district, area, Censos 2011 and 2021 counts, town hall contacts and parishes. "
                   "Parishes: municipality, area, Censos 2021 count and parish council contacts. "
                   "Postal codes: place and streets.")
    api = "https://json.geoapi.pt"
    pool = 3
    max_bytes = 16_000_000      # a reply holds the boundary geometry; Ponta Delgada is 2.6 MB

    def fetch(self, question: str) -> list[Document]:
        lookups = [(self.place, name) for name in self.names(question)]
        lookups += [(self.postal_code, code) for code in POSTAL_CODE.findall(question)]
        lookups = lookups[:self.pool]
        with ThreadPoolExecutor(max(len(lookups), 1)) as pool:
            return [doc for doc in pool.map(lambda lookup: lookup[0](lookup[1]), lookups) if doc]

    def lookup(self, path: str):
        """The JSON at ``path``, or None when the service has no such place."""
        try:
            return self.get_json(f"{self.api}/{quote(path)}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def place(self, name: str) -> Document | None:
        """The first municipality or parish among the tails of the name: "Junta de Freguesia de Canaviais" is the
        parish Canaviais."""
        for tail in tails(name):
            if (found := self.lookup(f"municipio/{tail}")) and found.get("nome"):
                return self.municipality(found)
            if (found := self.lookup(f"freguesia/{tail}")) and found.get("nome"):
                return self.parish(found)
        return None

    def municipality(self, data: dict) -> Document:
        town = data["nome"]
        region = f"do distrito de {data['distrito']}" if data.get("distrito") else f"da {data['distrito_ilha']}"
        lines = [f"{town} é um município {region}."]
        area = (((data.get("geojsons") or {}).get("municipio") or {}).get("properties") or {}).get("Area_T_ha")
        if area:
            lines.append(f"A área do município de {town} é de {number(area / 100, 2)} km².")
        census = data.get("censos2021") or {}
        if "N_INDIVIDUOS" in census:
            lines.append(f"Nos Censos de 2021, o município de {town} tinha {number(census['N_INDIVIDUOS'])} habitantes: "
                         f"{number(census['N_INDIVIDUOS_H'])} homens e {number(census['N_INDIVIDUOS_M'])} mulheres.")
            lines.append(f"Nos Censos de 2021, {number(census['N_INDIVIDUOS_0_14'])} residentes de {town} tinham "
                         f"menos de 15 anos e {number(census['N_INDIVIDUOS_65_OU_MAIS'])} tinham 65 anos ou mais.")
            lines.append(f"Nos Censos de 2021, o município de {town} tinha {number(census['N_EDIFICIOS_CLASSICOS'])} "
                         f"edifícios e {number(census['N_ALOJAMENTOS_TOTAL'])} alojamentos.")
        if "N_INDIVIDUOS_RESIDENT" in (old := data.get("censos2011") or {}):
            lines.append(f"Nos Censos de 2011, o município de {town} tinha {number(old['N_INDIVIDUOS_RESIDENT'])} "
                         "habitantes.")
        if data.get("morada"):
            lines.append(f"A Câmara Municipal de {town} fica em {data['morada']}, {data.get('codigopostal') or ''}."
                         f"{contacts(data)}".replace(", .", "."))
        parishes = (self.lookup(f"municipio/{town}/freguesias") or {}).get("freguesias") or []
        if parishes:
            lines.append(f"O município de {town} tem {len(parishes)} freguesia{'s' if len(parishes) > 1 else ''}: "
                         f"{', '.join(parishes)}.")
        return Document(data.get("codigoine") or town, f"Município de {town}", f"{self.api}/{quote(f'municipio/{town}')}",
                        "\n\n".join(lines))

    def parish(self, data: dict) -> Document:
        parish, town = data["nome"], data.get("municipio")
        region = f", distrito de {data['distrito_ilha']}" if data.get("distrito_ilha") else ""
        lines = [f"{parish} é uma freguesia do município de {town}{region}."]
        if area := data.get("area_ha"):
            lines.append(f"A área da freguesia de {parish} é de {number(float(area) / 100, 2)} km².")
        if "N_INDIVIDUOS" in (census := data.get("censos2021") or {}):
            lines.append(f"Nos Censos de 2021, a freguesia de {parish} tinha {number(census['N_INDIVIDUOS'])} "
                         "habitantes.")
        if data.get("morada"):
            lines.append(f"A Junta de Freguesia de {parish} fica em {data['morada']}, {data.get('codigopostal') or ''}."
                         f"{contacts(data)}".replace(", .", "."))
        return Document(data.get("codigoine") or parish, f"Freguesia de {parish}",
                        f"{self.api}/{quote(f'freguesia/{parish}')}?municipio={quote(town or '')}", "\n\n".join(lines))

    def postal_code(self, code: str) -> Document | None:
        data = self.lookup(f"cp/{code}")
        if not data or not data.get("Localidade"):
            return None
        lines = [f"O código postal {code} é de {data['Localidade']}, no município de {data.get('Concelho')}, "
                 f"distrito de {data.get('Distrito')}."]
        streets = [f"{part['Artéria']} ({part['Troço']})" if part.get("Troço") else part["Artéria"]
                   for part in data.get("partes") or [] if part.get("Artéria")]
        if streets:
            lines.append(f"O código postal {code} abrange: {'; '.join(streets[:20])}.")
        return Document(code, f"Código postal {code}", f"{self.api}/cp/{code}", "\n\n".join(lines))


WIND = {"N": "norte", "NE": "nordeste", "E": "leste", "SE": "sudeste", "S": "sul", "SW": "sudoeste", "W": "oeste",
        "NW": "noroeste"}
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
WARNING_LEVELS = {"yellow": "amarelo", "orange": "laranja", "red": "vermelho"}
# the words of a question about earthquakes, the sea or the UV index, after fold()
SEISMIC = re.compile(r"\b(?:sismos?|sismic[ao]s?|terramotos?|abalos?(?! financeir| politic| emociona)|tremor(?:es)? de terra)\b")
SEA = re.compile(r"\b(?:mar|ondas?(?! de (?:calor|frio|choque))|ondulacao|agitacao maritima|surf)\b")
UV = re.compile(r"\b(?:uv|ultravioleta)\b")
SEISMIC_AREAS = {7: ("Portugal continental e Madeira", "em Portugal continental e na Madeira"), 3: ("Açores", "nos Açores")}
MAX_QUAKES = 20     # quakes listed for one area
UV_RISK = ((3, "baixo"), (6, "moderado"), (8, "elevado"), (11, "muito elevado"))   # WHO bands of the whole index; 11 and over is extremo
NO_MAGNITUDE = -9     # IPMA gives -99 to a quake that has no magnitude


class Ipma(RemoteSource):
    """Weather forecasts and warnings for the places named in the question that IPMA forecasts."""
    name = "ipma"
    title = "IPMA"
    homepage = "https://www.ipma.pt"
    licence = "IPMA open data terms; IPMA asks each user to tell webmaster@ipma.pt what the data is used for"
    commercial = False
    description = ("Five-day forecasts and weather warnings for the district capitals and the islands; the "
                   "earthquakes of the last 30 days; sea state on the coast; and the UV index.")
    api = "https://api.ipma.pt/open-data"
    pool = 2
    today = staticmethod(lambda: datetime.now(LISBON).date())

    @cached_property
    def places(self) -> dict[str, dict]:
        return {fold(p["local"]): p for p in self.get_json(f"{self.api}/distrits-islands.json")["data"]}

    @cached_property
    def weather_types(self) -> dict[int, str]:
        return {t["idWeatherType"]: t["descWeatherTypePT"]
                for t in self.get_json(f"{self.api}/weather-type-classe.json")["data"]}

    @cached_property
    def wind_classes(self) -> dict[int, str]:
        return {int(c["classWindSpeed"]): c["descClassWindSpeedDailyPT"]
                for c in self.get_json(f"{self.api}/wind-speed-daily-classe.json")["data"]}

    @cached_property
    def coast(self) -> dict[str, dict]:
        """The places of the sea forecast, keyed by the folded name before the comma: "Faro, costa" is faro."""
        return {fold(p["local"].split(",")[0]): p for p in self.get_json(f"{self.api}/sea-locations.json")}

    def fetch(self, question: str) -> list[Document]:
        asked, names = fold(question), self.names(question)
        # the longest tail that is a place: "Vila do Porto" is not also Porto
        named = [next((fold(t) for t in tails(name) if fold(t) in self.places), "") for name in names]
        places = list({key: self.places[key] for key in named if key}.values())[:self.pool]
        docs = []
        if SEISMIC.search(asked):
            # a question that names the Azores, or Madeira or the mainland, asks about the areas it names
            areas = [area for area, pattern in ((7, r"\b(?:madeira|continente)\b"), (3, r"\bacores\b"))
                     if re.search(pattern, asked)] or list(SEISMIC_AREAS)
            docs += [doc for area in areas if (doc := self.earthquakes(area, names))]
        if SEA.search(asked):
            coast = [next((fold(t) for t in tails(name) if fold(t) in self.coast), "") for name in names]
            if keys := [key for key in dict.fromkeys(coast) if key][:self.pool]:
                days = [self.get_json(f"{self.api}/forecast/oceanography/daily/hp-daily-sea-forecast-day{day}.json")
                        for day in range(3)]
                docs += [self.sea(self.coast[key], days) for key in keys]
        if UV.search(asked) and places:
            docs += [doc for place in places if (doc := self.uv(place))]
        if places:
            warnings = self.get_json(f"{self.api}/forecast/warnings/warnings_www.json")
            docs += [self.forecast(place, warnings) for place in places]
        return docs

    def earthquakes(self, area: int, names: list[str]) -> Document | None:
        """The quakes of the last 30 days in one area: a count with the largest, then each quake that people felt,
        that has magnitude 2.5 or more, or that is in a named place, the named ones first and then the newest."""
        quakes = [q for q in self.get_json(f"{self.api}/observation/seismic/{area}.json")["data"]
                  if q.get("time") and q.get("magnitud") and float(q["magnitud"]) > NO_MAGNITUDE]
        if not quakes:
            return None
        title, where = SEISMIC_AREAS[area]
        largest = max(quakes, key=lambda q: float(q["magnitud"]))
        last = max(q["time"] for q in quakes)[:10]
        lines = [f"Nos 30 dias até {last}, o IPMA registou {len(quakes)} sismo{'s' if len(quakes) > 1 else ''} "
                 f"{where}. O maior teve magnitude {number(float(largest['magnitud']), 1)}, em {largest['time'][:10]}, "
                 f"na região {' '.join(largest['obsRegion'].split())}."]
        keys = [re.compile(rf"\b{re.escape(fold(t))}\b") for name in names for t in tails(name)]
        def in_named(q):
            # the region starts with a compass point, such as "SE Monchique"
            region = re.sub(r"^[NSEW]{1,3}\s+", "", q["obsRegion"].strip())
            return any(key.search(fold(f"{region} {q.get('local') or ''}")) for key in keys)
        listed = [q for q in quakes if q.get("sensed") or float(q["magnitud"]) >= 2.5 or in_named(q)]
        listed.sort(key=lambda q: q["time"], reverse=True)
        listed.sort(key=lambda q: not in_named(q))
        for q in listed[:MAX_QUAKES]:
            near = f", perto de {q['local']}" if q.get("local") else ""
            felt = f" Foi sentido, com intensidade {q['degree']}." if q.get("sensed") and q.get("degree") else \
                " Foi sentido." if q.get("sensed") else ""
            depth = f", a {q['depth']} km de profundidade" if q.get("depth") is not None else ""
            lines.append(f"Sismo de magnitude {number(float(q['magnitud']), 1)} em {q['time'][:10]} às {q['time'][11:16]} "
                         f"UTC, na região {' '.join(q['obsRegion'].split())}{near}{depth}.{felt}")
        return Document(f"seismic-{area}", f"Sismos registados pelo IPMA: {title}",
                        f"{self.api}/observation/seismic/{area}.json", "\n\n".join(lines))

    def sea(self, place: dict, days: list[dict]) -> Document:
        local, today = place["local"].split(",")[0], self.today()
        lines = []
        for forecast in days:
            for p in forecast["data"]:
                if p["globalIdLocal"] == place["globalIdLocal"]:
                    when = self.label(date.fromisoformat(forecast["forecastDate"]), today)
                    wave = WIND.get(p.get("predWaveDir"), p.get("predWaveDir") or "")
                    def span(lo, hi):
                        low, high = number(float(p[lo]), 1), number(float(p[hi]), 1)
                        return low if low == high else f"{low} a {high}"
                    lines.append(f"Estado do mar previsto pelo IPMA para {local}, {when}: ondulação de {wave} com "
                                 f"{span('waveHighMin', 'waveHighMax')} m e período de {span('wavePeriodMin', 'wavePeriodMax')} s; "
                                 f"altura total do mar de {span('totalSeaMin', 'totalSeaMax')} m; temperatura da água do "
                                 f"mar de {span('sstMin', 'sstMax')} °C.")
        head = f"O IPMA prevê o estado do mar para {local} nos próximos 3 dias."
        return Document(f"sea-{place['globalIdLocal']}", f"Estado do mar previsto pelo IPMA para {local}",
                        f"{self.api}/forecast/oceanography/daily/", "\n\n".join([head] + lines))

    def uv(self, place: dict) -> Document | None:
        local, today = place["local"], self.today()
        days = sorted((u["data"], float(u["iUv"])) for u in self.get_json(f"{self.api}/forecast/meteorology/uv/uv.json")
                      if u["globalIdLocal"] == place["globalIdLocal"])
        if not days:
            return None
        lines = [f"Índice ultravioleta previsto pelo IPMA para {local}, {self.label(date.fromisoformat(day), today)}: "
                 f"{number(index, 1)}, risco {next((risk for top, risk in UV_RISK if int(index + 0.5) < top), 'extremo')}."
                 for day, index in days]
        return Document(f"uv-{place['globalIdLocal']}", f"Índice ultravioleta previsto pelo IPMA para {local}",
                        f"{self.api}/forecast/meteorology/uv/uv.json", "\n\n".join(lines))

    def label(self, when: date, today: date) -> str:
        """The weekday and the date, with hoje, amanhã or depois de amanhã when it is one of those."""
        relative = {0: "hoje", 1: "amanhã", 2: "depois de amanhã"}.get((when - today).days)
        return f"{WEEKDAYS[when.weekday()]}, {when.isoformat()}" + (f" ({relative})" if relative else "")

    def forecast(self, place: dict, warnings: list[dict]) -> Document:
        url = f"{self.api}/forecast/meteorology/cities/daily/{place['globalIdLocal']}.json"
        local, today = place["local"], self.today()
        lines = [self.day(local, day, today) for day in self.get_json(url)["data"]]
        active = [w for w in warnings if w.get("idAreaAviso") == place["idAreaAviso"]
                  and w.get("awarenessLevelID") in WARNING_LEVELS]
        for w in active:
            start, end = (w[k].replace("T", " ")[:16] for k in ("startTime", "endTime"))
            text = f" {w['text'].strip()}" if (w.get("text") or "").strip() else ""
            lines.append(f"Aviso {WARNING_LEVELS[w['awarenessLevelID']]} do IPMA para a zona de {local}: "
                         f"{w['awarenessTypeName']}, de {start} a {end}.{text}")
        if not active:
            lines.append(f"O IPMA não tem avisos meteorológicos amarelos, laranja ou vermelhos para a zona de {local}.")
        return Document(str(place["globalIdLocal"]), f"Previsão do IPMA para {local}", url, "\n\n".join(lines))

    def day(self, local: str, day: dict, today: date) -> str:
        label = self.label(date.fromisoformat(day["forecastDate"]), today)
        sky = self.weather_types.get(day.get("idWeatherType"), "") if day.get("idWeatherType", -99) > 0 else ""
        facts = [sky[:1].lower() + sky[1:]]
        if "tMin" in day and "tMax" in day:
            facts.append(f"temperatura mínima {number(float(day['tMin']), 1)} °C e máxima {number(float(day['tMax']), 1)} °C")
        if float(day.get("precipitaProb", -99)) >= 0:
            facts.append(f"probabilidade de precipitação {number(float(day['precipitaProb']))} %")
        if int(day.get("classWindSpeed", -99)) > 0 and (wind := self.wind_classes.get(int(day["classWindSpeed"]))):
            facts.append(f"vento {wind.lower()}" + (f" de {WIND[day['predWindDir']]}" if day.get("predWindDir") in WIND else ""))
        return f"Previsão do IPMA para {local}, {label}: {'; '.join(f for f in facts if f)}."


class DadosGov(RemoteSource):
    """The national open data portal: the datasets that match the question, with publisher and licence."""
    name = "dados.gov.pt"
    title = "dados.gov.pt"
    homepage = "https://dados.gov.pt"
    licence = "per dataset, given in each passage"
    commercial = False
    description = "Open datasets of public bodies: what each dataset holds, who publishes it, its licence and formats."
    api = "https://dados.gov.pt/api/1/datasets/"
    pool = 5

    def fetch(self, question: str) -> list[Document]:
        data = self.get_json(self.api, q=" ".join(self.terms(question)), page_size=self.pool)
        return [doc for dataset in data.get("data", []) if (doc := self.document(dataset))]

    def document(self, dataset: dict) -> Document | None:
        title = (dataset.get("title") or "").strip()
        if not title:
            return None
        licence = dataset.get("license")
        return Document(dataset["id"], title, dataset.get("page", ""), catalogue_entry(
            title, (dataset.get("organization") or {}).get("name"), None if licence in ("notspecified", "unknown") else licence,
            dataset.get("resources", []), dataset.get("last_update"), dataset.get("description")), "markdown")


def catalogue_entry(title: str, publisher: str | None, licence: str | None, resources: list[dict], updated: str | None,
                    description: str | None, extra: str = "") -> str:
    """The passage of one dataset in a catalogue: who publishes it, its licence, formats and last update."""
    about = f"Conjunto de dados publicado por {publisher or 'um publicador sem nome'}. Licença: {licence or 'não indicada'}."
    if formats := sorted({r["format"].lower() for r in resources if r.get("format")}):
        about += f" Formatos: {', '.join(formats)}."
    if updated := (updated or "")[:10]:
        about += f" Última atualização: {updated}."
    about += extra
    text = "\n\n".join(p for p in (about, plain(description or "")) if p)
    return f"# {title}\n\n{text}"


class Ckan(RemoteSource):
    """A CKAN open data portal: the datasets that match the question, with publisher and licence."""
    licence = "per dataset, given in each passage"
    commercial = False
    pool = 5

    def fetch(self, question: str) -> list[Document]:
        found = self.get_json(f"{self.homepage}/api/3/action/package_search", q=self.query(question), rows=self.pool)
        return [self.document(p) for p in found["result"]["results"] if (p.get("title") or "").strip()]

    def document(self, package: dict) -> Document:
        title = package["title"].strip()
        return Document(package["id"], title, f"{self.homepage}/dataset/{package['name']}", catalogue_entry(
            title, (package.get("organization") or {}).get("title"), package.get("license_title"),
            package.get("resources", []), package.get("metadata_modified"), package.get("notes")), "markdown")


class CascaisDados(Ckan):
    name = "dadosabertos.cascais.pt"
    title = "Dados Abertos Cascais"
    homepage = "https://dadosabertos.cascais.pt"
    description = "Open datasets of the municipality of Cascais: what each holds, its licence and formats."


class AguedaDados(Ckan):
    name = "dadosabertos.cm-agueda.pt"
    title = "Dados Abertos Águeda"
    homepage = "https://dadosabertos.cm-agueda.pt"
    description = "Open datasets of the municipality of Águeda: what each holds, its licence and formats."



class PortoDados(Ckan):
    name = "dadosabertos.cm-porto.pt"
    title = "Dados Abertos Porto"
    homepage = "https://dadosabertos.cm-porto.pt"
    description = "Open datasets of the municipality of Porto: what each holds, its licence and formats."

    def query(self, question: str) -> str:
        # the free-text search of this portal finds no dataset, and a search by field works
        return " OR ".join(f"{field}:{word}" for word in super().query(question).split() for field in ("title", "notes"))

class Opendatasoft(RemoteSource):
    """An Opendatasoft portal: the datasets that match the question, with publisher, licence and record count."""
    licence = "per dataset, given in each passage"
    commercial = False
    pool = 5

    def fetch(self, question: str) -> list[Document]:
        query = self.query(question).replace('"', " ")
        found = self.get_json(f"{self.homepage}/api/explore/v2.1/catalog/datasets", where=f'search("{query}")',
                              limit=self.pool)
        return [doc for dataset in found.get("results", []) if (doc := self.document(dataset))]

    def document(self, dataset: dict) -> Document | None:
        meta = (dataset.get("metas") or {}).get("default") or {}
        if not (title := (meta.get("title") or "").strip()):
            return None
        count = meta.get("records_count")
        extra = f" Tem {number(int(count))} registos." if count is not None else ""
        return Document(dataset["dataset_id"], title, f"{self.homepage}/explore/dataset/{dataset['dataset_id']}/",
                        catalogue_entry(title, meta.get("publisher"), meta.get("license"), [], meta.get("modified"),
                                        meta.get("description"), extra), "markdown")


class SnsTransparencia(Opendatasoft):
    name = "transparencia.sns.gov.pt"
    title = "SNS Transparência"
    homepage = "https://transparencia.sns.gov.pt"
    description = ("Datasets of the national health service: what each holds, such as emergency visits or waiting "
                   "times, its publisher and its number of records. It does not give the numbers.")


def nifs(question: str) -> list[str]:
    """The NIFs in a question that names a tax number, with a valid check digit."""
    return [n for n in NIF.findall(question) if valid_nif(n)] if NIF_CONTEXT.search(question) else []


def valid_nif(nif: str) -> bool:
    """The check digit of a Portuguese NIF or NIPC: the weighted sum of the first eight digits, modulo 11."""
    check = 11 - sum(int(d) * w for d, w in zip(nif[:8], range(9, 1, -1))) % 11
    return int(nif[8]) == (0 if check >= 10 else check)


class Vies(RemoteSource):
    """The EU VAT register: the name and address registered for each Portuguese NIF in the question."""
    name = "vies"
    title = "VIES"
    homepage = "https://ec.europa.eu/taxation_customs/vies/"
    licence = "European Commission VIES terms; no reuse licence stated"
    commercial = False
    description = "The name and address registered for a Portuguese VAT number (NIF or NIPC) named in the question."
    api = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/PT/vat"
    pool = 3

    def fetch(self, question: str) -> list[Document]:
        numbers = list(dict.fromkeys(nifs(question)))[:self.pool]
        with ThreadPoolExecutor(max(len(numbers), 1)) as pool:
            return list(pool.map(self.company, numbers))

    def company(self, nif: str) -> Document:
        url = f"{self.api}/{nif}"
        data = self.get_json(url)
        if data.get("userError", "VALID") not in ("VALID", "INVALID"):
            raise SourceUnavailable(f"{self.name}: {data['userError']}")
        if not data.get("isValid"):
            return Document(nif, f"NIF {nif}", url, f"O NIF {nif} não tem um número de IVA válido no VIES.")
        holder = (data.get("name") or "").strip()
        address = ", ".join(line.strip() for line in (data.get("address") or "").splitlines() if line.strip())
        lines = [f"O NIF {nif} tem o número de IVA PT{nif}, válido no VIES em {(data.get('requestDate') or '')[:10]}."]
        if holder and holder != "---":
            lines.append(f"O NIF {nif} pertence a {holder}" + (f", com morada em {address}." if address else "."))
        return Document(nif, f"NIF {nif}", url, "\n\n".join(lines))


ENTITY_STATUS = {"ACTIVE": "ativa", "INACTIVE": "inativa"}


class Gleif(RemoteSource):
    """The global LEI register: Portuguese companies and other legal entities that match a name or a NIPC."""
    name = "gleif"
    title = "GLEIF"
    homepage = "https://www.gleif.org"
    licence = "CC0 1.0"
    description = ("Companies, associations and public bodies with a Legal Entity Identifier: legal name, registered "
                   "address, NIPC, date of creation and status.")
    api = "https://api.gleif.org/api/v1/lei-records"
    pool = 4

    def fetch(self, question: str) -> list[Document]:
        searches = [{"filter[entity.legalName]": name} for name in self.names(question)[:2]]
        searches += [{"filter[entity.registeredAs]": nif} for nif in nifs(question)][:2]
        if not searches:
            return []
        with ThreadPoolExecutor(len(searches)) as pool:
            pages = list(pool.map(self.lookup, searches))
        # a name with no entity may hold one at its end: "NIPC da EDP" is EDP. Only then, because the end of
        # "Câmara Municipal do Porto" is a place that many companies have in their name
        retry = [{"filter[entity.legalName]": tails(f["filter[entity.legalName]"])[-1]} for f, page in zip(searches, pages)
                 if not page and len(tails(f.get("filter[entity.legalName]", ""))) > 1]
        with ThreadPoolExecutor(max(len(retry), 1)) as pool:
            pages += pool.map(self.lookup, retry)
        records = {r["id"]: r for page in pages for r in page}
        return [self.document(r["attributes"]) for r in records.values()]

    def lookup(self, search: dict[str, str]) -> list[dict]:
        return self.get_json(self.api, **search, **{"filter[entity.legalAddress.country]": "PT",
                                                    "page[size]": self.pool})["data"]

    def document(self, record: dict) -> Document:
        entity, lei = record["entity"], record["lei"]
        legal = entity["legalName"]["name"]
        place = entity.get("legalAddress") or {}
        address = ", ".join([*place.get("addressLines", []), " ".join(filter(None, (place.get("postalCode"),
                                                                                    place.get("city"))))])
        lines = [f"{legal} é uma entidade jurídica com sede em {address}."]
        if entity.get("registeredAs"):
            lines.append(f"O NIPC de {legal} é {entity['registeredAs']}.")
        if created := (entity.get("creationDate") or "")[:10]:
            lines.append(f"{legal} foi constituída em {created}.")
        if status := ENTITY_STATUS.get(entity.get("status", "")):
            lines.append(f"O registo LEI de {legal} indica que a entidade está {status}.")
        lines.append(f"O LEI de {legal} é {lei}.")
        return Document(lei, legal, f"https://search.gleif.org/#/record/{lei}", "\n\n".join(lines))


SECOND_LEVEL = {"com", "org", "edu", "int", "publ", "net", "nome"}   # .pt names registered one level down


class DnsPt(RemoteSource):
    """The .pt registry: holder, dates and name servers of each .pt domain in the question, from its WHOIS."""
    name = "dns.pt"
    title = "DNS.pt"
    homepage = "https://www.dns.pt"
    licence = "DNS.pt WHOIS terms; no reuse licence stated"
    commercial = False
    description = "The holder, registration and expiry dates and name servers of a .pt domain named in the question."
    server = ("whois.dns.pt", 43)
    pool = 2
    connect = staticmethod(socket.create_connection)

    def fetch(self, question: str) -> list[Document]:
        domains = list(dict.fromkeys(self.registered(d.lower()) for d in DOMAIN.findall(question)))[:self.pool]
        with ThreadPoolExecutor(max(len(domains), 1)) as pool:
            return list(pool.map(self.whois, domains))

    @staticmethod
    def registered(host: str) -> str:
        """The registered domain of a host: www.sapo.pt is sapo.pt, and a.empresa.com.pt is empresa.com.pt."""
        labels = host.split(".")
        return ".".join(labels[-3:] if len(labels) > 2 and labels[-2] in SECOND_LEVEL else labels[-2:])

    def whois(self, domain: str) -> Document:
        deadline = time.monotonic() + self.timeout
        reply = bytearray()
        with self.connect(self.server, timeout=self.timeout) as conn:
            conn.sendall(f"{domain}\r\n".encode("idna"))
            while block := conn.recv(4096):
                reply += block
                if len(reply) > self.max_bytes:
                    raise ValueError(f"{self.server[0]}: reply of more than {self.max_bytes} bytes")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{self.server[0]}: no complete reply in {self.timeout} s")
        text = reply.decode("utf-8", errors="replace")
        url = f"{self.homepage}/pt/"
        if "no match" in text.casefold():
            return Document(domain, domain, url, f"O domínio {domain} não está registado.")
        fields: dict[str, list[str]] = {}
        for line in text.splitlines():
            key, sep, value = line.partition(":")
            if sep and value.strip():
                fields.setdefault(key.strip(), []).append(value.strip())
        if "Domain" not in fields:
            raise ValueError(f"{self.server[0]}: no domain record in the reply for {domain}")
        first = lambda key: (fields.get(key) or [""])[0]
        day = lambda key: first(key).split(" ")[0]
        lines = []
        if first("Creation Date"):
            expiry = f" e expira em {day('Expiration Date')}" if first("Expiration Date") else ""
            lines.append(f"O domínio {domain} foi registado em {day('Creation Date')}{expiry}.")
        if first("Domain Status"):
            lines.append(f"O estado do domínio {domain} é {first('Domain Status')}.")
        if first("Owner Name"):
            locality = f", de {first('Owner Locality')}" if first("Owner Locality") else ""
            lines.append(f"O titular do domínio {domain} é {first('Owner Name')}{locality}.")
        if servers := [v.split("|")[0].strip() for v in fields.get("Name Server", [])]:
            lines.append(f"Os servidores de nomes do domínio {domain} são {', '.join(servers)}.")
        return Document(domain, domain, url, "\n\n".join(lines) or f"O domínio {domain} está registado.")


FUELS = {"gasóleo": "Gasóleo simples", "diesel": "Gasóleo simples", "gasolina": "Gasolina simples 95",
         "gpl": "GPL Auto"}     # the fuel a question means when it names no type


class Dgeg(RemoteSource):
    """Fuel prices at the filling stations of a mainland municipality or district named in the question."""
    name = "dgeg"
    title = "DGEG - Preços dos Combustíveis"
    homepage = "https://precoscombustiveis.dgeg.gov.pt"
    licence = "DGEG terms; no reuse licence stated"
    commercial = False
    description = ("Pump prices that filling stations report, cheapest first, for the mainland municipality or "
                   "district in the question.")
    api = "https://precoscombustiveis.dgeg.gov.pt/api/PrecoComb"
    pool = 3        # with the cheapest-station passage, all fit in the default top_k of 4

    @cached_property
    def fuels(self) -> dict[str, int]:
        return {fold(f["Descritivo"]): f["Id"] for f in self.get_json(f"{self.api}/GetTiposCombustiveis")["resultado"]}

    @cached_property
    def municipalities(self) -> dict[str, dict]:
        return {fold(m["Descritivo"]): m for m in self.get_json(f"{self.api}/GetMunicipios")["resultado"]}

    @cached_property
    def districts(self) -> dict[str, int]:
        return {fold(d["Descritivo"]): d["Id"] for d in self.get_json(f"{self.api}/GetDistritos")["resultado"]}

    def fetch(self, question: str) -> list[Document]:
        asked = fold(question)
        fuel = next((self.fuels[n] for n in self.fuels if n in asked), None)
        fuel = fuel or next((self.fuels.get(fold(FUELS[w])) for w in FUELS if re.search(rf"\b{fold(w)}\b", asked)), None)
        if fuel is None:
            return []
        # every district shares its name with a municipality, so the district only when the question says so
        district_first = re.search(r"\bdistrito\b", asked)
        for name in self.names(question):
            for tail in tails(name):
                town = self.municipalities.get(fold(tail))
                district = self.districts.get(fold(tail))
                if district is not None and (district_first or not town):
                    return self.stations(fuel, district, "")
                if town:
                    return self.stations(fuel, town["IdDistrito"], str(town["Id"]))
        return []

    def stations(self, fuel: int, district: int, town: str) -> list[Document]:
        found = self.get_json(f"{self.api}/PesquisarPostos", idsTiposComb=fuel, idDistrito=district,
                              idsMunicipios=town, qtdPorPagina=self.pool, pagina=1)["resultado"] or []
        found = [{k: " ".join(v.split()) if isinstance(v, str) else v for k, v in station.items()} for station in found]
        docs = []
        if found:
            # the reply is sorted by price, so the first station is the cheapest
            s, area = found[0], f"município de {found[0]['Municipio']}" if town else f"distrito de {found[0]['Distrito']}"
            docs.append(Document(f"cheapest-{fuel}-{district}-{town}", f"{s['Combustivel']} mais barato no {area}",
                                 self.homepage, f"O combustível {s['Combustivel']} mais barato no {area} custava "
                                 f"{s['Preco'].replace(' €', ' euros')} por litro, no posto {s['Nome']} ({s['Marca']}), "
                                 f"em {s['Morada']}, {s['Localidade']}, em {s['DataAtualizacao']}."))
        for rank, s in enumerate(found, 1):
            price = s["Preco"].replace(" €", " euros")
            text = (f"No posto {s['Nome']} ({s['Marca']}), em {s['Morada']}, {s['Localidade']}, município de "
                    f"{s['Municipio']}, o combustível {s['Combustivel']} custava {price} por litro em {s['DataAtualizacao']}. "
                    f"É o {rank}.º preço mais baixo do combustível {s['Combustivel']} entre os postos do "
                    f"{'município' if town else 'distrito'} de {s['Municipio'] if town else s['Distrito']}.")
            docs.append(Document(str(s["Id"]), f"{s['Nome']}, {s['Combustivel']}", self.homepage, text))
        return docs


BUS = re.compile(r"\b(?:linhas?(?! de costa| do horizonte)|autocarros?|carreiras?(?! de tiro| profissiona)|carris|paragem|paragens)\b")   # after fold()
# a line number, with the word before it; 2900-461 is a postal code
LINE = re.compile(r"\b(?:(?:linhas?|carreiras?|autocarros?)\s+(?:(?:numero|n\.?\s*[oº]?)\s*)?)?(\d{4})\b(?!-\d{3}\b)")


class CarrisMetropolitana(RemoteSource):
    """The bus lines of the Lisbon metropolitan area, and the service alerts that have not ended."""
    name = "carris-metropolitana"
    title = "Carris Metropolitana"
    homepage = "https://carrismetropolitana.pt"
    licence = "Carris Metropolitana open data; no reuse licence named (Lei n.º 68/2021)"
    commercial = False
    description = ("Bus lines of the Lisbon metropolitan area outside the city of Lisbon: the route, the "
                   "municipalities and localities each line serves, and the service alerts that have not ended.")
    api = "https://api.carrismetropolitana.pt/v2"
    pool = 3
    max_bytes = 4_000_000       # the list of localities is 3 MB
    max_listed = 60             # lines named for one place
    now = staticmethod(time.time)

    @cached_property
    def lines(self) -> dict[str, dict]:
        return {line["id"]: line for line in self.get_json(f"{self.api}/lines")}

    @cached_property
    def municipalities(self) -> dict[str, str]:
        return {m["id"]: m["name"] for m in self.get_json(f"{self.api}/locations/municipalities")["data"]
                if m.get("id") and m.get("name")}

    @cached_property
    def localities(self) -> dict[str, str]:
        # the list holds a row with no id or name
        return {loc["id"]: loc["name"] for loc in self.get_json(f"{self.api}/locations/localities")["data"]
                if loc.get("id") and loc.get("name")}

    def fetch(self, question: str) -> list[Document]:
        asked = fold(question)
        if not BUS.search(asked):
            return []
        alerts = [a for a in self.get_json(f"{self.api}/alerts")
                  if any(period.get("end", 0) >= self.now() for period in a.get("active_period", []))]
        # a number that looks like a year is a line only after the word linha: "em 2026" is a year
        numbers = [m[1] for m in LINE.finditer(asked)
                   if m[1] in self.lines and (m[0] != m[1] or not 1900 <= int(m[1]) <= 2099)][:self.pool]
        docs = [self.line(self.lines[n], alerts) for n in numbers]
        if not numbers:
            docs += [doc for name in self.names(question)[:self.pool] if (doc := self.serving(name))]
        return docs

    def line(self, line: dict, alerts: list[dict]) -> Document:
        towns = [self.municipalities[m] for m in line.get("municipality_ids", []) if m in self.municipalities]
        places = [self.localities[loc] for loc in line.get("locality_ids", []) if loc in self.localities]
        text = f"A linha {line['id']} da Carris Metropolitana faz o percurso {line['long_name']}."
        if towns:
            text += f" Serve {'o município de' if len(towns) == 1 else 'os municípios de'} {joined(towns)}"
            text += f" e as localidades {joined(places)}." if places else "."
        lines = [text]
        for alert in alerts:
            if any(e.get("route_id", "").split("]")[-1].split("_")[0] == line["id"] for e in alert.get("informed_entity", [])):
                lines.append(f"Alerta da Carris Metropolitana para a linha {line['id']}, {self.period(alert)}: "
                             f"{translated(alert, 'header_text').strip().rstrip('.')}. {translated(alert, 'description_text')}")
        return Document(line["id"], f"Linha {line['id']} da Carris Metropolitana",
                        f"{self.homepage}/lines/{line['id']}", "\n\n".join(lines))

    def serving(self, name: str) -> Document | None:
        """The lines that serve the first tail of the name that is a municipality or a locality. A municipality
        comes first: Seixal is a municipality and also a village in Mafra."""
        for tail in tails(name):
            key = fold(tail)
            towns = {i for i, n in self.municipalities.items() if fold(n) == key}
            localities = {i for i, n in self.localities.items() if fold(n) == key}
            found = [line for line in self.lines.values() if towns & set(line.get("municipality_ids", []))] or \
                [line for line in self.lines.values() if localities & set(line.get("locality_ids", []))]
            if found:
                listed = ", ".join(f"{line['id']} ({line['long_name']})" for line in found[:self.max_listed])
                return Document(f"serving-{key}", f"Linhas da Carris Metropolitana em {tail}", f"{self.homepage}/lines",
                                f"As linhas da Carris Metropolitana que servem {tail} são: {listed}.")
        return None

    def period(self, alert: dict) -> str:
        start, end = (datetime.fromtimestamp(alert["active_period"][0][k], LISBON).strftime("%Y-%m-%d %H:%M")
                      for k in ("start", "end"))
        return f"de {start} a {end}"


JOB = re.compile(r"\b(?:ofertas? de (?:emprego|trabalho)"   # after fold()
                 r"|vagas? (?:de|para)(?! (?:[ao] )?(?:calor|frio|estacionamento|estacionar|garagem|curso|universidade|escola|creche))"
                 r"|procur\w* (?:de )?(?:trabalho|emprego)|(?<!centro de )(?<!centros de )(?<!taxa de )empregos?|iefp)\b")
JOB_WORDS = {"oferta", "ofertas", "emprego", "empregos", "trabalho", "vaga", "vagas", "iefp", "procuro", "procura",
             "arranjar", "encontrar", "municipio", "concelho"}


class Iefp(RemoteSource):
    """The job offers of the public employment service for the job and the municipality in the question."""
    name = "iefp"
    title = "IEFP - Ofertas de emprego"
    homepage = "https://iefponline.iefp.pt"
    licence = "IEFP terms; no reuse licence stated"
    commercial = False
    description = ("Job offers of the public employment service: job title, municipality, schedule, contract and "
                   "required education, for the job and the municipality in the question.")
    api = "https://iefponline.iefp.pt/IEFP/pesquisas"

    def fetch(self, question: str) -> list[Document]:
        if not JOB.search(fold(question)):
            return []
        spelling, _ = gazetteer()
        town = next((spelling[fold(t)] for name in self.names(question) for t in tails(name) if fold(t) in spelling), "")
        skip = JOB_WORDS | {fold(w) for w in self.stopwords} | {fold(w) for w in town.split()}
        # the server reads ISO-8859-1, and a word it cannot read would empty the search
        job = " ".join(w for w in self.terms(question) if fold(w) not in skip and w.encode("iso-8859-1", "ignore").decode("iso-8859-1") == w)
        if not job and not town:
            return []
        params = {"cat": "ofertaEmprego"} | ({"text": job} if job else {}) | ({"concelho_facet": town.upper()} if town else {})
        # the server reads the query in ISO-8859-1: ÉVORA in UTF-8 finds nothing
        body = self.get(f"{self.api}/search.do?{urlencode(params, encoding='iso-8859-1')}")
        try:
            page = body.decode("utf-8")
        except UnicodeDecodeError:    # the server sends ISO-8859-1 and names it only in the header
            page = body.decode("cp1252", errors="replace")
        offers = [doc for card in re.findall(r'(?s)<article class="card[^"]*card-oferta.*?</article>', page)
                  if (doc := self.offer(card))]
        if total := re.search(r'id="totalResultados">\s*(\d+)', page):
            count = int(total[1])
        elif "foram encontrados resultados" in unescape(page):
            count = 0
        else:
            return offers
        # the text search finds an offer with any of the words, so the count is the count of a search
        found = f"oferta{'s' if count != 1 else ''} de emprego"
        where = f" no município de {town}" if town else ""
        if job and count:
            head = f'A pesquisa do IEFP por "{job}" encontra {number(count)} {found}{where}.'
        elif job:
            head = f'A pesquisa do IEFP por "{job}" não encontra ofertas de emprego{where}.'
        elif count:
            head = f"O IEFP tem {number(count)} {found}{where}."
        else:
            head = f"O IEFP não tem ofertas de emprego{where}."
        return [Document(f"total-{job}-{town}", "Ofertas de emprego do IEFP", f"{self.api}/search.do?cat=ofertaEmprego",
                         head)] + offers

    def offer(self, card: str) -> Document | None:
        def field(label: str) -> str:
            found = re.search(rf"(?s){label}\s*</div>\s*<div><strong>(.*?)</strong></div>", card)
            return " ".join(unescape(re.sub(r"<[^>]+>", "", found[1])).split()) if found else ""
        offer = re.search(r'id="ofertacard_(\d+)"', card)
        title = re.search(r'(?s)min-height: 3rem;"><strong>(.*?)</strong>', card)
        if not offer or not title:
            return None
        title = " ".join(unescape(title[1]).split())
        place, _, schedule = field("Localização e Horário").partition("|")
        place = " ".join(place.split())
        spelling, _ = gazetteer()
        place = spelling.get(fold(place), place.title())
        contract = re.sub(r"\s*\((.*)\)", r", \1", field("Tipo de contrato")).lower()
        form = field("Forma de prestação de trabalho").lower()
        conditions = [" ".join(unescape(re.sub(r"<[^>]+>", "", li)).split()) for li in re.findall(r"(?s)<li>(.*?)</li>", card)]
        text = f"Oferta de emprego do IEFP n.º {offer[1]}: {title}, em {place}"
        text += f", horário {schedule.strip().lower()}." if schedule.strip() else "."
        if contract:
            text += f" Contrato {contract}."
        if form:
            text += f" Trabalho {form}."
        for condition in conditions:
            label, _, value = condition.partition(":")
            text += f" {label}: {value.strip().lower()}." if value else f" {condition}."
        return Document(offer[1], title, f"{self.api}/detalheOfertas2.do?idOferta={offer[1]}", text)


class Rss(RemoteSource):
    """An RSS 2.0 feed of headlines: each item is one passage with its section, its time and its link. The
    retrieval ranks the items against the question."""
    feed = ""
    publisher = ""

    def fetch(self, question: str) -> list[Document]:
        root = ET.fromstring(self.get(self.feed))
        docs = []
        for item in root.iterfind("./channel/item"):
            title = " ".join((item.findtext("title") or "").split())
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            head = f"Notícia da {self.publisher}"
            if section := " ".join((item.findtext("category") or "").split()):
                head += f", secção {section}"
            try:
                stamp = parsedate_to_datetime((item.findtext("pubDate") or "").strip())
            except (TypeError, ValueError):
                stamp = None
            if stamp:
                # RFC 2822 "-0000" is a time in UTC with no known zone, and Python leaves it naive
                stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
                head += f", de {stamp.astimezone(LISBON).strftime('%Y-%m-%d %H:%M')}"
            docs.append(Document(link, title, link, f"{head}: {title.rstrip('.')}."))
        return docs


class RtpNoticias(Rss):
    name = "rtp-noticias"
    title = "RTP Notícias"
    homepage = "https://www.rtp.pt/noticias"
    licence = "RTP terms; headlines and links only, the article text is not used"
    commercial = False
    description = "The latest 50 news headlines of the public broadcaster, with section, time and link."
    feed = "https://www.rtp.pt/noticias/rss"
    publisher = "RTP"


def joined(items: list[str]) -> str:
    """"A", "A e B", "A, B e C"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} e {items[-1]}"


def translated(alert: dict, field: str) -> str:
    texts = (alert.get(field) or {}).get("translation", [])
    return next((t["text"] for t in texts if t.get("language") == "pt"), texts[0]["text"] if texts else "").strip()


CATALOG: dict[str, type[RemoteSource]] = {cls.name: cls for cls in
                                          (Wikipedia, Wikisource, Wiktionary, Wikidata, DicionarioAberto, ArquivoPt,
                                           GovPt, GeoApiPt, Ipma, DadosGov, Vies, Gleif, DnsPt, Dgeg, CarrisMetropolitana, RtpNoticias,
                                           CascaisDados, AguedaDados, PortoDados, SnsTransparencia, Iefp)}


def build_sources(names: list[str], user_agent: str, stopwords: frozenset[str], lang: str, timeout: float,
                  embed: Embed | None = None, *, transport: str | None = None, flaresolverr_url: str | None = None,
                  browserless_url: str | None = None) -> dict[str, RemoteSource]:
    """The named sources, sharing one session. It sends each request with the TLS handshake of a browser, and
    with ``flaresolverr_url`` it sends a GET that a site refuses again through FlareSolverr. It never answers from
    an archived copy, because a passage must say what the source says now."""
    unknown = set(names) - set(CATALOG)
    if unknown:
        raise ValueError(f"unknown sources {sorted(unknown)}; known: {sorted(CATALOG)}")
    from curl_cffi.const import CurlOpt
    http = CloudflareSession(mode=transport or "curl_cffi", flaresolverr_url=flaresolverr_url, flaresolverr_fallback=True,
                             browserless_url=browserless_url, wayback_fallback=False, env_prefix="EVIDENCIA_SESSION",   # Config reads the documented variables
                             flaresolverr_timeout_ms=int(timeout * 1000), browserless_timeout_ms=int(timeout * 1000),
                             # curl reads a whole reply before the source sees it, so curl stops at the largest cap
                             curl_options={CurlOpt.MAXFILESIZE_LARGE: max(CATALOG[n].max_bytes for n in names or CATALOG)})
    http.headers["User-Agent"] = user_agent
    return {name: CATALOG[name](http, stopwords, lang, timeout, embed) for name in names}
