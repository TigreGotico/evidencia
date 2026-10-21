<!-- agent-hint: Backed by evidencia/web/index.html, served at GET / by create_app() in evidencia/api.py when config.ui is true. The page reads /v1/status, /v1/sources, /v1/collections and the event stream of POST /v1/answer/stream; when an event or a status field changes, change the page and this description together. Screenshots of every state are in knowledge/wiki/audits/kev-rag/evidencia/ui/. -->

# Demo page

The service has a web page that shows each gate decide, live. Open it in a browser at the address of the service:

```text
http://127.0.0.1:8080/
```

The page needs no installation. It works offline and loads nothing from other sites.

## Ask a question

1. Choose a collection, or choose none.
2. Tick the public sources to search. The sources that the service has not enabled are listed under **Not enabled on this server**, and you cannot tick them.
3. Type a question in European Portuguese and click **Ask**. Or click one of the example questions: the page asks it at once.

Name a collection, at least one source, or both.

## Read the result

The page fills in while the service works:

- **Outcome.** "Answered" when every gate passed, or "Abstained at" and the name of the gate that stopped the answer, with one line that explains it. The answer cites its evidence as [1], [2]. Click a number to go to that passage.
- **Evidence.** The passages that the select gate kept, with their select probability, the document they came from, and a link to the page for a public source.
- **Gate trace.** Each retrieval round, with every candidate passage. A bar shows its select probability, and a mark on the bar shows the threshold. Each passage is labelled "kept as evidence", "dropped", or "not kept" when it passed but more than `top_k` passages passed. Below the passages are the sufficient gate, the draft answer and the grounded gate, each with the same bar.

A warning banner, "Gates not applied", says when the gates did not run, because the decision model was unavailable and `fail_open` let the request continue.

## Without a chat model

When the service has no chat model, the page runs only the gates and shows the checked evidence without an answer. When the chat model fails, the page shows the error and the button **Ask without generating**. Click it to see the checked evidence.

## Add documents

The **Documents** panel adds a document to a collection:

- Type a collection name. A new name makes a new collection.
- Paste text and click **Add text**, or choose a `.txt`, `.md`, `.html` or `.pdf` file and click **Upload file**.
- **Delete** removes a whole collection, after it asks you to confirm.

## A service with an API key

When the service has an `api_key`, the page asks for it the first time. The page keeps the key in the browser, so it does not ask again on that computer.

## Turn the page off

Set `ui = false` in the configuration. `GET /` then returns 404. The API does not change.

---
[← Agents](agents.md) · [Home](index.md) · [HTTP API →](api.md)
