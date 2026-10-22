<!-- agent-hint: Backed by evidencia/index.py (chunk, _html, _markdown, _bounded, MAX_PASSAGE_CHARS, Collection, RRF_K), evidencia/store.py, evidencia/embeddings.py, Evidencia._novel in evidencia/pipeline.py, and the ingest routes in evidencia/api.py. The URL-ingest security statements must stay true: no redirects, a byte limit, off by default. HTML text extraction must never fetch or open anything. -->

# Documents

A collection is a named set of documents. evidencia splits each document into passages and indexes the passages with BM25, or with BM25 and embeddings together.

## Persistence

Set `data_dir` to keep collections on disk:

```toml
data_dir = "collections"
```

evidencia writes each collection to one JSON file in that folder, after each change. It loads every file when it starts, so the collections are there after a restart. Deleting a collection deletes its file. Without `data_dir`, collections are in memory and are lost when the service stops.

A relative `data_dir` is relative to the folder where the service starts.

With hybrid retrieval, evidencia also writes the embeddings of each collection to `<name>.vectors.npz`, next to its JSON file. The file names the embedder that made them and the passages they belong to. At start, evidencia loads the stored embeddings and does not embed the passages again. It embeds a collection again when the embedder or its settings changed, or when the file does not match the passages, and then saves the new embeddings for the next start. A key or token in `embedder_config` is not part of the embedder's settings, so a new key does not embed the collections again.

## Formats

| Format | How evidencia splits it |
| --- | --- |
| `text` | One passage per paragraph. A blank line ends a paragraph |
| `markdown` | One passage per paragraph. Each passage starts with the heading that it is under |
| `html` | The visible text, with a paragraph break at each block element (`p`, `div`, `li`, `h1` and others). The content of `script`, `style`, `noscript`, `template` and `head` is not used |
| `pdf` | The text of the PDF, one passage per paragraph. Send a PDF as a file upload or as a URL, not as JSON text |

A passage is never longer than 1500 characters. evidencia splits a longer paragraph between words, so one paragraph cannot fill the decision model's context.

Document content is always treated as text. A document that contains a URL or a file path is indexed as that text. evidencia does not open or fetch it.

## Sources

Each document has a `source`, a name that you choose. A request with a known `source` replaces that document. Each passage has an `id` derived from its source, its position and its text.

In Python, `add_document()` also takes a `url`, the address of the original. Each passage of the document keeps it, and the response shows it next to the evidence. Documents added over HTTP have an empty `url`.

When two sources contain the same text, a search returns a passage for each source.

## Adding documents

Send text as JSON:

```bash
curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "notas.md", "format": "markdown", "text": "# Lisboa\n\nLisboa é a capital de Portugal."}'
```

Upload a file as multipart form data:

```bash
curl -s localhost:8080/v1/collections/docs/files -F file=@relatorio.pdf
```

evidencia finds the format of an upload from its extension: `.txt`, `.md`, `.markdown`, `.html`, `.htm` or `.pdf`. For another extension, add the form field `format`. The form field `source` sets the source name. The default source name is the file name. A text upload must be UTF-8.

## Size limit

`max_document_bytes` sets the largest document, in bytes. The default is 20,000,000. evidencia counts the bytes before it decodes the text. A larger document returns 413, and the collection does not change. A document with no text returns 422, and a collection that did not exist is not made.

## URL ingestion

```bash
curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "pagina", "format": "html", "url": "https://example.org/pagina.html"}'
```

URL ingestion is off by default. Set `allow_url_ingest = true` to turn it on. When it is on:

- evidencia does not follow redirects. A redirect returns 422. Give the final URL.
- The download stops at `max_document_bytes` and returns 413.
- A fetch that fails returns 502. The cause goes to the service log, not to the client.
- evidencia decodes the text with the character set that the server declares. When the server declares none, evidencia uses UTF-8.
- A URL can also point to a PDF. Set `format` to `pdf`.

The service can reach every host that it can see on its network, including internal hosts. Turn URL ingestion on only when you trust every client.

## Retrieval

By default, BM25 ranks the passages for each question. A passage that shares no word with the question has a score of zero and is not returned.

Set `embedding_model` to turn on hybrid retrieval:

```toml
embedding_model = "minishlab/potion-multilingual-128M"
```

