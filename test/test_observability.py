import json
import logging
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia, HttpDecision
from evidencia.api import create_app
from evidencia.embeddings import Embedder
from evidencia.gates import load_locale
from evidencia.observability import REQUEST_ID, JsonFormatter, RequestIdFilter, configure_logging
from evidencia.sources import SourceUnavailable, Wikipedia

Q = "Qual é a capital de Portugal?"
STOPWORDS = frozenset(load_locale("pt-PT")["stopwords"])
HEX_ID = re.compile(r"[0-9a-f]{32}")


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class DownSource:
    def __init__(self, name):
        self.name = name

    def search(self, query, k):
        raise SourceUnavailable(f"{self.name}: timeout")


def client_for(decision=None, sources=None, **cfg):
    engine = Evidencia(Config(**cfg), decision=decision or StubDecision(), chat=StubChat(), sources=sources)
    engine.collection("geo", create=True).add(DOC, "geo.txt")
    return TestClient(create_app(engine=engine))


@pytest.fixture
def records():
    """The records of the evidencia logger, with the request id filter of the service's handler."""
    caught: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record):
            caught.append(record)

    handler = Keep(logging.DEBUG)
    handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("evidencia")
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield caught
    logger.removeHandler(handler)
    logger.setLevel(level)


def test_a_request_without_an_id_gets_a_new_one():
    first = client_for().get("/health").headers["x-request-id"]
    second = client_for().get("/health").headers["x-request-id"]
    assert HEX_ID.fullmatch(first) and HEX_ID.fullmatch(second) and first != second


def test_a_valid_request_id_is_echoed():
    assert client_for().get("/health", headers={"X-Request-ID": "gw-7:a.b_c"}).headers["x-request-id"] == "gw-7:a.b_c"


@pytest.mark.parametrize("given", ["a b", "x" * 129, "id;drop", "é".encode()])
def test_an_unsafe_request_id_is_replaced(given):
    got = client_for().get("/health", headers={"X-Request-ID": given}).headers["x-request-id"]
    assert HEX_ID.fullmatch(got)


def test_a_refused_body_still_gets_an_id():
    r = client_for(max_document_bytes=10).post("/v1/collections/geo/documents", content=b"x" * 70_000,
                                               headers={"content-type": "application/json", "X-Request-ID": "big-1"})
    assert r.status_code == 413 and r.headers["x-request-id"] == "big-1"


def test_log_lines_from_source_threads_carry_the_request_id(records):
    c = client_for(sources={"a": DownSource("a"), "b": DownSource("b")})
    c.post("/v1/context", json={"question": Q, "sources": ["a", "b"]}, headers={"X-Request-ID": "req-42"})
    down = [r for r in records if r.getMessage().startswith("source unavailable")]
    assert len(down) >= 2
    assert {r.threadName for r in down} - {threading.current_thread().name}
    assert {r.request_id for r in down} == {"req-42"}


def test_log_lines_of_an_answer_stream_carry_the_request_id(records):
    c = client_for(sources={"a": DownSource("a")})
    with c.stream("POST", "/v1/answer/stream", json={"question": Q, "sources": ["a"]},
                  headers={"X-Request-ID": "stream-1"}) as r:
        r.read()
    down = [r for r in records if r.getMessage().startswith("source unavailable")]
    assert down and {r.request_id for r in down} == {"stream-1"}


def test_a_log_line_outside_a_request_has_no_id(records):
    logging.getLogger("evidencia").warning("outside")
    assert records[-1].request_id == "-"


def test_each_request_is_logged_once_with_its_route_and_status(records):
    c = client_for()
    c.post("/v1/context", json={"question": Q, "collection": "geo"}, headers={"X-Request-ID": "r-1"})
    c.get("/health", headers={"X-Request-ID": "r-2"})
    lines = [r for r in records if getattr(r, "route", None)]
    assert [(r.request_id, r.method, r.route, r.status, r.levelno) for r in lines] == [
        ("r-1", "POST", "/v1/context", 200, logging.INFO), ("r-2", "GET", "/health", 200, logging.DEBUG)]
    assert all(r.duration_ms >= 0 for r in lines)


