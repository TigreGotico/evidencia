<!-- agent-hint: The chat part is served by ovos-persona-server with the ovos-evidencia-chat-plugin handler; the commands and replies were run live (knowledge/wiki/audits/kev-rag/evidencia/round2/persona-server-live/). The search part is vector_store_search in evidencia/api.py, tested with the official openai SDK in test/test_openai_compat.py. Keep the field names exact. -->

# Vendor APIs

A client that speaks the OpenAI, Ollama, Anthropic, Gemini, Cohere, Hugging Face TGI or AWS Bedrock API can ask evidencia with no code of its own. Two parts make this work:

- **Chat.** ovos-persona-server serves those APIs for a persona. A persona whose handler is `ovos-evidencia-chat-plugin` gives the gated answers of an evidencia service.
- **Search.** evidencia itself answers the OpenAI vector store search, with the passages that pass the select gate. OpenAI RAG tools use this route.

## Chat through ovos-persona-server

ovos-persona-server runs next to evidencia and calls it over HTTP. Install it and the evidencia chat plugin in their own environment. The plugin package is `ovos-evidencia-plugin`. Install it from its repository checkout:

```bash
uv venv persona-env
uv pip install --python persona-env/bin/python --prerelease=allow ovos-persona-server ./ovos-evidencia-plugin
```

Write a persona file, `evidencia.json`. Its name is the model name that clients send:

```json
{
  "name": "evidencia",
  "handlers": ["ovos-evidencia-chat-plugin"],
  "ovos-evidencia-chat-plugin": {
    "url": "http://127.0.0.1:8080",
    "key": "<api_key>",
    "collection": "docs",
    "abstain_message": true
  }
}
```

| Plugin setting | Value |
| --- | --- |
| `url` | The evidencia service root |
| `key` | The evidencia `api_key`, when the service sets one |
| `collection`, `sources` | What to search. Give a collection, at least one source, or both |
| `abstain_message` | `true` replies with the abstain text of the locale when evidencia abstains. `false` (default) replies with nothing, so the persona tries its next handler |
| `citations` | `true` keeps the `[1]` marks. Default `false` |
| `timeout` | Seconds to wait for each reply. Default 60 |
| `top_k`, `n_candidates`, `max_rounds`, `select_threshold`, `sufficient_threshold`, `grounded_threshold` | Sent with each question, as in `/v1/answer` |

The plugin answers only a question in the language of the evidencia service. ovos-persona-server takes the language of each request from the OVOS configuration, and the default is `en-US`. Set it in `~/.config/mycroft/mycroft.conf`:

```json
{"lang": "pt-PT"}
```

Without it, every question gets no answer, and ovos-persona-server returns 422.

Start the server:

```bash
persona-env/bin/ovos-persona-server --persona evidencia.json --port 8337
```

Each API has its own path prefix:

| API | Base URL |
| --- | --- |
| OpenAI | `http://<host>:8337/openai/v1` |
| Ollama | `http://<host>:8337/ollama/api` |
| Anthropic | `http://<host>:8337/anthropic/v1` |
| Gemini | `http://<host>:8337/gemini/v1beta/models` |
| Cohere | `http://<host>:8337/cohere/v1` |
| Hugging Face TGI | `http://<host>:8337/tgi` |
| AWS Bedrock | `http://<host>:8337/bedrock/model` |

With the openai SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8337/openai/v1", api_key="unused")
reply = client.chat.completions.create(
    model="evidencia", messages=[{"role": "user", "content": "Qual é a capital de Portugal?"}])
print(reply.choices[0].message.content)
```

With the Ollama API:

```bash
curl http://127.0.0.1:8337/ollama/api/chat -d '{"model": "evidencia", "stream": false,
  "messages": [{"role": "user", "content": "Qual é a capital de Portugal?"}]}'
```

The reply is the gated answer, or the abstain text when evidencia abstains and `abstain_message` is true. A stream sends the same text. When evidencia abstains and no other handler answers, ovos-persona-server returns 422.

To fall back to another engine when evidencia abstains, leave `abstain_message` false and list the other engine after the evidencia handler. Then evidencia answers what its evidence supports, and the other engine answers the rest. Such an answer is not checked by any gate.

One server can serve several personas, for example one per collection. Give `--persona` once for each file. The reply does not hold the gates or the evidence. To show them, call `/v1/answer` of evidencia. Refer to [HTTP API](api.md#answer).

## Vector store search

`POST /v1/vector_stores/{collection}/search` of evidencia answers the OpenAI vector store search. Use the evidencia service as the base URL, `http://<host>:8080/v1`, and the evidencia `api_key` as the API key.

The vector store is a collection. The body holds `query`, a string or a list of strings, and `max_num_results`, from 1 to 50 (default 10). It can also hold `sources` and the per-request settings. evidencia runs the select and sufficient gates, as `/v1/context` does:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="<api_key>")
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

evidencia ignores `filters`, `ranking_options` and `rewrite_query`. The results are the evidence passages, at most `max_num_results`. When the evidence is not enough to answer, the page has no results, and its `evidencia` field gives `abstained` and `abstained_at`. When the decision backend is unavailable, the search returns 503, also with `fail_open` true, because a search result has no field that says no gate checked it.

`ovos-openai-rag-memory-plugin` works with this route: set its `api_url` to `http://<host>:8080/v1` and its `vector_store_id` to the collection. The other OpenAI routes, such as the vector store file uploads, are not served. Add documents through the [HTTP API](api.md#documents).

| Status | Cause |
| --- | --- |
| 401 | The API key is wrong |
| 404 | The collection does not exist |
| 422 | A setting or the query is not valid |
| 503 | The decision backend is unavailable |

An error body is `{"detail": "<reason>"}`, as on the other routes, and not the OpenAI `{"error": {...}}` shape. Some older clients show only the status for such a body.

---
[← HTTP API](api.md) · [Home](index.md) · [Configuration →](configuration.md)
