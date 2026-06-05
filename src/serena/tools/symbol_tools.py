"""
LSP-backed symbol tools for the Serena MCP toolbox.

Renamed and restructured for agent clarity:
  - get_symbols_overview    -> symbols_overview
  - find_referencing_symbols -> find_usages
  - find_declaration        -> find_definition
  - get_diagnostics_for_file -> check_errors
  - get_diagnostics_for_symbol -> check_symbol_errors
  - replace_symbol_body     -> rewrite_symbol
  - insert_after_symbol +
    insert_before_symbol    -> inject_code (merged; position=before|after)
  - safe_delete_symbol      -> delete_symbol
  - find_symbol, find_implementations, rename_symbol: kept with light cleanup
"""

import copy
import os
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any, Literal

import re as _re

from serena.symbol import LanguageServerSymbol, LanguageServerSymbolDictGrouper, _compute_overload_distinct_params
from serena.tools.tools_base import (
    SUCCESS_RESULT,
    EditingToolWithDiagnostics,
    Tool,
    ToolMarkerOptional,
    ToolMarkerSymbolicEdit,
    ToolMarkerSymbolicRead,
)
from serena.tools.tools_base import DEFAULT_MAX_RESULTS
from serena.util.ls_diagnostics import GroupedDiagnostics
from serena.util.text_utils import TextCoords, find_all_text_coordinates
from solidlsp.ls_types import SymbolKind


class RestartLanguageServerTool(Tool, ToolMarkerOptional):
    """Restarts the language server(s). Use only when the server appears hung."""

    def apply(self) -> str:
        """
        Restart all language servers for the active project.

        Use this tool only on explicit user request or when language server responses
        are clearly stale or non-responsive.
        """
        self.agent.reset_language_server_manager()
        return SUCCESS_RESULT


