"""
Pattern-based search and search-and-replace tools for the Serena MCP toolbox.

Four tools are exposed:
- search               read-only, exact text match
- search_regex         read-only, Python regex match (DOTALL + MULTILINE)
- search_and_replace   exact text replace; replacement is required
- search_and_replace_regex  regex replace; replacement is required

The former all-in-one search_and_replace tool (which had a mode parameter and
allowed search-only by omitting replacement) has been split into these four
focused tools for clarity and correct MCP read-only annotation on search tools.
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from serena.tools.tools_base import (
    EditedFileContext,
    EditingToolWithDiagnostics,
    Tool,
    ToolResult,
    format_tool_redirect_error,
)
from serena.util.file_system import scan_directory
from serena.util.text_utils import ContentReplacer, search_files

if TYPE_CHECKING:
    from serena.workspace import SerenaWorkspace


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class SearchResult(ToolResult):
    """Structured result for read-only search (search / search_regex)."""

    def __init__(
        self,
        pattern: str,
        matches: dict[str, list[str]],
        total_count: int,
    ):
        self.pattern = pattern
        self.matches = matches  # labeled_path -> list of context-annotated match strings
        self.total_count = total_count

    def to_mcp_string(self) -> str:
        return json.dumps(self.matches, ensure_ascii=False)

    def to_cli_text(self) -> str:
        if not self.matches:
            return "No matches found."
        lines: list[str] = [f"Found matches in {len(self.matches)} file(s):\n"]
        for path, snippets in self.matches.items():
            lines.append(f"  {path}")
            for snippet in snippets:
                for sline in snippet.splitlines():
                    lines.append(f"    {sline}")
            lines.append("")
        return "\n".join(lines)


@dataclass
class FileChangeRecord:
    """Tracks replacements made in a single file."""

    relative_path: str
    count: int
    preview_lines: list[str] = field(default_factory=list)
    """A short snippet around the first match, for dry-run display."""


class ReplaceResult(ToolResult):
    """Structured result for search_and_replace / search_and_replace_regex (dry-run or apply)."""

    def __init__(
        self,
        mode: str,  # "dry_run" or "apply"
        pattern: str,
        replacement: str,
        changes: list[FileChangeRecord],
        total_count: int,
        dry_run_sample_count: int,
    ):
        self.mode = mode
        self.pattern = pattern
        self.replacement = replacement
        self.changes = changes
        self.total_count = total_count
        self.dry_run_sample_count = dry_run_sample_count

    def to_mcp_string(self) -> str:
        if self.mode == "dry_run":
            data: dict = {
                "total_matches": self.total_count,
                "files_affected": len(self.changes),
                "preview_files": self.dry_run_sample_count,
                "preview": {c.relative_path: c.preview_lines for c in self.changes[: self.dry_run_sample_count]},
            }
            return json.dumps(data, ensure_ascii=False)

        # apply
        manifest = {c.relative_path: c.count for c in self.changes}
        return json.dumps(
            {
                "replacements_made": self.total_count,
                "files_changed": len(self.changes),
                "per_file": manifest,
            },
            ensure_ascii=False,
        )

    def to_cli_text(self) -> str:
        lines: list[str] = []
        if self.mode == "dry_run":
            lines.append(f"DRY RUN: {self.total_count} match(es) in {len(self.changes)} file(s) would be changed.")
            lines.append(f"Showing preview for up to {self.dry_run_sample_count} file(s):\n")
            for record in self.changes[: self.dry_run_sample_count]:
                lines.append(f"  {record.relative_path}  ({record.count} replacement(s))")
                for pline in record.preview_lines:
                    lines.append(f"    {pline}")
                lines.append("")
            return "\n".join(lines)

        # apply
        if not self.changes:
            lines.append("No replacements made (pattern not found in any file).")
        else:
            lines.append(f"Replaced {self.total_count} occurrence(s) across {len(self.changes)} file(s):\n")
            for record in self.changes:
                lines.append(f"  {record.relative_path}  ({record.count} replacement(s))")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared file-scanning + search pipeline
# ---------------------------------------------------------------------------


def _collect_file_matches(
    ws: "SerenaWorkspace",
    pattern: str,
    *,
    literal: bool,
    relative_path: str = "",
    include: str = "",
    exclude: str = "",
    context_lines: int = 2,
) -> tuple[dict[str, list], dict[str, tuple]]:
    """
    Scan project files and return all pattern matches.

    Literal tools pass re.escape(pattern) into search_files so that regex
    metacharacters are treated as literal characters. Regex tools pass the
    pattern unchanged.

    :param ws: active workspace
    :param pattern: search pattern (exact text or regex, see `literal`)
    :param literal: if True, pattern is exact text (re-escaped internally);
        if False, pattern is a Python regex.
    :param relative_path: restrict search to this file or directory
    :param include: glob pattern to include files
    :param exclude: glob pattern to exclude files
    :param context_lines: number of context lines around each match
    :return: (file_matches, labeled_path_to_unit_and_proj_rel) where
        file_matches maps labeled_path -> list[MatchedConsecutiveLines] and
        labeled_path_to_unit_and_proj_rel maps labeled_path -> (unit, proj_rel_path)
    """
    search_pattern = re.escape(pattern) if literal else pattern
    relative_path = relative_path.strip()

    if relative_path:
        unit, proj_rel_path = ws.resolve_unit_for_path(relative_path)
        units_and_paths = [(unit, proj_rel_path)]
    else:
        units_and_paths = [(u, "") for u in ws.units]

    file_matches: dict[str, list] = defaultdict(list)
    labeled_path_to_unit_and_proj_rel: dict[str, tuple] = {}

    for unit, search_path in units_and_paths:
        proj = unit.project
        if search_path and os.path.isfile(os.path.join(proj.project_root, search_path)):
            rel_paths = [search_path]
        else:
            abs_search = os.path.join(proj.project_root, search_path) if search_path else proj.project_root
            if not os.path.exists(abs_search):
                continue
            _dirs, rel_paths = scan_directory(
                path=abs_search,
                recursive=True,
                is_ignored_dir=proj.is_ignored_path,
                is_ignored_file=proj.is_ignored_path,
                relative_to=proj.project_root,
            )

        unit_matches = search_files(
            rel_paths,
            search_pattern,
            context_lines_before=context_lines,
            context_lines_after=context_lines,
            file_reader=proj.read_file,
            root_path=proj.project_root,
            paths_include_glob=include.strip(),
            paths_exclude_glob=exclude.strip(),
            multiline=True,
        )

        for match in unit_matches:
            assert match.source_file_path is not None
            labeled = ws.label(unit.project_id, match.source_file_path)
            file_matches[labeled].append(match)
            labeled_path_to_unit_and_proj_rel[labeled] = (unit, match.source_file_path)

    return file_matches, labeled_path_to_unit_and_proj_rel


# ---------------------------------------------------------------------------
# Search tools (read-only)
# ---------------------------------------------------------------------------


class SearchTool(Tool):
    """
    Search for an exact text string across project files.

    Returns all match locations with surrounding context. This tool is read-only
    and never modifies any file.

    Use search_regex for Python regex patterns. Use search_and_replace to replace
    exact text, or search_and_replace_regex for regex-based replacements.
    """

    def apply(
        self,
        pattern: str,
        relative_path: str = "",
        include: str = "",
        exclude: str = "",
        context_lines: int = 2,
    ) -> SearchResult:
        """
        Search for an exact text string across project files.

        :param pattern: exact text to search for.
        :param relative_path: restrict search to this file or directory. Empty means the whole project.
        :param include: glob pattern to restrict which files are searched, e.g. "src/**/*.py".
        :param exclude: glob pattern to exclude files; takes precedence over include.
        :param context_lines: number of context lines to show around each match.
        :return: match locations with context, grouped by file.
        """
        file_matches, _ = _collect_file_matches(
            self.workspace,
            pattern,
            literal=True,
            relative_path=relative_path,
            include=include,
            exclude=exclude,
            context_lines=context_lines,
        )
        total = sum(len(v) for v in file_matches.values())
        matches_display = {path: [m.to_display_string() for m in ml] for path, ml in file_matches.items()}
        return SearchResult(pattern=pattern, matches=matches_display, total_count=total)


class SearchRegexTool(Tool):
    """
    Search for a Python regex pattern across project files.

    Returns all match locations with surrounding context. This tool is read-only
    and never modifies any file. Uses Python re syntax with DOTALL and MULTILINE flags.

    Use search for exact text (literal) matching. Use search_and_replace_regex to
    replace regex matches.
    """

    def apply(
        self,
        pattern: str,
        relative_path: str = "",
        include: str = "",
        exclude: str = "",
        context_lines: int = 2,
    ) -> SearchResult:
        r"""
        Search for a Python regex pattern across project files.

        :param pattern: Python regex pattern. DOTALL and MULTILINE flags are applied automatically.
        :param relative_path: restrict search to this file or directory. Empty means the whole project.
        :param include: glob pattern to restrict which files are searched, e.g. "src/**/*.py".
        :param exclude: glob pattern to exclude files; takes precedence over include.
        :param context_lines: number of context lines to show around each match.
        :return: match locations with context, grouped by file.
        """
        file_matches, _ = _collect_file_matches(
            self.workspace,
            pattern,
            literal=False,
            relative_path=relative_path,
            include=include,
            exclude=exclude,
            context_lines=context_lines,
        )
        total = sum(len(v) for v in file_matches.values())
        matches_display = {path: [m.to_display_string() for m in ml] for path, ml in file_matches.items()}
        return SearchResult(pattern=pattern, matches=matches_display, total_count=total)


# ---------------------------------------------------------------------------
# Replace tools (destructive)
# ---------------------------------------------------------------------------


class SearchAndReplaceTool(EditingToolWithDiagnostics):
    """
    Replace an exact text string across project files.

    Two sub-modes controlled by dry_run:
    - Dry run (dry_run=True): preview total match count and sample diffs without modifying files.
    - Apply (dry_run=False, the default): perform the replacements and return a per-file manifest.

    Use search for read-only literal search. Use search_and_replace_regex for regex-based replacements.
    """

    def apply(
        self,
        pattern: str,
        replacement: str | None = None,
        relative_path: str = "",
        include: str = "",
        exclude: str = "",
        dry_run: bool = False,
        context_lines: int = 2,
        max_preview_files: int = 3,
    ) -> "ReplaceResult | str":
        """
        Replace all occurrences of an exact text string across project files.

        :param pattern: exact text to search for.
        :param replacement: replacement text. Required — omitting it returns a redirect error.
        :param relative_path: restrict search to this file or directory. Empty means the whole project.
        :param include: glob pattern to restrict which files are searched, e.g. "src/**/*.py".
        :param exclude: glob pattern to exclude files; takes precedence over include.
        :param dry_run: when True, show a preview without modifying files.
        :param context_lines: number of context lines to show around each match in dry-run output.
        :param max_preview_files: maximum number of files to show in dry-run preview.
        :return: change manifest (apply) or preview (dry-run).
        """
        # replacement is logically required; returning a redirect is better UX than a raw TypeError
        if replacement is None:
            return format_tool_redirect_error(
                tool_name="search_and_replace",
                reason="replacement is required. Use a dedicated search tool if you only want to find matches.",
                suggestions=[
                    ("search", "read-only exact text search"),
                    ("search_regex", "read-only regex search"),
                    ("search_and_replace_regex", "regex-based replace"),
                ],
            )

        file_matches, labeled_path_to_unit_and_proj_rel = _collect_file_matches(
            self.workspace,
            pattern,
            literal=True,
            relative_path=relative_path,
            include=include,
            exclude=exclude,
            context_lines=context_lines,
        )
        total_matches = sum(len(v) for v in file_matches.values())

        if dry_run:
            changes: list[FileChangeRecord] = []
            for labeled_path, match_list in file_matches.items():
                preview = [line for m in match_list[:2] for line in m.to_display_string().splitlines()]
                changes.append(FileChangeRecord(
                    relative_path=labeled_path,
                    count=len(match_list),
                    preview_lines=preview,
                ))
            return ReplaceResult(
                mode="dry_run",
                pattern=pattern,
                replacement=replacement,
                changes=changes,
                total_count=total_matches,
                dry_run_sample_count=max_preview_files,
            )

        replacer = ContentReplacer(mode="literal", allow_multiple_occurrences=True)
        apply_changes: list[FileChangeRecord] = []

        for labeled_path, _match_list in file_matches.items():
            unit, proj_rel = labeled_path_to_unit_and_proj_rel[labeled_path]
            proj = unit.project
            with self.DiagnosticsContext(self, proj_rel, project=proj):
                with EditedFileContext(proj_rel, self.create_ls_code_editor_for(proj)) as ctx:
                    original = ctx.get_original_content()
                    count = original.count(pattern)
                    if count == 0:
                        continue
                    updated = replacer.replace(original, pattern, replacement)
                    ctx.set_updated_content(updated)
            apply_changes.append(FileChangeRecord(relative_path=labeled_path, count=count))

        total_applied = sum(c.count for c in apply_changes)
        return ReplaceResult(
            mode="apply",
            pattern=pattern,
            replacement=replacement,
            changes=apply_changes,
            total_count=total_applied,
            dry_run_sample_count=0,
        )


class SearchAndReplaceRegexTool(EditingToolWithDiagnostics):
    """
    Replace a Python regex pattern across project files.

    Two sub-modes controlled by dry_run:
    - Dry run (dry_run=True): preview total match count and sample diffs without modifying files.
    - Apply (dry_run=False, the default): perform the replacements and return a per-file manifest.

    Uses Python re syntax with DOTALL and MULTILINE flags. Backreferences in replacement
    use the $!1, $!2, ... syntax (e.g. replace "(\\w+)_old" with "$!1_new").

    Use search_regex for read-only regex search. Use search_and_replace for literal text replacements.
    """

    def apply(
        self,
        pattern: str,
        replacement: str | None = None,
        relative_path: str = "",
        include: str = "",
        exclude: str = "",
        dry_run: bool = False,
        context_lines: int = 2,
        max_preview_files: int = 3,
    ) -> "ReplaceResult | str":
        r"""
        Replace all occurrences of a Python regex pattern across project files.

        Regex backreferences in replacement use the $!1, $!2, ... syntax
        (e.g. replace "(\w+)_old" with "$!1_new").

        :param pattern: Python regex pattern. DOTALL and MULTILINE flags are applied automatically.
        :param replacement: replacement text; use $!1, $!2, ... for captured groups. Required.
        :param relative_path: restrict search to this file or directory. Empty means the whole project.
        :param include: glob pattern to restrict which files are searched, e.g. "src/**/*.py".
        :param exclude: glob pattern to exclude files; takes precedence over include.
        :param dry_run: when True, show a preview without modifying files.
        :param context_lines: number of context lines to show around each match in dry-run output.
        :param max_preview_files: maximum number of files to show in dry-run preview.
        :return: change manifest (apply) or preview (dry-run).
        """
        if replacement is None:
            return format_tool_redirect_error(
                tool_name="search_and_replace_regex",
                reason="replacement is required. Use a dedicated search tool if you only want to find matches.",
                suggestions=[
                    ("search_regex", "read-only regex search"),
                    ("search", "read-only exact text search"),
                    ("search_and_replace", "literal text replace"),
                ],
            )

        file_matches, labeled_path_to_unit_and_proj_rel = _collect_file_matches(
            self.workspace,
            pattern,
            literal=False,
            relative_path=relative_path,
            include=include,
            exclude=exclude,
            context_lines=context_lines,
        )
        total_matches = sum(len(v) for v in file_matches.values())

        if dry_run:
            changes: list[FileChangeRecord] = []
            for labeled_path, match_list in file_matches.items():
                preview = [line for m in match_list[:2] for line in m.to_display_string().splitlines()]
                changes.append(FileChangeRecord(
                    relative_path=labeled_path,
                    count=len(match_list),
                    preview_lines=preview,
                ))
            return ReplaceResult(
                mode="dry_run",
                pattern=pattern,
                replacement=replacement,
                changes=changes,
                total_count=total_matches,
                dry_run_sample_count=max_preview_files,
            )

        replacer = ContentReplacer(mode="regex", allow_multiple_occurrences=True)
        apply_changes: list[FileChangeRecord] = []

        for labeled_path, _match_list in file_matches.items():
            unit, proj_rel = labeled_path_to_unit_and_proj_rel[labeled_path]
            proj = unit.project
            with self.DiagnosticsContext(self, proj_rel, project=proj):
                with EditedFileContext(proj_rel, self.create_ls_code_editor_for(proj)) as ctx:
                    original = ctx.get_original_content()
                    flags = re.MULTILINE | re.DOTALL
                    count = len(re.findall(pattern, original, flags=flags))
                    if count == 0:
                        continue
                    updated = replacer.replace(original, pattern, replacement)
                    ctx.set_updated_content(updated)
            apply_changes.append(FileChangeRecord(relative_path=labeled_path, count=count))

        total_applied = sum(c.count for c in apply_changes)
        return ReplaceResult(
            mode="apply",
            pattern=pattern,
            replacement=replacement,
            changes=apply_changes,
            total_count=total_applied,
            dry_run_sample_count=0,
        )
