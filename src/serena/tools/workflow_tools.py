"""
Session-start tool for the Serena MCP toolbox.

StartHereTool replaces the former trio of initial_instructions,
get_current_config, and get_workspace_status with a single call that
auto-detects (and optionally activates) a project, then returns
instructions, workspace health, and the tool catalog in one shot.
"""

from serena.tools.tools_base import Tool, ToolMarkerDoesNotRequireActiveProject, ToolRegistry

# Agent instructions: concise, IDE-like affordances.
# No process prescriptions, no omni-tool marketing.
_INSTRUCTIONS = """\
Serena is an LSP-backed toolbox that gives you IDE-grade code intelligence:
symbols, references, definitions, implementations, diagnostics, and semantic edits.

MULTI-PROJECT WORKSPACES
When the workspace contains multiple projects every result is prefixed with
[project_id] so you know which project it belongs to. Pass paths relative
to the workspace root; Serena routes them to the right project.

LINE NUMBERS
All line numbers returned by Serena tools are 0-based.

EDIT WORKFLOW
Prefer symbol-level edits (rewrite_symbol, inject_code, rename_symbol) over
pattern-based ones. Use search_and_replace when you need to change text that
does not map cleanly to a single symbol body.

TOOL CATALOG
{tool_catalog}
"""


def _build_tool_catalog() -> str:
    lines: list[str] = []
    registry = ToolRegistry()
    for name in sorted(registry.get_tool_names_default_enabled()):
        cls = registry.get_tool_class_by_name(name)
        desc = cls.get_tool_description().strip().replace("\n", " ")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


class StartHereTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    One-call session start: auto-detects and activates the project, then
    returns usage instructions, workspace health, and the full tool catalog.

    Call this at the beginning of every session before using any other tool.
    If a project is already active the activation step is skipped gracefully.
    """

    def apply(self, project: str = "") -> str:
        """
        Initialize the session. Activates the project if not already active, then
        returns the Serena manual, workspace health summary, and tool catalog.

        If no project is found and none can be detected, clear guidance is returned
        on how to proceed (call manage_project with the workspace path).

        :param project: optional project path or registered name to activate.
            If empty, Serena tries to auto-detect from the server working directory.
        :return: session context: instructions, workspace health, and tool catalog.
        """
        from serena.cli import find_project_root, resolve_project_for_activation

        activation_note = ""
        replacement_warning = ""

        # Attempt auto-activation only when no project is currently active.
        if self.agent.get_active_project() is None:
            try:
                resolved = resolve_project_for_activation(project or None)
                self.agent.activate_project_from_path_or_name(resolved)
                replacement_warning = self.agent.consume_last_workspace_replacement_warning() or ""
            except ValueError:
                # Could not detect a project; provide clear guidance.
                known = self.agent.serena_config.project_names
                known_hint = f"  Known projects: {known}" if known else ""
                activation_note = (
                    "\nNO PROJECT ACTIVE\n"
                    "Call manage_project with the workspace root path to activate a project."
                    + known_hint
                )

        # Build the workspace health block.
        workspace = self.agent.get_active_workspace()
        if workspace is not None:
            health = workspace.health_summary()
        else:
            health = "No workspace active."

        # Config overview (active tools, version).
        config_overview = self.agent.get_current_config_overview()

        # Build instructions with live tool catalog.
        catalog = _build_tool_catalog()
        instructions = _INSTRUCTIONS.format(tool_catalog=catalog)

        sections: list[str] = [instructions]
        if replacement_warning:
            sections.append(f"NOTE: {replacement_warning}")
        sections.append(f"WORKSPACE STATUS\n{health}")
        sections.append(f"CONFIGURATION\n{config_overview}")
        if activation_note:
            sections.append(activation_note)

        return "\n\n---\n\n".join(sections)
