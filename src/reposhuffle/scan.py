"""Read-only inventory and implementation selection; auxiliary trees stay in place."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import tokenize
from io import BytesIO
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from .models import Diagnostic, FileRecord, Inspection, ShuffleError
from .project import discover, identifier

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
    "build",
    "dist",
    "node_modules",
    ".idea",
    ".vscode",
}
BUILD_SCRIPTS = {"setup.py", "conftest.py"}
AUXILIARY_SCOPES = {
    "tests",
    "test",
    "docs",
    "doc",
    "examples",
    "fixtures",
    "benchmarks",
    "questions",
}


def _resource_directories(source: Path, paths: list[Path]) -> set[Path]:
    """Locate data beside code, excluding documentation and typing/build metadata."""
    directories = set()
    for path in paths:
        relative = path.relative_to(source)
        if (
            path.suffix in {".py", ".pyi", ".md", ".rst", ".toml", ".cfg", ".ini"}
            or path.name == "py.typed"
            or any(part.startswith(".") or part in AUXILIARY_SCOPES for part in relative.parts)
        ):
            continue
        directories.update(parent for parent in path.parents if parent.is_relative_to(source))
    return directories


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_python(data: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(BytesIO(data).readline)
    return data.decode(encoding)


def matches(path: str, patterns: list[str]) -> bool:
    return any(
        path == pattern.rstrip("/")
        or path.startswith(pattern.rstrip("/") + "/")
        or fnmatch.fnmatchcase(path, pattern)
        for pattern in patterns
    )


def _walk_error(error: OSError) -> None:
    raise error


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


def read_tree(
    source: Path, *, include: list[str] | None = None, exclude: list[str] | None = None
) -> Inspection:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ShuffleError(f"Not a project directory: {source}")
    packages, scripts, anchors = discover(source)
    result = Inspection(source, packages=packages, script_roots=scripts)
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
            if name in EXCLUDED_DIRS or name.endswith(".egg-info"):
                result.excluded.append(rel + "/")
            elif path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                result.diagnostics.append(
                    Diagnostic("SYMLINK", rel, "Directory links need an explicit copy policy.")
                )
            else:
                result.directories.append(rel)
                contexts[path] = (_is_ignored(path, scopes, blocked, is_directory=True), scopes)
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
    resources = _resource_directories(source, paths)
    seen: dict[str, str] = {}
    for directory in result.directories:
        if directory.casefold() in seen:
            result.diagnostics.append(
                Diagnostic(
                    "CASE_COLLISION",
                    directory,
                    f"Conflicts with {seen[directory.casefold()]} on Windows.",
                )
            )
        seen[directory.casefold()] = directory
    for path in sorted(paths, key=lambda p: p.relative_to(source).as_posix()):
        rel = path.relative_to(source).as_posix()
        if rel.casefold() in seen:
            result.diagnostics.append(
                Diagnostic(
                    "CASE_COLLISION", rel, f"Conflicts with {seen[rel.casefold()]} on Windows."
                )
            )
        seen[rel.casefold()] = rel
        data = path.read_bytes()
        role, reason, owner, module, package = "preserved", "auxiliary file", None, None, False
        if path.suffix == ".py":
            owners = [(name, root) for name, root in packages.items() if rel.startswith(root + "/")]
            if owners:
                owner, root = max(owners, key=lambda item: len(item[1]))
                parts = list(PurePosixPath(rel[len(root) + 1 :]).with_suffix("").parts)
                package = parts[-1] == "__init__"
                if package:
                    parts.pop()
                if all(identifier(part) for part in parts):
                    module = ".".join([owner] + parts)
                    role, reason = "source", "implementation package"
            else:
                areas = [
                    area
                    for area in scripts
                    if (area == "." and "/" not in rel) or rel.startswith(area + "/")
                ]
                if areas:
                    owner = max(areas, key=len)
                    role, reason = "script", "implementation script area"
            protected = any(
                part in AUXILIARY_SCOPES or part.startswith(".")
                for part in PurePosixPath(rel).parts[:-1]
            )
            if protected or path.name.startswith("test_") or path.name.endswith("_test.py"):
                role, reason = "preserved", "test/documentation/fixture scope"
            if include and not matches(rel, include):
                role, reason = "preserved", "outside explicit include selection"
            elif (
                include
                and matches(rel, include)
                and role == "preserved"
                and not owners
                and not protected
            ):
                role, reason, owner = (
                    "script",
                    "explicit implementation selection",
                    str(PurePosixPath(rel).parent),
                )
            if exclude and matches(rel, exclude):
                role, reason = "preserved", "explicitly excluded from relocation"
            if path.name in BUILD_SCRIPTS:
                role, reason = "preserved", "build entry or pytest scope"
            if module == owner and package:
                role, reason = "preserved", "public package initialization entry"
            if module in anchors:
                role, reason = "preserved", anchors[module]
            if rel in ignored:
                role, reason = "preserved", "matched .gitignore rule"
            if role in {"source", "script"}:
                try:
                    tree = ast.parse(decode_python(data), filename=rel)
                    compile(tree, rel, "exec")
                    # Source-location consumers keep their anchor, not a fabricated old __file__.
                    if any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(tree)):
                        role, reason = "preserved", "source-relative filesystem anchor"
                    elif path.parent in resources and any(
                        isinstance(node, ast.Call)
                        and any(
                            isinstance(arg, ast.Name) and arg.id == "__name__"
                            for arg in [*node.args, *(kw.value for kw in node.keywords)]
                        )
                        for node in ast.walk(tree)
                    ):
                        # Resource factories can derive their root from the caller's
                        # module identity (e.g. a Blueprint), without a local __file__.
                        role, reason = "preserved", "module-identity resource anchor"
                except (SyntaxError, UnicodeError, LookupError) as exc:
                    result.diagnostics.append(
                        Diagnostic("PYTHON_SYNTAX", rel, str(exc), getattr(exc, "lineno", None))
                    )
        if rel in ignored:
            role, reason = "preserved", "matched .gitignore rule"
        result.files.append(
            FileRecord(rel, digest(data), len(data), module, package, role, owner, reason)
        )
    result.excluded.sort()
    if not any(f.role in {"source", "script"} for f in result.files):
        result.diagnostics.append(
            Diagnostic(
                "NO_IMPLEMENTATION",
                ".",
                "No movable implementation files selected.",
                severity="warning",
            )
        )
    return result
