<!-- agent-hint: Backed by Containerfile, compose.yml and evidencia.example.toml. The commands here were run with rootless podman; keep the --userns=keep-id note, because without it the container user cannot read a mounted host cache. If you change the image's installed extras, update the "What the image holds" list. -->

# Deployment

evidencia runs as one process. It keeps its collections in memory, so run one instance per set of collections.

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

- evidencia with the `local` extra, so the decision model runs inside the container.
- The OpenAI-compatible chat engine. The GGUF engine is not in the image, because it needs a C compiler to build.
- A non-root user with uid 1000, and the models directory `/models` (`HF_HOME`).

The service in the image listens on port 8080.

## Run with compose

`compose.yml` builds the image, mounts `evidencia.toml` from the current directory, and keeps the models in a named volume. Run it with `podman-compose`:

```bash
cp evidencia.example.toml evidencia.toml
podman-compose up
```

The example file runs the decision model on the CPU, and sends the chat requests to `http://host.containers.internal:8000/v1`, which is port 8000 on the host. `host.containers.internal` is the name that Podman gives the host. Change `generator_config.api_url` to point to your chat model.

## Run with a host model cache

To use models that the host has already downloaded, mount the host cache read-only on `/models`, and set `HF_HUB_OFFLINE=1` so that the container downloads nothing. With rootless podman, add `--userns=keep-id`, so that the container user can read the cache:

```bash
podman run --rm --userns=keep-id -p 127.0.0.1:8080:8080 \
  -v "$HOME/.cache/huggingface:/models:ro" -e HF_HUB_OFFLINE=1 \
  -e EVIDENCIA_DECISION_BACKEND=local -e EVIDENCIA_DECISION_DEVICE=cpu \
  evidencia
```

The model must be in the host cache already. Without `--userns=keep-id`, the model cannot load. Then gate questions fail, and after the first one `/health` reports `degraded`.

Without a configuration file, the image uses the defaults. The default decision backend is `http`, so set `EVIDENCIA_DECISION_BACKEND=local` or mount a configuration file.

## Authentication

Set `api_key`, or `EVIDENCIA_API_KEY`, before other hosts can reach the port. Then every `/v1` call must send `Authorization: Bearer <key>`. `GET /health` and the OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) stay open.

```bash
EVIDENCIA_API_KEY=change-me podman-compose up
```

The service has no TLS. Put it behind a reverse proxy that terminates TLS when the traffic leaves the host.

## Health checks

`GET /health` needs no token. It returns `ok` or `degraded`, and it checks the decision backend at most once every 10 seconds. Use it for container and load-balancer health checks. `GET /v1/status` gives more detail and needs the token.

## Logs

The service writes its log to standard error and the HTTP access log to standard output. When the decision backend or the chat model fails, the log line holds the cause, and the client gets a fixed message.

---
[← Configuration](configuration.md) · [Home](index.md) · [Python →](python.md)
