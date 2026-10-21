<!-- agent-hint: Front page of the repository. Keep it short: what evidencia does, the licence of the default model, install, a quickstart and links. Detail belongs in docs/; when a fact here changes, change the matching docs page too. -->

# evidencia

Decision gates for retrieval-augmented generation: passage selection, evidence sufficiency and answer grounding, with calibrated probabilities.

evidencia answers questions from your documents only when a decision model says that the evidence supports the answer. It retrieves passages with BM25 and asks the model three yes/no questions:

1. **Select.** Does this passage help to answer the question?
2. **Sufficient.** Is the selected evidence enough to answer? If not, evidencia retrieves more passages and asks again.
3. **Grounded.** Is every fact in the generated answer supported by the evidence?

When a gate fails, evidencia does not answer. Each response gives the probability and the threshold of each gate that ran, so you can audit every decision. The gate questions are in European Portuguese (pt-PT).

evidencia is an in-house research service. The default decision model, [Jevásio-0.8B-v2](https://huggingface.co/TigreGotico/jevasio-0.8b-v2), is licensed CC BY-NC-SA 4.0: research and non-commercial use only. Refer to [Decision models](docs/decision-models.md) for the alternatives.

## Install

```bash
git clone https://github.com/TigreGotico/evidencia
cd evidencia
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,generators]"
```

## Quickstart

Write `evidencia.toml`. The chat model that writes the answers can be any OpenAI-compatible server, for example llama.cpp:

```toml
generator = "ovos-chat-openai-plugin"

[generator_config]
api_url = "http://127.0.0.1:8000/v1"
model = "local-model"

[decision]
backend = "local"
```

Start the service, add a document and ask a question:

```bash
.venv/bin/evidencia serve --config evidencia.toml --port 8080

curl -s localhost:8080/v1/collections/docs/documents -H 'content-type: application/json' \
  -d '{"source": "lisboa.txt", "text": "Lisboa é a capital de Portugal.\n\nO Porto fica no norte."}'

curl -s localhost:8080/v1/answer -H 'content-type: application/json' \
  -d '{"collection": "docs", "question": "Qual é a capital de Portugal?"}'
```

The first question downloads the decision model. [Getting started](docs/getting-started.md) explains each step and the response.

## Documentation

| Page | Contents |
| --- | --- |
| [Getting started](docs/getting-started.md) | Install, start the service, ask a first question |
| [Gates](docs/gates.md) | How the gates decide, the retrieval rounds, and what each abstention means |
| [Decision models](docs/decision-models.md) | The models, their licences, and the HTTP and in-process backends |
| [Documents](docs/documents.md) | Formats, passages, size limits and URL ingestion |
| [HTTP API](docs/api.md) | Every endpoint, with request and response examples |
| [Configuration](docs/configuration.md) | Every setting and environment variable |
| [Deployment](docs/deployment.md) | The container image, authentication and health checks |
| [Python](docs/python.md) | Use evidencia as a library |
| [Evaluation](docs/evaluation.md) | The pt-PT gate cases and the measurement scripts |

## Licence

evidencia is licensed under the Apache License 2.0. The decision model that you configure has its own licence, and that licence applies to its use.

`evidencia/_vendor/kev/` holds four inference modules of [Kev](https://github.com/jaredpalmer/kev) (commit `1f04cfa`), unchanged, under the Apache License 2.0 in that directory.
