"""HTTP API."""
from __future__ import annotations

import hmac
import logging
import threading
import time
from pathlib import PurePath
from typing import Literal

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from evidencia import __version__
from evidencia.config import Config
from evidencia.decision import DecisionRejected, DecisionUnavailable
from evidencia.index import FORMATS
from evidencia.pipeline import Evidencia, GeneratorUnavailable

LOG = logging.getLogger("evidencia")
Format = Literal["text", "markdown", "html", "pdf"]
HEALTH_TTL = 10.0       # seconds a decision-backend probe result is reused by /health
EXTENSIONS = {".txt": "text", ".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html", ".pdf": "pdf"}


class DocumentIn(BaseModel):
    source: str = Field(min_length=1, description="Name that identifies the document; re-sending it replaces it")
    text: str | None = None
    url: str | None = None
    format: Format = "text"

    @model_validator(mode="after")
    def one_of(self):
        if (self.text is None) == (self.url is None):
            raise ValueError("give exactly one of text or url")
        if self.text is not None and self.format == "pdf":
            raise ValueError("send a pdf as a file upload")
        return self


class QuestionIn(BaseModel):
    collection: str
    question: str = Field(min_length=1)


class RetrieveIn(QuestionIn):
    k: int = Field(default=8, ge=1, le=100)


class GroundingIn(BaseModel):
    question: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    answer: str = Field(min_length=1)


