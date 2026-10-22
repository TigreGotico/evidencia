"""The three gate questions, asked of a decision backend."""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Sequence

from evidencia.decision import Decision, DecisionRejected, DecisionUnavailable


def load_locale(lang: str) -> dict:
    path = resources.files("evidencia") / "locale" / f"{lang}.json"
    if not path.is_file():
        raise ValueError(f"no gate questions for {lang!r}")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Verdict:
    p: float
    threshold: float

    @property
    def passed(self) -> bool:
        return self.p >= self.threshold

    def as_dict(self) -> dict:
        return {"p": self.p, "threshold": self.threshold, "passed": self.passed}


class Gates:
    def __init__(self, decision: Decision, lang: str = "pt-PT"):
        self.decision = decision
        self.locale = load_locale(lang)

    def _noul(self, gate: str, state: dict) -> float:
        return self._probability(gate, self.decision.ask(state, {gate: self.locale[gate]}))

    @staticmethod
    def _probability(gate: str, answers: dict) -> float:
        answer = answers.get(gate)
        if not isinstance(answer, dict):
            raise DecisionUnavailable(f"{gate}: no answer object in the reply: {answer!r}")
        p = answer.get("noul")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
            raise DecisionUnavailable(f"{gate}: answer has no noul probability in [0, 1]: {answer}")
        return float(p)

    def select(self, question: str, passage: str) -> float:
        """P(the passage helps answer the question)."""
        return self._noul("select", {"question": question, "passage": passage})

    def select_many(self, question: str, passages: Sequence[str]) -> list[float | DecisionRejected]:
        """P(select) for each passage, in one call to the backend; a refused passage gives its DecisionRejected."""
        questions = {"select": self.locale["select"]}
        answers = self.decision.ask_many([({"question": question, "passage": p}, questions) for p in passages])
        return [a if isinstance(a, DecisionRejected) else self._probability("select", a) for a in answers]

    def sufficient(self, question: str, evidence: Sequence[str]) -> float:
        """P(the evidence is enough to answer the question)."""
        return self._noul("sufficient", {"question": question, "evidence": list(evidence)})

    def grounded(self, question: str, evidence: Sequence[str], answer: str) -> float:
        """P(every fact in the answer is supported by the evidence)."""
        return self._noul("grounded", {"question": question, "evidence": list(evidence), "answer": answer})
