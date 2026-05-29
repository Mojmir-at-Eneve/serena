# Connecting Your MCP Client

In the following, we provide general instructions on how to connect Serena to your MCP-enabled client,
as well as specific instructions for popular clients.

(clients-general-instructions)=
## General Instructions

In general, Serena can be used with any MCP-enabled client.
To connect Serena to your favourite client, simply

1. determine how to add a custom MCP server to your client (refer to the client's documentation).
2. add a new MCP server entry by specifying either
    * a [run command](start-mcp-server) that allows the client to start the MCP server in stdio mode as a subprocess, or
    * the URL of the HTTP/SSE endpoint, having started the [Serena MCP server in HTTP/SSE mode](streamable-http) beforehand.

Find concrete examples for popular clients below.

Depending on your needs, you might want to further customize Serena's behaviour by
* [adding command-line arguments](mcp-args)
* [adjusting configuration](050_configuration).

**Mode of Operation**.
Note that some clients have a per-workspace MCP configuration (e.g, VSCode and Claude Code),
while others have a global MCP configuration (e.g. Codex and Claude Desktop).

- In the per-workspace case, you typically want to start Serena with your workspace directory as the project directory 
  and never switch to a different project. This is achieved by specifying the
  `--project <path>` argument (absolute path to the workspace).
- In the global configuration case, you must first activate the project you want to work on, which you can do by asking
  the LLM to do so (e.g., "Activate the current dir as project using serena"). In such settings, the `activate_project`
  tool is required.

**Tool Selection**.
While you may be able to turn off tools through your client's interface (e.g., in VSCode or Claude Desktop),
we recommend selecting your base tool set through Serena's [configuration](050_configuration) (`excluded_tools`, `included_optional_tools`, and related settings).

(clients-common-pitfalls)=
### Common Pitfalls

**Discoverability of the `serena` command**.
Your client may not find the `serena` CLI command, even if it is on your system PATH.
In this case, a workaround is to provide the full path to the `serena` executable.

**Serena's tools not being used**.
With some clients, you may experience that Serena's tools are not being used.
This is mainly due to problems in the client itself (like a poorly implemented tool discovery). To counteract this,
Serena comes with a set of commands that can be used in _hooks_. See the sections on hooks for Claude Code and VSCode below.

**Environment Variables**.
Some language servers may require additional environment variables to be set (e.g. F# on macOS with Homebrew),
which you may need to explicitly add to the MCP server configuration.
Note that for some clients (e.g. Claude Desktop), the spawned MCP server process may not inherit environment variables that
are only configured in your shell profile (e.g. `.bashrc`, `.zshrc`, etc.); they would need to be set system-wide instead.
An easy fix is to add them explicitly to the MCP server entry.
For example, in Claude Desktop and other clients, you can simply add an `env` key to the `serena`
object, e.g.

```
"env": {
    "DOTNET_ROOT": "/opt/homebrew/Cellar/dotnet/9.0.8/libexec"
}
```

(cursor)=
## Cursor

Cursor starts MCP servers for you. For normal use with stdio, **do not** run `serena start-mcp-server` in a terminal alongside the IDE. That duplicates what Cursor already does from `mcp.json` and is a common reason people think Serena “works in the terminal but not in Cursor.”

### Add the server

1. Open **Cursor Settings** → **MCP** (or edit `mcp.json` directly).
2. Add a **stdio** server entry that runs `serena start-mcp-server` with your project (see examples below).
3. Reload MCP or restart the agent session if tools do not appear.

You can put configuration in:

- **User (global)** — applies everywhere; use `--project` with an absolute path, or accept that `--project-from-cwd` depends on how Cursor sets the subprocess working directory.
- **Workspace** — `.cursor/mcp.json` in the repo; good for team defaults and for contributors cloning Serena itself.

### `mcp.json` examples

**Install via `uvx` (no local clone):**

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

**`serena` on your PATH** (after [installation](010_installation)):

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

**Develop from a cloned Serena repo:**

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

### Choosing `--project` vs `--project-from-cwd`

| Flag | Behavior | When to use |
|------|----------|-------------|
| `--project <path>` | Always uses that directory as the Serena project root | Workspace-specific `mcp.json`, or a global config aimed at one repo |
| `--project-from-cwd` | Detects project from the server process’s current directory (`.serena/project.yml` or `.git` in cwd or parents) | Client always launches the server with cwd set to the workspace root |

Prefer **`--project` with an absolute path** when you want predictable behavior regardless of how Cursor spawns the subprocess. Use **`--project-from-cwd`** when you maintain one MCP template and open different folders, and you have verified Cursor sets cwd to the workspace root.

### One-time setup (not MCP lifecycle)

Before first use in a repo, run once on your machine:

```bash
serena init
cd /path/to/your/project
serena project create
serena project index   # optional, recommended
```

Cursor does not run these for you.

### Pitfall: terminal `start-mcp-server`

If Serena works in the terminal but not in Cursor, the fix is almost always the **MCP config**, not an extra manual server. For HTTP/SSE instead of stdio, you **do** start the server yourself — see [Running the MCP Server](020_running#streamable-http).

### Further reading

- [MCP server command-line arguments](mcp-args) — `--project`, `--project-from-cwd`, transport options
- [Configuration](050_configuration) — tools, projects, and optional `included_optional_tools`

There is no `serena setup cursor` command; configure Cursor with the JSON examples above.

## Copilot in JetBrains

Open the settings of your JetBrains IDE and go to Tools / GitHub Copilot / Model Context Protocol (MCP). Then click
on the Configure button. This will open your global `mcp.json` file, where you can add the following entry for Serena:

```json
{
    "servers": {
      "serena": {
        "type": "stdio",
        "command": "serena",
        "args": [
          "start-mcp-server",
          "--context=jb-copilot-plugin"
        ]
      }
    }
}
```

**Verification.**
Open Copilot, switch to Agent mode, and click on the configure tools button. You should see Serena's tools in the list and be able to start
the Serena server there (you do not generally have to start Serena in the future, Copilot will start the server by itself). If the server is shown as running, Copilot is successfully connected to Serena. Most models will understand how to use Serena's tools out of the box, but for some models you may have to prompt "Activate the current project with Serena and read initial instructions" in the beginning of the chat.

**Recommended Configuration**.
The `jb-copilot-plugin` context (see above) comes with our recommended subset of Serena's tools for Copilot in JetBrains IDEs. We also 
recommend *disabling* the following built-in tools for optimal performance: 
replace_string_in_file, apply_patch, list_dir, file_search, grep_search. Note that running subagents may not use MCP servers, consider deactivating the run_subagent tool as well.

Serena offers better alternatives to these basic tools. If you do prefer to use the built-in tools instead,
you should disable corresponding Serena tools instead to prevent context bloat.

We also recommend marking Serena's tools as approved so you don't have to manually approve them in agent sessions. 
You can do this in Tools / GitHub Copilot / Chat, where at the bottom you can click on the Configure button for MCP tool auto-approval.

## Claude Code

Serena is a great way to make Claude Code both more efficient and more powerful!
To set up the Serena MCP server for Claude Code, you can simply run this command: 

    serena setup claude-code

Find manual setup instructions as well as workarounds for Claude Code's recent regressions pertaining to (external) tool use below.

:::{attention}
Recent updates to Claude Code (CC) and to the Opus line of models resulted in drastically reduced
adherence to instructions pertaining to Serena's tools.

After extensive analysis, we identified part of the reason to be very long and detailed
tool descriptions for built-in tools and parts of the default system prompt. 
The descriptions of CC's system tools take almost 16k tokens, cannot be adjusted by the user,
and introduce a very strong bias towards internal tools, making it almost impossible to convince Opus 4.7 to use Serena.

As a workaround, we crafted a system prompt that counteracts this bias.
When using Serena, we highly recommend that you start CC as 

```shell
claude --system-prompt="$(serena prompts print-cc-system-prompt-override)"
```

You can also consider adding the content of `serena cc-system-prompt-override` to your `CLAUDE.md` files,
but the effect be insufficient for counteracting Claude Code's bias towards internal tools.
:::

**Global Configuration**. To add the Serena MCP server for all your projects, use the user-level configuration of claude code and the `--project-from-cwd` flag:

```shell
claude mcp add --scope user serena -- serena start-mcp-server --context claude-code --project-from-cwd
```

**Per-Project Configuration.** Alternatively, to add Serena only for the current project in the current directory, 
use the command:

```shell
claude mcp add serena -- serena start-mcp-server --context claude-code --project "$(pwd)"
```

**Verification.**
Confirm that Claude Code is connected to Serena by running the `/mcp` command and by reconnecting, if necessary.
If Serena fails to start fast enough, you should set `MCP_TIMEOUT` to a sufficiently high value
(e.g. by adding `export MCP_TIMEOUT=60000` to your shell profile)

**Hooks.**
Due to recent changes (especially dynamic tool loading) in Claude Code, the agent will often fail to make proper use
of Serena's tools, either by failing to load them in the beginning or by forgetting the instructions in a long session
(a behavior known as agent drift). To counteract this, we provide reminder hooks. We **strongly recommend** setting
up the hooks as below (or a variation thereof) for optimal performance of Serena in Claude Code.

:::{note}
While recommended, hooks are an **alpha feature**. Provide feedback via the [GitHub issue tracker](https://github.com/oraios/serena/issues) if you encounter any issues.
:::

To set up hooks, add the following to your Claude Code settings file
(`.claude/settings.json` in your project directory, or `~/.claude/settings.json` globally):

All hooks below are opt-in — include only the ones you want. Add the following to your
Claude Code settings file (`.claude/settings.json` in your project directory, or
`~/.claude/settings.json` globally):

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks remind --client=claude-code"
                    }
                ]
            },
            {
                "matcher": "mcp__serena__*",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks auto-approve --client=claude-code"
                    }
                ]
            }
        ],
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks activate --client=claude-code"
                    }
                ]
            }
        ],
        "SessionEnd": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks cleanup --client=claude-code"
                    }
                ]
            }
        ]
    }
}
```

The hooks will:

- **`remind`**: Nudge the agent to use Serena's symbolic tools when it makes too many consecutive
  `grep` or `read_file` calls without using any Serena tools in between.
- **`activate`**: Prompt the agent to activate the project and read Serena's instructions at session start.
- **`cleanup`**: Clean up hook session data when the session ends.
- **`auto-approve`**: Auto-approve Serena tool calls whenever Claude Code is in a permissive
  permission mode (`acceptEdits` or `auto`), so blanket approvals cover Serena's destructive
  tools (e.g. `replace_symbol_body`, `rename_symbol`) instead of prompting on every call.

For more details on Claude Code's hook system, see the
[Claude Code hooks documentation](https://code.claude.com/docs/en/hooks).

## VSCode

You can add Serena to VSCode by running the MCP: Add Server command.
In that dialogue, select the Command (stdio) option. You can decide between installing it globally
or in the workspace (only for the currently open project), and the command you should enter depends on that choice.
(You will be asked to choose after entering the mcp run command.)

**Global.** (Recommended)
Enter `serena start-mcp-server --context=vscode`. Unfortunately, due to a [bug in VSCode](https://github.com/microsoft/vscode/issues/245905),
in this setting Serena won't be able to activate the project automatically. You will have to remember to prompt
"Activate the current dir as project using serena" at the start of each session.

**Workspace.**
Enter `serena start-mcp-server --context=vscode --project ${workspaceFolder}`. This will allow Serena to automatically activate the project,
with the downside that you will have to add Serena to each project you want to use it in.

In both cases, proceed to enter Serena as the name, then select either global or workspace.

**Verification.**
You should be able to see Serena in the tools overview in the AI Chat window.

**Hooks.**
Due to recent changes (especially dynamic tool loading) in VSCode, the agent will often fail to make proper use
of Serena's tools, either by failing to load them in the beginning or by forgetting the instructions in a long session
(a behaviour known as agent drift). To counteract this, we provide reminder hooks. We **strongly recommend** setting
up the hooks as below (or a variation thereof) for optimal performance of Serena in VSCode.


The hooks will:

- **`remind`**: Nudge the agent to use Serena's symbolic tools when it makes too many consecutive
  `grep` or `read_file` calls without using any Serena tools in between.
- **`activate`**: Prompt the agent to activate the project and read Serena's instructions at session start.
- **`cleanup`**: Clean up hook session data when the session ends.

To set this up, create the file `~/.copilot/hooks/serena-hooks.json` with the following content:

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "type": "command",
                "command": "serena-hooks remind --client=vscode"
            }
        ],
        "SessionStart": [
            {
                "type": "command",
                "command": "serena-hooks activate --client=vscode"
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": "serena-hooks cleanup --client=vscode"
            }
        ]
    }
}
```

