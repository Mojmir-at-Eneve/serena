"""
Serena CLI — agent-facing command-line interface.

Every MCP tool is exposed as a subcommand so agents with terminal access can
call Serena directly without an MCP client.  Output is structured plain text
(no JSON blobs, no special symbols) — readable both by agents and humans.

Usage:
  serena [--project PATH] COMMAND [OPTIONS] [ARGS]

The --project option is global: pass an absolute path or a registered project
name.  If omitted, Serena auto-detects the project from the current working
directory (.serena/project.yml or .git).

Tool subcommands mirror the MCP surface exactly:
  start-here, manage-project,
  search, search-regex, search-and-replace, search-and-replace-regex,
  run-command, run-project-command, manage-project-commands,
  symbols-overview, find-symbol, find-usages, find-implementations,
  find-definition, check-errors, check-symbol-errors,
  rewrite-symbol, inject-code, rename-symbol, delete-symbol

Setup subcommands (human / CI):
  init, project create|index, start-mcp-server
"""

import os
import sys
import time
import collections
from collections.abc import Iterator
from logging import Logger
from pathlib import Path
from typing import Any, Literal

import click
from sensai.util import logging
from sensai.util.logging import FileLoggerContext, datetime_tag
from sensai.util.string import dict_string
from tqdm import tqdm

from serena import serena_version
from serena.config.serena_config import LanguageBackend, ProjectConfig, RegisteredProject, SerenaConfig, SerenaPaths
from serena.constants import SERENA_LOG_FORMAT, SERENA_MANAGED_DIR_NAME
from serena.util.cli_util import AutoRegisteringGroup
from serena.util.logging import MemoryLogHandler
from solidlsp.ls_config import Language
from solidlsp.ls_types import SymbolKind
from solidlsp.util.subprocess_util import subprocess_kwargs

log = logging.getLogger(__name__)
_MAX_CONTENT_WIDTH = 200


# ---------------------------------------------------------------------------
# Project root detection helpers (used by tools and setup commands alike)
# ---------------------------------------------------------------------------

def find_project_root(root: str | Path | None = None) -> str | None:
    """Find project root by walking up from CWD.

    Checks for .serena/project.yml first (explicit Serena project), then .git (git root).

    :param root: If provided, constrains the search to this directory and below
                 (acts as a virtual filesystem root). Search stops at this boundary.
    :return: absolute path to project root or None if not suitable root is found
    """
    current = Path.cwd().resolve()
    boundary = Path(root).resolve() if root is not None else None

    def ancestors() -> Iterator[Path]:
        yield current
        for parent in current.parents:
            yield parent
            if boundary is not None and parent == boundary:
                return

    for directory in ancestors():
        if (directory / ".serena" / "project.yml").is_file():
            return str(directory)

    for directory in ancestors():
        if (directory / ".git").exists():
            return str(directory)

    return None


def resolve_project_root_for_startup(explicit_project: str | None) -> str | None:
    """Resolve the project to activate when the MCP server starts."""
    if explicit_project is not None:
        return explicit_project
    return find_project_root()


def resolve_project_for_activation(project: str | None) -> str:
    """Resolve the project argument for ManageProjectTool / StartHereTool.

    Empty, whitespace-only, or ``"."`` means detect from the server cwd.
    """
    if project is not None and project.strip() and project.strip() != ".":
        return project.strip()
    detected = find_project_root()
    if detected is None:
        raise ValueError(
            "Could not detect a project root from the server working directory. "
            "Call manage_project with the IDE workspace root path (absolute path to the project directory)."
        )
    return detected


# ---------------------------------------------------------------------------
# Agent bootstrap helper
# ---------------------------------------------------------------------------

def _make_agent(project_path: str | None) -> "SerenaAgent":  # type: ignore[name-defined]
    """Create a SerenaAgent for CLI use, optionally pre-activating a project."""
    from serena.agent import SerenaAgent

    serena_config = SerenaConfig.from_config_file()
    agent = SerenaAgent(project=project_path, serena_config=serena_config)
    return agent


