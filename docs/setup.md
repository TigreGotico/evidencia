<!-- agent-hint: A step-by-step procedure for a reader who has never used a terminal. Strict STE: one instruction per step, the result of each step stated. Every command must work as written on Linux and macOS. When a command, a path, a preset name or a default changes, run these steps again from the start in a new directory. -->

# Step-by-step setup

This guide installs evidencia on your computer and connects it to a chat model. Then you ask a first question in the browser. It also connects Claude Desktop to evidencia. You do not need to know how to program. Do the steps in order.

The guide is for Linux and macOS. Each step tells you what you see when it works.

## What you need

- A computer with Linux or macOS, and 15 GB of free disk space.
- An internet connection. The first start downloads about 5 GB.
- Access to the evidencia repository on GitHub. Ask the owner of the repository to give you access.
- One of these, for the chat model that writes the answers:
  - **An API key from Anthropic (Claude).** This is the easiest choice. It costs a small amount per question.
  - **An NVIDIA graphics card** with 6 GB of memory or more. With it, AMALIA runs on your own computer. This choice is free, but it needs more setup.

## 1. Open a terminal

- **macOS:** press Cmd and Space, type `Terminal`, and press Enter.
- **Linux:** press Ctrl, Alt and T together.

A window opens with a line where you can type. In this guide, a grey box is a command. Copy all of the box, paste it in the terminal, and press Enter.

## 2. Install uv

uv installs Python programs. Paste this command:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

When it finishes, close the terminal and open a new one, so that it finds uv. To check, paste:

```bash
uv --version
```

You see a line such as `uv 0.12.9`.

## 3. Get evidencia

Paste these commands. They download evidencia to a folder named `evidencia` in your home folder, and go into that folder:

```bash
cd ~
git clone https://github.com/TigreGotico/evidencia
cd evidencia
```

If git asks for a user name and a password, give your GitHub user name, and a GitHub access token as the password. On macOS, if a window asks to install the developer tools, click **Install** and then paste the commands again.

## 4. Install evidencia

```bash
uv venv -p 3.12 .venv
uv pip install --python .venv/bin/python --prerelease=allow -e ".[local,hybrid,mcp]"
```

This takes a few minutes. It downloads up to 4 GB. When it finishes, the last lines say `Installed` and a number of packages.

## 5. Choose the chat model

Do **5A** or **5B**, not both.

### 5A. Claude, with an API key

1. Go to <https://console.anthropic.com>, sign in, and open **API Keys**.
2. Click **Create Key**. Copy the key. It starts with `sk-ant-`.
3. Make the configuration file. Paste this command. It opens a text editor:

   ```bash
   nano evidencia.toml
   ```

4. Paste this text in the editor. Replace `sk-ant-PASTE-YOUR-KEY-HERE` with your key:

   ```toml
   generator = "anthropic"
   data_dir = "collections"
   sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]

   [generator_config]
   key = "sk-ant-PASTE-YOUR-KEY-HERE"

   [decision]
   backend = "local"
   ```

5. Press Ctrl and O, then Enter, to save. Press Ctrl and X to close the editor.

The file holds your key. Do not send this file to other people.

### 5B. AMALIA, on your own computer

AMALIA is a European Portuguese chat model. On Linux it runs in a container, with Podman and an NVIDIA graphics card. On a Mac with Apple silicon and 16 GB of memory or more, it runs with Homebrew. Refer to the note after step 1.

