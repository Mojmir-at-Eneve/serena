from sensai.util.helper import mark_used

from serena.cli import resolve_project_for_activation
from serena.tools import Tool, ToolMarkerDoesNotRequireActiveProject, ToolMarkerOptional

# OpenDashboardTool removed: web dashboard is not part of the internal MCP deployment.


class ActivateProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Activates a project based on the project name or path.

    Multi-project activation:
    - Activating a **parent directory** that contains multiple Serena projects automatically
      discovers and registers all nested projects as a multi-project workspace.
    - Activating a **child directory** directly sets only that project as the active workspace,
      replacing any previously active workspace.
    - Example: activating ``C:\\repos`` discovers ``C:\\repos\\ProjectA`` and
      ``C:\\repos\\ProjectB`` together; activating ``C:\\repos\\ProjectA`` alone replaces
      the workspace.
    """

    # noinspection PyIncorrectDocstring
    # (session_id is injected via apply_ex)
    def apply(self, project: str = "", session_id: str = "") -> str:
        """
        Activates the project with the given name or path.

        Activating a parent directory discovers all nested Serena projects as one workspace;
        activating a child project path replaces any previously active workspace with that
        project alone. See the class docstring for examples.

        :param project: registered project name, absolute path to the project directory, or empty/``.``
            to auto-detect from the server working directory. When MCP is configured without a fixed
            project path, pass the IDE workspace root path from the host environment.
        """
        try:
            resolved_project = resolve_project_for_activation(project)
        except ValueError as e:
            return f"Error: {e}"
        is_new_activation = self.agent.activate_project_from_path_or_name(resolved_project)
        mark_used(is_new_activation)
        # get_project_activation_message() now returns the full workspace health summary.
        result = self.agent.get_project_activation_message()
        replacement_warning = self.agent.consume_last_workspace_replacement_warning()
        if replacement_warning is not None:
            result = f"{replacement_warning}\n\n{result}"
        result += (
            "\nCall `initial_instructions` if you have not yet read the Serena toolbox manual."
        )
        return result


class RemoveProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject, ToolMarkerOptional):
    """
    Removes a project from the Serena configuration.
    """

    def apply(self, project_name: str) -> str:
        """
        Removes a project from the Serena configuration.

        :param project_name: Name of the project to remove
        """
        self.agent.serena_config.remove_project(project_name)
        return f"Successfully removed project '{project_name}' from configuration."


class GetCurrentConfigTool(Tool):
    """
    Prints the current configuration of the agent, including the active and available projects and tools.
    """

    def apply(self) -> str:
        """
        Print the current configuration of the agent, including the active and available projects and tools.
        """
        return self.agent.get_current_config_overview()
