"""
Serena agent: orchestrates MCP tools, projects, and the LSP language-server backend.
"""

import os
import platform
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from logging import Logger
from typing import TypeVar

from sensai.util import logging
from sensai.util.logging import LogTime

from serena import serena_version
from serena.config.serena_config import (
    NamedToolInclusionDefinition,
    RegisteredProject,
    SerenaConfig,
    SerenaPaths,
    ToolInclusionDefinition,
)
from serena.ls_manager import LanguageServerManager
from serena.project import Project
from serena.task_executor import TaskExecutor
from serena.tools import ActivateProjectTool, GetCurrentConfigTool, ReplaceContentTool, Tool, ToolMarker, ToolRegistry
from serena.util.inspection import iter_subclasses
from serena.util.logging import MemoryLogHandler
from solidlsp.ls_config import Language

log = logging.getLogger(__name__)
TTool = TypeVar("TTool", bound="Tool")
T = TypeVar("T")


class ProjectNotFoundError(Exception):
    pass


class AvailableTools:
    def __init__(self, tools: list[Tool]):
        self.tools = tools
        self.tool_names = sorted([tool.get_name_from_cls() for tool in tools])
        self._tool_name_set = set(self.tool_names)
        self.tool_marker_names = set()
        for marker_class in iter_subclasses(ToolMarker):
            for tool in tools:
                if isinstance(tool, marker_class):
                    self.tool_marker_names.add(marker_class.__name__)

    def __len__(self) -> int:
        return len(self.tools)

    def contains_tool_name(self, tool_name: str) -> bool:
        return tool_name in self._tool_name_set

    def contains_tool_class(self, tool_class: type[Tool]) -> bool:
        return self.contains_tool_name(tool_class.get_name_from_cls())


class ToolSet:
    LEGACY_TOOL_NAME_MAPPING = {"replace_regex": ReplaceContentTool.get_name_from_cls()}

    def __init__(self, tool_names: set[str]) -> None:
        self._tool_names = tool_names

    def __len__(self) -> int:
        return len(self._tool_names)

    @classmethod
    def default(cls) -> "ToolSet":
        from serena.tools import ToolRegistry

        return cls(set(ToolRegistry().get_tool_names_default_enabled()))

    def apply(self, *tool_inclusion_definitions: ToolInclusionDefinition) -> "ToolSet":
        from serena.tools import ToolRegistry

        def get_updated_tool_name(tool_name: str) -> str:
            if tool_name in self.LEGACY_TOOL_NAME_MAPPING:
                new_tool_name = self.LEGACY_TOOL_NAME_MAPPING[tool_name]
                log.warning("Tool name '%s' is deprecated, please use '%s' instead", tool_name, new_tool_name)
                return new_tool_name
            return tool_name

        registry = ToolRegistry()
        tool_names = set(self._tool_names)
        for definition in tool_inclusion_definitions:
            if definition.is_fixed_tool_set():
                tool_names = set()
                for fixed_tool in definition.fixed_tools:
                    fixed_tool = get_updated_tool_name(fixed_tool)
                    if registry.check_valid_tool_name(fixed_tool, " (in fixed tools set)"):
                        tool_names.add(fixed_tool)
            else:
                for included_tool in definition.included_optional_tools:
                    included_tool = get_updated_tool_name(included_tool)
                    if registry.check_valid_tool_name(included_tool, " (in included optional tools)") and included_tool not in tool_names:
                        tool_names.add(included_tool)
                for excluded_tool in definition.excluded_tools:
                    excluded_tool = get_updated_tool_name(excluded_tool)
                    registry.check_valid_tool_name(excluded_tool, " (in excluded tools)")
                    if excluded_tool in tool_names:
                        tool_names.remove(excluded_tool)
        return ToolSet(tool_names)

    def without_editing_tools(self) -> "ToolSet":
        from serena.tools import ToolRegistry

        registry = ToolRegistry()
        tool_names = set(self._tool_names)
        for tool_name in self._tool_names:
            if registry.get_tool_class_by_name(tool_name).can_edit():
                tool_names.remove(tool_name)
        return ToolSet(tool_names)

    def get_tool_names(self) -> set[str]:
        return self._tool_names

    def includes_name(self, tool_name: str) -> bool:
        return tool_name in self._tool_names

    def to_available_tools(self, all_tools: dict[type[Tool], Tool]) -> AvailableTools:
        return AvailableTools([t for t in all_tools.values() if self.includes_name(t.get_name())])


