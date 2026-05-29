# Running Serena

Serena is a command-line tool with a variety of sub-commands.
This section describes
 * how to run Serena in general
 * how to run and configure the most important command, i.e. starting the MCP server
 * other useful commands.

The main way to run Serena is to use the [installed version](install-serena),
which should be available in your system PATH as `serena.`

In general, to get help, append `--help` to the command, i.e.

    serena --help
    serena <command> --help


(start-mcp-server)=
## Running the MCP Server

The main entry point is:

    serena start-mcp-server [options]

How you use that command depends on the **transport**:

| Transport | Who starts the server | Typical use |
|-----------|------------------------|-------------|
| **stdio** (default) | The MCP **client** spawns `start-mcp-server` as a subprocess | Cursor, VS Code, Claude Code, most IDE integrations |
| **streamable-http** / **sse** | **You** start the server; the client connects to a URL | Remote setups, clients that only speak HTTP, debugging |

Serena does **not** start a web dashboard on launch in the current deployment. Older documentation may still describe a dashboard; that is not part of the default MCP lifecycle.

### Standard I/O Mode

In **stdio** mode, the MCP client runs `serena start-mcp-server` and communicates over the process’s standard input and output. This is the default (`--transport` omitted or `stdio`).

**You usually do not start the server in a terminal.** Configure the client with the launch command (see [Configuring Your MCP Client](030_clients)). When you open a chat or enable the MCP server in the IDE, the client starts Serena, keeps one process per configured server, and stops it when the session ends.

That design is intentional: stdio MCP servers have no listening port; nothing useful is running until the client spawns the subprocess.

**When manual startup still makes sense:**

- **Debugging** — reproduce logs, flags, or project activation in a shell before fixing `mcp.json`.
- **HTTP/SSE mode** — you must start the server yourself and give the client a URL (see [Streamable HTTP Mode](streamable-http) below).

For a minimal local smoke test (not how IDEs run day to day):

    serena start-mcp-server

With no `--project`, Serena **auto-detects** the project root from the process working directory (`.serena/project.yml` or `.git`). Use an explicit path only when debugging or when cwd is not the workspace:

    serena start-mcp-server --project /absolute/path/to/project

