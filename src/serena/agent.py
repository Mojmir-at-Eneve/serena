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
from serena.config.serena_config import RegisteredProject, SerenaConfig, SerenaPaths, ToolInclusionDefinition
from serena.ls_manager import LanguageServerManager
from serena.project import Project
from serena.task_executor import TaskExecutor
from serena.tools import Tool, ToolMarker, ToolRegistry
from serena.util.inspection import iter_subclasses
from serena.util.logging import MemoryLogHandler
from serena.workspace import ProjectUnit, SerenaWorkspace
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
    # Legacy name mapping for tools that were renamed. Kept so that project.yml files
    # written with old tool names can still be loaded without errors.
    LEGACY_TOOL_NAME_MAPPING: dict[str, str] = {
        "replace_regex": "search_and_replace_regex",
        "replace_content": "search_and_replace",
        "execute_shell_command": "run_command",
        "initial_instructions": "start_here",
        "get_workspace_status": "start_here",
        "get_current_config": "start_here",
        "activate_project": "manage_project",
        "remove_project": "manage_project",
        "get_symbols_overview": "symbols_overview",
        "find_referencing_symbols": "find_usages",
        "find_declaration": "find_definition",
        "get_diagnostics_for_file": "check_errors",
        "get_diagnostics_for_symbol": "check_symbol_errors",
        "replace_symbol_body": "rewrite_symbol",
        "insert_after_symbol": "inject_code",
        "insert_before_symbol": "inject_code",
        "safe_delete_symbol": "delete_symbol",
    }

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
        self._active_workspace: SerenaWorkspace | None = None
        self._last_workspace_replacement_warning: str | None = None
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

        self._base_toolset = self._create_base_toolset(
            self.serena_config, self._active_workspace
        )
        self._exposed_tools = self._base_toolset.to_available_tools(self._all_tools)
        self._update_active_tools()
        log.info("Exposed tools (%s): %s", len(self._exposed_tools), self._exposed_tools.tool_names)

    @classmethod
    def _create_base_toolset(
        cls,
        serena_config: SerenaConfig,
        workspace: SerenaWorkspace | None,
    ) -> ToolSet:
        tool_inclusion_definitions: list[ToolInclusionDefinition] = [serena_config]
        if workspace is not None:
            # Apply the primary (root) project config for workspace-level tool inclusion.
            tool_inclusion_definitions.append(
                workspace.primary_unit.project.project_config
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

    # ------------------------------------------------------------------
    # Workspace access
    # ------------------------------------------------------------------

    def get_active_workspace(self) -> SerenaWorkspace | None:
        return self._active_workspace

    def get_active_workspace_or_raise(self) -> SerenaWorkspace:
        if self._active_workspace is None:
            raise ValueError(
                "No active workspace. Call `activate_project` with the workspace root path."
            )
        return self._active_workspace

    # ------------------------------------------------------------------
    # Project access — backward-compatible shims that return the primary unit
    # ------------------------------------------------------------------

    def get_active_project(self) -> Project | None:
        """Return the primary (root) project of the active workspace, or None."""
        if self._active_workspace is None:
            return None
        return self._active_workspace.primary_unit.project

    def get_active_project_or_raise(self) -> Project:
        """Return the primary (root) project, or raise if no workspace is active."""
        return self.get_active_workspace_or_raise().primary_unit.project

    def get_project_for_path(self, workspace_relative_path: str) -> tuple[ProjectUnit, str]:
        """
        Resolve a workspace-relative path to its owning ProjectUnit and the
        corresponding project-relative path.

        :param workspace_relative_path: path relative to workspace root
        :return: (ProjectUnit, project_relative_path)
        """
        return self.get_active_workspace_or_raise().resolve_unit_for_path(
            workspace_relative_path
        )

    def get_log_inspection_instructions(self) -> str:
        log_path = SerenaPaths().last_returned_log_file_path
        if log_path is not None:
            return f"Find the current log file here: {log_path}"
        return "Logs are written to the Serena log file configured for this session."

    def _update_active_tools(self) -> None:
        tool_set = self._base_toolset
        primary = self.get_active_project()
        if primary is not None:
            tool_set = tool_set.apply(primary.project_config)
            if primary.project_config.read_only:
                tool_set = tool_set.without_editing_tools()
        self._active_tools = tool_set.to_available_tools(self._all_tools)
        log.info("Active tools (%s): %s", len(self._active_tools), ", ".join(self._active_tools.tool_names))

    def get_project_activation_message(self) -> str:
        """Return the workspace health summary shown to agents after activation."""
        ws = self.get_active_workspace_or_raise()
        return ws.health_summary()

    def consume_last_workspace_replacement_warning(self) -> str | None:
        """Return and clear the warning set when the active workspace was replaced."""
        warning = self._last_workspace_replacement_warning
        self._last_workspace_replacement_warning = None
        return warning

    def issue_task(self, task: Callable[[], T], name: str | None = None, logged: bool = True, timeout: float | None = None):
        return self._task_executor.issue_task(task, name=name, logged=logged, timeout=timeout)

    def execute_task(self, task: Callable[[], T], name: str | None = None, logged: bool = True, timeout: float | None = None) -> T:
        return self._task_executor.execute_task(task, name=name, logged=logged, timeout=timeout)

    def _activate_workspace(
        self, workspace: SerenaWorkspace, update_active_tools: bool = True
    ) -> bool:
        """Swap in *workspace* as the active workspace, shutting down the previous one."""
        if (
            self._active_workspace is not None
            and self._active_workspace.workspace_root == workspace.workspace_root
        ):
            self._last_workspace_replacement_warning = None
            return False

        # Decision: warn when agents activate a child project after a parent/multi-project
        # workspace, so they know prior context was dropped (not a silent no-op).
        self._last_workspace_replacement_warning = None
        if self._active_workspace is not None:
            self._last_workspace_replacement_warning = (
                "NOTE: The previously active workspace has been replaced. "
                "If you intended to work across multiple projects simultaneously, "
                "activate the shared parent directory instead of individual project subdirectories."
            )

        log.info("Activating workspace at %s", workspace.workspace_root)
        if self._active_workspace is not None:
            self._active_workspace.shutdown()

        self._active_workspace = workspace

        # Wire each project unit back to this agent.
        for unit in workspace.units:
            unit.project.set_agent(self)

        if update_active_tools:
            self._update_active_tools()

        # Initialise language server managers for all project units in parallel.
        def init_all_ls() -> None:
            with LogTime("Workspace language-server initialisation", logger=log):
                for unit in workspace.units:
                    try:
                        unit.project.create_language_server_manager()
                    except Exception as exc:
                        log.error(
                            "Language server initialisation failed for unit %s: %s",
                            unit.project_id,
                            exc,
                            exc_info=exc,
                        )

        self.issue_task(init_all_ls)

        if self._project_activation_callback is not None:
            self._project_activation_callback()
        return True

    # Keep the old name for backward compatibility (MCP tool calls this).
    def activate_project_from_path_or_name(
        self, project_root_or_name: str, language: str = "", update_active_tools: bool = True
    ) -> bool:
        """
        Activate a workspace rooted at *project_root_or_name*.

        Accepts an absolute path to a directory, a registered project name, or
        an empty string / "." for the server cwd.  Recursively discovers
        sub-projects and wires them up as a SerenaWorkspace.

        :param language: comma-separated language(s) to use when auto-generating a
            .serena/project.yml for a directory that does not yet have one (e.g.
            'csharp', 'python,typescript'). When empty, auto-detection is used as
            a fallback — but providing an explicit value is strongly preferred to
            avoid the expensive tree-walk language scan on large repositories.
        """
        # Resolve the root directory.
        root = self._resolve_workspace_root(project_root_or_name)

        # Parse the language string into a typed list so discover_and_create can
        # pass it straight through to ProjectConfig.autogenerate, skipping auto-detect.
        languages: list[Language] | None = None
        if language:
            languages = [
                Language(lang.strip())
                for lang in language.split(",")
                if lang.strip()
            ]

        # Discover workspace (finds nested .serena/project.yml).
        workspace = SerenaWorkspace.discover_and_create(
            root, self.serena_config, languages=languages
        )

        # Register the primary project in global config for backward compat
        # (so it shows up in project_names etc.). Ignore if already registered.
        primary = workspace.primary_unit.project
        if self.serena_config.get_registered_project(primary.project_root) is None:
            from serena.config.serena_config import RegisteredProject as _RP
            self.serena_config.add_registered_project(
                _RP.from_project_instance(primary)
            )

        return self._activate_workspace(workspace, update_active_tools=update_active_tools)

    def _resolve_workspace_root(self, project_root_or_name: str) -> str:
        """
        Resolve a project name, absolute path, or empty string to an absolute
        directory path that can serve as a workspace root.
        """
        # Empty / "." → server cwd.
        if not project_root_or_name or project_root_or_name.strip() in (".", ""):
            return os.getcwd()

        # Registered project name?
        registered: Project | None = self.serena_config.get_project(project_root_or_name)
        if registered is not None:
            return registered.project_root

        # Absolute or relative directory path.
        candidate = os.path.abspath(project_root_or_name)
        if os.path.isdir(candidate):
            return candidate

        raise ProjectNotFoundError(
            f"Project '{project_root_or_name}' not found. "
            f"Known projects: {self.serena_config.project_names}"
        )

    def get_active_tool_names(self) -> list[str]:
        return self._active_tools.tool_names

    def tool_is_active(self, tool_name: str) -> bool:
        return self._active_tools.contains_tool_name(tool_name)

    def get_current_config_overview(self) -> str:
        result_str = "Current configuration:\n"
        result_str += f"Serena version: {self.version}\n"
        result_str += f"Log level: {self.serena_config.log_level}, trace_lsp={self.serena_config.trace_lsp_communication}\n"
        if self._active_workspace is not None:
            result_str += self._active_workspace.health_summary() + "\n"
        else:
            result_str += "No active workspace\n"
        # Show name + path so agents can distinguish same-named projects registered
        # at different locations (e.g. taskrabbit/ vs projects/ copies of Ecedo.ERP).
        project_lines = [
            f"  {p.project_config.project_name}  ({p.project_root})"
            for p in self.serena_config.projects
        ]
        result_str += "Available projects:\n" + "\n".join(project_lines) + "\n"
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
        project = self.get_active_project()
        if project is not None:
            return project.language_server_manager
        return None

    def get_language_server_manager_or_raise(self) -> LanguageServerManager:
        return self.get_active_project_or_raise().get_language_server_manager_or_raise()

    def is_using_language_server(self) -> bool:
        """Serena is LSP-only; always True when a project is active."""
        return True

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
        if self._active_workspace is not None:
            self._active_workspace.shutdown(timeout=timeout)
            self._active_workspace = None

    def shutdown(self) -> None:
        self.on_shutdown()
        os.kill(os.getpid(), signal.SIGTERM)

    def __del__(self) -> None:
        self.on_shutdown()

    @contextmanager
    def active_project_context(self, project: Project) -> Iterator[None]:
        """
        Temporarily override the active project for the duration of a with-block.

        Used mainly in tests.  In a workspace context this swaps the entire
        workspace to a single-unit workspace wrapping *project*.
        """
        from serena.workspace import ProjectUnit, SerenaWorkspace

        original_workspace = self._active_workspace
        unit = ProjectUnit(project=project, workspace_relative_path="")
        temp_workspace = SerenaWorkspace(project.project_root, [unit])
        project.set_agent(self)
        self._active_workspace = temp_workspace
        try:
            yield
        finally:
            self._active_workspace = original_workspace
