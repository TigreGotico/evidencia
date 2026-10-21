<!-- agent-hint: Backed by evidencia/index.py (chunk, _html, _markdown, _bounded, MAX_PASSAGE_CHARS, Collection) and the ingest routes in evidencia/api.py. The URL-ingest security statements must stay true: no redirects, a byte limit, off by default. HTML text extraction must never fetch or open anything. -->

# Documents

A collection is a named set of documents. evidencia splits each document into passages and indexes the passages with BM25. Collections are in memory. They are lost when the service stops.

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

BM25 ranks the passages for each question. A passage that shares no word with the question has a score of zero and is not returned. The [gates](gates.md) then decide which passages to use.

---
[← Decision models](decision-models.md) · [Home](index.md) · [HTTP API →](api.md)
