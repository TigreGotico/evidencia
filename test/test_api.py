import pytest
from fastapi.testclient import TestClient

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia.api import create_app

Q = "Qual é a capital de Portugal?"


def client_for(decision=None, chat=None, **cfg):
    engine = Evidencia(Config(**cfg), decision=decision or StubDecision(), chat=chat or StubChat())
    return TestClient(create_app(engine=engine))


@pytest.fixture
def client():
    c = client_for()
    assert c.post("/v1/collections/geo/documents", json={"source": "geo.txt", "text": DOC}).status_code == 200
    return c


def test_ingest_reports_passage_counts():
    c = client_for()
    r = c.post("/v1/collections/geo/documents", json={"source": "geo.txt", "text": DOC})
    assert r.json() == {"collection": "geo", "source": "geo.txt", "passages": 4, "total": 4}
    assert c.get("/v1/collections").json() == {"geo": {"passages": 4, "sources": ["geo.txt"]}}


def test_answer_round_trip(client):
    r = client.post("/v1/answer", json={"collection": "geo", "question": Q})
    body = r.json()
    assert r.status_code == 200
    assert body["answer"] == "Lisboa."
    assert body["gates"] == "applied"
    assert [p["text"] for p in body["evidence"]] == ["Lisboa é a capital de Portugal desde o século XIII."]
    assert body["sufficient"] == {"p": 0.9, "threshold": 0.5, "passed": True}


def test_context_does_not_generate():
    chat = StubChat()
    c = client_for(chat=chat)
    c.post("/v1/collections/geo/documents", json={"source": "geo.txt", "text": DOC})
    body = c.post("/v1/context", json={"collection": "geo", "question": Q}).json()
    assert body["abstain"] is False and len(body["evidence"]) == 1
    assert chat.prompts == []


def test_retrieve_scores_every_passage(client):
    body = client.post("/v1/retrieve", json={"collection": "geo", "question": Q, "k": 4}).json()
    assert body["gates"] == "applied"
    assert {p["p_select"] for p in body["passages"]} <= {0.9, 0.1}
    assert body["passages"][0]["p_select"] == 0.9


def test_check_grounding():
    c = client_for(decision=StubDecision(grounded=0.25))
    r = c.post("/v1/check/grounding", json={"question": Q, "evidence": ["Lisboa é a capital."], "answer": "Porto."})
    assert r.json() == {"p": 0.25, "threshold": 0.5, "passed": False}


def test_unknown_collection_is_404(client):
    assert client.post("/v1/answer", json={"collection": "nope", "question": Q}).status_code == 404
    assert client.delete("/v1/collections/nope").status_code == 404


def test_fail_closed_is_503_and_hides_the_cause(caplog):
    c = client_for(decision=StubDecision(down=True), fail_open=False)
    c.post("/v1/collections/geo/documents", json={"source": "geo.txt", "text": DOC})
    r = c.post("/v1/answer", json={"collection": "geo", "question": Q})
    assert r.status_code == 503
    assert r.json() == {"detail": "decision backend unavailable"}
    assert "stub is down" in caplog.text


def test_missing_generator_is_502_and_hides_the_cause(caplog):
    engine = Evidencia(Config(), decision=StubDecision())
    c = TestClient(create_app(engine=engine))
    c.post("/v1/collections/geo/documents", json={"source": "geo.txt", "text": DOC})
    r = c.post("/v1/answer", json={"collection": "geo", "question": Q})
    assert r.status_code == 502
    assert r.json() == {"detail": "generator unavailable"}
    assert "no generator configured" in caplog.text


def test_bearer_auth():
    c = client_for(api_key="k1")
    doc = {"source": "geo.txt", "text": DOC}
    assert c.post("/v1/collections/geo/documents", json=doc).status_code == 401
    assert c.post("/v1/collections/geo/documents", json=doc,
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/v1/collections/geo/documents", json=doc,
                  headers={"Authorization": "Bearer k1"}).status_code == 200
    health = c.get("/health").json()
    assert health == {"status": "ok", "version": health["version"], "decision_reachable": True}
    assert c.get("/v1/status").status_code == 401


def test_url_ingest_is_off_by_default():
    r = client_for().post("/v1/collections/geo/documents", json={"source": "x", "url": "http://127.0.0.1:1/"})
    assert r.status_code == 403


def test_text_xor_url():
    c = client_for()
    assert c.post("/v1/collections/geo/documents", json={"source": "x"}).status_code == 422
    assert c.post("/v1/collections/geo/documents",
                  json={"source": "x", "text": "a", "url": "http://x"}).status_code == 422


def test_file_upload_detects_format_and_rejects_bad_text():
    c = client_for()
    r = c.post("/v1/collections/geo/files", files={"file": ("notas.md", b"# Notas\n\nLisboa tem sete colinas.")})
    assert r.json()["passages"] == 1
    assert c.get("/v1/collections").json()["geo"]["sources"] == ["notas.md"]
    r = c.post("/v1/collections/geo/files", files={"file": ("mau.txt", b"\xff\xfe\xfa")})
    assert r.status_code == 422
    r = c.post("/v1/collections/geo/files", files={"file": ("dados.bin", b"abc")})
    assert r.status_code == 422


