import json
import socket
import threading
import time

import pytest
import requests
import uvicorn

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia, Passage
from evidencia.api import create_app
from evidencia.index import Collection
from evidencia.pipeline import strip_citations
from evidencia.sources import ArquivoPt, SourceUnavailable, Wikipedia
from test_sources import STOPWORDS, FakeHttp, FakeResponse

Q = "Qual é a capital de Portugal?"


def test_a_bracketed_number_that_names_no_passage_reaches_the_grounded_gate():
    assert strip_citations("Foi construída em [1910] [1].", 1) == "Foi construída em [1910]."
    decision = StubDecision()
    engine = Evidencia(Config(), decision=decision, chat=StubChat("Lisboa [1], desde [1910]."), sources={})
    engine.add_document("geo", DOC, "geo.txt")
    engine.answer(Q, "geo")
    assert [s["answer"] for g, s in decision.calls if g == "grounded"] == ["Lisboa, desde [1910]."]


def test_a_collection_name_longer_than_a_file_name_is_saved(tmp_path):
    name = "Coleção de documentos históricos de Évora e Óbidos " * 4
    first = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    first.add_document(name, DOC, "geo.txt")
    second = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    assert list(second.collections) == [name]
    assert len(second.collection(name).passages) == 4


def test_a_collection_dropped_during_an_add_stays_dropped(tmp_path, monkeypatch):
    engine = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    engine.add_document("geo", DOC, "geo.txt")
    adding, release = threading.Event(), threading.Event()
    add = Collection.add

    def slow_add(self, *args, **kwargs):
        adding.set()
        release.wait(5)
        return add(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "add", slow_add)
    worker = threading.Thread(target=engine.add_document, args=("geo", "Outro texto.", "outro.txt"))
    worker.start()
    assert adding.wait(5)
    engine.drop_collection("geo")
    release.set()
    worker.join(5)
    monkeypatch.setattr(Collection, "add", add)
    restarted = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    assert restarted.collections == {}


def test_a_collection_with_the_name_of_a_requested_source_is_refused():
    engine = Evidencia(Config(), decision=StubDecision(), chat=StubChat(), sources={"pt.wikipedia": Collection("x")})
    engine.add_document("pt.wikipedia", DOC, "geo.txt")
    with pytest.raises(ValueError, match="also the name of a source"):
        engine.context(Q, "pt.wikipedia", ["pt.wikipedia"])


class SourceWithoutOrigin:
    name = "glossario"

    def search(self, query, k):
        return [Passage("g1", "A capital de Portugal é Lisboa.", "glossario.txt", 1.0)][:k]


def test_a_custom_source_that_sets_no_origin_is_named_by_the_engine():
    engine = Evidencia(Config(), decision=StubDecision(), chat=StubChat(), sources={"glossario": SourceWithoutOrigin()})
    ctx = engine.context(Q, sources=["glossario"])
    assert [(s.passage.origin, s.passage.text) for s in ctx.evidence] == [("glossario", "A capital de Portugal é Lisboa.")]
    assert ctx.sources == [{"name": "glossario", "kind": "remote", "candidates": 1, "duplicates": 0, "error": None}]


class FlakySource:
    """Fails on its first search, then answers."""
    name = "wikidata"

    def __init__(self):
        self.calls = 0

    def search(self, query, k):
        self.calls += 1
        if self.calls == 1:
            raise SourceUnavailable("wikidata: timeout")
        return [Passage(f"w{i}", "Lisboa, capital de: Portugal.", "Lisboa", 1.0, origin=self.name) for i in range(1)]


def test_a_source_that_recovers_in_a_later_round_is_not_reported_as_failed():
    engine = Evidencia(Config(), decision=StubDecision(), chat=StubChat(), sources={"wikidata": FlakySource()})
    ctx = engine.context(Q, sources=["wikidata"])
    assert ctx.rounds == 2
    assert ctx.sources == [{"name": "wikidata", "kind": "remote", "candidates": 1, "duplicates": 0, "error": None}]


class TrickleResponse(FakeResponse):
    """Sends one byte every 0.1 s, so no single read waits as long as the timeout."""

    def iter_content(self, size):
        for byte in self.body:
            time.sleep(0.1)
            yield bytes([byte])


def test_source_timeout_bounds_the_whole_request():
    http = FakeHttp(lambda url, params: TrickleResponse(b'{"query": {"search": []}}'))
    source = Wikipedia(http, STOPWORDS, timeout=0.3)
    started = time.monotonic()
    with pytest.raises(SourceUnavailable):
        source.search(Q, 1)
    assert time.monotonic() - started < 1.0


