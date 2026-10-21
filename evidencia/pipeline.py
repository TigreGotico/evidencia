"""Retrieve, gate, generate, check."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from evidencia.config import Config
from evidencia.decision import Decision, DecisionRejected, DecisionUnavailable
from evidencia.gates import Gates, Verdict
from evidencia.index import Collection, Passage

LOG = logging.getLogger("evidencia")


class GeneratorUnavailable(RuntimeError):
    """No chat engine is configured, or it failed to produce an answer."""


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

    @property
    def abstain(self) -> bool:
        return self.gated and (self.sufficient is None or not self.sufficient.passed)

    def as_dict(self) -> dict:
        return {"question": self.question,
                "evidence": [p.as_dict() for p in self.evidence],
                "candidates": [p.as_dict() for p in self.candidates],
                "rounds": self.rounds,
                "select_threshold": self.select_threshold,
                "sufficient": self.sufficient.as_dict() if self.sufficient else None,
                "gates": "applied" if self.gated else "unavailable",
                "abstain": self.abstain,
                "timings_ms": self.timings_ms}


class Evidencia:
    def __init__(self, config: Config | None = None, decision: Decision | None = None, chat: Any = None):
        self.config = config or Config()
        self.decision = decision or self.config.decision.build()
        self.gates = Gates(self.decision, self.config.lang)
        self._chat = chat
        self._chat_lock = threading.Lock()
        self._collections_lock = threading.Lock()
        self.collections: dict[str, Collection] = {}

    def collection(self, name: str, create: bool = False) -> Collection:
        with self._collections_lock:
            if name not in self.collections:
                if not create:
                    raise KeyError(name)
                self.collections[name] = Collection(name, lang=self.config.lang)
            return self.collections[name]

    def add_document(self, name: str, content: str | bytes, source: str, fmt: str = "text") -> tuple[Collection, list[Passage]]:
        """Add a document to a collection, making the collection only when the document has passages."""
        with self._collections_lock:
            col = self.collections.get(name)
            if col is None:
                col = Collection(name, lang=self.config.lang)
                added = col.add(content, source, fmt)
                self.collections[name] = col
                return col, added
        return col, col.add(content, source, fmt)

    def drop_collection(self, name: str) -> None:
        with self._collections_lock:
            del self.collections[name]

    def collections_snapshot(self) -> dict[str, Collection]:
        with self._collections_lock:
            return dict(self.collections)

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

    def _ungated(self, question: str, collection: Collection, started: float) -> ContextResult:
        hits = [ScoredPassage(p) for p in collection.search(question, self.config.top_k)]
        return ContextResult(question, hits, hits, rounds=1, sufficient=None, gated=False,
                             select_threshold=self.config.select_threshold, timings_ms={"total": (time.perf_counter() - started) * 1000})

    def _degrade(self, error: DecisionUnavailable, question: str, collection: Collection, started: float):
        if not self.config.fail_open:
            raise error
        LOG.warning("decision backend unavailable (%s); answering without gates", error)
        return self._ungated(question, collection, started)

    def context(self, question: str, collection: str) -> ContextResult:
        """Retrieve candidates, keep the passages the select gate passes, and widen until the evidence is sufficient."""
        col = self.collection(collection)
        cfg = self.config
        started = time.perf_counter()
        scored: dict[str, ScoredPassage] = {}
        evidence: list[ScoredPassage] = []
        sufficient: Verdict | None = None
        timings = {"retrieve": 0.0, "select": 0.0, "sufficient": 0.0}
        rounds = 0
        try:
            for rounds in range(1, cfg.max_rounds + 1):
                t = time.perf_counter()
                hits = col.search(question, cfg.n_candidates * 2 ** (rounds - 1))
                timings["retrieve"] += (time.perf_counter() - t) * 1000
                new = [p for p in hits if p.id not in scored]
                if not new and rounds > 1:
                    rounds -= 1     # the collection has nothing left to add
                    break
                t = time.perf_counter()
                for p in new:
                    scored[p.id] = ScoredPassage(p, self.select(question, p))
                timings["select"] += (time.perf_counter() - t) * 1000
                kept = [s for s in scored.values() if s.p_select is not None and s.p_select >= cfg.select_threshold]
                previous = [s.passage.id for s in evidence]
                evidence = sorted(kept, key=lambda s: s.p_select, reverse=True)[:cfg.top_k]
                if not evidence:
                    sufficient = None
                    continue
                if sufficient is not None and [s.passage.id for s in evidence] == previous:
                    continue        # same evidence as the last round: the answer would be the same
                t = time.perf_counter()
                try:
                    sufficient = Verdict(self.gates.sufficient(question, [s.passage.text for s in evidence]),
                                         cfg.sufficient_threshold)
                except DecisionRejected as e:
                    LOG.warning("decision model rejected the sufficiency question (%s); abstaining", e)
                    sufficient = None
                    break
                timings["sufficient"] += (time.perf_counter() - t) * 1000
                if sufficient.passed:
                    break
        except DecisionUnavailable as e:
            return self._degrade(e, question, col, started)
        timings["total"] = (time.perf_counter() - started) * 1000
        candidates = sorted(scored.values(), key=lambda s: s.passage.score, reverse=True)
        return ContextResult(question, evidence, candidates, max(rounds, 1), sufficient, gated=True,
                             select_threshold=cfg.select_threshold, timings_ms=timings)

    def select(self, question: str, passage: Passage) -> float | None:
        """P(select), or None when the model refuses the passage; a refused passage is never evidence."""
        try:
            return self.gates.select(question, passage.text)
        except DecisionRejected as e:
            LOG.warning("decision model rejected passage %s from %s (%s); skipping it", passage.id, passage.source, e)
            return None

    def generate(self, question: str, evidence: list[str]) -> str:
        from ovos_plugin_manager.templates.agents import AgentMessage, MessageRole
        prompt = self.gates.locale["user_template"].format(
            context="\n\n".join(f"- {text}" for text in evidence), question=question)
        try:
            reply = self.chat.continue_chat([AgentMessage(MessageRole.USER, prompt)], lang=self.config.lang)
        except GeneratorUnavailable:
            raise
        except Exception as e:
            raise GeneratorUnavailable(f"{self.config.generator}: {e}") from e
        text = (reply.content if reply is not None else "") or ""
        if not text.strip():
            raise GeneratorUnavailable(f"{self.config.generator}: empty answer")
        return text.strip()

    def check_grounding(self, question: str, evidence: list[str], answer: str) -> Verdict:
        return Verdict(self.gates.grounded(question, evidence, answer), self.config.grounded_threshold)

    def answer(self, question: str, collection: str) -> dict:
        """The full loop. ``answer`` is None when a gate abstains; ``abstained_at`` names the gate."""
        ctx = self.context(question, collection)
        result = {**ctx.as_dict(), "answer": None, "draft": None, "grounded": None, "abstained_at": None,
                  "message": None}
        if not ctx.candidates:
            result.update(abstained_at="retrieve", message=self.gates.locale["abstain"])
            return result
        if not ctx.evidence:
            result.update(abstained_at="select", message=self.gates.locale["abstain"])
            return result
        if ctx.abstain:
            result.update(abstained_at="sufficient", message=self.gates.locale["abstain"])
            return result
        texts = [s.passage.text for s in ctx.evidence]
        t = time.perf_counter()
        draft = self.generate(question, texts)
        result["timings_ms"]["generate"] = (time.perf_counter() - t) * 1000
        if not ctx.gated:
            result.update(answer=draft, draft=draft)
            return result
        t = time.perf_counter()
        try:
            grounded = self.check_grounding(question, texts, draft)
        except DecisionUnavailable as e:
            if not self.config.fail_open:
                raise
            LOG.warning("decision backend unavailable for the grounding check (%s); answer is unchecked", e)
            result.update(answer=draft, draft=draft, gates="partial")
            return result
        except DecisionRejected as e:
            LOG.warning("decision model rejected the grounding question (%s); abstaining", e)
            result.update(draft=draft, abstained_at="grounded", message=self.gates.locale["abstain"])
            return result
        result["timings_ms"]["grounded"] = (time.perf_counter() - t) * 1000
        result.update(draft=draft, grounded=grounded.as_dict())
        if grounded.passed:
            result["answer"] = draft
        else:
            result.update(abstained_at="grounded", message=self.gates.locale["abstain"])
        return result
