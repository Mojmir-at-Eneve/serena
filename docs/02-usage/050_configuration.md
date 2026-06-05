# Configuration

Serena is configured through YAML files. Global settings live in `~/.serena/serena_config.yml` (created by `serena init`). Per-project settings live in `.serena/project.yml` inside each repository.

Edit `~/.serena/serena_config.yml` directly, or run `serena init` to create it from the template.

Project files are created by `serena project create` and can be edited directly or via your editor.

## Global configuration (`serena_config.yml`)

| Setting | Purpose |
|---------|---------|
| `log_level` | Minimum log level (10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR). |
| `trace_lsp_communication` | Log raw LSP traffic (mainly for debugging language servers). |
| `tool_timeout` | Seconds after which tool execution is aborted (default 240). |
| `line_ending` | Line endings when writing source files: `lf`, `crlf`, or `native`. |
| `ignored_paths` | Gitignore-style patterns ignored in **all** projects (merged with each project's `ignored_paths`). |
| `ls_specific_settings` | Per-language LSP options (see SolidLSP / language-server docs in code). |
| `symbol_info_budget` | Seconds per tool call for extra symbol info (docstrings, etc.); `0` disables the budget. |
| `default_max_tool_answer_chars` | Default cap on tool response size. |
| `project_serena_folder_location` | Where per-project data (cache, index) is stored; default `$projectDir/.serena`. |
| `projects` | Registered project paths (updated automatically when you create projects). |
| `excluded_tools` | Tool names to disable. |
| `included_optional_tools` | Optional tools to enable (disabled by default). |
| `fixed_tools` | If non-empty, use exactly this tool set (cannot combine with exclusions/inclusions). |

`language_backend` must be `LSP` (the only supported backend). Legacy keys such as `base_modes`, `read_only_memory_patterns`, `web_dashboard`, or `jetbrains_plugin_server_address` in old config files are ignored.

See the [template file](https://github.com/oraios/serena/blob/main/src/serena/resources/serena_config.template.yml) for comments and defaults.

## Project configuration (`project.yml`)

| Setting | Purpose |
|---------|---------|
| `project_name` | Name used when activating the project in conversation. |
| `languages` | Language servers to start (see [Language Support](../01-about/020_programming-languages.md)). |
| `encoding` | Text encoding for source files (default `utf-8`). |
| `line_ending` | Override global line ending for this project, or leave unset. |
| `ignore_all_files_in_gitignore` | Respect `.gitignore` when ignoring paths (default true). |
| `ignored_paths` | Extra ignore patterns for this project. |
| `additional_workspace_folders` | Extra LSP workspace roots (e.g. monorepo siblings; TypeScript today). |
| `read_only` | If true, disable all editing tools. |
| `initial_prompt` | Text appended when the project is activated (project-specific instructions for the agent). |
| `ls_specific_settings` | Per-language LSP overrides for this project. |
| `symbol_info_budget` | Override global symbol info budget. |
| `excluded_tools` / `included_optional_tools` / `fixed_tools` | Tool set overrides for this project. |

Local overrides: `project.local.yml` in the same directory (typically gitignored).

See the [project template](https://github.com/oraios/serena/blob/main/src/serena/resources/project.template.yml).

## Tool selection

Serena exposes a default MCP tool set plus optional tools. Tune what the agent sees via:

- **Global:** `excluded_tools`, `included_optional_tools`, or `fixed_tools` in `serena_config.yml`
- **Project:** same keys in `project.yml`

The full catalog is documented in [Tools](../01-about/035_tools.md) (auto-generated). Each MCP tool is also exposed as a CLI subcommand; run `serena --help` to list them.

## Command-line overrides

When starting the MCP server, some global options can be overridden:

    serena start-mcp-server --help

Examples: `--log-level`, `--trace-lsp-communication`, `--tool-timeout`, `--project`, `--project-from-cwd`.

## Logs

Log files are written under `~/.serena/logs` (or `%USERPROFILE%\.serena\logs` on Windows). See [Logs](065_logs.md).
