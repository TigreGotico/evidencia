"""The OpenAI vector store search route, driven by the official openai SDK."""
import openai
import pytest
from fastapi.testclient import TestClient

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia.api import create_app

Q = "Qual é a capital de Portugal?"
EVERY = "Lisboa, Porto, Belém e Coimbra"


def sdk(decision=None, chat=None, key="k", **cfg):
    engine = Evidencia(Config(api_key="k", **cfg), decision=decision or StubDecision(), chat=chat or StubChat())
    engine.add_document("geo", DOC, "geo.txt")
    http = TestClient(create_app(engine=engine))
    return openai.OpenAI(base_url="http://testserver/v1", api_key=key, http_client=http, max_retries=0), engine


def test_a_vector_store_search_returns_the_selected_passages():
    client, _ = sdk()
    page = client.vector_stores.search("geo", query=Q, max_num_results=5)
    assert [(r.file_id, r.filename, r.score) for r in page.data] == [("geo.txt", "geo.txt", 0.9)]
    assert page.data[0].content[0].text == "Lisboa é a capital de Portugal desde o século XIII."
    assert page.data[0].attributes["origin"] == "geo"


def test_a_vector_store_search_returns_nothing_when_the_evidence_does_not_suffice():
    client, _ = sdk(decision=StubDecision(sufficient=0.1))
    assert client.vector_stores.search("geo", query=Q).data == []


def test_a_vector_store_search_caps_the_results():
    client, _ = sdk(decision=StubDecision(keyword=" "))
    wide = {"top_k": 4, "n_candidates": 4}
    assert len(client.vector_stores.search("geo", query=EVERY, max_num_results=4, extra_body=wide).data) == 4
    assert len(client.vector_stores.search("geo", query=EVERY, max_num_results=1, extra_body=wide).data) == 1


def test_a_vector_store_search_never_returns_unchecked_passages():
    client, _ = sdk(decision=StubDecision(down=True), fail_open=True)
    with pytest.raises(openai.InternalServerError, match="decision backend unavailable"):
        client.vector_stores.search("geo", query=Q)


def test_an_unknown_vector_store_is_not_found():
    client, _ = sdk()
    with pytest.raises(openai.NotFoundError, match="no collection 'nada'"):
        client.vector_stores.search("nada", query=Q)


def test_a_vector_store_search_needs_the_key():
    client, _ = sdk(key="errada")
    with pytest.raises(openai.AuthenticationError):
        client.vector_stores.search("geo", query=Q)