def _run_tool(agent: "SerenaAgent", tool_cls: type, **kwargs: Any) -> str:  # type: ignore[name-defined]
    """Instantiate a tool from agent and call apply_ex(), returning the result string."""
    tool = agent.get_tool(tool_cls)
    result = tool.apply_ex(log_call=False, catch_exceptions=False, **kwargs)
    return result


def _run_tool_cli(agent: "SerenaAgent", tool_cls: type, **kwargs: Any) -> None:  # type: ignore[name-defined]
    """Run a tool, rendering CLI text if the result is a ToolResult, else print as-is."""
    from serena.tools.tools_base import ToolResult

    tool = agent.get_tool(tool_cls)
    try:
        raw = tool.apply_ex(log_call=False, catch_exceptions=False, **kwargs)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    # apply_ex normalises ToolResult -> str via to_mcp_string(); but for CLI we want
    # to_cli_text(). We call apply() directly here so we can check the raw return.
    try:
        raw_result = tool.apply(**kwargs)  # type: ignore[call-arg]
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if isinstance(raw_result, ToolResult):
        click.echo(raw_result.to_cli_text())
    else:
        click.echo(str(raw_result))


# ---------------------------------------------------------------------------
# Global --project option passed via Click context
# ---------------------------------------------------------------------------

class _ProjectOption:
    """Holds the resolved project path for the current invocation."""

    def __init__(self, project: str | None) -> None:
        self.project = project


pass_project = click.make_pass_decorator(_ProjectOption, ensure=True)


# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------

@click.group(context_settings={"max_content_width": _MAX_CONTENT_WIDTH})
@click.option(
    "--project",
    "project_path",
    type=click.Path(),
    default=None,
    help="Project root path or registered name. Auto-detected from cwd if omitted.",
)
@click.pass_context
def top_level(ctx: click.Context, project_path: str | None) -> None:
    """Serena LSP toolbox for coding agents.

    Every MCP tool is available as a subcommand. Output is structured plain text.
    """
    ctx.ensure_object(dict)
    ctx.obj["project"] = project_path


# ---------------------------------------------------------------------------
# Helper: create agent lazily from context
# ---------------------------------------------------------------------------

def _agent_from_ctx(ctx: click.Context) -> "SerenaAgent":  # type: ignore[name-defined]
    project_path: str | None = ctx.obj.get("project")
    resolved = resolve_project_root_for_startup(project_path)
    return _make_agent(resolved)


# ---------------------------------------------------------------------------
# Tool subcommands
# ---------------------------------------------------------------------------

@top_level.command("start-here")
@click.pass_context
def cmd_start_here(ctx: click.Context) -> None:
    """Check workspace state and return instructions + directory scan for project activation."""
    from serena.tools.workflow_tools import StartHereTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, StartHereTool)


@top_level.command("manage-project")
@click.option(
    "--action",
    type=click.Choice(["activate", "remove"]),
    default="activate",
    show_default=True,
    help="Activate a project or remove it from configuration.",
)
@click.option("--project", "project", default="", help="Project path or registered name.")
@click.option(
    "--language",
    "language",
    required=True,
    help=(
        "Comma-separated language(s) for project config creation "
        "(e.g. 'csharp', 'python,typescript'). Required for activate; "
        "accepted but unused for remove."
    ),
)
@click.pass_context
def cmd_manage_project(ctx: click.Context, action: str, project: str, language: str) -> None:
    """Activate or remove a Serena project."""
    from serena.tools.config_tools import ManageProjectTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, ManageProjectTool, language=language, action=action, project=project)


@top_level.command("search")
@click.argument("pattern")
@click.option("--path", "relative_path", default="", help="Restrict to this file or directory.")
@click.option("--include", default="", help="Glob pattern to include files (e.g. 'src/**/*.py').")
@click.option("--exclude", default="", help="Glob pattern to exclude files.")
@click.option("--context", "context_lines", type=int, default=2, show_default=True, help="Context lines around each match.")
@click.pass_context
def cmd_search(
    ctx: click.Context,
    pattern: str,
    relative_path: str,
    include: str,
    exclude: str,
    context_lines: int,
) -> None:
    """Search for an exact text string across project files (read-only)."""
    from serena.tools.file_tools import SearchTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        SearchTool,
        pattern=pattern,
        relative_path=relative_path,
        include=include,
        exclude=exclude,
        context_lines=context_lines,
    )


