"""
Search-and-replace tool for the Serena MCP toolbox.

All former file-system and line-oriented tools (read_file, create_text_file,
list_dir, find_file, replace_content, delete_lines, replace_lines,
insert_at_line, search_for_pattern) have been removed. Agents should use
host built-in file tools for those operations.

This module retains only the LSP-aware search_and_replace tool, which is the
single point-of-truth for pattern-based text edits across project files.
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from serena.tools.tools_base import (
    EditedFileContext,
    EditingToolWithDiagnostics,
    ToolResult,
)
from serena.util.file_system import scan_directory
from serena.util.text_utils import ContentReplacer, search_files


@dataclass
class FileChangeRecord:
    """Tracks replacements made in a single file."""

    relative_path: str
    count: int
    preview_lines: list[str] = field(default_factory=list)
    """A short snippet around the first match, for dry-run display."""


class SearchAndReplaceResult(ToolResult):
    """
    Structured result for search_and_replace.

    Covers all three modes:
    - search-only (replacement is None): match locations and context.
    - dry-run (replacement set, dry_run=True): preview diffs + total count.
    - apply (replacement set, dry_run=False): per-file change manifest.
    """

    def __init__(
        self,
        mode: Literal["search", "dry_run", "apply"],
        pattern: str,
        replacement: str | None,
        matches: dict[str, list[str]],
        changes: list[FileChangeRecord],
        total_count: int,
        dry_run_sample_count: int,
    ):
        self.mode = mode
        self.pattern = pattern
        self.replacement = replacement
        self.matches = matches  # file -> list of context-annotated match strings
        self.changes = changes
        self.total_count = total_count
        self.dry_run_sample_count = dry_run_sample_count

    def to_mcp_string(self) -> str:
        if self.mode == "search":
            return json.dumps(self.matches, ensure_ascii=False)

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

        if self.mode == "search":
            if not self.matches:
                lines.append("No matches found.")
            else:
                lines.append(f"Found matches in {len(self.matches)} file(s):\n")
                for path, snippets in self.matches.items():
                    lines.append(f"  {path}")
                    for snippet in snippets:
                        for sline in snippet.splitlines():
                            lines.append(f"    {sline}")
                    lines.append("")
            return "\n".join(lines)

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


class SearchAndReplaceTool(EditingToolWithDiagnostics):
    """
    Search for a pattern across project files and optionally replace it.

    Three modes depending on the arguments:
    - Search only  (replacement omitted): returns all match locations with context.
    - Dry run (replacement + dry_run=True): shows a sample of what would change
      plus the total replacement count, without touching any file.
    - Apply (replacement + dry_run=False): performs the replacements and returns
      a per-file change manifest.

    Use literal mode for exact text matches; use regex mode for patterns that
    would otherwise require heavy escaping. Regex uses Python re syntax with
    DOTALL and MULTILINE flags.
    """

    def apply(
        self,
        pattern: str,
        replacement: str | None = None,
        mode: Literal["literal", "regex"] = "literal",
        relative_path: str = "",
        include: str = "",
        exclude: str = "",
        dry_run: bool = False,
        context_lines: int = 2,
        max_preview_files: int = 3,
    ) -> SearchAndReplaceResult:
        r"""
        Search for a pattern across the project (or a sub-path) and optionally replace it.

        Omit `replacement` to search only.  Provide `replacement` with `dry_run=True` to
        preview what would change.  Provide `replacement` with `dry_run=False` to apply.

        Regex backreferences in `replacement` use the $!1, $!2, ... syntax when mode is
        'regex' (e.g. replace "(\w+)_old" with "$!1_new").

        :param pattern: text or regex pattern to search for.
        :param replacement: replacement text. Omit or pass None for search-only.
        :param mode: 'literal' for exact text match, 'regex' for Python regex syntax.
        :param relative_path: restrict search to this file or directory. Empty means
            the whole project.
        :param include: glob pattern (relative to project root) to restrict which files
            are searched, e.g. "src/**/*.py".
        :param exclude: glob pattern to exclude files; takes precedence over include.
        :param dry_run: when True and replacement is set, show a preview without
            modifying files. Returns sample diffs and total replacement count.
        :param context_lines: number of context lines to show around each match in
            search and dry-run output. Default 2.
        :param max_preview_files: maximum number of files to show in dry-run output.
        :return: structured result with match locations (search), diffs (dry-run),
            or change manifest (apply).
        """
        ws = self.workspace
        relative_path = relative_path.strip()

        # Gather project units to search.
        if relative_path:
            unit, proj_rel_path = ws.resolve_unit_for_path(relative_path)
            units_and_paths = [(unit, proj_rel_path)]
        else:
            units_and_paths = [(u, "") for u in ws.units]

        # --- Collect all matching files and their match objects ---
        file_matches: dict[str, list] = defaultdict(list)  # labeled_path -> MatchedConsecutiveLines
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
                pattern,
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

        total_matches = sum(len(v) for v in file_matches.values())

        # --- Search-only mode ---
        if replacement is None:
            matches_display: dict[str, list[str]] = {
                path: [m.to_display_string() for m in match_list]
                for path, match_list in file_matches.items()
            }
            return SearchAndReplaceResult(
                mode="search",
                pattern=pattern,
                replacement=None,
                matches=matches_display,
                changes=[],
                total_count=total_matches,
                dry_run_sample_count=0,
            )

        # --- Dry-run mode: count + preview without touching files ---
        if dry_run:
            changes: list[FileChangeRecord] = []
            for labeled_path, match_list in file_matches.items():
                preview = [line for m in match_list[:2] for line in m.to_display_string().splitlines()]
                changes.append(FileChangeRecord(
                    relative_path=labeled_path,
                    count=len(match_list),
                    preview_lines=preview,
                ))
            return SearchAndReplaceResult(
                mode="dry_run",
                pattern=pattern,
                replacement=replacement,
                matches={},
                changes=changes,
                total_count=total_matches,
                dry_run_sample_count=max_preview_files,
            )

        # --- Apply mode: perform replacements ---
        replacer = ContentReplacer(mode=mode, allow_multiple_occurrences=True)
        apply_changes: list[FileChangeRecord] = []

        for labeled_path, _match_list in file_matches.items():
            unit, proj_rel = labeled_path_to_unit_and_proj_rel[labeled_path]
            proj = unit.project
            with self.DiagnosticsContext(self, proj_rel, project=proj):
                with EditedFileContext(proj_rel, self.create_ls_code_editor_for(proj)) as ctx:
                    original = ctx.get_original_content()
                    # Count occurrences before replacing.
                    if mode == "literal":
                        count = original.count(pattern)
                    else:
                        flags = re.MULTILINE | re.DOTALL
                        count = len(re.findall(pattern, original, flags=flags))
                    if count == 0:
                        continue
                    updated = replacer.replace(original, pattern, replacement)
                    ctx.set_updated_content(updated)
            apply_changes.append(FileChangeRecord(relative_path=labeled_path, count=count))

        total_applied = sum(c.count for c in apply_changes)
        return SearchAndReplaceResult(
            mode="apply",
            pattern=pattern,
            replacement=replacement,
            matches={},
            changes=apply_changes,
            total_count=total_applied,
            dry_run_sample_count=0,
        )
