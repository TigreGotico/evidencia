<!-- agent-hint: Backed by the public names in evidencia/__init__.py, the Source protocol in evidencia/sources.py and the on_event names in evidencia/pipeline.py. Every code block on this page runs as written, in order, in one process; the outputs are from one such run with Jevásio-0.8B-v2 in bf16 on CUDA (knowledge/wiki/audits/kev-rag/evidencia/live-v2/python-blocks.txt). When an exported name or a signature changes, run the blocks again. Keep the custom-backend example in step with the Decision protocol in evidencia/decision.py. -->

# Python

The HTTP service is a thin layer over the `Evidencia` class. You can use the class directly in your own program.

## Gated context

```python
from evidencia import Config, Evidencia

engine = Evidencia(Config(decision={"backend": "local"}, generator=None))
engine.add_document("docs", "Lisboa é a capital de Portugal.\n\nO Porto fica no norte.", "lisboa.txt")

ctx = engine.context("Qual é a capital de Portugal?", "docs")
print(ctx.abstain, ctx.sufficient.p, [s.passage.text for s in ctx.evidence])
```

```text
False 0.9973 ['Lisboa é a capital de Portugal.']
```

`context()` runs the select and sufficient gates. It returns a `ContextResult` with `evidence`, `candidates`, `rounds`, `sufficient`, `gated`, `abstain`, `abstained_at` and `sources`. `as_dict()` gives the JSON of `POST /v1/context`.

`generator=None` makes an engine with no chat model. `context()` does not need one.

## Public sources

Name the sources in `Config`, then in each call:

```python
engine = Evidencia(Config(decision={"backend": "local"}, generator=None, sources=["wikidata"]))
ctx = engine.context("Qual é a capital de Portugal?", sources=["wikidata"])
best = ctx.evidence[0].passage
print(best.origin, best.url, best.text.splitlines()[0])
```

```text
wikidata https://www.wikidata.org/wiki/Q597 Lisboa: capital e maior cidade de Portugal.
```

A call can name a collection, sources, or both. Refer to [Public sources](sources.md).

## Watch each step

`context()` and `answer()` take `on_event`, a function that gets the name and the data of each step. The names and the data are the same as the events of `POST /v1/answer/stream`:

```python
def show(name, data):
    if name == "select":
        print(name, data["p_select"], data["text"].splitlines()[0])
    elif name == "sufficient":
        print(name, data.get("p"), data.get("skipped"))

engine.context("Qual é a capital de Portugal?", sources=["wikidata"], on_event=show)
```

```text
select 0.9851 Lisboa: capital e maior cidade de Portugal.
select 0.985 Lisboa: capital e maior cidade de Portugal.
select 0.0108 Pena de morte em Portugal: overview of the state of capital punishment in Portugal.
select 0.0292 Coimbra: município de Portugal.
...
sufficient 0.9934 None
```

The output is shortened. The public sources change, so the passages and the probabilities can be different on your computer.

## Full answers

`answer()` also needs a chat model. Set `generator` and `generator_config` in the `Config`, or give your own engine as `chat`. Refer to [Chat models](generators.md). An engine is any object with a `continue_chat(messages, session_id, lang, units, tools)` method that returns an object with a `content` string:

```python
from types import SimpleNamespace


class FixedReply:
    def continue_chat(self, messages, session_id="default", lang=None, units=None, tools=None):
        return SimpleNamespace(content="Lisboa [1].")


engine = Evidencia(Config(decision={"backend": "local"}), chat=FixedReply())
engine.add_document("docs", "Lisboa é a capital de Portugal.", "lisboa.txt")
result = engine.answer("Qual é a capital de Portugal?", "docs")
print(result["answer"], result["cited"], result["abstained_at"])
```

```text
Lisboa [1]. [1] None
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
| `engine.add_document(name, content, source, fmt="text", url="")` | Adds or replaces a document, and makes the collection if it does not exist. Saves to `data_dir` when it is set |
| `engine.remove_document(name, source)` | Removes a document and returns the count of removed passages |
| `engine.drop_collection(name)` | Deletes a collection, and its file in `data_dir` |
| `engine.collection(name)` | Returns the `Collection`. `KeyError` when it does not exist |
| `collection.search(question, k)` | Returns up to `k` `Passage` objects, best first, without gates |

`fmt` is `text`, `markdown`, `html` or `pdf`. A PDF is `bytes`.

## A custom source

A source is any object with a `name` and a `search(query, k)` method that returns `Passage` objects, best first:

```python
from evidencia import Passage


class Glossary:
    name = "glossario"

    def search(self, query, k):
        text = "Uma freguesia é a menor divisão administrativa de Portugal."
        return [Passage("g1", text, "glossario.txt", 1.0, origin=self.name)][:k]


engine = Evidencia(Config(decision={"backend": "local"}, generator=None), sources={"glossario": Glossary()})
ctx = engine.context("O que é uma freguesia?", sources=["glossario"])
print(ctx.abstain, ctx.evidence[0].passage.origin)
```

```text
False glossario
```

Raise `SourceUnavailable`, from `evidencia.sources`, when the source cannot answer. evidencia then reports it in `sources` and uses the other targets.

## A custom decision backend

A backend is any object with `ask(state, questions)` and `describe()`:

```python
class AlwaysYes:
    def ask(self, state, questions):
        return {name: {"type": "noul", "noul": 1.0} for name in questions}

    def describe(self):
        return {"backend": "always-yes", "reachable": True}


engine = Evidencia(Config(generator=None), decision=AlwaysYes())
```

`ask()` returns one answer per question, in the System One answer shape.

Raise `DecisionUnavailable` when the backend cannot answer. Raise `DecisionRejected` when it refuses the content of the request. Refer to [Gates](gates.md) for what each one causes.

## Exceptions

| Exception | Raised when |
| --- | --- |
| `DecisionUnavailable` | The decision backend cannot answer, and `fail_open` is false or the call does not apply it |
| `DecisionRejected` | The decision model refused a direct grounding check |
| `GeneratorUnavailable` | No chat model is configured, or it failed or gave an empty answer. `reason` is a sentence that you can show a user |
| `KeyError` | The collection does not exist |
| `ValueError` | A document has no text, has an unknown format, or is not a readable PDF. A call names a source that is not enabled, or names no collection and no source. A setting that is not valid |

---
[← Observability](observability.md) · [Home](index.md) · [Evaluation →](evaluation.md)