@top_level.command("search-regex")
@click.argument("pattern")
@click.option("--path", "relative_path", default="", help="Restrict to this file or directory.")
@click.option("--include", default="", help="Glob pattern to include files (e.g. 'src/**/*.py').")
@click.option("--exclude", default="", help="Glob pattern to exclude files.")
@click.option("--context", "context_lines", type=int, default=2, show_default=True, help="Context lines around each match.")
@click.pass_context
def cmd_search_regex(
    ctx: click.Context,
    pattern: str,
    relative_path: str,
    include: str,
    exclude: str,
    context_lines: int,
) -> None:
    """Search for a Python regex pattern across project files (read-only)."""
    from serena.tools.file_tools import SearchRegexTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        SearchRegexTool,
        pattern=pattern,
        relative_path=relative_path,
        include=include,
        exclude=exclude,
        context_lines=context_lines,
    )


@top_level.command("search-and-replace")
@click.argument("pattern")
@click.option("--replace", "replacement", required=True, help="Replacement text.")
@click.option("--path", "relative_path", default="", help="Restrict to this file or directory.")
@click.option("--include", default="", help="Glob pattern to include files (e.g. 'src/**/*.py').")
@click.option("--exclude", default="", help="Glob pattern to exclude files.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview changes without modifying files.")
@click.option("--context", "context_lines", type=int, default=2, show_default=True, help="Context lines around each match.")
@click.option("--max-preview", "max_preview_files", type=int, default=3, show_default=True, help="Max files shown in dry-run preview.")
@click.pass_context
def cmd_search_and_replace(
    ctx: click.Context,
    pattern: str,
    replacement: str,
    relative_path: str,
    include: str,
    exclude: str,
    dry_run: bool,
    context_lines: int,
    max_preview_files: int,
) -> None:
    """Replace an exact text string across project files. Use 'search' for read-only matching."""
    from serena.tools.file_tools import SearchAndReplaceTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        SearchAndReplaceTool,
        pattern=pattern,
        replacement=replacement,
        relative_path=relative_path,
        include=include,
        exclude=exclude,
        dry_run=dry_run,
        context_lines=context_lines,
        max_preview_files=max_preview_files,
    )


@top_level.command("search-and-replace-regex")
@click.argument("pattern")
@click.option("--replace", "replacement", required=True, help="Replacement text; use $!1, $!2, ... for captured groups.")
@click.option("--path", "relative_path", default="", help="Restrict to this file or directory.")
@click.option("--include", default="", help="Glob pattern to include files (e.g. 'src/**/*.py').")
@click.option("--exclude", default="", help="Glob pattern to exclude files.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview changes without modifying files.")
@click.option("--context", "context_lines", type=int, default=2, show_default=True, help="Context lines around each match.")
@click.option("--max-preview", "max_preview_files", type=int, default=3, show_default=True, help="Max files shown in dry-run preview.")
@click.pass_context
def cmd_search_and_replace_regex(
    ctx: click.Context,
    pattern: str,
    replacement: str,
    relative_path: str,
    include: str,
    exclude: str,
    dry_run: bool,
    context_lines: int,
    max_preview_files: int,
) -> None:
    """Replace a Python regex pattern across project files. Use 'search-regex' for read-only matching."""
    from serena.tools.file_tools import SearchAndReplaceRegexTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        SearchAndReplaceRegexTool,
        pattern=pattern,
        replacement=replacement,
        relative_path=relative_path,
        include=include,
        exclude=exclude,
        dry_run=dry_run,
        context_lines=context_lines,
        max_preview_files=max_preview_files,
    )


@top_level.command("run-command")
@click.argument("command")
@click.option("--cwd", default=None, help="Working directory (default: project root).")
@click.option("--no-stderr", "capture_stderr", is_flag=True, default=True, flag_value=False, help="Omit stderr from output.")
@click.pass_context
def cmd_run_command(ctx: click.Context, command: str, cwd: str | None, capture_stderr: bool) -> None:
    """Execute a shell command in the project root."""
    from serena.tools.cmd_tools import RunCommandTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, RunCommandTool, command=command, cwd=cwd, capture_stderr=capture_stderr)


