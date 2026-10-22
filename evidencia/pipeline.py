"""Retrieve, gate, generate, check."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

import requests
from ovos_plugin_manager.embeddings import load_embeddings_db_plugin

from evidencia.config import Config
from evidencia.decision import BATCH, Decision, DecisionRejected, DecisionUnavailable
from evidencia.embeddings import Embedder, PluginEmbedder
from evidencia.generators import PRESETS, build_generator
from evidencia.gates import Gates, Verdict
from evidencia.index import Collection, Passage, db_collection_name
from evidencia.sources import Source, SourceUnavailable, build_sources
from evidencia.store import Store

LOG = logging.getLogger("evidencia")
# "[2]" or "[1, 3]" after a fact: the evidence numbers the chat model cites
CITATION = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")
# called with an event name and its data at each step of a request
EventSink = Callable[[str, dict], None]


class GeneratorUnavailable(RuntimeError):
    """No chat engine is configured, or it failed to produce an answer. ``reason`` is safe to show a client."""

    def __init__(self, message: str, reason: str = "generator unavailable"):
        super().__init__(message)
        self.reason = reason


@dataclass
class ScoredPassage:
    passage: Passage
    p_select: float | None = None

    def as_dict(self) -> dict:
        return {**asdict(self.passage), "p_select": self.p_select}


@dataclass
class ContextResult:
    question: str
    evidence: list[ScoredPassage]
    candidates: list[ScoredPassage]
    rounds: int
    sufficient: Verdict | None
    gated: bool                       # False when the decision backend was unavailable and fail_open applied
    select_threshold: float = 0.5
    timings_ms: dict[str, float] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)

    @property
    def abstain(self) -> bool:
        return self.gated and (self.sufficient is None or not self.sufficient.passed)

    @property
    def abstained_at(self) -> str | None:
        """The step that stopped the request: retrieve, select or sufficient. None when the evidence suffices."""
        if not self.candidates:
            return "retrieve"
        if not self.abstain:
            return None
        return "select" if not self.evidence else "sufficient"

    def as_dict(self) -> dict:
        return {"question": self.question,
                "evidence": [p.as_dict() for p in self.evidence],
                "candidates": [p.as_dict() for p in self.candidates],
                "rounds": self.rounds,
                "select_threshold": self.select_threshold,
                "sufficient": self.sufficient.as_dict() if self.sufficient else None,
                "gates": "applied" if self.gated else "unavailable",
                "abstain": self.abstain,
                "abstained_at": self.abstained_at,
                "sources": self.sources,
                "timings_ms": self.timings_ms}


def cited(answer: str, evidence_count: int) -> list[int]:
    """The evidence numbers that ``answer`` cites, ignoring numbers that name no passage."""
    numbers = {int(n) for group in CITATION.findall(answer) for n in group.split(",")}
    return sorted(n for n in numbers if 1 <= n <= evidence_count)


def strip_citations(answer: str, evidence_count: int) -> str:
    """``answer`` without its citation marks. A bracketed number that names no passage is text, and stays."""
    def mark(match: re.Match) -> str:
        numbers = [int(n) for n in match[1].split(",")]
        return "" if all(1 <= n <= evidence_count for n in numbers) else match[0]
    return CITATION.sub(mark, answer).strip()


def _interleave(lists: Iterable[list[Passage]]) -> list[Passage]:
    """Round-robin by rank: the scores of different sources are not comparable, their ranks are."""
    lists = [lst for lst in lists if lst]
    return [lst[i] for i in range(max(map(len, lists), default=0)) for lst in lists if i < len(lst)]


def _key(passage: Passage) -> tuple[str, str]:
    return passage.origin, passage.id


class Evidencia:
    def __init__(self, config: Config | None = None, decision: Decision | None = None, chat: Any = None,
                 sources: dict[str, Source] | None = None):
        self.config = config or Config()
        self.decision = decision or self.config.decision.build()
        self.gates = Gates(self.decision, self.config.lang)
        if chat is None and self.config.generator in PRESETS:
            # built here, so a wrong model name or a missing key stops the service at start
            chat = build_generator(self.config.generator, self.config.generator_config,
                                   self.gates.locale["system_prompt"])
        self._chat = chat
        self._chat_lock = threading.Lock()
        self._collections_lock = threading.Lock()
        if self.config.embedder:
            self.embed = PluginEmbedder(self.config.embedder, self.config.embedder_config)
        else:
            self.embed = Embedder(self.config.embedding_model) if self.config.embedding_model else None
        width = self.embed.load() if self.embed else None
        self.vector_db = self._vector_db(width) if self.config.vector_db else None
        # services that share a database server keep apart by their data_dir; without one nothing persists
        self.db_namespace = os.path.realpath(self.config.data_dir) if self.config.data_dir else uuid.uuid4().hex
        self.store = Store(self.config.data_dir, self.embed.id if self.embed else None,
                           width) if self.config.data_dir else None
        self.collections: dict[str, Collection] = self.store.load(self.embed, self.vector_db, self.db_namespace) if self.store else {}
        if sources is None:
            sources = build_sources(self.config.sources, self.config.user_agent,
                                    frozenset(self.gates.locale.get("stopwords", ())), self.config.lang,
                                    self.config.source_timeout, self.embed, transport=self.config.transport,
                                    flaresolverr_url=self.config.flaresolverr_url,
                                    browserless_url=self.config.browserless_url)
        self.sources: dict[str, Source] = sources

    def _vector_db(self, width: int):
        plugin = load_embeddings_db_plugin(self.config.vector_db)
        if plugin is None:
            raise ValueError(f"embeddings database plugin {self.config.vector_db!r} is not installed")
        config = dict(self.config.vector_db_config)
        if "host" not in config and "path" not in config:
            # a plugin's own default path is in the working directory
            config["path"] = os.path.join(self.config.data_dir, "vectors")
        config.setdefault("vector_size", width)
        return plugin(config=config)

    def _new_collection(self, name: str) -> Collection:
        db_name = db_collection_name(name, self.embed.id, self.db_namespace) if self.vector_db is not None else ""
        return Collection(name, lang=self.config.lang, embed=self.embed, db=self.vector_db, db_name=db_name)

    def collection(self, name: str, create: bool = False) -> Collection:
        with self._collections_lock:
            if name not in self.collections:
                if not create:
                    raise KeyError(name)
                self.collections[name] = self._new_collection(name)
            return self.collections[name]

    def add_document(self, name: str, content: str | bytes, source: str, fmt: str = "text",
                     url: str = "") -> tuple[Collection, list[Passage]]:
        """Add a document to a collection, making the collection only when the document has passages."""
        with self._collections_lock:
            col = self.collections.get(name)
        if col is None:
            # embedded outside the lock, so other requests are not held while a large document is embedded
            fresh = self._new_collection(name)
            added = fresh.add(content, source, fmt, url)
            with self._collections_lock:
                col = self.collections.setdefault(name, fresh)
            if col is fresh:
                self._save(col)
                return col, added
        added = col.add(content, source, fmt, url)
        self._save(col)
        return col, added

    def remove_document(self, name: str, source: str) -> int:
        col = self.collection(name)
        removed = col.remove(source)
        if removed:
            self._save(col)
        return removed

    def drop_collection(self, name: str) -> None:
        # under the lock: a collection of the same name created after the pop has the same database collection
        with self._collections_lock:
            col = self.collections.pop(name)
            if self.store:
                self.store.delete(name)
            col.drop()

    def _save(self, col: Collection) -> None:
        """Write ``col`` to the store, unless it was dropped while it changed."""
        with self._collections_lock:
            if self.store and self.collections.get(col.name) is col:
                self.store.save(col)

    def collections_snapshot(self) -> dict[str, Collection]:
        with self._collections_lock:
            return dict(self.collections)

    def targets(self, collection: str | None = None, sources: Sequence[str] = ()) -> list[Source]:
        """The local collection and the remote sources that a request names. KeyError: no such collection."""
        unknown = [name for name in sources if name not in self.sources]
        if unknown:
            raise ValueError(f"sources not enabled on this service: {unknown}; enabled: {sorted(self.sources)}")
        if collection is not None and collection in sources:
            raise ValueError(f"collection {collection!r} is also the name of a source in this request")
        targets: list[Source] = [self.collection(collection)] if collection is not None else []
        targets += [self.sources[name] for name in dict.fromkeys(sources)]
        if not targets:
            raise ValueError("name a collection, a source, or both")
        return targets

    @property
    def has_generator(self) -> bool:
        """True when a chat engine is given or named; whether it answers shows only on the first request."""
        return self._chat is not None or bool(self.config.generator)

    @property
    def chat(self):
        with self._chat_lock:
            if self._chat is None:
                if not self.config.generator:
                    raise GeneratorUnavailable("no generator configured")
                from ovos_plugin_manager.agents import load_chat_plugin
                engine = load_chat_plugin(self.config.generator)
                if engine is None:
                    raise GeneratorUnavailable(f"chat engine {self.config.generator!r} is not installed")
                cfg = {"system_prompt": self.gates.locale["system_prompt"], **self.config.generator_config}
                self._chat = engine(config=cfg)
            return self._chat

    def _search(self, targets: list[Source], question: str, k: int, report: dict[str, dict]) -> list[Passage]:
        """``k`` passages from each target, interleaved. A remote source that fails adds nothing."""
        def one(target: Source) -> list[Passage]:
            try:
                hits = target.search(question, k)
            except SourceUnavailable as e:
                LOG.warning("source unavailable: %s", e)
                report[target.name]["error"] = "unavailable"
                return []
            report[target.name]["error"] = None
            # passages are keyed by origin, so each one carries the name of the target that returned it
            return [p if p.origin == target.name else replace(p, origin=target.name) for p in hits]

        if len(targets) == 1:
            results = [one(targets[0])]
        else:
            with ThreadPoolExecutor(len(targets)) as pool:
                results = list(pool.map(one, targets))
        return _interleave(results)

    @staticmethod
    def _report(targets: list[Source], collection: str | None) -> dict[str, dict]:
        """Per target: its kind, how many candidates it gave, and whether it failed. The collection comes first."""
        return {t.name: {"name": t.name, "kind": "collection" if collection is not None and i == 0 else "remote",
                         "candidates": 0, "duplicates": 0, "error": None} for i, t in enumerate(targets)}

    def _novel(self, passages: list[Passage], seen: list, report: dict[str, dict]) -> list[Passage]:
        """``passages`` without those as similar as ``dedupe_threshold`` to an earlier one. ``seen`` grows."""
        if not passages or self.config.dedupe_threshold is None:
            return passages
        novel = []
        for passage, vector in zip(passages, self.embed([p.text for p in passages])):
            if any(float(vector @ other) >= self.config.dedupe_threshold for other in seen):
                report[passage.origin]["duplicates"] += 1
                continue
            seen.append(vector)
            novel.append(passage)
        return novel

    def _ungated(self, question: str, targets: list[Source], report: dict[str, dict], started: float,
                 cfg: Config) -> ContextResult:
        report = {name: {**r, "candidates": 0, "duplicates": 0} for name, r in report.items()}
        hits = [ScoredPassage(p) for p in self._novel(self._search(targets, question, cfg.top_k, report),
                                                       [], report)]
        for s in hits:
            report[s.passage.origin]["candidates"] += 1
        return ContextResult(question, hits[:cfg.top_k], hits, rounds=1, sufficient=None, gated=False,
                             select_threshold=cfg.select_threshold,
                             timings_ms={"total": (time.perf_counter() - started) * 1000},
                             sources=list(report.values()))

    def context(self, question: str, collection: str | None = None, sources: Sequence[str] = (),
                on_event: EventSink | None = None, settings: Config | None = None) -> ContextResult:
        """Retrieve candidates, keep the passages the select gate passes, and widen until the evidence is sufficient.
        ``settings`` holds the thresholds and sizes of this request, from ``Config.for_request``."""
        targets = self.targets(collection, sources)
        emit = on_event or (lambda name, data: None)
        cfg = settings or self.config
        started = time.perf_counter()
        report = self._report(targets, collection)
        scored: dict[tuple[str, str], ScoredPassage] = {}
        dropped: set[tuple[str, str]] = set()   # near-duplicates, never scored
        evidence: list[ScoredPassage] = []
        sufficient: Verdict | None = None
        timings = {"retrieve": 0.0, "select": 0.0, "sufficient": 0.0}
        rounds = 0
        seen: list = []     # embeddings of the candidates kept so far, when dedupe_threshold is set
        try:
            for rounds in range(1, cfg.max_rounds + 1):
                t = time.perf_counter()
                hits = self._search(targets, question, cfg.n_candidates * 2 ** (rounds - 1), report)
                timings["retrieve"] += (time.perf_counter() - t) * 1000
                fresh = [p for p in hits if _key(p) not in scored and _key(p) not in dropped]
                new = self._novel(fresh, seen, report)
                dropped.update(_key(p) for p in fresh if p not in new)
                if not fresh and rounds > 1:
                    rounds -= 1     # the sources have nothing left to add
                    break
                emit("candidates", {"round": rounds, "passages": [asdict(p) for p in new]})
                t = time.perf_counter()
                # one backend call per chunk, with its events after it, so a client that leaves stops the next chunk
                for start in range(0, len(new), BATCH):
                    chunk = new[start:start + BATCH]
                    for p, p_select in zip(chunk, self.select_many(question, chunk)):
                        report[p.origin]["candidates"] += 1
                        scored[_key(p)] = ScoredPassage(p, p_select)
                        emit("select", {"round": rounds, **scored[_key(p)].as_dict()})
                timings["select"] += (time.perf_counter() - t) * 1000
                kept = [s for s in scored.values() if s.p_select is not None and s.p_select >= cfg.select_threshold]
                previous = [_key(s.passage) for s in evidence]
                evidence = sorted(kept, key=lambda s: s.p_select, reverse=True)[:cfg.top_k]
                emit("evidence", {"round": rounds, "passages": [s.as_dict() for s in evidence]})
                if not evidence:
                    sufficient = None
                    emit("sufficient", {"round": rounds, "skipped": "no evidence"})
                    continue
                if sufficient is not None and [_key(s.passage) for s in evidence] == previous:
                    # same evidence as the last round: the answer would be the same
                    emit("sufficient", {"round": rounds, "skipped": "same evidence", **sufficient.as_dict()})
                    continue
                t = time.perf_counter()
                try:
                    sufficient = Verdict(self.gates.sufficient(question, [s.passage.text for s in evidence]),
                                         cfg.sufficient_threshold)
                except DecisionRejected as e:
                    LOG.warning("decision model rejected the sufficiency question (%s); abstaining", e)
                    sufficient = None
                    break
                timings["sufficient"] += (time.perf_counter() - t) * 1000
                emit("sufficient", {"round": rounds, **sufficient.as_dict()})
                if sufficient.passed:
                    break
        except DecisionUnavailable as e:
            if not cfg.fail_open:
                raise
            LOG.warning("decision backend unavailable (%s); answering without gates", e)
            result = self._ungated(question, targets, report, started, cfg)
            emit("gates_unavailable", {"candidates": [s.as_dict() for s in result.candidates]})
            return result
        timings["total"] = (time.perf_counter() - started) * 1000
        return ContextResult(question, evidence, list(scored.values()), max(rounds, 1), sufficient, gated=True,
                             select_threshold=cfg.select_threshold, timings_ms=timings, sources=list(report.values()))

    def retrieve(self, question: str, collection: str | None = None, sources: Sequence[str] = (), k: int = 8,
                 settings: Config | None = None) -> dict:
        """Up to ``k`` passages from each target, with P(select) for each and no other gate."""
        targets = self.targets(collection, sources)
        report = self._report(targets, collection)
        hits = self._novel(self._search(targets, question, k, report), [], report)
        for p in hits:
            report[p.origin]["candidates"] += 1
        try:
            scored, gates = [ScoredPassage(p, s) for p, s in zip(hits, self.select_many(question, hits))], "applied"
        except DecisionUnavailable:
            if not self.config.fail_open:
                raise
            scored, gates = [ScoredPassage(p) for p in hits], "unavailable"
        return {"question": question, "passages": [s.as_dict() for s in scored], "gates": gates,
                "select_threshold": (settings or self.config).select_threshold, "sources": list(report.values())}

    def select_many(self, question: str, passages: list[Passage]) -> list[float | None]:
        """P(select) of each passage, or None when the model refuses it; a refused passage is never evidence."""
        if not passages:
            return []
        out = []
        for passage, p in zip(passages, self.gates.select_many(question, [p.text for p in passages])):
            if isinstance(p, DecisionRejected):
                LOG.warning("decision model rejected passage %s from %s (%s); skipping it", passage.id, passage.source, p)
                out.append(None)
            else:
                out.append(p)
        return out

    def generate(self, question: str, evidence: list[str]) -> str:
        """The chat model's answer. It cites each fact as [n], the number of a passage in ``evidence``."""
        from ovos_plugin_manager.templates.agents import AgentMessage, MessageRole
        locale = self.gates.locale
        context = "\n\n".join(locale["evidence_item"].format(n=n, text=text) for n, text in enumerate(evidence, 1))
        prompt = locale["user_template"].format(context=context, question=question)
        try:
            reply = self.chat.continue_chat([AgentMessage(MessageRole.USER, prompt)], lang=self.config.lang)
        except GeneratorUnavailable:
            raise
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            reason = {401: "the chat model provider refused the API key",
                      403: "the chat model provider refused the API key",
                      404: "the chat model provider does not know the model or the address",
                      429: "the chat model provider is rate limiting or the account has no credit"}.get(
                status, f"the chat model provider returned HTTP {status}")
            raise GeneratorUnavailable(f"{self.config.generator}: {e}", reason) from e
        except requests.ConnectionError as e:
            raise GeneratorUnavailable(f"{self.config.generator}: {e}",
                                       "cannot connect to the chat model server; is it running?") from e
        except Exception as e:
            raise GeneratorUnavailable(f"{self.config.generator}: {e}") from e
        text = (reply.content if reply is not None else "") or ""
        if not text.strip():
            raise GeneratorUnavailable(f"{self.config.generator}: empty answer")
        return text.strip()

    def check_grounding(self, question: str, evidence: list[str], answer: str, threshold: float | None = None) -> Verdict:
        return Verdict(self.gates.grounded(question, evidence, answer),
                       self.config.grounded_threshold if threshold is None else threshold)

    def answer(self, question: str, collection: str | None = None, sources: Sequence[str] = (),
               on_event: EventSink | None = None, settings: Config | None = None) -> dict:
        """The full loop. ``answer`` is None when a gate abstains; ``abstained_at`` names the gate."""
        emit = on_event or (lambda name, data: None)
        cfg = settings or self.config
        ctx = self.context(question, collection, sources, on_event, cfg)
        result = {**ctx.as_dict(), "answer": None, "draft": None, "cited": [], "grounded": None, "message": None}
        if ctx.abstained_at:
            result["message"] = self.gates.locale["abstain"]
            return result
        texts = [s.passage.text for s in ctx.evidence]
        t = time.perf_counter()
        draft = self.generate(question, texts)
        result["timings_ms"]["generate"] = (time.perf_counter() - t) * 1000
        result.update(draft=draft, cited=cited(draft, len(texts)))
        emit("draft", {"draft": draft, "cited": result["cited"]})
        if not ctx.gated:
            result["answer"] = draft
            return result
        t = time.perf_counter()
        try:
            # the citation numbers are not facts, and the grounded question does not ask about them
            grounded = self.check_grounding(question, texts, strip_citations(draft, len(texts)), cfg.grounded_threshold)
        except DecisionUnavailable as e:
            if not self.config.fail_open:
                raise
            LOG.warning("decision backend unavailable for the grounding check (%s); answer is unchecked", e)
            result.update(answer=draft, gates="partial")
            return result
        except DecisionRejected as e:
            LOG.warning("decision model rejected the grounding question (%s); abstaining", e)
            result.update(abstained_at="grounded", message=self.gates.locale["abstain"])
            return result
        result["timings_ms"]["grounded"] = (time.perf_counter() - t) * 1000
        result["grounded"] = grounded.as_dict()
        emit("grounded", grounded.as_dict())
        if grounded.passed:
            result["answer"] = draft
        else:
            result.update(abstained_at="grounded", message=self.gates.locale["abstain"])
        return result
