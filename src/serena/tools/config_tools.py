"""
Project management tools for the Serena MCP toolbox.

ManageProjectTool consolidates project activation and removal into a single
intent-driven tool, replacing the former ActivateProjectTool and RemoveProjectTool.

InitializeSubprojectsTool scans a parent directory and creates a Serena
project configuration for each immediate child that does not yet have one,
enabling one-shot monorepo initialisation instead of per-project invocations.
"""

from typing import Literal

from serena.tools.tools_base import Tool, ToolMarkerDoesNotRequireActiveProject


class ManageProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Activates or removes a Serena project.

    Activation discovers multi-project workspaces automatically:
    - Activating a parent directory containing multiple Serena projects registers
      all nested projects as a single multi-project workspace.
    - Activating a child project directory sets only that project as the active
      workspace, replacing any previously active workspace.
    """

    # noinspection PyIncorrectDocstring
    # (session_id is injected via apply_ex)
    def apply(
        self,
        action: Literal["activate", "remove"] = "activate",
        project: str = "",
        session_id: str = "",
    ) -> str:
        """
        Manage a Serena project: activate it for use or remove it from configuration.

        For 'activate': auto-detects the project from the server working directory when
        project is empty. Activating a parent directory discovers all nested projects as
        one workspace; activating a child path replaces any active workspace with that
        project alone.

        For 'remove': permanently removes the named project from Serena's configuration.
        The project must be referenced by its registered name.

        :param action: 'activate' to load a project for use, 'remove' to delete it from config.
        :param project: for 'activate' — registered project name, absolute path to the project
            directory, or empty to auto-detect from the server working directory. For 'remove' —
            the registered project name to delete.
        :return: confirmation with workspace health summary for activate, or success message for remove.
        """
        from sensai.util.helper import mark_used

        from serena.cli import resolve_project_for_activation

        if action == "remove":
            if not project:
                return "Error: 'project' must be provided when action is 'remove'."
            self.agent.serena_config.remove_project(project)
            return f"Removed project '{project}' from Serena configuration."

        # action == "activate"
        try:
            resolved_project = resolve_project_for_activation(project or None)
        except ValueError as e:
            return f"Error: {e}"

        is_new_activation = self.agent.activate_project_from_path_or_name(resolved_project)
        mark_used(is_new_activation)
        result = self.agent.get_project_activation_message()
        replacement_warning = self.agent.consume_last_workspace_replacement_warning()
        if replacement_warning is not None:
            result = f"{replacement_warning}\n\n{result}"
        result += "\nCall start_here if you have not yet initialized your session."
        return result


class InitializeSubprojectsTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Create Serena project configurations for all immediate child directories under a parent path.

    Scans one level deep. Directories that already have a .serena/project.yml are skipped.
    Use this to initialise an entire monorepo at once instead of calling manage_project
    once per child directory.
    """

    def apply(self, parent_path: str, language: str = "") -> str:
        """
        Scan immediate sub-directories of *parent_path* and create a Serena project
        config for each one that does not already have one.

        :param parent_path: absolute path to the parent directory whose children should be initialised.
        :param language: optional language to apply to every sub-project (e.g. 'csharp', 'python').
            When empty, the language is auto-detected for each child. Can specify multiple
            languages separated by commas (e.g. 'python,typescript').
        :return: per-project summary (created / skipped / error) plus a total count line.
        """
        from serena.cli import _create_all_subprojects

        # Parse comma-separated language string into a tuple for _create_all_subprojects.
        languages: tuple[str, ...] = tuple(lang.strip() for lang in language.split(",") if lang.strip())
        lines = _create_all_subprojects(parent_path, languages)
        return "\n".join(lines)