@top_level.command("run-project-command")
@click.argument("name")
@click.option("--no-stderr", "capture_stderr", is_flag=True, default=True, flag_value=False, help="Omit stderr from output.")
@click.pass_context
def cmd_run_project_command(ctx: click.Context, name: str, capture_stderr: bool) -> None:
    """Run a named project command (e.g. test, lint, build)."""
    from serena.tools.cmd_tools import RunProjectCommandTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, RunProjectCommandTool, name=name, capture_stderr=capture_stderr)


@top_level.command("manage-project-commands")
@click.option(
    "--action",
    type=click.Choice(["add", "update", "remove", "list"]),
    default="list",
    show_default=True,
    help="Action to perform.",
)
@click.option("--name", default="", help="Command name (e.g. 'test', 'lint').")
@click.option("--command", "command_str", default="", help="Shell command to save.")
@click.option("--description", default="", help="Human-readable description.")
@click.option("--example", "examples", multiple=True, help="Example usage (can be repeated).")
@click.pass_context
def cmd_manage_project_commands(
    ctx: click.Context,
    action: str,
    name: str,
    command_str: str,
    description: str,
    examples: tuple[str, ...],
) -> None:
    """Add, update, remove, or list named project commands stored in project.yml."""
    from serena.tools.cmd_tools import ManageProjectCommandsTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        ManageProjectCommandsTool,
        action=action,
        name=name,
        command=command_str,
        description=description,
        examples=list(examples) if examples else None,
    )


@top_level.command("symbols-overview")
@click.argument("file")
@click.option("--depth", type=int, default=1, show_default=True, help="Child depth (1 = immediate children).")
@click.pass_context
def cmd_symbols_overview(ctx: click.Context, file: str, depth: int) -> None:
    """Show the top-level symbol tree of a file."""
    from serena.tools.symbol_tools import SymbolsOverviewTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, SymbolsOverviewTool, relative_path=file, depth=depth)


@top_level.command("find-symbol")
@click.argument("pattern")
@click.option("--file", "relative_path", default="", help="Restrict to this file or directory.")
@click.option("--body", "include_body", is_flag=True, default=False, help="Include symbol body.")
@click.option("--info", "include_info", is_flag=True, default=False, help="Include hover-style info.")
@click.option("--depth", type=int, default=0, show_default=True, help="Child depth.")
@click.option("--max", "max_matches", type=int, default=12, show_default=True, help="Max matches.")
@click.pass_context
def cmd_find_symbol(
    ctx: click.Context,
    pattern: str,
    relative_path: str,
    include_body: bool,
    include_info: bool,
    depth: int,
    max_matches: int,
) -> None:
    """Find symbols by name pattern (globally or within a file/directory)."""
    from serena.tools.symbol_tools import FindSymbolTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(
        agent,
        FindSymbolTool,
        name_path_pattern=pattern,
        relative_path=relative_path,
        include_body=include_body,
        include_info=include_info,
        depth=depth,
        max_matches=max_matches,
    )


@top_level.command("find-usages")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the symbol.")
@click.pass_context
def cmd_find_usages(ctx: click.Context, name_path: str, relative_path: str) -> None:
    """Find all usages (references) of a symbol across the codebase."""
    from serena.tools.symbol_tools import FindUsagesTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, FindUsagesTool, name_path=name_path, relative_path=relative_path)


@top_level.command("find-implementations")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the abstract symbol.")
@click.option("--info", "include_info", is_flag=True, default=False, help="Include hover-style info.")
@click.pass_context
def cmd_find_implementations(ctx: click.Context, name_path: str, relative_path: str, include_info: bool) -> None:
    """Find concrete implementations of an abstract symbol (interface/abstract class)."""
    from serena.tools.symbol_tools import FindImplementationsTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, FindImplementationsTool, name_path=name_path, relative_path=relative_path, include_info=include_info)


