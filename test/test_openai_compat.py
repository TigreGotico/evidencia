"""The OpenAI-compatible routes, driven by the official openai SDK."""
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


def ask(client, content=Q, model="evidencia/geo", **kw):
    return client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}], **kw)


def test_a_chat_completion_is_the_gated_answer_with_its_evidence():
    client, _ = sdk()
    reply = ask(client, temperature=0.2)
    assert reply.choices[0].message.content == "Lisboa."
    assert reply.choices[0].finish_reason == "stop"
    assert reply.model == "evidencia/geo" and reply.id.startswith("chatcmpl-")
    side = reply.model_extra["evidencia"]
    assert side["gates"] == "applied" and side["abstained"] is False and side["abstained_at"] is None
    assert [p["text"] for p in side["evidence"]] == ["Lisboa é a capital de Portugal desde o século XIII."]
    assert side["grounded"]["passed"] is True


def test_an_abstention_answers_with_the_abstain_text_and_generates_nothing():
    chat = StubChat()
    client, engine = sdk(decision=StubDecision(sufficient=0.1), chat=chat)
    reply = ask(client)
    assert reply.choices[0].message.content == engine.gates.locale["abstain"]
    assert reply.model_extra["evidencia"]["abstained"] is True
    assert reply.model_extra["evidencia"]["abstained_at"] == "sufficient"
    assert chat.prompts == []


def test_an_ungrounded_draft_is_not_sent():
    client, engine = sdk(decision=StubDecision(grounded=0.1), chat=StubChat("O Porto."))
    reply = ask(client)
    assert reply.choices[0].message.content == engine.gates.locale["abstain"]
    assert reply.model_extra["evidencia"]["abstained_at"] == "grounded"


def test_the_last_user_message_is_the_question():
    chat = StubChat()
    client, _ = sdk(decision=StubDecision(keyword="Porto"), chat=chat)
    reply = client.chat.completions.create(model="evidencia/geo", messages=[
        {"role": "system", "content": "És um assistente."},
        {"role": "user", "content": "Qual é a capital?"},
        {"role": "assistant", "content": "Lisboa."},
        {"role": "user", "content": [{"type": "text", "text": "E o Porto"}, {"type": "text", "text": "fica onde?"}]}])
    assert reply.model_extra["evidencia"]["evidence"][0]["text"].startswith("O Porto fica no norte")
    assert "E o Porto fica onde?" in chat.prompts[-1]


def test_the_model_names_the_collection():
    client, _ = sdk()
    with pytest.raises(openai.NotFoundError, match="no model 'gpt-4o'"):
        ask(client, model="gpt-4o")
    with pytest.raises(openai.NotFoundError, match="no collection 'nada'"):
        ask(client, model="evidencia/nada")
    with pytest.raises(openai.UnprocessableEntityError, match="name a collection"):
        ask(client, model="evidencia")


def test_request_settings_ride_in_the_extra_body():
    client, _ = sdk()
    reply = ask(client, extra_body={"select_threshold": 0.95})
    assert reply.model_extra["evidencia"]["abstained_at"] == "select"
    with pytest.raises(openai.UnprocessableEntityError):
        ask(client, extra_body={"select_threshold": 2.0})


def test_a_wrong_key_is_refused():
    client, _ = sdk(key="errada")
    with pytest.raises(openai.AuthenticationError):
        ask(client)


def test_a_stream_sends_the_checked_answer_then_the_evidence():
    client, _ = sdk()
    chunks = list(ask(client, stream=True))
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "Lisboa."
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert chunks[-1].model_extra["evidencia"]["evidence"][0]["source"] == "geo.txt"
    assert len({c.id for c in chunks}) == 1


def test_a_stream_reports_a_backend_failure_as_an_error():
    client, _ = sdk(decision=StubDecision(down=True), fail_open=False)
    with pytest.raises(openai.APIError, match="decision backend unavailable"):
        list(ask(client, stream=True))


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


def test_a_fail_open_chat_completion_says_its_answer_is_unchecked():
    client, _ = sdk(decision=StubDecision(down=True), fail_open=True)
    reply = ask(client)
    assert reply.choices[0].message.content == "Lisboa."
    assert reply.model_extra["evidencia"]["gates"] == "unavailable"


def test_a_vector_store_search_never_returns_unchecked_passages():
    client, _ = sdk(decision=StubDecision(down=True), fail_open=True)
    with pytest.raises(openai.InternalServerError, match="decision backend unavailable"):
        client.vector_stores.search("geo", query=Q)


def test_an_unknown_vector_store_is_not_found():
    client, _ = sdk()
    with pytest.raises(openai.NotFoundError, match="no collection 'nada'"):
        client.vector_stores.search("nada", query=Q)
