import asyncio
import json
import socket
import sys
import threading
import time

import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia.api import create_app

Q = "Qual é a capital de Portugal?"


def serve(**cfg):
    """A real HTTP service on a free port, with stub models."""
    engine = Evidencia(Config(**cfg), decision=StubDecision(), chat=StubChat("Lisboa [1]."), sources={})
    engine.add_document("geo", DOC, "geo.txt")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(engine=engine), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


async def session_calls(url, key, calls):
    params = StdioServerParameters(command=sys.executable, args=["-m", "evidencia", "mcp", "--url", url]
                                   + (["--key", key] if key else []))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            results = [await session.call_tool(name, args) for name, args in calls]
    return tools, results


def run(url, calls, key=None):
    return asyncio.run(session_calls(url, key, calls))


def text_of(result):
    return "".join(getattr(c, "text", "") for c in result.content)


def test_an_agent_asks_through_mcp_over_stdio():
    server, url = serve(api_key="segredo")
    try:
        tools, (asked, checked) = run(url, [
            ("ask", {"question": Q, "collection": "geo"}),
            ("check_answer", {"question": Q, "evidence": ["Lisboa é a capital de Portugal."], "answer": "Lisboa."}),
        ], key="segredo")
    finally:
        server.should_exit = True
    assert tools == ["add_document", "ask", "check_answer", "find_evidence", "list_collections", "list_sources"]
    assert not asked.is_error
    answer = json.loads(text_of(asked))
    assert answer["answer"] == "Lisboa [1]." and answer["abstained_at"] is None
    assert answer["evidence"][0] == {"text": "Lisboa é a capital de Portugal desde o século XIII.",
                                     "source": "geo.txt", "origin": "geo", "p_select": 0.9}
    assert json.loads(text_of(checked)) == {"p": 0.9, "threshold": 0.5, "passed": True}


def test_a_wrong_key_and_a_stopped_service_are_tool_errors_with_the_cause():
    server, url = serve(api_key="segredo")
    try:
        _, (denied,) = run(url, [("list_collections", {})], key="errado")
    finally:
        server.should_exit = True
    assert denied.is_error and "401" in text_of(denied)
    _, (down,) = run("http://127.0.0.1:9", [("list_collections", {})])
    assert down.is_error and "cannot reach the evidencia service" in text_of(down)


def test_the_mcp_command_writes_nothing_but_protocol_to_stdout():
    """Each stdout line of an MCP stdio server must be a JSON-RPC message; a log line there breaks strict clients."""
    import subprocess
    out = subprocess.run([sys.executable, "-c", "import evidencia, evidencia.mcp_server, evidencia.__main__"],
                         capture_output=True, timeout=60)
    assert out.returncode == 0 and out.stdout == b""
