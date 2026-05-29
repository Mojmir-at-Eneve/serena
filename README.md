<p align="center" style="text-align:center;">
  <img src="resources/serena-logo.svg#gh-light-mode-only" style="width:500px">
  <img src="resources/serena-logo-dark-mode.svg#gh-dark-mode-only" style="width:500px">
</p>

<h3 align="center">
    LSP-backed MCP toolbox for coding agents
</h3>

<div align="center">
  <a href="https://github.com/oraios/serena/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-b0e8ff?style=flat-square&labelColor=0a0e14" alt="license"></a>
</div>
<br>

Serena is a **Model Context Protocol (MCP) server** that gives coding agents IDE-grade, language-server-backed tools:

- **Ground-truth code intelligence** — symbols, references, declarations, implementations, diagnostics (not grep guesses)
- **Semantic editing** — replace symbol bodies, insert around symbols, rename via LSP
- **File-level utilities** — read/search/replace, directory listing, shell commands
- **40+ languages** via [SolidLSP](src/solidlsp/) (open-source language servers)

There is **no** bundled agent persona, memory system, JetBrains plugin integration, or context/mode prompt machinery. The host agent uses Serena as a **toolbox**; call `initial_instructions` (or read MCP server instructions) for usage guidance.

## Quick Start

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/)

Serena’s default transport is **stdio**. Your MCP client (Cursor, Claude Code, VS Code, and similar) **starts Serena automatically** from `mcp.json` when it needs the server. You do **not** need a separate terminal running `serena start-mcp-server` for normal IDE use.

### One-time setup

Run these once on your machine (not each time you open the editor):

```bash
# Initialize global config (~/.serena/serena_config.yml)
uvx --from git+https://github.com/oraios/serena serena init

# Register and index a project
cd /path/to/your/project
uvx --from git+https://github.com/oraios/serena serena project create
uvx --from git+https://github.com/oraios/serena serena project index   # optional but recommended
```

Indexing warms language-server caches and can speed up the first real session.

### Connect your MCP client

Add Serena to your client’s MCP configuration. The client spawns the process and talks over stdin/stdout.

**Cursor** (user or workspace `mcp.json`):

```json
{
  "mcpServers": {
    "serena": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/oraios/serena",
        "serena", "start-mcp-server",
        "--project", "/absolute/path/to/your/project"
      ]
    }
  }
}
```

- **`--project`** — fixed absolute path to the repo Serena should use. Best when the MCP entry is tied to one workspace.
- **`--project-from-cwd`** — Serena detects the project from the server process’s working directory (`.serena/project.yml` or `.git` in the current directory or parents). Use when the client always launches the server from the project root.

Contributors working inside the Serena repo can commit a **project-level** `.cursor/mcp.json` so MCP settings travel with the clone.

If the host already exposes tools that overlap with Serena’s, tune the tool set via `excluded_tools` or `included_optional_tools` in [configuration](docs/02-usage/050_configuration.md) (see also **Default tools** below).

### When you start the server yourself

Only needed for **HTTP/SSE transport** (you run the server and point the client at a URL) or **debugging** from a terminal. See [Running the MCP Server](docs/02-usage/020_running.md) and [Connecting Your MCP Client](docs/02-usage/030_clients.md).

### Developing Serena from source

From a clone, run the server through `uv` (replace paths with yours):

```bash
uv run --directory /path/to/serena serena start-mcp-server --project /path/to/workspace
```

Use the same command line in your MCP client’s `command` / `args` when wiring Cursor or another client to your local checkout.

## Default tools

Symbolic read/write, diagnostics, file utilities, `activate_project`, `get_current_config`, and `initial_instructions`. Optional tools (e.g. line-based edits, `restart_language_server`) can be enabled via `included_optional_tools` in config.

Run `serena tools list --all` for the full registry.

## CLI

| Command | Purpose |
|---------|---------|
| `serena start-mcp-server` | Run the MCP server |
| `serena init` | Create global config |
| `serena project create` | Create `.serena/project.yml` |
| `serena project index` | Warm LSP caches |
| `serena project health-check` | Smoke-test tools on a project |
| `serena config edit` | Edit `serena_config.yml` |
| `serena tools list` | List tools |

## Architecture

```
Agent (Cursor, Claude Code, …) ──MCP──► serena.mcp ──► SerenaAgent ──► SolidLSP ──► language servers
```

## Development

```bash
uv sync --extra dev
uv run pytest test/serena/ test/solidlsp/python/ -q
```

## License

MIT — see [LICENSE](LICENSE).
