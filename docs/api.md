<!-- agent-hint: Backed by create_app() in evidencia/api.py and the as_dict() methods in evidencia/pipeline.py. The JSON examples come from one run: The gate probabilities and passage ids come from Jevásio-0.8B-v2 in bf16 on CUDA, with a fixed-reply chat engine that answers "Lisboa."; the raw responses are in the knowledge wiki at audits/kev-rag/evidencia/docs-sample-responses.json. Keep field names exact. Every route must appear in the endpoint table, and every status code that a route can return must appear in the errors table. -->

# HTTP API

Start the service with `evidencia serve`. The request and response bodies are JSON, except for file uploads. The service also publishes an OpenAPI description at `/docs` and `/openapi.json`.

## Authentication

When `api_key` is set, every `/v1` call must send the header `Authorization: Bearer <api_key>`. `GET /health` and the OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) need no token. A missing or wrong token returns 401. When `api_key` is not set, the service needs no token. Refer to [Deployment](deployment.md).

## Endpoints

| Method and path | Does |
| --- | --- |
| `POST /v1/collections/{name}/documents` | Adds a document from text or a URL |
| `POST /v1/collections/{name}/files` | Adds an uploaded file |
| `GET /v1/collections` | Lists the collections, their passage counts and sources |
| `DELETE /v1/collections/{name}` | Deletes a collection |
| `DELETE /v1/collections/{name}/documents/{source}` | Deletes one document |
| `POST /v1/retrieve` | Returns passages with their BM25 score and select probability |
| `POST /v1/context` | Runs the select and sufficient gates and returns the evidence, without an answer |
| `POST /v1/answer` | Runs the full loop and returns an answer or an abstention |
| `POST /v1/check/grounding` | Checks an answer that you wrote |
| `POST /v1/systemone` | Answers System One questions with this instance's decision model |
| `GET /v1/models` | Names the model that `/v1/systemone` serves |
| `GET /v1/status` | Returns the backend, the generator, the thresholds and the collection count |
| `GET /health` | Returns whether the service and its decision backend are reachable. Needs no token |

## Documents

`POST /v1/collections/{name}/documents` takes `source`, one of `text` or `url`, and an optional `format`. The format is `text` (the default), `markdown` or `html`. With `url`, it can also be `pdf`. The collection is made if it does not exist.

```json
{"source": "lisboa.txt", "text": "Lisboa é a capital de Portugal.\n\nO Porto fica no norte."}
```

```json
{"collection": "docs", "source": "lisboa.txt", "passages": 2, "total": 2}
```

`passages` is the passage count of this document. `total` is the passage count of the collection.

`POST /v1/collections/{name}/files` takes multipart form data with the field `file`, and the optional fields `source` and `format`. It returns the same body. Refer to [Documents](documents.md) for formats and limits.

`GET /v1/collections` returns:

```json
{"docs": {"passages": 2, "sources": ["lisboa.txt"]}}
```

`DELETE /v1/collections/docs` returns `{"deleted": "docs"}`. `DELETE /v1/collections/docs/documents/lisboa.txt` returns `{"collection": "docs", "source": "lisboa.txt", "removed": 2}`.

## Retrieve

`POST /v1/retrieve` takes `collection`, `question` and `k` (1 to 100, default 8). It returns up to `k` passages, each with its select probability:

```json
{
  "question": "Qual é a capital de Portugal?",
  "passages": [
    {"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
     "score": 0.8318, "p_select": 0.9974}
  ],
  "gates": "applied",
  "select_threshold": 0.5
}
```

`score` is the BM25 score. When the backend is unavailable and `fail_open` is true, every `p_select` is `null` and `gates` is `unavailable`. A passage that the model refuses has `p_select` `null`, and the other passages keep their scores.

## Context

`POST /v1/context` takes `collection` and `question`. Use it when your own chat model writes the answer. It returns:

| Field | Contents |
| --- | --- |
| `evidence` | The passages to give to the chat model, best first |
| `candidates` | Every passage that the select gate scored, by BM25 score |
| `rounds` | The number of retrieval rounds |
| `select_threshold` | The threshold of the select gate |
| `sufficient` | `{"p", "threshold", "passed"}`, or `null` when no evidence was judged |
| `abstain` | `true` when the evidence is not sufficient. Do not answer |
| `gates` | `applied` or `unavailable` |
| `timings_ms` | Time spent in each step |

Then check your answer with `POST /v1/check/grounding`.

