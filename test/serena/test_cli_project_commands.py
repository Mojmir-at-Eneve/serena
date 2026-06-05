"""Tests for CLI project commands (create, index)."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from serena.cli import (
    ProjectCommands,
    TopLevelCommands,
    find_project_root,
    resolve_project_for_activation,
    resolve_project_root_for_startup,
)
from serena.config.serena_config import ProjectConfig

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture
def temp_project_dir():
    """Create a temporary directory for testing."""
    tmpdir = tempfile.mkdtemp()
    try:
        yield tmpdir
    finally:
        # if windows, wait a bit to avoid PermissionError on cleanup
        if os.name == "nt":
            time.sleep(0.2)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_project_dir_with_python_file():
    """Create a temporary directory with a Python file for testing."""
    tmpdir = tempfile.mkdtemp()
    try:
        # Create a simple Python file so language detection works
        py_file = os.path.join(tmpdir, "test.py")
        with open(py_file, "w") as f:
            f.write("def hello():\n    pass\n")
        yield tmpdir
    finally:
        # if windows, wait a bit to avoid PermissionError on cleanup
        if os.name == "nt":
            time.sleep(0.2)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def cli_runner():
    """Create a CliRunner for testing Click commands."""
    return CliRunner()


class TestProjectCreate:
    """Tests for 'project create' command."""

    def test_create_basic_with_language(self, cli_runner, temp_project_dir):
        """Test basic project creation with explicit language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path), f"project.yml not found at {yml_path}"

    def test_create_auto_detect_language(self, cli_runner, temp_project_dir_with_python_file):
        """Test project creation with auto-detected language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir_with_python_file])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_create_with_name(self, cli_runner, temp_project_dir):
        """Test project creation with custom name and explicit language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--name", "my-custom-project", "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_create_with_language(self, cli_runner, temp_project_dir):
        """Test project creation with specified language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

    def test_create_with_multiple_languages(self, cli_runner, temp_project_dir):
        """Test project creation with multiple languages."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir, "--language", "python", "--language", "typescript"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output

    def test_create_with_invalid_language(self, cli_runner, temp_project_dir):
        """Test project creation with invalid language raises error."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir, "--language", "invalid-lang"],
        )
        assert result.exit_code != 0, "Should fail with invalid language"
        assert "Unknown language" in result.output or "invalid-lang" in result.output

    def test_create_already_exists(self, cli_runner, temp_project_dir):
        """Test that creating a project twice fails gracefully."""
        # Create once with explicit language
        result1 = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result1.exit_code == 0

        # Try to create again - should fail gracefully
        result2 = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result2.exit_code != 0, "Should fail when project.yml already exists"
        assert "already exists" in result2.output.lower()
        assert "Error:" in result2.output  # Should be user-friendly error

    def test_create_with_index_flag(self, cli_runner, temp_project_dir_with_python_file):
        """Test project creation with --index flag performs indexing."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir_with_python_file, "--language", "python", "--index", "--log-level", "ERROR", "--timeout", "5"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "Indexing project" in result.output

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path)

        # Verify cache directory was created (proof of indexing)
        cache_dir = os.path.join(temp_project_dir_with_python_file, ".serena", "cache")
        assert os.path.exists(cache_dir), "Cache directory should exist after indexing"

    def test_create_without_index_flag(self, cli_runner, temp_project_dir):
        """Test that project creation without --index does NOT perform indexing."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0
        assert "Generated project" in result.output
        assert "Indexing" not in result.output

        # Verify cache directory was NOT created
        cache_dir = os.path.join(temp_project_dir, ".serena", "cache")
        assert not os.path.exists(cache_dir), "Cache directory should not exist without --index"


class TestProjectIndex:
    """Tests for 'project index' command."""

    def test_index_auto_creates_project_with_files(self, cli_runner, temp_project_dir_with_python_file):
        """Test that index command auto-creates project.yml if it doesn't exist (with source files)."""
        result = cli_runner.invoke(ProjectCommands.index, [temp_project_dir_with_python_file, "--log-level", "ERROR", "--timeout", "5"])
        # Should succeed and perform indexing
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Auto-creating" in result.output or "Indexing" in result.output

        # Verify project.yml was auto-created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path), "project.yml should be auto-created"

    def test_index_with_explicit_language(self, cli_runner, temp_project_dir):
        """Test index with explicit --language for empty directory."""
        result = cli_runner.invoke(
            ProjectCommands.index,
            [temp_project_dir, "--language", "python", "--log-level", "ERROR", "--timeout", "5"],
        )
        # Should succeed even without source files if language is explicit
        assert result.exit_code == 0, f"Command failed: {result.output}"

        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_index_with_language_auto_creates(self, cli_runner, temp_project_dir):
        """Test index with --language option for auto-creation."""
        result = cli_runner.invoke(
            ProjectCommands.index,
            [temp_project_dir, "--language", "python", "--log-level", "ERROR"],
        )
        assert result.exit_code == 0 or "Indexing" in result.output

        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_index_is_equivalent_to_create_with_index(self, cli_runner, temp_project_dir_with_python_file):
        """Test that 'index' behaves like 'create --index' for new projects."""
        # Use manual temp directory creation with Windows-safe cleanup
        # to avoid PermissionError on Windows CI when language servers hold file locks
        dir1 = tempfile.mkdtemp()
        dir2 = tempfile.mkdtemp()

        try:
            # Setup both directories with same file
            for d in [dir1, dir2]:
                with open(os.path.join(d, "test.py"), "w") as f:
                    f.write("def hello():\n    pass\n")

            # Run 'create --index' on dir1
            result1 = cli_runner.invoke(
                ProjectCommands.create, [dir1, "--language", "python", "--index", "--log-level", "ERROR", "--timeout", "5"]
            )

            # Run 'index' on dir2
            result2 = cli_runner.invoke(ProjectCommands.index, [dir2, "--language", "python", "--log-level", "ERROR", "--timeout", "5"])

            # Both should succeed
            assert result1.exit_code == 0, f"create --index failed: {result1.output}"
            assert result2.exit_code == 0, f"index failed: {result2.output}"

            # Both should create project.yml
            assert os.path.exists(os.path.join(dir1, ".serena", "project.yml"))
            assert os.path.exists(os.path.join(dir2, ".serena", "project.yml"))

            # Both should create cache (proof of indexing)
            assert os.path.exists(os.path.join(dir1, ".serena", "cache"))
            assert os.path.exists(os.path.join(dir2, ".serena", "cache"))
        finally:
            # Windows-safe cleanup: wait for file handles to be released
            if os.name == "nt":
                time.sleep(0.2)
            # Use ignore_errors to handle lingering file locks on Windows
            shutil.rmtree(dir1, ignore_errors=True)
            shutil.rmtree(dir2, ignore_errors=True)


    def test_create_warns_when_children_have_serena_configs(self, cli_runner, temp_project_dir):
        """Creating a root project.yml when children already have .serena/project.yml must
        emit a visible warning so users understand the monorepo layout implications."""
        # Pre-populate two child dirs with their own .serena/project.yml
        for child in ["alpha", "beta"]:
            child_serena = Path(temp_project_dir) / child / ".serena"
            child_serena.mkdir(parents=True)
            (child_serena / "project.yml").write_text(f"project_name: {child}\nlanguages: []\n")

        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Warning" in result.output
        assert ".serena/project.yml" in result.output
        # Both child names must appear in the warning
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_create_no_warning_when_no_children_have_serena_configs(self, cli_runner, temp_project_dir):
        """No warning should appear when no child directories have .serena/project.yml."""
        # Add a plain directory (no .serena) to ensure the check doesn't over-trigger
        (Path(temp_project_dir) / "src").mkdir()

        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Warning" not in result.output


