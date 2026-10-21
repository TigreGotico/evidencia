<!-- agent-hint: Backed by evidencia/sources.py (CATALOG, RemoteSource, MediaWiki, Wikidata, DicionarioAberto, ArquivoPt) and Evidencia.targets/_search in evidencia/pipeline.py. Licence and terms statements were read from each service (probe captures in knowledge/wiki/audits/kev-rag/probes/); never soften the Arquivo.pt non-commercial terms. The source table must match CATALOG. -->

# Public sources

evidencia can search six public Portuguese services at question time, next to your own collections or instead of them. Their passages go through the same gates as your documents. Each passage keeps a link to the page that it came from.

No source needs an API key.

## The sources

| Name | Service | Gives | Licence | Commercial use |
| --- | --- | --- | --- | --- |
| `pt.wikipedia` | [Wikipédia em português](https://pt.wikipedia.org) | Encyclopedia articles | CC BY-SA 4.0 | Yes, with attribution and share-alike |
| `pt.wikisource` | [Wikisource em português](https://pt.wikisource.org) | Public-domain texts: literature, laws, historical documents | CC BY-SA 4.0 | Yes, with attribution and share-alike |
| `pt.wiktionary` | [Wikcionário](https://pt.wiktionary.org) | Dictionary entries | CC BY-SA 4.0 | Yes, with attribution and share-alike |
| `wikidata` | [Wikidata](https://www.wikidata.org) | Short facts about people, places and things | CC0 1.0 | Yes |
| `dicionario-aberto` | [Dicionário Aberto](https://dicionario-aberto.net) | Word definitions | CC BY-SA 2.5 PT | Yes, with attribution and share-alike |
| `arquivo.pt` | [Arquivo.pt](https://arquivo.pt) | Archived Portuguese web pages since 1996 | Arquivo.pt terms of use | **No**. Research and education only |

`GET /v1/sources` returns this table, with a field that says which sources the service has enabled.

Things to know about each source:

- **pt.wikipedia** mixes European and Brazilian Portuguese. Each article keeps the spelling of its first authors.
- **wikidata** gives facts, not prose. evidencia writes each fact as a line such as `Portugal, capital: Lisboa.`.
- **dicionario-aberto** is based on an old dictionary, so its spelling is from before the 1990 spelling reform. Use it for questions about the meaning of a word.
- **arquivo.pt** gives the text of whole pages, including menus and other page furniture. The select gate drops most of that text. evidencia reads the page text only from addresses on arquivo.pt.

## Turn sources on

The service queries only the sources that its configuration names:

```toml
sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]
```

Or with the environment: `EVIDENCIA_SOURCES=pt.wikipedia,wikidata`.

Wikimedia asks every client to send a User-Agent with contact information. Set yours:

```toml
user_agent = "evidencia (https://your-site.example; you@example.org)"
```

Without contact information, Wikimedia can limit or block the requests.

## Ask a source

Name the sources in the request. A request can name a collection, sources, or both:

```bash
curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"question": "Qual é a capital de Portugal?", "sources": ["pt.wikipedia", "wikidata"]}'
```

A request that names a source the service has not enabled returns 422. The default is no sources, so a request without `sources` searches only its collection.

Each passage in the response says where it came from:

| Field | Meaning |
| --- | --- |
| `origin` | The collection or the source that returned the passage |
| `source` | The document: a file name, or the page title for a remote source |
| `url` | The page to cite. Empty for documents added over HTTP |

## How a source is searched

1. evidencia finds the names in the question: runs of capitalised words, such as "Torre de Belém". For the wikis, it fetches the page with that title first. For Wikidata, it fetches the item of the Wikipédia article with that title.
2. evidencia removes the question words and other common words from the question, and sends the rest as a keyword search. "Qual é a capital de Portugal?" becomes `capital portugal`. A keyword search finds only pages that contain every word, so the named page can be missing from it.
3. The source returns a fixed number of documents: 4 pages for the wikis, Wikidata and Arquivo.pt, and the entries for up to 3 words of the question for Dicionário Aberto.
4. evidencia splits the documents into passages and ranks the passages with the same retrieval as a local collection. It drops a passage that repeats the text of a passage ranked above it.
5. The best passages from each source go to the select gate, interleaved by rank with the passages from the collection. The scores of different sources are not compared.

A wider round asks for more passages from the same documents, so it does not search the service again. evidencia keeps the documents for each question for 10 minutes.

## When a source fails

A source that does not complete a request in `source_timeout` seconds (default 10), or that returns an error, adds no passages. evidencia tries it again in the next round. The other sources and the collection still answer. The response names the failed source:

```json
"sources": [
  {"name": "docs", "kind": "collection", "candidates": 2, "duplicates": 0, "error": null},
  {"name": "wikidata", "kind": "remote", "candidates": 0, "duplicates": 0, "error": "unavailable"}
]
```

The service log has the cause. When the source answers in a later round, `error` is `null`.

A request cannot name a collection that has the name of a source in the same request. It returns 422.

## Licence obligations

The licence of a source applies to the text that you show from it:

- Show the `url` of each passage that an answer uses. This gives the attribution that CC BY-SA asks for.
- An answer that copies text from a CC BY-SA source carries the share-alike terms.
- Do not use `arquivo.pt` in a commercial product. Its terms forbid commercial use.

---
[← Documents](documents.md) · [Home](index.md) · [Agents →](agents.md)
