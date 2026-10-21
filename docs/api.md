<!-- agent-hint: Backed by create_app() in evidencia/api.py and the as_dict() methods in evidencia/pipeline.py. The JSON examples are from the live run in knowledge/wiki/audits/kev-rag/evidencia/live-v2/ (Jevásio-0.8B-v2 on CPU, hybrid retrieval, AMALIA Q4_K_M). Keep field names exact. Every route must appear in the endpoint table, every stream event in the events table, and every status code that a route can return in the errors table. -->

# HTTP API

Start the service with `evidencia serve`. The request and response bodies are JSON, except for file uploads and the event stream. The service also publishes an OpenAPI description at `/docs` and `/openapi.json`.

## Authentication

When `api_key` is set, every `/v1` call must send the header `Authorization: Bearer <api_key>`. `GET /`, `GET /health` and the OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) need no token. A missing or wrong token returns 401. When `api_key` is not set, the service needs no token. Refer to [Deployment](deployment.md).

## Endpoints

| Method and path | Does |
| --- | --- |
| `POST /v1/collections/{name}/documents` | Adds a document from text or a URL |
| `POST /v1/collections/{name}/files` | Adds an uploaded file |
| `GET /v1/collections` | Lists the collections, their passage counts and sources |
| `DELETE /v1/collections/{name}` | Deletes a collection |
| `DELETE /v1/collections/{name}/documents/{source}` | Deletes one document |
| `GET /v1/sources` | Lists the public sources and which ones are enabled |
| `POST /v1/retrieve` | Returns passages with their retrieval score and select probability |
| `POST /v1/context` | Runs the select and sufficient gates and returns the evidence, without an answer |
| `POST /v1/answer` | Runs the full loop and returns an answer or an abstention |
| `POST /v1/answer/stream` | The same as `/v1/answer`, as server-sent events, one for each step |
| `POST /v1/check/grounding` | Checks an answer that you wrote |
| `POST /v1/systemone` | Answers System One questions with this instance's decision model |
| `GET /v1/models` | Names the model that `/v1/systemone` serves |
| `GET /v1/status` | Returns the backend, the chat model, the thresholds, the retrieval settings and the enabled sources |
| `GET /health` | Returns whether the service and its decision backend are reachable. Needs no token |
| `GET /` | The [demo page](demo.md), when `ui` is true. Needs no token |

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

## Sources

`GET /v1/sources` returns every source that evidencia knows, with `enabled` true for the sources in the configuration:

```json
{"sources": [
  {"name": "pt.wikipedia", "title": "Wikipédia em português", "homepage": "https://pt.wikipedia.org",
   "licence": "CC BY-SA 4.0", "commercial": true,
   "description": "Encyclopedia articles. Articles are in European or Brazilian Portuguese.", "enabled": true}
]}
```

Refer to [Public sources](sources.md).

## Questions

`/v1/retrieve`, `/v1/context`, `/v1/answer` and `/v1/answer/stream` take the same fields:

| Field | Contents |
| --- | --- |
| `question` | The question, in European Portuguese |
| `collection` | A collection to search. Optional |
| `sources` | A list of enabled source names to search. Optional, default `[]` |

Give a collection, at least one source, or both.

Each passage in a response has these fields:

| Field | Contents |
| --- | --- |
| `id` | An id derived from the document, the position and the text |
| `text` | The passage text |
| `source` | The document: a file name, or the page title for a public source |
| `origin` | The collection or the public source that returned the passage |
| `url` | The page to cite, for a public source. Empty for documents added over HTTP |
| `score` | The retrieval score: BM25, or the fused rank score with hybrid retrieval. Compare scores only inside one `origin` |
| `p_select` | The select probability, or `null` when the gate did not run or the model refused the passage |

Each response also has `sources`, one row for each target, with `candidates`, `duplicates` and `error`. Refer to [Gates](gates.md).

## Retrieve

`POST /v1/retrieve` also takes `k` (1 to 100, default 8). It returns up to `k` passages from each target, each with its select probability:

