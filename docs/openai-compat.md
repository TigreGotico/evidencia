<!-- agent-hint: Backed by chat_completions and vector_store_search in evidencia/api.py, and tested with the official openai SDK in test/test_openai_compat.py. Keep the field names exact. -->

# OpenAI compatibility

evidencia answers two OpenAI routes, so an OpenAI client and an OpenAI RAG tool can use it with no code of their own:

- `POST /v1/chat/completions` gives the gated answer as a chat completion.
- `POST /v1/vector_stores/{collection}/search` gives the passages that pass the select gate as a vector store search.

Set the client's base URL to `http://<host>:8080/v1`. Use `api_key` as the API key. When the server sets no `api_key`, any key works.

## Chat completions

The last user message is the question. evidencia ignores the other messages and the OpenAI fields that it does not use, such as `temperature`. The `model` field names what to search:

| `model` | Searches |
| --- | --- |
| `evidencia/<collection>` | The collection, and the `sources` in the body |
| `evidencia` | Only the `sources` in the body |

The body can also hold `sources` and the per-request settings of `/v1/answer`: `top_k`, `n_candidates`, `max_rounds`, `select_threshold`, `sufficient_threshold` and `grounded_threshold`. With the openai SDK, send them in `extra_body`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="<api_key>")
reply = client.chat.completions.create(
    model="evidencia/docs",
    messages=[{"role": "user", "content": "Qual é a capital de Portugal?"}],
    extra_body={"sources": ["dre"], "top_k": 4},
)
print(reply.choices[0].message.content)
print(reply.model_extra["evidencia"]["evidence"])
```

The message content is the answer, with its citation marks such as `[1]`. When a gate abstains, the content is the abstain text of the locale, for example `Não tenho informação suficiente para responder a essa pergunta.` The `finish_reason` is `stop` in both cases. The reply also holds an `evidencia` field:

| Field | Value |
| --- | --- |
| `gates` | `applied`, `partial` or `unavailable`, as in `/v1/answer` |
| `abstained` | `true` when the content is the abstain text |
| `abstained_at` | The gate that abstained: `retrieve`, `select`, `sufficient` or `grounded`. Otherwise `null` |
| `evidence` | The evidence passages, numbered as the citation marks count them, each with its `p_select` |
| `cited` | The evidence numbers that the answer cites |
| `grounded` | The grounded verdict: `p`, `threshold`, `passed`. `null` when the gate did not run |

With `fail_open` true and the decision backend down, the content is the draft that no gate checked, and `gates` is `unavailable`. A client that must not show an unchecked answer reads `gates`.

### Streaming

With `stream: true`, the reply is a stream of chat completion chunks. The grounded gate checks the whole draft before evidencia sends any of it, so the stream sends the checked answer as one chunk. A last chunk holds `finish_reason: "stop"` and the `evidencia` field, and `data: [DONE]` ends the stream. A failure after the stream starts is sent as an `error` chunk, and the openai SDK raises `APIError`.

```python
for chunk in client.chat.completions.create(model="evidencia/docs", stream=True,
                                            messages=[{"role": "user", "content": "Qual é a capital de Portugal?"}]):
    print(chunk.choices[0].delta.content or "", end="")
```

## Vector store search

The vector store is a collection. The body holds `query`, a string or a list of strings, and `max_num_results`, from 1 to 50 (default 10). It can also hold `sources` and the per-request settings. evidencia runs the select and sufficient gates, as `/v1/context` does:

```python
page = client.vector_stores.search("docs", query="Qual é a capital de Portugal?", max_num_results=5)
for hit in page.data:
    print(hit.score, hit.filename, hit.content[0].text)
```

| Field of a result | Value |
| --- | --- |
| `file_id`, `filename` | The source name of the document |
| `score` | The select probability |
| `content` | One text part with the passage |
| `attributes` | `passage_id`, `url` and `origin`, the collection or public source |

The results are the evidence passages, at most `max_num_results`. When the evidence is not enough to answer, the page has no results, and its `evidencia` field gives `abstained` and `abstained_at`. When the decision backend is unavailable, the search returns 503, also with `fail_open` true, because a search result has no field that says no gate checked it.

`ovos-openai-rag-memory-plugin` works with this route: set its `api_url` to `http://<host>:8080/v1` and its `vector_store_id` to the collection.

## Errors

| Status | Cause |
| --- | --- |
| 401 | The API key is wrong |
| 404 | The `model` is not `evidencia` or `evidencia/<collection>`, or the collection does not exist |
| 422 | The messages hold no user message, the model names no collection and the body no source, or a setting is not valid |
| 503 | The decision backend is unavailable: for a chat completion when `fail_open` is false, for a search always |

The other statuses are the same as for `/v1/answer`. Refer to [HTTP API](api.md#errors).

---
[← HTTP API](api.md) · [Home](index.md) · [Configuration →](configuration.md)
