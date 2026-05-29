# Connecting Your MCP Client

Serena works with any MCP client that can launch a stdio server or connect to HTTP/SSE.

(clients-general-instructions)=
## General instructions

1. Add a custom MCP server in your client (see the client's documentation).
2. Configure either:
   - a **stdio** launch command: `serena start-mcp-server` with optional `--project` or `--project-from-cwd`, or
   - an **HTTP/SSE** URL after you start the server manually ([Running the MCP Server](020_running#streamable-http)).

Adjust behaviour via [configuration](050_configuration) and [command-line options](mcp-args).

**Per-workspace vs global.** Some clients (Cursor, VS Code, Claude Code) use per-workspace MCP config; others use a global file. With a fixed workspace, prefer `--project` with an absolute path. With a global config, use `--project-from-cwd` or ask the agent to activate a project via the `activate_project` tool.

**Tool selection.** Prefer tuning `excluded_tools` / `included_optional_tools` in Serena's config rather than disabling tools only in the client UI.

(clients-common-pitfalls)=
### Common pitfalls

**`serena` not on PATH.** Use the full path to the executable in `command`, or use `uvx` / `uv run` as in the examples below.

**Serena's tools not used.** Some clients under-use external MCP tools. Ask the agent to call `initial_instructions` and use symbolic tools (`find_symbol`, `find_referencing_symbols`, etc.) for navigation and refactors.

**Environment variables.** Language servers may need extra env vars (e.g. `DOTNET_ROOT`). Add an `env` object to the MCP server entry if the subprocess does not inherit your shell profile.

(cursor)=
## Cursor

Cursor spawns the MCP server from `mcp.json`. **Do not** run `serena start-mcp-server` in a terminal for normal stdio use.

1. **Cursor Settings → MCP** (or edit `.cursor/mcp.json`).
2. Add a stdio server with `serena start-mcp-server` and your project path.

**Installed via `uv tool install`:**

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--project", "/absolute/path/to/your/project"
      ]
    }
  }
}
```

**From a cloned repo:**

```json
{
  "mcpServers": {
    "serena": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/serena",
        "serena", "start-mcp-server",
        "--project", "/absolute/path/to/your/workspace"
      ]
    }
  }
}
```

Use `--project-from-cwd` only if you have verified Cursor sets the subprocess working directory to the workspace root.

One-time in the repo: `serena project create` and optionally `serena project index`.

## Claude Code

**Global** (any directory, detect project from cwd):

```shell
claude mcp add --scope user serena -- serena start-mcp-server --project-from-cwd
```

**Per-project:**

```shell
claude mcp add serena -- serena start-mcp-server --project "$(pwd)"
```

Confirm with `/mcp`. If the server starts slowly, increase `MCP_TIMEOUT` (e.g. `export MCP_TIMEOUT=60000`).

At the start of a session, ask the agent to read Serena's `initial_instructions` tool if it does not use symbolic tools automatically.

## VS Code

Use **MCP: Add Server** → **Command (stdio)**.

**Workspace (recommended):**

    serena start-mcp-server --project ${workspaceFolder}

**Global:**

    serena start-mcp-server --project-from-cwd

You may need to prompt: "Activate the current directory as the Serena project" when using global config.

## Codex

Add to `~/.codex/config.toml` (adjust paths):

```toml
[mcp_servers.serena]
command = "serena"
args = ["start-mcp-server", "--project-from-cwd"]
```

Or with `uv run` from a Serena clone. See [Codex MCP documentation](https://developers.openai.com/codex/mcp) for your version's exact format.

## Claude Desktop

Add to the MCP config (location depends on OS; see Claude Desktop docs):

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--project-from-cwd"
      ]
    }
  }
}
```

Fully quit and restart the app after changing the config.

## Copilot CLI

Interactive: `/mcp add` → stdio → `serena start-mcp-server --project-from-cwd`.

Or in `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "serena": {
      "type": "stdio",
      "command": "serena",
      "args": ["start-mcp-server", "--project-from-cwd"]
    }
  }
}
```

## Antigravity

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": ["start-mcp-server", "--project-from-cwd"]
    }
  }
}
```

Prompt the agent to activate the project at session start if needed.

## Other clients

Follow the [general instructions](#clients-general-instructions). Terminal agents (Gemini CLI, OpenHands CLI, opencode, etc.) and GUI clients (Jan, OpenWebUI, etc.) typically use the same stdio pattern.

If built-in tools overlap with Serena's, narrow the tool set via [configuration](050_configuration) instead of running two Serena instances.
