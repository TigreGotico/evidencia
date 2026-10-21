"""An MCP server that lets an agent ask a running evidencia service, over stdio."""
from __future__ import annotations

from typing import Any

import requests

INSTRUCTIONS = """evidencia answers questions in European Portuguese from documents and public Portuguese sources,
and refuses when the evidence does not support an answer. Use `ask` for a checked answer, `find_evidence` to get
the passages and write the answer yourself, then `check_answer` to test that answer against the passages.
An answer of null is a refusal: tell the user the documents do not say, and do not answer from memory."""


class ServiceError(RuntimeError):
    """The service could not be reached, or it refused the request. The message says which and why."""


class Service:
    """The evidencia HTTP API. ``http`` is anything with requests-style ``get`` and ``post``."""

    def __init__(self, url: str, key: str | None = None, http: Any = None, timeout: float = 300.0):
        self.url = url.rstrip("/")
        self.headers = {"authorization": f"Bearer {key}"} if key else {}
        self.http = http or requests.Session()
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        kwargs = {"headers": self.headers}
        if isinstance(self.http, requests.Session):
            kwargs["timeout"] = self.timeout
        if body is not None:
            kwargs["json"] = body
        try:
            resp = getattr(self.http, method)(f"{self.url}{path}", **kwargs)
        except requests.ConnectionError:
            raise ServiceError(f"cannot reach the evidencia service at {self.url}; is it running?") from None
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text
            raise ServiceError(f"evidencia returned {resp.status_code}: {detail}")
        return resp.json()


def _passage(p: dict) -> dict:
    return {key: p[key] for key in ("text", "source", "url", "origin", "p_select") if p.get(key) not in (None, "")}


def _question(question: str, collection: str | None, sources: list[str] | None) -> dict:
    return {"question": question, "collection": collection, "sources": sources or []}


def build_server(service: Service):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer(name="evidencia", instructions=INSTRUCTIONS)

    def call(method: str, path: str, body: dict | None = None):
        try:
            return service.call(method, path, body)
        except ServiceError as e:
            raise ToolError(str(e)) from None

    @server.tool()
    def list_collections() -> dict:
        """The local document collections, with their passage counts and document names."""
        return call("get", "/v1/collections")

    @server.tool()
    def list_sources() -> list[dict]:
        """The public sources this service can search, with their licences. Only enabled ones can be used."""
        return call("get", "/v1/sources")["sources"]

    @server.tool()
    def ask(question: str, collection: str | None = None, sources: list[str] | None = None) -> dict:
        """Answer a question from a collection, from public sources, or both, with each gate checked.

        `answer` is null when evidencia refuses; `abstained_at` then names the check that failed. Each [n] in
        the answer cites evidence n. Name a collection, at least one source (see list_sources), or both.
        """
        result = call("post", "/v1/answer", _question(question, collection, sources))
        return {"answer": result["answer"], "abstained_at": result["abstained_at"], "message": result["message"],
                "evidence": [_passage(p) for p in result["evidence"]], "grounded": result["grounded"],
                "gates": result["gates"]}

    @server.tool()
    def find_evidence(question: str, collection: str | None = None, sources: list[str] | None = None) -> dict:
        """The passages that answer a question, without writing an answer. Write the answer from these only."""
        result = call("post", "/v1/context", _question(question, collection, sources))
        return {"evidence": [_passage(p) for p in result["evidence"]], "sufficient": result["sufficient"],
                "abstained_at": result["abstained_at"], "gates": result["gates"]}

    @server.tool()
    def check_answer(question: str, evidence: list[str], answer: str) -> dict:
        """Whether every fact in `answer` is supported by `evidence`. `passed` false means do not give it."""
        return call("post", "/v1/check/grounding",
                            {"question": question, "evidence": evidence, "answer": answer})

    @server.tool()
    def add_document(collection: str, source: str, text: str, format: str = "text") -> dict:
        """Add a document to a collection, making the collection if needed. format: text, markdown or html."""
        return call("post", f"/v1/collections/{requests.utils.quote(collection, safe='')}/documents",
                            {"source": source, "text": text, "format": format})

    return server
