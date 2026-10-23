"""Decision backends: a state plus typed questions in, calibrated answers out.

Requests and answers use the System One shape: each question is a ``noul``
(yes/no probability), a ``choice`` or a ``score``. ``HttpDecision`` talks to any
server that implements ``POST /v1/systemone``; ``LocalDecision`` runs a Kev-family
checkpoint in this process.
"""
from __future__ import annotations

import threading
from typing import Any, Protocol

import requests

from evidencia.observability import REQUEST_ID, ContextPool

DEFAULT_MODEL = "kev-latest"
# statuses that refuse the request content; an auth or routing error means the backend is unusable instead
REJECTED_STATUS = {400, 413, 422}
BATCH = 16      # states in one forward pass of the local model


class DecisionUnavailable(RuntimeError):
    """The decision backend could not answer: unreachable, failing, or a malformed reply."""


class DecisionRejected(ValueError):
    """The decision backend read the request and refused its content, for example a passage too long for the model.

    Document content can cause this, so it never switches the gates off: the gate that asked abstains.
    """


class Decision(Protocol):
    def ask(self, state: Any, questions: dict[str, dict]) -> dict[str, dict]:
        """Answer every question about ``state``; returns the ``answers`` mapping."""
        ...

    def ask_many(self, items: list[tuple[Any, dict[str, dict]]]) -> list[dict[str, dict] | DecisionRejected]:
        """``ask`` for each (state, questions), in order. A refused state gives its DecisionRejected in its place;
        DecisionUnavailable is raised for the whole call."""
        ...

    def describe(self) -> dict[str, Any]:
        """Backend identity for health checks and response metadata."""
        ...


