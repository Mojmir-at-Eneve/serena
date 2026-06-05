"""
The Serena Model Context Protocol (MCP) Server
"""

import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import docstring_parser
from mcp.server.fastmcp import server
from mcp.server.fastmcp.server import FastMCP, Settings
from mcp.server.fastmcp.tools.base import Tool as MCPTool
from mcp.types import ToolAnnotations
from pydantic_settings import SettingsConfigDict
from sensai.util import logging

from serena.agent import SerenaAgent
from serena.config.serena_config import SerenaConfig
from serena.constants import SERENA_LOG_FORMAT
from serena.tools import Tool
from serena.util.exception import show_fatal_exception_safe
from serena.util.logging import MemoryLogHandler

log = logging.getLogger(__name__)


def configure_logging(*args, **kwargs) -> None:  # type: ignore
    if not logging.is_enabled():
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=SERENA_LOG_FORMAT)


server.configure_logging = configure_logging  # type: ignore


@dataclass
class SerenaMCPRequestContext:
    agent: SerenaAgent


class SerenaMCPFactory:
    def __init__(
        self,
        transport: Literal["stdio"] = "stdio",
        project: str | None = None,
        memory_log_handler: MemoryLogHandler | None = None,
    ):
        self.transport = transport
        self.project = project
        self.agent: SerenaAgent | None = None
        self.memory_log_handler = memory_log_handler

    @staticmethod
    def make_mcp_tool(tool: Tool) -> MCPTool:
        func_name = tool.get_name()
        func_doc = tool.get_apply_docstring() or ""
        func_arg_metadata = tool.get_apply_fn_metadata()
        parameters = func_arg_metadata.arg_model.model_json_schema()

        docstring = docstring_parser.parse(func_doc)
        func_doc = (docstring.description or "").strip().strip(".")
        if func_doc:
            func_doc += "."
        if docstring.returns and (docstring_returns_descr := docstring.returns.description):
            prefix = " " if func_doc else ""
            func_doc = f"{func_doc}{prefix}Returns {docstring_returns_descr.strip().strip('.')}."

        docstring_params = {param.arg_name: param for param in docstring.params}
        parameters_properties: dict[str, dict[str, Any]] = parameters["properties"]
        for parameter, properties in parameters_properties.items():
            if (param_doc := docstring_params.get(parameter)) and param_doc.description:
                param_desc = f"{param_doc.description.strip().strip('.')}."
                properties["description"] = param_desc[0].upper() + param_desc[1:]

        def execute_fn(**kwargs) -> str:  # type: ignore
            return tool.apply_ex(log_call=True, catch_exceptions=True, **kwargs)

        tool_title = " ".join(word.capitalize() for word in func_name.split("_"))
        can_edit = tool.can_edit()
        annotations = ToolAnnotations(
            title=tool_title,
            readOnlyHint=not can_edit,
            destructiveHint=can_edit,
        )

        return MCPTool(
            fn=execute_fn,
            name=func_name,
            description=func_doc,
            parameters=parameters,
            fn_metadata=func_arg_metadata,
            is_async=False,
            context_kwarg="mcp_ctx",
            annotations=annotations,
            title=tool_title,
        )

    def _create_serena_agent(self, serena_config: SerenaConfig) -> SerenaAgent:
        return SerenaAgent(
            project=self.project,
            serena_config=serena_config,
            memory_log_handler=self.memory_log_handler,
        )

    def _iter_tools(self) -> Iterator[Tool]:
        assert self.agent is not None
        yield from self.agent.get_exposed_tool_instances()

    def _set_mcp_tools(self, mcp: FastMCP) -> None:
        if mcp is not None:
            mcp._tool_manager._tools = {}
            for tool in self._iter_tools():
                mcp_tool = self.make_mcp_tool(tool)
                mcp._tool_manager._tools[tool.get_name()] = mcp_tool
            log.info(
                "Starting MCP server with %s tools: %s",
                len(mcp._tool_manager._tools),
                list(mcp._tool_manager._tools.keys()),
            )

    def create_mcp_server(
        self,
        log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
        trace_lsp_communication: bool | None = None,
        tool_timeout: float | None = None,
    ) -> FastMCP:
        try:
            config = SerenaConfig.from_config_file()
            if log_level is not None:
                log_level = cast(Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], log_level.upper())
                config.log_level = logging.getLevelNamesMapping()[log_level]
            if trace_lsp_communication is not None:
                config.trace_lsp_communication = trace_lsp_communication
            if tool_timeout is not None:
                config.tool_timeout = tool_timeout

            self.agent = self._create_serena_agent(config)
        except Exception as e:
            show_fatal_exception_safe(e)
            raise

        Settings.model_config = SettingsConfigDict(env_prefix="FASTMCP_")
        instructions = self._get_initial_instructions()
        log.info("MCP server initial instructions:\n%s", instructions)
        return FastMCP(
            name="Serena",
            lifespan=self.server_lifespan,
            website_url="https://oraios.github.io/serena",
            instructions=instructions,
        )

    @asynccontextmanager
    async def server_lifespan(self, mcp_server: FastMCP) -> AsyncIterator[None]:
        self._set_mcp_tools(mcp_server)
        log.info("MCP server lifetime setup complete")
        try:
            yield
        finally:
            log.info("MCP server shutting down")
            if self.agent is not None:
                self.agent.on_shutdown()

    def _get_initial_instructions(self) -> str:
        # start_here is now a lightweight passive tool that the agent must call
        # explicitly at the start of each session. The MCP instructions field
        # carries only the mandatory directive so that no activation logic runs
        # at server startup and the agent always performs the decision itself.
        return (
            "IMPORTANT: You are connected to the Serena MCP server — an LSP-backed "
            "toolbox for IDE-grade code intelligence. "
            "Always call start_here as the very first tool in every session before "
            "using any other Serena tool. "
            "start_here checks workspace state, scans the server working directory, "
            "and returns the full Serena guide plus tool catalog so you can decide "
            "how to activate the project."
        )