def test_arquivo_reads_extracted_text_only_from_arquivo():
    def route(url, params):
        if url == ArquivoPt.api:
            return {"response_items": [{"title": "T", "linkToArchive": "https://arquivo.pt/wayback/1/x",
                                        "linkToExtractedText": "http://169.254.169.254/latest/meta-data"}]}
        return b"A Torre de Bel\xc3\xa9m foi constru\xc3\xadda em 1514."
    http = FakeHttp(route)
    assert ArquivoPt(http, STOPWORDS).search("Torre de Belém", 1) == []
    assert [url for url, _ in http.calls] == [ArquivoPt.api]


class SlowDecision(StubDecision):
    def ask(self, state, questions):
        time.sleep(0.05)
        return super().ask(state, questions)


def test_a_stream_that_the_client_leaves_stops_asking_the_decision_model():
    decision = SlowDecision()
    engine = Evidencia(Config(n_candidates=60, max_rounds=1), decision=decision, chat=StubChat(), sources={})
    engine.add_document("geo", "\n\n".join(f"Lisboa é a capital número {i}." for i in range(60)), "geo.txt")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(engine=engine), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        with requests.post(f"http://127.0.0.1:{port}/v1/answer/stream", json={"question": Q, "collection": "geo"},
                           stream=True, timeout=10) as resp:
            for line in resp.iter_lines():
                if line.startswith(b"event: select"):
                    break
        time.sleep(1.0)
        asked = len(decision.calls)
        time.sleep(1.0)
        assert len(decision.calls) == asked
        assert asked < 30
    finally:
        server.should_exit = True


def test_one_arquivo_page_that_fails_to_load_leaves_the_others():
    def route(url, params):
        if url == ArquivoPt.api:
            return {"response_items": [{"title": t, "linkToArchive": f"https://arquivo.pt/wayback/1/{t}",
                                        "linkToExtractedText": f"https://arquivo.pt/textextracted?{t}"} for t in "ab"]}
        if url.endswith("?a"):
            return OSError("curl: (63) Exceeded the maximum allowed file size")
        return "A Torre de Belém foi construída em 1514.".encode()
    hits = ArquivoPt(FakeHttp(route), STOPWORDS).search("Torre de Belém", 2)
    assert [h.source for h in hits] == ["b"]


class BrokenSource(Wikipedia):
    def fetch(self, question):
        raise NotImplementedError("a bug, not an outage")


def test_a_programming_error_in_a_source_is_raised_not_reported_as_an_outage():
    with pytest.raises(NotImplementedError):
        BrokenSource(FakeHttp(lambda url, params: {}), STOPWORDS).search(Q, 1)


class SlowSend(FakeHttp):
    """A transport that holds the whole reply before it returns, as curl_cffi and FlareSolverr do."""

    def _send(self, url, params):
        time.sleep(0.5)
        return super()._send(url, params)


def test_a_reply_that_arrives_whole_after_the_timeout_window_of_the_stream_is_kept():
    http = SlowSend(lambda url, params: {"query": {"search": []}})
    assert Wikipedia(http, STOPWORDS, timeout=0.3).get_json("https://pt.wikipedia.org/w/api.php") == {"query": {"search": []}}


class FlatDecision(StubDecision):
    def ask(self, state, questions):
        return {gate: 0.9 for gate in questions}


def test_a_decision_reply_without_an_answer_object_is_unavailable_not_a_crash():
    from evidencia.decision import DecisionUnavailable
    from evidencia.gates import Gates
    with pytest.raises(DecisionUnavailable):
        Gates(FlatDecision()).select(Q, DOC)


def test_a_corrupt_collection_file_is_set_aside_and_the_others_load(tmp_path):
    first = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    first.add_document("geo", DOC, "geo.txt")
    (tmp_path / "broken.json").write_text("{not json")
    second = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    assert list(second.collections) == ["geo"]
    assert (tmp_path / "broken.json.corrupt").read_text() == "{not json"


def test_an_embedding_model_that_cannot_load_stops_the_start_and_keeps_the_files(tmp_path):
    from evidencia.store import Store
    first = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    first.add_document("geo", DOC, "geo.txt")
    saved = sorted(p.name for p in tmp_path.iterdir())

    def offline(texts):
        raise OSError("LocalEntryNotFoundError: cannot reach the Hub")
    with pytest.raises(OSError):
        Store(str(tmp_path)).load(offline)
    assert sorted(p.name for p in tmp_path.iterdir()) == saved