class SymbolsOverviewTool(Tool, ToolMarkerSymbolicRead):
    """
    Returns the top-level symbol tree of a file: classes, functions, constants,
    and their immediate children grouped by kind.

    Call this first when exploring a new file to understand its structure before
    diving into specific symbols.
    """

    symbol_dict_grouper = LanguageServerSymbolDictGrouper(["kind"], ["kind"], collapse_singleton=True)

    def apply(self, relative_path: str, depth: int = 1, max_answer_chars: int = -1) -> str:
        """
        Get a compact symbol overview of a file to understand its structure at a glance.

        :param relative_path: the file to inspect.
        :param depth: how many levels of children to include. 1 = immediate children
            (e.g. methods of a class). 0 = top-level symbols only. Default 1.
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: symbols grouped by kind in a compact JSON format.
        """
        result = self._get_symbol_overview(relative_path, depth=depth)

        kind_names = [d.get("kind", "unknown") for d in result]
        if depth > 0:
            depth_0_result = [d.copy() for d in result]
            for d in depth_0_result:
                d.pop("children", None)

        compact_result = self.symbol_dict_grouper.group(result)
        result_json_str = self._to_json(compact_result)

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

    def _get_symbol_overview(self, relative_path: str, depth: int = 1) -> list[LanguageServerSymbol.OutputDict]:
        unit, proj_rel = self.resolve_project(relative_path)
        proj = unit.project
        symbol_retriever = self.create_language_server_symbol_retriever_for(proj)

        file_path = os.path.join(proj.project_root, proj_rel)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File or directory {relative_path} does not exist in the project.")
        if os.path.isdir(file_path):
            raise ValueError(f"Expected a file path, but got a directory path: {relative_path}.")
        if not symbol_retriever.can_analyze_file(proj_rel):
            raise ValueError(
                f"Cannot extract symbols from file {relative_path}. "
                f"Active languages: {[l.value for l in self.agent.get_active_lsp_languages()]}"
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
    Finds symbols by name pattern, globally or within a file/directory.

    A name path is the dot-like path within a source file, e.g. "MyClass/my_method".
    Use this to locate where something is defined, get its location, or read its body.
    """

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
        max_matches: int = DEFAULT_MAX_RESULTS,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Find symbols by name path pattern and return their locations (and optionally bodies).

        A name path pattern can be:
        - a simple name ("my_method") — matches any symbol with that name
        - a relative path ("MyClass/my_method") — matches any symbol with that name path suffix
        - an absolute path ("/MyClass/my_method") — requires an exact full match

        Overloaded symbols are automatically annotated with an ``overload_info`` block
        that shows the overload index, how many siblings appear in this result set, and
        the parameter tokens that distinguish each overload from the others.  Use
        ``depth:1`` on a class to discover its full overload inventory before renaming.

        :param name_path_pattern: the pattern to match against symbol name paths.
        :param depth: child depth to include (1 = immediate children such as methods of a class).
            Ignored when include_body is True.
        :param relative_path: restrict search to this file or directory. Empty = whole codebase.
        :param include_body: include the symbol's source code in the result.
        :param include_info: include hover-style info (docstring, signature). Ignored when
            include_body is True.
        :param include_kinds: limit results to these LSP symbol kind integers.
        :param exclude_kinds: exclude these LSP symbol kind integers.
        :param substring_matching: if True, match the last name path element as a substring.
        :param max_matches: maximum matches to return; -1 for unlimited. Default 12.
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: matching symbols with their locations (and optionally bodies or info).
            Overloaded symbols carry an additional ``overload_info`` key.
        """
        if include_body:
            depth = 0
        assert max_matches != 0, "max_matches must be > 0 or -1."
        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        ws = self.workspace
        if relative_path:
            unit, proj_rel_path = ws.resolve_unit_for_path(relative_path)
            units_and_paths = [(unit, proj_rel_path)]
        else:
            units_and_paths = [(u, "") for u in ws.units]

        from serena.symbol import LanguageServerSymbol  # local to avoid circular at module level

        symbols: list[tuple[str, LanguageServerSymbol, "LanguageServerSymbolRetriever"]] = []  # type: ignore[name-defined]
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
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "find_symbol: skipping unit %s due to error: %s", unit.project_id, exc
                )

        n_matches = len(symbols)

        def create_short_result() -> str:
            rel_to_name_paths: defaultdict[str, list[str]] = defaultdict(list)
            for proj_id, s, _ in symbols:
                labeled = ws.label(proj_id, s.location.relative_path or "unknown")
                rel_to_name_paths[labeled].append(s.get_name_path())
            return f"Shortened result:\n{self._to_json(rel_to_name_paths)}"

        if 0 < max_matches < n_matches:
            return (
                f"Showing {max_matches} of {n_matches} total matches (pass max_matches=-1 for all).\n"
                + create_short_result()
            )

        # Build overload_info annotations for any overloaded symbol in the result set.
        # Symbols sharing the same base name path (i.e. differing only in their [n] suffix)
        # are grouped; the parameter tokens that distinguish each member from its siblings
        # are computed and stored so callers know immediately how overloads differ.
        overload_annotation: dict[int, dict] = {}
        if any(s.overload_idx is not None for _, s, _ in symbols):
            base_to_indices: dict[str, list[int]] = {}
            for result_idx, (_, s, _) in enumerate(symbols):
                if s.overload_idx is not None:
                    # Strip trailing [n] to obtain the shared base path for grouping
                    base_path = _re.sub(r"\[\d+\]$", "", s.get_name_path())
                    base_to_indices.setdefault(base_path, []).append(result_idx)

            for base_path, indices in base_to_indices.items():
                group_syms = [symbols[i][1] for i in indices]
                distinct_per_sym = _compute_overload_distinct_params(group_syms)
                for list_pos, result_idx in enumerate(indices):
                    sym = symbols[result_idx][1]
                    info: dict = {
                        "index": sym.overload_idx,
                        "total_in_results": len(indices),
                    }
                    if distinct_per_sym[list_pos]:
                        info["distinguishing_params"] = distinct_per_sym[list_pos]
                    overload_annotation[result_idx] = info

        symbol_dicts = []
        for result_idx, (proj_id, s, _) in enumerate(symbols):
            d = dict(
                s.to_dict(
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
            )
            if ws.is_multi_project and d.get("relative_path"):
                d["relative_path"] = ws.label(proj_id, d["relative_path"])
            if result_idx in overload_annotation:
                d["overload_info"] = overload_annotation[result_idx]
            symbol_dicts.append(d)

        if not include_body and include_info:
            from collections import defaultdict as _dd
            by_retriever: dict[int, list] = _dd(list)
            for idx, (proj_id, s, retr) in enumerate(symbols):
                by_retriever[id(retr)].append((idx, s, retr))
            for _, indexed in by_retriever.items():
                retriever = indexed[0][2]
                ls_symbols = [s for _, s, _ in indexed]
                info_by_symbol = retriever.request_info_for_symbol_batch(ls_symbols)
                for orig_idx, s, _ in indexed:
                    if symbol_info := info_by_symbol.get(s):
                        symbol_dicts[orig_idx]["info"] = symbol_info  # type: ignore[typeddict-unknown-key]

        grouped = self.symbol_dict_grouper.group(symbol_dicts)
        result = self._to_json(grouped)
        return self._limit_length(result, max_answer_chars, shortened_result_factories=[create_short_result])


class FindUsagesTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds all usages (references) of a symbol: which other symbols call or
    reference this symbol, and where in the code they do so.

    Use this to understand the impact of changing a symbol before editing it,
    or to trace how a function/class/variable is used throughout the codebase.
    """

    symbol_dict_grouper = LanguageServerSymbolDictGrouper(["relative_path", "kind"], ["kind"], collapse_singleton=True)

    # noinspection PyDefaultArgument
    def apply(
        self,
        relative_path: str,
        name_path: str = "",
        name_path_pattern: str = "",
        include_kinds: list[int] = [],  # noqa: B006
        exclude_kinds: list[int] = [],  # noqa: B006
        max_answer_chars: int = -1,
    ) -> str:
        """
        Find all usages of a symbol across the codebase.

        Each result includes the name path and location of the referencing symbol
        plus a short code snippet around the actual reference site.

        **Naming note** — ``name_path`` vs ``name_path_pattern``:
        ``find_symbol`` accepts a *pattern* (substring / glob-style matching).
        This tool requires an *exact* name path that identifies one specific symbol
        (e.g. ``"MyClass/MyMethod[2]"``).  To avoid confusion, both ``name_path``
        and ``name_path_pattern`` are accepted here and treated identically — use
        whichever matches the name you are already working with.

        :param name_path: exact name path of the symbol whose usages are sought.
            Accepts overload disambiguation suffixes such as ``[n]``, ``@line:N``,
            or ``(partial_sig)``.  Alias: ``name_path_pattern``.
        :param name_path_pattern: alias for ``name_path``; use when the name path
            was obtained from a ``find_symbol`` call and copying the parameter name
            is more natural.  If both are provided, ``name_path`` takes precedence.
        :param relative_path: file containing the symbol. For external dependency symbols,
            use the <ext...> identifier returned by earlier tool calls.
        :param include_kinds: limit results to these LSP symbol kind integers.
        :param exclude_kinds: exclude these LSP symbol kind integers.
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: referencing symbols grouped by file and kind, with code snippets.
        """
        # Resolve alias: name_path wins; fall back to name_path_pattern.
        resolved_name_path = name_path or name_path_pattern
        if not resolved_name_path:
            return "Error: provide either name_path or name_path_pattern."
        name_path = resolved_name_path

        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        references_in_symbols = symbol_retriever.find_referencing_symbols(
            name_path,
            relative_file_path=proj_rel,
            include_body=False,
            include_kinds=parsed_include_kinds,
            exclude_kinds=parsed_exclude_kinds,
        )

        reference_dicts = []
        for ref in references_in_symbols:
            ref_dict = dict(ref.symbol.to_dict(kind=True, relative_path=True, depth=0, body=False, body_location=True))
            ref_relative_path = ref.symbol.location.relative_path
            assert ref_relative_path is not None
            content_around_ref = unit.project.retrieve_content_around_line(
                relative_file_path=ref_relative_path, line=ref.line, context_lines_before=1, context_lines_after=1
            )
            ref_dict["content_around_reference"] = content_around_ref.to_display_string()
            reference_dicts.append(ref_dict)

        ref_summaries = [
            {
                "name_path": d.get("name_path"),
                "kind": d.get("kind"),
                "relative_path": d.get("relative_path"),
                "reference_line": ref.line,
            }
            for ref, d in zip(references_in_symbols, reference_dicts, strict=True)
        ]

        result = self.symbol_dict_grouper.group(reference_dicts)  # type: ignore

        def make_refs_without_context() -> str:
            grouped = self.symbol_dict_grouper.group(copy.deepcopy(ref_summaries))  # type: ignore
            return f"References without surrounding lines:\n{self._to_json(grouped)}"

        def make_per_file_counts() -> str:
            counts = Counter(str(r["relative_path"]) for r in ref_summaries)
            return f"Reference counts per file:\n{self._to_json(counts)}"

        def make_summary() -> str:
            return f"Found {len(ref_summaries)} references."

        result_json = self._to_json(result)

        if not reference_dicts:
            scope_note = (
                "\nNo usages found within the currently active project scope. "
                "If this symbol is consumed by other projects, activate the parent workspace "
                "(the directory containing all sibling projects) to find cross-project usages."
            )
            return result_json + scope_note

        return self._limit_length(result_json, max_answer_chars, shortened_result_factories=[make_refs_without_context, make_per_file_counts, make_summary])


class FindImplementationsTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds concrete implementations of an abstract symbol (interface, abstract class,
    or abstract method).

    Use this to discover which classes implement an interface or which methods
    override an abstract method in the codebase.
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
        Find all symbols that implement the given abstract symbol.

        :param name_path: name path of the abstract symbol (interface, abstract class, or method).
        :param relative_path: file containing the symbol. Must be a file, not a directory.
        :param include_info: include hover-style info about each implementing symbol.
        :param include_kinds: limit results to these LSP symbol kind integers.
        :param exclude_kinds: exclude these LSP symbol kind integers.
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: implementing symbols with their locations.
        """
        parsed_include_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in include_kinds] if include_kinds else None
        parsed_exclude_kinds: Sequence[SymbolKind] | None = [SymbolKind(k) for k in exclude_kinds] if exclude_kinds else None
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        implementing_symbols = symbol_retriever.find_implementing_symbols(
            name_path,
            relative_file_path=proj_rel,
            include_body=False,
            include_kinds=parsed_include_kinds,
            exclude_kinds=parsed_exclude_kinds,
        )

        symbol_dicts = [
            dict(s.to_dict(kind=True, relative_path=True, depth=0, body=False, body_location=True))
            for s in implementing_symbols
        ]
        if include_info:
            info_by_symbol = symbol_retriever.request_info_for_symbol_batch(implementing_symbols)
            for s, s_dict in zip(implementing_symbols, symbol_dicts, strict=True):
                if symbol_info := info_by_symbol.get(s):
                    s_dict["info"] = symbol_info
                    s_dict.pop("name", None)

        result = self._to_json(symbol_dicts)
        return self._limit_length(result, max_answer_chars)


