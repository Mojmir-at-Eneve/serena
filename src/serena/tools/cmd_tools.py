"""
Command execution tools for the Serena MCP toolbox.

Three tools:
  - run_command           raw shell execution at the project root
  - run_project_command   execute a saved named command (test/lint/build/...)
  - manage_project_commands  CRUD for named project commands stored in project.yml
"""

import json
import os
from dataclasses import asdict
from typing import Literal

from serena.config.serena_config import ProjectCommand
from serena.tools.tools_base import Tool, ToolMarkerCanEdit, ToolResult
from serena.util.shell import execute_shell_command


class CommandResult(ToolResult):
    """
    Structured result for a shell command execution.

    Renders as JSON for MCP (same schema as the old ExecuteShellCommandTool) and
    as a readable block for the CLI.
    """

    def __init__(self, command: str, stdout: str, stderr: str | None, return_code: int, cwd: str, label: str | None = None):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.cwd = cwd
        self.label = label  # optional display name (for named commands)

    def to_mcp_string(self) -> str:
        data = {
            "command": self.command,
            "return_code": self.return_code,
            "cwd": self.cwd,
            "stdout": self.stdout,
        }
        if self.stderr is not None:
            data["stderr"] = self.stderr
        return json.dumps(data, ensure_ascii=False)

    def to_cli_text(self) -> str:
        lines: list[str] = []
        header = self.label if self.label else self.command
        lines.append(f"Command: {header}")
        lines.append(f"Exit code: {self.return_code}")
        lines.append(f"Working dir: {self.cwd}")
        if self.stdout.strip():
            lines.append("\nOutput:")
            lines.append(self.stdout.rstrip())
        if self.stderr and self.stderr.strip():
            lines.append("\nStderr:")
            lines.append(self.stderr.rstrip())
        return "\n".join(lines)


class RunCommandTool(Tool, ToolMarkerCanEdit):
    """
    Runs a shell command in the project's root directory (or a specified sub-directory)
    and returns the output.

    Use this for any arbitrary shell operation: building, running scripts, git commands,
    package manager invocations, etc. For frequently repeated commands consider saving
    them with manage_project_commands so they are available by name via run_project_command.

    Do NOT use this tool to start long-running processes (servers, watchers) or
    processes that require interactive terminal input.
    """

    def apply(
        self,
        command: str,
        cwd: str | None = None,
        capture_stderr: bool = True,
        max_answer_chars: int = -1,
    ) -> CommandResult:
        """
        Execute a shell command and return its output.

        :param command: the shell command to run.
        :param cwd: working directory for the command. If None, the project root is used.
            Relative paths are resolved against the project root.
        :param capture_stderr: include stderr in the result. Default True.
        :param max_answer_chars: cap the result length; -1 uses the configured default.
        :return: command output including stdout, return code, and optionally stderr.
        """
        if cwd is None:
            _cwd = self.get_project_root()
        elif os.path.isabs(cwd):
            _cwd = cwd
        else:
            _cwd = os.path.join(self.get_project_root(), cwd)
            if not os.path.isdir(_cwd):
                raise FileNotFoundError(
                    f"Relative working directory '{cwd}' does not exist as a directory: {_cwd}"
                )

        raw = execute_shell_command(command, cwd=_cwd, capture_stderr=capture_stderr)
        result = CommandResult(
            command=command,
            stdout=raw.stdout,
            stderr=raw.stderr,
            return_code=raw.return_code,
            cwd=_cwd,
        )
        # Honour max_answer_chars by truncating the MCP string if needed.
        mcp_str = result.to_mcp_string()
        limited = self._limit_length(mcp_str, max_answer_chars)
        if limited is not mcp_str and len(limited) < len(mcp_str):
            # Return a plain string when truncation occurred so agents see the warning.
            return limited  # type: ignore[return-value]
        return result


