"""
Session-start tool for the Serena MCP toolbox.

start_here is the single entry point for new sessions: it auto-detects the
project, initialises the workspace, and returns everything an agent needs to
start working — instructions, workspace health, and the tool catalog — in a
single call.
"""

from serena.tools.tools_base import Tool, ToolMarkerDoesNotRequireActiveProject, ToolRegistry

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
3. Find code: find_symbol <pattern>  or  search_and_replace <pattern>
4. Edit semantically: rewrite_symbol / inject_code / rename_symbol
5. Verify: check_errors <file>  or  run_project_command test

PATH CONVENTIONS
All file paths are relative to the workspace root. In multi-project
workspaces every result is prefixed [project_id:] so you can tell which
project a symbol or match belongs to.

LINE NUMBERS
All line numbers are 0-based (first line of a file is line 0).

EDITING STRATEGY
Prefer symbol-level tools (rewrite_symbol, inject_code, rename_symbol) over
text search-and-replace — they use the language server so they are accurate
even when a symbol appears in multiple files.
Use search_and_replace for text that does not map to a named symbol, or for
cross-cutting changes like updating comments, strings, or config values.
After any edit, call check_errors to catch new diagnostics early.

SHELL COMMANDS
Use run_command for one-off shell operations (build, test, git, etc.).
Save recurring commands with manage_project_commands so they are callable by
name via run_project_command in future sessions.

TOOL CATALOG
{tool_catalog}
"""


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

        return "\n\n---\n\n".join(sections)
