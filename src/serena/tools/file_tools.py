"""
File and file system-related tools, specifically for
  * listing directory contents
  * reading files
  * creating files
  * editing at the file level
"""

import os
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from serena.tools import SUCCESS_RESULT, EditedFileContext, EditingToolWithDiagnostics, Tool, ToolMarkerOptional
from serena.util.file_system import scan_directory
from serena.util.text_utils import ContentReplacer, search_files


class ReadFileTool(Tool):
    """
    Reads a file within the project directory.
    """

    def apply(self, relative_path: str, start_line: int = 0, end_line: int | None = None, max_answer_chars: int = -1) -> str:
        """
        Reads the given file or a chunk of it.

        :param relative_path: the relative path to the file to read
        :param start_line: the 0-based index of the first line to be retrieved.
        :param end_line: the 0-based index of the last line to be retrieved (inclusive). If None, read until the end of the file.
        :param max_answer_chars: if the file (chunk) is longer than this number of characters,
            no content will be returned. Don't adjust unless there is really no other way to get the content
            required for the task.
        :return: the full text of the file at the given relative path
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        proj.validate_relative_path(proj_rel, require_not_ignored=True)

        result = proj.read_file(proj_rel)
        result_lines = result.splitlines()
        if end_line is None:
            result_lines = result_lines[start_line:]
        else:
            result_lines = result_lines[start_line : end_line + 1]
        result = "\n".join(result_lines)

        return self._limit_length(result, max_answer_chars)


class CreateTextFileTool(EditingToolWithDiagnostics):
    """
    Creates/overwrites a file in the project directory.
    """

    def apply(self, relative_path: str, content: str) -> str:
        """
        Write a new file or overwrite an existing file.

        :param relative_path: the relative path to the file to create
        :param content: the (appropriately encoded) content to write to the file
        :return: a message indicating success or failure
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        with self.DiagnosticsContext(self, relative_path, project=proj) as diagnostics_context:
            abs_path = (Path(proj.project_root) / proj_rel).resolve()
            will_overwrite_existing = abs_path.exists()

            if will_overwrite_existing:
                proj.validate_relative_path(proj_rel, require_not_ignored=True)
            else:
                assert abs_path.is_relative_to(proj.project_root), (
                    f"Cannot create file outside of the project directory, got {relative_path=}"
                )

            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(
                content,
                encoding=proj.project_config.encoding,
                newline=proj.line_ending.newline_str,
            )
            answer = f"File created: {relative_path}."
            if will_overwrite_existing:
                answer += " Overwrote existing file."

            return diagnostics_context.format_result(answer)


class ListDirTool(Tool):
    """
    Lists files and directories in the given directory (optionally with recursion).
    """

    def apply(self, relative_path: str, recursive: bool, skip_ignored_files: bool = False, max_answer_chars: int = -1) -> str:
        """
        Lists files and directories in the given directory (optionally with recursion).

        :param relative_path: the relative path to the directory to list; pass "." to scan the project root
        :param recursive: whether to scan subdirectories recursively
        :param skip_ignored_files: whether to skip files and directories that are ignored
        :param max_answer_chars: if the output is longer than this number of characters,
            no content will be returned. -1 means the default value from the config will be used.
            Don't adjust unless there is really no other way to get the content required for the task.
        :return: a JSON object with the names of directories and files within the given directory
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project

        if not proj.relative_path_exists(proj_rel):
            error_info = {
                "error": f"Directory not found: {relative_path}",
                "project_root": proj.project_root,
                "hint": "Check if the path is correct relative to the workspace root",
            }
            return self._to_json(error_info)

        proj.validate_relative_path(proj_rel, require_not_ignored=skip_ignored_files)

        dirs, files = scan_directory(
            os.path.join(proj.project_root, proj_rel),
            relative_to=proj.project_root,
            recursive=recursive,
            is_ignored_dir=proj.is_ignored_path if skip_ignored_files else None,
            is_ignored_file=proj.is_ignored_path if skip_ignored_files else None,
        )

        ws = self.workspace
        labeled_dirs = [ws.label(unit.project_id, d) for d in dirs]
        labeled_files = [ws.label(unit.project_id, f) for f in files]
        result = self._to_json({"dirs": labeled_dirs, "files": labeled_files})
        return self._limit_length(result, max_answer_chars)


class FindFileTool(Tool):
    """
    Finds files in the given relative paths
    """

    def apply(self, file_mask: str, relative_path: str) -> str:
        """
        Finds non-gitignored files matching the given file mask within the given relative path

        :param file_mask: the filename or file mask (using the wildcards * or ?) to search for
        :param relative_path: the relative path to the directory to search in; pass "." to scan the project root
        :return: a JSON object with the list of matching files
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        proj.validate_relative_path(proj_rel, require_not_ignored=True)

        dir_to_scan = os.path.join(proj.project_root, proj_rel)

        def is_ignored_file(abs_path: str) -> bool:
            if proj.is_ignored_path(abs_path):
                return True
            filename = os.path.basename(abs_path)
            return not fnmatch(filename, file_mask)

        _dirs, files = scan_directory(
            path=dir_to_scan,
            recursive=True,
            is_ignored_dir=proj.is_ignored_path,
            is_ignored_file=is_ignored_file,
            relative_to=proj.project_root,
        )

        ws = self.workspace
        labeled_files = [ws.label(unit.project_id, f) for f in files]
        result = self._to_json({"files": labeled_files})
        return result


