"""Chat models that write the answer: any OpenAI-compatible server, or Claude's Messages API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import requests


@dataclass(frozen=True)
class Preset:
    provider: str               # "openai" (any OpenAI-compatible server) or "anthropic"
    api_url: str = ""
    model: str = ""             # empty: the configuration must name the model
    key_env: str = ""           # environment variable that holds the API key


PRESETS = {
    # llama.cpp's llama-server serving AMALIA-9B, a European Portuguese model; see docs/generators.md
    "amalia": Preset("openai", "http://127.0.0.1:8000/v1", "amalia"),
    "llama.cpp": Preset("openai", "http://127.0.0.1:8000/v1", "local-model"),
    "ollama": Preset("openai", "http://127.0.0.1:11434/v1"),
    "openai": Preset("openai", "https://api.openai.com/v1", key_env="OPENAI_API_KEY"),
    "openrouter": Preset("openai", "https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY"),
    "opencode": Preset("openai", "https://opencode.ai/zen/v1", key_env="OPENCODE_API_KEY"),
    "anthropic": Preset("anthropic", model="claude-opus-5-5", key_env="ANTHROPIC_API_KEY"),
}


class OpenAICompatible:
    """POST {api_url}/chat/completions. The reply has a ``content`` string, like a chat engine plugin's."""

    def __init__(self, api_url: str, model: str, key: str | None = None, system_prompt: str = "",
                 timeout: float = 120.0, max_tokens: int = 512, temperature: float = 0.2):
        self.url = api_url.rstrip("/") + "/chat/completions"
        self.model, self.key, self.system_prompt = model, key, system_prompt
        self.timeout, self.max_tokens, self.temperature = timeout, max_tokens, temperature

    def continue_chat(self, messages, session_id="default", lang=None, units=None, tools=None):
        body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                "messages": [{"role": "system", "content": self.system_prompt}] * bool(self.system_prompt)
                + [{"role": str(getattr(m.role, "value", m.role)), "content": m.content} for m in messages]}
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        resp = requests.post(self.url, json=body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return SimpleNamespace(content=resp.json()["choices"][0]["message"].get("content") or "")


class Claude:
    """Claude through the Messages API, at low effort: the answer is a few sentences from given evidence."""
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, key: str, system_prompt: str = "", timeout: float = 120.0,
                 max_tokens: int = 1024, effort: str = "low"):
        self.model, self.key, self.system_prompt = model, key, system_prompt
        self.timeout, self.max_tokens, self.effort = timeout, max_tokens, effort

    def continue_chat(self, messages, session_id="default", lang=None, units=None, tools=None):
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": self.system_prompt,
                "output_config": {"effort": self.effort},
                # a declined request is answered again by the fallback model the API picks for its category
                "fallbacks": "default",
                "messages": [{"role": "user" if str(getattr(m.role, "value", m.role)) == "user" else "assistant",
                              "content": m.content} for m in messages]}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                   "anthropic-beta": "server-side-fallback-2026-07-01"}
        resp = requests.post(self.url, json=body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("the model declined the request")
        return SimpleNamespace(content="".join(b["text"] for b in data["content"] if b["type"] == "text"))


def build_generator(name: str, config: dict[str, Any], system_prompt: str, env=os.environ):
    """A built-in generator from ``PRESETS``, configured by ``generator_config`` and the environment.

    An ``EVIDENCIA_GENERATOR_*`` variable replaces the matching setting, as other ``EVIDENCIA_*`` variables do.
    The key comes from ``EVIDENCIA_GENERATOR_KEY``, then ``key``, then the provider's own variable, such as
    ``OPENAI_API_KEY``.
    """
    preset = PRESETS[name]
    unknown = set(config) - {"api_url", "model", "key", "timeout", "max_tokens", "temperature", "effort"}
    if unknown:
        raise ValueError(f"unknown generator_config keys for {name!r}: {sorted(unknown)}. In the TOML file, a "
                         f"setting below the [generator_config] line belongs to it: move other settings above it")
    model = env.get("EVIDENCIA_GENERATOR_MODEL") or config.get("model") or preset.model
    if not model:
        raise ValueError(f"generator {name!r} needs a model: set generator_config.model")
    key = env.get("EVIDENCIA_GENERATOR_KEY") or config.get("key") or (env.get(preset.key_env) if preset.key_env else None)
    options = {k: config[k] for k in ("timeout", "max_tokens") if k in config}
    if preset.key_env and not key:
        raise ValueError(f"generator {name!r} needs an API key: set generator_config.key, "
                         f"EVIDENCIA_GENERATOR_KEY or {preset.key_env}")
    if preset.provider == "anthropic":
        return Claude(model, key, system_prompt, effort=config.get("effort", "low"), **options)
    if "temperature" in config:
        options["temperature"] = config["temperature"]
    return OpenAICompatible(env.get("EVIDENCIA_GENERATOR_URL") or config.get("api_url") or preset.api_url, model, key,
                            system_prompt, **options)
