"""
Language server-related tools
"""

import copy
import os
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from serena.symbol import LanguageServerSymbol, LanguageServerSymbolDictGrouper
from serena.tools import (
    SUCCESS_RESULT,
    EditingToolWithDiagnostics,
    Tool,
    ToolMarkerSymbolicEdit,
    ToolMarkerSymbolicRead,
)
from serena.tools.tools_base import DEFAULT_MAX_RESULTS, ToolMarkerOptional
from serena.util.ls_diagnostics import GroupedDiagnostics
from serena.util.text_utils import find_text_coordinates
from solidlsp.ls_types import SymbolKind


class RestartLanguageServerTool(Tool, ToolMarkerOptional):
    """Restarts the language server(s)."""

    def apply(self) -> str:
        """Use this tool only on explicit user request or after confirmation.
        It may be necessary to restart the language server if it hangs.
        """
        self.agent.reset_language_server_manager()
        return SUCCESS_RESULT


class GetSymbolsOverviewTool(Tool, ToolMarkerSymbolicRead):
    """
    Gets an overview of the top-level symbols defined in a given file.
    """

    symbol_dict_grouper = LanguageServerSymbolDictGrouper(["kind"], ["kind"], collapse_singleton=True)

    def apply(self, relative_path: str, depth: int = 1, max_answer_chars: int = -1) -> str:
        """
        Use this tool to get a high-level understanding of the code symbols in a file.
        This should be the first tool to call when you want to understand a new file, unless you already know
        what you are looking for.

        :param relative_path: the relative path to the file to get the overview of
        :param depth: depth up to which descendants of top-level symbols shall be retrieved
            (e.g. 1 retrieves immediate children). Default 1.
        :param max_answer_chars: if the overview is longer than this number of characters,
            no content will be returned. -1 means the default value from the config will be used.
            Don't adjust unless there is really no other way to get the content required for the task.
        :return: a JSON object containing symbols grouped by kind in a compact format.
        """
        result = self.get_symbol_overview(relative_path, depth=depth)

        # capture kind names and depth-0 snapshots before grouping, which mutates the dicts
        kind_names = [d.get("kind", "unknown") for d in result]
        if depth > 0:
            depth_0_result = [d.copy() for d in result]
            for d in depth_0_result:
                d.pop("children", None)

        compact_result = self.symbol_dict_grouper.group(result)
        result_json_str = self._to_json(compact_result)

        # shortened result closures
        def make_kind_counts() -> str:
            return f"Symbol counts by kind:\n{self._to_json(Counter(kind_names))}"

        if depth == 0:
            shortened_results = [make_kind_counts]
        else:

            def make_depth_0_result() -> str:
                compact_depth_0_result = self.symbol_dict_grouper.group(depth_0_result)
                return "Depth 0 overview:\n" + self._to_json(compact_depth_0_result)

            shortened_results = [make_depth_0_result, make_kind_counts]

        return self._limit_length(result_json_str, max_answer_chars, shortened_result_factories=shortened_results)

    def get_symbol_overview(self, relative_path: str, depth: int = 1) -> list[LanguageServerSymbol.OutputDict]:
        """
        :param relative_path: relative path to a source file
        :param depth: the depth up to which descendants shall be retrieved
        :return: a list of symbol dictionaries representing the symbol overview of the file
        """
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        symbol_retriever = self.create_language_server_symbol_retriever_for(proj)

        # The symbol overview is capable of working with both files and directories,
        # but we want to ensure that the user provides a file path.
        file_path = os.path.join(proj.project_root, proj_rel)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File or directory {relative_path} does not exist in the project.")
        if os.path.isdir(file_path):
            raise ValueError(f"Expected a file path, but got a directory path: {relative_path}. ")
        if not symbol_retriever.can_analyze_file(proj_rel):
            raise ValueError(
                f"Cannot extract symbols from file {relative_path}. Active languages: {[l.value for l in self.agent.get_active_lsp_languages()]}"
            )

        symbols = symbol_retriever.get_symbol_overview(proj_rel)[proj_rel]

        def child_inclusion_predicate(s: LanguageServerSymbol) -> bool:
            return not s.is_low_level()

        symbol_dicts = []
        for symbol in symbols:
            symbol_dicts.append(
                symbol.to_dict(
                    name_path=False,
                    name=True,
                    depth=depth,
                    kind=True,
                    relative_path=False,
                    location=False,
                    child_inclusion_predicate=child_inclusion_predicate,
                )
            )
        return symbol_dicts


