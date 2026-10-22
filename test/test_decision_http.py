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


@pytest.mark.parametrize("reply", [{"answers": [{"noul": 0.9}]}, {"answers": None}, [{"noul": 0.9}],
                                   {"answers": {"select": 0.9}}])
def test_a_reply_of_the_wrong_shape_is_unavailable(reply, monkeypatch):
    from evidencia.decision import DecisionUnavailable, HttpDecision
    decision = HttpDecision("http://decision")

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return reply
    monkeypatch.setattr(decision.session, "post", lambda *a, **k: Reply())
    with pytest.raises(DecisionUnavailable):
        decision.ask({"question": "q", "passage": "p"}, {"select": {}})


@pytest.fixture
def slow_server():
    """A threaded server that takes 0.2 s per request and records how many requests it held at once."""
    import time
    from http.server import ThreadingHTTPServer
    state = {"now": 0, "most": 0, "lock": threading.Lock(), "threads": set()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            with state["lock"]:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(0.2)
            with state["lock"]:
                state["now"] -= 1
            passage = body["state"]["passage"]
            if passage == "longa":
                data, status = b'{"detail": "state too long"}', 422
            else:
                data, status = json.dumps({"answers": {"select": {"type": "noul", "noul": len(passage) / 100}}}).encode(), 200
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()


def test_many_select_questions_go_to_the_server_at_once_and_keep_their_order(slow_server):
    from evidencia.decision import DecisionRejected
    q = {"select": load_locale("pt-PT")["select"]}
    passages = ["a" * n for n in range(1, 8)] + ["longa"]
    answers = HttpDecision(slow_server["url"], workers=4).ask_many([({"question": "q", "passage": p}, q) for p in passages])
    assert [a["select"]["noul"] for a in answers[:7]] == [n / 100 for n in range(1, 8)]
    assert isinstance(answers[7], DecisionRejected)
    assert slow_server["most"] == 4


def test_the_local_backend_scores_many_states_in_one_pass_and_refuses_only_the_long_one(monkeypatch):
    """The model calls are fakes: the real batch was compared with single calls on the checkpoint itself."""
    import types
    from evidencia import LocalDecision
    from evidencia.decision import DecisionRejected

    class Fake(LocalDecision):
        passes = []

        def _load(self):
            return "tok", "model"

        def _batch_probs(self, model, encs):
            self.passes.append(len(encs))
            return [[[1 - e["p"], e["p"]]] for e in encs]

    def admit(model, tok, rec):
        text = str(rec)
        if "longa" in text:
            raise ValueError("state too long")
        return {"p": 0.25 if "um" in text else 0.75}

    monkeypatch.setitem(sys.modules, "evidencia._vendor.kev.model", types.SimpleNamespace(admit=admit))
    q = {"select": load_locale("pt-PT")["select"]}
    backend = Fake("nobody/nothing")
    answers = backend.ask_many([({"question": "q", "passage": p}, q) for p in ["um", "longa", "dois"]])
    assert isinstance(answers[1], DecisionRejected)
    assert [answers[0]["select"]["noul"], answers[2]["select"]["noul"]] == [0.25, 0.75]
    assert Fake.passes == [2]
