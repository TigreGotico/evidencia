<!-- agent-hint: Front page of the repository. Keep it short: what evidencia does, the licence of the default model, install, a quickstart and links. Detail belongs in docs/; when a fact here changes, change the matching docs page too. The sample answers are from the live run in knowledge/wiki/audits/kev-rag/evidencia/live-v2/ (AMALIA Q4_K_M, Jevásio-0.8B-v2 on CPU). -->

# evidencia

Decision gates for retrieval-augmented generation: passage selection, evidence sufficiency and answer grounding, with calibrated probabilities.

evidencia answers questions in European Portuguese (pt-PT) from your documents and from public Portuguese sources. It answers only when a decision model says that the evidence supports the answer. The decision model answers three yes/no questions:

1. **Select.** Does this passage help to answer the question?
2. **Sufficient.** Is the selected evidence enough to answer? If not, evidencia retrieves more passages and asks again.
3. **Grounded.** Is every fact in the generated answer supported by the evidence?

When a gate fails, evidencia does not answer. Each response gives the probability and the threshold of each gate that ran, the evidence with its links, and the passage numbers that the answer cites.

```text
Pergunta:  Quando foi construída a Torre de Belém?        (pt.wikipedia + arquivo.pt)
Resposta:  A Torre de Belém foi construída no século XVI [4]. A construção começou em 1514 [1].
           sufficient 0.934 · grounded 0.966 · 4 of 16 passages kept

Pergunta:  Qual é o telefone da Câmara Municipal de Vila Nova de Gaia?    (geoapi.pt)
Resposta:  O telefone da Câmara Municipal de Vila Nova de Gaia é 223742400 [1].
           sufficient 0.995 · grounded 0.995

Pergunta:  Quantos habitantes tem Lisboa?                  (a document that does not say)
Resposta:  Não tenho informação suficiente para responder a essa pergunta.
           abstained at sufficient, p 0.128
```

## What it has

- **Your documents.** Text, Markdown, HTML and PDF, in named collections that persist on disk. Retrieval is BM25, or BM25 and embeddings together.
- **Public sources.** Wikipédia, Wikisource, Wikcionário, Wikidata, Dicionário Aberto and Arquivo.pt. Public services and service desks such as employment centres on gov.pt, municipality and parish facts and postal codes from geoapi.pt, IPMA weather forecasts, warnings, earthquakes, sea state and UV index, the open datasets on dados.gov.pt and the Cascais, Águeda, Porto and SNS catalogues, companies by NIF or name from VIES and GLEIF, .pt domains from DNS.pt, fuel prices from DGEG, Carris Metropolitana bus lines and alerts, IEFP job offers, and RTP headlines. All are searched at question time with no API key.
- **A chat model of your choice.** [AMALIA](https://huggingface.co/duarteocarmo/AMALIA-9B-0626-DPO-GGUF), a European Portuguese model, on your own GPU. Or Claude, OpenAI, OpenRouter, OpenCode, Ollama, or any OpenAI-compatible server.
- **A demo page** that shows each gate decide, live.
- **An MCP server**, so that Claude Desktop, Claude Code or OpenCode can use evidencia as a tool.
- **An HTTP API**, with a streaming endpoint, for your own programs.

evidencia is an in-house research service. The default decision model, [Jevásio-0.8B-v2](https://huggingface.co/TigreGotico/jevasio-0.8b-v2), is licensed CC BY-NC-SA 4.0: research and non-commercial use only. Refer to [Decision models](docs/decision-models.md) for the alternatives.

## Install

```bash
git clone https://github.com/TigreGotico/evidencia
cd evidencia
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,hybrid,mcp]"
```

New to the terminal? [Step-by-step setup](docs/setup.md) does every step with you, from installing uv to connecting Claude Desktop.

## Quickstart

Start AMALIA on a GPU with 6 GB or more. The first start downloads 5.2 GB:

```bash
podman run --rm -p 127.0.0.1:8000:8000 --device nvidia.com/gpu=all \
  -v llama-models:/models -e LLAMA_CACHE=/models \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  -hf duarteocarmo/AMALIA-9B-0626-DPO-GGUF:Q4_K_M --alias amalia \
  --host 0.0.0.0 --port 8000 --ctx-size 4096
```

Write `evidencia.toml`:

```toml
generator = "amalia"
data_dir = "collections"
sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]

[decision]
backend = "local"
device = "cpu"
```

Start the service, add a document and ask a question:

```bash
.venv/bin/evidencia serve --config evidencia.toml

curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "lisboa.txt", "text": "Lisboa é a capital de Portugal.\n\nO Porto fica no norte."}'

curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Qual é a capital de Portugal?"}'
```

Then open <http://127.0.0.1:8080/> for the demo page. The first question downloads the decision model, about 3 GB. [Getting started](docs/getting-started.md) explains each step and the response.

No GPU? Use Claude or another hosted model instead of AMALIA: refer to [Chat models](docs/generators.md).

## Documentation

| Page | Contents |
| --- | --- |
| [Step-by-step setup](docs/setup.md) | A guided install for readers who do not program |
| [Getting started](docs/getting-started.md) | Install, start the service, ask a first question |
| [Gates](docs/gates.md) | How the gates decide, the retrieval rounds, and what each abstention means |
| [Decision models](docs/decision-models.md) | The models, their licences, and the HTTP and in-process backends |
| [Chat models](docs/generators.md) | AMALIA, Claude, OpenAI, OpenRouter, OpenCode, Ollama and API keys |
| [Documents](docs/documents.md) | Formats, passages, persistence, hybrid retrieval and near-duplicates |
| [Public sources](docs/sources.md) | The twenty-one public sources, their licences, and how they are searched |
| [Agents](docs/agents.md) | Connect Claude Desktop, Claude Code or OpenCode through MCP |
| [Demo page](docs/demo.md) | The web page that shows each gate decide |
| [HTTP API](docs/api.md) | Every endpoint, with request and response examples |
| [Vendor APIs](docs/vendor-apis.md) | Chat through the OpenAI, Ollama, Anthropic and Gemini APIs, and the OpenAI vector-store search |
| [Configuration](docs/configuration.md) | Every setting and environment variable |
| [Deployment](docs/deployment.md) | Every deployment option, containers, OVOS personas, authentication and health checks |
| [Observability](docs/observability.md) | Request ids, text and JSON logs, and the Prometheus metrics |
| [Python](docs/python.md) | Use evidencia as a library |
| [Evaluation](docs/evaluation.md) | The pt-PT gate cases and the measurement scripts |
| [Troubleshooting](docs/troubleshooting.md) | Each error message and its fix |

## Licence

evidencia is licensed under the Apache License 2.0. The decision model, the chat model and each public source have their own licence, and that licence applies to their use. Arquivo.pt forbids commercial use. evidencia does not clear gov.pt, geoapi.pt, IPMA, dados.gov.pt, VIES, DNS.pt, DGEG, Carris Metropolitana, RTP, IEFP or the municipal and SNS catalogues for commercial use.

`evidencia/_vendor/kev/` holds four inference modules of [Kev](https://github.com/jaredpalmer/kev) (commit `1f04cfa`), unchanged, under the Apache License 2.0 in that directory.
