<!-- agent-hint: Backed by the public names in evidencia/__init__.py. Every code block on this page runs as written. When an exported name or a signature changes, run the blocks again. Keep the custom-backend example in step with the Decision protocol in evidencia/decision.py. Outputs are from Jevásio-0.8B-v2 in bf16 on CUDA. -->

# Python

The HTTP service is a thin layer over the `Evidencia` class. You can use the class directly in your own program.

## Gated context

```python
from evidencia import Config, Evidencia

engine = Evidencia(Config(decision={"backend": "local"}))
engine.collection("docs", create=True).add("Lisboa é a capital de Portugal.\n\nO Porto fica no norte.", "lisboa.txt")

ctx = engine.context("Qual é a capital de Portugal?", "docs")
print(ctx.abstain, ctx.sufficient.p, [s.passage.text for s in ctx.evidence])
```

```text
False 0.9973 ['Lisboa é a capital de Portugal.']
```

`context()` runs the select and sufficient gates. It returns a `ContextResult` with `evidence`, `candidates`, `rounds`, `sufficient`, `gated` and `abstain`.

`as_dict()` gives the JSON of `POST /v1/context`.

## Full answers

`answer()` also needs a chat engine. Set `generator` and `generator_config` in the `Config`, or give your own engine as `chat`. An engine is any object with a `continue_chat(messages, session_id, lang, units, tools)` method that returns an object with a `content` string:

```python
from types import SimpleNamespace

from evidencia import Config, Evidencia


class FixedReply:
    def continue_chat(self, messages, session_id="default", lang=None, units=None, tools=None):
        return SimpleNamespace(content="Lisboa.")


engine = Evidencia(Config(decision={"backend": "local"}), chat=FixedReply())
engine.collection("docs", create=True).add("Lisboa é a capital de Portugal.", "lisboa.txt")
result = engine.answer("Qual é a capital de Portugal?", "docs")
print(result["answer"], result["abstained_at"])
```

```text
Lisboa. None
```

`answer()` returns the same dictionary as `POST /v1/answer`. Refer to [HTTP API](api.md).

## Checking your own answers

```python
verdict = engine.check_grounding("Qual é a capital de Portugal?", ["Lisboa é a capital de Portugal."], "O Porto.")
print(verdict.p, verdict.passed)
```

```text
0.107 False
```

## Collections

| Method | Does |
| --- | --- |
| `engine.collection(name, create=True)` | Returns the collection, and makes it if it does not exist |
| `collection.add(content, source, fmt="text")` | Adds or replaces a document. `fmt` is `text`, `markdown`, `html` or `pdf`. A PDF is `bytes` |
| `collection.remove(source)` | Removes a document and returns the count of removed passages |
| `collection.search(question, k)` | Returns up to `k` `Passage` objects by BM25 score, without gates |
| `engine.drop_collection(name)` | Deletes a collection |

## A custom decision backend

A backend is any object with `ask(state, questions)` and `describe()`:

```python
from evidencia import Config, Evidencia


class AlwaysYes:
    def ask(self, state, questions):
        return {name: {"type": "noul", "noul": 1.0} for name in questions}

    def describe(self):
        return {"backend": "always-yes", "reachable": True}


engine = Evidencia(Config(), decision=AlwaysYes())
```

`ask()` returns one answer per question, in the System One answer shape.

Raise `DecisionUnavailable` when the backend cannot answer. Raise `DecisionRejected` when it refuses the content of the request. Refer to [Gates](gates.md) for what each one causes.

## Exceptions

| Exception | Raised when |
| --- | --- |
| `DecisionUnavailable` | The decision backend cannot answer, and `fail_open` is false or the call does not apply it |
| `DecisionRejected` | The decision model refused a direct grounding check |
| `GeneratorUnavailable` | No chat engine is configured, or it failed or gave an empty answer |
| `KeyError` | The collection does not exist |
| `ValueError` | A document has no text, has an unknown format, or is not a readable PDF. Also a setting that is not valid |

---
[← Deployment](deployment.md) · [Home](index.md) · [Evaluation →](evaluation.md)
