# evidencia

Decision gates for retrieval-augmented generation: passage selection, evidence sufficiency and answer grounding, with calibrated probabilities.

evidencia retrieves passages from your documents and asks a decision model three yes/no questions:

1. **Select.** Does this passage help to answer the question? Passages below the threshold do not go to the generator.
2. **Sufficient.** Is the selected evidence enough to answer? If not, evidencia retrieves more candidates and asks again. When the rounds run out, it does not answer.
3. **Grounded.** Is every fact in the generated answer supported by the evidence? If not, evidencia suppresses the answer.

Each response carries the probability of each gate and the threshold that was applied, so you can audit every decision. The questions are in European Portuguese (pt-PT).

## Install

```bash
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow "evidencia[local,generators]"
```

| Extra | Adds |
| --- | --- |
| `local` | Runs the decision model in the same process (torch, transformers, peft) |
| `generators` | Chat engines that write the answers (an OpenAI-compatible API client, a local GGUF runner) |
| `test` | pytest and httpx |

Without `local`, evidencia calls a decision server over HTTP.

## Decision models

The decision backend is any model with the System One request shape: a state plus typed `noul`, `choice` and `score` questions, answered with probabilities.

| Model | Licence | Notes |
| --- | --- | --- |
| [Kev-0.8B](https://huggingface.co/jaredpalmer/kev-0.8b) | Apache-2.0 | Default. Multilingual base, trained on English decisions |
| [Jevásio-0.8B-v2](https://huggingface.co/TigreGotico/jevasio-0.8b-v2) | CC BY-NC-SA 4.0 | Trained on pt-PT decisions. Research and non-commercial use only |
| Any System One server | — | Set `decision.backend = "http"` and `decision.url` |

The licence of the model you configure applies to its outputs. Jevásio is not licensed for commercial use.

## Quickstart

Write `evidencia.toml`:

```toml
generator = "ovos-chat-openai-plugin"

[generator_config]
api_url = "http://127.0.0.1:8000/v1"   # any OpenAI-compatible server, for example llama.cpp
model = "local-model"

[decision]
backend = "local"
run = "jaredpalmer/kev-0.8b"
```

Start the service, add a document and ask a question:

```bash
evidencia serve --config evidencia.toml --port 8080

curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "lisboa.txt", "text": "Lisboa é a capital de Portugal.\n\nO Porto fica no norte."}'

curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Qual é a capital de Portugal?"}'
```

The first decision call downloads the model to the Hugging Face cache.

## API

| Method and path | Does |
| --- | --- |
| `POST /v1/collections/{name}/documents` | Adds a document: `{"source", "text", "format"}`, with format `text`, `markdown` or `html`. A new `source` value adds a document; a known one replaces it |
| `POST /v1/collections/{name}/files` | Adds an uploaded file (`.txt`, `.md`, `.html`, `.pdf`), as multipart form data |
| `GET /v1/collections` | Lists the collections, their passage counts and sources |
| `DELETE /v1/collections/{name}` | Deletes a collection |
| `DELETE /v1/collections/{name}/documents/{source}` | Deletes one document |
| `POST /v1/retrieve` | Returns the passages with their BM25 score and select probability |
| `POST /v1/context` | Runs the select and sufficiency gates and returns the evidence, for clients that use their own generator |
| `POST /v1/answer` | Runs the full loop |
| `POST /v1/check/grounding` | Checks an answer that you generated: `{"question", "evidence": [...], "answer"}` |
| `GET /health` | Returns whether the service and the decision backend are reachable. Needs no token. The backend is probed at most once every 10 seconds |
| `GET /v1/status` | Returns the backend, the generator and the thresholds |
| `POST /v1/systemone` | Answers System One questions with this instance's decision model, so another instance can use it as its decision backend |
| `GET /v1/models` | Names the model that `/v1/systemone` serves |

An `/v1/answer` response has `answer` set to `null` when a gate stops it. `abstained_at` names the step that stopped it, and `message` holds a sentence you can show the user:

| `abstained_at` | Meaning |
| --- | --- |
| `retrieve` | No passage shares a word with the question |
| `select` | Passages were found, but the select gate kept none |
| `sufficient` | The kept evidence is not enough to answer |
| `grounded` | The generated answer is not supported by the evidence |

When the grounding gate stops an answer, `draft` keeps the rejected answer for audit. Do not show `draft` to users.

`gates` is `applied` when every gate ran. It is `unavailable` when the decision backend was down and `fail_open` let the request continue without gates. It is `partial` when the backend went down after generation, so the answer has not had a grounding check.

The decision model can refuse a request, for example a state that is longer than its context. A refusal is not an outage, so it never switches the gates off. A refused passage is not used as evidence, and a refused sufficiency or grounding question makes the answer abstain at that gate. A direct call that the model refuses returns 422. Documents are split into passages of at most 1500 characters, so one long paragraph cannot fill the model context.

Collections are in memory. They are lost when the service stops.

## Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `select_threshold` | `0.5` | Minimum select probability to keep a passage |
| `sufficient_threshold` | `0.5` | Minimum sufficiency probability to answer |
| `grounded_threshold` | `0.5` | Minimum grounding probability to return an answer |
| `n_candidates` | `8` | Passages retrieved in the first round. Each later round doubles it |
| `top_k` | `4` | Selected passages kept as evidence |
| `max_rounds` | `2` | Retrieval rounds before evidencia stops |
| `fail_open` | `true` | When the decision backend is down: `true` answers without gates and marks the response `"gates": "unavailable"`; `false` returns 503. A refused request is not an outage and never answers without gates |
| `generator` | none | Name of the chat engine that writes answers |
| `generator_config` | `{}` | Settings for that chat engine |
| `api_key` | none | Bearer token that every `/v1` call must send |
| `allow_url_ingest` | `false` | Lets clients make the service fetch a URL. Redirects are refused, and the download stops at `max_document_bytes`. The service can still reach any host that it can see, so turn this on only on a trusted network |
| `max_document_bytes` | `20000000` | Largest document accepted, in bytes |
| `decision.backend` | `http` | `http` or `local` |
| `decision.url` | `http://127.0.0.1:8008` | HTTP: the server root |
| `decision.key` | none | HTTP: bearer token for the decision server |
| `decision.run` | `jaredpalmer/kev-0.8b` | Local: a Hugging Face model id or a local checkpoint directory |
| `decision.device` | `auto` | Local: `auto`, `cpu`, `cuda` or `mps` |

These environment variables override the file: `EVIDENCIA_CONFIG` (path of the file), `EVIDENCIA_API_KEY`, `EVIDENCIA_DECISION_BACKEND`, `EVIDENCIA_DECISION_URL`, `EVIDENCIA_DECISION_KEY`, `EVIDENCIA_DECISION_RUN` and `EVIDENCIA_DECISION_DEVICE`.

The default thresholds are not tuned. Measure them on your own questions before you rely on them.

## Python

```python
from evidencia import Config, Evidencia

engine = Evidencia(Config(decision={"backend": "local"}))
engine.collection("docs", create=True).add("Lisboa é a capital de Portugal.", "lisboa.txt")
ctx = engine.context("Qual é a capital de Portugal?", "docs")
print(ctx.sufficient, [s.passage.text for s in ctx.evidence])
```

## Third-party code

`evidencia/_vendor/kev/` holds four inference modules of [Kev](https://github.com/jaredpalmer/kev) (commit `1f04cfa`), under the Apache License 2.0 in that directory. They are used unchanged.
