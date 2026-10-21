<!-- agent-hint: Backed by evidencia/mcp_server.py (build_server, Service, INSTRUCTIONS) and the mcp command in evidencia/__main__.py. The tool list must match the @server.tool functions; test/test_mcp.py runs them over real stdio. The `claude mcp add` syntax was read from `claude mcp add --help`; the opencode format from opencode.ai/docs/mcp-servers. -->

# Agents

An AI agent, such as Claude Desktop, Claude Code or OpenCode, can use evidencia as a tool. The agent then answers from your documents and from the public sources, with the gates checked, and it can refuse when the evidence is not there.

There are two ways to connect an agent:

- **MCP.** `evidencia mcp` is a Model Context Protocol server. Most agent apps can start it. This is the easy way.
- **HTTP.** Any program can call the [HTTP API](api.md). `GET /openapi.json` describes every endpoint, and many agent frameworks make tools from it.

## What the agent gets

| Tool | Does |
| --- | --- |
| `ask` | Answers a question with every gate checked. Gives the answer, the evidence with links, or the reason for a refusal |
| `find_evidence` | Gives the checked evidence for a question, without an answer. The agent writes the answer itself |
| `check_answer` | Checks an answer that the agent wrote against evidence. Gives the grounding probability |
| `list_collections` | Lists the collections and their documents |
| `list_sources` | Lists the public sources, their licences, and which are enabled |
| `add_document` | Adds a text, Markdown or HTML document to a collection |

The server also gives the agent short instructions: an answer of `null` is a refusal, and the agent must not answer from memory instead.

## Before you start

1. Install evidencia with the `mcp` extra:

   ```bash
   uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,mcp]"
   ```

2. Start the service, as in [Getting started](getting-started.md). The MCP server is only a bridge: it sends each tool call to the running service.

3. Find the full path of the `evidencia` command. The agent app needs it:

   ```bash
   realpath .venv/bin/evidencia
   ```

   The examples below use `/home/you/evidencia/.venv/bin/evidencia`. Replace it with your path.

The MCP server reads two settings:

| Option | Variable | Default | Meaning |
| --- | --- | --- | --- |
| `--url` | `EVIDENCIA_URL` | `http://127.0.0.1:8080` | Address of the running service |
| `--key` | `EVIDENCIA_API_KEY` | none | The service's `api_key`, when it has one |

## Claude Code

```bash
claude mcp add evidencia -- /home/you/evidencia/.venv/bin/evidencia mcp
```

With an API key on the service:

```bash
claude mcp add evidencia -e EVIDENCIA_API_KEY=change-me -- /home/you/evidencia/.venv/bin/evidencia mcp
```

Run `claude mcp list` to check that it connects.

## Claude Desktop

1. Open Claude Desktop. Go to **Settings**, then **Developer**, then **Edit Config**. This opens `claude_desktop_config.json`.
2. Add `evidencia` to `mcpServers`:

   ```json
   {
     "mcpServers": {
       "evidencia": {
         "command": "/home/you/evidencia/.venv/bin/evidencia",
         "args": ["mcp"],
         "env": {"EVIDENCIA_URL": "http://127.0.0.1:8080", "EVIDENCIA_API_KEY": "change-me"}
       }
     }
   }
   ```

   Leave out `EVIDENCIA_API_KEY` when the service has no `api_key`.

3. Close Claude Desktop fully and open it again. The tools appear in the tools menu of a new chat.

## OpenCode

Add this to `opencode.json` in your project, or to the global OpenCode configuration:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "evidencia": {
      "type": "local",
      "command": ["/home/you/evidencia/.venv/bin/evidencia", "mcp"],
      "enabled": true,
      "environment": {"EVIDENCIA_URL": "http://127.0.0.1:8080"}
    }
  }
}
```

## Other MCP clients

Any client that starts a stdio MCP server works. Give it the command `/home/you/evidencia/.venv/bin/evidencia` with the argument `mcp`, and set `EVIDENCIA_URL` and `EVIDENCIA_API_KEY` in its environment.

## Try it

Ask the agent a question and name the tool, for example:

> Usa a ferramenta ask do evidencia, com a coleção docs, para responder: qual é a capital de Portugal?

The agent calls `ask`, and shows the answer with its evidence. For a question that the documents do not answer, `ask` returns `answer: null` with `abstained_at`, and the agent says that the documents do not say.

## When the tools fail

The tool result is an error with the cause:

| Error | Fix |
| --- | --- |
| `cannot reach the evidencia service at http://127.0.0.1:8080; is it running?` | Start the service, or give the right `--url` |
| `evidencia returned 401: invalid or missing bearer token` | Set `EVIDENCIA_API_KEY` to the service's `api_key` |
| `evidencia returned 404: no collection 'docs'` | Add a document to that collection first, or call `list_collections` |
| `evidencia returned 502: ...` | The chat model failed. Refer to [Chat models](generators.md). `find_evidence` still works |

---
[← Public sources](sources.md) · [Home](index.md) · [Demo page →](demo.md)