class RunProjectCommandTool(Tool, ToolMarkerCanEdit):
    """
    Runs a named project command that was previously saved with manage_project_commands.

    Named commands (e.g. 'test', 'lint', 'build') are stored in the project's
    .serena/project.yml so agents don't have to remember the exact shell invocation
    across sessions. Use manage_project_commands to view, add, or update them.
    """

    def apply(
        self,
        name: str,
        capture_stderr: bool = True,
        max_answer_chars: int = -1,
    ) -> CommandResult:
        """
        Run a named project command by its registered name.

        :param name: name of the project command to run (e.g. 'test', 'lint', 'build').
        :param capture_stderr: include stderr in the result. Default True.
        :param max_answer_chars: cap the result length; -1 uses the configured default.
        :return: command output including stdout, return code, and optionally stderr.
        """
        project_config = self.project.project_config
        if name not in project_config.commands:
            available = sorted(project_config.commands.keys())
            hint = f"  Available commands: {available}" if available else "  No commands defined yet."
            raise ValueError(f"No project command named '{name}'.{hint}")

        cmd = project_config.commands[name]
        cwd = self.get_project_root()
        raw = execute_shell_command(cmd.command, cwd=cwd, capture_stderr=capture_stderr)
        label = f"{name}: {cmd.command}" if cmd.description else f"{name}: {cmd.command}"
        result = CommandResult(
            command=cmd.command,
            stdout=raw.stdout,
            stderr=raw.stderr,
            return_code=raw.return_code,
            cwd=cwd,
            label=label,
        )
        mcp_str = result.to_mcp_string()
        limited = self._limit_length(mcp_str, max_answer_chars)
        if limited is not mcp_str and len(limited) < len(mcp_str):
            return limited  # type: ignore[return-value]
        return result


class ManageProjectCommandsTool(Tool):
    """
    Create, update, remove, or list named project commands stored in project.yml.

    Named commands (e.g. 'test', 'lint', 'build') let agents remember the exact
    shell invocations for a project so they can call them by name via
    run_project_command. Commands persist across sessions in .serena/project.yml.
    """

    def apply(
        self,
        action: Literal["add", "update", "remove", "list"],
        name: str = "",
        command: str = "",
        description: str = "",
        examples: list[str] | None = None,
    ) -> str:
        """
        Manage the named project commands registered for this project.

        Actions:
        - list: show all registered commands with their shell commands and descriptions.
        - add: create a new named command; fails if the name already exists.
        - update: update an existing command (name must already exist).
        - remove: delete a named command by its name.

        :param action: one of 'add', 'update', 'remove', 'list'.
        :param name: command name (e.g. 'test', 'lint'). Required for add/update/remove.
        :param command: the shell command to execute. Required for add/update.
        :param description: human-readable description of what the command does.
        :param examples: list of example invocations to document usage.
        :return: confirmation message or JSON list of commands.
        """
        project = self.project
        config = project.project_config

        if action == "list":
            if not config.commands:
                return "No project commands defined. Use action='add' to create one."
            rows = []
            for cmd_name, cmd in sorted(config.commands.items()):
                row = {"name": cmd_name, "command": cmd.command, "description": cmd.description}
                if cmd.examples:
                    row["examples"] = cmd.examples  # type: ignore[assignment]
                rows.append(row)
            return json.dumps(rows, ensure_ascii=False, indent=2)

        if not name:
            return "Error: 'name' is required for add, update, and remove actions."

        if action == "remove":
            if name not in config.commands:
                return f"Error: No command named '{name}' found."
            del config.commands[name]
            project.save_config()
            return f"Removed command '{name}'."

        # add / update
        if not command:
            return "Error: 'command' is required for add and update actions."

        if action == "add" and name in config.commands:
            return (
                f"Error: Command '{name}' already exists. Use action='update' to change it."
            )
        if action == "update" and name not in config.commands:
            return (
                f"Error: No command named '{name}'. Use action='add' to create it."
            )

        config.commands[name] = ProjectCommand(
            command=command,
            description=description,
            examples=examples or [],
        )
        project.save_config()
        verb = "Added" if action == "add" else "Updated"
        return f"{verb} command '{name}': {command}"
