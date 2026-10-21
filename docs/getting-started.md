<!-- agent-hint: The quickstart must work verbatim. If you change a config key, an extra, the CLI flags (evidencia/__main__.py) or the response shape, run these steps again and update the commands and the sample output. The sample responses are from the live run in knowledge/wiki/audits/kev-rag/evidencia/live-v2/: AMALIA Q4_K_M in llama.cpp, Jevásio-0.8B-v2 on CPU, hybrid retrieval. -->

# Getting started

This page installs evidencia from a checkout, starts it with AMALIA and the default decision model, and asks three questions: one that a document answers, one that it does not, and one for a public source.

For a guided install with every step explained, refer to [Step-by-step setup](setup.md).

## Requirements

- Python 3.11 or later, and [uv](https://docs.astral.sh/uv/).
- About 3 GB of disk space in the Hugging Face cache, for the decision model and its base model.
- A chat model that writes the answers. This page uses AMALIA, which needs an NVIDIA GPU with 6 GB of memory and 5.2 GB of disk space. To use Claude or another hosted model instead, refer to [Chat models](generators.md).

## Install

```bash
git clone https://github.com/TigreGotico/evidencia
cd evidencia
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,hybrid,mcp]"
```

| Extra | Adds |
| --- | --- |
| `local` | Runs the decision model in the same process (torch, transformers, peft) |
| `hybrid` | Embeddings for hybrid retrieval (model2vec) |
| `mcp` | The MCP server for agents |
| `generators` | Chat engine plugins, loaded by their plugin name, for engines that have no preset |
| `test` | pytest, httpx and mcp |

Without `local`, evidencia sends the gate questions to a decision server over HTTP. Refer to [Decision models](decision-models.md).

## Start AMALIA

```bash
podman run --rm -p 127.0.0.1:8000:8000 --device nvidia.com/gpu=all \
  -v llama-models:/models -e LLAMA_CACHE=/models \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  -hf duarteocarmo/AMALIA-9B-0626-DPO-GGUF:Q4_K_M --alias amalia \
  --host 0.0.0.0 --port 8000 --ctx-size 4096
```

The first start downloads the model to the `llama-models` volume. AMALIA is ready when the log says `listening on http://0.0.0.0:8000`. Docker takes the same arguments, with `--gpus all` in place of `--device nvidia.com/gpu=all`.

## Configure

Write `evidencia.toml`:

```toml
generator = "amalia"
data_dir = "collections"
sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]
embedding_model = "minishlab/potion-multilingual-128M"

[decision]
backend = "local"
device = "cpu"
```

| Setting | Meaning |
| --- | --- |
| `generator = "amalia"` | The chat model preset. It calls `http://127.0.0.1:8000/v1` with the model name `amalia` |
| `data_dir` | The folder for collections. They persist across restarts |
| `sources` | The public sources that a request can name |
| `embedding_model` | Turns on hybrid retrieval: BM25 and embeddings together |
| `decision.backend = "local"` | Runs the decision model, Jevásio-0.8B-v2, inside the service |
| `decision.device = "cpu"` | Keeps the decision model off the GPU, because AMALIA uses its memory |

Put top-level settings above the first `[table]` line. A setting below `[decision]` belongs to that table.

## Start the service

```bash
.venv/bin/evidencia serve --config evidencia.toml --port 8080
```

The service listens on `127.0.0.1`. It is ready when the log says `Uvicorn running on http://127.0.0.1:8080`. The first gate question downloads the decision model and its base model, Qwen3.5-0.8B-Base, to the Hugging Face cache. The first request is slow for that reason.

## Add a document

```bash
curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "lisboa.txt", "text": "Lisboa é a capital de Portugal.\n\nO Porto fica no norte."}'
```

```json
{"collection": "docs", "source": "lisboa.txt", "passages": 2, "total": 2}
```

The collection `docs` did not exist, so evidencia made it. The document has two paragraphs, so it gives two passages.

## Ask a question that the document answers

```bash
curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Qual é a capital de Portugal?"}'
```

The response has the answer and the result of each gate (shortened):

```json
{
  "answer": "Lisboa é a capital de Portugal [1].",
  "cited": [1],
  "abstained_at": null,
  "gates": "applied",
  "evidence": [{"text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt", "origin": "docs", "url": "", "p_select": 0.9974}],
  "sufficient": {"p": 0.9975, "threshold": 0.5, "passed": true},
  "grounded": {"p": 0.9967, "threshold": 0.5, "passed": true},
  "sources": [{"name": "docs", "kind": "collection", "candidates": 2, "duplicates": 0, "error": null}]
}
```

`[1]` cites the first item in `evidence`. `cited` lists the numbers that the answer uses.

## Ask a question that the document does not answer

```bash
curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Quantos habitantes tem Lisboa?"}'
```

The response (shortened):

```json
{
  "answer": null,
  "abstained_at": "sufficient",
  "message": "Não tenho informação suficiente para responder a essa pergunta.",
  "sufficient": {"p": 0.1284, "threshold": 0.5, "passed": false}
}
```

The passage about Lisboa is related, but it does not give the population. The sufficiency gate stops the answer before the chat model runs. Show `message` to the user.

## Ask a public source

Add `pt.wikipedia` and `wikidata` to the same question:

```bash
curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "sources": ["wikidata", "pt.wikipedia"], "question": "Quantos habitantes tem Lisboa?"}'
```

The response, shortened to the first item of `evidence`:

```json
{
  "answer": "Lisboa tem uma população estimada de 548 703 habitantes em 2022 [1].",
  "evidence": [{"text": "Lisboa Lisboa é a capital e maior cidade de Portugal, com uma população estimada de 548 703 habitantes em 2022 ...",
                "source": "Lisboa", "origin": "pt.wikipedia", "url": "https://pt.wikipedia.org/wiki/Lisboa", "p_select": 0.8283}],
  "sufficient": {"p": 0.9534, "threshold": 0.5, "passed": true},
  "grounded": {"p": 0.9533, "threshold": 0.5, "passed": true}
}
```

Now the evidence is enough. Each passage from a source has the `url` of its page. Refer to [Public sources](sources.md).

## Open the demo page

Open <http://127.0.0.1:8080/> in a browser. The page asks the same questions and shows each gate decide. Refer to [Demo page](demo.md).

## Next steps

- Set an API key before other hosts can reach the service. Refer to [Deployment](deployment.md).
- Let an agent use evidencia as a tool. Refer to [Agents](agents.md).
- Measure the thresholds on your own questions. Refer to [Evaluation](evaluation.md).

---
[← Step-by-step setup](setup.md) · [Home](index.md) · [Gates →](gates.md)
