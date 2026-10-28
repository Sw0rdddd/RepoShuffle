"""Shared traversal, selection matching and byte-level inventory verification."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from pathlib import Path

from pathspec import GitIgnoreSpec

from .contents import file_hash
from .models import Diagnostic, FileRecord, Inspection, ShuffleError

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".tmp",
    "node_modules",
    ".idea",
    ".gradle",
    ".vscode",
}


def matches(path: str, patterns: list[str]) -> bool:
    return any(
        path == pattern.rstrip("/")
        or path.startswith(pattern.rstrip("/") + "/")
        or fnmatch.fnmatchcase(path, pattern)
        for pattern in patterns
    )


def _walk_error(error: OSError) -> None:
    raise error


def _generated_build(path: Path) -> bool:
    """Recognise root build outputs without hiding source directories named build."""
    try:
        entries = list(path.iterdir())
    except OSError:
        return True
    names = {entry.name for entry in entries}
    return any(
        name.startswith(("bdist.", "temp.", "scripts-")) or name in {"lib", "PKG-INFO"}
        for name in names
    ) or any(entry.suffix in {".whl", ".egg", ".tar"} for entry in entries)


def _is_ignored(
    path: Path,
    scopes: list[tuple[Path, GitIgnoreSpec]],
    blocked: bool,
    *,
    is_directory: bool = False,
) -> bool:
    if blocked:
        return True  # Git cannot re-include a file inside an excluded parent.
    matched = False
    for base, spec in scopes:
        relative = path.relative_to(base).as_posix() + ("/" if is_directory else "")
        include = spec.check_file(relative).include
        if include is not None:
            matched = include
    return matched


def _walk_tree(
    source: Path,
    result: Inspection,
    *,
    exclude_directory: Callable[[Path], bool] | None = None,
) -> tuple[list[Path], set[str]]:
    """Share traversal, exclusions and link checks between analysis and validation."""
    if not source.is_dir():
        raise ShuffleError(f"Not a project directory: {source}")
    if (source / "manifest.json").exists() and (source / "shuffled").is_dir():
        raise ShuffleError("A RepoShuffle output container is not an input project")
    paths: list[Path] = []
    ignored: set[str] = set()
    contexts: dict[Path, tuple[bool, list[tuple[Path, GitIgnoreSpec]]]] = {source: (False, [])}
    for root, dirs, files in os.walk(source, followlinks=False, onerror=_walk_error):
        directory = Path(root)
        blocked, inherited = contexts.pop(directory)
        scopes = list(inherited)
        ignore_file = directory / ".gitignore"
        if not blocked and ignore_file.is_file() and not ignore_file.is_symlink():
            scopes.append(
                (
                    directory,
                    GitIgnoreSpec.from_lines(
                        ignore_file.read_text(encoding="utf-8-sig").splitlines()
                    ),
                )
            )

        kept = []
        for name in sorted(dirs):
            path = Path(root) / name
            rel = path.relative_to(source).as_posix()
            ignored_directory = _is_ignored(path, scopes, blocked, is_directory=True)
            if not ignored_directory and exclude_directory is not None and exclude_directory(path):
                result.inventory_excludes.append(rel)
            if (
                rel in result.inventory_excludes
                or name in EXCLUDED_DIRS
                or name.endswith(".egg-info")
                or (directory == source and name in {"build", "dist"} and _generated_build(path))
            ):
                result.excluded.append(rel + "/")
            elif path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                result.diagnostics.append(
                    Diagnostic("SYMLINK", rel, "Directory links need an explicit copy policy.")
                )
            else:
                result.directories.append(rel)
                contexts[path] = (ignored_directory, scopes)
                kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = Path(root) / name
            rel = path.relative_to(source).as_posix()
            if name in {".git", ".hg", ".svn"} or name.endswith((".pyc", ".pyo")):
                result.excluded.append(rel)
            elif path.is_symlink() or not path.is_file():
                result.diagnostics.append(
                    Diagnostic(
                        "SYMLINK",
                        rel,
                        "Only regular files are copied; links are not silently followed.",
                    )
                )
            else:
                if _is_ignored(path, scopes, blocked):
                    ignored.add(rel)
                paths.append(path)
    result.directories.sort()
    result.excluded.sort()
    return sorted(paths, key=lambda p: p.relative_to(source).as_posix()), ignored


def read_inventory(source: Path, *, inventory_excludes: list[str] | None = None) -> Inspection:
    """Recheck paths and all content hashes without repeating semantic analysis."""
    source = source.expanduser().resolve()
    result = Inspection(source, inventory_excludes=list(inventory_excludes or []))
    paths, _ = _walk_tree(source, result)
    for path in paths:
        sha256, size = file_hash(path)
        result.files.append(FileRecord(path.relative_to(source).as_posix(), sha256, size))
    return result
