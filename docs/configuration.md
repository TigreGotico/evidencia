<!-- agent-hint: Backed by Config and DecisionConfig in evidencia/config.py, and build_generator() in evidencia/generators.py for the EVIDENCIA_GENERATOR_* variables. Every dataclass field must appear in one of the two tables, with its real default; every EVIDENCIA_* variable that Config.load(), build_generator() or the mcp command reads must appear in the environment table. evidencia.example.toml must stay a valid file for Config.load(). -->

# Configuration

evidencia reads one TOML file, then applies environment variables over it. Give the file with `evidencia serve --config <path>`. Without `--config`, evidencia reads the file that `EVIDENCIA_CONFIG` names. Without a file, every setting has its default.

```toml
generator = "amalia"
api_key = "change-me"
grounded_threshold = 0.9
data_dir = "collections"
sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]
embedding_model = "minishlab/potion-multilingual-128M"
dedupe_threshold = 0.95

[generator_config]
api_url = "http://127.0.0.1:8000/v1"

[decision]
backend = "local"
run = "TigreGotico/jevasio-0.8b-v2"
device = "cpu"
```

Put the top-level settings above the first `[table]` line. In TOML, a setting below `[generator_config]` belongs to that table, and evidencia then stops with `unknown generator_config keys`.

An unknown key stops the service when it starts, with an error that names the key. So does a threshold that is not a number from 0 to 1, a count that is not a positive integer, an empty `api_key`, an unknown source, a `dedupe_threshold` without `embedding_model` or `embedder`, both `embedder` and `embedding_model`, an `embedder` plugin that is not installed, a `vector_db` without embeddings or without a place to keep them, a `vector_db` plugin that is not installed, an embedding model that cannot load, and a preset that needs a key or a model that is not set.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `lang` | `pt-PT` | Language of the gate questions and messages. pt-PT is the only language |
| `select_threshold` | `0.5` | Minimum select probability to keep a passage. From 0 to 1 |
| `sufficient_threshold` | `0.5` | Minimum sufficiency probability to answer. From 0 to 1 |
| `grounded_threshold` | `0.8` | Minimum grounding probability to return an answer. From 0 to 1 |
| `n_candidates` | `8` | Passages retrieved in the first round. Each later round doubles it |
| `top_k` | `4` | Selected passages kept as evidence |
| `max_rounds` | `2` | Retrieval rounds before evidencia stops |
| `max_request_top_k` | `16` | The largest `top_k` that a request can set |
| `max_request_candidates` | `64` | The largest `n_candidates` that a request can set |
| `max_request_rounds` | `4` | The largest `max_rounds` that a request can set |
| `allow_lower_thresholds` | `false` | Lets a request set a gate threshold below the server value. A request can always set a higher one. Refer to [the API reference](api.md#questions) |
| `fail_open` | `true` | When the decision backend is unavailable: `true` answers without gates, `false` returns 503. Refer to [Gates](gates.md) |
| `generator` | `amalia` | The chat model that writes answers: a preset such as `amalia` or `anthropic`, or the plugin name of a chat engine, such as `ovos-chat-openai-plugin`. Refer to [Chat models](generators.md) |
| `generator_config` | `{}` | Settings for the chat model: `api_url`, `model`, `key`, `max_tokens`, `temperature`, `timeout`, and `effort` for Claude. For a plugin, its own settings, and evidencia adds a `system_prompt` unless you set one |
| `api_key` | none | Bearer token that every `/v1` call must send. An empty string is an error: leave the key out to run without a token |
| `allow_url_ingest` | `false` | Lets clients make the service fetch a URL. Refer to [Documents](documents.md) |
| `max_document_bytes` | `20000000` | Largest document accepted, in bytes |
| `data_dir` | none | Folder that keeps the collections across restarts. Without it, collections are in memory. Refer to [Documents](documents.md) |
| `embedding_model` | none | A model2vec model for hybrid retrieval, for example `minishlab/potion-multilingual-128M`. Without it, retrieval is BM25 only. Needs the `hybrid` extra |
| `embedder` | none | A text embeddings plugin for hybrid retrieval, instead of `embedding_model`, for example `ovos-m2v-embeddings-plugin`. Refer to [Documents](documents.md#embedding-plugins) |
| `embedder_config` | `{}` | Settings for the `embedder` plugin |
| `vector_db` | none | An embeddings database plugin that keeps the embeddings, for example `ovos-qdrant-embeddings-plugin`. Needs `embedding_model` or `embedder`. Refer to [Documents](documents.md#vector-databases) |
| `vector_db_config` | `{}` | Settings for the `vector_db` plugin. Without `host` or `path`, evidencia sets `path` to `<data_dir>/vectors` |
| `dedupe_threshold` | none | Drops a candidate whose embedding has this cosine similarity, or more, to an earlier candidate. More than 0 and not more than 1. Needs `embedding_model` or `embedder` |
| `sources` | `[]` | Public sources that a request can name. Refer to [Public sources](sources.md) |
| `user_agent` | `evidencia/<version> (https://github.com/TigreGotico/evidencia)` | User-Agent sent to the public sources. Add your contact information |
| `source_timeout` | `10` | Seconds to wait for each request to a public source |
| `transport` | none | How requests to the public sources are sent: `curl_cffi`, `requests`, `flaresolverr` or `browserless`. Without it, curl_cffi. Refer to [Public sources](sources.md#sites-that-refuse-a-client) |
| `flaresolverr_url` | none | A FlareSolverr server. A GET that a site refuses is sent again through it |
| `browserless_url` | none | A browserless server, for `transport = "browserless"` |
| `ui` | `true` | Serves the [demo page](demo.md) at `/` |

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
| `workers` | `4` | HTTP: select questions sent at the same time. A server that batches its requests answers them in one pass |

The default backend is `http`. To run the default model inside the service, set `backend = "local"`.

## Environment variables

A variable that is set and not empty replaces the value from the file. `EVIDENCIA_CONFIG` is the exception: `--config` takes priority over it.

| Variable | Replaces |
| --- | --- |
| `EVIDENCIA_CONFIG` | The path of the TOML file, when `--config` is not given |
| `EVIDENCIA_API_KEY` | `api_key`. The `mcp` command also reads it, as the token to send |
| `EVIDENCIA_GENERATOR` | `generator` |
| `EVIDENCIA_GENERATOR_KEY` | `generator_config.key`, and the provider's own variable |
| `EVIDENCIA_GENERATOR_MODEL` | `generator_config.model` |
| `EVIDENCIA_GENERATOR_URL` | `generator_config.api_url` |
| `EVIDENCIA_DATA_DIR` | `data_dir` |
| `EVIDENCIA_TRANSPORT` | `transport` |
| `EVIDENCIA_FLARESOLVERR_URL` | `flaresolverr_url` |
| `EVIDENCIA_BROWSERLESS_URL` | `browserless_url` |
| `EVIDENCIA_SOURCES` | `sources`, as a comma-separated list: `pt.wikipedia,wikidata` |
| `EVIDENCIA_DECISION_BACKEND` | `decision.backend` |
| `EVIDENCIA_DECISION_URL` | `decision.url` |
| `EVIDENCIA_DECISION_KEY` | `decision.key` |
| `EVIDENCIA_DECISION_RUN` | `decision.run` |
| `EVIDENCIA_DECISION_DEVICE` | `decision.device` |
| `EVIDENCIA_URL` | Not a service setting. The `mcp` command sends its requests to this address. Refer to [Agents](agents.md) |

The `EVIDENCIA_GENERATOR_*` variables apply to the presets only. `EVIDENCIA_GENERATOR_URL` has no effect for `anthropic`. The presets also read the provider's own key variable when no key is set: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` or `OPENCODE_API_KEY`.

`HF_HOME` sets where the decision model and the embedding model are stored.

---
[← HTTP API](api.md) · [Home](index.md) · [Deployment →](deployment.md)
