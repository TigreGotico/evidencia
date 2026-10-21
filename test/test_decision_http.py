import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from evidencia import DecisionUnavailable, Gates, HttpDecision
from evidencia.gates import load_locale


@pytest.fixture
def server():
    """A real HTTP server that records each request and replies with ``state["reply"]``."""
    state = {"requests": [], "reply": (200, {"answers": {}}), "key": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
            if state["key"] and self.headers.get("authorization") != f"Bearer {state['key']}":
                return self._send(401, {"detail": "unauthorized"})
            self._send(*state["reply"])

        def do_GET(self):
            self._send(200, {"models": [{"name": "kev-latest", "run": "TigreGotico/jevasio-0.8b-v2"}]})

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()


def test_request_body_matches_the_system_one_schema(server):
    server["reply"] = (200, {"answers": {"select": {"type": "noul", "noul": 0.73}}})
    gates = Gates(HttpDecision(server["url"], model="kev-latest"))
    assert gates.select("Qual é a capital?", "Lisboa é a capital.") == 0.73
    (req,) = server["requests"]
    assert req["path"] == "/v1/systemone"
    assert req["auth"] is None
    assert req["body"] == {"state": {"question": "Qual é a capital?", "passage": "Lisboa é a capital."},
                           "model": "kev-latest",
                           "questions": {"select": load_locale("pt-PT")["select"]}}
    assert req["body"]["questions"]["select"]["type"] == "noul"
    assert set(req["body"]["questions"]["select"]["criteria"]) == {"true", "false"}


def test_bearer_key_is_sent(server):
    server["key"] = "s3cret"
    server["reply"] = (200, {"answers": {"q": {"type": "noul", "noul": 0.5}}})
    HttpDecision(server["url"], key="s3cret").ask({"x": 1}, {"q": {"type": "noul", "instructions": "?"}})
    assert server["requests"][0]["auth"] == "Bearer s3cret"


def test_wrong_key_is_unavailable_not_an_answer(server):
    server["key"] = "s3cret"
    with pytest.raises(DecisionUnavailable, match="401"):
        HttpDecision(server["url"], key="wrong").ask({}, {"q": {"type": "noul", "instructions": "?"}})


def test_missing_answer_is_unavailable(server):
    server["reply"] = (200, {"answers": {"other": {"type": "noul", "noul": 0.5}}})
    with pytest.raises(DecisionUnavailable, match="no answer"):
        HttpDecision(server["url"]).ask({}, {"q": {"type": "noul", "instructions": "?"}})


def test_answer_without_probability_is_unavailable(server):
    server["reply"] = (200, {"answers": {"select": {"type": "choice", "choice": "a"}}})
    with pytest.raises(DecisionUnavailable, match="noul"):
        Gates(HttpDecision(server["url"])).select("q", "p")


def test_unreachable_server_is_unavailable():
    with pytest.raises(DecisionUnavailable):
        HttpDecision("http://127.0.0.1:9", timeout=2).ask({}, {"q": {"type": "noul", "instructions": "?"}})


def test_describe_reports_the_served_model(server):
    info = HttpDecision(server["url"]).describe()
    assert info["reachable"] is True and info["serves"] == ["TigreGotico/jevasio-0.8b-v2"]
    assert HttpDecision("http://127.0.0.1:9", timeout=2).describe()["reachable"] is False


@pytest.mark.parametrize("status", [400, 413, 422])
def test_refused_content_is_rejected_not_unavailable(server, status):
    from evidencia.decision import DecisionRejected
    server["reply"] = (status, {"detail": "state exceeds 8192 tokens"})
    with pytest.raises(DecisionRejected) as e:
        HttpDecision(server["url"]).ask({"x": 1}, {"g": {"type": "noul", "instructions": "?"}})
    assert not isinstance(e.value, DecisionUnavailable)     # an outage would let fail_open drop the gates


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_backend_faults_stay_unavailable(server, status):
    server["reply"] = (status, {"detail": "x"})
    with pytest.raises(DecisionUnavailable):
        HttpDecision(server["url"]).ask({"x": 1}, {"g": {"type": "noul", "instructions": "?"}})


def test_local_backend_that_cannot_load_is_unavailable():
    from evidencia import LocalDecision

    class Broken(LocalDecision):
        def _load(self):
            raise OSError("no such checkpoint")

    backend = Broken("nobody/nothing")
    q = {"select": load_locale("pt-PT")["select"]}
    with pytest.raises(DecisionUnavailable, match="no such checkpoint"):
        backend.ask({"question": "q", "passage": "p"}, q)
    assert backend.describe()["reachable"] is False


def test_local_backend_without_torch_is_unavailable(monkeypatch):
    from evidencia import LocalDecision

    monkeypatch.setitem(sys.modules, "torch", None)     # as when the local extra is not installed
    monkeypatch.delitem(sys.modules, "evidencia._vendor.kev.model", raising=False)
    backend = LocalDecision("nobody/nothing")
    with pytest.raises(DecisionUnavailable, match="torch"):
        backend.ask({"question": "q", "passage": "p"}, {"select": load_locale("pt-PT")["select"]})
    assert backend.describe()["reachable"] is False
