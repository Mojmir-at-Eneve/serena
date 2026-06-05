"""
Session-start tool for the Serena MCP toolbox.

start_here is the single entry point for new sessions: it auto-detects the
project, initialises the workspace, and returns everything an agent needs to
start working — instructions, workspace health, and the tool catalog — in a
single call.
"""

from __future__ import annotations

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
1. Run start_here once per session. It activates the project and returns
   this guide plus workspace health.
2. Explore a file: symbols_overview <file>
3. Find symbols: find_symbol <pattern>
4. Find text: search <exact_text>  or  search_regex <pattern>
5. Edit semantically: rewrite_symbol / inject_code / rename_symbol
6. Replace text: search_and_replace <exact>  or  search_and_replace_regex <pattern>
7. Verify: check_errors <file>  or  run_project_command test

PATH CONVENTIONS
All file paths are relative to the workspace root. In multi-project
workspaces every result is prefixed [project_id] so you can tell which
project a symbol or match belongs to.

MULTI-PROJECT WORKSPACES (monorepos)
When multiple sub-directories each have .serena/project.yml, activating
the parent folder loads ALL of them as one workspace. start_here lists
every active project unit and its language-server status.

Key rules:
- Activate the PARENT (monorepo root), never individual child dirs — activating
  a child replaces the whole workspace with that one project.
- File paths include the sub-project folder: backend/src/Foo.cs, not src/Foo.cs.
- Every result carries [project_id] so you always know which project owns it.
- If a language server fails in one unit, the others keep running (degraded mode).
- To set up: run `serena project create` in each sub-directory, then activate root.

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


class StartHereTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Initialises the session: auto-detects and activates the project, then
    returns the Serena guide, workspace health, and the full tool catalog.

    Call this at the start of every session before using any other tool.
    If a project is already active the activation step is a no-op.
    """

    def apply(self, project: str = "") -> str:
        """
        Start the session. Activates the project (if not already active), then
        returns usage instructions, workspace health, and the tool catalog.

        Pass a project path or registered name only when you want to override
        the auto-detected project. Normally leave this empty.

        :param project: optional project root path or registered project name.
            Leave empty to auto-detect from the server working directory.
        :return: full session context: guide + workspace status + tool catalog.
        """
        from serena.cli import resolve_project_for_activation

        activation_note = ""
        replacement_warning = ""

        # Only attempt activation when no project is currently active.
        if self.agent.get_active_project() is None:
            try:
                resolved = resolve_project_for_activation(project or None)
                self.agent.activate_project_from_path_or_name(resolved)
                replacement_warning = self.agent.consume_last_workspace_replacement_warning() or ""
            except ValueError:
                # Could not detect a project; return clear guidance.
                known = self.agent.serena_config.project_names
                known_hint = f"\nKnown registered projects: {known}" if known else ""
                activation_note = (
                    "NO PROJECT ACTIVE\n"
                    "Call manage_project with the workspace root path to activate a project."
                    + known_hint
                )

        # Workspace health summary.
        workspace = self.agent.get_active_workspace()
        health = workspace.health_summary() if workspace is not None else "No workspace active."

        # Build the main instruction block with a live tool catalog.
        catalog = _build_tool_catalog()
        instructions = _INSTRUCTIONS.format(tool_catalog=catalog)

        # Config overview (active tools, version, settings).
        config_overview = self.agent.get_current_config_overview()

        sections: list[str] = [instructions]
        if replacement_warning:
            sections.append(f"NOTE: {replacement_warning}")
        sections.append(f"WORKSPACE STATUS\n{health}")
        sections.append(f"ACTIVE CONFIGURATION\n{config_overview}")
        if activation_note:
            sections.append(activation_note)

        # Actionable setup notes — only emitted when something needs fixing.
        if workspace is not None:
            setup_notes = _build_setup_notes(workspace)
            if setup_notes:
                sections.append(f"SETUP NOTES\n{setup_notes}")

        return "\n\n---\n\n".join(sections)
