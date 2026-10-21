import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia.api import create_app
from evidencia.index import Collection
from evidencia.pipeline import cited, strip_citations
from evidencia.sources import SourceUnavailable

Q = "Qual é a capital de Portugal?"
REMOTE = ("A capital de Portugal é Lisboa, segundo a enciclopédia.\n\n"
          "Portugal é um país da Península Ibérica.")


def remote(name="pt.wikipedia"):
    col = Collection(name)
    col.add(REMOTE, "Portugal", url="https://pt.wikipedia.org/wiki/Portugal")
    return col


class DownSource:
    name = "wikidata"

    def search(self, query, k):
        raise SourceUnavailable("wikidata: ConnectionError")


def engine(decision=None, chat=None, sources=None, **cfg):
    e = Evidencia(Config(**cfg), decision=decision or StubDecision(), chat=chat or StubChat(),
                  sources={"pt.wikipedia": remote()} if sources is None else sources)
    e.add_document("geo", DOC, "geo.txt")
    return e


def test_a_question_can_use_remote_sources_only():
    ctx = engine().context(Q, sources=["pt.wikipedia"])
    assert [(s.passage.text, s.passage.origin, s.passage.url) for s in ctx.evidence] == [
        ("A capital de Portugal é Lisboa, segundo a enciclopédia.", "pt.wikipedia",
         "https://pt.wikipedia.org/wiki/Portugal")]
    assert ctx.sources == [{"name": "pt.wikipedia", "kind": "remote", "candidates": 2, "duplicates": 0, "error": None}]


def test_local_and_remote_candidates_alternate_by_rank():
    ctx = engine().context(Q, "geo", ["pt.wikipedia"])
    origins = [s.passage.origin for s in ctx.candidates]
    assert origins[:4] == ["geo", "pt.wikipedia", "geo", "pt.wikipedia"]
    assert {s.passage.text for s in ctx.evidence} == {"Lisboa é a capital de Portugal desde o século XIII.",
                                                      "A capital de Portugal é Lisboa, segundo a enciclopédia."}


def test_a_failed_remote_source_is_reported_and_the_collection_still_answers():
    e = engine(sources={"wikidata": DownSource()})
    result = e.answer(Q, "geo", ["wikidata"])
    assert result["answer"] == "Lisboa."
    assert result["sources"] == [
        {"name": "geo", "kind": "collection", "candidates": 2, "duplicates": 0, "error": None},
        {"name": "wikidata", "kind": "remote", "candidates": 0, "duplicates": 0, "error": "unavailable"}]


def test_only_enabled_sources_and_at_least_one_target():
    e = engine()
    with pytest.raises(ValueError, match="not enabled"):
        e.context(Q, "geo", ["arquivo.pt"])
    with pytest.raises(ValueError, match="name a collection"):
        e.context(Q)


def test_the_answer_cites_evidence_numbers_and_grounding_sees_no_markers():
    decision = StubDecision()
    chat = StubChat("Lisboa [1], e não o Porto [1, 9].")
    result = engine(decision, chat).answer(Q, "geo")
    assert result["answer"] == "Lisboa [1], e não o Porto [1, 9]."
    assert result["cited"] == [1]
    grounded_state = [state for gate, state in decision.calls if gate == "grounded"][0]
    assert grounded_state["answer"] == "Lisboa, e não o Porto [1, 9]."
    assert chat.prompts[0].startswith("Evidência:\n[1] Lisboa é a capital")


def test_citation_helpers():
    assert cited("A [2] B [1,3] C [0] D [12]", 3) == [1, 2, 3]
    assert strip_citations("Lisboa [1]. Porto [2, 3].", 3) == "Lisboa. Porto."


def test_events_follow_the_pipeline_steps_in_order():
    events = []
    engine().answer(Q, "geo", on_event=lambda name, data: events.append((name, data)))
    names = [n for n, _ in events]
    # BM25 returns the two passages that share a word with the question
    assert names == ["candidates"] + ["select"] * 2 + ["evidence", "sufficient", "draft", "grounded"]
    assert [d["p_select"] for n, d in events if n == "select"] == [0.9, 0.1]
    assert events[-1][1] == {"p": 0.9, "threshold": 0.5, "passed": True}