class FindDefinitionTool(Tool, ToolMarkerSymbolicRead):
    """
    Finds where a symbol is defined given a usage site in a source file.

    Useful when you see a call like "obj.process()" or a type annotation and want
    to jump to the definition of 'process'. Provide a regex that captures the exact
    symbol name within its context to locate the definition via the language server.
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
        Find the definition of a symbol referenced at a usage site in a file.

        Provide a regex with exactly one capture group that isolates the symbol
        at its call/usage site. Example: to find the definition of 'process' in
        "obj.process(x=42)", use "obj\.(process)\(x=42\)".

        Prefer a regex with enough surrounding context to be unambiguous.
        Uses Python re syntax with MULTILINE and DOTALL flags.

        :param relative_path: file containing the usage site to resolve.
        :param regex: regex with one capture group isolating the symbol name at its usage.
        :param containing_symbol_name_path: optional name path of the enclosing symbol
            to restrict the search to that symbol's body.
        :param include_body: include the full source body of the found definition.
        :param include_info: include hover-style info (docstring/signature) of the definition.
        :return: definition location(s) with name path, file, and line.
        """
        relative_path = self._sanitize_input_param(relative_path)
        regex = self._sanitize_input_param(regex)
        unit, proj_rel = self.resolve_project(relative_path)
        symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)

        editor = self.create_ls_code_editor_for(unit.project)
        line_offset = 0
        if not containing_symbol_name_path:
            content = editor.read_file(proj_rel)
        else:
            symbol = symbol_retriever.find_unique(name_path_pattern=containing_symbol_name_path, within_relative_path=proj_rel)
            body_line_numbers = symbol.get_body_line_numbers_or_raise()
            content = editor.read_file(proj_rel, lines=body_line_numbers)
            line_offset = body_line_numbers[0]

        match_coords = find_all_text_coordinates(content, regex)
        if not match_coords:
            raise ValueError(f"No match found for regex: {regex}")
        if line_offset:
            match_coords = [TextCoords(coords.line + line_offset, coords.col) for coords in match_coords]

        unique_declarations: list[dict[str, Any]] = []
        seen_declaration_sites: set[tuple[str, int]] = set()
        for coords in match_coords:
            defining_symbol = symbol_retriever.find_declaration(
                relative_file_path=proj_rel,
                line=coords.line,
                column=coords.col,
                include_body=include_body,
            )
            if defining_symbol is None:
                continue
            declaration_relative_path = defining_symbol.relative_path
            declaration_line = defining_symbol.line
            if declaration_relative_path is None or declaration_line is None:
                continue
            declaration_key = (declaration_relative_path, declaration_line)
            if declaration_key in seen_declaration_sites:
                continue
            seen_declaration_sites.add(declaration_key)

            entry: dict[str, Any] = {
                "relative_path": declaration_relative_path,
                "line": declaration_line,
            }
            name_path = defining_symbol.get_name_path()
            if name_path:
                entry["name_path"] = name_path
            entry["symbol_kind"] = defining_symbol.symbol_kind_name
            if include_body and defining_symbol.body is not None:
                entry["body"] = defining_symbol.body
            if include_info:
                if symbol_info := symbol_retriever.request_info_for_symbol(defining_symbol):
                    entry["info"] = symbol_info
            unique_declarations.append(entry)

        if not unique_declarations:
            raise ValueError(
                f"No definition found for any of the {len(match_coords)} regex match(es) in {relative_path}."
            )

        total_unique = len(unique_declarations)
        truncated = unique_declarations[:DEFAULT_MAX_RESULTS]
        result_json = self._to_json(truncated)

        if total_unique == 1:
            return f"Found definition.\n{result_json}"
        if total_unique > DEFAULT_MAX_RESULTS:
            n_more = total_unique - DEFAULT_MAX_RESULTS
            return f"{result_json}\n... and {n_more} more. Provide a more specific regex to narrow results."
        return result_json