@top_level.command("find-definition")
@click.option("--file", "relative_path", required=True, help="File containing the usage site.")
@click.option("--regex", required=True, help="Regex with one capture group isolating the symbol at its usage.")
@click.option("--body", "include_body", is_flag=True, default=False, help="Include the definition body.")
@click.option("--info", "include_info", is_flag=True, default=False, help="Include hover-style info.")
@click.pass_context
def cmd_find_definition(ctx: click.Context, relative_path: str, regex: str, include_body: bool, include_info: bool) -> None:
    """Find where a symbol is defined given a usage site in a file."""
    from serena.tools.symbol_tools import FindDefinitionTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, FindDefinitionTool, relative_path=relative_path, regex=regex, include_body=include_body, include_info=include_info)


@top_level.command("check-errors")
@click.argument("file")
@click.option("--start-line", type=int, default=0, show_default=True, help="First 0-based line to include.")
@click.option("--end-line", type=int, default=-1, show_default=True, help="Last 0-based line (-1 = end of file).")
@click.option("--min-severity", type=int, default=4, show_default=True, help="Min LSP severity (1=Error..4=Hint).")
@click.pass_context
def cmd_check_errors(ctx: click.Context, file: str, start_line: int, end_line: int, min_severity: int) -> None:
    """Get LSP diagnostics (errors, warnings, hints) for a file."""
    from serena.tools.symbol_tools import CheckErrorsTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, CheckErrorsTool, relative_path=file, start_line=start_line, end_line=end_line, min_severity=min_severity)


@top_level.command("check-symbol-errors")
@click.argument("name_path")
@click.option("--file", "reference_file", default="", help="File to disambiguate the symbol.")
@click.option("--check-usages", is_flag=True, default=False, help="Also check diagnostics for symbols that use this one.")
@click.pass_context
def cmd_check_symbol_errors(ctx: click.Context, name_path: str, reference_file: str, check_usages: bool) -> None:
    """Get LSP diagnostics for a specific symbol (optionally including its usages)."""
    from serena.tools.symbol_tools import CheckSymbolErrorsTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, CheckSymbolErrorsTool, name_path=name_path, reference_file=reference_file, check_usages=check_usages)


@top_level.command("rewrite-symbol")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the symbol.")
@click.option("--body", required=True, help="Complete new symbol definition including signature.")
@click.pass_context
def cmd_rewrite_symbol(ctx: click.Context, name_path: str, relative_path: str, body: str) -> None:
    """Replace a symbol's full implementation with new code."""
    from serena.tools.symbol_tools import RewriteSymbolTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, RewriteSymbolTool, name_path=name_path, relative_path=relative_path, body=body)


@top_level.command("inject-code")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the anchor symbol.")
@click.option(
    "--position",
    type=click.Choice(["before", "after"]),
    required=True,
    help="Insert before or after the symbol.",
)
@click.option("--body", required=True, help="Code to insert.")
@click.pass_context
def cmd_inject_code(ctx: click.Context, name_path: str, relative_path: str, position: str, body: str) -> None:
    """Insert code immediately before or after a symbol's definition."""
    from serena.tools.symbol_tools import InjectCodeTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, InjectCodeTool, name_path=name_path, relative_path=relative_path, position=position, body=body)


@top_level.command("rename-symbol")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the symbol.")
@click.option("--new-name", required=True, help="New name for the symbol.")
@click.pass_context
def cmd_rename_symbol(ctx: click.Context, name_path: str, relative_path: str, new_name: str) -> None:
    """Rename a symbol throughout the codebase using language server refactoring."""
    from serena.tools.symbol_tools import RenameSymbolTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, RenameSymbolTool, name_path=name_path, relative_path=relative_path, new_name=new_name)


@top_level.command("delete-symbol")
@click.argument("name_path")
@click.option("--file", "relative_path", required=True, help="File containing the symbol.")
@click.pass_context
def cmd_delete_symbol(ctx: click.Context, name_path: str, relative_path: str) -> None:
    """Delete a symbol if it has no references (safe delete)."""
    from serena.tools.symbol_tools import DeleteSymbolTool

    agent = _agent_from_ctx(ctx)
    _run_tool_cli(agent, DeleteSymbolTool, name_path=name_path, relative_path=relative_path)