def test_gate_decisions_are_logged_with_probability_and_threshold(records):
    body = client_for().post("/v1/context", json={"question": Q, "collection": "geo"}).json()
    select = [r for r in records if getattr(r, "gate", None) == "select"]
    assert {r.passage_id: r.p for r in select} == {p["id"]: p["p_select"] for p in body["candidates"]}
    assert {r.passage_id for r in select if r.passed} == {p["id"] for p in body["evidence"]}
    assert all(r.levelno == logging.DEBUG and r.threshold == 0.5 and r.round == 1 for r in select)
    (sufficient,) = [r for r in records if getattr(r, "gate", None) == "sufficient"]
    assert (sufficient.p, sufficient.threshold, sufficient.passed, sufficient.levelno) == (0.9, 0.5, True, logging.INFO)
    (summary,) = [r for r in records if r.getMessage().startswith("context:")]
    assert summary.abstained_at is None and summary.evidence == 1
    assert summary.sources == body["sources"]


def test_the_grounded_gate_is_logged(records):
    client_for(decision=StubDecision(grounded=0.3)).post("/v1/answer", json={"question": Q, "collection": "geo"})
    (grounded,) = [r for r in records if getattr(r, "gate", None) == "grounded"]
    assert (grounded.p, grounded.threshold, grounded.passed) == (0.3, 0.8, False)


def test_json_lines_hold_the_message_the_request_id_and_the_extras():
    token = REQUEST_ID.set("j-1")
    try:
        record = logging.LogRecord("evidencia", logging.WARNING, __file__, 1, "%s down", ("wikidata",), None)
        record.source = "wikidata"
        record.color_message = "\x1b[36mwikidata\x1b[0m down"
        RequestIdFilter().filter(record)
        line = json.loads(JsonFormatter().format(record))
    finally:
        REQUEST_ID.reset(token)
    assert {k: line[k] for k in ("level", "logger", "message", "request_id", "source")} == {
        "level": "WARNING", "logger": "evidencia", "message": "wikidata down", "request_id": "j-1",
        "source": "wikidata"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["time"])
    assert "exception" not in line and "color_message" not in line


