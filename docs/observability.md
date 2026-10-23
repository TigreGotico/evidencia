<!-- agent-hint: Backed by evidencia/observability.py (request ids, log formats, metric definitions), the observe middleware and the /metrics route in evidencia/api.py, the gate log lines in evidencia/pipeline.py, the decision metrics in evidencia/gates.py, the source metrics in evidencia/sources.py and the embed metrics in evidencia/embeddings.py. Every metric that observability.py defines must appear in the metrics table, with its labels. -->

# Observability

The service gives each request an id, writes log lines as text or as JSON, and serves Prometheus metrics at `GET /metrics`. Use the id to find every log line of one request, also the lines from the threads that search the sources and ask the decision backend.

## Request ids

The service reads the id of a request from its `X-Request-ID` header. An id is 1 to 128 characters from `A-Z`, `a-z`, `0-9`, `.`, `_`, `:` and `-`. When the header is missing, or the id has other characters, the service makes a new id of 32 hexadecimal characters. Every reply has the id in its `X-Request-ID` header, also an error reply.

```bash
curl -si localhost:8080/v1/context -H 'Authorization: Bearer <key>' -H 'Content-Type: application/json' \
     -H 'X-Request-ID: support-4711' -d '{"question": "Qual é a capital de Portugal?", "collection": "docs"}' \
  | grep -i x-request-id
```

```text
x-request-id: support-4711
```

The HTTP decision backend gets the same id in the `X-Request-ID` header of each `/v1/systemone` request. An evidencia instance that serves `/v1/systemone` to other instances therefore logs the id of the question that started the call.

## Log format

Set `log_format` and `log_level` in the configuration file, or `EVIDENCIA_LOG_FORMAT` and `EVIDENCIA_LOG_LEVEL`. Refer to [Configuration](configuration.md).

| Setting | Values | Default |
| --- | --- | --- |
| `log_format` | `text` or `json` | `text` |
| `log_level` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, in any case | `INFO` |

The lines of the service and of uvicorn go to standard error, one line each. `DEBUG` applies to the lines of the service only, and the libraries log from `INFO`. At `DEBUG`, a library such as urllib3 would log each URL, and the URL of a request to a public source holds the words of the question. The OVOS plugin libraries write their own lines to standard output, in their own text format. Read standard error for the JSON lines. A text line holds the time, the level, the logger, the request id in brackets and the message:

```text
2026-10-23 17:18:39,927 INFO evidencia [support-4711] POST /v1/context 200 14 ms
```

A JSON line is one object. It always has these fields:

| Field | Value |
| --- | --- |
| `time` | UTC time with milliseconds, such as `2026-10-23T16:18:39.927Z` |
| `level` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `logger` | The logger name. `evidencia` for the lines of the service |
| `message` | The text of the line |
| `request_id` | The id of the request. `-` for a line outside a request, such as the start of the service |
| `exception` | The traceback. Only on a line that logs an exception |

The lines in the next sections add their own fields to the object.

### Request lines

The service writes one line for each request when the response starts. The message holds the path, with control characters percent-encoded. An error that no handler catches gives a 500 reply with the request id, and an `ERROR` line with the traceback under the same id. `GET /health` and `GET /metrics` log at `DEBUG`, and the other routes at `INFO`. The service turns off uvicorn's access log, because these lines replace it.

| Field | Value |
| --- | --- |
| `method` | The HTTP method. `other` for a method that is not `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE` or `OPTIONS` |
| `route` | The route template, such as `/v1/collections/{name}/documents`. `unmatched` for a path with no route, and for a request refused for its body size, because that refusal comes before the routing |
| `status` | The HTTP status |
| `duration_ms` | Milliseconds until the response starts. For `/v1/answer/stream` this is the time until the first byte, not until the last event |

### Gate lines

Each gate decision has a line with these fields:

| Field | Value |
| --- | --- |
| `gate` | `select`, `sufficient` or `grounded` |
| `p` | The probability from the decision model. On a `select` line, `null` when the model refused the passage |
| `threshold` | The threshold of the request |
| `passed` | Whether `p` reached the threshold |
| `round` | The retrieval round. Not on a `grounded` line |
| `passage_id`, `origin` | The passage and the collection or source that returned it. Only on a `select` line |

The `select` lines log at `DEBUG`, one for each scored passage. The `sufficient` and `grounded` lines log at `INFO`.

### Context lines

The select and sufficient gates end with one `INFO` line that starts with `context:`. It is also written when the decision backend is down and `fail_open` lets the request continue without gates.