## Answer

`POST /v1/answer` takes `collection` and `question`. It returns the fields of `/v1/context` and these fields:

| Field | Contents |
| --- | --- |
| `answer` | The answer, or `null` when a step stopped it |
| `abstained_at` | `retrieve`, `select`, `sufficient`, `grounded`, or `null` |
| `message` | A sentence to show the user when `answer` is `null` |
| `draft` | The answer that the chat model wrote, also when the grounded gate rejected it. For audit only |
| `grounded` | `{"p", "threshold", "passed"}`, or `null` when the check did not run |

`gates` can also be `partial`. Refer to [Gates](gates.md) for the meaning of each value.

An answered question:

```json
{
  "question": "Qual é a capital de Portugal?",
  "evidence": [{"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
                "score": 0.8318, "p_select": 0.9974}],
  "candidates": [{"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
                  "score": 0.8318, "p_select": 0.9974}],
  "rounds": 1,
  "select_threshold": 0.5,
  "sufficient": {"p": 0.9973, "threshold": 0.5, "passed": true},
  "gates": "applied",
  "abstain": false,
  "timings_ms": {"retrieve": 0.5, "select": 6902.3, "sufficient": 77.1, "total": 6979.9, "generate": 2.1, "grounded": 80.1},
  "answer": "Lisboa.",
  "draft": "Lisboa.",
  "grounded": {"p": 0.9959, "threshold": 0.5, "passed": true},
  "abstained_at": null,
  "message": null
}
```

This was the first request to the service, so `select` includes the time to load the decision model. The chat model in this example returns a fixed reply, so `generate` is short.

A question that the evidence does not answer (shortened):

```json
{
  "question": "Quantos habitantes tem Lisboa?",
  "evidence": [{"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
                "score": 0.2773, "p_select": 0.521}],
  "rounds": 1,
  "select_threshold": 0.5,
  "sufficient": {"p": 0.1305, "threshold": 0.5, "passed": false},
  "gates": "applied",
  "abstain": true,
  "answer": null,
  "draft": null,
  "grounded": null,
  "abstained_at": "sufficient",
  "message": "Não tenho informação suficiente para responder a essa pergunta."
}
```

## Check grounding

`POST /v1/check/grounding` takes `question`, `evidence` (a list of strings) and `answer`:

```json
{"question": "Qual é a capital de Portugal?", "evidence": ["Lisboa é a capital de Portugal."], "answer": "O Porto."}
```

```json
{"p": 0.107, "threshold": 0.5, "passed": false}
```

This call does not apply `fail_open`. When the backend is unavailable, it returns 503.

## System One

`POST /v1/systemone` takes a System One request and returns the answers from this instance's decision model. `GET /v1/models` returns `{"models": [{"name": "<model>"}]}`. Refer to [Decision models](decision-models.md).

## Status and health

`GET /health`:

```json
{"status": "ok", "version": "0.0.1a1", "decision_reachable": true}
```

`status` is `degraded` when the decision backend is not reachable. The service checks the backend at most once every 10 seconds and reuses the result.

`GET /v1/status`:

```json
{
  "version": "0.0.1a1",
  "lang": "pt-PT",
  "decision": {"backend": "local", "run": "TigreGotico/jevasio-0.8b-v2", "device": "cuda", "loaded": true,
               "reachable": true, "error": null},
  "generator": null,
  "fail_open": true,
  "thresholds": {"select": 0.5, "sufficient": 0.5, "grounded": 0.5},
  "collections": 1
}
```

`generator` is the configured chat engine name, or `null` when none is configured.

## Errors

An error body is `{"detail": "<reason>"}`. Errors from the decision backend and the chat model give a fixed reason. The cause goes to the service log, because it can name internal hosts.

| Status | Cause |
| --- | --- |
| 401 | The bearer token is missing or wrong |
| 403 | A URL was sent, and URL ingestion is off |
| 404 | The collection or the document does not exist |
| 413 | The document is larger than `max_document_bytes` |
| 422 | The request body is not valid; a document has no text; a file has an unknown format, is not UTF-8 or is not a readable PDF; a URL redirects or its text does not decode; or the decision model refused the request |
| 502 | The chat model is not configured or failed (`generator unavailable`), or a URL could not be fetched |
| 503 | The decision backend is unavailable and `fail_open` is false, or the call does not apply `fail_open` |

---
[← Documents](documents.md) · [Home](index.md) · [Configuration →](configuration.md)
