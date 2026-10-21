<!-- agent-hint: The quickstart must work verbatim. If you change a config key, an extra, the CLI flags (evidencia/__main__.py) or the response shape, run these steps again and update the commands and the sample output. The sample responses come from the default model in bf16 on CUDA; the answer text came from a fixed-reply chat engine. -->

# Getting started

This page installs evidencia from a checkout, starts the service with the default decision model, and asks two questions: one that the document answers and one that it does not.

## Requirements

- Python 3.11 or later, and [uv](https://docs.astral.sh/uv/).
- About 3 GB of disk space in the Hugging Face cache, for the decision model and its base model.
- A GPU is optional. The decision model also runs on a CPU, more slowly.
- A chat model behind an OpenAI-compatible API, for example a llama.cpp server on `127.0.0.1:8000`. evidencia uses it to write the answers.

## Install

Clone the repository and install it with the `local` and `generators` extras:

```bash
git clone https://github.com/TigreGotico/evidencia
cd evidencia
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,generators]"
```

| Extra | Adds |
| --- | --- |
| `local` | Runs the decision model in the same process (torch, transformers, peft) |
| `generators` | Chat engines that write the answers: an OpenAI-compatible API client and a local GGUF runner. The GGUF runner needs a C compiler to build |
| `test` | pytest and httpx |

Without `local`, evidencia sends the gate questions to a decision server over HTTP. Refer to [Decision models](decision-models.md).

## Configure

Write `evidencia.toml`:

```toml
generator = "ovos-chat-openai-plugin"

[generator_config]
api_url = "http://127.0.0.1:8000/v1"
model = "local-model"

[decision]
backend = "local"
```

`generator` is the name of the chat engine. `generator_config` goes to that engine. `decision.backend = "local"` runs the default decision model, Jevásio-0.8B-v2, inside the service.

## Start the service

```bash
.venv/bin/evidencia serve --config evidencia.toml --port 8080
```

The service listens on `127.0.0.1`. The first gate question downloads the decision model and its base model, Qwen3.5-0.8B-Base, to the Hugging Face cache. The first request is slow for that reason.

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
  "answer": "Lisboa.",
  "abstained_at": null,
  "gates": "applied",
  "evidence": [{"text": "Lisboa é a capital de Portugal.", "source": "lisboa.txt", "p_select": 0.9974}],
  "sufficient": {"p": 0.9973, "threshold": 0.5, "passed": true},
  "grounded": {"p": 0.9959, "threshold": 0.5, "passed": true}
}
```

## Ask a question that the document does not answer

```bash
curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Quantos habitantes tem Lisboa?"}'
```

```json
{
  "answer": null,
  "abstained_at": "sufficient",
  "message": "Não tenho informação suficiente para responder a essa pergunta.",
  "sufficient": {"p": 0.1305, "threshold": 0.5, "passed": false}
}
```

The passage about Lisboa is related, but it does not give the population. The sufficiency gate stops the answer before the chat model runs. Show `message` to the user.

## Next steps

- Set an API key before other hosts can reach the service. Refer to [Deployment](deployment.md).
- Measure the thresholds on your own questions. Refer to [Evaluation](evaluation.md).

---
[Home](index.md) · [Gates →](gates.md)
