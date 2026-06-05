"""
Session-start tool for the Serena MCP toolbox.

start_here is the required first call for every session. It is intentionally
lightweight: it checks whether a workspace is already active, scans the server
working directory one level deep to show which subdirectories are already
configured, and returns the full Serena guide plus the tool catalog.

Activation is NOT performed by start_here — the agent reads the scan output,
decides whether this is a single project or a monorepo, and calls
manage_project or initialize_subprojects with the correct arguments (including
the required language parameter).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from serena.tools.tools_base import Tool, ToolMarkerDoesNotRequireActiveProject, ToolRegistry

if TYPE_CHECKING:
    from serena.workspace import SerenaWorkspace

# -------------------------------------------------------------------------
# Agent instructions — compact reference shown at session start.
# Design goals:
#   * Actionable: tell agents what to DO, not just what things are.
#   * Minimal: scannable in one pass. No prose padding.
#   * Live catalog: injected at runtime so it always reflects the current
#     tool set (no drift when tools are added/removed).
# -------------------------------------------------------------------------
_INSTRUCTIONS = """\
You are using Serena — an LSP-backed toolbox for IDE-grade code intelligence.
It gives you symbol search, go-to-definition, find-usages, diagnostics, and
semantic edits backed by the language server for the active project.

QUICK START
1. Run start_here once per session (REQUIRED FIRST STEP). It checks whether a
   workspace is already active and scans the server working directory so you
   can decide how to activate.
2. If no workspace is active, review the scan output and call:
   - Single project: manage_project(action="activate", project=<path>, language=<lang>)
   - Monorepo: initialize_subprojects(parent_path=<path>, language=<lang>) first,
     then manage_project(action="activate", project=<path>, language=<lang>)
3. Explore a file: symbols_overview <file>
4. Find symbols: find_symbol <pattern>
5. Find text: search <exact_text>  or  search_regex <pattern>
6. Edit semantically: rewrite_symbol / inject_code / rename_symbol
7. Replace text: search_and_replace <exact>  or  search_and_replace_regex <pattern>
8. Verify: check_errors <file>  or  run_project_command test

PATH CONVENTIONS
All file paths are relative to the workspace root. In multi-project
workspaces every result is prefixed [project_id] so you can tell which
project a symbol or match belongs to.

MULTI-PROJECT WORKSPACES (monorepos)
When multiple sub-directories each have .serena/project.yml, activating
the parent folder loads ALL of them as one workspace.

Key rules:
- Activate the PARENT (monorepo root), never individual child dirs — activating
  a child replaces the whole workspace with that one project.
- File paths include the sub-project folder: backend/src/Foo.cs, not src/Foo.cs.
- Every result carries [project_id] so you always know which project owns it.
- If a language server fails in one unit, the others keep running (degraded mode).
- To set up: call initialize_subprojects with the parent path and language, then
  activate the parent with manage_project.

LINE NUMBERS
All line numbers are 0-based (first line of a file is line 0).

EDITING STRATEGY
Prefer symbol-level tools (rewrite_symbol, inject_code, rename_symbol) over
text search-and-replace — they use the language server so they are accurate
even when a symbol appears in multiple files.
Use search / search_regex to locate text without modifying files.
Use search_and_replace (exact) or search_and_replace_regex (Python regex)
for cross-cutting text changes such as comments, strings, or config values.
After any edit, call check_errors to catch new diagnostics early.