def test_a_json_line_holds_the_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("evidencia", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    RequestIdFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in line["exception"] and line["request_id"] == "-"


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_configure_logging_writes_one_line_per_record_to_stderr(fmt, capsys):
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging(fmt, "DEBUG")
        token = REQUEST_ID.set("cfg-1")
        logging.getLogger("evidencia").debug("hello %s", "there")
        REQUEST_ID.reset(token)
        err = capsys.readouterr().err.strip().splitlines()
    finally:
        root.handlers[:], _ = saved
        root.setLevel(saved[1])
    assert len(err) == 1
    if fmt == "json":
        assert json.loads(err[0])["message"] == "hello there" and json.loads(err[0])["request_id"] == "cfg-1"
    else:
        assert err[0].endswith("DEBUG evidencia [cfg-1] hello there")


def test_requests_are_counted_by_route_template_and_status():
    c = client_for()
    key = {"route": "/v1/collections/{name}", "method": "DELETE", "status": "404"}
    before = sample("evidencia_requests_total", **key), sample("evidencia_request_seconds_count",
                                                               route="/v1/collections/{name}")
    c.delete("/v1/collections/nothere")
    c.delete("/v1/collections/alsonot")
    assert sample("evidencia_requests_total", **key) - before[0] == 2
    assert sample("evidencia_request_seconds_count", route="/v1/collections/{name}") - before[1] == 2


def test_a_path_with_no_route_is_counted_as_unmatched():
    key = {"route": "unmatched", "method": "GET", "status": "404"}
    before = sample("evidencia_requests_total", **key)
    client_for().get("/no/such/path/123")
    assert sample("evidencia_requests_total", **key) - before == 1


def test_abstentions_are_counted_by_gate():
    before = {g: sample("evidencia_abstentions_total", gate=g) for g in ("select", "sufficient", "grounded")}
    client_for(decision=StubDecision(keyword="nada")).post("/v1/context", json={"question": Q, "collection": "geo"})
    client_for(decision=StubDecision(sufficient=0.1)).post("/v1/answer", json={"question": Q, "collection": "geo"})
    client_for(decision=StubDecision(grounded=0.1)).post("/v1/answer", json={"question": Q, "collection": "geo"})
    client_for().post("/v1/answer", json={"question": Q, "collection": "geo"})
    assert {g: sample("evidencia_abstentions_total", gate=g) - before[g] for g in before} == {
        "select": 1, "sufficient": 1, "grounded": 1}


def test_decision_calls_and_select_batch_sizes_are_counted():
    decision = StubDecision()
    calls = {g: sample("evidencia_decision_calls_total", gate=g) for g in ("select", "sufficient", "grounded")}
    batch = sample("evidencia_select_batch_size_sum"), sample("evidencia_select_batch_size_count")
    client_for(decision=decision).post("/v1/answer", json={"question": Q, "collection": "geo"})
    assert {g: sample("evidencia_decision_calls_total", gate=g) - calls[g] for g in calls} == {
        "select": len(decision.batches), "sufficient": 1, "grounded": 1}
    assert sample("evidencia_select_batch_size_sum") - batch[0] == sum(decision.batches) > 0
    assert sample("evidencia_select_batch_size_count") - batch[1] == len(decision.batches)


def test_decision_errors_are_counted_by_gate_and_kind():
    rejected = {"gate": "sufficient", "error": "rejected"}
    down = {"gate": "select", "error": "unavailable"}
    before = sample("evidencia_decision_errors_total", **rejected), sample("evidencia_decision_errors_total", **down)
    client_for(decision=StubDecision(reject=("sufficient",))).post("/v1/context", json={"question": Q, "collection": "geo"})
    client_for(decision=StubDecision(down=True)).post("/v1/context", json={"question": Q, "collection": "geo"})
    assert sample("evidencia_decision_errors_total", **rejected) - before[0] == 1
    assert sample("evidencia_decision_errors_total", **down) - before[1] == 1


def test_source_fetches_errors_and_pauses_are_counted(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])

    class Http:
        def get(self, url, params=None, timeout=None, stream=False):
            raise requests.ConnectionError("no route")

    source = Wikipedia(Http(), STOPWORDS)
    name = {"source": "pt.wikipedia"}
    before = [sample(f"evidencia_source_{m}_total", **name) for m in ("fetches", "errors", "pauses")]
    for question in ("Lisboa", "Porto", "Faro", "Braga"):
        with pytest.raises(SourceUnavailable):
            source.search(question, 2)
    now[0] += 61
    with pytest.raises(SourceUnavailable):
        source.search("Évora", 2)
    assert [sample(f"evidencia_source_{m}_total", **name) - b
            for m, b in zip(("fetches", "errors", "pauses"), before)] == [4, 4, 2]


def test_embed_calls_and_texts_are_counted():
    class Model:
        def encode(self, texts):
            return np.ones((len(texts), 3))

    embedder = Embedder("stub")
    embedder._model = Model()
    before = sample("evidencia_embed_calls_total"), sample("evidencia_embed_texts_total")
    embedder(["a", "b", "c"])
    assert (sample("evidencia_embed_calls_total") - before[0], sample("evidencia_embed_texts_total") - before[1]) == (1, 3)