If auto-detection fails at startup, the host agent should call `activate_project` with the IDE workspace path (see [Cursor](030_clients#cursor)).

See [Configuring Your MCP Client](030_clients) for path-free `mcp.json` examples.

(streamable-http)=
### Streamable HTTP Mode

When using *Streamable HTTP* mode, you control the server lifecycle yourself,
i.e. you start the server and provide the client with the URL to connect to it.

Simply provide `start-mcp-server` with the `--transport streamable-http` option and optionally provide the desired port
via the `--port` option.
For example, to start the server on port 9121, run

    serena start-mcp-server --transport streamable-http --port <port>

and then configure your client to connect to `http://localhost:9121/mcp`.

By default, only connections from localhost are allowed; pass the `--host <listen_address>` option to configure
the listen address and allow remote connections if needed (but be aware of the security implications of doing so).

**When to use.** Note that Serena is a stateful MCP server, and only one coding project can be active at a time.
Therefore, starting a single Serena instance and connecting it to multiple clients is only 
appropriate if all clients will be working on the same project.  
If you want several agents to work on different projects, making each client/agent start its own server
in stdio mode is likely the best option.
See section [The Project Workflow](040_workflow) for more information on how to manage projects in Serena.

The legacy SSE transport is also supported (via `--transport sse` with corresponding /sse endpoint), its use is discouraged.

(mcp-args)=
### MCP Server Command-Line Arguments

The Serena MCP server supports a wide range of additional command-line options.
Use the command

    <serena> start-mcp-server --help

to get a list of all available options.

Some useful options include:

  * `--project <path|name>`: optional explicit project; overrides cwd auto-detection.
  * **Default (no `--project`)**: auto-detect from the server working directory (``.serena/project.yml`` or ``.git``). If nothing is found, the agent can activate via the `activate_project` tool.
  * `--project-from-cwd`: deprecated alias for the default behaviour when `--project` is omitted.
  * `--transport <stdio|streamable-http|sse>`: communication protocol (stdio is the default for IDE clients).
  * `--log-level`, `--trace-lsp-communication`, `--tool-timeout`: override values from [configuration](050_configuration).

## Other Commands

Serena provides several other commands in addition to `start-mcp-server`, 
most of which are related to project setup and configuration.

To get a list of available commands, run:

    <serena> --help

To get help on a specific command, run:

    <serena> <command> --help

In general, add `--help` to any command or sub-command to get information about its usage and available options.

Here are some examples of commands you might find useful:

```bash
# get help about a sub-command
serena> tools list --help

# list all available tools
serena> tools list --all

# get detailed description of a specific tool
serena> tools description find_symbol

# creating a new Serena project in the current directory 
serena project create

# creating and immediately indexing a project
serena project create --index

# indexing the project in the current directory (auto-creates if needed)
serena project index

# run a health check on the project in the current directory
serena project health-check

# check if a path is ignored by the project
serena project is_ignored_path path/to/check

# edit Serena's global configuration file
serena config edit
```

Explore the full set of commands and options using the CLI itself!


## Alternative Ways of Running Serena

Depending on your requirements, you may want to run Serena in different ways.
When applying one of these approaches, replace `serena` in commands mentioned throughout the documentation
with the respective command and options.

### Using uvx to Run the Latest Source Version
    
`uvx` is part of `uv`. It can be used to run the latest version of Serena directly from the repository, without an explicit local installation.

    uvx -p 3.13 --from git+https://github.com/oraios/serena serena 

This was previously the main way of running Serena.
Since this has the downside that every new commit in the repository will trigger a (potentially slow) re-synchronization, an [installation](010_installation) of Serena should usually be preferred.
If you should experience timeouts when connecting the MCP server, consider switching.  
If, however, the synchronisation is fast enough for you, this is still a good option.

### Running from Cloned Source

1. Clone the repository and change into it.

   ```shell
   git clone https://github.com/oraios/serena
   cd serena
   ```

2. Run Serena via

   ```shell
   uv run serena 
   ```

   when within the serena installation directory.     
   From other directories, run it with the `--directory` option, i.e.

   ```shell
    uv run --directory /abs/path/to/serena serena
    ```

:::{note}
Adding the `--directory` option results in the working directory being set to the Serena directory.
As a consequence, you will need to specify paths when using CLI commands that would otherwise operate on the current directory.
:::

(docker)=
### Using Docker

The Docker approach offers several advantages:

* better security isolation for shell command execution
* no need to install language servers and dependencies locally
* consistent environment across different systems

You can run the Serena MCP server directly via Docker as follows,
assuming that the projects you want to work on are all located in `/path/to/your/projects`:

```shell
docker run --rm -i --network host -v /path/to/your/projects:/workspaces/projects ghcr.io/oraios/serena:latest serena 
```

This command mounts your projects into the container under `/workspaces/projects`, so when working with projects,
you need to refer to them using the respective path (e.g. `/workspaces/projects/my-project`).

Alternatively, you may use Docker compose with the `compose.yml` file provided in the repository.
See our [advanced Docker usage](https://github.com/oraios/serena/blob/main/DOCKER.md) documentation for more detailed instructions, configuration options, and limitations.

:::{note}
Docker usage is subject to limitations; see the [advanced Docker usage](https://github.com/oraios/serena/blob/main/DOCKER.md) documentation for details.
:::

### Using Nix to Run the Latest Source Version

If you are using Nix and [have enabled the `nix-command` and `flakes` features](https://nixos.wiki/wiki/flakes), you can run Serena using the following command:

```bash
nix run github:oraios/serena -- <command> [options]
```

You can also install Serena by referencing this repo (`github:oraios/serena`) and using it in your Nix flake. The package is exported as `serena`.
