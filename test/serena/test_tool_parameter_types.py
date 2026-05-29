import logging

from serena.config.serena_config import SerenaConfig
from serena.mcp import SerenaMCPFactory


def test_all_tool_parameters_have_type():
    """
    For every tool exposed by Serena, ensure that the generated MCP schema
    contains a ``type`` entry for each parameter.
    """
    cfg = SerenaConfig(log_level=logging.ERROR)
    factory = SerenaMCPFactory(transport="stdio")
    factory.agent = factory._create_serena_agent(cfg)
    tools = list(factory._iter_tools())

    for tool in tools:
        mcp_tool = factory.make_mcp_tool(tool)
        params = mcp_tool.parameters

        issues = []
        if "properties" not in params:
            issues.append(f"Tool {tool.get_name()!r} missing properties section")
        else:
            for pname, prop in params["properties"].items():
                if "type" not in prop and "anyOf" not in prop and "oneOf" not in prop:
                    issues.append(f"Tool {tool.get_name()!r} parameter {pname!r} missing 'type'")
        if issues:
            raise AssertionError("\n".join(issues))
