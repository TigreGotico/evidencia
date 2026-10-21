"""Service configuration: a TOML file, then environment overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class DecisionConfig:
    backend: str = "http"                # "http" or "local"
    url: str = "http://127.0.0.1:8008"   # http: a server implementing POST /v1/systemone
    key: str | None = None               # http: bearer token, if the server requires one
    model: str = "kev-latest"            # http: model name sent in each request
    run: str = "TigreGotico/jevasio-0.8b-v2"   # local: Hub id or run directory of the checkpoint
    device: str = "auto"                 # local: auto, cpu, cuda or mps
    dtype: str | None = None             # local: bf16 or fp32; default bf16 on CUDA, fp32 elsewhere
    timeout: float = 30.0

    def build(self):
        from evidencia.decision import HttpDecision, LocalDecision
        if self.backend == "http":
            return HttpDecision(self.url, key=self.key, model=self.model, timeout=self.timeout)
        if self.backend == "local":
            return LocalDecision(self.run, device=self.device, dtype=self.dtype)
        raise ValueError(f"decision.backend must be 'http' or 'local', got {self.backend!r}")


@dataclass
class Config:
    lang: str = "pt-PT"
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    select_threshold: float = 0.5
    sufficient_threshold: float = 0.5
    grounded_threshold: float = 0.5
    n_candidates: int = 8       # passages retrieved in the first round; doubles each round
    top_k: int = 4              # selected passages kept as evidence
    max_rounds: int = 2
    fail_open: bool = True      # decision backend down: answer ungated (True) or refuse (False)
    generator: str | None = None                       # chat engine plugin name, e.g. "ovos-chat-openai-plugin"
    generator_config: dict[str, Any] = field(default_factory=dict)
    api_key: str | None = None                         # bearer token required by the HTTP API, if set
    allow_url_ingest: bool = False                     # let clients make the service fetch a URL
    max_document_bytes: int = 20_000_000

    def __post_init__(self):
        if isinstance(self.decision, dict):
            self.decision = _decision_config(self.decision)
        for name in ("select_threshold", "sufficient_threshold", "grounded_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a number in [0, 1], got {value!r}")
        for name in ("n_candidates", "top_k", "max_rounds", "max_document_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be an integer of at least 1, got {value!r}")
        if self.api_key == "":
            raise ValueError("api_key is empty; leave it out to run without authentication")

    @classmethod
    def load(cls, path: str | None = None, env: dict[str, str] | None = None) -> Config:
        """Read ``path`` (or ``$EVIDENCIA_CONFIG``), then apply the ``EVIDENCIA_*`` environment overrides."""
        env = os.environ if env is None else env
        path = path or env.get("EVIDENCIA_CONFIG")
        data: dict[str, Any] = {}
        if path:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        decision = dict(data.pop("decision", {}))
        for var, key in (("EVIDENCIA_DECISION_BACKEND", "backend"), ("EVIDENCIA_DECISION_URL", "url"),
                         ("EVIDENCIA_DECISION_KEY", "key"), ("EVIDENCIA_DECISION_RUN", "run"),
                         ("EVIDENCIA_DECISION_DEVICE", "device")):
            if env.get(var):
                decision[key] = env[var]
        if env.get("EVIDENCIA_API_KEY"):
            data["api_key"] = env["EVIDENCIA_API_KEY"]
        return cls(decision=_decision_config(decision), **data)


def _decision_config(data: dict[str, Any]) -> DecisionConfig:
    unknown = set(data) - {f.name for f in fields(DecisionConfig)}
    if unknown:
        raise ValueError(f"unknown decision config keys: {sorted(unknown)}")
    return DecisionConfig(**data)