evidencia then ranks the passages twice: by BM25, and by the similarity of their embeddings to the question. It adds the two rankings with reciprocal-rank fusion. A passage in rank *r* of one ranking adds `1 / (60 + r)` to its score. Hybrid retrieval finds a passage that has the meaning of the question in other words, for example "população" for "habitantes". Every passage gets a score, so hybrid retrieval always returns candidates when the collection has passages.

The `hybrid` extra installs model2vec. The model is a static embedding model of about 500 MB. It runs on the CPU and needs no GPU. It downloads to the Hugging Face cache, and it loads when the service starts. A model that cannot load stops the start.

An added document is embedded once, and only its own passages are embedded. Questions to the collection continue while a document is embedded. They use the passages from before the document, until the new index is ready.

### Embedding plugins

`embedder` names a text embeddings plugin instead of the built-in model2vec model. `embedder_config` holds the plugin's settings:

```toml
embedder = "ovos-m2v-embeddings-plugin"

[embedder_config]
model = "minishlab/potion-multilingual-128M"
```

`ovos-m2v-embeddings-plugin` comes with the `ovos-m2v-pipeline` package and runs a model2vec model in the same process. Install the package of the plugin in the same environment as evidencia. At start, evidencia embeds one text with the plugin. A plugin name that is not installed, or a plugin that cannot embed, stops the start. Set `embedder` or `embedding_model`, not both. evidencia sends each document to the plugin as one batch, and it normalises the vectors that come back.

The [gates](gates.md) then decide which passages to use.

### Vector databases

`vector_db` names an embeddings database plugin that keeps the embeddings instead of evidencia's memory and vector files. `vector_db_config` holds its settings:

```toml
embedding_model = "minishlab/potion-multilingual-128M"
data_dir = "collections"
vector_db = "ovos-qdrant-embeddings-plugin"
```

| Plugin | Package | Default storage |
| --- | --- | --- |
| `ovos-chromadb-embeddings-plugin` | `ovos-chromadb-embeddings-plugin` | A folder on disk, or a chromadb server with `host` and `port` |
| `ovos-qdrant-embeddings-plugin` | `ovos-qdrant-embeddings-plugin` | A folder on disk, or a qdrant server with `host`, `port` and `api_key` |

Without `host` or `path` in `vector_db_config`, the database keeps its files in `<data_dir>/vectors`. Without `data_dir`, set one of them. evidencia sets `vector_size` from the embedder, unless the configuration sets it.

Each collection has its own database collection. Its name comes from the collection name, the embedder and a random id that evidencia writes to `<data_dir>/instance.id` at the first start. A new embedder therefore starts with an empty database collection and never mixes vectors of two models. Two services that share a database server keep their collections apart, also when their `data_dir` has the same path in two containers. A `data_dir` that moves keeps its id and its database collections. Without `data_dir`, the passages do not persist, and each start uses new database collections.

At start, evidencia asks the database which passages it holds, and embeds only the passages that it does not hold. When the database holds more rows than there are passages, evidencia rebuilds the database collection from the rows of its passages, without embedding them again. Such rows come from a write that stopped before evidencia saved the passages.

A question uses the 200 nearest passages from the database, and fuses them with the BM25 ranking. Up to 200 passages, the ranking is the same as with the vectors in memory. In a larger collection, a passage below the 200 nearest gets no embedding score, so the ranking can differ. Dropping a collection drops its database collection.

The JSON files in `data_dir` still hold the passages. BM25 needs them.

## Near-duplicates

Copies of a page, quotes and reposts give many passages with almost the same text. Each one costs a select question, and they can fill the evidence with one fact.

Set `dedupe_threshold` to drop them:

```toml
embedding_model = "minishlab/potion-multilingual-128M"
dedupe_threshold = 0.95
```

evidencia compares the embedding of each candidate with the candidates before it in the same question. It drops a candidate when the cosine similarity is equal to or more than the threshold. The select gate does not score a dropped candidate, and `duplicates` in the `sources` report counts it. The setting needs `embedding_model` or `embedder`, and its value must be more than 0 and not more than 1. A value of 0.95 drops near-identical text only.

---
[← Chat models](generators.md) · [Home](index.md) · [Public sources →](sources.md)