class FindSymbolTool(Tool, ToolMarkerSymbolicRead):
    """
    Performs a global (or local) search using the language server backend.
    """

    # group children by kind, keeping just the name (the parent's name_path makes it unambiguous);
    # we don't group the top-level result list because many tests rely on it being a flat list of symbol dicts
    symbol_dict_grouper = LanguageServerSymbolDictGrouper([], ["kind"], collapse_singleton=True)

    # noinspection PyDefaultArgument
    def apply(
        self,
        name_path_pattern: str,
        depth: int = 0,
        relative_path: str = "",
        include_body: bool = False,
        include_info: bool = False,
        include_kinds: list[int] = [],  # noqa: B006
        exclude_kinds: list[int] = [],  # noqa: B006
        substring_matching: bool = False,
        max_matches: int = -1,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Retrieves information on all symbols/code entities (classes, methods, etc.) based on the given name path pattern.
        The returned symbol information can be used for edits or further queries.
        Specify `depth > 0` to also retrieve children/descendants (e.g., methods of a class).

        A name path is a path in the symbol tree *within a source file*.
        For example, the method `my_method` defined in class `MyClass` would have the name path `MyClass/my_method`.
        If a symbol is overloaded (e.g., in Java), a 0-based index is appended (e.g. "MyClass/my_method[0]") to
        uniquely identify it.

        To search for a symbol, you provide a name path pattern that is used to match against name paths.
        It can be
         * a simple name (e.g. "method"), which will match any symbol with that name
         * a relative path like "class/method", which will match any symbol with that name path suffix
         * an absolute name path "/class/method" (absolute name path), which requires an exact match of the full name path within the source file.
        Append an index `[i]` to match a specific overload only, e.g. "MyClass/my_method[1]".

        :param name_path_pattern: the name path matching pattern (see above)
        :param depth: depth up to which descendants shall be retrieved (e.g. use 1 to also retrieve immediate children;
            for the case where the symbol is a class, this will return its methods).
            Ignored if `include_body=True`. Default 0.
        :param relative_path: (optional) restrict search to this file or directory. If None, searches entire codebase.
            If a directory is passed, the search will be restricted to the files in that directory.
            If a file is passed, the search will be restricted to that file.
        :param include_body: whether to include the symbol's source code. Use judiciously.
        :param include_info: whether to include additional info (hover-like, typically including docstring and signature),
            about the symbol (ignored if include_body is True). Info is never included for child symbols.
            Note: Depending on the language, this can be slow (e.g., C/C++).
        :param include_kinds: (optional) limits results to the given LSP symbol kinds (integers)
        :param exclude_kinds: (optional) list of LSP symbol kinds (integers) to exclude.
        :param substring_matching: If True, use substring matching for the last element of the pattern, such that
            "Foo/get" would match "Foo/getValue" and "Foo/getData".
        :param max_matches: maximum number of permitted matches. If exceeded, a shortened result is returned
             which allows refining the search. -1 (default) means no limit. Set to 1 if you search for a single symbol.
        :param max_answer_chars: max result length; -1 for default
        :return: symbols (with locations) matching the name.
        """
        if include_body:
            depth = 0  # ignore user-specified depth if include_body is True
        assert max_matches != 0, "max_matches must be > 0 or equal to -1."
        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        ws = self.workspace
        # Determine which project units to search.
        if relative_path:
            unit, proj_rel_path = ws.resolve_unit_for_path(relative_path)
            units_and_paths = [(unit, proj_rel_path)]
        else:
            # Workspace-wide: search all units.
            units_and_paths = [(u, "") for u in ws.units]

        from serena.symbol import LanguageServerSymbol

        # Track (project_id, symbol, retriever) so the same retriever can be reused
        # for the info-fetch step that must use the same instance.
        symbols: list[tuple[str, LanguageServerSymbol, "LanguageServerSymbolRetriever"]] = []
        for unit, search_path in units_and_paths:
            proj = unit.project
            retriever = self.create_language_server_symbol_retriever_for(proj)
            try:
                unit_symbols = retriever.find(
                    name_path_pattern,
                    include_kinds=parsed_include_kinds,
                    exclude_kinds=parsed_exclude_kinds,
                    substring_matching=substring_matching,
                    within_relative_path=search_path,
                )
                for s in unit_symbols:
                    symbols.append((unit.project_id, s, retriever))
            except Exception as exc:
                # Skip units whose language server cannot service this query.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "find_symbol: skipping unit %s due to error: %s", unit.project_id, exc
                )

        n_matches = len(symbols)

        def create_short_result_relative_path_to_name_paths() -> str:
            relative_path_to_name_paths: defaultdict[str, list[str]] = defaultdict(list)
            for proj_id, s, _ in symbols:
                labeled = ws.label(proj_id, s.location.relative_path or "unknown")
                relative_path_to_name_paths[labeled].append(s.get_name_path())
            return f"Shortened result:\n{self._to_json(relative_path_to_name_paths)}"

        if 0 < max_matches < n_matches:
            return f"Matched {n_matches}>{max_matches=} symbols.\n" + create_short_result_relative_path_to_name_paths()

        symbol_dicts = []
        for proj_id, s, _ in symbols:
            d = s.to_dict(
                kind=True,
                name_path=True,
                name=False,
                relative_path=True,
                body_location=True,
                depth=depth,
                body=include_body,
                children_name=True,
                children_name_path=False,
            )
            # Label relative_path with project identifier in multi-project workspaces.
            if ws.is_multi_project and "relative_path" in d and d["relative_path"]:
                d = dict(d)
                d["relative_path"] = ws.label(proj_id, d["relative_path"])
            symbol_dicts.append(d)
        if not include_body and include_info:
            # Group by retriever (same-instance required by request_info_for_symbol_batch).
            from collections import defaultdict as _dd
            by_retriever: dict[int, list[tuple[int, LanguageServerSymbol, "LanguageServerSymbolRetriever"]]] = _dd(list)
            for idx, (proj_id, s, retr) in enumerate(symbols):
                by_retriever[id(retr)].append((idx, s, retr))
            for _, indexed in by_retriever.items():
                retriever = indexed[0][2]
                ls_symbols = [s for _, s, _ in indexed]
                info_by_symbol = retriever.request_info_for_symbol_batch(ls_symbols)
                for orig_idx, s, _ in indexed:
                    if symbol_info := info_by_symbol.get(s):
                        symbol_dicts[orig_idx]["info"] = symbol_info  # type: ignore[typeddict-unknown-key]

        grouped_symbol_dicts = self.symbol_dict_grouper.group(symbol_dicts)
        result = self._to_json(grouped_symbol_dicts)
        return self._limit_length(result, max_answer_chars, shortened_result_factories=[create_short_result_relative_path_to_name_paths])


class FindReferencingSymbolsTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds symbols that reference the given symbol using the language server backend
    """

    symbol_dict_grouper = LanguageServerSymbolDictGrouper(["relative_path", "kind"], ["kind"], collapse_singleton=True)

    # noinspection PyDefaultArgument
    def apply(
        self,
        name_path: str,
        relative_path: str,
        include_kinds: list[int] = [],  # noqa: B006
        exclude_kinds: list[int] = [],  # noqa: B006
        max_answer_chars: int = -1,
    ) -> str:
        """
        Finds references to the symbol at the given `name_path`. The result will contain metadata about the referencing symbols
        as well as a short code snippet around the reference.

        :param name_path: name path of the symbol
        :param relative_path: the relative path to the file containing the symbol for which to find references.
            Note: for external dependencies, this must be an identifier starting with `<ext` that you have received
            earlier (don't try to guess!).
        :param include_kinds: (optional) limits results to the given LSP symbol kinds (integers)
        :param exclude_kinds: optional list of LSP symbol kinds (integers) to exclude.
        :param max_answer_chars: max result length; -1 for default
        :return: a list of JSON objects with the symbols referencing the requested symbol
        """
        include_body = False  # It is probably never a good idea to include the body of the referencing symbols
        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        references_in_symbols = symbol_retriever.find_referencing_symbols(
            name_path,
            relative_file_path=proj_rel,
            include_body=include_body,
            include_kinds=parsed_include_kinds,
            exclude_kinds=parsed_exclude_kinds,
        )

        reference_dicts = []
        for ref in references_in_symbols:
            ref_dict_orig = ref.symbol.to_dict(kind=True, relative_path=True, depth=0, body=include_body, body_location=True)
            ref_dict = dict(ref_dict_orig)
            if not include_body:
                ref_relative_path = ref.symbol.location.relative_path
                assert ref_relative_path is not None, f"Referencing symbol {ref.symbol.name} has no relative path, this is likely a bug."
                content_around_ref = unit.project.retrieve_content_around_line(
                    relative_file_path=ref_relative_path, line=ref.line, context_lines_before=1, context_lines_after=1
                )
                ref_dict["content_around_reference"] = content_around_ref.to_display_string()
            reference_dicts.append(ref_dict)

        # capture lightweight reference data before grouping
        ref_summaries = []
        for ref, d in zip(references_in_symbols, reference_dicts, strict=True):
            ref_summaries.append(
                {
                    "name_path": d.get("name_path"),
                    "kind": d.get("kind"),
                    "relative_path": d.get("relative_path"),
                    "reference_line": ref.line,
                }
            )

        result = self.symbol_dict_grouper.group(reference_dicts)  # type: ignore

        # shortened result closures, from least to most aggressive shortening
        def make_refs_without_context() -> str:
            """References with name_path and reference line, without surrounding code lines"""
            grouped = self.symbol_dict_grouper.group(copy.deepcopy(ref_summaries))  # type: ignore
            return f"References without surrounding lines:\n{self._to_json(grouped)}"

        def make_per_file_counts() -> str:
            counts = Counter(str(r["relative_path"]) for r in ref_summaries)
            return f"Reference counts per file:\n{self._to_json(counts)}"

        def make_summary() -> str:
            return f"Found {len(ref_summaries)} references."

        shortened_results = [make_refs_without_context, make_per_file_counts, make_summary]

        result_json = self._to_json(result)
        return self._limit_length(result_json, max_answer_chars, shortened_result_factories=shortened_results)


class FindImplementationsTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds symbols that implement the given symbol using the language server backend.
    """

    # noinspection PyDefaultArgument
    def apply(
        self,
        name_path: str,
        relative_path: str,
        include_info: bool = False,
        include_kinds: list[int] = [],  # noqa: B006
        exclude_kinds: list[int] = [],  # noqa: B006
        max_answer_chars: int = -1,
    ) -> str:
        """
        Finds implementations of the symbol at the given `name_path`.

        :param name_path: the symbol's name path
        :param relative_path: the relative path to the file containing the symbol for which to find implementations.
            Note that here you can't pass a directory but must pass a file.
        :param include_info: whether to include additional info (hover-like, typically including docstring and signature),
            about the implementing symbols.
        :param include_kinds: (optional) limits results to the given LSP symbol kinds (integers)
        :param exclude_kinds: (optional) list of LSP symbol kinds (integers) to exclude.
        :param max_answer_chars: max result length; -1 for default
        :return: a list of JSON objects with the symbols implementing the requested symbol
        """
        include_body = False
        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        implementing_symbols = symbol_retriever.find_implementing_symbols(
            name_path,
            relative_file_path=proj_rel,
            include_body=include_body,
            include_kinds=parsed_include_kinds,
            exclude_kinds=parsed_exclude_kinds,
        )

        symbol_dicts = [
            dict(s.to_dict(kind=True, relative_path=True, depth=0, body=include_body, body_location=True)) for s in implementing_symbols
        ]
        if include_info:
            info_by_symbol = symbol_retriever.request_info_for_symbol_batch(implementing_symbols)
            for s, s_dict in zip(implementing_symbols, symbol_dicts, strict=True):
                if symbol_info := info_by_symbol.get(s):
                    s_dict["info"] = symbol_info
                    s_dict.pop("name", None)  # name is included in the info

        result = self._to_json(symbol_dicts)
        return self._limit_length(result, max_answer_chars)


class FindDeclarationTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds the declaration/definition of a symbol
    """

    def apply(
        self,
        relative_path: str,
        regex: str,
        containing_symbol_name_path: str | None = None,
        include_body: bool = False,
        include_info: bool = False,
    ) -> str:
        r"""
        Finds the declaration of a symbol.

        :param relative_path: the relative path to the source file containing the symbol for which to find the declaration.
        :param regex: a regular expression with one group, where the group matches the symbol for which to perform the lookup.
            For example, to find the declaration of the `process` method in a call like `obj.process()`,
            pass an expression like "obj\.(process)\(process_input_arg=37\)".
            Prefer regexes with sufficiently large context around the group to render the match unambiguous.
            Uses Python syntax with MULTILINE and DOTALL flags enabled.
        :param containing_symbol_name_path: optional name path of a containing symbol whose body shall be searched instead of the full file.
        :param include_body: whether to include the symbol's body in the result. Default False.
        :param include_info: whether to include additional info (hover-like). Default False.
        """
        relative_path = self._sanitize_input_param(relative_path)
        regex = self._sanitize_input_param(regex)
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        # find relevant location for lookup
        editor = self.create_ls_code_editor_for(unit.project)
        if not containing_symbol_name_path:
            content = editor.read_file(proj_rel)
            coords = find_text_coordinates(content, regex, require_unique=True)
            assert coords is not None
        else:
            symbol = symbol_retriever.find_unique(name_path_pattern=containing_symbol_name_path, within_relative_path=proj_rel)
            body_line_numers = symbol.get_body_line_numbers_or_raise()
            content = editor.read_file(proj_rel, lines=body_line_numers)
            coords = find_text_coordinates(content, regex, require_unique=True)
            assert coords is not None
            coords.line += body_line_numers[0]

        # retrieve declaration
        defining_symbol = symbol_retriever.find_declaration(
            relative_file_path=proj_rel,
            line=coords.line,
            column=coords.col,
            include_body=include_body,
        )
        if defining_symbol is None:
            raise ValueError(
                f"No symbol declaration found at the location of the regex match. Location: {relative_path}:{coords.line}:{coords.col}."
            )

        # create output
        symbol_dict = self._defining_symbol_to_result_dict(
            symbol_retriever,
            defining_symbol,
            include_body,
            include_info,
        )
        result = self._to_json(symbol_dict)
        return result

    @staticmethod
    def _defining_symbol_to_result_dict(
        symbol_retriever: Any,
        defining_symbol: LanguageServerSymbol,
        include_body: bool,
        include_info: bool,
    ) -> dict[str, Any]:
        symbol_dict = dict(defining_symbol.to_dict(kind=True, relative_path=True, depth=0, body=include_body, body_location=True))
        if not include_body and include_info:
            if symbol_info := symbol_retriever.request_info_for_symbol(defining_symbol):
                symbol_dict["info"] = symbol_info
                symbol_dict.pop("name", None)
        return symbol_dict


class GetDiagnosticsForFileTool(Tool, ToolMarkerSymbolicRead):
    """
    Gets diagnostics for a file, optionally restricted to a line range, grouped by file, severity, and containing symbol.
    """

    FILE_LEVEL_DIAGNOSTIC_BUCKET = "<file>"

    def apply(
        self,
        relative_path: str,
        start_line: int = 0,
        end_line: int = -1,
        min_severity: int = 4,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Gets diagnostics for a file. Diagnostics are grouped as `relative_path -> severity -> name_path -> diagnostics_results`.
        If a diagnostic cannot be mapped to a symbol, it is grouped under the special name path `<file>`.

        :param relative_path: the relative path to the file to inspect.
        :param start_line: the first 0-based line to include. Defaults to 0.
        :param end_line: the last 0-based line to include. Defaults to -1, which means until the end of the file.
        :param min_severity: minimum LSP severity to include, where 1=Error, 2=Warning, 3=Information, 4=Hint.
            Diagnostics with lower-or-equal numeric severity are returned.
        :param max_answer_chars: max result length; -1 for default
        :return: grouped diagnostics for the requested file.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)
        diagnostics = symbol_retriever.get_file_diagnostics(
            relative_file_path=proj_rel,
            start_line=start_line,
            end_line=end_line,
            min_severity=min_severity,
        )

        ws = self.workspace
        grouped_diagnostics = GroupedDiagnostics()
        for diagnostic in diagnostics:
            diag_range = diagnostic["range"]["start"]
            name_path_label = self.FILE_LEVEL_DIAGNOSTIC_BUCKET
            owner_symbol = symbol_retriever.find_diagnostic_owner_symbol(
                relative_file_path=proj_rel,
                line=diag_range["line"],
                column=diag_range["character"],
            )
            if owner_symbol is not None:
                name_path_label = owner_symbol.get_name_path()
            labeled_path = ws.label(unit.project_id, proj_rel)
            grouped_diagnostics.add(labeled_path, name_path_label, diagnostic)

        result = self._to_json(grouped_diagnostics.get_dict())
        return self._limit_length(result, max_answer_chars)


class GetDiagnosticsForSymbolTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Gets diagnostics for a symbol and, optionally, for symbols that reference it.
    """

    def apply(
        self,
        name_path: str,
        reference_file: str = "",
        check_symbol_references: bool = False,
        min_severity: int = 4,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Gets diagnostics for the specified symbol. When `check_symbol_references` is true, diagnostics for all
        referencing symbols are also included. The result is grouped as
        `relative_path -> severity -> name_path -> diagnostics_results`.

        :param name_path: the name path of the symbol to inspect.
        :param reference_file: optional file path used to disambiguate the symbol search.
        :param check_symbol_references: whether to additionally collect diagnostics for symbols that reference the symbol.
        :param min_severity: minimum LSP severity to include, where 1=Error, 2=Warning, 3=Information, 4=Hint.
            Diagnostics with lower-or-equal numeric severity are returned.
        :param max_answer_chars: max result length; -1 for default
        :return: grouped diagnostics for the requested symbol and, optionally, its referencing symbols.
        """
        # Route the reference file to the owning project unit.
        if reference_file:
            unit, proj_ref_file = self.resolve_project(reference_file)
        else:
            unit = self.workspace.primary_unit
            proj_ref_file = reference_file
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)
        ws = self.workspace
        diagnostics_by_symbol = symbol_retriever.get_symbol_diagnostics(
            name_path=name_path,
            reference_file=proj_ref_file or None,
            check_symbol_references=check_symbol_references,
            min_severity=min_severity,
        )

        grouped_diagnostics = GroupedDiagnostics()
        for symbol, diagnostics in diagnostics_by_symbol.items():
            relative_path = symbol.relative_path
            if relative_path is None:
                continue
            labeled_path = ws.label(unit.project_id, relative_path)
            symbol_name_path = symbol.get_name_path()
            for diagnostic in diagnostics:
                grouped_diagnostics.add(labeled_path, symbol_name_path, diagnostic)

        result = self._to_json(grouped_diagnostics.get_dict())
        return self._limit_length(result, max_answer_chars)