class TestProjectCreateAll:
    """Tests for 'serena project create-all' command and its MCP tool counterpart."""

    def test_create_all_initialises_unconfigured_children(self, cli_runner, temp_project_dir):
        """create-all must create project.yml for every child that lacks one."""
        parent = Path(temp_project_dir)
        for child in ["alpha", "beta", "gamma"]:
            (parent / child).mkdir()

        result = cli_runner.invoke(ProjectCommands.create_all, [str(parent), "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"

        for child in ["alpha", "beta", "gamma"]:
            yml = parent / child / ".serena" / "project.yml"
            assert yml.exists(), f"project.yml not created for {child}"

        assert "3 created" in result.output
        assert "0 errors" in result.output

    def test_create_all_skips_already_configured_children(self, cli_runner, temp_project_dir):
        """create-all must skip children that already have .serena/project.yml."""
        parent = Path(temp_project_dir)
        (parent / "already").mkdir()
        already_serena = parent / "already" / ".serena"
        already_serena.mkdir()
        (already_serena / "project.yml").write_text("project_name: already\nlanguages: []\n")
        (parent / "new").mkdir()

        result = cli_runner.invoke(ProjectCommands.create_all, [str(parent), "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "skip" in result.output.lower()
        assert "1 created" in result.output
        assert "1 skipped" in result.output

    def test_create_all_empty_parent_reports_no_subdirs(self, cli_runner, temp_project_dir):
        """create-all on a directory with no subdirectories reports clearly."""
        result = cli_runner.invoke(ProjectCommands.create_all, [temp_project_dir])
        assert result.exit_code == 0
        assert "No subdirectories" in result.output

    def test_create_all_helper_returns_result_lines(self, temp_project_dir):
        """_create_all_subprojects helper must return a list of result strings."""
        parent = Path(temp_project_dir)
        (parent / "svc").mkdir()

        lines = ProjectCommands._create_all_subprojects(str(parent), ("python",))
        assert any("svc" in line for line in lines), "Result must mention the child name"
        # Summary line must be present
        assert any("created" in line and "skipped" in line for line in lines)

    def test_initialize_subprojects_mcp_tool(self, temp_project_dir):
        """InitializeSubprojectsTool must delegate to _create_all_subprojects and
        return a summary string."""
        from unittest.mock import MagicMock, patch

        from serena.tools.config_tools import InitializeSubprojectsTool

        parent = Path(temp_project_dir)
        for child in ["svc_a", "svc_b"]:
            (parent / child).mkdir()

        mock_agent = MagicMock()
        tool = InitializeSubprojectsTool.__new__(InitializeSubprojectsTool)
        tool._agent = mock_agent

        result = tool.apply(parent_path=str(parent), language="python")
        assert "svc_a" in result
        assert "svc_b" in result
        assert "created" in result


class TestProjectCreateHelper:
    """Tests for _create_project helper method."""

    def test_create_project_helper_returns_config(self, temp_project_dir):
        """Test that _create_project returns a ProjectConfig with explicit language."""
        config = ProjectCommands._create_project(temp_project_dir, "test-project", ("python",)).project_config
        assert isinstance(config, ProjectConfig)
        assert config.project_name == "test-project"

    def test_create_project_helper_with_auto_detect(self, temp_project_dir_with_python_file):
        """Test _create_project with auto-detected language."""
        config = ProjectCommands._create_project(temp_project_dir_with_python_file, "my-project", ()).project_config
        assert isinstance(config, ProjectConfig)
        assert config.project_name == "my-project"
        assert len(config.languages) >= 1

    def test_create_project_helper_with_languages(self, temp_project_dir):
        """Test _create_project with language specification."""
        config = ProjectCommands._create_project(temp_project_dir, None, ("python", "typescript")).project_config
        assert isinstance(config, ProjectConfig)
        assert len(config.languages) >= 1

    def test_create_project_helper_file_exists_error(self, temp_project_dir):
        """Test _create_project raises error if project.yml exists."""
        # Create project first with explicit language
        ProjectCommands._create_project(temp_project_dir, None, ("python",))

        # Try to create again - should raise FileExistsError
        with pytest.raises(FileExistsError):
            ProjectCommands._create_project(temp_project_dir, None, ("python",))


class TestFindProjectRoot:
    """Tests for find_project_root helper with virtual chroot boundary."""

    def test_finds_serena_from_subdirectory(self, temp_project_dir):
        """Test that .serena/project.yml is found when searching from a subdirectory."""
        serena_dir = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_dir)
        Path(os.path.join(serena_dir, "project.yml")).touch()
        subdir = os.path.join(temp_project_dir, "src", "nested")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_serena_preferred_over_git(self, temp_project_dir):
        """Test that .serena/project.yml takes priority over .git at the same level."""
        serena_dir = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_dir)
        Path(os.path.join(serena_dir, "project.yml")).touch()
        os.makedirs(os.path.join(temp_project_dir, ".git"))

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_project_dir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.isdir(os.path.join(result, ".serena"))
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_git_used_as_fallback(self, temp_project_dir):
        """Test that .git is found when no .serena exists."""
        os.makedirs(os.path.join(temp_project_dir, ".git"))
        subdir = os.path.join(temp_project_dir, "src")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_falls_back_to_none_when_no_markers(self, temp_project_dir):
        """Test falls back to None when no markers exist within boundary."""
        subdir = os.path.join(temp_project_dir, "src")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is None
        finally:
            os.chdir(original_cwd)


class TestResolveProjectRoot:
    """Tests for startup and activation project resolution helpers."""

    def test_resolve_project_root_for_startup_uses_explicit_project(self) -> None:
        assert resolve_project_root_for_startup("/explicit/path") == "/explicit/path"

    def test_resolve_project_root_for_startup_detects_from_cwd(self, temp_project_dir) -> None:
        os.makedirs(os.path.join(temp_project_dir, ".git"))
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_project_dir)
            assert resolve_project_root_for_startup(None) == str(Path(temp_project_dir).resolve())
        finally:
            os.chdir(original_cwd)

    def test_resolve_project_for_activation_dot_uses_cwd(self, temp_project_dir) -> None:
        os.makedirs(os.path.join(temp_project_dir, ".git"))
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_project_dir)
            assert resolve_project_for_activation(".") == str(Path(temp_project_dir).resolve())
            assert resolve_project_for_activation("") == str(Path(temp_project_dir).resolve())
        finally:
            os.chdir(original_cwd)

    def test_resolve_project_for_activation_raises_when_undetectable(self, temp_project_dir) -> None:
        subdir = os.path.join(temp_project_dir, "src")
        os.makedirs(subdir)
        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            with pytest.raises(ValueError, match="IDE workspace root path"):
                resolve_project_for_activation(".")
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    # For manual testing, you can run this file directly:
    # uv run pytest test/serena/test_cli_project_commands.py -v
    pytest.main([__file__, "-v"])
