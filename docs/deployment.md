<!-- agent-hint: Backed by Containerfile, compose.yml and evidencia.example.toml. The podman commands were run with rootless podman; keep the --userns=keep-id note, because without it the container user cannot read a mounted host cache. The AMALIA memory figures were measured on an RTX 3060 6 GB (knowledge/wiki/audits/kev-rag/evidencia/live-v2/). If you change the image's installed extras, update the "What the image holds" list. -->

# Deployment

evidencia runs as one process. With `data_dir`, it keeps its collections in files. Run one instance for each `data_dir`, because two instances do not see each other's changes.

## Deployment options

Choose how evidencia runs, then how its clients reach it. The options in each table combine freely.

How the service runs:

| Option | Use it when | Refer to |
| --- | --- | --- |
| A local install, `evidencia serve` | You try evidencia, or run it on one machine | [Getting started](getting-started.md) |
| The container image | You deploy on a server | [Container image](#container-image) |
| Compose, with AMALIA and FlareSolverr | You want the chat model and the challenge solver next to evidencia | [Run with compose](#run-with-compose) |
| A Python library, with no service | Your program calls evidencia in its own process | [Python](python.md) |

Where its models and data live:

| Part | Options | Refer to |
| --- | --- | --- |
| Decision model | In the process, on the CPU or a GPU (`local`), or on another server (`http`). One instance with a GPU can serve the model to the others | [Decision models](decision-models.md#one-instance-as-the-backend-of-another) |
| Chat model | AMALIA on llama.cpp, Ollama, or a hosted provider | [Chat models](generators.md) |
| Embeddings | None (BM25 only), the built-in model2vec model, or an embeddings plugin | [Documents](documents.md#retrieval) |
| Vectors | In memory with files in `data_dir`, or a chromadb or qdrant database, on disk or on its own server | [Documents](documents.md#vector-databases) |

How clients reach it:

| Client | Path | Refer to |
| --- | --- | --- |
| A program, with HTTP and JSON | The evidencia HTTP API | [HTTP API](api.md) |
| A person, in a browser | The demo page at `/` | [Demo page](demo.md) |
| An agent: Claude Desktop, Claude Code, OpenCode | `evidencia mcp` | [Agents](agents.md) |
| A client of OpenAI, Ollama, Anthropic, Gemini, Cohere, TGI or Bedrock | ovos-persona-server with the evidencia chat plugin | [Vendor APIs](vendor-apis.md#chat-through-ovos-persona-server) |
| An OpenAI RAG tool | The OpenAI vector store search of evidencia | [Vendor APIs](vendor-apis.md#vector-store-search) |
| An OVOS persona or voice assistant | The evidencia plugins | [OVOS personas](#ovos-personas) |

## Container image

Build the image from the repository root:

```bash
podman build -t evidencia -f Containerfile .
```

The image installs PyTorch for the CPU. For an NVIDIA GPU, pass a CUDA wheel index:

```bash
podman build -t evidencia -f Containerfile --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 .
```

What the image holds:

- evidencia with the `local` and `hybrid` extras, so the decision model and the embedding model run inside the container.
- The built-in chat model presets. They call the chat model over HTTP, so the image needs no chat engine package.
- A non-root user with uid 1000, the models directory `/models` (`HF_HOME`), and the collections directory `/collections`. Both are volumes.

The service in the image listens on port 8080.

## Run with compose

`compose.yml` starts three services:

| Service | Does |
| --- | --- |
| `evidencia` | Builds the image, mounts `evidencia.toml` from the current directory, and keeps models and collections in named volumes |
| `amalia` | llama.cpp with CUDA, serving AMALIA-9B Q4_K_M as the model `amalia` on port 8000 inside the compose network |
| `flaresolverr` | FlareSolverr on port 8191 inside the compose network. evidencia sends to it a request that a public source refuses. Refer to [Public sources](sources.md#sites-that-refuse-a-client) |

```bash
cp evidencia.example.toml evidencia.toml
podman-compose up
```

The example file sends the chat requests to `http://amalia:8000/v1`, keeps the collections in `/collections`, and runs the decision model on the CPU. The first start downloads AMALIA (5.2 GB) to the `llama-models` volume. The first question downloads the decision model (about 3 GB) and the embedding model to the `models` volume. The two services use separate volumes, because llama.cpp runs as root and evidencia runs as uid 1000. The `evidencia` service starts when AMALIA reports healthy, and compose checks `/health` of the `evidencia` service every 30 seconds.

The `amalia` service needs an NVIDIA GPU and the NVIDIA Container Toolkit. With Docker, replace `devices` with a GPU reservation (`deploy.resources.reservations.devices`).

### GPU memory

AMALIA-9B Q4_K_M with a 4096-token context uses about 4.8 GB on a 6 GB GPU. Do not set `--n-gpu-layers`: without it, llama.cpp puts as many layers on the GPU as the free memory holds. A fixed `--n-gpu-layers 99` with a context of 8192 runs out of memory on 6 GB.

The decision model also needs GPU memory. When AMALIA uses a 6 GB GPU, keep `device = "cpu"` for the decision model. On the CPU, the first question takes about 11 seconds to select, because the model loads. After that, each gate took 0.5 to 3 seconds in the measured runs. Longer evidence from public sources takes longer.

### A hosted chat model instead

To use Claude or another hosted model, remove the `amalia` service and the `depends_on` lines of the `evidencia` service, change `generator` in `evidencia.toml`, and give the key in the environment:

```toml
generator = "anthropic"
```

```bash
EVIDENCIA_GENERATOR_KEY=sk-ant-... podman-compose up evidencia
```

Refer to [Chat models](generators.md).

## Run with a host model cache

To use models that the host has already downloaded, mount the host cache read-only on `/models`, and set `HF_HUB_OFFLINE=1` so that the container downloads nothing. With rootless podman, add `--userns=keep-id`, so that the container user can read the cache:

```bash
podman run --rm --userns=keep-id -p 127.0.0.1:8080:8080 \
  -v "$HOME/.cache/huggingface:/models:ro" -e HF_HUB_OFFLINE=1 \
  -v evidencia-collections:/collections -e EVIDENCIA_DATA_DIR=/collections \
  -e EVIDENCIA_DECISION_BACKEND=local -e EVIDENCIA_DECISION_DEVICE=cpu \
  -e EVIDENCIA_GENERATOR_URL=http://host.containers.internal:8000/v1 \
  evidencia
```

The model must be in the host cache already. Without `--userns=keep-id`, the model cannot load. Then gate questions fail, and after the first one `/health` reports `degraded`.

Without a configuration file, the image uses the defaults. The default decision backend is `http`, so set `EVIDENCIA_DECISION_BACKEND=local` or mount a configuration file. `host.containers.internal` is the name that Podman gives the host.

## OVOS personas

The `ovos-evidencia-plugin` package gives an OVOS persona three plugins. Each one calls an evidencia service over HTTP, so the persona needs no model of evidencia.

| Plugin | Persona key | Does |
| --- | --- | --- |
| `ovos-evidencia-chat-plugin` | `handlers` | Answers with the gated answer. On an abstention it gives no answer, so the persona tries its next handler |
| `ovos-evidencia-retrieval-plugin` | `handlers` | Returns the evidence passages, with their select probability. On an abstention it returns no passages |
| `ovos-evidencia-memory-plugin` | `memory_module` | Adds the passages that pass the gates to the context of another chat engine. On an abstention it adds the `no_evidence` note, so the chat engine can say that it does not know. `inject_mode` places the evidence |

The three plugins take the connection and question settings of the chat plugin in [Vendor APIs](vendor-apis.md#chat-through-ovos-persona-server). The chat and retrieval plugins answer only a question in the language of the evidencia service, so the persona must run in that language.

ovos-persona-server serves such a persona to the vendor APIs. An OVOS voice assistant uses it as any other persona.

## Authentication

Set `api_key`, or `EVIDENCIA_API_KEY`, before other hosts can reach the port. Then every `/v1` call must send `Authorization: Bearer <key>`. `GET /`, `GET /health` and the OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) stay open. When a call returns 401, the demo page asks for the key.

```bash
EVIDENCIA_API_KEY=change-me podman-compose up
```

The service has no TLS. Put it behind a reverse proxy that terminates TLS when the traffic leaves the host.

## Health checks

`GET /health` needs no token. It returns `ok` or `degraded`, and it checks the decision backend at most once every 10 seconds. Use it for container and load-balancer health checks. `GET /v1/status` gives more detail and needs the token.

## Logs

The service writes its log to standard error and the HTTP access log to standard output. When the decision backend, the chat model or a public source fails, the log line holds the cause, and the client gets a fixed message.

---
[← Configuration](configuration.md) · [Home](index.md) · [Python →](python.md)
