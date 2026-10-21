<!-- agent-hint: Backed by Config and DecisionConfig in evidencia/config.py. Every dataclass field must appear in one of the two tables, with its real default; every EVIDENCIA_* variable that Config.load() reads must appear in the environment table. evidencia.example.toml must stay a valid file for Config.load(). -->

# Configuration

evidencia reads one TOML file, then applies environment variables over it. Give the file with `evidencia serve --config <path>`. Without `--config`, evidencia reads the file that `EVIDENCIA_CONFIG` names. Without a file, every setting has its default.

```toml
generator = "ovos-chat-openai-plugin"
api_key = "change-me"
grounded_threshold = 0.8

[generator_config]
api_url = "http://127.0.0.1:8000/v1"
model = "local-model"

[decision]
backend = "local"
run = "TigreGotico/jevasio-0.8b-v2"
device = "cuda"
```

An unknown key stops the service when it starts, with an error that names the key. So does a threshold that is not a number from 0 to 1, a count that is not a positive integer, or an empty `api_key`.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `lang` | `pt-PT` | Language of the gate questions and messages. pt-PT is the only language |
| `select_threshold` | `0.5` | Minimum select probability to keep a passage. From 0 to 1 |
| `sufficient_threshold` | `0.5` | Minimum sufficiency probability to answer. From 0 to 1 |
| `grounded_threshold` | `0.5` | Minimum grounding probability to return an answer. From 0 to 1 |
| `n_candidates` | `8` | Passages retrieved in the first round. Each later round doubles it |
| `top_k` | `4` | Selected passages kept as evidence |
| `max_rounds` | `2` | Retrieval rounds before evidencia stops |
| `fail_open` | `true` | When the decision backend is unavailable: `true` answers without gates, `false` returns 503. Refer to [Gates](gates.md) |
| `generator` | none | Name of the chat engine that writes answers, for example `ovos-chat-openai-plugin` |
| `generator_config` | `{}` | Settings for that chat engine. evidencia adds a `system_prompt` unless you set one |
| `api_key` | none | Bearer token that every `/v1` call must send. An empty string is an error: leave the key out to run without a token |
| `allow_url_ingest` | `false` | Lets clients make the service fetch a URL. Refer to [Documents](documents.md) |
| `max_document_bytes` | `20000000` | Largest document accepted, in bytes |

## Decision settings

These go in the `[decision]` table. Refer to [Decision models](decision-models.md).

| Key | Default | Meaning |
| --- | --- | --- |
| `backend` | `http` | `local` runs the model in the service. `http` calls a decision server |
| `run` | `TigreGotico/jevasio-0.8b-v2` | Local: a Hugging Face model id or a local checkpoint directory |
| `device` | `auto` | Local: `auto`, `cpu`, `cuda` or `mps` |
| `dtype` | none | Local: `bf16` or `fp32`. Without it, bf16 on CUDA and fp32 elsewhere |
| `url` | `http://127.0.0.1:8008` | HTTP: the server root |
| `key` | none | HTTP: bearer token for the decision server |
| `model` | `kev-latest` | HTTP: model name sent in each request |
| `timeout` | `30` | HTTP: seconds to wait for each reply |

The default backend is `http`. To run the default model inside the service, set `backend = "local"`.

## Environment variables

A variable that is set and not empty replaces the value from the file. `EVIDENCIA_CONFIG` is the exception: `--config` takes priority over it.

| Variable | Replaces |
| --- | --- |
| `EVIDENCIA_CONFIG` | The path of the TOML file, when `--config` is not given |
| `EVIDENCIA_API_KEY` | `api_key` |
| `EVIDENCIA_DECISION_BACKEND` | `decision.backend` |
| `EVIDENCIA_DECISION_URL` | `decision.url` |
| `EVIDENCIA_DECISION_KEY` | `decision.key` |
| `EVIDENCIA_DECISION_RUN` | `decision.run` |
| `EVIDENCIA_DECISION_DEVICE` | `decision.device` |

`HF_HOME` sets where the local backend stores downloaded models.

---
[← HTTP API](api.md) · [Home](index.md) · [Deployment →](deployment.md)