def test_document_too_large_is_413():
    c = client_for(max_document_bytes=10)
    r = c.post("/v1/collections/geo/documents", json={"source": "x", "text": "a" * 11})
    assert r.status_code == 413


def test_delete_document(client):
    r = client.delete("/v1/collections/geo/documents/geo.txt")
    assert r.json() == {"collection": "geo", "source": "geo.txt", "removed": 4}
    assert client.delete("/v1/collections/geo/documents/geo.txt").status_code == 404


def test_systemone_passthrough_serves_another_instance():
    """A second instance with the HTTP backend can use the first one as its decision server."""
    from evidencia import HttpDecision
    front = client_for(decision=StubDecision(grounded=0.3), api_key="k")
    q = {"grounded": {"type": "noul", "instructions": "?"}}
    r = front.post("/v1/systemone", json={"state": {"answer": "x"}, "questions": q},
                   headers={"Authorization": "Bearer k"})
    assert r.json() == {"model": "kev-latest", "answers": {"grounded": {"type": "noul", "noul": 0.3}}}
    assert front.post("/v1/systemone", json={"state": {}, "questions": q}).status_code == 401
    assert front.post("/v1/systemone", json={"state": {}, "questions": {}},
                      headers={"Authorization": "Bearer k"}).status_code == 422
    remote = HttpDecision("http://testserver", key="k")
    remote.session = front
    assert remote.ask({"answer": "x"}, q) == {"grounded": {"type": "noul", "noul": 0.3}}


def test_upload_limit_counts_bytes_not_characters():
    c = client_for(max_document_bytes=100)
    data = ("Lisboa é a capital de Portugal. " * 21).encode()      # 672 bytes, 651 characters
    r = c.post("/v1/collections/geo/files", files={"file": ("a.txt", data)})
    assert r.status_code == 413
    assert "geo" not in c.get("/v1/collections").json()


def test_upload_at_the_limit_is_kept_whole():
    text = "Lisboa é a capital. " * 5
    c = client_for(max_document_bytes=len(text.encode()))
    r = c.post("/v1/collections/geo/files", files={"file": ("a.txt", text.encode())})
    assert r.status_code == 200
    body = c.post("/v1/retrieve", json={"collection": "geo", "question": "capital", "k": 1}).json()
    assert body["passages"][0]["text"] == text.strip()


def test_text_limit_counts_bytes_not_characters():
    c = client_for(max_document_bytes=10)
    assert c.post("/v1/collections/geo/documents", json={"source": "x", "text": "éééééé"}).status_code == 413


def test_malformed_pdf_is_422():
    r = client_for().post("/v1/collections/geo/files", files={"file": ("a.pdf", b"%PDF-1.4 broken")})
    assert r.status_code == 422


def test_refused_request_is_422_and_hides_the_cause(caplog):
    c = client_for(decision=StubDecision(reject={"grounded"}))
    r = c.post("/v1/check/grounding", json={"question": Q, "evidence": ["Lisboa."], "answer": "Lisboa."})
    assert r.status_code == 422 and r.json() == {"detail": "decision model rejected the request"}
    assert "state too long" in caplog.text
    q = {"grounded": {"type": "noul", "instructions": "?"}}
    assert c.post("/v1/systemone", json={"state": {}, "questions": q}).status_code == 422


def test_health_probes_the_backend_at_most_once_per_interval():
    class Counting(StubDecision):
        probes = 0

        def describe(self):
            Counting.probes += 1
            return super().describe()

    c = client_for(decision=Counting())
    for _ in range(20):
        assert c.get("/health").json()["status"] == "ok"
    assert Counting.probes == 1


def test_models_lets_another_instance_report_reachable():
    from evidencia import HttpDecision
    front = client_for(api_key="k")
    remote = HttpDecision("http://testserver", key="k")
    remote.session = front
    assert remote.describe()["reachable"] is True
    assert remote.describe()["serves"] == ["kev-latest"]


@pytest.fixture
def web():
    """A real HTTP server for URL ingestion: /big streams 1 MB, /moved redirects, /page is a small page."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("location", "/page")
                self.end_headers()
                return
            body = b"x " * 500_000 if self.path == "/big" else "Lisboa é a capital.".encode()
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_url_ingest_limits_size_and_refuses_redirects(web):
    c = client_for(allow_url_ingest=True, max_document_bytes=1000)
    ingest = lambda path: c.post("/v1/collections/geo/documents", json={"source": path, "url": web + path})
    assert ingest("/page").json()["passages"] == 1
    assert ingest("/big").status_code == 413
    assert ingest("/moved").status_code == 422
    r = c.post("/v1/collections/geo/documents", json={"source": "x", "url": "http://127.0.0.1:1/"})
    assert r.status_code == 502 and r.json() == {"detail": "could not fetch the URL"}
