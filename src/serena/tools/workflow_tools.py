"""
Lightweight workflow tools for the ground-truth MCP toolbox.
"""

from serena.tools import Tool, ToolMarkerDoesNotRequireActiveProject, ToolRegistry

# Decision: agent guidance lives in one static string (no Jinja/context/mode prompts).
_INITIAL_INSTRUCTIONS = """\
Serena is an LSP-backed MCP toolbox for coding agents. It exposes semantic code intelligence
(find symbols, references, declarations, implementations, diagnostics) and editing tools that
operate on real language-server facts rather than fragile text guessing.

## Before you start

1. **Project activation.** MCP is often configured without a fixed project path in `mcp.json`.
   The server may auto-detect a project from its working directory at startup. If tools report
   "No active project", call `activate_project` with the **IDE workspace root path** (absolute path
   from the host environment). Use `activate_project` with ``.`` or an empty path only when the
   server cwd is the workspace root. This creates `.serena/project.yml` if missing; you do not need
   a separate `serena project create` step first.
2. Prefer symbolic tools over raw file grep/read when exploring code structure.
3. Line numbers returned by Serena tools are **0-based**.

## Symbol conventions

Symbols are addressed by `name_path` and `relative_path` (see `find_symbol`).

Examples (Python):
- Class overview: `find_symbol` with `name_path_pattern="Foo"`, `include_body=False`, `depth=1`
- Read a method body: `find_symbol` with `name_path_pattern="Foo/__init__"`, `include_body=True`
- Cross-file impact: `find_referencing_symbols`

## Tool catalog

{tool_catalog}

## Editing guidance

- Prefer `replace_symbol_body`, `insert_after_symbol`, `insert_before_symbol` for whole-symbol edits.
- Use `replace_content` for small intra-symbol line edits.
- After edits, use `get_diagnostics_for_file` to verify the language server reports no new issues.
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
    Returns concise usage instructions for Serena's MCP tools (for clients that do not load MCP server instructions).
    """

    def apply(self) -> str:
        """
        Returns the Serena toolbox manual: project activation, symbol conventions, and a catalog of available tools.
        Call this once at the start of a session if your client did not already provide MCP server instructions.
        """
        catalog = _build_tool_catalog()
        return _INITIAL_INSTRUCTIONS.format(tool_catalog=catalog)
