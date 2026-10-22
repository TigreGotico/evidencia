"""HTTP API."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import queue
import threading
import time
from importlib import resources
from pathlib import PurePath
from typing import Annotated, Literal

from curl_cffi import requests as curl
from curl_cffi.const import CurlECode, CurlOpt
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from evidencia import __version__
from evidencia.config import REQUEST_SIZES, THRESHOLDS, Config
from evidencia.decision import DecisionRejected, DecisionUnavailable
from evidencia.index import FORMATS
from evidencia.pipeline import Evidencia, GeneratorUnavailable
from evidencia.sources import CATALOG, RemoteSource

LOG = logging.getLogger("evidencia")
Format = Literal["text", "markdown", "html", "pdf"]
HEALTH_TTL = 10.0       # seconds a decision-backend probe result is reused by /health
URL_FETCH_TIMEOUT = 30.0    # seconds for the whole download of a document fetched by URL
MAX_QUESTION = 2000         # characters in a question
MAX_TEXT = 20_000           # characters in an answer or one evidence passage sent for a grounding check
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


class SettingsIn(BaseModel):
    """The retrieval sizes and thresholds that a request may set for itself."""
    top_k: int | None = Field(default=None, ge=1, strict=True, description="Selected passages kept as evidence")
    n_candidates: int | None = Field(default=None, ge=1, strict=True, description="Passages retrieved in round one")
    max_rounds: int | None = Field(default=None, ge=1, strict=True, description="Rounds of widening at most")
    select_threshold: float | None = Field(default=None, ge=0, le=1, strict=True)
    sufficient_threshold: float | None = Field(default=None, ge=0, le=1, strict=True)
    grounded_threshold: float | None = Field(default=None, ge=0, le=1, strict=True)


class QuestionIn(SettingsIn):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    collection: str | None = Field(default=None, description="A local collection to search")
    sources: list[str] = Field(default=[], description="Remote sources to search, from GET /v1/sources")

    @model_validator(mode="after")
    def somewhere(self):
        if self.collection is None and not self.sources:
            raise ValueError("name a collection, at least one source, or both")
        return self


class StreamIn(QuestionIn):
    generate: bool = Field(default=True, description="False stops after the context gates, as /v1/context does")


class RetrieveIn(QuestionIn):
    k: int = Field(default=8, ge=1, le=100)


class VectorSearchIn(SettingsIn):
    """An OpenAI vector store search request; the vector store is a collection."""
    query: str | list[str]
    max_num_results: int = Field(default=10, ge=1, le=50)
    sources: list[str] = Field(default=[], description="Remote sources to search as well, from GET /v1/sources")


class GroundingIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    evidence: list[Annotated[str, Field(max_length=MAX_TEXT)]] = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=MAX_TEXT)


class EventStream(StreamingResponse):
    """Server-sent events that set ``left`` when the response ends for any reason. A disconnect can cancel the
    response while the event generator waits at a yield, and the generator's own cleanup then never runs."""

    def __init__(self, events, left: threading.Event):
        super().__init__(events, media_type="text/event-stream", headers={"cache-control": "no-cache"})
        self.left = left

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.left.set()


class StreamClosed(Exception):
    """Raised inside a streamed request after its client has gone, to stop the work."""