def test_metrics_need_the_api_key():
    c = client_for(api_key="k")
    assert c.get("/metrics").status_code == 401
    r = c.get("/metrics", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'evidencia_requests_total{method="GET",route="/metrics",status="401"}' in r.text


def test_the_decision_request_carries_the_request_id():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            seen.append(self.headers.get("x-request-id"))
            data = json.dumps({"answers": {"select": {"type": "noul", "noul": 0.5}}}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        decision = HttpDecision(f"http://127.0.0.1:{httpd.server_port}", workers=2)
        items = [({"question": Q, "passage": p}, {"select": {"type": "noul"}}) for p in ("a", "b", "c")]
        decision.ask_many(items)
        token = REQUEST_ID.set("dec-5")
        try:
            decision.ask_many(items)
        finally:
            REQUEST_ID.reset(token)
    finally:
        httpd.shutdown()
    assert seen == [None] * 3 + ["dec-5"] * 3


@pytest.mark.parametrize("key, value", [("log_format", "xml"), ("log_level", "LOUD"), ("log_level", 10)])
def test_config_refuses_an_unknown_log_setting(key, value):
    with pytest.raises(ValueError, match=key):
        Config(**{key: value})


def test_config_reads_the_log_settings_from_the_environment():
    cfg = Config.load(env={"EVIDENCIA_LOG_FORMAT": "json", "EVIDENCIA_LOG_LEVEL": "debug"})
    assert (cfg.log_format, cfg.log_level) == ("json", "DEBUG")
    assert (Config().log_format, Config().log_level) == ("text", "INFO")


def test_an_unknown_method_adds_no_metric_series():
    c = client_for()
    for method in ("FOO1", "FOO2"):
        c.request(method, "/no/route")
    assert sample("evidencia_requests_total", route="unmatched", method="FOO1", status="404") == 0
    assert sample("evidencia_requests_total", route="unmatched", method="other", status="404") >= 2


def test_an_unhandled_error_replies_with_the_id_and_logs_the_traceback_under_it(records):
    engine = Evidencia(Config(), decision=StubDecision(), chat=StubChat())
    engine.collection("geo", create=True).add(DOC, "geo.txt")
    engine.context = lambda *a, **k: 1 / 0
    c = TestClient(create_app(engine=engine), raise_server_exceptions=False)
    r = c.post("/v1/context", json={"question": Q, "collection": "geo"}, headers={"X-Request-ID": "boom-1"})
    assert (r.status_code, r.headers["x-request-id"], r.json()) == (500, "boom-1", {"detail": "internal error"})
    (failed,) = [rec for rec in records if rec.exc_info]
    assert failed.request_id == "boom-1" and failed.exc_info[0] is ZeroDivisionError
    assert [rec.status for rec in records if getattr(rec, "route", None)] == [500]


def test_the_request_line_quotes_control_characters_of_the_path(records):
    client_for().get("/x%1B[31mred")
    (line,) = [r for r in records if getattr(r, "route", None)]
    assert "\x1b" not in line.getMessage() and "%1B" in line.getMessage()


def test_concurrent_failures_count_one_pause(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])

    class Http:
        def get(self, url, params=None, timeout=None, stream=False):
            raise requests.ConnectionError("no route")

    source = Wikipedia(Http(), STOPWORDS)
    before = sample("evidencia_source_pauses_total", source="pt.wikipedia")
    for _ in range(6):     # as fetches that started before the pause and fail after it
        source._failed(requests.ConnectionError("no route"), 0.1)
    assert sample("evidencia_source_pauses_total", source="pt.wikipedia") - before == 1


def test_debug_applies_to_the_lines_of_evidencia_only():
    root, ours = logging.getLogger(), logging.getLogger("evidencia")
    saved = root.handlers[:], root.level, ours.level
    try:
        # a library logger with no level of its own, as urllib3 has in the service
        library = logging.getLogger("evidencia-test-library")
        configure_logging("text", "DEBUG")
        assert ours.isEnabledFor(logging.DEBUG) and not library.isEnabledFor(logging.DEBUG)
        assert library.isEnabledFor(logging.INFO)
        configure_logging("text", "WARNING")
        assert not ours.isEnabledFor(logging.INFO) and not library.isEnabledFor(logging.INFO)
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        ours.setLevel(saved[2])


def test_an_extra_field_does_not_replace_a_standard_field():
    record = logging.LogRecord("evidencia", logging.INFO, __file__, 1, "real", (), None)
    record.level = "FAKE"
    RequestIdFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    assert (line["level"], line["message"]) == ("INFO", "real")
