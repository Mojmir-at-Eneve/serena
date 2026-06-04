"""
Tests for SerenaWorkspace: recursive discovery, path routing, project identifiers,
and backward-compat single-project behaviour.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from solidlsp.ls_config import Language

from serena.workspace import ProjectUnit, SerenaWorkspace, _SKIP_DIRS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_project(root: str, name: str) -> MagicMock:
    proj = MagicMock()
    proj.project_root = root
    proj.project_name = name
    proj.project_config = MagicMock()
    proj.project_config.languages = []
    proj.language_server_manager = None
    proj._language_server_manager_init_error = None
    return proj


def _unit(root: str, ws_rel: str = "", name: str | None = None) -> ProjectUnit:
    return ProjectUnit(
        project=_make_mock_project(root, name or os.path.basename(root) or "root"),
        workspace_relative_path=ws_rel,
    )


# ---------------------------------------------------------------------------
# ProjectUnit
# ---------------------------------------------------------------------------


class TestProjectUnit:
    def test_project_id_root_uses_project_name(self) -> None:
        unit = _unit("/workspace", "", name="my_project")
        assert unit.project_id == "my_project"

    def test_project_id_subdir_uses_last_component(self) -> None:
        unit = _unit("/workspace/backend", "backend", name="backend_svc")
        assert unit.project_id == "backend"

    def test_depth_root(self) -> None:
        unit = _unit("/workspace", "")
        assert unit.depth == 0

    def test_depth_nested(self) -> None:
        unit = _unit("/workspace/a/b", "a/b")
        assert unit.depth == 2


# ---------------------------------------------------------------------------
# SerenaWorkspace — path routing
# ---------------------------------------------------------------------------


class TestSerenaWorkspaceRouting:
    @pytest.fixture
    def multi_workspace(self) -> SerenaWorkspace:
        root_unit = _unit("/ws", "", name="root")
        backend_unit = _unit("/ws/backend", "backend", name="backend")
        frontend_unit = _unit("/ws/frontend", "frontend", name="frontend")
        return SerenaWorkspace("/ws", [root_unit, backend_unit, frontend_unit])

    @pytest.fixture
    def single_workspace(self) -> SerenaWorkspace:
        root_unit = _unit("/ws", "", name="my_project")
        return SerenaWorkspace("/ws", [root_unit])

    def test_root_is_primary(self, multi_workspace: SerenaWorkspace) -> None:
        assert multi_workspace.primary_unit.workspace_relative_path == ""

    def test_resolve_backend_file(self, multi_workspace: SerenaWorkspace) -> None:
        unit, proj_rel = multi_workspace.resolve_unit_for_path("backend/src/Foo.cs")
        assert unit.project_id == "backend"
        assert proj_rel == os.path.join("src", "Foo.cs")

    def test_resolve_frontend_file(self, multi_workspace: SerenaWorkspace) -> None:
        unit, proj_rel = multi_workspace.resolve_unit_for_path("frontend/index.ts")
        assert unit.project_id == "frontend"
        assert proj_rel == "index.ts"

    def test_resolve_root_level_file(self, multi_workspace: SerenaWorkspace) -> None:
        unit, proj_rel = multi_workspace.resolve_unit_for_path("README.md")
        assert unit.workspace_relative_path == ""  # root
        assert proj_rel == "README.md"

    def test_resolve_empty_path(self, multi_workspace: SerenaWorkspace) -> None:
        unit, proj_rel = multi_workspace.resolve_unit_for_path("")
        assert unit.workspace_relative_path == ""
        assert proj_rel == ""

    def test_is_multi_project(self, multi_workspace: SerenaWorkspace) -> None:
        assert multi_workspace.is_multi_project is True

    def test_single_not_multi_project(self, single_workspace: SerenaWorkspace) -> None:
        assert single_workspace.is_multi_project is False

    def test_label_multi(self, multi_workspace: SerenaWorkspace) -> None:
        label = multi_workspace.label("backend", "src/Foo.cs")
        assert label == "[backend] src/Foo.cs"

    def test_label_single_no_prefix(self, single_workspace: SerenaWorkspace) -> None:
        label = single_workspace.label("my_project", "src/Foo.cs")
        assert label == "src/Foo.cs"

    def test_label_message_multi(self, multi_workspace: SerenaWorkspace) -> None:
        msg = multi_workspace.label_message("frontend", "compiled ok")
        assert msg == "[frontend] compiled ok"

    def test_units_sorted_by_depth(self, multi_workspace: SerenaWorkspace) -> None:
        depths = [u.depth for u in multi_workspace.units]
        assert depths == sorted(depths)


# ---------------------------------------------------------------------------
# SerenaWorkspace — discovery
# ---------------------------------------------------------------------------


class TestSerenaWorkspaceDiscovery:
    def test_discovers_nested_serena_projects(self) -> None:
        """
        workspace_root/
          backend/
            .serena/project.yml   <- sub-project
          frontend/
            .serena/project.yml   <- sub-project
        """
        with tempfile.TemporaryDirectory() as ws_root:
            for subdir in ["backend", "frontend"]:
                proj_yml_dir = Path(ws_root) / subdir / ".serena"
                proj_yml_dir.mkdir(parents=True)
                (proj_yml_dir / "project.yml").write_text(
                    f"project_name: {subdir}\nlanguages: []\n"
                )

            mock_serena_config = MagicMock()
            mock_serena_config.get_registered_project.return_value = None

            def mock_project_load(path, serena_config, autogenerate):
                proj = _make_mock_project(str(path), Path(path).name)
                return proj

            with patch("serena.workspace.Project.load", side_effect=mock_project_load):
                ws = SerenaWorkspace.discover_and_create(ws_root, mock_serena_config)

            unit_paths = {u.workspace_relative_path for u in ws.units}
            assert "backend" in unit_paths
            assert "frontend" in unit_paths
            # workspace root itself has no .serena/project.yml but is the root
            # (discovered as fallback since root has no project.yml and no sub sub-project)

    def test_stops_recursion_at_nested_serena_project(self) -> None:
        """
        A nested project's subtree must NOT be scanned further.
        """
        with tempfile.TemporaryDirectory() as ws_root:
            # backend is a serena project
            backend_serena = Path(ws_root) / "backend" / ".serena"
            backend_serena.mkdir(parents=True)
            (backend_serena / "project.yml").write_text("project_name: backend\nlanguages: []\n")
            # deeply nested sub-project inside backend — must NOT be discovered
            deep_serena = Path(ws_root) / "backend" / "deep" / "sub" / ".serena"
            deep_serena.mkdir(parents=True)
            (deep_serena / "project.yml").write_text("project_name: deep_sub\nlanguages: []\n")

            mock_serena_config = MagicMock()
            mock_serena_config.get_registered_project.return_value = None

            def mock_project_load(path, serena_config, autogenerate):
                return _make_mock_project(str(path), Path(path).name)

            with patch("serena.workspace.Project.load", side_effect=mock_project_load):
                ws = SerenaWorkspace.discover_and_create(ws_root, mock_serena_config)

            unit_paths = {u.workspace_relative_path for u in ws.units}
            assert "backend" in unit_paths
            assert "backend/deep/sub" not in unit_paths

    def test_skip_dirs_not_scanned(self) -> None:
        """node_modules and similar directories must never become project units."""
        with tempfile.TemporaryDirectory() as ws_root:
            for skip_dir in ["node_modules", "bin", ".git"]:
                skip_path = Path(ws_root) / skip_dir / ".serena"
                skip_path.mkdir(parents=True)
                (skip_path / "project.yml").write_text("project_name: skip\nlanguages: []\n")

            mock_serena_config = MagicMock()
            mock_serena_config.get_registered_project.return_value = None

            def mock_project_load(path, serena_config, autogenerate):
                return _make_mock_project(str(path), Path(path).name)

            with patch("serena.workspace.Project.load", side_effect=mock_project_load):
                ws = SerenaWorkspace.discover_and_create(ws_root, mock_serena_config)

            unit_paths = {u.workspace_relative_path for u in ws.units}
            assert not any("node_modules" in p for p in unit_paths)
            assert not any("bin" in p for p in unit_paths)

    def test_single_project_fallback(self) -> None:
        """No .serena/project.yml anywhere → single root project created."""
        with tempfile.TemporaryDirectory() as ws_root:
            mock_serena_config = MagicMock()
            mock_serena_config.get_registered_project.return_value = None

            def mock_project_load(path, serena_config, autogenerate):
                return _make_mock_project(str(path), "root")

            with patch("serena.workspace.Project.load", side_effect=mock_project_load):
                ws = SerenaWorkspace.discover_and_create(ws_root, mock_serena_config)

            assert len(ws.units) == 1
            assert ws.units[0].workspace_relative_path == ""


# ---------------------------------------------------------------------------
# Backward compat: single-project workspace behaves like the old model
# ---------------------------------------------------------------------------


class TestHealthSummary:
    def test_health_summary_flags_no_indexable_source_files(self) -> None:
        """C# LS can be OK on .sln-only trees with no .cs files — surface that in health."""
        with tempfile.TemporaryDirectory() as proj_root:
            Path(proj_root, "App.sln").write_text(
                "Microsoft Visual Studio Solution File, Format Version 12.00\n",
                encoding="utf-8",
            )
            proj = _make_mock_project(proj_root, "sln_only")
            proj.project_config.languages = [Language.CSHARP]
            ls_mgr = MagicMock()
            ls_mgr.get_active_languages.return_value = [Language.CSHARP]
            ls_mgr.get_failed_languages.return_value = []
            proj.language_server_manager = ls_mgr

            ws = SerenaWorkspace(proj_root, [_unit(proj_root, "", name="sln_only")])
            ws.units[0].project = proj

            summary = ws.health_summary()
            assert "no source files indexed" in summary


class TestSingleProjectBackwardCompat:
    def test_primary_unit_covers_all_paths(self) -> None:
        unit = _unit("/project", "", name="my_project")
        ws = SerenaWorkspace("/project", [unit])
        resolved_unit, proj_rel = ws.resolve_unit_for_path("src/main.py")
        assert resolved_unit is unit
        assert proj_rel == "src/main.py"

    def test_no_label_prefix_in_single_project(self) -> None:
        unit = _unit("/project", "", name="my_project")
        ws = SerenaWorkspace("/project", [unit])
        assert ws.label("my_project", "src/foo.py") == "src/foo.py"