OVERLOAD DISAMBIGUATION (C#, Java, …)
When a symbol name has multiple overloads, append one of the following to the
method segment in name_path to select the right one:
  Class/Method[n]              — 0-based index (use find_symbol depth:1 to discover)
  Class/Method@line:42         — 0-based line of the identifier (stable across renames)
  Class/Method(TypeA, TypeB)   — substring of the LSP signature (stable, human-readable)
Use rename_symbol with dry_run=True to confirm the target before applying.
Re-run find_symbol(depth:1) after each rename — indices shift when overloads are removed.

SHELL COMMANDS
Use run_command for one-off shell operations (build, test, git, etc.).
Save recurring commands with manage_project_commands so they are callable by
name via run_project_command in future sessions.

TOOL CATALOG
{tool_catalog}
"""


def _build_setup_notes(workspace: "SerenaWorkspace") -> str:
    """
    Build an actionable SETUP NOTES block for units that need attention.

    Returns an empty string when everything is healthy so the section is
    omitted entirely from start_here output — no noise when nothing is wrong.
    """
    attention = workspace.attention_units()
    if not attention:
        return ""

    lines: list[str] = []
    for project_id, tag in attention:
        if tag == "LS_ERROR":
            lines.append(
                f"- [{project_id}] Language server failed to start (LS_ERROR). "
                "Check that the required runtime is on PATH and re-activate, "
                "or remove the unsupported language from .serena/project.yml."
            )
        elif tag == "DEGRADED":
            lines.append(
                f"- [{project_id}] Some language servers failed (DEGRADED). "
                "Run check_errors or inspect the Serena log for details."
            )
        elif tag == "NO_SOURCE":
            lines.append(
                f"- [{project_id}] Language server started but no source files were indexed. "
                "Verify the configured languages match the files in this directory."
            )
        else:
            lines.append(f"- [{project_id}] Status: {tag}")

    return "\n".join(lines)


def _build_tool_catalog() -> str:
    """
    Build a flat, alphabetically-sorted tool catalog from the live registry.

    Each line is "  <name>: <first-line-of-description>".
    Using the live registry prevents the catalog from drifting when tools are
    added, removed, or renamed.
    """
    registry = ToolRegistry()
    lines: list[str] = []
    for name in sorted(registry.get_tool_names_default_enabled()):
        cls = registry.get_tool_class_by_name(name)
        # Use only the first sentence of the description for brevity.
        description = (cls.get_tool_description() or "").strip().splitlines()[0]
        lines.append(f"  {name}: {description}")
    return "\n".join(lines)


def _scan_cwd_for_projects() -> str:
    """
    Non-recursively scan the server cwd and report which immediate
    subdirectories already have a .serena/project.yml.

    This is a lightweight, read-only check (os.scandir + os.path.isfile only)
    intended to give the agent enough information to decide between
    single-project and monorepo activation without triggering any indexing or
    language detection.
    """
    cwd = os.getcwd()
    lines: list[str] = [f"Working directory: {cwd}"]

    try:
        subdirs = sorted(
            (entry for entry in os.scandir(cwd) if entry.is_dir(follow_symlinks=False)),
            key=lambda e: e.name,
        )
    except PermissionError:
        lines.append("(cannot scan working directory — permission denied)")
        return "\n".join(lines)

    root_yml = os.path.join(cwd, ".serena", "project.yml")
    has_root_config = os.path.isfile(root_yml)
    if has_root_config:
        lines.append("Root has .serena/project.yml — configured as a single-project root.")

    if not subdirs:
        lines.append("No subdirectories found.")
    else:
        configured: list[str] = []
        unconfigured: list[str] = []
        for entry in subdirs:
            yml = os.path.join(entry.path, ".serena", "project.yml")
            if os.path.isfile(yml):
                configured.append(entry.name)
            else:
                unconfigured.append(entry.name)

        if configured:
            lines.append(
                f"Subdirectories with .serena/project.yml ({len(configured)}): "
                + ", ".join(configured)
            )
        if unconfigured:
            lines.append(
                f"Subdirectories without .serena/project.yml ({len(unconfigured)}): "
                + ", ".join(unconfigured)
            )

    # Provide an actionable decision hint so the agent knows its next step.
    has_any_config = has_root_config or bool(configured if subdirs else False)

    if has_any_config:
        lines.append(
            "\nDECISION: Some projects are already configured.\n"
            "- To activate as monorepo (all configured subdirs): "
            'manage_project(action="activate", project="<parent_path>", language="<lang>")\n'
            "- If some subdirs are still missing config: "
            'initialize_subprojects(parent_path="<path>", language="<lang>") first, '
            'then manage_project(action="activate", ...)\n'
            "- To activate a single configured project: "
            'manage_project(action="activate", project="<subdir_path>", language="<lang>")'
        )
    else:
        lines.append(
            "\nDECISION: No .serena/project.yml files found — projects need initialisation.\n"
            "- Single project: "
            'manage_project(action="activate", project="<path>", language="<lang>")\n'
            "- Monorepo: "
            'initialize_subprojects(parent_path="<path>", language="<lang>") first, '
            'then manage_project(action="activate", project="<path>", language="<lang>")'
        )

    return "\n".join(lines)


class StartHereTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Checks workspace state, scans the server working directory for project
    configuration files, and returns the Serena guide plus the full tool
    catalog.

    Call this at the start of every session before using any other tool.
    Does NOT activate any project — the agent inspects the scan output and
    calls manage_project or initialize_subprojects with the correct arguments,
    including the required language parameter.
    """

    def apply(self) -> str:
        """
        Start the session. Returns workspace status (health summary if active,
        or a directory scan with activation guidance if not), the Serena guide,
        and the full tool catalog.

        No project activation is performed. After reviewing the output, call
        manage_project or initialize_subprojects to activate the workspace.

        :return: full session context: guide + workspace status/scan + config + tool catalog.
        """
        # Build the main instruction block with a live tool catalog.
        catalog = _build_tool_catalog()
        instructions = _INSTRUCTIONS.format(tool_catalog=catalog)

        # Config overview (active tools, version, settings).
        config_overview = self.agent.get_current_config_overview()

        workspace = self.agent.get_active_workspace()

        sections: list[str] = [instructions]
        sections.append(f"ACTIVE CONFIGURATION\n{config_overview}")

        if workspace is not None:
            # Workspace is already active — report health only, no scan needed.
            health = workspace.health_summary()
            sections.append(f"WORKSPACE STATUS\n{health}")
            setup_notes = _build_setup_notes(workspace)
            if setup_notes:
                sections.append(f"SETUP NOTES\n{setup_notes}")
        else:
            # No active workspace — scan cwd and guide the agent to decide.
            scan_result = _scan_cwd_for_projects()
            known = self.agent.serena_config.project_names
            known_hint = (
                f"\nKnown registered projects: {known}" if known else ""
            )
            sections.append(
                "NO WORKSPACE ACTIVE\n\n" + scan_result + known_hint
            )

        return "\n\n---\n\n".join(sections)
