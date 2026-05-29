from sensai.util.helper import mark_used

from serena.cli import resolve_project_for_activation
from serena.tools import Tool, ToolMarkerDoesNotRequireActiveProject, ToolMarkerOptional

# OpenDashboardTool removed: web dashboard is not part of the internal MCP deployment.


class ActivateProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Activates a project based on the project name or path.
    """

    # noinspection PyIncorrectDocstring
    # (session_id is injected via apply_ex)
    def apply(self, project: str = "", session_id: str = "") -> str:
        """
        Activates the project with the given name or path.

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
        result = self.agent.get_project_activation_message()
        result += "\nCall `initial_instructions` if you have not yet read the Serena toolbox manual."
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
