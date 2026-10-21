"""Decision gates for retrieval-augmented generation."""
from importlib import import_module

from evidencia.version import __version__

# names load on first use, so `evidencia mcp` imports no retrieval or model code and writes nothing to stdout
_EXPORTS = {"Config": "config", "DecisionConfig": "config", "Decision": "decision", "DecisionRejected": "decision",
            "DecisionUnavailable": "decision", "HttpDecision": "decision", "LocalDecision": "decision",
            "Gates": "gates", "Verdict": "gates", "Collection": "index", "Passage": "index",
            "ContextResult": "pipeline", "Evidencia": "pipeline", "GeneratorUnavailable": "pipeline"}

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module 'evidencia' has no attribute {name!r}")
    return getattr(import_module(f"evidencia.{_EXPORTS[name]}"), name)