class ReplaceContentTool(EditingToolWithDiagnostics):
    """
    Replaces content in a file (optionally using regular expressions).
    """

    def apply(
        self,
        relative_path: str,
        needle: str,
        repl: str,
        mode: Literal["literal", "regex"],
        allow_multiple_occurrences: bool = False,
    ) -> str:
        r"""
        Replaces one or more occurrences of a given pattern in a file with new content.

        This is the preferred way to replace content in a file whenever the symbol-level
        tools are not appropriate.

        VERY IMPORTANT: The "regex" mode allows very large sections of code to be replaced without fully quoting them!
        Use a regex of the form "beginning.*?end-of-text-to-be-replaced" to be faster and more economical!
        ALWAYS try to use wildcards to avoid specifying the exact content to be replaced,
        especially if it spans several lines. Note that you cannot make mistakes, because if the regex should match
        multiple occurrences while you disabled `allow_multiple_occurrences`, an error will be returned, and you can retry
        with a revised regex.
        Therefore, using regex mode with suitable wildcards is usually the best choice!

        :param relative_path: the relative path to the file
        :param needle: the string or regex pattern to search for.
            If `mode` is "literal", this string will be matched exactly.
            If `mode` is "regex", this string will be treated as a regular expression (syntax of Python's `re` module,
            with flags DOTALL and MULTILINE enabled).
        :param repl: the replacement string (verbatim).
            If mode is "regex", the string can contain backreferences to matched groups in the needle regex,
            specified using the syntax $!1, $!2, etc. for groups 1, 2, etc.
        :param mode: either "literal" or "regex", specifying how the `needle` parameter is to be interpreted.
        :param allow_multiple_occurrences: whether to allow matching and replacing multiple occurrences.
            If false and multiple occurrences are found, an error will be returned
        """
        return self.replace_content(
            relative_path, needle, repl, mode=mode, allow_multiple_occurrences=allow_multiple_occurrences, require_not_ignored=True
        )

    def replace_content(
        self,
        relative_path: str,
        needle: str,
        repl: str,
        mode: Literal["literal", "regex"],
        allow_multiple_occurrences: bool = False,
        require_not_ignored: bool = True,
    ) -> str:
        """
        Performs the replacement, with additional options not exposed in the tool.
        This function can be used internally by other tools.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        with self.DiagnosticsContext(self, proj_rel, project=proj) as diagnostics_context:
            proj.validate_relative_path(proj_rel, require_not_ignored=require_not_ignored)
            with EditedFileContext(proj_rel, self.create_ls_code_editor_for(proj)) as context:
                original_content = context.get_original_content()
                replacer = ContentReplacer(mode=mode, allow_multiple_occurrences=allow_multiple_occurrences)
                updated_content = replacer.replace(original_content, needle, repl)
                context.set_updated_content(updated_content)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class DeleteLinesTool(EditingToolWithDiagnostics, ToolMarkerOptional):
    """
    Deletes a range of lines within a file.
    """

    def apply(
        self,
        relative_path: str,
        start_line: int,
        end_line: int,
    ) -> str:
        """
        Deletes the given lines in the file.
        Requires that the same range of lines was previously read using the `read_file` tool to verify correctness
        of the operation.

        :param relative_path: the relative path to the file
        :param start_line: the 0-based index of the first line to be deleted
        :param end_line: the 0-based index of the last line to be deleted
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        with self.DiagnosticsContext(self, proj_rel, project=proj) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(proj)
            code_editor.delete_lines(proj_rel, start_line, end_line)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class ReplaceLinesTool(EditingToolWithDiagnostics, ToolMarkerOptional):
    """
    Replaces a range of lines within a file with new content.
    """

    def apply(
        self,
        relative_path: str,
        start_line: int,
        end_line: int,
        content: str,
    ) -> str:
        """
        Replaces the given range of lines in the given file.
        Requires that the same range of lines was previously read using the `read_file` tool to verify correctness
        of the operation.

        :param relative_path: the relative path to the file
        :param start_line: the 0-based index of the first line to be deleted
        :param end_line: the 0-based index of the last line to be deleted
        :param content: the content to insert
        """
        # normalizing the replacement content
        if not content.endswith("\n"):
            content += "\n"

        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        with self.DiagnosticsContext(self, proj_rel, project=proj) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(proj)
            code_editor.delete_lines(proj_rel, start_line, end_line)
            code_editor.insert_at_line(proj_rel, start_line, content)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class InsertAtLineTool(EditingToolWithDiagnostics, ToolMarkerOptional):
    """
    Inserts content at a given line in a file.
    """

    def apply(
        self,
        relative_path: str,
        line: int,
        content: str,
    ) -> str:
        """
        Inserts the given content at the given line in the file, pushing existing content of the line down.
        In general, symbolic insert operations like insert_after_symbol or insert_before_symbol should be preferred if you know which
        symbol you are looking for.
        However, this can also be useful for small targeted edits of the body of a longer symbol (without replacing the entire body).

        :param relative_path: the relative path to the file
        :param line: the 0-based index of the line to insert content at
        :param content: the content to be inserted
        """
        # normalizing the inserted content
        if not content.endswith("\n"):
            content += "\n"

        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        with self.DiagnosticsContext(self, proj_rel, project=proj) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(proj)
            code_editor.insert_at_line(proj_rel, line, content)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class SearchForPatternTool(Tool):
    """
    Performs a search for a pattern in the project.
    """

    def apply(
        self,
        substring_pattern: str,
        context_lines_before: int = 0,
        context_lines_after: int = 0,
        paths_include_glob: str = "",
        paths_exclude_glob: str = "",
        relative_path: str = "",
        restrict_search_to_code_files: bool = False,
        multiline: bool = True,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Searches for a regex pattern across project files, returning whole matched lines (plus optional context).
        Prefer symbolic operations if you know which symbols you are looking for!

        :param substring_pattern: regular expression to search for.
        :param context_lines_before: number of context lines to include before each match.
        :param context_lines_after: number of context lines to include after each match.
        :param paths_include_glob: optional glob (relative to project root, e.g. ``"src/**/*.ts"``) restricting which files are searched.
        :param paths_exclude_glob: optional glob to exclude files; takes precedence over `paths_include_glob`.
        :param relative_path: restricts the search to this file or subdirectory of the project root
        :param restrict_search_to_code_files: whether to search only files containing analyzable code symbols
            (useful when looking for class/method definitions); otherwise also search non-code files.
        :param multiline: whether to apply multi-line matching (default: True), enabling the flags re.DOTALL and re.MULTILINE
        :param max_answer_chars: Maximum number of characters in the response. -1 uses the configured default.
            Prefer narrowing the search with relative_path or paths_include_glob before raising this limit.
        :return: A mapping from file paths to matched consecutive lines (0-based line numbers).
        """
        relative_path = relative_path.strip()
        ws = self.workspace

        # Determine which project units to search.
        if relative_path:
            # Route to the owning project unit.
            unit, proj_rel_path = ws.resolve_unit_for_path(relative_path)
            units_and_paths = [(unit, proj_rel_path)]
        else:
            # Workspace-wide: search all units.
            units_and_paths = [(u, "") for u in ws.units]

        file_to_matches: dict[str, list[str]] = defaultdict(list)
        match_lines_by_file: dict[str, list[int]] = defaultdict(list)
        total_matches = 0

        for unit, search_path in units_and_paths:
            proj = unit.project
            if search_path:
                proj.validate_relative_path(search_path, require_not_ignored=True)
            abs_path = os.path.join(proj.project_root, search_path) if search_path else proj.project_root

            if not os.path.exists(abs_path):
                continue

            if restrict_search_to_code_files:
                unit_matches = proj.search_source_files_for_pattern(
                    pattern=substring_pattern,
                    relative_path=search_path,
                    context_lines_before=context_lines_before,
                    context_lines_after=context_lines_after,
                    paths_include_glob=paths_include_glob.strip(),
                    paths_exclude_glob=paths_exclude_glob.strip(),
                    multiline=multiline,
                )
            else:
                if os.path.isfile(abs_path):
                    rel_paths_to_search = [search_path]
                else:
                    _dirs, rel_paths_to_search = scan_directory(
                        path=abs_path,
                        recursive=True,
                        is_ignored_dir=proj.is_ignored_path,
                        is_ignored_file=proj.is_ignored_path,
                        relative_to=proj.project_root,
                    )
                unit_matches = search_files(
                    rel_paths_to_search,
                    substring_pattern,
                    context_lines_before=context_lines_before,
                    context_lines_after=context_lines_after,
                    file_reader=proj.read_file,
                    root_path=proj.project_root,
                    paths_include_glob=paths_include_glob,
                    paths_exclude_glob=paths_exclude_glob,
                    multiline=multiline,
                )

            for match in unit_matches:
                assert match.source_file_path is not None
                # Label the path with the project identifier in multi-project workspaces.
                labeled_path = ws.label(unit.project_id, match.source_file_path)
                file_to_matches[labeled_path].append(match.to_display_string())
                match_lines_by_file[labeled_path].append(match.matched_lines[0].line_number)
                total_matches += 1

        # shortened result closures, from least to most aggressive shortening
        def make_lines_only() -> str:
            """Match locations without surrounding context"""
            return f"Match lines per file:\n{self._to_json(match_lines_by_file)}"

        def make_per_file_counts() -> str:
            counts = {path: len(lines) for path, lines in match_lines_by_file.items()}
            return f"Match counts per file:\n{self._to_json(counts)}"

        def make_summary() -> str:
            return f"Found {total_matches} matches in {len(match_lines_by_file)} files."

        result = self._to_json(file_to_matches)
        return self._limit_length(
            result, max_answer_chars, shortened_result_factories=[make_lines_only, make_per_file_counts, make_summary]
        )
