<!-- agent-hint: Each row quotes an exact message from the code: Config validation in evidencia/config.py, build_generator in evidencia/generators.py, the reasons in Evidencia.generate(), the HTTPException details in evidencia/api.py, and the ServiceError messages in evidencia/mcp_server.py. When a message changes, change its row. Strict STE: each fix is an instruction. -->

# Troubleshooting

Find the message that you see, then do the fix.

## The service does not start

| Message | Fix |
| --- | --- |
| `uv: command not found` or `command not found: uv` | Close the terminal and open a new one. If the message stays, install uv again: [Step-by-step setup](setup.md), step 2 |
| `No such file or directory: 'evidencia.toml'` | Go to the evidencia folder first: `cd ~/evidencia`. Or give the full path of the file to `--config` |
| `unknown config keys: [...]` | A setting name has a spelling mistake. Compare it with [Configuration](configuration.md) |
| `unknown generator_config keys for 'amalia': [...]` | A setting is below the `[generator_config]` line, so it belongs to that table. Move settings that are not chat model settings above the first `[...]` line |
| `generator 'anthropic' needs an API key: ...` | Put the key in `[generator_config]` as `key = "..."`, or set the variable that the message names |
| `generator 'openai' needs a model: set generator_config.model` | Add `model = "..."` under `[generator_config]`. Refer to [Chat models](generators.md) |
| `unknown sources [...]` | Use only the names in [Public sources](sources.md) |
| `address already in use` | Another program uses port 8080, often an evidencia that is still running. Stop it, or start with `--port 8081` |

## The browser page

| Symptom | Fix |
| --- | --- |
| The page does not open | Check that evidencia is running and that its last line says `Uvicorn running on http://127.0.0.1:8080`. Use the same address in the browser |
| The page asks for a key | The configuration has `api_key`. Type that value |
| **Remote sources** says "This server queries no remote sources." | Add `sources = [...]` to the configuration and start evidencia again |
| The first question takes minutes | The decision model downloads on the first question, about 3 GB. Wait |

## Errors in an answer

| Message | Cause and fix |
| --- | --- |
| `cannot connect to the chat model server; is it running?` | The chat model is not running, or `api_url` is wrong. Start AMALIA or your chat server. To see the checked evidence without an answer, click **Ask without generating** |
| `the chat model provider refused the API key` | The key is wrong or expired, or it is for a different provider. Make a new key |
| `the chat model provider does not know the model or the address` | Correct `model` or `api_url` |
| `the chat model provider is rate limiting or the account has no credit` | Wait a minute, or add credit to the account. Free models on OpenRouter often give this |
| `decision backend unavailable` | The decision model cannot answer. Read the service log. With `fail_open = true`, answers then come without gates |
| `sources not enabled on this service: [...]` | Add the source to `sources` in the configuration, or do not tick it |
| `no collection 'x'` | Add a document to that collection first |
| `name a collection, at least one source, or both` | Choose a collection or tick a source |

## The answer is "Não tenho informação suficiente"

This is not an error. evidencia did not find evidence that supports an answer, so it did not answer. The field `abstained_at` gives the check that stopped it:

| `abstained_at` | What to try |
| --- | --- |
| `retrieve` | No passage shares a word with the question. Use the words that the documents use, or turn on hybrid retrieval (`embedding_model`) |
| `select` | Passages were found, but none helps. Add a document that answers the question, or tick a public source |
| `sufficient` | The passages help, but they are not enough. Add more documents, or ask a narrower question |
| `grounded` | The chat model wrote an answer that the evidence does not support. Ask again, or use a stronger chat model |

## Agents

| Message | Fix |
| --- | --- |
| `cannot reach the evidencia service at ...; is it running?` | Start evidencia (setup step 6). The agent uses the running service |
| `evidencia returned 401: invalid or missing bearer token` | Set `EVIDENCIA_API_KEY` in the agent's MCP configuration to the service's `api_key` |
| The tools do not show in Claude Desktop | Check that the path in `command` is the full path from `realpath`. Quit Claude Desktop fully and open it again |

## AMALIA

| Message | Fix |
| --- | --- |
| `cudaMalloc failed: out of memory` | Remove `--n-gpu-layers` from the command, so that llama.cpp fits the model to the free memory. Keep `--ctx-size 4096`. Close other programs that use the GPU |
| `unresolvable CDI devices nvidia.com/gpu=all` (Podman) or `could not select device driver` (Docker) | The NVIDIA Container Toolkit is not installed. Install it, or use the image `ghcr.io/ggml-org/llama.cpp:server` without `--device` (slower) |
| The decision model fails while AMALIA runs on the same GPU | Set `device = "cpu"` in the `[decision]` table |

---
[← Evaluation](evaluation.md) · [Home](index.md)
