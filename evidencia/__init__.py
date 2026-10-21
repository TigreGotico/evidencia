"""Decision gates for retrieval-augmented generation."""
from evidencia.version import __version__
from evidencia.config import Config, DecisionConfig
from evidencia.decision import Decision, DecisionUnavailable, HttpDecision, LocalDecision
from evidencia.gates import Gates, Verdict
from evidencia.index import Collection, Passage
from evidencia.pipeline import ContextResult, Evidencia, GeneratorUnavailable

__all__ = ["__version__", "Config", "DecisionConfig", "Decision", "DecisionUnavailable", "HttpDecision",
           "LocalDecision", "Gates", "Verdict", "Collection", "Passage", "ContextResult", "Evidencia",
           "GeneratorUnavailable"]
