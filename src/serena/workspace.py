"""
SerenaWorkspace: a multi-project container activated from a single repository root.

When a repository is activated, Serena scans recursively for nested Serena
projects (.serena/project.yml).  Each discovered sub-directory with its own
.serena/project.yml becomes a delegated ProjectUnit that retains full
ownership of its validation, config, ignore rules, and language-server
lifecycle.  The workspace layer routes tool calls to the appropriate unit by
longest-prefix path match and labels results with stable project identifiers.

Upper-layer behaviour:
- If a subdirectory already has .serena/project.yml, we load it as-is and
  stop recursing — that project owns itself.
- If no sub-projects are found, or if workspace_root itself has
  .serena/project.yml, the workspace becomes a single-unit workspace (fully
  backward-compatible).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from serena.project import Project

if TYPE_CHECKING:
    from serena.config.serena_config import SerenaConfig
    from solidlsp.ls_config import Language

log = logging.getLogger(__name__)

# Directories that should never be scanned for nested projects.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        # build artefacts
        "bin",
        "obj",
        "target",
        "dist",
        "build",
        ".build",
        "out",
        # dependency directories
        "node_modules",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        # serena data — don't load .serena/project.yml inside a .serena folder
        ".serena",
        # IDE / VCS metadata
        ".idea",
        ".vs",
        ".vscode",
    }
)

# Maximum directory depth to recurse (workspace root = depth 0).
_DEFAULT_MAX_DEPTH: int = 6

# Shallow scan depth when checking for indexable source files in health_summary.
_SOURCE_FILE_SCAN_MAX_DEPTH: int = 3


@dataclass
class ProjectUnit:
    """
    A single project unit inside a workspace.

    workspace_relative_path is the path from workspace root to this project
    root.  For the workspace root itself it is the empty string.
    """

    project: Project
    workspace_relative_path: str

    @property
    def project_id(self) -> str:
        """
        Short, human-readable identifier used in tool output labels.

        Uses the last path component of workspace_relative_path so agents can
        tell which sub-directory a result came from.  Falls back to the
        project_name if the path is empty (root project).
        """
        if not self.workspace_relative_path:
            return self.project.project_name
        # e.g. "backend/api" → "api"
        last = os.path.basename(self.workspace_relative_path)
        return last or self.project.project_name

    @property
    def depth(self) -> int:
        """Nesting depth relative to workspace root (0 = root project)."""
        if not self.workspace_relative_path:
            return 0
        return len(Path(self.workspace_relative_path).parts)

    def __repr__(self) -> str:
        p = self.workspace_relative_path
        return f"ProjectUnit(id={self.project_id!r}, path={p!r})"


class SerenaWorkspace:
    """
    Holds all project units discovered under a workspace root.

    Tool calls are routed to the owning project unit by longest-prefix path
    match.  In multi-project workspaces, results are prefixed with
    ``[project_id]`` so agents can distinguish project membership.
    """

    def __init__(self, workspace_root: str, units: list[ProjectUnit]) -> None:
        self.workspace_root: str = os.path.normpath(workspace_root)
        # Sorted shallowest-first so iteration order is deterministic.
        self._units: list[ProjectUnit] = sorted(units, key=lambda u: u.depth)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def units(self) -> list[ProjectUnit]:
        return list(self._units)

    @property
    def primary_unit(self) -> ProjectUnit:
        """The shallowest (root) project unit — always present."""
        if not self._units:
            raise ValueError("Workspace has no project units.")
        return self._units[0]

    @property
    def is_multi_project(self) -> bool:
        return len(self._units) > 1

    # ------------------------------------------------------------------
    # Path routing
    # ------------------------------------------------------------------

    def resolve_unit_for_path(
        self, workspace_relative_path: str
    ) -> tuple[ProjectUnit, str]:
        """
        Find the deepest ProjectUnit whose root is a prefix of
        *workspace_relative_path* and return ``(unit, project_relative_path)``.

        Falls back to the primary (root) unit when no prefix match exists.
        """
        # Normalise: "." and "" both mean the workspace root itself.
        if workspace_relative_path in ("", "."):
            return self.primary_unit, ""

        norm = os.path.normpath(workspace_relative_path)
        best_unit: ProjectUnit = self.primary_unit
        best_depth: int = -1

        for unit in self._units:
            unit_rel = unit.workspace_relative_path
            if not unit_rel:
                # Root project — matches everything, lowest priority.
                if best_depth < 0:
                    best_unit = unit
                    best_depth = 0
                continue

            try:
                # Check whether norm is inside unit_rel.
                proj_rel = Path(norm).relative_to(Path(unit_rel))
                depth = len(Path(unit_rel).parts)
                if depth > best_depth:
                    best_unit = unit
                    best_depth = depth
            except ValueError:
                continue

        # Compute the project-relative path for the winning unit.
        unit_rel = best_unit.workspace_relative_path
        if not unit_rel:
            project_relative = norm
        else:
            try:
                project_relative = str(Path(norm).relative_to(Path(unit_rel)))
            except ValueError:
                project_relative = norm

        return best_unit, project_relative

    def label(self, project_id: str, project_relative_path: str) -> str:
        """
        Format a result line with project context.

        In single-project workspaces the label is omitted (backward compat).
        """
        if not self.is_multi_project:
            return project_relative_path
        return f"[{project_id}] {project_relative_path}"

    def label_message(self, project_id: str, message: str) -> str:
        """Label a free-text message with a project identifier."""
        if not self.is_multi_project:
            return message
        return f"[{project_id}] {message}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self, timeout: float = 2.0) -> None:
        for unit in self._units:
            try:
                unit.project.shutdown(timeout=timeout)
            except Exception as exc:
                log.warning(
                    "Error shutting down project unit %s: %s", unit.project_id, exc
                )

    # ------------------------------------------------------------------
    # Factory / discovery
    # ------------------------------------------------------------------

    @classmethod
    def discover_and_create(
        cls,
        workspace_root: str,
        serena_config: "SerenaConfig",
        max_depth: int = _DEFAULT_MAX_DEPTH,
        languages: "list[Language] | None" = None,
    ) -> "SerenaWorkspace":
        """
        Recursively discover Serena project units under *workspace_root* and
        return a ready-to-use SerenaWorkspace.

        Discovery rules
        ---------------
        1. If *workspace_root* itself contains ``.serena/project.yml``, it is
           treated as a project unit and recursion continues into children so
           that nested projects are also discovered.
        2. Any **non-root** sub-directory that contains ``.serena/project.yml``
           becomes a ProjectUnit.  Recursion stops at that level — the project
           owns its subtree.
        3. Directories in ``_SKIP_DIRS`` or starting with ``.`` are not scanned.
        4. If no project files are found anywhere, the workspace root is
           auto-generated as a single project (preserves backward compatibility).

        :param languages: language(s) to use when auto-generating .serena/project.yml
            for directories without existing config. Passed to Project.load so that
            auto-detection is skipped entirely when a value is supplied.
        """
        workspace_root = str(Path(workspace_root).resolve())

        discovered: list[tuple[str, str]] = []  # (abs_path, workspace_relative_path)
        cls._scan(workspace_root, workspace_root, 0, max_depth, discovered)

        if not discovered:
            # No .serena/project.yml found anywhere — auto-create root project.
            discovered = [(workspace_root, "")]

        units: list[ProjectUnit] = []
        for abs_path, ws_rel in discovered:
            try:
                # Prefer a registered project that already has the correct name etc.
                registered = serena_config.get_registered_project(abs_path)
                if registered is not None:
                    project = registered.get_project_instance(serena_config=serena_config)
                else:
                    # Pass languages so auto-generation uses the provided value
                    # instead of running the expensive tree-walk language detection.
                    project = Project.load(
                        abs_path, serena_config=serena_config, autogenerate=True,
                        languages=languages,
                    )
                units.append(
                    ProjectUnit(project=project, workspace_relative_path=ws_rel)
                )
                log.info(
                    "Workspace: loaded project unit '%s' at %s",
                    project.project_name,
                    ws_rel or "(root)",
                )
            except Exception as exc:
                log.warning(
                    "Workspace: could not load project at %s: %s", abs_path, exc
                )

        if not units:
            raise ValueError(
                f"No usable project units found under workspace root: {workspace_root}"
            )

        return cls(workspace_root, units)

    @classmethod
    def _scan(
        cls,
        current_dir: str,
        workspace_root: str,
        depth: int,
        max_depth: int,
        discovered: list[tuple[str, str]],
    ) -> None:
        """Depth-first scan that adds directories with .serena/project.yml."""
        if depth > max_depth:
            return

        project_yml = os.path.join(current_dir, ".serena", "project.yml")
        has_project = os.path.isfile(project_yml)
        ws_rel = os.path.relpath(current_dir, workspace_root)
        if ws_rel == ".":
            ws_rel = ""

        if has_project:
            discovered.append((current_dir, ws_rel))
            if ws_rel:
                # Non-root project owns its subtree; do not recurse further.
                # (Per design: "the lower projects handle their stuff")
                return
            # Workspace root may have its own project.yml AND nested children —
            # continue scanning so children are also registered as units.

        if depth >= max_depth:
            return

        # Recurse into non-skipped subdirectories.
        try:
            children = sorted(os.scandir(current_dir), key=lambda e: e.name)
        except PermissionError:
            return

        for entry in children:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            cls._scan(entry.path, workspace_root, depth + 1, max_depth, discovered)

    # ------------------------------------------------------------------
    # Status summary
    # ------------------------------------------------------------------

    def health_summary(self) -> str:
        """
        Returns a concise workspace status string.

        Only units that require attention are listed individually:
        - LS_ERROR: language server failed to start
        - DEGRADED: some languages failed
        - OK (no source files indexed): LS started but found nothing to index

        Healthy units (OK with source files) are collapsed into a single count
        line to keep the output scannable. Includes "Activated project" phrasing
        for backward-compatible test/client checks.
        """
        unit_names = ", ".join(u.project.project_name for u in self._units)
        lines: list[str] = [
            f"Activated project workspace: {self.workspace_root}",
            f"Projects ({len(self._units)}): {unit_names}",
        ]

        ok_count = 0
        attention_lines: list[str] = []

        for unit in self._units:
            proj = unit.project
            languages = [l.value for l in proj.project_config.languages]
            ls_mgr = proj.language_server_manager
            rel = unit.workspace_relative_path or "(root)"

            if ls_mgr is None:
                # Language-server manager may still be initialising or errored.
                if proj._language_server_manager_init_error is not None:
                    attention_lines.append(
                        f"  [{unit.project_id}] {rel}  languages={languages}"
                        f"  LS_ERROR: {proj._language_server_manager_init_error}"
                    )
                else:
                    attention_lines.append(
                        f"  [{unit.project_id}] {rel}  languages={languages}  LS_INITIALISING"
                    )
            else:
                active_langs = [l.value for l in ls_mgr.get_active_languages()]
                failed_langs = [l.value for l in ls_mgr.get_failed_languages()]
                if failed_langs:
                    attention_lines.append(
                        f"  [{unit.project_id}] {rel}  languages={languages}"
                        f"  DEGRADED (failed: {', '.join(failed_langs)})"
                    )
                elif not _has_indexable_source_files(proj, active_langs):
                    attention_lines.append(
                        f"  [{unit.project_id}] {rel}  languages={languages}"
                        "  OK (no source files indexed — LS started but found no indexable source)"
                    )
                else:
                    # Healthy — aggregate rather than list individually.
                    ok_count += 1

        if ok_count:
            lines.append(f"  {ok_count} project unit(s) OK (language servers running)")
        lines.extend(attention_lines)

        return "\n".join(lines)

    def attention_units(self) -> list[tuple[str, str]]:
        """
        Return (project_id, status_tag) pairs for units that need attention.

        Status tags: 'LS_ERROR', 'DEGRADED', 'NO_SOURCE'.
        Healthy OK units are excluded. Used by start_here to build SETUP NOTES.
        """
        result: list[tuple[str, str]] = []
        for unit in self._units:
            proj = unit.project
            ls_mgr = proj.language_server_manager
            if ls_mgr is None:
                tag = "LS_ERROR" if proj._language_server_manager_init_error else "LS_INITIALISING"
                result.append((unit.project_id, tag))
            else:
                active_langs = [l.value for l in ls_mgr.get_active_languages()]
                if ls_mgr.get_failed_languages():
                    result.append((unit.project_id, "DEGRADED"))
                elif not _has_indexable_source_files(proj, active_langs):
                    result.append((unit.project_id, "NO_SOURCE"))
        return result


def _source_extensions_for_language_values(language_values: list[str]) -> frozenset[str]:
    """Collect file extensions for active/configured languages from the central registry."""
    from solidlsp.language_registry import get_entry

    extensions: set[str] = set()
    for lang_value in language_values:
        entry = get_entry(lang_value)
        if entry is not None:
            extensions.update(entry.extensions)
    return frozenset(extensions)


def _has_indexable_source_files(proj: Project, active_lang_values: list[str]) -> bool:
    """
    Return True if at least one source file for the active languages exists under the project root.

    Uses a shallow directory walk (not a full tree scan) so health checks stay lightweight.
  """
    lang_values = active_lang_values or [lang.value for lang in proj.project_config.languages]
    extensions = _source_extensions_for_language_values(lang_values)
    if not extensions:
        # Unknown language metadata — do not claim the index is empty.
        return True

    root = proj.project_root
    for dirpath, dirnames, filenames in os.walk(root):
        depth = 0 if dirpath == root else len(Path(dirpath).relative_to(root).parts)
        if depth >= _SOURCE_FILE_SCAN_MAX_DEPTH:
            dirnames.clear()
        else:
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]
        for filename in filenames:
            for ext in extensions:
                if filename.endswith(ext):
                    return True
    return False
