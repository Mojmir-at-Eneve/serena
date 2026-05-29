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

```bash
# Initialize global config (~/.serena/serena_config.yml)
uvx --from git+https://github.com/oraios/serena serena init

# Register and index a project
cd /path/to/your/project
uvx --from git+https://github.com/oraios/serena serena project create
uvx --from git+https://github.com/oraios/serena serena project index

# Start MCP server (stdio — typical for Cursor, Claude Code, etc.)
uvx --from git+https://github.com/oraios/serena serena start-mcp-server --project /path/to/your/project
```

Configure your MCP client to run the command above (or `serena start-mcp-server` from a local install).

### MCP client example (Cursor)

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
