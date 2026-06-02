import logging
import os
from collections.abc import Callable, Iterator
from typing import TypeVar

from serena.util.file_system import find_all_non_ignored_files
from solidlsp.language_registry import detect_languages_in_directory
from solidlsp.ls_config import Language

T = TypeVar("T")

log = logging.getLogger(__name__)


def iter_subclasses(
    cls: type[T], recursive: bool = True, inclusion_predicate: Callable[[type[T]], bool] = lambda t: True
) -> Iterator[type[T]]:
    """Iterate over all subclasses of a class.

    :param cls: The class whose subclasses to iterate over.
    :param recursive: If True, also iterate over all subclasses of all subclasses.
    :param inclusion_predicate: a predicate function to decide whether to include a subclass in the result
    """
    for subclass in cls.__subclasses__():
        if inclusion_predicate(subclass):
            yield subclass
        if recursive:
            yield from iter_subclasses(subclass, recursive, inclusion_predicate)


# Minimum confidence threshold for auto-enabling a language without interactive prompting.
_CONFIDENCE_THRESHOLD = 0.5


def determine_programming_language_composition(repo_path: str) -> dict[Language, float]:
    """
    Determine the programming language composition of a repository.

    Uses the central language registry for framework-marker detection first,
    then falls back to extension-count analysis across all non-ignored files.
    Multiple high-confidence languages can be returned (not just the top one).

    :param repo_path: Path to the repository to analyze
    :return: Dictionary mapping languages to confidence scores
    """
    # Registry-based detection on the top-level directory (fast, framework-aware).
    registry_scores = detect_languages_in_directory(repo_path)

    # Extension-count fallback across the full tree for languages not already
    # detected by the registry scan.
    all_files = find_all_non_ignored_files(repo_path)
    ext_language_counts: dict[Language, int] = {}
    total_files = len(all_files) or 1

    for language in Language.iter_all(include_experimental=False):
        matcher = language.get_source_fn_matcher()
        count = sum(
            1 for f in all_files if matcher.is_relevant_filename(os.path.basename(f))
        )
        if count > 0:
            ext_language_counts[language] = count

    # Merge: registry scores take precedence; extension counts fill the gaps.
    merged: dict[Language, float] = {}

    for lang_value, confidence in registry_scores:
        try:
            lang = Language(lang_value)
        except ValueError:
            continue
        merged[lang] = confidence

    for language, count in ext_language_counts.items():
        if language not in merged:
            fraction = count / total_files
            # Scale so extension-only detection maxes out at 0.4 (below registry scores).
            merged[language] = round(fraction * 0.4, 4)

    return merged
