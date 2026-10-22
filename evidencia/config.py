"""Service configuration: a TOML file, then environment overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from typing import Any

from evidencia.version import __version__

TRANSPORTS = {"requests", "curl_cffi", "flaresolverr", "browserless"}
THRESHOLDS = ("select_threshold", "sufficient_threshold", "grounded_threshold")
# a request may set these; the server caps each size with its max_request_* value
REQUEST_SIZES = {"top_k": "max_request_top_k", "n_candidates": "max_request_candidates", "max_rounds": "max_request_rounds"}


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
    workers: int = 4                     # http: select questions sent at the same time

    def build(self):
        from evidencia.decision import HttpDecision, LocalDecision
        if self.backend == "http":
            return HttpDecision(self.url, key=self.key, model=self.model, timeout=self.timeout, workers=self.workers)
        if self.backend == "local":
            return LocalDecision(self.run, device=self.device, dtype=self.dtype)
        raise ValueError(f"decision.backend must be 'http' or 'local', got {self.backend!r}")


@dataclass
class Config:
    lang: str = "pt-PT"
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    select_threshold: float = 0.5
    sufficient_threshold: float = 0.5
    grounded_threshold: float = 0.8   # at 0.5, 10 of 22 wrong answers in eval/ pass; at 0.8, 3 pass
    n_candidates: int = 8       # passages retrieved in the first round; doubles each round
    top_k: int = 4              # selected passages kept as evidence
    max_rounds: int = 2
    max_request_top_k: int = 16        # the largest top_k a request may ask for
    max_request_candidates: int = 64   # the largest n_candidates a request may ask for
    max_request_rounds: int = 4        # the largest max_rounds a request may ask for
    allow_lower_thresholds: bool = False   # let a request set a threshold below the server's; above is always allowed
    fail_open: bool = True      # decision backend down: answer ungated (True) or refuse (False)
    generator: str | None = "amalia"                   # a built-in preset, or a chat engine plugin name
    generator_config: dict[str, Any] = field(default_factory=dict)
    api_key: str | None = None                         # bearer token required by the HTTP API, if set
    allow_url_ingest: bool = False                     # let clients make the service fetch a URL
    max_document_bytes: int = 20_000_000
    sources: list[str] = field(default_factory=list)   # remote sources a request may name, e.g. "pt.wikipedia"
    user_agent: str = f"evidencia/{__version__} (https://github.com/TigreGotico/evidencia)"
    source_timeout: float = 10.0                       # seconds for each request to a remote source
    transport: str | None = None                       # requests, curl_cffi, flaresolverr or browserless; curl_cffi when unset
    flaresolverr_url: str | None = None                # a FlareSolverr that solves a request a source refuses
    browserless_url: str | None = None                 # a browserless that the browserless transport loads pages with
    data_dir: str | None = None                        # keep collections on disk here; in memory when unset
    embedding_model: str | None = None                 # model2vec model for hybrid retrieval; BM25 only when unset
    embedder: str | None = None                        # an OPM text embeddings plugin, instead of embedding_model
    embedder_config: dict[str, Any] = field(default_factory=dict)
    vector_db: str | None = None                       # an OPM embeddings database plugin that keeps the vectors
    vector_db_config: dict[str, Any] = field(default_factory=dict)
    dedupe_threshold: float | None = None              # drop a candidate this similar to one already seen
    ui: bool = True                                    # serve the demo page at /

    def __post_init__(self):
        if isinstance(self.decision, dict):
            self.decision = _decision_config(self.decision)
        for name in THRESHOLDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a number in [0, 1], got {value!r}")
        for name in (*REQUEST_SIZES, *REQUEST_SIZES.values(), "max_document_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be an integer of at least 1, got {value!r}")
        if self.api_key == "":
            raise ValueError("api_key is empty; leave it out to run without authentication")
        from evidencia.sources import CATALOG
        if not isinstance(self.sources, list) or not all(isinstance(n, str) for n in self.sources):
            raise ValueError(f"sources must be a list of source names, got {self.sources!r}")
        unknown = sorted(set(self.sources) - set(CATALOG))
        if unknown:
            raise ValueError(f"unknown sources {unknown}; known: {sorted(CATALOG)}")
        if self.dedupe_threshold is not None:
            if isinstance(self.dedupe_threshold, bool) or not isinstance(self.dedupe_threshold, (int, float)) \
                    or not 0.0 < self.dedupe_threshold <= 1.0:
                raise ValueError(f"dedupe_threshold must be a number in (0, 1], got {self.dedupe_threshold!r}")
            if not self.hybrid:
                raise ValueError("dedupe_threshold needs embedding_model or embedder")
        if self.embedder and self.embedding_model:
            raise ValueError("set embedder or embedding_model, not both")
        if not isinstance(self.embedder_config, dict):
            raise ValueError(f"embedder_config must be a table, got {self.embedder_config!r}")
        if not isinstance(self.vector_db_config, dict):
            raise ValueError(f"vector_db_config must be a table, got {self.vector_db_config!r}")
        if self.vector_db:
            if not self.hybrid:
                raise ValueError("vector_db needs embedding_model or embedder")
            if not self.data_dir and not {"host", "path"} & set(self.vector_db_config):
                raise ValueError("vector_db needs data_dir, or a host or path in vector_db_config")
        if self.transport is not None and self.transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of {sorted(TRANSPORTS)}, got {self.transport!r}")
        if isinstance(self.source_timeout, bool) or not isinstance(self.source_timeout, (int, float)) \
                or self.source_timeout <= 0:
            raise ValueError(f"source_timeout must be a number of seconds above 0, got {self.source_timeout!r}")

    @property
    def hybrid(self) -> bool:
        """Whether retrieval fuses BM25 with embeddings."""
        return bool(self.embedder or self.embedding_model)

    def for_request(self, **settings: float | int | None) -> Config:
        """This configuration with the thresholds and sizes that one request sets. A request may make a gate
        stricter, and looser only with ``allow_lower_thresholds``; it may not go above the ``max_request_*`` sizes."""
        given = {name: value for name, value in settings.items() if value is not None}
        for name, limit in REQUEST_SIZES.items():
            if name in given and given[name] > getattr(self, limit):
                raise ValueError(f"{name} {given[name]} is above the server limit {getattr(self, limit)} ({limit})")
        if not self.allow_lower_thresholds:
            for name in THRESHOLDS:
                if name in given and given[name] < getattr(self, name):
                    raise ValueError(f"{name} {given[name]} is below the server value {getattr(self, name)}; "
                                     f"the server must set allow_lower_thresholds to allow it")
        return replace(self, **given) if given else self

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
        if env.get("EVIDENCIA_GENERATOR"):
            data["generator"] = env["EVIDENCIA_GENERATOR"]
        if env.get("EVIDENCIA_DATA_DIR"):
            data["data_dir"] = env["EVIDENCIA_DATA_DIR"]
        for var, key in (("EVIDENCIA_TRANSPORT", "transport"), ("EVIDENCIA_FLARESOLVERR_URL", "flaresolverr_url"),
                         ("EVIDENCIA_BROWSERLESS_URL", "browserless_url")):
            if env.get(var):
                data[key] = env[var]
        if env.get("EVIDENCIA_SOURCES"):
            data["sources"] = [n.strip() for n in env["EVIDENCIA_SOURCES"].split(",") if n.strip()]
        return cls(decision=_decision_config(decision), **data)


def _decision_config(data: dict[str, Any]) -> DecisionConfig:
    unknown = set(data) - {f.name for f in fields(DecisionConfig)}
    if unknown:
        raise ValueError(f"unknown decision config keys: {sorted(unknown)}")
    return DecisionConfig(**data)
