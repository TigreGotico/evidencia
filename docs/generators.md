<!-- agent-hint: Backed by evidencia/generators.py (PRESETS, OpenAICompatible, Claude, build_generator) and GeneratorUnavailable in evidencia/pipeline.py. The preset table must match PRESETS exactly. The 502 messages must match the reasons in Evidencia.generate(). AMALIA facts (licence, sizes) come from the model card of duarteocarmo/AMALIA-9B-0626-DPO-GGUF; the OpenCode free-tier statement was read from a live 403 reply. -->

# Chat models

The chat model writes the answer from the evidence that the gates kept. The grounded gate then checks that answer. The chat model never sees passages that the select gate dropped.

The default chat model is AMALIA-9B, a European Portuguese model, served by llama.cpp on your own machine. You can use a hosted model instead, with an API key.

## Presets

Set `generator` to a preset name. Each preset knows the address of its provider and where to find its API key.

| `generator` | Provider | Address | Key variable | Default model |
| --- | --- | --- | --- | --- |
| `amalia` | llama.cpp serving AMALIA-9B, on your machine | `http://127.0.0.1:8000/v1` | none | `amalia` |
| `llama.cpp` | llama.cpp serving any GGUF model | `http://127.0.0.1:8000/v1` | none | `local-model` |
| `ollama` | Ollama | `http://127.0.0.1:11434/v1` | none | none: set `model` |
| `openai` | OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` | none: set `model` |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | none: set `model` |
| `opencode` | OpenCode Zen | `https://opencode.ai/zen/v1` | `OPENCODE_API_KEY` | none: set `model` |
| `anthropic` | Anthropic (Claude) | `https://api.anthropic.com/v1/messages` | `ANTHROPIC_API_KEY` | `claude-opus-5-5` |

All presets except `anthropic` use the OpenAI chat completions format. Any other server that uses that format works with the `llama.cpp` preset and a different `api_url`.

Any other `generator` value is the name of a chat engine plugin, loaded by name.

## Settings

Put these in the `[generator_config]` table:

| Key | Default | Meaning |
| --- | --- | --- |
| `model` | the preset's model | Model name sent to the provider |
| `key` | none | API key. The environment is a safer place for it, see below |
| `api_url` | the preset's address | Address of the server, ending in `/v1`. Not used by `anthropic` |
| `max_tokens` | `512`, or `1024` for `anthropic` | Longest answer, in tokens |
| `temperature` | `0.2` | Sampling temperature. Not for `anthropic` |
| `effort` | `low` | `anthropic` only: how much the model thinks before it answers |
| `timeout` | `120` | Seconds to wait for an answer |

evidencia reads the API key from the first of these that is set:

1. The environment variable `EVIDENCIA_GENERATOR_KEY`.
2. `key` in `[generator_config]`.
3. The provider's own variable, from the table above, for example `OPENAI_API_KEY`.

A preset that needs a key and has none stops the service when it starts. So does a preset that needs a model name and has none. The error names the setting to add.

`EVIDENCIA_GENERATOR` replaces `generator`, `EVIDENCIA_GENERATOR_MODEL` replaces `model`, and `EVIDENCIA_GENERATOR_URL` replaces `api_url`. These variables apply to the presets only, not to a chat engine plugin.

## AMALIA on your machine

[AMALIA-9B](https://huggingface.co/duarteocarmo/AMALIA-9B-0626-DPO-GGUF) is a 9-billion-parameter model trained for European Portuguese. Its licence is Apache-2.0. The GGUF files run with llama.cpp:

| File | Size | Use |
| --- | --- | --- |
| `Q4_K_M` | 5.2 GB | The default. On a GPU with 6 GB, part of the model runs on the CPU |
| `Q3_K_M` | 4.2 GB | Less memory, lower quality |
| `Q5_K_M` | 6.1 GB | Better quality. Fits fully on a GPU with 8 GB or more |

Start llama.cpp's server with the container image. It downloads the model on the first start:

```bash
podman run --rm -p 127.0.0.1:8000:8000 --device nvidia.com/gpu=all \
  -v llama-models:/models -e LLAMA_CACHE=/models \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  -hf duarteocarmo/AMALIA-9B-0626-DPO-GGUF:Q4_K_M --alias amalia \
  --host 0.0.0.0 --port 8000 --ctx-size 4096
```

- llama.cpp puts as many layers on the GPU as its free memory holds, and runs the other layers on the CPU. Do not give `--n-gpu-layers`: with a fixed value, the server stops with an out-of-memory error when the layers do not fit.
- `--ctx-size 4096` is enough for evidencia: the evidence is at most `top_k` passages of 1500 characters. A larger context takes GPU memory from the model layers.
- Without an NVIDIA GPU, use the image `ghcr.io/ggml-org/llama.cpp:server`, and remove `--device`. Answers then take longer.
- `--device nvidia.com/gpu=all` needs the NVIDIA Container Toolkit. With Docker, use `--gpus all`.

Then use the default configuration, or say it explicitly:

```toml
generator = "amalia"
```

[Deployment](deployment.md) runs AMALIA and evidencia together with compose.

## Claude

```toml
generator = "anthropic"
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Get a key at <https://console.anthropic.com>. evidencia calls the Messages API with low effort, because the answer is a few sentences from given evidence. When Claude declines a request, the API answers it again with a fallback model. If all models decline, evidencia returns 502.

To use a different Claude model, set `model`, for example `claude-sonnet-5-5` or `claude-haiku-5-5`.

## OpenAI

```toml
generator = "openai"

[generator_config]
model = "the model name from your OpenAI account"
```

```bash
export OPENAI_API_KEY=sk-...
```

## OpenRouter

OpenRouter gives one key for models from many providers. Choose a model id at <https://openrouter.ai/models>.

```toml
generator = "openrouter"

[generator_config]
model = "anthropic/claude-opus-5.5"
```

```bash
export OPENROUTER_API_KEY=sk-or-...
```

Models with `:free` in the id cost nothing, but they have low rate limits and often return HTTP 429. Some of them think first and return an empty answer when `max_tokens` is too small. evidencia then returns 502.

## OpenCode Zen

```toml
generator = "opencode"

[generator_config]
model = "a model id from https://opencode.ai/zen/v1/models"
```

```bash
export OPENCODE_API_KEY=...
```

The OpenCode free models work only inside the OpenCode app. Outside it, the API refuses them with HTTP 403, and evidencia returns 502. Use a paid Zen key.

## Ollama

```toml
generator = "ollama"

[generator_config]
model = "the name from 'ollama list'"
```

## When the chat model fails

`POST /v1/answer` returns 502, and `detail` says what to fix:

| `detail` | Cause |
| --- | --- |
| `cannot connect to the chat model server; is it running?` | Nothing answers at `api_url`. Start the server, or correct the address |
| `the chat model provider refused the API key` | HTTP 401 or 403. The key is wrong, expired, or for a different provider |
| `the chat model provider does not know the model or the address` | HTTP 404. Correct `model` or `api_url` |
| `the chat model provider is rate limiting or the account has no credit` | HTTP 429. Wait, or add credit |
| `the chat model provider returned HTTP <status>` | Another error from the provider |
| `generator unavailable` | Another failure, for example an empty answer. The service log has the cause |

The gates still work when the chat model fails. `POST /v1/context` returns the checked evidence without an answer.

---
[← Decision models](decision-models.md) · [Home](index.md) · [Documents →](documents.md)