The `SessionStart` hook also addresses the global configuration limitation mentioned above — it will
automatically prompt the agent to activate the project directory, so you no longer need to do this manually.

## Copilot CLI

Use the interactive `/mcp add` slash command, choose Serena as the name, STDIO as the server type, and
`serena start-mcp-server --context=copilot-cli --project-from-cwd` as command. Copilot CLI will immediately notify you
that Serena is running if everything is set up correctly or display an error otherwise.

You should add the same **hooks** as in VSCode (see above) if Copilot CLI didn't pick them up automatically.


## Codex (CLI and App)

You can simply run `serena setup codex`.

Alternatively, you can manually add the following to `~/.codex/config.toml` (create the file if it does not exist):

```toml
[mcp_servers.serena]
startup_timeout_sec = 15
command = "serena"
args = ["start-mcp-server", "--project-from-cwd", "--context=codex"]
```

**Verification.**
Run the `/mcp` command and verify that Serena is connected.
The Codex app does not start a session in the project's directory, so when using the app, we recommend
asking Codex to "Activate the current dir as project using serena" at the start of each session (though Codex might
do this automatically).

**Hooks.**
Codex supports lifecycle hooks; see the
[Codex hooks documentation](https://developers.openai.com/codex/hooks) for details. To enable
Serena's hooks for Codex, add this feature flag to `~/.codex/config.toml`:

```toml
[features]
codex_hooks = true
```

Then create `~/.codex/hooks.json` with the following content:

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks remind --client=codex"
                    }
                ]
            }
        ],
        "SessionStart": [
            {
                "matcher": "startup|resume",
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks activate --client=codex"
                    }
                ]
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "serena-hooks cleanup --client=codex"
                    }
                ]
            }
        ]
    }
}
```

The hooks will:

- **`activate`**: Prompt the agent to activate the current project and read Serena's instructions
  when a Codex session starts or resumes.
- **`remind`**: Nudge the agent to use Serena's symbolic tools when it makes too many consecutive
  code-search or code-file-read calls without using Serena tools in between.
- **`cleanup`**: Clean up hook session data when the session ends.

The `PreToolUse` matcher is intentionally restricted to `Bash`. The Serena reminder hook for Codex
tracks shell-based grep and code-file reads, so running it for every tool call is unnecessary.

## Claude Desktop

On Windows and macOS, there are official [Claude Desktop applications by Anthropic](https://claude.ai/download); for Linux, there is an [open-source
community version](https://github.com/aaddrick/claude-desktop-debian).

To configure MCP server settings, go to File / Settings / Developer / MCP Servers / Edit Config,
which will let you open the JSON file `claude_desktop_config.json`.

Add the `serena` MCP server configuration

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--context=claude-desktop"
      ]
    }
  }
}
```

