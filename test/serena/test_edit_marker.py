from serena.tools import Tool
from serena.tools.symbol_tools import FindSymbolTool, RewriteSymbolTool


class TestEditMarker:
    def test_tool_can_edit_method(self):
        """Test that Tool.can_edit() works for read-only and editing tools."""
        # FindSymbolTool is read-only
        assert issubclass(FindSymbolTool, Tool)
        assert not FindSymbolTool.can_edit()

        # RewriteSymbolTool performs edits
        assert issubclass(RewriteSymbolTool, Tool)
        assert RewriteSymbolTool.can_edit()