# ---------------------------------------------------------------------------
# Setup subcommands
# ---------------------------------------------------------------------------

@top_level.command("init")
def cmd_init() -> None:
    """Create ~/.serena/serena_config.yml from the template."""
    click.echo(f"Serena version: {serena_version()}")
    serena_config = SerenaConfig.from_config_file()
    serena_config.save()
    click.echo(f"Configuration file: {serena_config.config_file_path}")


@top_level.command("start-mcp-server")
@click.option("--project", "project", type=click.Path(), default=None, help="Path or name of project to activate at startup.")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    default=None,
    help="Override log level in config.",
)
@click.option("--trace-lsp-communication", type=bool, is_flag=False, default=None, help="Whether to trace LSP communication.")
@click.option("--tool-timeout", type=float, default=None, help="Override tool execution timeout in config.")
def cmd_start_mcp_server(
    project: str | None,
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None,
    trace_lsp_communication: bool | None,
    tool_timeout: float | None,
) -> None:
    """Start the Serena MCP server (stdio transport)."""
    from serena.mcp import SerenaMCPFactory

    Logger.root.setLevel(logging.INFO)
    formatter = logging.Formatter(SERENA_LOG_FORMAT)
    memory_log_handler = MemoryLogHandler()
    Logger.root.addHandler(memory_log_handler)
    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.formatter = formatter
    Logger.root.addHandler(stderr_handler)
    log_path = SerenaPaths().get_next_log_file_path("mcp")
    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.formatter = formatter
    Logger.root.addHandler(file_handler)

    project = resolve_project_root_for_startup(project)
    if project is None:
        log.warning(
            "No project root auto-detected from cwd %s; call start_here or manage_project with the workspace path",
            os.getcwd(),
        )
    else:
        log.info("Using project root %s (cwd %s)", project, os.getcwd())

    factory = SerenaMCPFactory(transport="stdio", project=project, memory_log_handler=memory_log_handler)
    server = factory.create_mcp_server(
        log_level=log_level,
        trace_lsp_communication=trace_lsp_communication,
        tool_timeout=tool_timeout,
    )
    server.run(transport="stdio")


# ---------------------------------------------------------------------------
# Project setup subgroup
# ---------------------------------------------------------------------------

class _ProjectType(click.ParamType):
    name = "[PROJECT_NAME|PROJECT_PATH]"

    def convert(self, value: str, param: Any, ctx: Any) -> str:
        path = Path(value).resolve()
        if path.exists() and path.is_dir():
            return str(path)
        return value


_PROJECT_TYPE = _ProjectType()


@top_level.group("project")
def project_group() -> None:
    """Manage Serena project configurations."""


@project_group.command("create")
@click.argument("project_path", type=click.Path(exists=True, file_okay=False), default=os.getcwd())
@click.option("--name", type=str, default=None, help="Project name; defaults to directory name.")
@click.option("--language", type=str, multiple=True, help="Language(s) to configure. Can be repeated.")
@click.option("--index", is_flag=True, help="Index the project after creation.")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    default="WARNING",
    help="Log level for indexing (only if --index is set).",
)
@click.option("--timeout", type=float, default=10, help="Per-file indexing timeout (only if --index is set).")
def project_create(project_path: str, name: str | None, language: tuple[str, ...], index: bool, log_level: str, timeout: float) -> None:
    """Create a new Serena project configuration."""
    try:
        registered_project, messages = _create_project(project_path, name, language)
        for msg in messages:
            click.echo(msg)
        if index:
            click.echo("Indexing project...")
            _index_project(registered_project, log_level, timeout=timeout)
    except FileExistsError as e:
        raise click.ClickException(f"Project already exists: {e}\nUse 'serena project index' to index an existing project.")
    except ValueError as e:
        raise click.ClickException(str(e))