class SerenaAgent:
    def __init__(
        self,
        project: str | None = None,
        project_activation_callback: Callable[[], None] | None = None,
        serena_config: SerenaConfig | None = None,
        memory_log_handler: MemoryLogHandler | None = None,
    ):
        self._active_project: Project | None = None
        self._project_activation_callback = project_activation_callback
        self.version = serena_version()
        self.serena_config = serena_config or SerenaConfig.from_config_file()
        self.serena_config.propagate_settings()

        registered_project_to_activate: RegisteredProject | None = (
            self.serena_config.get_registered_project(project, autoregister=True) if project is not None else None
        )

        serena_log_level = self.serena_config.log_level
        if Logger.root.level != serena_log_level:
            log.info("Changing the root logger level to %s", serena_log_level)
            Logger.root.setLevel(serena_log_level)

        if memory_log_handler is None:
            memory_log_handler = MemoryLogHandler(level=serena_log_level)
            Logger.root.addHandler(memory_log_handler)

        self._all_tools: dict[type[Tool], Tool] = {tool_class: tool_class(self) for tool_class in ToolRegistry().get_all_tool_classes()}

        log.info(
            "Starting Serena server (version=%s, process id=%s); Python %s on %s",
            self.version,
            os.getpid(),
            platform.python_version(),
            platform.platform(),
        )
        log.info("Configuration file: %s", self.serena_config.config_file_path)
        log.info("Available projects: %s", ", ".join(self.serena_config.project_names))
        log.info("Loaded tools (%s): %s", len(self._all_tools), ", ".join(t.get_name_from_cls() for t in self._all_tools.values()))

        self._check_shell_settings()
        self._task_executor = TaskExecutor("SerenaAgentTaskExecutor")

        if project is not None:
            try:
                self.activate_project_from_path_or_name(project, update_active_tools=False)
            except Exception as e:
                log.error("Error activating project '%s' at startup: %s", project, e, exc_info=e)

        self._base_toolset = self._create_base_toolset(self.serena_config, self._active_project)
        self._exposed_tools = self._base_toolset.to_available_tools(self._all_tools)
        self._update_active_tools()
        log.info("Exposed tools (%s): %s", len(self._exposed_tools), self._exposed_tools.tool_names)

    @classmethod
    def _create_base_toolset(cls, serena_config: SerenaConfig, project: Project | None) -> ToolSet:
        tool_inclusion_definitions: list[ToolInclusionDefinition] = [serena_config]
        if project is not None:
            tool_inclusion_definitions.append(project.project_config)
            tool_inclusion_definitions.append(
                NamedToolInclusionDefinition(
                    name="SingleProjectExclusions",
                    excluded_tools=[ActivateProjectTool.get_name_from_cls(), GetCurrentConfigTool.get_name_from_cls()],
                )
            )
        return ToolSet.default().apply(*tool_inclusion_definitions)

    def _check_shell_settings(self) -> None:
        if platform.system() == "Windows":
            comspec = os.environ.get("COMSPEC", "")
            if "bash" in comspec:
                os.environ["COMSPEC"] = ""
                log.info("Adjusting COMSPEC to default shell instead of '%s'", comspec)

    def get_exposed_tool_instances(self) -> list[Tool]:
        return list(self._exposed_tools.tools)

    def get_active_project(self) -> Project | None:
        return self._active_project

    def get_active_project_or_raise(self) -> Project:
        project = self._active_project
        if project is None:
            raise ValueError("No active project. Please activate a project first.")
        return project

    def get_log_inspection_instructions(self) -> str:
        log_path = SerenaPaths().last_returned_log_file_path
        if log_path is not None:
            return f"Find the current log file here: {log_path}"
        return "Logs are written to the Serena log file configured for this session."

    def _update_active_tools(self) -> None:
        tool_set = self._base_toolset
        if self._active_project is not None:
            tool_set = tool_set.apply(self._active_project.project_config)
            if self._active_project.project_config.read_only:
                tool_set = tool_set.without_editing_tools()
        self._active_tools = tool_set.to_available_tools(self._all_tools)
        log.info("Active tools (%s): %s", len(self._active_tools), ", ".join(self._active_tools.tool_names))

    def get_project_activation_message(self) -> str:
        proj = self.get_active_project_or_raise()
        if proj.is_newly_created:
            msg = f"Created and activated project '{proj.project_name}' at {proj.project_root}."
        else:
            msg = f"Activated project '{proj.project_name}' at {proj.project_root}."
        languages_str = ", ".join(lang.value for lang in proj.project_config.languages)
        msg += f"\nProgramming languages: {languages_str}."
        msg += f"\nFile encoding: {proj.project_config.encoding}."
        if proj.project_config.initial_prompt:
            msg += f"\nProject-specific instructions:\n{proj.project_config.initial_prompt}"
        return msg

    def issue_task(self, task: Callable[[], T], name: str | None = None, logged: bool = True, timeout: float | None = None):
        return self._task_executor.issue_task(task, name=name, logged=logged, timeout=timeout)

    def execute_task(self, task: Callable[[], T], name: str | None = None, logged: bool = True, timeout: float | None = None) -> T:
        return self._task_executor.execute_task(task, name=name, logged=logged, timeout=timeout)

    def _activate_project(self, project: Project, update_active_tools: bool = True) -> bool:
        if self._active_project is not None and self._active_project.project_root == project.project_root:
            return False

        log.info("Activating %s at %s", project.project_name, project.project_root)
        if self._active_project is not None:
            self._active_project.shutdown()

        self._active_project = project
        project.set_agent(self)

        if update_active_tools:
            self._update_active_tools()

        def init_language_server_manager() -> None:
            with LogTime("Language server initialization", logger=log):
                self.reset_language_server_manager()

        self.issue_task(init_language_server_manager)

        if self._project_activation_callback is not None:
            self._project_activation_callback()
        return True

    def activate_project_from_path_or_name(self, project_root_or_name: str, update_active_tools: bool = True) -> bool:
        project_instance: Project | None = self.serena_config.get_project(project_root_or_name)
        if project_instance is not None:
            log.info("Found registered project '%s' at %s", project_instance.project_name, project_instance.project_root)
        elif os.path.isdir(project_root_or_name):
            project_instance = self.serena_config.add_project_from_path(project_root_or_name)
            log.info("Added new project %s for path %s", project_instance.project_name, project_instance.project_root)

        if project_instance is None:
            raise ProjectNotFoundError(
                f"Project '{project_root_or_name}' not found. Existing project names: {self.serena_config.project_names}"
            )
        return self._activate_project(project_instance, update_active_tools=update_active_tools)

    def get_active_tool_names(self) -> list[str]:
        return self._active_tools.tool_names

    def tool_is_active(self, tool_name: str) -> bool:
        return self._active_tools.contains_tool_name(tool_name)

    def get_current_config_overview(self) -> str:
        result_str = "Current configuration:\n"
        result_str += f"Serena version: {self.version}\n"
        result_str += f"Log level: {self.serena_config.log_level}, trace_lsp={self.serena_config.trace_lsp_communication}\n"
        if self._active_project is not None:
            result_str += f"Active project: {self._active_project.project_name}\n"
        else:
            result_str += "No active project\n"
        result_str += "Available projects:\n" + "\n".join(self.serena_config.project_names) + "\n"
        result_str += "Active tools:\n"
        for i in range(0, len(self._active_tools.tool_names), 4):
            result_str += "  " + ", ".join(self._active_tools.tool_names[i : i + 4]) + "\n"
        return result_str

    def reset_language_server_manager(self) -> None:
        self.get_active_project_or_raise().create_language_server_manager()

    def add_language(self, language: Language) -> None:
        self.execute_task(lambda: self.get_active_project_or_raise().add_language(language), name=f"AddLanguage:{language.value}")

    def remove_language(self, language: Language) -> None:
        self.issue_task(lambda: self.get_active_project_or_raise().remove_language(language), name=f"RemoveLanguage:{language.value}")

    def get_tool(self, tool_class: type[TTool]) -> TTool:
        return self._all_tools[tool_class]  # type: ignore

    def get_tool_by_name(self, tool_name: str) -> Tool:
        return self.get_tool(ToolRegistry().get_tool_class_by_name(tool_name))

    def get_language_server_manager(self) -> LanguageServerManager | None:
        if self._active_project is not None:
            return self._active_project.language_server_manager
        return None

    def get_language_server_manager_or_raise(self) -> LanguageServerManager:
        return self.get_active_project_or_raise().get_language_server_manager_or_raise()

    def get_active_lsp_languages(self) -> list[Language]:
        ls_manager = self.get_language_server_manager()
        if ls_manager is None:
            return []
        return ls_manager.get_active_languages()

    def get_current_tasks(self):
        return self._task_executor.get_current_tasks()

    def get_last_executed_task(self):
        return self._task_executor.get_last_executed_task()

    def print_tool_overview(self) -> None:
        ToolRegistry().print_tool_overview(self._active_tools.tools)

    def on_shutdown(self, timeout: float = 2.0) -> None:
        log.info("SerenaAgent shutting down …")
        if self._active_project is not None:
            self._active_project.shutdown(timeout=timeout)
            self._active_project = None

    def shutdown(self) -> None:
        self.on_shutdown()
        os.kill(os.getpid(), signal.SIGTERM)

    def __del__(self) -> None:
        self.on_shutdown()

    @contextmanager
    def active_project_context(self, project: Project) -> Iterator[None]:
        original_project = self._active_project
        self._active_project = project
        try:
            yield
        finally:
            self._active_project = original_project
