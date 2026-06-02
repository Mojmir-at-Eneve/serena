"""
Lightweight workflow tools for the ground-truth MCP toolbox.
"""

from serena.tools import Tool, ToolMarkerDoesNotRequireActiveProject, ToolRegistry

# Agent instructions: concise, IDE-like affordances.
# No process prescriptions, no omni-tool marketing.
_INITIAL_INSTRUCTIONS = """\
Serena is an LSP-backed MCP toolbox that gives you IDE-grade code intelligence:
symbols, references, declarations, implementations, diagnostics, and semantic edits.

## Workspace activation
If tools report "No active workspace", call `activate_project` with the IDE
workspace root path. Serena will discover all nested projects automatically
and activate them as a workspace.

## Multi-project workspaces
When the workspace contains multiple projects, every result is prefixed with
`[project_id]` so you know which project it belongs to. Pass paths relative
to the workspace root; Serena routes them to the right project.

## Grounding
Call `get_workspace_status` at session start to see all active project units,
their languages, language-server health, and any degraded states.

## Line numbers
All line numbers returned by Serena tools are **0-based**.

## Tool catalog
{tool_catalog}
"""


def _build_tool_catalog() -> str:
    lines: list[str] = []
    registry = ToolRegistry()
    for name in sorted(registry.get_tool_names_default_enabled()):
        cls = registry.get_tool_class_by_name(name)
        desc = cls.get_tool_description().strip().replace("\n", " ")
        lines.append(f"- `{name}`: {desc}")
    return "\n".join(lines)


class InitialInstructionsTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Returns concise Serena usage instructions (for clients that skip MCP server instructions).
    """

    def apply(self) -> str:
        """
        Returns a brief reference for Serena's tool catalog, workspace activation,
        and multi-project path conventions.
        Call once at session start if your client did not already provide MCP server instructions.
        """
        catalog = _build_tool_catalog()
        return _INITIAL_INSTRUCTIONS.format(tool_catalog=catalog)


class GetWorkspaceStatusTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Returns a health summary of the active workspace: project units, languages,
    language-server status, and any degraded/failed states.
    """

    def apply(self) -> str:
        """
        Returns the current workspace health summary.

        Use this at the start of a session to orient yourself: which projects are
        active, what languages each project uses, and whether any language servers
        failed to start.  A `[project_id]` prefix is shown for each project unit.
        """
        workspace = self.agent.get_active_workspace()
        if workspace is None:
            known = self.agent.serena_config.project_names
            hint = f"  Known projects: {known}" if known else ""
            return (
                "No active workspace.  "
                "Call `activate_project` with the workspace root path." + hint
            )
        return workspace.health_summary()