def create_app(config: Config | None = None, engine: Evidencia | None = None) -> FastAPI:
    config = config or (engine.config if engine else Config.load())
    engine = engine or Evidencia(config)
    app = FastAPI(title="evidencia", version=__version__,
                  description="Decision gates for retrieval-augmented generation.")
    app.state.engine = engine
    # a JSON body escapes each character in at most 6 bytes; anything larger is refused before it is read
    max_body = 6 * config.max_document_bytes + 64 * 1024

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        size = request.headers.get("content-length")
        if size is None and "chunked" in request.headers.get("transfer-encoding", ""):
            return JSONResponse({"detail": "send the request body with a Content-Length"}, status_code=411)
        if size and size.isdigit() and int(size) > max_body:
            return JSONResponse({"detail": f"request body larger than {max_body} bytes"}, status_code=413)
        return await call_next(request)

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
        return JSONResponse({"detail": e.reason}, status_code=502)

    def collection_or_404(name: str):
        try:
            return engine.collection(name)
        except KeyError:
            raise HTTPException(404, f"no collection {name!r}") from None

    def check_targets(body: QuestionIn):
        try:
            engine.targets(body.collection, body.sources)
        except KeyError:
            raise HTTPException(404, f"no collection {body.collection!r}") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    def question_from(body: SettingsIn, **fields) -> QuestionIn:
        try:
            return QuestionIn(**body.model_dump(include=set(SettingsIn.model_fields)), **fields)
        except ValidationError as e:
            raise HTTPException(422, "; ".join(err["msg"] for err in e.errors())) from None

    def settings(body: SettingsIn) -> Config:
        """The configuration of this request: the server's, with the thresholds and sizes the body sets."""
        try:
            given = {name: getattr(body, name) for name in (*REQUEST_SIZES, *THRESHOLDS)}
            return engine.config.for_request(**given)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    def too_large(size: int):
        if size > config.max_document_bytes:
            raise HTTPException(413, f"document larger than {config.max_document_bytes} bytes")

    def ingest(name: str, source: str, content: str | bytes, fmt: str, url: str = "") -> dict:
        try:
            col, added = engine.add_document(name, content, source, fmt, url)
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
                "generator": config.generator, "generator_configured": engine.has_generator,
                "fail_open": config.fail_open,
                "thresholds": {"select": config.select_threshold, "sufficient": config.sufficient_threshold,
                               "grounded": config.grounded_threshold},
                "retrieval": {"n_candidates": config.n_candidates, "top_k": config.top_k,
                              "max_rounds": config.max_rounds, "hybrid": config.hybrid,
                              "dedupe_threshold": config.dedupe_threshold},
                "sources": sorted(engine.sources),
                "collections": len(engine.collections_snapshot())}

    @app.get("/v1/models", dependencies=[Depends(auth)])
    def models():
        """The model this instance serves on /v1/systemone, in the shape HttpDecision.describe reads."""
        name = config.decision.run if config.decision.backend == "local" else config.decision.model
        return {"models": [{"name": name}]}

    @app.get("/v1/sources", dependencies=[Depends(auth)])
    def sources():
        """Every remote source evidencia knows, with its licence, whether this service queries it, and the health of
        each source it queries."""
        return {"sources": [{**cls.describe(), "enabled": name in engine.sources,
                             "health": source.health() if isinstance(source := engine.sources.get(name), RemoteSource)
                             else None} for name, cls in CATALOG.items()]}

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
            # curl bounds the whole transfer, headers included, and stops at the size limit; a redirect could
            # lead to a host the client may not name directly, so it is refused, not followed
            with curl.Session(curl_options={CurlOpt.MAXFILESIZE_LARGE: config.max_document_bytes}) as http:
                resp = http.get(doc.url, timeout=URL_FETCH_TIMEOUT, allow_redirects=False)
        except curl.RequestsError as e:
            if e.code == CurlECode.FILESIZE_EXCEEDED:
                too_large(config.max_document_bytes + 1)
            LOG.warning("could not fetch %s: %s", doc.url, e)
            raise HTTPException(502, "could not fetch the URL") from None
        if 300 <= resp.status_code < 400:
            raise HTTPException(422, "the URL redirects; give the final URL")
        if resp.status_code >= 400:
            LOG.warning("could not fetch %s: HTTP %s", doc.url, resp.status_code)
            raise HTTPException(502, "could not fetch the URL")
        data = resp.content
        too_large(len(data))
        if doc.format == "pdf":
            return ingest(name, doc.source, data, doc.format, doc.url)
        charset = resp.headers.get("content-type", "").partition("charset=")[2].split(";")[0].strip(' "')
        try:
            text = data.decode(charset or "utf-8")
        except (LookupError, UnicodeDecodeError):
            raise HTTPException(422, "the URL did not return text in its declared encoding") from None
        return ingest(name, doc.source, text, doc.format, doc.url)

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
        try:
            removed = engine.remove_document(name, source)
        except KeyError:
            raise HTTPException(404, f"no collection {name!r}") from None
        if not removed:
            raise HTTPException(404, f"no document {source!r} in {name!r}")
        return {"collection": name, "source": source, "removed": removed}

    @app.post("/v1/retrieve", dependencies=[Depends(auth)])
    def retrieve(body: RetrieveIn):
        check_targets(body)
        return engine.retrieve(body.question, body.collection, body.sources, body.k, settings(body))

    @app.post("/v1/context", dependencies=[Depends(auth)])
    def context(body: QuestionIn):
        check_targets(body)
        return engine.context(body.question, body.collection, body.sources, settings=settings(body)).as_dict()

    @app.post("/v1/answer", dependencies=[Depends(auth)])
    def answer(body: QuestionIn):
        check_targets(body)
        return engine.answer(body.question, body.collection, body.sources, settings=settings(body))

    @app.post("/v1/answer/stream", dependencies=[Depends(auth)])
    def answer_stream(body: StreamIn, request: Request):
        """Server-sent events: one event per pipeline step, then ``result`` with the body of /v1/answer or /v1/context."""
        check_targets(body)
        cfg = settings(body)
        events: queue.Queue = queue.Queue()
        left = threading.Event()    # the client closed the stream

        def emit(name: str, data: dict):
            if left.is_set():
                raise StreamClosed
            events.put((name, data))

        def run():
            try:
                if body.generate:
                    result = engine.answer(body.question, body.collection, body.sources, on_event=emit, settings=cfg)
                else:
                    result = engine.context(body.question, body.collection, body.sources, on_event=emit,
                                            settings=cfg).as_dict()
                events.put(("result", result))
            except StreamClosed:
                LOG.info("answer stream closed by the client; stopped")
            except DecisionUnavailable as e:
                LOG.error("decision backend unavailable: %s", e)
                events.put(("error", {"status": 503, "detail": "decision backend unavailable"}))
            except DecisionRejected as e:
                LOG.warning("decision model rejected a request: %s", e)
                events.put(("error", {"status": 422, "detail": "decision model rejected the request"}))
            except GeneratorUnavailable as e:
                LOG.error("generator unavailable: %s", e)
                events.put(("error", {"status": 502, "detail": e.reason}))
            except Exception:
                LOG.exception("answer stream failed")
                events.put(("error", {"status": 500, "detail": "internal error"}))
            events.put(None)

        threading.Thread(target=run, daemon=True).start()

        async def stream():
            while True:
                try:
                    item = events.get_nowait()
                except queue.Empty:
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.05)
                    continue
                if item is None:
                    return
                name, data = item
                yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return EventStream(stream(), left)

    @app.post("/v1/vector_stores/{collection}/search", dependencies=[Depends(auth)])
    def vector_store_search(collection: str, body: VectorSearchIn):
        """The passages that pass the select gate, as an OpenAI vector store search. ``score`` is the select
        probability. An abstention returns no passages, and without the decision backend the request fails."""
        queries = [body.query] if isinstance(body.query, str) else body.query
        q = question_from(body, question=" ".join(queries), collection=collection, sources=body.sources)
        check_targets(q)
        ctx = engine.context(q.question, q.collection, q.sources, settings=settings(body))
        if not ctx.gated:
            raise HTTPException(503, "decision backend unavailable")
        evidence = [] if ctx.abstain else ctx.evidence[:body.max_num_results]
        return {"object": "vector_store.search_results.page", "search_query": queries,
                "data": [{"file_id": s.passage.source, "filename": s.passage.source, "score": s.p_select,
                          "attributes": {"passage_id": s.passage.id, "url": s.passage.url, "origin": s.passage.origin},
                          "content": [{"type": "text", "text": s.passage.text}]} for s in evidence],
                "has_more": False, "next_page": None,
                "evidencia": {"abstained": ctx.abstain, "abstained_at": ctx.abstained_at}}

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

    if config.ui:
        page = (resources.files("evidencia") / "web" / "index.html").read_text(encoding="utf-8")

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def ui():
            return page

    return app