class CheckErrorsTool(Tool, ToolMarkerSymbolicRead):
    """
    Returns LSP diagnostics (errors, warnings, hints) for a file, grouped by
    severity and owning symbol.

    Call this after editing a file to verify the changes did not introduce new
    problems, or to understand existing issues before starting an edit.
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
        Get diagnostics (errors, warnings, hints) for a file.

        Results are grouped as: relative_path -> severity -> name_path -> diagnostics.
        Diagnostics not attributable to a specific symbol are grouped under "<file>".

        :param relative_path: the file to check.
        :param start_line: first 0-based line to include. Defaults to 0 (start of file).
        :param end_line: last 0-based line to include. -1 means until the end of the file.
        :param min_severity: lowest severity to include. 1=Error, 2=Warning, 3=Info, 4=Hint.
            Diagnostics at or below this number are returned. Default 4 (all).
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: diagnostics grouped by file, severity, and symbol.
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


class CheckSymbolErrorsTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Returns LSP diagnostics for a specific symbol and, optionally, for all
    symbols that reference it.

    Use this after editing a symbol to verify no errors were introduced, or
    to get a focused error view for a particular class/function.
    """

    def apply(
        self,
        name_path: str,
        reference_file: str = "",
        check_usages: bool = False,
        min_severity: int = 4,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Get diagnostics for a symbol, and optionally for symbols that use it.

        Results are grouped as: relative_path -> severity -> name_path -> diagnostics.

        :param name_path: name path of the symbol to inspect.
        :param reference_file: optional file path to disambiguate the symbol if it
            appears in multiple files.
        :param check_usages: if True, also include diagnostics for all symbols that
            reference the given symbol.
        :param min_severity: lowest severity to include. 1=Error, 2=Warning, 3=Info, 4=Hint.
        :param max_answer_chars: cap on result size; -1 uses the configured default.
        :return: diagnostics grouped by file, severity, and symbol.
        """
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
            check_symbol_references=check_usages,
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


class RewriteSymbolTool(EditingToolWithDiagnostics):
    """
    Replaces the full implementation of a symbol with new code.

    Use this when you know the complete new version of a function, method, class,
    or other symbol and want to replace its entire body. Always retrieve the symbol
    with include_body=True first so you have the current body as reference.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        body: str,
    ) -> str:
        r"""
        Replace a symbol's full implementation with new code.

        The body must include the full definition — for example, a function body
        must include the signature line (def/func/function ...) as well as the body.
        Retrieve the current body with find_symbol(include_body=True) before editing.

        :param name_path: name path of the symbol to rewrite.
        :param relative_path: file containing the symbol.
        :param body: the complete new symbol definition, including signature/declaration.
        :return: OK on success.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        with self.DiagnosticsContext(self, proj_rel, project=unit.project) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(unit.project)
            code_editor.replace_body(name_path, relative_file_path=proj_rel, body=body)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class InjectCodeTool(EditingToolWithDiagnostics):
    """
    Inserts new code immediately before or after a symbol's definition.

    Use 'before' to add imports, decorators, or a new declaration that should
    appear above the symbol. Use 'after' to add a new method after a class,
    a sibling function after an existing function, etc.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        position: Literal["before", "after"],
        body: str,
    ) -> str:
        """
        Insert code immediately before or after a symbol's definition.

        The inserted code is placed on the line immediately adjacent to the symbol —
        before its opening line (position='before') or after its closing line
        (position='after').

        :param name_path: name path of the anchor symbol.
        :param relative_path: file containing the anchor symbol.
        :param position: 'before' to insert before the symbol, 'after' to insert after it.
        :param body: the code to insert. Should not rely on leading/trailing blank lines;
            Serena handles spacing based on language conventions.
        :return: OK on success.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        with self.DiagnosticsContext(self, proj_rel, project=unit.project) as diagnostics_context:
            code_editor = self.create_ls_code_editor_for(unit.project)
            if position == "after":
                code_editor.insert_after_symbol(name_path, relative_file_path=proj_rel, body=body)
            else:
                code_editor.insert_before_symbol(name_path, relative_file_path=proj_rel, body=body)
            return diagnostics_context.format_result(SUCCESS_RESULT)


class RenameSymbolTool(Tool, ToolMarkerSymbolicEdit):
    """
    Renames a symbol throughout the entire codebase using language server refactoring.

    All references to the symbol are updated atomically — no manual search-and-replace
    needed. Works across files within the active workspace.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
        new_name: str,
        dry_run: bool = False,
    ) -> str:
        """
        Rename a symbol and all its references across the codebase.

        To target a specific overload in languages like C# or Java, append a
        0-based index to the method segment: ``ClassName/MethodName[0]``.

        Two stable alternatives that survive index shifts between renames:
          - Line hint:         ``MethodName@line:42``   (0-based line of the identifier)
          - Partial signature: ``MethodName(TypeA, TypeB)`` (substring of the LSP signature)

        All three forms may be combined with the method name:
        ``MethodName[0]``, ``MethodName@line:42``, ``MethodName(TypeA)``.

        Set ``dry_run=True`` to verify the correct overload is targeted before
        committing — resolves the symbol and returns its info without renaming.

        :param name_path: name path of the symbol to rename. Append ``[n]``,
            ``@line:N``, or ``(partial_sig)`` to the method segment for overloads.
        :param relative_path: file containing the symbol.
        :param new_name: the new name to give the symbol.
        :param dry_run: if True, resolve the target symbol and return its info
            without applying the rename.
        :return: resolved symbol info (name path, line, detail) plus — when
            dry_run is False — the number of references updated.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        code_editor = self.create_ls_code_editor_for(unit.project)
        status_message = code_editor.rename_symbol(name_path, relative_path=proj_rel, new_name=new_name, dry_run=dry_run)
        return status_message


class DeleteSymbolTool(Tool, ToolMarkerSymbolicEdit):
    """
    Deletes a symbol if it has no references (safe delete).

    If any other code references the symbol, the deletion is refused and the
    reference locations are returned instead so you can decide how to proceed.
    This prevents accidentally orphaning call sites.
    """

    def apply(
        self,
        name_path: str,
        relative_path: str,
    ) -> str:
        """
        Delete a symbol if it is unreferenced; otherwise return its reference locations.

        :param name_path: name path of the symbol to delete.
        :param relative_path: file containing the symbol.
        :return: OK if deleted, or a map of files and lines that reference the symbol.
        """
        unit, proj_rel = self.resolve_project(relative_path)
        ls_symbol_retriever = self.create_language_server_symbol_retriever_for(unit.project)
        symbol = ls_symbol_retriever.find_unique(name_path, substring_matching=False, within_relative_path=proj_rel)
        symbol_rel_path = symbol.relative_path
        assert symbol_rel_path is not None, f"Symbol {name_path} has no relative path, this is likely a bug."
        assert symbol_rel_path == proj_rel, f"Symbol {name_path} is not in the expected relative path {proj_rel}."
        symbol_name_path = symbol.get_name_path()

        symbol_line = symbol.line
        symbol_col = symbol.column
        assert symbol_line is not None and symbol_col is not None, (
            f"Symbol {name_path} has no identifier position, this is likely a bug."
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
            return (
                f"Cannot delete: symbol '{symbol_name_path}' is referenced in: {self._to_json(file_to_lines)}"
            )
        code_editor = self.create_ls_code_editor()
        code_editor.delete_symbol(symbol_name_path, relative_file_path=symbol_rel_path)
        return SUCCESS_RESULT
