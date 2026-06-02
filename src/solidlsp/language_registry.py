"""
Central language/framework metadata registry.

Each LanguageEntry records the file extensions, framework detection markers,
tooling requirements, and confidence hints that let workspace discovery pick
the right language servers automatically — without relying solely on extension
counts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solidlsp.ls_config import Language


@dataclass(frozen=True)
class FrameworkMarker:
    """
    A file or directory whose presence is strong evidence that a particular
    language / framework is used in that directory.
    """

    filename: str
    "Filename or glob-style name to look for (matched with os.path.basename)."

    confidence: float = 1.0
    "0-1 confidence boost applied when this marker is found."

    notes: str = ""
    "Human-readable note about what this marker indicates."


@dataclass(frozen=True)
class LanguageEntry:
    """
    All metadata Serena needs to detect, configure, and label a language.
    """

    language_value: str
    "Matches Language enum .value (e.g. 'csharp', 'typescript')."

    extensions: tuple[str, ...]
    "Canonical file extensions including the leading dot, e.g. ('.cs',)."

    aliases: tuple[str, ...] = ()
    "Alternative names recognised in project.yml and CLI."

    framework_markers: tuple[FrameworkMarker, ...] = ()
    "Files/dirs whose presence suggests this language is in use."

    tooling_requirements: tuple[str, ...] = ()
    "Human-readable requirements, e.g. '.NET 10+', 'Node.js + npm'."

    is_experimental: bool = False
    "Experimental languages are not auto-detected."

    superseded_by: tuple[str, ...] = ()
    "Language values that supersede this one (prefer those if both detected)."


# ---------------------------------------------------------------------------
# Registry data
# ---------------------------------------------------------------------------

_ENTRIES: list[LanguageEntry] = [
    # --- C# ------------------------------------------------------------------
    LanguageEntry(
        language_value="csharp",
        extensions=(".cs",),
        aliases=("c#", "dotnet"),
        framework_markers=(
            FrameworkMarker("*.sln", 1.0, ".NET solution file"),
            FrameworkMarker("*.csproj", 1.0, ".NET project file"),
            FrameworkMarker("global.json", 0.8, ".NET global config"),
            FrameworkMarker("Directory.Build.props", 0.8, "MSBuild convention"),
            FrameworkMarker("NuGet.Config", 0.7, "NuGet package config"),
            FrameworkMarker("packages.config", 0.8, "legacy NuGet config"),
        ),
        tooling_requirements=(".NET 10+ (default Roslyn) or OmniSharp for legacy",),
    ),
    LanguageEntry(
        language_value="csharp_omnisharp",
        extensions=(".cs",),
        aliases=("omnisharp",),
        framework_markers=(
            FrameworkMarker("*.sln", 1.0, ".NET solution file"),
            FrameworkMarker("packages.config", 1.0, "legacy NuGet (prefers OmniSharp)"),
        ),
        tooling_requirements=("OmniSharp runtime dependencies auto-downloaded",),
        is_experimental=True,
        superseded_by=("csharp",),
    ),
    # --- TypeScript / JavaScript ---------------------------------------------
    LanguageEntry(
        language_value="typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"),
        aliases=("javascript", "js", "ts"),
        framework_markers=(
            FrameworkMarker("package.json", 1.0, "Node.js package manifest"),
            FrameworkMarker("tsconfig.json", 1.0, "TypeScript config"),
            FrameworkMarker("jsconfig.json", 0.9, "JavaScript config"),
            FrameworkMarker(".nvmrc", 0.5, "Node version file"),
        ),
        tooling_requirements=("Node.js + npm",),
    ),
    # --- Python --------------------------------------------------------------
    LanguageEntry(
        language_value="python",
        extensions=(".py", ".pyi"),
        aliases=("py",),
        framework_markers=(
            FrameworkMarker("pyproject.toml", 1.0, "Python project config"),
            FrameworkMarker("setup.py", 1.0, "setuptools config"),
            FrameworkMarker("setup.cfg", 0.9, "setuptools config"),
            FrameworkMarker("requirements.txt", 0.8, "pip requirements"),
            FrameworkMarker("Pipfile", 0.8, "pipenv config"),
            FrameworkMarker("poetry.lock", 1.0, "poetry lockfile"),
        ),
    ),
    # --- Rust ----------------------------------------------------------------
    LanguageEntry(
        language_value="rust",
        extensions=(".rs",),
        framework_markers=(
            FrameworkMarker("Cargo.toml", 1.0, "Rust project manifest"),
            FrameworkMarker("Cargo.lock", 0.9, "Rust lockfile"),
        ),
        tooling_requirements=("rustup + rust-analyzer",),
    ),
    # --- Go ------------------------------------------------------------------
    LanguageEntry(
        language_value="go",
        extensions=(".go",),
        framework_markers=(
            FrameworkMarker("go.mod", 1.0, "Go module definition"),
            FrameworkMarker("go.sum", 0.9, "Go module checksums"),
        ),
        tooling_requirements=("gopls on PATH",),
    ),
    # --- Java ----------------------------------------------------------------
    LanguageEntry(
        language_value="java",
        extensions=(".java",),
        framework_markers=(
            FrameworkMarker("pom.xml", 1.0, "Maven project"),
            FrameworkMarker("build.gradle", 1.0, "Gradle project"),
            FrameworkMarker("build.gradle.kts", 1.0, "Gradle Kotlin DSL project"),
        ),
        tooling_requirements=("JDK 17+; Eclipse JDT auto-downloaded",),
    ),
    # --- Kotlin --------------------------------------------------------------
    LanguageEntry(
        language_value="kotlin",
        extensions=(".kt", ".kts"),
        framework_markers=(
            FrameworkMarker("build.gradle.kts", 1.0, "Gradle Kotlin DSL"),
        ),
    ),
    # --- Ruby ----------------------------------------------------------------
    LanguageEntry(
        language_value="ruby",
        extensions=(".rb", ".erb"),
        framework_markers=(
            FrameworkMarker("Gemfile", 1.0, "Bundler manifest"),
            FrameworkMarker("Gemfile.lock", 0.9, "Bundler lockfile"),
            FrameworkMarker(".ruby-version", 0.8, "rbenv/rvm version"),
        ),
    ),
    # --- PHP -----------------------------------------------------------------
    LanguageEntry(
        language_value="php",
        extensions=(".php",),
        framework_markers=(
            FrameworkMarker("composer.json", 1.0, "Composer manifest"),
            FrameworkMarker("composer.lock", 0.9, "Composer lockfile"),
        ),
        tooling_requirements=("Intelephense license optional; PHP 8+ for phpactor",),
    ),
    # --- Vue / Angular / Svelte (framework-specific TS variants) -------------
    LanguageEntry(
        language_value="vue",
        extensions=(".vue",),
        aliases=("vuejs",),
        framework_markers=(
            FrameworkMarker("vue.config.js", 1.0, "Vue CLI config"),
            FrameworkMarker("vue.config.ts", 1.0, "Vue CLI config (TS)"),
            FrameworkMarker("vite.config.ts", 0.7, "Vite (often Vue)"),
        ),
        tooling_requirements=("Node.js + npm",),
        superseded_by=("angular",),
    ),
    LanguageEntry(
        language_value="svelte",
        extensions=(".svelte",),
        framework_markers=(
            FrameworkMarker("svelte.config.js", 1.0, "SvelteKit config"),
            FrameworkMarker("svelte.config.ts", 1.0, "SvelteKit config (TS)"),
        ),
        tooling_requirements=("Node.js 18+ + npm",),
    ),
    LanguageEntry(
        language_value="angular",
        extensions=(".ts", ".html"),
        aliases=("ng",),
        framework_markers=(
            FrameworkMarker("angular.json", 1.0, "Angular workspace config"),
            FrameworkMarker("nx.json", 0.9, "Nx monorepo (Angular)"),
        ),
        tooling_requirements=("Node.js + npm; angular.json must be present",),
        is_experimental=True,
        superseded_by=(),
    ),
    # --- Swift ---------------------------------------------------------------
    LanguageEntry(
        language_value="swift",
        extensions=(".swift",),
        framework_markers=(
            FrameworkMarker("Package.swift", 1.0, "Swift Package Manager"),
            FrameworkMarker("*.xcodeproj", 0.8, "Xcode project"),
        ),
    ),
    # --- C / C++ -------------------------------------------------------------
    LanguageEntry(
        language_value="cpp",
        extensions=(
            ".c", ".h", ".cpp", ".cc", ".cxx", ".hh", ".hpp", ".hxx",
            ".inl", ".ipp", ".tpp", ".cu", ".hip",
        ),
        aliases=("c", "c++"),
        framework_markers=(
            FrameworkMarker("CMakeLists.txt", 1.0, "CMake project"),
            FrameworkMarker("compile_commands.json", 1.0, "compile DB for clangd"),
            FrameworkMarker("Makefile", 0.6, "Make project"),
            FrameworkMarker("meson.build", 0.8, "Meson project"),
        ),
        tooling_requirements=("clangd on PATH; compile_commands.json recommended",),
    ),
    # --- Terraform -----------------------------------------------------------
    LanguageEntry(
        language_value="terraform",
        extensions=(".tf", ".tfvars"),
        framework_markers=(
            FrameworkMarker("*.tf", 1.0, "Terraform files"),
            FrameworkMarker(".terraform.lock.hcl", 1.0, "Terraform lock"),
        ),
    ),
    # --- Bash / Shell --------------------------------------------------------
    LanguageEntry(
        language_value="bash",
        extensions=(".sh", ".bash"),
        framework_markers=(),
    ),
    # --- Scala ---------------------------------------------------------------
    LanguageEntry(
        language_value="scala",
        extensions=(".scala", ".sbt"),
        framework_markers=(
            FrameworkMarker("build.sbt", 1.0, "SBT build"),
        ),
    ),
    # --- Dart / Flutter -------------------------------------------------------
    LanguageEntry(
        language_value="dart",
        extensions=(".dart",),
        framework_markers=(
            FrameworkMarker("pubspec.yaml", 1.0, "Dart/Flutter package"),
        ),
    ),
]

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_BY_VALUE: dict[str, LanguageEntry] = {e.language_value: e for e in _ENTRIES}
_BY_ALIAS: dict[str, LanguageEntry] = {
    alias: e for e in _ENTRIES for alias in e.aliases
}


def get_entry(language_value: str) -> LanguageEntry | None:
    """Return the registry entry for the given Language.value string, or None."""
    return _BY_VALUE.get(language_value) or _BY_ALIAS.get(language_value.lower())


def get_all_entries() -> list[LanguageEntry]:
    return list(_ENTRIES)


def detect_languages_in_directory(directory: str) -> list[tuple[str, float]]:
    """
    Scan *directory* (non-recursively) for framework markers and count source
    files to produce a ranked list of ``(language_value, confidence)`` pairs.

    Confidence is a simple heuristic combining marker presence and file-count
    fraction; values are not probabilities.  The caller should use the ranking
    to decide which language servers to start.
    """
    if not os.path.isdir(directory):
        return []

    try:
        entries_in_dir = {e.name: e for e in os.scandir(directory)}
    except PermissionError:
        return []

    scores: dict[str, float] = {}

    # --- Marker-based scoring ------------------------------------------------
    for lang_entry in _ENTRIES:
        if lang_entry.is_experimental:
            continue
        marker_score = 0.0
        for marker in lang_entry.framework_markers:
            # Support simple glob-style *.ext patterns
            if marker.filename.startswith("*"):
                suffix = marker.filename[1:]  # e.g. ".sln"
                if any(name.endswith(suffix) for name in entries_in_dir):
                    marker_score = max(marker_score, marker.confidence)
            elif marker.filename in entries_in_dir:
                marker_score = max(marker_score, marker.confidence)
        if marker_score > 0:
            scores[lang_entry.language_value] = scores.get(lang_entry.language_value, 0.0) + marker_score

    # --- Extension-count scoring (lightweight, non-recursive) ----------------
    ext_counts: dict[str, int] = {}
    total = 0
    for name in entries_in_dir:
        _, ext = os.path.splitext(name)
        if ext:
            ext_counts[ext.lower()] = ext_counts.get(ext.lower(), 0) + 1
            total += 1

    if total > 0:
        for lang_entry in _ENTRIES:
            if lang_entry.is_experimental:
                continue
            count = sum(ext_counts.get(ext.lower(), 0) for ext in lang_entry.extensions)
            if count > 0:
                fraction = count / total
                existing = scores.get(lang_entry.language_value, 0.0)
                # Extension fraction adds at most 0.4 so markers dominate
                scores[lang_entry.language_value] = existing + fraction * 0.4

    # --- Resolve supersede conflicts -----------------------------------------
    for lang_entry in _ENTRIES:
        for superior_value in lang_entry.superseded_by:
            if superior_value in scores and lang_entry.language_value in scores:
                del scores[lang_entry.language_value]
                break

    result = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return result
