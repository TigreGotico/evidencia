<!-- agent-hint: Backed by evidencia/sources.py (CATALOG, RemoteSource, MediaWiki, Wikidata, DicionarioAberto, ArquivoPt, GovPt, GeoApiPt, Ipma, DadosGov, Vies, Gleif, DnsPt, Dgeg, tails, valid_nif, build_sources and its CloudflareSession from unblock_requests) and Evidencia.targets/_search in evidencia/pipeline.py. Licence and terms statements were read from each service (probe captures in knowledge/wiki/audits/kev-rag/probes/); never soften the Arquivo.pt non-commercial terms. gov.pt, geoapi.pt, ipma, dados.gov.pt, vies, dns.pt and dgeg have commercial = False because no reuse licence for them was read, not because a licence forbids it; keep that wording. The GLEIF CC0 statement is from https://www.gleif.org/en/meta/lei-data-terms-of-use. The gov.pt search is the portal's own front-end endpoint with no published contract; the live captures are in knowledge/wiki/audits/kev-rag/evidencia/live-gov/. The source table must match CATALOG. -->

# Public sources

evidencia can search fourteen public services at question time, next to your own collections or instead of them. Six are open knowledge bases. Eight give data about Portuguese public services, places, companies, domains, weather and fuel prices. Their passages go through the same gates as your documents. Each passage keeps a link to the page that it came from.

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
| `gov.pt` | [gov.pt](https://www.gov.pt) | Public services for citizens and companies, practical guides, news, public bodies, and service desks with address and opening hours | gov.pt terms of use | Not cleared. No reuse licence is stated |
| `geoapi.pt` | [GEO API PT](https://geoapi.pt) | Municipalities: district, area, Censos 2011 and 2021, town hall contacts, parishes. Postal codes | Data from DGT (CAOP), INE (Censos) and CTT (postal codes) | Not cleared |
| `ipma` | [IPMA](https://www.ipma.pt) | Five-day weather forecasts and weather warnings for the district capitals and the islands | IPMA open data terms | Not cleared. IPMA asks each user to send an email to webmaster@ipma.pt that names the use |
| `dados.gov.pt` | [dados.gov.pt](https://dados.gov.pt) | Open datasets of public bodies: what each holds, its publisher, licence and formats | Each dataset has its own licence | Not cleared for the catalogue. Each passage names the licence of its dataset |
| `vies` | [VIES](https://ec.europa.eu/taxation_customs/vies/) | The name and address registered for a Portuguese NIF or NIPC | European Commission VIES terms | Not cleared |
| `gleif` | [GLEIF](https://www.gleif.org) | Portuguese companies and other legal entities with an LEI: legal name, registered address, NIPC, date of creation, status | CC0 1.0 | Yes |
| `dns.pt` | [DNS.pt](https://www.dns.pt) | The holder, dates and name servers of a .pt domain | DNS.pt WHOIS terms | Not cleared |
| `dgeg` | [DGEG - Preços dos Combustíveis](https://precoscombustiveis.dgeg.gov.pt) | Pump prices at the filling stations of a mainland municipality or district, cheapest first | DGEG terms | Not cleared |

`GET /v1/sources` returns this table, with a field that says which sources the service has enabled.

Things to know about each source:

- **pt.wikipedia** mixes European and Brazilian Portuguese. Each article keeps the spelling of its first authors.
- **wikidata** gives facts, not prose. evidencia writes each fact as a line such as `Portugal, capital: Lisboa.`.
- **dicionario-aberto** is based on an old dictionary, so its spelling is from before the 1990 spelling reform. Use it for questions about the meaning of a word.
- **arquivo.pt** gives the text of whole pages, including menus and other page furniture. The select gate drops most of that text. evidencia reads the page text only from addresses on arquivo.pt.
- **gov.pt** gives the short description of each service, guide or news item that the portal search finds. It does not give the full page. For a service desk, such as an IEFP employment centre or a Loja do Cidadão, it gives the address and the opening hours. For a public body, such as a Câmara Municipal, it gives the area, the district and the web site. A news item has its publication date in its title, because news text often says "hoje" or "este ano". evidencia uses the search of the portal web page. The portal publishes no contract for it, so it can change without notice.
- **geoapi.pt** answers only for a municipality, a parish or a postal code in the question, such as `Évora`, `Junta de Freguesia de Canaviais` or `1000-001`. For a municipality, it gives the district, the area, the Censos 2011 and 2021 counts, the town hall contacts and the parishes. For a parish, it gives the municipality, the area, the Censos 2021 count and the contacts of the parish council. A parish name that several municipalities have adds nothing. evidencia writes each fact as a sentence, such as `Nos Censos de 2021, o município de Évora tinha 53 577 habitantes`. A name that is not a place adds nothing.
- **ipma** answers only for the 35 places that IPMA forecasts: the district capitals, the islands and some other towns. Each day of the forecast is one passage. The passage says "hoje", "amanhã" or "depois de amanhã" from the date of the server. The warnings passage names only yellow, orange and red warnings, or says that there are none.
- **dados.gov.pt** tells where data is, not what the data says. Use it for questions such as "Há dados abertos sobre a qualidade do ar?".
- **vies** answers only for a NIF or NIPC in the question with a correct check digit. VIES knows only numbers registered for VAT. For a sole trader, the name is a person's name.
- **gleif** searches the names in the question, and a NIPC in the question. When a whole name finds no entity, it searches the end of the name: "NIPC da EDP" finds EDP. It knows only entities that have an LEI: most large companies, banks, funds and many public bodies, but few small companies.
- **dns.pt** asks the .pt WHOIS server over TCP port 43, not over HTTP. It reads the registered domain of a host: `www.sapo.pt` is `sapo.pt`, and `loja.empresa.com.pt` is `empresa.com.pt`. The holder of a domain can be a private person.
- **dgeg** answers only when the question names a fuel and a mainland municipality or district, such as "Onde está o gasóleo mais barato em Évora?". "gasóleo" means Gasóleo simples, "gasolina" means Gasolina simples 95, and "GPL" means GPL Auto, unless the question names the full type. A district shares its name with a municipality, so "Lisboa" means the municipality, and "distrito de Lisboa" means the district. The first passage names the cheapest station. DGEG has no prices for Madeira and the Azores.

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

1. evidencia finds the names in the question: runs of capitalised words, such as "Torre de Belém". For the wikis, it fetches the page with that title first. For Wikidata, it fetches the item of the Wikipédia article with that title. For geoapi.pt, IPMA and DGEG, a name can also be the end of a longer name: "Câmara Municipal de Vila Nova de Gaia" finds the municipality Vila Nova de Gaia. GLEIF searches the end of a name only when the whole name finds nothing.
2. evidencia removes the question words and other common words from the question, and sends the rest as a keyword search. "Qual é a capital de Portugal?" becomes `capital portugal`. A keyword search finds only pages that contain every word, so the named page can be missing from it. For gov.pt, evidencia also searches each run of adjacent words, longest first, up to 6 searches, because a question often holds a word that no page has: "Como peço o subsídio de desemprego?" searches `peço subsídio desemprego`, `peço subsídio`, `subsídio desemprego`, `peço`, `subsídio` and `desemprego`.
3. The source returns a fixed number of documents: 4 pages for the wikis, Wikidata and Arquivo.pt; the entries for up to 3 words of the question for Dicionário Aberto; up to 6 results for each gov.pt search; 5 datasets for dados.gov.pt; up to 3 places or postal codes for geoapi.pt; up to 2 places for IPMA; up to 3 numbers for VIES; up to 4 entities for each GLEIF search; up to 2 domains for DNS.pt; and the 3 cheapest stations for DGEG, with one more passage that names the cheapest.
4. evidencia splits the documents into passages and ranks the passages with the same retrieval as a local collection. It drops a passage that repeats the text of a passage ranked above it.
5. The best passages from each source go to the select gate, interleaved by rank with the passages from the collection. The scores of different sources are not compared.

A wider round asks for more passages from the same documents, so it does not search the service again. evidencia keeps the documents for each question for 10 minutes.

## Sites that refuse a client

evidencia sends each request to a public source with the TLS handshake of a browser (curl_cffi), so a site that refuses plain scripts still answers. A site behind a Cloudflare challenge needs a browser to solve it. Run [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) and give its address:

```toml
flaresolverr_url = "http://127.0.0.1:8191"
```

With `flaresolverr_url`, evidencia sends a GET that a site refuses again through FlareSolverr. A request that the site answers stays fast, because it does not go through the browser. A solve can take up to `source_timeout` seconds. FlareSolverr solves only GET requests. A source that searches with a POST, such as gov.pt, does not go through it.

`transport` changes how every request is sent:

| `transport` | Sends each request |
| --- | --- |
| not set | With curl_cffi, and through FlareSolverr when a site refuses a GET and `flaresolverr_url` is set |
| `curl_cffi` | The same as not set |
| `requests` | With the plain `requests` library |
| `flaresolverr` | Each GET through FlareSolverr. Slow: use it only when every source needs it |
| `browserless` | Each GET through the [browserless](https://www.browserless.io) server at `browserless_url`. Other requests use `requests` |

A challenge page is the choice of the site. Read the terms of a site before you send solved requests to it, most of all in a product.

evidencia never answers from an archived copy of a page, because a passage must give the current text of the source. A challenge that no solver passes makes the source fail, as in the next section.

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
- evidencia does not clear `gov.pt`, `geoapi.pt`, `ipma`, `dados.gov.pt`, `vies`, `dns.pt` or `dgeg` for commercial use. Read the terms of each service before you use it in a product. For `dados.gov.pt`, the licence of each dataset applies to the data in it.

---
[← Documents](documents.md) · [Home](index.md) · [Agents →](agents.md)