@project_group.command("create-all")
@click.argument("parent_path", type=click.Path(exists=True, file_okay=False), default=os.getcwd())
@click.option("--language", type=str, multiple=True, help="Language(s) applied to every sub-project. Inferred per-project if omitted.")
def project_create_all(parent_path: str, language: tuple[str, ...]) -> None:
    """Create Serena project configs for all immediate child directories under PARENT_PATH.

    Scans one level deep. Directories that already have .serena/project.yml are skipped.
    Use this to initialise a monorepo in a single command instead of running
    'project create' once per child.
    """
    parent = Path(parent_path).resolve()
    results = _create_all_subprojects(str(parent), language)
    for line in results:
        click.echo(line)


def _create_all_subprojects(parent_path: str, language: tuple[str, ...]) -> list[str]:
    """Scan immediate children of *parent_path* and create a Serena project for each.

    Returns a list of human-readable result lines (created / skipped / error).
    Extracted as a standalone function so the MCP tool can reuse it without Click.
    """
    from serena.config.serena_config import SerenaConfig

    parent = Path(parent_path).resolve()
    serena_config = SerenaConfig.from_config_file()
    lines: list[str] = []

    try:
        children = sorted(entry for entry in parent.iterdir() if entry.is_dir())
    except PermissionError as exc:
        return [f"Error: cannot read {parent}: {exc}"]

    if not children:
        return [f"No subdirectories found under {parent}."]

    created = skipped = errors = 0
    for child in children:
        yml_path = serena_config.get_project_yml_location(str(child))
        if os.path.exists(yml_path):
            lines.append(f"  skip    {child.name}  (already configured)")
            skipped += 1
            continue
        try:
            # Discard messages returned by _create_project — they would write to
            # stdout if echoed, corrupting the MCP JSON protocol stream.  The
            # per-project outcome is captured in the result lines returned by this
            # function instead.
            _create_project(str(child), None, language)
            lines.append(f"  created {child.name}")
            created += 1
        except Exception as exc:
            lines.append(f"  error   {child.name}: {exc}")
            errors += 1

    summary = f"\nDone: {created} created, {skipped} skipped, {errors} errors."
    lines.append(summary)
    return lines


@project_group.command("index")
@click.argument("project", type=_PROJECT_TYPE, default=os.getcwd(), required=False)
@click.option("--name", type=str, default=None, help="Project name (only if auto-creating project.yml).")
@click.option("--language", type=str, multiple=True, help="Language(s). Inferred if not specified.")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    default="WARNING",
    help="Log level for indexing.",
)
@click.option("--timeout", type=float, default=10, help="Per-file indexing timeout.")
def project_index(project: str, name: str | None, language: tuple[str, ...], log_level: str, timeout: float) -> None:
    """Index a project's symbols into the LSP cache."""
    serena_config = SerenaConfig.from_config_file()
    registered_project = serena_config.get_registered_project(project, autoregister=True)
    if registered_project is None:
        click.echo(f"No existing project found for '{project}'. Attempting auto-creation ...")
        try:
            registered_project, messages = _create_project(project, name, language)
            for msg in messages:
                click.echo(msg)
        except Exception as e:
            raise click.ClickException(str(e))
    _index_project(registered_project, log_level, timeout=timeout)


# ---------------------------------------------------------------------------
# Internal helpers for project create / index
# ---------------------------------------------------------------------------

