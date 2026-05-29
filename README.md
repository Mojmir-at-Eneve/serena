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

Serena is installed **locally from a clone** of this repository (not via `uvx` or PyPI). Your MCP client starts Serena automatically from `mcp.json` over **stdio**; you do **not** need a separate terminal running `serena start-mcp-server` for normal IDE use.

### Install locally

Clone once and install the `serena` CLI on your machine (replace the path with where you keep the repo):

```bash
git clone https://github.com/oraios/serena.git /path/to/serena
cd /path/to/serena
uv sync
uv tool install -p 3.13 .
```

After this, `serena` should be on your PATH. To refresh after pulling changes: `uv sync && uv tool install --reinstall -p 3.13 .`

### One-time setup

Run these once on your machine (not each time you open the editor):

```bash
# Initialize global config (~/.serena/serena_config.yml)
serena init

# Register and index a project you want to work on
cd /path/to/your/project
serena project create
serena project index   # optional but recommended
```

Indexing warms language-server caches and can speed up the first real session.

### Connect your MCP client

Add Serena to your client’s MCP configuration. The client spawns the process and talks over stdin/stdout.
You do **not** need a project path in `mcp.json`.

**Cursor** (user or workspace `mcp.json`) — path-free, uses the locally installed `serena` command:

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": ["start-mcp-server"]
    }
  }
}
```

At startup Serena **auto-detects** the project from the MCP subprocess working directory (`.serena/project.yml` or `.git` in cwd or parents). If detection fails (for example with a global MCP config whose cwd is not the workspace), the agent should call **`activate_project`** with the IDE workspace root path; that also creates `.serena/project.yml` when missing.

**Without a global `serena` install**, point MCP at `uv run` in your Serena clone (still no project path in args):

```json
{
  "mcpServers": {
    "serena": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/serena",
        "serena", "start-mcp-server"
      ]
    }
  }
}
```

Optional override for debugging: `serena start-mcp-server --project /absolute/path/to/project`.

Contributors can commit a **project-level** `.cursor/mcp.json` in this repo so MCP settings travel with the clone.

If the host already exposes tools that overlap with Serena’s, tune the tool set via `excluded_tools` or `included_optional_tools` in [configuration](docs/02-usage/050_configuration.md) (see also **Default tools** below).

### When you start the server yourself

Only needed for **HTTP/SSE transport** (you run the server and point the client at a URL) or **debugging** from a terminal:

```bash
serena start-mcp-server --project /path/to/your/project
```

See [Running the MCP Server](docs/02-usage/020_running.md) and [Connecting Your MCP Client](docs/02-usage/030_clients.md).

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

From your local clone:

```bash
uv sync --extra dev
uv run pytest test/serena/ test/solidlsp/python/ -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor workflow.

## License

MIT — see [LICENSE](LICENSE).
