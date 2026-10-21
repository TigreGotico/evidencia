from json import dumps

import pytest
import requests
from fastapi.testclient import TestClient

from conftest import DOC, StubDecision
from evidencia import Config, Evidencia
from evidencia.api import create_app
from evidencia.generators import Claude, build_generator
from ovos_plugin_manager.templates.agents import AgentMessage, MessageRole

Q = "Qual é a capital de Portugal?"


class Recorder:
    def __init__(self, reply, status=200):
        self.reply, self.status, self.calls = reply, status, []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        resp = requests.Response()
        resp.status_code = self.status
        resp._content = dumps(self.reply).encode()
        resp.url = url
        return resp


OPENAI_REPLY = {"choices": [{"message": {"role": "assistant", "content": "Lisboa [1]."}}]}
CLAUDE_REPLY = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""},
                                                       {"type": "text", "text": "Lisboa [1]."}]}


def test_openai_compatible_sends_the_system_prompt_and_a_bearer_key(monkeypatch):
    post = Recorder(OPENAI_REPLY)
    monkeypatch.setattr(requests, "post", post)
    gen = build_generator("openrouter", {"model": "qwen/qwen3.8-flash"}, "Responde só com a evidência.",
                          env={"OPENROUTER_API_KEY": "sk-or-1"})
    reply = gen.continue_chat([AgentMessage(MessageRole.USER, "Pergunta")])
    assert reply.content == "Lisboa [1]."
    call = post.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"] == {"authorization": "Bearer sk-or-1"}
    assert call["json"]["model"] == "qwen/qwen3.8-flash"
    assert call["json"]["messages"] == [{"role": "system", "content": "Responde só com a evidência."},
                                        {"role": "user", "content": "Pergunta"}]


def test_a_local_server_needs_no_key(monkeypatch):
    post = Recorder(OPENAI_REPLY)
    monkeypatch.setattr(requests, "post", post)
    gen = build_generator("amalia", {}, "", env={})
    gen.continue_chat([AgentMessage(MessageRole.USER, "Pergunta")])
    assert post.calls[0]["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert post.calls[0]["headers"] == {}
    assert post.calls[0]["json"]["model"] == "amalia"


def test_claude_uses_the_messages_api_with_fallbacks(monkeypatch):
    post = Recorder(CLAUDE_REPLY)
    monkeypatch.setattr(requests, "post", post)
    gen = build_generator("anthropic", {}, "Sê breve.", env={"ANTHROPIC_API_KEY": "sk-ant-1"})
    assert isinstance(gen, Claude)
    assert gen.continue_chat([AgentMessage(MessageRole.USER, "Pergunta")]).content == "Lisboa [1]."
    call = post.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"] == {"x-api-key": "sk-ant-1", "anthropic-version": "2023-06-01",
                               "anthropic-beta": "server-side-fallback-2026-07-01"}
    assert call["json"] == {"model": "claude-opus-5-5", "max_tokens": 1024, "system": "Sê breve.",
                            "output_config": {"effort": "low"}, "fallbacks": "default",
                            "messages": [{"role": "user", "content": "Pergunta"}]}


def test_claude_refusal_is_an_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(requests, "post", Recorder({"stop_reason": "refusal", "content": []}))
    gen = Claude("claude-opus-5-5", "k")
    with pytest.raises(RuntimeError, match="declined"):
        gen.continue_chat([AgentMessage(MessageRole.USER, "Pergunta")])


def test_key_order_is_evidencia_variable_then_config_then_provider_variable():
    both = {"EVIDENCIA_GENERATOR_KEY": "from-evidencia", "OPENAI_API_KEY": "from-openai"}
    assert build_generator("openai", {"model": "m", "key": "from-config"}, "", env=both).key == "from-evidencia"
    provider = {"OPENAI_API_KEY": "from-openai"}
    assert build_generator("openai", {"model": "m", "key": "from-config"}, "", env=provider).key == "from-config"
    assert build_generator("openai", {"model": "m"}, "", env=provider).key == "from-openai"


def test_evidencia_variables_replace_the_file_settings():
    env = {"EVIDENCIA_GENERATOR_MODEL": "m2", "EVIDENCIA_GENERATOR_URL": "http://gpu:9000/v1"}
    gen = build_generator("llama.cpp", {"model": "m1", "api_url": "http://x/v1"}, "", env=env)
    assert (gen.model, gen.url) == ("m2", "http://gpu:9000/v1/chat/completions")


def test_a_hosted_provider_without_a_key_or_model_stops_at_start():
    with pytest.raises(ValueError, match="needs an API key: .*OPENAI_API_KEY"):
        build_generator("openai", {"model": "m"}, "", env={})
    with pytest.raises(ValueError, match="needs a model"):
        build_generator("openai", {"key": "k"}, "", env={})
    with pytest.raises(ValueError, match="unknown generator_config keys"):
        build_generator("amalia", {"api_key": "k"}, "", env={})
    with pytest.raises(ValueError, match="needs an API key"):
        Evidencia(Config(generator="anthropic"), decision=StubDecision(), sources={})


def answer_with(monkeypatch, recorder_or_exc):
    if isinstance(recorder_or_exc, Exception):
        def post(*a, **k):
            raise recorder_or_exc
        monkeypatch.setattr(requests, "post", post)
    else:
        monkeypatch.setattr(requests, "post", recorder_or_exc)
    engine = Evidencia(Config(generator="amalia"), decision=StubDecision(), sources={})
    engine.add_document("geo", DOC, "geo.txt")
    return TestClient(create_app(engine=engine)).post("/v1/answer", json={"collection": "geo", "question": Q})


def test_the_default_generator_answers_through_llama_server(monkeypatch):
    r = answer_with(monkeypatch, Recorder(OPENAI_REPLY))
    assert r.status_code == 200 and r.json()["answer"] == "Lisboa [1]." and r.json()["cited"] == [1]


def test_a_stopped_chat_server_and_a_bad_key_say_what_to_fix(monkeypatch):
    r = answer_with(monkeypatch, requests.ConnectionError("refused"))
    assert r.status_code == 502
    assert r.json() == {"detail": "cannot connect to the chat model server; is it running?"}
    r = answer_with(monkeypatch, Recorder({"error": "bad key"}, status=401))
    assert r.json() == {"detail": "the chat model provider refused the API key"}