1. Start AMALIA. Paste this command. Leave this terminal open: AMALIA runs while it is open.

   ```bash
   podman run --rm -p 127.0.0.1:8000:8000 --device nvidia.com/gpu=all \
     -v llama-models:/models -e LLAMA_CACHE=/models \
     ghcr.io/ggml-org/llama.cpp:server-cuda \
     -hf duarteocarmo/AMALIA-9B-0626-DPO-GGUF:Q4_K_M --alias amalia \
     --host 0.0.0.0 --port 8000 --ctx-size 4096
   ```

   The first start downloads 5.2 GB. When AMALIA is ready, a line near the end says `listening on http://0.0.0.0:8000`.

   **On a Mac**, install [Homebrew](https://brew.sh) first, then paste these commands instead:

   ```bash
   brew install llama.cpp
   llama-server -hf duarteocarmo/AMALIA-9B-0626-DPO-GGUF:Q4_K_M --alias amalia --port 8000 --ctx-size 4096
   ```

2. Open a new terminal (step 1), and go to the evidencia folder:

   ```bash
   cd ~/evidencia
   ```

3. Make the configuration file:

   ```bash
   nano evidencia.toml
   ```

4. Paste this text in the editor:

   ```toml
   generator = "amalia"
   data_dir = "collections"
   sources = ["pt.wikipedia", "wikidata", "dicionario-aberto"]

   [decision]
   backend = "local"
   device = "cpu"
   ```

   `device = "cpu"` keeps the decision model off the graphics card, because AMALIA uses all of its memory.

5. Press Ctrl and O, then Enter, to save. Press Ctrl and X to close the editor.


## 6. Start evidencia

In the evidencia folder, paste:

```bash
.venv/bin/evidencia serve --config evidencia.toml
```

When evidencia is ready, the last line says `Uvicorn running on http://127.0.0.1:8080`. Leave this terminal open: evidencia runs while it is open.

To stop evidencia, click in this terminal and press Ctrl and C. To start it again later, open a terminal and paste:

```bash
cd ~/evidencia
.venv/bin/evidencia serve --config evidencia.toml
```

## 7. Ask a question in the browser

1. Open this address in your web browser: <http://127.0.0.1:8080/>
2. Under **Remote sources**, tick **Wikipédia em português**.
3. Click the example question **Qual é a capital de Portugal?**. The page asks it at once.

The first question takes a few minutes, because evidencia downloads its decision model (about 3 GB). The next questions take a few seconds.

The page shows the answer, the passages that it comes from, and how each check decided. Refer to [Demo page](demo.md) to read the result.

## 8. Add your own documents

1. In the **Documents** panel, type a collection name, for example `notas`.
2. Choose a file (`.txt`, `.md`, `.html` or `.pdf`) and click **Upload file**. Or paste text and click **Add text**.
3. In the **Ask** panel, choose the collection `notas`, and ask a question about your document.

evidencia keeps your collections in the `collections` folder, so they are still there after a restart.

## 9. Connect Claude Desktop

This lets Claude Desktop use evidencia while you chat. evidencia must be running (step 6). Claude Desktop runs on macOS and Windows. On Linux, use Claude Code instead: refer to [Agents](agents.md).

1. Find the path of the evidencia command. Paste:

   ```bash
   realpath ~/evidencia/.venv/bin/evidencia
   ```

   Copy the line that it shows, for example `/home/ana/evidencia/.venv/bin/evidencia`.

2. Open Claude Desktop. Go to **Settings**, then **Developer**, then **Edit Config**. A file named `claude_desktop_config.json` opens.
3. Replace all of its text with this text. Replace the path with the line that you copied:

   ```json
   {
     "mcpServers": {
       "evidencia": {
         "command": "/home/ana/evidencia/.venv/bin/evidencia",
         "args": ["mcp"]
       }
     }
   }
   ```

   If the file already has other servers under `mcpServers`, add the `evidencia` part next to them instead.

4. Save the file. Quit Claude Desktop fully, and open it again.
5. In a new chat, ask: *Usa a ferramenta ask do evidencia, com a fonte pt.wikipedia, para responder: qual é a capital de Portugal?*

Claude calls evidencia and shows the answer with its sources. Refer to [Agents](agents.md) for Claude Code and OpenCode.

## If something does not work

Refer to [Troubleshooting](troubleshooting.md). It lists each error message and what to do.

---
[Home](index.md) · [Getting started →](getting-started.md)