def create_app(config: Config | None = None, engine: Evidencia | None = None) -> FastAPI:
    config = config or (engine.config if engine else Config.load())
    engine = engine or Evidencia(config)
    app = FastAPI(title="evidencia", version=__version__,
                  description="Decision gates for retrieval-augmented generation.")
    app.state.engine = engine

    def auth(request: Request):
        if not config.api_key:
            return
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {config.api_key}".encode()):
            raise HTTPException(401, "invalid or missing bearer token")

    # the cause names internal hosts and credentials errors: it goes to the log, not to the client
    @app.exception_handler(DecisionUnavailable)
    def decision_down(_, e: DecisionUnavailable):
        LOG.error("decision backend unavailable: %s", e)
        return JSONResponse({"detail": "decision backend unavailable"}, status_code=503)

    @app.exception_handler(DecisionRejected)
    def decision_rejected(_, e: DecisionRejected):
        LOG.warning("decision model rejected a request: %s", e)
        return JSONResponse({"detail": "decision model rejected the request"}, status_code=422)

    @app.exception_handler(GeneratorUnavailable)
    def generator_down(_, e: GeneratorUnavailable):
        LOG.error("generator unavailable: %s", e)
        return JSONResponse({"detail": "generator unavailable"}, status_code=502)

    def collection_or_404(name: str):
        try:
            return engine.collection(name)
        except KeyError:
            raise HTTPException(404, f"no collection {name!r}") from None

    def too_large(size: int):
        if size > config.max_document_bytes:
            raise HTTPException(413, f"document larger than {config.max_document_bytes} bytes")

    def ingest(name: str, source: str, content: str | bytes, fmt: str) -> dict:
        col = engine.collection(name, create=True)
        try:
            added = col.add(content, source, fmt)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"collection": name, "source": source, "passages": len(added), "total": len(col.passages)}

    # /health needs no token, so it must not let a caller drive one outbound request per call
    probe = {"at": float("-inf"), "reachable": True}
    probe_lock = threading.Lock()

    def decision_reachable() -> bool:
        if probe_lock.acquire(blocking=False):
            try:
                if time.monotonic() - probe["at"] > HEALTH_TTL:
                    probe.update(reachable=engine.decision.describe()["reachable"], at=time.monotonic())
            finally:
                probe_lock.release()
        return probe["reachable"]

    @app.get("/health")
    def health():
        reachable = decision_reachable()
        return {"status": "ok" if reachable else "degraded", "version": __version__,
                "decision_reachable": reachable}

    @app.get("/v1/status", dependencies=[Depends(auth)])
    def status():
        return {"version": __version__, "lang": config.lang, "decision": engine.decision.describe(),
                "generator": config.generator, "fail_open": config.fail_open,
                "thresholds": {"select": config.select_threshold, "sufficient": config.sufficient_threshold,
                               "grounded": config.grounded_threshold},
                "collections": len(engine.collections_snapshot())}

    @app.get("/v1/models", dependencies=[Depends(auth)])
    def models():
        """The model this instance serves on /v1/systemone, in the shape HttpDecision.describe reads."""
        name = config.decision.run if config.decision.backend == "local" else config.decision.model
        return {"models": [{"name": name}]}

    @app.get("/v1/collections", dependencies=[Depends(auth)])
    def collections():
        return {name: {"passages": len(c.passages), "sources": sorted({p.source for p in c.passages})}
                for name, c in engine.collections_snapshot().items()}

    @app.post("/v1/collections/{name}/documents", dependencies=[Depends(auth)])
    def add_document(name: str, doc: DocumentIn):
        if doc.text is not None:
            too_large(len(doc.text.encode("utf-8")))
            return ingest(name, doc.source, doc.text, doc.format)
        if not config.allow_url_ingest:
            raise HTTPException(403, "URL ingestion is disabled; set allow_url_ingest to enable it")
        try:
            # a redirect could lead to a host the client may not name directly, so it is refused, not followed
            with requests.get(doc.url, timeout=30, stream=True, allow_redirects=False) as resp:
                if resp.is_redirect:
                    raise HTTPException(422, "the URL redirects; give the final URL")
                resp.raise_for_status()
                data = bytearray()
                for block in resp.iter_content(64 * 1024):
                    data += block
                    too_large(len(data))
        except requests.RequestException as e:
            LOG.warning("could not fetch %s: %s", doc.url, e)
            raise HTTPException(502, "could not fetch the URL") from None
        if doc.format == "pdf":
            return ingest(name, doc.source, bytes(data), doc.format)
        try:
            text = data.decode(resp.encoding or "utf-8")
        except (LookupError, UnicodeDecodeError):
            raise HTTPException(422, "the URL did not return text in its declared encoding") from None
        return ingest(name, doc.source, text, doc.format)

    @app.post("/v1/collections/{name}/files", dependencies=[Depends(auth)])
    def add_file(name: str, file: UploadFile = File(...), source: str | None = Form(None),
                 format: str | None = Form(None)):
        fmt = format or EXTENSIONS.get(PurePath(file.filename or "").suffix.lower())
        if fmt not in FORMATS:
            raise HTTPException(422, f"cannot tell the format of {file.filename!r}; pass format= one of {FORMATS}")
        data = file.file.read(config.max_document_bytes + 1)
        too_large(len(data))
        if fmt != "pdf":
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(422, f"{file.filename!r} is not UTF-8 text") from None
        return ingest(name, source or file.filename or "upload", data, fmt)

    @app.delete("/v1/collections/{name}", dependencies=[Depends(auth)])
    def delete_collection(name: str):
        try:
            engine.drop_collection(name)
        except KeyError:
            raise HTTPException(404, f"no collection {name!r}") from None
        return {"deleted": name}

    @app.delete("/v1/collections/{name}/documents/{source:path}", dependencies=[Depends(auth)])
    def delete_document(name: str, source: str):
        removed = collection_or_404(name).remove(source)
        if not removed:
            raise HTTPException(404, f"no document {source!r} in {name!r}")
        return {"collection": name, "source": source, "removed": removed}

    @app.post("/v1/retrieve", dependencies=[Depends(auth)])
    def retrieve(body: RetrieveIn):
        hits = collection_or_404(body.collection).search(body.question, body.k)
        try:
            scored = [{**p.__dict__, "p_select": engine.gates.select(body.question, p.text)} for p in hits]
            gates = "applied"
        except DecisionUnavailable:
            if not config.fail_open:
                raise
            scored, gates = [{**p.__dict__, "p_select": None} for p in hits], "unavailable"
        return {"question": body.question, "passages": scored, "gates": gates,
                "select_threshold": config.select_threshold}

    @app.post("/v1/context", dependencies=[Depends(auth)])
    def context(body: QuestionIn):
        collection_or_404(body.collection)
        return engine.context(body.question, body.collection).as_dict()

    @app.post("/v1/answer", dependencies=[Depends(auth)])
    def answer(body: QuestionIn):
        collection_or_404(body.collection)
        return engine.answer(body.question, body.collection)

    @app.post("/v1/systemone", dependencies=[Depends(auth)])
    def systemone(body: dict):
        """The decision backend itself, so one instance with a GPU can serve the model to others."""
        state, questions = body.get("state"), body.get("questions")
        if state is None or not isinstance(questions, dict) or not questions:
            raise HTTPException(422, "a System One request needs a state and at least one question")
        return {"model": body.get("model", "kev-latest"), "answers": engine.decision.ask(state, questions)}

    @app.post("/v1/check/grounding", dependencies=[Depends(auth)])
    def check_grounding(body: GroundingIn):
        return engine.check_grounding(body.question, body.evidence, body.answer).as_dict()

    return app