class ReplaceSymbolBodyTool(EditingToolWithDiagnostics):
    """
    Replaces the full definition of a symbol using the language server backend.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        body: str,
    ) -> str:
        r"""
        Replaces the body of the given symbol.

        IMPORTANT: Only replace symbol bodies if you have previously made a retrieval with include_body=True and thus know what
        constitutes the body!

        :param name_path: name path of the symbol whose body to replace
        :param relative_path: the relative path to the file containing the symbol
        :param body: the new symbol body. The symbol body is the definition of a symbol
            in the programming language, including e.g. the signature line for functions.
            Depending on the language, it may or may not include a preceding docstring or other preceding annotations.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        with self.DiagnosticsContext(self, proj_rel, project=unit.project) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(unit.project)
            code_editor.replace_body(name_path, relative_file_path=proj_rel, body=body)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class InsertAfterSymbolTool(EditingToolWithDiagnostics):
    """
    Inserts content after the end of the definition of a given symbol.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        body: str,
    ) -> str:
        """
        Use this to insert code after a class/method/function definition.
        Don't use to insert after assignments (constants, fields).

        :param name_path: name path of the symbol after which to insert content
        :param relative_path: the relative path to the file containing the symbol
        :param body: the body/content to be inserted. The inserted code shall begin with the next line after
            the symbol.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        with self.DiagnosticsContext(self, proj_rel, project=unit.project) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(unit.project)
            code_editor.insert_after_symbol(name_path, relative_file_path=proj_rel, body=body)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class InsertBeforeSymbolTool(EditingToolWithDiagnostics):
    """
    Inserts content before the beginning of the definition of a given symbol.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        body: str,
    ) -> str:
        """
        Inserts the given content before the beginning of the definition of the given symbol (via the symbol's location).
        A typical use case is to insert a new class, function, method, field or variable assignment; or
        a new import statement before the first symbol in the file.

        :param name_path: name path of the symbol before which to insert content
        :param relative_path: the relative path to the file containing the symbol
        :param body: the body/content to be inserted before the line in which the referenced symbol is defined
        """
        unit, proj_rel = self.resolve_project(relative_path)
        with self.DiagnosticsContext(self, proj_rel, project=unit.project) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(unit.project)
            code_editor.insert_before_symbol(name_path, relative_file_path=proj_rel, body=body)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class RenameSymbolTool(Tool, ToolMarkerSymbolicEdit):
    """
    Renames a symbol throughout the codebase using language server refactoring capabilities.
    For JB, we use a separate tool.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        new_name: str,
    ) -> str:
        """
        Renames the symbol with the given `name_path` to `new_name` throughout the entire codebase.
        Note: for languages with method overloading, like Java, name_path may have to include a method's
        signature to uniquely identify a method.

        :param name_path: name path of the symbol to rename
        :param relative_path: the relative path to the file containing the symbol to rename
        :param new_name: the new name for the symbol
        :return: result summary indicating success or failure
        """
        unit, proj_rel = self.resolve_project(relative_path)
        code_editor = self.create_ls_code_editor_for(unit.project)
        status_message = code_editor.rename_symbol(name_path, relative_path=proj_rel, new_name=new_name)
        return status_message