def _create_project(project_path: str, name: str | None, language: tuple[str, ...]) -> tuple[RegisteredProject, list[str]]:
    """Create a Serena project configuration and return the registered project together
    with a list of human-readable messages (confirmation + any warnings).

    Messages are returned rather than printed so that callers running inside the MCP
    server (where stdout is the JSON protocol stream) can safely discard or forward
    them without corrupting the transport.  CLI callers are responsible for echoing
    the returned messages.
    """
    project_root = Path(project_path).resolve()
    serena_config = SerenaConfig.from_config_file()
    yml_path = serena_config.get_project_yml_location(str(project_root))
    if os.path.exists(yml_path):
        raise FileExistsError(f"Project file {yml_path} already exists.")

    languages: list[Language] = []
    if language:
        for lang in language:
            try:
                languages.append(Language(lang.lower()))
            except ValueError:
                all_langs = [l.value for l in Language]
                raise ValueError(f"Unknown language '{lang}'. Supported: {all_langs}")

    generated_conf = ProjectConfig.autogenerate(
        project_root=project_path,
        serena_config=serena_config,
        project_name=name,
        languages=languages if languages else None,
        interactive=True,
    )
    languages_str = ", ".join([lang.value for lang in generated_conf.languages]) if generated_conf.languages else "N/A"

    messages: list[str] = [f"Generated project with languages {{{languages_str}}} at {yml_path}."]

    # Warn when immediate children already have .serena/project.yml configs.
    # Creating a root-level config on top of an existing monorepo layout can confuse
    # users who expect only the child projects — point them at the intended workflow.
    child_configs = [
        entry.name
        for entry in sorted(project_root.iterdir())
        if entry.is_dir()
        and (entry / SERENA_MANAGED_DIR_NAME / ProjectConfig.SERENA_PROJECT_FILE).exists()
    ]
    if child_configs:
        messages.append(
            f"Warning: {len(child_configs)} child director{'y' if len(child_configs) == 1 else 'ies'} "
            f"already ha{'s' if len(child_configs) == 1 else 've'} .serena/project.yml "
            f"({', '.join(child_configs)}). "
            "Activating this parent will load all of them alongside the root project. "
            "If you only want the monorepo model (parent activates children, no root config), "
            "remove this root project.yml and activate the parent directory directly."
        )

    registered_project = serena_config.get_registered_project(str(project_root))
    if registered_project is None:
        registered_project = RegisteredProject(str(project_root), generated_conf)
        serena_config.add_registered_project(registered_project)
    return registered_project, messages


class ProjectCommands:
    """
    Namespace for programmatic access to project setup commands (used in tests).
    Provides the Click command objects and the internal helpers.
    """

    create = project_create
    create_all = project_create_all
    index = project_index

    @staticmethod
    def _create_project(project_path: str, name: str | None, language: tuple[str, ...]) -> RegisteredProject:
        # Unpack and expose only the project; messages are for CLI callers.
        project, _ = _create_project(project_path, name, language)
        return project

    @staticmethod
    def _create_all_subprojects(parent_path: str, language: tuple[str, ...]) -> list[str]:
        return _create_all_subprojects(parent_path, language)


class TopLevelCommands:
    """Namespace for programmatic access to top-level commands (used in tests)."""

    start_mcp_server = cmd_start_mcp_server


def _index_project(registered_project: RegisteredProject, log_level: str, timeout: float) -> None:
    from sensai.util.string import dict_string as _dict_string

    lvl = logging.getLevelNamesMapping()[log_level.upper()]
    logging.configure(level=lvl)
    serena_config = SerenaConfig.from_config_file()
    proj = registered_project.get_project_instance(serena_config=serena_config)
    click.echo(f"Indexing symbols in {proj} ...")
    ls_mgr = proj.create_language_server_manager()
    try:
        log_file = os.path.join(proj.project_root, ".serena", "logs", "indexing.txt")
        files = proj.gather_source_files()
        collected_exceptions: list[Exception] = []
        files_failed: list[str] = []
        language_file_counts: dict[Language, int] = collections.defaultdict(lambda: 0)
        last_save_time = time.monotonic()
        for i, f in enumerate(tqdm(files, desc="Indexing")):
            try:
                ls = ls_mgr.get_language_server(f)
                ls.request_document_symbols(f)
                language_file_counts[ls.language] += 1
            except Exception as e:
                log.error(f"Failed to index {f}, continuing.")
                collected_exceptions.append(e)
                files_failed.append(f)
            now = time.monotonic()
            if now - last_save_time >= 30:
                ls_mgr.save_all_caches()
                last_save_time = now
        reported_language_file_counts = {k.value: v for k, v in language_file_counts.items()}
        click.echo(f"Indexed files per language: {_dict_string(reported_language_file_counts, brackets=None)}")
        ls_mgr.save_all_caches()
        if files_failed:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "w") as f:
                for file, exception in zip(files_failed, collected_exceptions, strict=True):
                    f.write(f"{file}\n")
                    f.write(f"{exception}\n")
            click.echo(f"Failed to index {len(files_failed)} files, see:\n{log_file}")
    finally:
        ls_mgr.stop_all()
