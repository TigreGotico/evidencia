<!-- agent-hint: Front page of the evidencia docs. Keep the page list in the same order as the footers of the other pages. Every fact in these docs must match the code in evidencia/ — config defaults in evidencia/config.py, routes in evidencia/api.py, gate logic in evidencia/pipeline.py. -->

# evidencia

evidencia answers questions from your documents only when a decision model says that the evidence supports the answer. It is a Python package and an HTTP service. It is an in-house research service for European Portuguese (pt-PT).

The service retrieves passages with BM25 and asks the decision model three yes/no questions:

1. **Select.** Does this passage help to answer the question?
2. **Sufficient.** Is the selected evidence enough to answer? If not, evidencia retrieves more passages and asks again.
3. **Grounded.** Is every fact in the generated answer supported by the evidence?

When a gate fails, evidencia does not answer. Each response gives the probability and the threshold of each gate that ran, so you can audit every decision.

The default decision model is Jevásio-0.8B-v2. Its licence is CC BY-NC-SA 4.0, so a deployment with the default model is for research and non-commercial use only. Refer to [Decision models](decision-models.md).

## Pages

| Page | Contents |
| --- | --- |
| [Getting started](getting-started.md) | Install, start the service, ask a first question |
| [Gates](gates.md) | How the three gates decide, the retrieval rounds, and what each abstention means |
| [Decision models](decision-models.md) | The models, their licences, and the HTTP and in-process backends |
| [Documents](documents.md) | Formats, passages, size limits and URL ingestion |
| [HTTP API](api.md) | Every endpoint, with request and response examples |
| [Configuration](configuration.md) | Every setting and environment variable |
| [Deployment](deployment.md) | The container image, authentication and health checks |
| [Python](python.md) | Use evidencia as a library |
| [Evaluation](evaluation.md) | The pt-PT gate cases and the measurement scripts |