```json
{
  "question": "Qual é a capital de Portugal?",
  "passages": [
    {"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
     "score": 0.0328, "url": "", "origin": "docs", "p_select": 0.9974},
    {"id": "c0abd6b65cd62d54", "text": "O Porto fica no norte.", "source": "lisboa.txt",
     "score": 0.0161, "url": "", "origin": "docs", "p_select": 0.038}
  ],
  "gates": "applied",
  "select_threshold": 0.5,
  "sources": [{"name": "docs", "kind": "collection", "candidates": 2, "duplicates": 0, "error": null}]
}
```

When the backend is unavailable and `fail_open` is true, every `p_select` is `null` and `gates` is `unavailable`. A passage that the model refuses has `p_select` `null`, and the other passages keep their scores.

## Context

`POST /v1/context` returns the checked evidence without an answer. Use it when your own chat model writes the answer.

| Field | Contents |
| --- | --- |
| `evidence` | The passages to give to the chat model, best first |
| `candidates` | Every passage that the select gate scored, in the order that retrieval found them |
| `rounds` | The number of retrieval rounds |
| `select_threshold` | The threshold of the select gate |
| `sufficient` | `{"p", "threshold", "passed"}`, or `null` when no evidence was judged |
| `abstain` | `true` when the evidence is not sufficient. Do not answer |
| `abstained_at` | `retrieve`, `select`, `sufficient`, or `null` |
| `gates` | `applied` or `unavailable` |
| `sources` | One row for each target |
| `timings_ms` | Time spent in each step, in milliseconds |

Then check your answer with `POST /v1/check/grounding`.

## Answer

`POST /v1/answer` returns the fields of `/v1/context` and these fields:

| Field | Contents |
| --- | --- |
| `answer` | The answer, or `null` when a step stopped it. It cites the evidence as `[1]`, `[2]` |
| `cited` | The evidence numbers that the answer cites, each once, from low to high. `[1]` is the first item of `evidence` |
| `abstained_at` | `retrieve`, `select`, `sufficient`, `grounded`, or `null` |
| `message` | A sentence to show the user when `answer` is `null` |
| `draft` | The answer that the chat model wrote, also when the grounded gate rejected it. For audit only |
| `grounded` | `{"p", "threshold", "passed"}`, or `null` when the check did not run |

`gates` can also be `partial`. Refer to [Gates](gates.md) for the meaning of each value.

An answered question (`candidates` shortened):

```json
{
  "question": "Qual é a capital de Portugal?",
  "evidence": [{"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
                "score": 0.0328, "url": "", "origin": "docs", "p_select": 0.9974}],
  "candidates": ["..."],
  "rounds": 1,
  "select_threshold": 0.5,
  "sufficient": {"p": 0.9975, "threshold": 0.5, "passed": true},
  "gates": "applied",
  "abstain": false,
  "abstained_at": null,
  "sources": [{"name": "docs", "kind": "collection", "candidates": 2, "duplicates": 0, "error": null}],
  "timings_ms": {"retrieve": 1.0, "select": 11018.3, "sufficient": 579.2, "total": 11598.9, "generate": 1046.6, "grounded": 972.1},
  "answer": "Lisboa é a capital de Portugal [1].",
  "draft": "Lisboa é a capital de Portugal [1].",
  "cited": [1],
  "grounded": {"p": 0.9967, "threshold": 0.5, "passed": true},
  "message": null
}
```

This was the first request to the service, so `select` includes the time to load the decision model on the CPU. The next request took 1.1 seconds to select.

A question that the evidence does not answer (shortened):

```json
{
  "question": "Quantos habitantes tem Lisboa?",
  "evidence": [{"id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt",
                "score": 0.0328, "url": "", "origin": "docs", "p_select": 0.5103}],
  "rounds": 1,
  "sufficient": {"p": 0.1284, "threshold": 0.5, "passed": false},
  "gates": "applied",
  "abstain": true,
  "answer": null,
  "draft": null,
  "cited": [],
  "grounded": null,
  "abstained_at": "sufficient",
  "message": "Não tenho informação suficiente para responder a essa pergunta."
}
```

## Answer stream

`POST /v1/answer/stream` takes the question fields and `generate` (default `true`). With `generate` false, it stops after the context gates, as `/v1/context` does. The response is `text/event-stream`. Each event has a name and one line of JSON:

```text
event: select
data: {"round": 1, "id": "ec181feda4262e90", "text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt", "score": 0.0328, "url": "", "origin": "docs", "p_select": 0.9974}

event: sufficient
data: {"round": 1, "p": 0.9975, "threshold": 0.5, "passed": true}
```

| Event | Data |
| --- | --- |
| `candidates` | `round`, and the new `passages` without `p_select` |
| `select` | `round`, and one passage with its `p_select` |
| `evidence` | `round`, and the evidence `passages` after that round |
| `sufficient` | `round`, and `p`, `threshold`, `passed`. Or `skipped`: `"no evidence"`, or `"same evidence"` with the earlier verdict |
| `gates_unavailable` | `candidates`, the ungated passages |
| `draft` | `draft` and `cited` |
| `grounded` | `p`, `threshold`, `passed` |
| `result` | The body that `/v1/answer` or `/v1/context` gives |
| `error` | `status` and `detail`, the same as the HTTP error would give |

The stream ends after `result` or `error`. When the client closes the stream, evidencia stops the request before its next step, so a closed stream asks the decision model no more questions. A request that is not valid, names a missing collection or names a source that is not enabled fails before the stream starts, with the HTTP status in the errors table.

## Check grounding

`POST /v1/check/grounding` takes `question`, `evidence` (a list of strings) and `answer`:

```json
{"question": "Qual é a capital de Portugal?", "evidence": ["Lisboa é a capital de Portugal."], "answer": "O Porto."}
```

```json
{"p": 0.1007, "threshold": 0.5, "passed": false}
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
  "decision": {"backend": "local", "run": "TigreGotico/jevasio-0.8b-v2", "device": "cpu", "loaded": true,
               "reachable": true, "error": null},
  "generator": "amalia",
  "generator_configured": true,
  "fail_open": true,
  "thresholds": {"select": 0.5, "sufficient": 0.5, "grounded": 0.5},
  "retrieval": {"n_candidates": 8, "top_k": 4, "max_rounds": 2, "hybrid": true, "dedupe_threshold": 0.95},
  "sources": ["arquivo.pt", "dicionario-aberto", "pt.wikipedia", "pt.wikisource", "pt.wiktionary", "wikidata"],
  "collections": 1
}
```

`generator` is the configured chat model name, or `null`. `generator_configured` is false when no chat model is set, so `/v1/answer` can only abstain or fail. `sources` lists the enabled sources.

## Errors

An error body is `{"detail": "<reason>"}`. Errors from the decision backend, the chat model and a URL fetch give a fixed reason. The full cause goes to the service log, because it can name internal hosts or keys.

| Status | Cause |
| --- | --- |
| 401 | The bearer token is missing or wrong |
| 403 | A URL was sent, and URL ingestion is off |
| 404 | The collection or the document does not exist |
| 413 | The document is larger than `max_document_bytes` |
| 422 | The request body is not valid; it names no collection and no source; it names a source that is not enabled, or a collection with the name of a requested source; a document has no text; a file has an unknown format, is not UTF-8 or is not a readable PDF; a URL redirects or its text does not decode; or the decision model refused the request |
| 500 | An unexpected error in the answer stream. The `error` event carries it |
| 502 | The chat model is not configured or failed, or a URL could not be fetched. The reasons for the chat model are below |
| 503 | The decision backend is unavailable and `fail_open` is false, or the call does not apply `fail_open` |

| 502 `detail` | Cause |
| --- | --- |
| `cannot connect to the chat model server; is it running?` | No server answers at `api_url` |
| `the chat model provider refused the API key` | The provider returned 401 or 403 |
| `the chat model provider does not know the model or the address` | The provider returned 404 |
| `the chat model provider is rate limiting or the account has no credit` | The provider returned 429 |
| `the chat model provider returned HTTP <status>` | The provider returned another error status |
| `generator unavailable` | No chat model is configured, it is not installed, it returned an empty answer, or it failed in another way |
| `could not fetch the URL` | URL ingestion failed |

---
[← Demo page](demo.md) · [Home](index.md) · [Configuration →](configuration.md)