class SafeDeleteSymbol(Tool, ToolMarkerSymbolicEdit):
    def apply(
        self,
        name_path_pattern: str,
        relative_path: str,
    ) -> str:
        """
        Deletes the symbol if it is safe to do so (i.e., if there are no references to it)
        or returns a list of references to it.

        :param name_path_pattern: name path of the symbol to delete
        :param relative_path: the relative path to the file containing the symbol to delete
        """
        unit, proj_rel = self.resolve_project(relative_path)
        ls_symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)
        symbol = ls_symbol_retriever.find_unique(name_path_pattern, substring_matching=False, within_relative_path=proj_rel)
        symbol_rel_path = symbol.relative_path
        assert symbol_rel_path is not None, f"Symbol {name_path_pattern} has no relative path, this is likely a bug."
        assert symbol_rel_path == proj_rel, f"Symbol {name_path_pattern} is not in the expected relative path {proj_rel}."
        symbol_name_path = symbol.get_name_path()

        symbol_line = symbol.line
        symbol_col = symbol.column
        assert symbol_line is not None and symbol_col is not None, (
            f"Symbol {name_path_pattern} has no identifier position, this is likely a bug."
        )
        lang_server = ls_symbol_retriever.get_language_server(symbol_rel_path)
        references_locations = lang_server.request_references(symbol_rel_path, symbol_line, symbol_col)
        file_to_lines: dict[str, list[int]] = defaultdict(list)
        if references_locations:
            for ref_loc in references_locations:
                ref_relative_path = ref_loc.get("relativePath")
                if ref_relative_path is None:
                    continue
                file_to_lines[ref_relative_path].append(ref_loc["range"]["start"]["line"])
        if file_to_lines:
            return f"Cannot delete, the symbol {symbol_name_path} is referenced in: {self._to_json(file_to_lines)}"
        code_editor = self.create_ls_code_editor()
        code_editor.delete_symbol(symbol_name_path, relative_file_path=symbol_rel_path)
        return SUCCESS_RESULT