def test_collections_survive_a_restart(tmp_path):
    first = engine(data_dir=str(tmp_path))
    first.add_document("../fora", "Texto de teste.", "t.txt")
    second = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    assert sorted(second.collections) == ["../fora", "geo"]
    assert [p.text for p in second.collection("geo").search("capital Portugal", 1)] == [
        "Lisboa é a capital de Portugal desde o século XIII."]
    assert sorted(f.name for f in tmp_path.iterdir()) == ["..%2Ffora.json", "geo.json"]
    second.remove_document("geo", "geo.txt")
    second.drop_collection("../fora")
    third = Evidencia(Config(data_dir=str(tmp_path)), decision=StubDecision(), chat=StubChat(), sources={})
    assert list(third.collections) == ["geo"] and third.collection("geo").passages == []


def concept_embed(texts):
    """A fake embedding with two meanings: universities and wine."""
    rows = [[float(any(w in t.lower() for w in ("universidade", "ensino"))),
             float(any(w in t.lower() for w in ("vinho", "vinha"))), 0.01] for t in texts]
    v = np.array(rows, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_hybrid_search_finds_a_passage_that_shares_no_word():
    plain = Collection("geo")
    plain.add(DOC, "geo.txt")
    hybrid = Collection("geo", embed=concept_embed)
    hybrid.add(DOC, "geo.txt")
    assert plain.search("ensino superior antigo", 2) == []
    hits = hybrid.search("ensino superior antigo", 2)
    assert hits[0].text == "Coimbra tem uma das universidades mais antigas da Europa."
    assert hits[0].score == pytest.approx(1 / 61)
    assert hybrid.search("ensino superior antigo", 3)[:2] == hits


def test_hybrid_score_adds_both_ranks():
    hybrid = Collection("geo", embed=concept_embed)
    hybrid.add(DOC, "geo.txt")
    top = hybrid.search("vinho do Porto", 1)[0]
    assert top.text.startswith("O Porto fica no norte")
    assert top.score == pytest.approx(2 / 61)


def client(**cfg):
    return TestClient(create_app(engine=engine(**cfg)))


def test_sources_endpoint_lists_the_catalogue_with_licences():
    body = client().get("/v1/sources").json()["sources"]
    by_name = {s["name"]: s for s in body}
    assert set(by_name) == {"pt.wikipedia", "pt.wikisource", "pt.wiktionary", "wikidata", "dicionario-aberto",
                            "arquivo.pt", "gov.pt", "geoapi.pt", "ipma", "dados.gov.pt", "vies", "gleif",
                            "dns.pt", "dgeg"}
    assert by_name["pt.wikipedia"]["enabled"] and not by_name["wikidata"]["enabled"]
    assert by_name["arquivo.pt"]["commercial"] is False
    assert by_name["wikidata"]["licence"] == "CC0 1.0"


def test_api_answers_from_sources_without_a_collection():
    r = client().post("/v1/answer", json={"question": Q, "sources": ["pt.wikipedia"]})
    assert r.status_code == 200
    assert r.json()["evidence"][0]["url"] == "https://pt.wikipedia.org/wiki/Portugal"


def test_api_refuses_a_disabled_source_and_an_empty_target():
    c = client()
    r = c.post("/v1/answer", json={"question": Q, "sources": ["arquivo.pt"]})
    assert r.status_code == 422 and "not enabled" in r.json()["detail"]
    assert c.post("/v1/answer", json={"question": Q}).status_code == 422
    assert c.post("/v1/answer", json={"question": Q, "collection": "nada"}).status_code == 404


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_sends_each_step_then_the_answer():
    r = client().post("/v1/answer/stream", json={"question": Q, "collection": "geo"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [n for n, _ in events] == ["candidates"] + ["select"] * 2 + [
        "evidence", "sufficient", "draft", "grounded", "result"]
    assert events[-1][1]["answer"] == "Lisboa."


def test_stream_without_generation_ends_with_the_context():
    events = parse_sse(client().post("/v1/answer/stream",
                                     json={"question": Q, "collection": "geo", "generate": False}).text)
    assert "draft" not in [n for n, _ in events]
    assert events[-1][0] == "result" and events[-1][1]["abstain"] is False and "answer" not in events[-1][1]


def test_stream_reports_a_backend_failure_as_an_error_event():
    c = client(decision=StubDecision(down=True), fail_open=False)
    events = parse_sse(c.post("/v1/answer/stream", json={"question": Q, "collection": "geo"}).text)
    assert [n for n, _ in events] == ["candidates", "error"]
    assert events[-1][1] == {"status": 503, "detail": "decision backend unavailable"}


def test_demo_page_is_served_unless_turned_off():
    page = client().get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert client(ui=False).get("/").status_code == 404


def test_deleting_a_document_through_the_api_is_saved(tmp_path):
    c = client(data_dir=str(tmp_path))
    assert c.delete("/v1/collections/geo/documents/geo.txt").json()["removed"] == 4
    assert json.loads((tmp_path / "geo.json").read_text())["passages"] == []


def test_near_duplicates_are_dropped_before_the_select_gate():
    """Copies of one statement cost no decision call and cannot fill the evidence."""
    decision = StubDecision(keyword="vinho")
    e = Evidencia(Config(n_candidates=4, max_rounds=1, embedding_model="fake", dedupe_threshold=0.95),
                  decision=decision, chat=StubChat(), sources={})
    e.embed = concept_embed
    copies = "\n\n".join(f"A universidade de Coimbra é antiga, versão {i}." for i in range(3))
    e.add_document("geo", copies + "\n\nO vinho do Porto vem do Douro, junto à universidade.", "big.txt")
    ctx = e.context("universidade vinho", "geo")
    texts = [s.passage.text for s in ctx.candidates]
    assert len(texts) == 2 and "O vinho do Porto vem do Douro, junto à universidade." in texts
    assert ctx.sources[0]["duplicates"] == 2
    assert decision.count("select") == 2


def test_dedupe_needs_an_embedding_model():
    with pytest.raises(ValueError, match="needs embedding_model"):
        Config(dedupe_threshold=0.9)
    with pytest.raises(ValueError, match="dedupe_threshold"):
        Config(dedupe_threshold=0, embedding_model="m")


def test_events_say_when_the_sufficient_question_is_not_asked():
    events = []
    decision = StubDecision(sufficient=0.2)
    engine(decision, n_candidates=1, max_rounds=3).context(Q, "geo", on_event=lambda n, d: events.append((n, d)))
    sufficient = [d for n, d in events if n == "sufficient"]
    assert [d.get("skipped") for d in sufficient] == [None, "same evidence"]
    assert decision.count("sufficient") == 1


def test_context_names_the_step_that_stopped_it():
    e = engine(StubDecision(keyword="nada"))
    assert e.context(Q, "geo").as_dict()["abstained_at"] == "select"
    assert e.context("xyzzy", "geo").as_dict()["abstained_at"] == "retrieve"
    assert engine(StubDecision(sufficient=0.1)).context(Q, "geo").as_dict()["abstained_at"] == "sufficient"
    assert engine().context(Q, "geo").as_dict()["abstained_at"] is None


def test_a_backend_failure_mid_request_sends_the_ungated_candidates():
    events = []
    ctx = engine(StubDecision(down=True)).context(Q, "geo", on_event=lambda n, d: events.append((n, d)))
    assert events[-1][0] == "gates_unavailable"
    assert [p["id"] for p in events[-1][1]["candidates"]] == [s.passage.id for s in ctx.candidates]


def test_status_reports_retrieval_settings_and_an_injected_generator():
    body = client(n_candidates=3, top_k=2, max_rounds=4).get("/v1/status").json()
    assert body["retrieval"] == {"n_candidates": 3, "top_k": 2, "max_rounds": 4, "hybrid": False,
                                 "dedupe_threshold": None}
    assert body["generator_configured"] is True and body["sources"] == ["pt.wikipedia"]