| Field | Value |
| --- | --- |
| `rounds` | The retrieval rounds that ran |
| `evidence` | The number of evidence passages |
| `abstained_at` | `retrieve`, `select`, `sufficient`, or `null` |
| `gated` | `false` when the decision backend was down and `fail_open` applied |
| `duration_ms` | Milliseconds for the context gates |
| `sources` | For each collection and source: `name`, `kind`, `candidates`, `duplicates` and `error`, as in the `sources` field of the [HTTP API](api.md#context) |

A JSON line of each kind, from one request:

```json
{"time": "2026-10-23T16:18:39.922Z", "level": "DEBUG", "logger": "evidencia", "message": "select c0429b92bb6f6863 from geo: p 0.9, threshold 0.5, passed", "request_id": "live-ctx-1", "gate": "select", "passage_id": "c0429b92bb6f6863", "origin": "geo", "p": 0.9, "threshold": 0.5, "passed": true, "round": 1}
{"time": "2026-10-23T16:18:39.926Z", "level": "INFO", "logger": "evidencia", "message": "sufficient: p 0.900, threshold 0.5, passed", "request_id": "live-ctx-1", "gate": "sufficient", "p": 0.9, "threshold": 0.5, "passed": true, "round": 1}
{"time": "2026-10-23T16:18:39.927Z", "level": "INFO", "logger": "evidencia", "message": "context: rounds 1, evidence 1, sufficient", "request_id": "live-ctx-1", "rounds": 1, "evidence": 1, "abstained_at": null, "gated": true, "duration_ms": 12.5, "sources": [{"name": "geo", "kind": "collection", "candidates": 1, "duplicates": 0, "error": null}]}
{"time": "2026-10-23T16:18:39.927Z", "level": "INFO", "logger": "evidencia", "message": "POST /v1/context 200 14 ms", "request_id": "live-ctx-1", "method": "POST", "route": "/v1/context", "status": 200, "duration_ms": 14.4}
```

The gate, context and request lines hold no question and no passage text. A failure line holds its cause. The cause can name an internal host, or give the URL of a failed request to a public source, and that URL holds the words of the question. Keep the log on hosts that you trust.

## Metrics

`GET /metrics` returns the metrics in the Prometheus text format. When `api_key` is set, the route needs the bearer token, as the `/v1` routes do. Set the token in the scrape job:

```yaml
scrape_configs:
  - job_name: evidencia
    authorization:
      credentials: <api_key>
    static_configs:
      - targets: ["127.0.0.1:8080"]
```

The counters start at zero when the process starts. Each worker process has its own counters.

| Metric | Type | Labels | Counts |
| --- | --- | --- | --- |
| `evidencia_requests_total` | counter | `route`, `method`, `status` | HTTP requests. `route` and `method` have the values of the request lines |
| `evidencia_request_seconds` | histogram | `route` | Seconds until the response starts |
| `evidencia_abstentions_total` | counter | `gate` | Questions that a step stopped: `retrieve`, `select`, `sufficient` or `grounded` |
| `evidencia_decision_calls_total` | counter | `gate` | Calls to the decision backend. One `select` call scores a batch of passages |
| `evidencia_decision_errors_total` | counter | `gate`, `error` | Decision backend failures. `error` is `unavailable` for a backend that failed, or `rejected` for each state that the model refused |
| `evidencia_select_batch_size` | histogram | none | Passages in one `select` call |
| `evidencia_source_fetches_total` | counter | `source` | Fetches from a public source, failed ones included. An answer from the cache is not a fetch |
| `evidencia_source_errors_total` | counter | `source` | Failed fetches and failed list refreshes of a public source |
| `evidencia_source_pauses_total` | counter | `source` | Times the circuit breaker paused a public source for 60 seconds. A failure during a pause makes the pause longer and is not a new pause. Refer to [Public sources](sources.md) |
| `evidencia_embed_calls_total` | counter | none | Calls to the embedder |
| `evidencia_embed_texts_total` | counter | none | Texts that the embedder embedded |

A `/v1/retrieve` call scores passages, but it does not abstain. A `/v1/answer` that abstains at a context step counts once, at that step.

Some example queries:

| Question | PromQL |
| --- | --- |
| Abstentions per minute, by gate | `60 * sum by (gate) (rate(evidencia_abstentions_total[5m]))` |
| 95th percentile latency of `/v1/answer` | `histogram_quantile(0.95, rate(evidencia_request_seconds_bucket{route="/v1/answer"}[5m]))` |
| Sources that the breaker paused in the last hour | `increase(evidencia_source_pauses_total[1h]) > 0` |
| Share of failed decision calls | `sum(rate(evidencia_decision_errors_total{error="unavailable"}[5m])) / sum(rate(evidencia_decision_calls_total[5m]))` |

---
[← Deployment](deployment.md) · [Home](index.md) · [Python →](python.md)
