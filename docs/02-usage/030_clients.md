# Connecting Your MCP Client

Serena works with any MCP client that can launch a stdio server.

(clients-general-instructions)=
## General instructions

1. Add a custom MCP server in your client (see the client's documentation).
2. Configure a **stdio** launch command: `serena start-mcp-server` (no project path required in `mcp.json`).

Adjust behaviour via [configuration](050_configuration) and [command-line options](mcp-args).

**Project binding without paths in MCP config.** By default the server auto-detects a project from its working directory at startup. If that fails, the agent should call `manage_project` (action=activate) with the IDE workspace root path (see [Cursor](#cursor)). Optional `--project` remains for explicit overrides.

**Tool selection.** Prefer tuning `excluded_tools` / `included_optional_tools` in Serena's config rather than disabling tools only in the client UI.

(clients-common-pitfalls)=
### Common pitfalls

**`serena` not on PATH.** Use the full path to the executable in `command`, or use `uvx` / `uv run` as in the examples below.

**Serena's tools not used.** Some clients under-use external MCP tools. Ask the agent to call `start_here` and use symbolic tools (`find_symbol`, `find_usages`, etc.) for navigation and refactors.

**Environment variables.** Language servers may need extra env vars (e.g. `DOTNET_ROOT`). Add an `env` object to the MCP server entry if the subprocess does not inherit your shell profile.

(cursor)=
## Cursor

Cursor spawns the MCP server from `mcp.json`. **Do not** run `serena start-mcp-server` in a terminal for normal stdio use.

1. **Cursor Settings → MCP** (or edit `.cursor/mcp.json`).
2. Add a stdio server with only `serena start-mcp-server` in `args` (no project path).

**Installed via `uv tool install`:**

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

**From a cloned Serena repo** (only the Serena install path is fixed, not the workspace):

```json
{
  "mcpServers": {
    "serena": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/serena",
        "serena", "start-mcp-server"
      ]
    }
  }
}
```

**How the workspace is chosen**

- **Startup:** Serena walks up from the MCP subprocess cwd looking for `.serena/project.yml` or `.git`. This works when Cursor launches the server with cwd set to the workspace root (typical for workspace-scoped MCP).
- **Agent fallback:** If tools report “No active project”, ask the agent to call `manage_project` (action=activate) with the workspace root path from the IDE. The tool registers the project and creates `.serena/project.yml` if needed.
- **Verify:** Check MCP server logs for `Using project root …` or the warning that auto-detection failed.

One-time on the machine: `serena init`. Optional: `serena project index` in the repo for faster first session.

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

At the start of a session, ask the agent to call Serena's `start_here` tool if it does not use symbolic tools automatically.

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