class HttpDecision:
    def __init__(self, url: str, key: str | None = None, model: str = DEFAULT_MODEL, timeout: float = 30.0,
                 workers: int = 4):
        self.url = url.rstrip("/")
        self.key = key
        self.model = model
        self.timeout = timeout
        self.workers = workers
        self.session = requests.Session()
        self._local = threading.local()     # a session for each worker thread of ask_many
        self._pool: ContextPool | None = None
        self._pool_lock = threading.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        if (rid := REQUEST_ID.get()) != "-":
            headers["X-Request-ID"] = rid
        return headers

    def ask(self, state: Any, questions: dict[str, dict]) -> dict[str, dict]:
        return self._ask(self.session, state, questions)

    def ask_many(self, items: list[tuple[Any, dict[str, dict]]]) -> list[dict[str, dict] | DecisionRejected]:
        """One request per state, up to ``workers`` at a time, so a server that batches can answer them together."""
        def one(item):
            if (session := getattr(self._local, "session", None)) is None:
                session = self._local.session = requests.Session()
            try:
                return self._ask(session, *item)
            except DecisionRejected as e:
                return e
        if len(items) < 2 or self.workers < 2:
            return [one(item) for item in items]
        with self._pool_lock:
            if self._pool is None:
                self._pool = ContextPool(self.workers, thread_name_prefix="decision")
        return list(self._pool.map(one, items))

    def _ask(self, session: requests.Session, state: Any, questions: dict[str, dict]) -> dict[str, dict]:
        body = {"state": state, "model": self.model, "questions": questions}
        try:
            resp = session.post(f"{self.url}/v1/systemone", json=body, headers=self._headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise DecisionUnavailable(f"{self.url}: {e}") from e
        if resp.status_code in REJECTED_STATUS:
            raise DecisionRejected(f"{self.url}: {resp.status_code} {resp.text[:200]}")
        try:
            resp.raise_for_status()
            answers = resp.json()["answers"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            raise DecisionUnavailable(f"{self.url}: {e}") from e
        if not isinstance(answers, dict):
            raise DecisionUnavailable(f"{self.url}: answers of the wrong shape: {str(answers)[:200]}")
        missing = set(questions) - set(answers)
        if missing:
            raise DecisionUnavailable(f"{self.url}: no answer for {sorted(missing)}")
        if not all(isinstance(answers[q], dict) for q in questions):
            raise DecisionUnavailable(f"{self.url}: answers of the wrong shape: {str(answers)[:200]}")
        return answers

    def describe(self) -> dict[str, Any]:
        try:
            resp = self.session.get(f"{self.url}/v1/models", headers=self._headers, timeout=self.timeout)
            resp.raise_for_status()
            models = [m.get("run") or m.get("name") for m in resp.json().get("models", [])]
            return {"backend": "http", "url": self.url, "model": self.model, "reachable": True, "serves": models}
        except (requests.RequestException, ValueError) as e:
            return {"backend": "http", "url": self.url, "model": self.model, "reachable": False, "error": str(e)}


class LocalDecision:
    """A Kev-family checkpoint (a Hub id such as ``TigreGotico/jevasio-0.8b-v2`` or a local run directory) in this process.

    Needs the ``local`` extra. The checkpoint loads on first use, so constructing this is cheap.
    """

    def __init__(self, run: str, device: str = "auto", dtype: str | None = None):
        self.run = run
        self.device = device
        self.dtype = dtype
        self._loaded = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def _load(self):
        import torch
        from evidencia._vendor.kev.checkpoint import Checkpoint, LoadOptions

        device = self.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = {"bf16": torch.bfloat16, "fp32": torch.float32}.get(self.dtype or "",
                                                                    torch.bfloat16 if device == "cuda" else torch.float32)
        # the plain torch path: the fused kernels, CUDA graphs and MLX need modules that are not vendored
        opts = LoadOptions(dtype=dtype, backend="torch", cuda_graphs=False, fused=False)
        tok, model = Checkpoint(self.run).load(device, opts)
        return tok, model

    def ask(self, state: Any, questions: dict[str, dict]) -> dict[str, dict]:
        (answer,) = self.ask_many([(state, questions)])
        if isinstance(answer, DecisionRejected):
            raise answer
        return answer

    def ask_many(self, items: list[tuple[Any, dict[str, dict]]]) -> list[dict[str, dict] | DecisionRejected]:
        """The states in forward passes of up to BATCH records: on a GPU 8 select questions take a sixth of the
        time of 8 single passes, and on a CPU about half."""
        from evidencia._vendor.kev.api import SystemOneRequest, to_answers, to_record

        out: list[dict[str, dict] | DecisionRejected | None] = [None] * len(items)
        records = {}
        for i, (state, questions) in enumerate(items):
            try:
                req = SystemOneRequest.model_validate({"state": state, "model": DEFAULT_MODEL, "questions": questions})
            except ValueError as e:
                out[i] = DecisionRejected(f"invalid request: {e}")
                continue
            records[i] = to_record(req)
        with self._lock:
            if self._loaded is None:
                try:
                    self._loaded = self._load()
                except Exception as e:
                    self._error = f"{self.run}: {e}"
                    raise DecisionUnavailable(self._error) from e
                self._error = None
            # imported after the load, which reports a missing torch as an unavailable backend
            from evidencia._vendor.kev.model import admit
            tok, model = self._loaded
            encs = {}
            for i, (rec, _) in records.items():
                try:
                    encs[i] = admit(model, tok, rec)
                except ValueError as e:
                    out[i] = DecisionRejected(f"{self.run}: {e}")
            admitted = list(encs)
            for start in range(0, len(admitted), BATCH):
                chunk = admitted[start:start + BATCH]
                for i, probs in zip(chunk, self._batch_probs(model, [encs[i] for i in chunk])):
                    out[i] = to_answers(probs, records[i][1])
        return out

    def _batch_probs(self, model, encs: list) -> list[list[list[float]]]:
        """The option probabilities of each question of each record, from one forward pass."""
        import torch
        import torch.nn.functional as F
        with torch.inference_mode():
            return [[F.softmax(z, -1).float().cpu().tolist() for z in logits] for logits in model.forward_batch(encs)]

    def describe(self) -> dict[str, Any]:
        return {"backend": "local", "run": self.run, "device": self.device, "loaded": self._loaded is not None,
                "reachable": self._error is None, "error": self._error}