If your language server requires specific environment variables to be set (e.g. F# on macOS with Homebrew),
you can add them via an `env` key (see [above](#clients-common-pitfalls)).

**Verification.**
Once you have created the new MCP server entry, save the config and then restart Claude Desktop.

:::{attention}
Be sure to fully quit the Claude Desktop application via File / Exit, as regularly closing the application will just
minimize it.
:::

After restarting, you should see Serena's tools in your chat interface (notice the small hammer icon).

## Copilot CLI

In the interactive mode, you can call `/mcp add` from within the copilot CLI. There, use serena as name, 
STDIO as the server type, and `serena start-mcp-server --context=copilot-cli --project-from-cwd` as command.

Alternatively, add the following to `~/.copilot/mcp-config.json` (create the file if it does not exist):

```json
{
  "mcpServers": {
    "serena": {
      "type": "stdio",
      "command": "serena",
      "tools": [
        "*"
      ],
      "args": [
        "start-mcp-server",
        "--context=copilot-cli",
        "--project-from-cwd"
      ]
    }
  }
}
```

**Verification.**
Copilot should now show that Serena is running, though you may have to restart it.


## JetBrains Junie

For the Junie plugin in JetBrains IDEs you can add Serena either to the global configuration in `~/.junie/mcp/mcp.json` 
or to the project configuration in `<project>/.junie/mcp/mcp.json`. Important, don't add both!
In both cases the entry should be:


```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--context=junie",
        "--project-from-cwd"
      ]
    }
  }
}
```

With the global configuration, Serena will be available in all projects. However,
within the Junie plugin, projects will not be automatically activated in Serena. 
You may thus have to prompt 
Junie to "Activate the current project using serena's activation tool" at the start of each session (though some models are
smart enough to activate the project automatically).

With the project-scoped configuration, Serena will be available only in that project, and the project will automatically
be recognized as active by Serena.


## JetBrains AI Assistant

Go to Settings / Tools / AI Assistant / MCP and enter the following configuration:

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--context=jb-ai-assistant",
        "--project-from-cwd"
      ]
    }
  }
}
```

Like for Junie, you have the choice between the global and the project-scoped configuration, 
with the same trade-off.

## Antigravity

Add this configuration:

```json
{
  "mcpServers": {
    "serena": {
      "command": "serena",
      "args": [
        "start-mcp-server",
        "--context=antigravity"
      ]
    }
  }
}
```

You will have to prompt Antigravity's agent to "Activate the current project using serena's activation tool" after starting Antigravity in the project directory (once in the first chat enough, all other chat sessions will continue using the same Serena session).


Unlike VSCode, Antigravity does not currently support including the working directory in the MCP configuration.
Also, the current client will be shown as `none` in Serena's dashboard (Antigravity currently does not fully support the MCP specifications). This is not a problem, all tools will work as expected.

## Other Clients

For other clients, follow the [general instructions](#clients-general-instructions) above to set up Serena as an MCP server.

### Terminal-Based Clients

There are many terminal-based coding assistants that support MCP servers, such as

 * [Gemini-CLI](https://github.com/google-gemini/gemini-cli), 
 * [Qwen3-Coder](https://github.com/QwenLM/Qwen3-Coder),
 * [rovodev](https://community.atlassian.com/forums/Rovo-for-Software-Teams-Beta/Introducing-Rovo-Dev-CLI-AI-Powered-Development-in-your-terminal/ba-p/3043623),
 * [OpenHands CLI](https://docs.all-hands.dev/usage/how-to/cli-mode) and
 * [opencode](https://github.com/sst/opencode).

They generally benefit from the symbolic tools provided by Serena. If the host’s built-in tools overlap with Serena’s,
tune the tool set via [configuration](050_configuration) rather than running a second server process.

### MCP-Enabled IDEs and Coding Clients (Cline, Roo-Code, Cursor, Windsurf, etc.)

Many coding assistants and AI-enabled IDEs can attach to MCP servers over stdio. Serena adds language-server-backed symbolic tools on top of whatever the host already provides.

**Cursor** has a dedicated walkthrough with full `mcp.json` examples, global vs workspace config, and the common “don’t start the server in a terminal” mistake — see [Cursor](#cursor) above.

For other hosts in this category (Cline, Roo Code, Windsurf, etc.), follow the [general instructions](#clients-general-instructions): add a stdio launch command, point it at your project, and let the **client** spawn Serena. Only use a manually started server when you deliberately run [HTTP/SSE mode](020_running#streamable-http).

If the host’s built-in tools overlap with Serena’s, adjust [configuration](050_configuration) (for example `excluded_tools` or `included_optional_tools`) rather than running a second server process.

### Local GUIs and Agent Frameworks

Over the last months, several technologies have emerged that allow you to run a local GUI client
and connect it to an MCP server. The respective applications will typically work with Serena out of the box.
Some of the leading open source GUI applications are

  * [Jan](https://jan.ai/docs/mcp), 
  * [OpenHands](https://github.com/All-Hands-AI/OpenHands/),
  * [OpenWebUI](https://docs.openwebui.com/openapi-servers/mcp) and 
  * [Agno](https://docs.agno.com/introduction/playground).

These applications allow combining Serena with almost any LLM (including locally running ones) 
and offer various other integrations.
