"""Read-only inventory and implementation selection; auxiliary trees stay in place."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import tokenize
from dataclasses import replace
from io import BytesIO
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from .models import Diagnostic, FileRecord, Inspection, ShuffleError
from .project import discover, identifier
from .references import referenced

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
    ".vscode",
}
BUILD_SCRIPTS = {"setup.py", "conftest.py"}
NESTED_PROJECT_FILES = {"pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml"}
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


def _preserve_compiled_fallbacks(result: Inspection) -> None:
    records = {record.path: record for record in result.files}
    order = [record.path for record in result.files]
    for relative, record in list(records.items()):
        if not relative.endswith(".py") or record.role not in {"source", "script"}:
            continue
        stem = relative[:-3]
        if stem.endswith("_cy"):
            paths = [relative]
            plain = stem[:-3] + ".py"
            if plain in records:
                paths.append(plain)
            reason = "compiled fallback module"
        else:
            compiled = stem + "_cy.py"
            if compiled not in records:
                continue
            paths = [relative, compiled]
            reason = "compiled/non-compiled sibling pair"
        for path in paths:
            records[path] = replace(records[path], role="preserved", reason=reason)
    result.files = [records[path] for path in order]


def _apply_references(
    source: Path,
    result: Inspection,
    reference_sets: dict[str, tuple[set[Path], set[Path], bool]],
) -> None:
    records = {record.path: record for record in result.files}
    order = [record.path for record in result.files]
    queue = [record.path for record in result.files if record.path.endswith(".py")]
    children: dict[str, list[str]] = {}
    for relative in queue:
        children.setdefault(PurePosixPath(relative).parent.as_posix(), []).append(relative)
    analyzed: set[str] = set()
    while queue:
        relative = queue.pop()
        if relative in analyzed:
            continue
        analyzed.add(relative)
        record = records[relative]
        references, directories, dynamic = reference_sets[relative]
        if directories and record.role in {"source", "script"}:
            records[relative] = replace(
                record,
                role="preserved",
                reason="source file lists a directory",
            )
            record = records[relative]
        for directory in directories:
            directory_path = directory.relative_to(source).as_posix()
            for candidate in children.get(directory_path, []):
                candidate_record = records[candidate]
                if candidate_record.role in {"source", "script"}:
                    records[candidate] = replace(
                        candidate_record,
                        role="preserved",
                        reason=f"listed by {relative}",
                    )
                    queue.append(candidate)
        for target in references:
            target_path = target.relative_to(source).as_posix()
            target_record = records.get(target_path)
            if target_record is None or not target_path.endswith(".py") or target_path == relative:
                continue
            if record.role in {"source", "script"}:
                record = replace(
                    record,
                    role="preserved",
                    reason=f"filesystem reference to {target_path}",
                )
                records[relative] = record
            if target_record.role in {"source", "script"}:
                records[target_path] = replace(
                    target_record,
                    role="preserved",
                    reason=f"referenced by {relative}",
                )
                queue.append(target_path)
        if dynamic:
            for sibling_path in children.get(PurePosixPath(relative).parent.as_posix(), []):
                sibling_record = records[sibling_path]
                if sibling_record.role not in {"source", "script"}:
                    continue
                records[sibling_path] = replace(
                    sibling_record,
                    role="preserved",
                    reason=f"sibling of dynamic source loader {relative}",
                )
                queue.append(sibling_path)
    result.files = [records[path] for path in order]


def _walk_tree(source: Path, result: Inspection) -> tuple[list[Path], set[str]]:
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
            if (
                name in EXCLUDED_DIRS
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
    result.excluded.sort()
    return sorted(paths, key=lambda p: p.relative_to(source).as_posix()), ignored


def read_inventory(source: Path) -> Inspection:
    """Recheck paths and all content hashes without repeating semantic analysis."""
    source = source.expanduser().resolve()
    result = Inspection(source)
    paths, _ = _walk_tree(source, result)
    for path in paths:
        data = path.read_bytes()
        result.files.append(
            FileRecord(path.relative_to(source).as_posix(), digest(data), len(data))
        )
    return result


def read_tree(
    source: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    _contents: dict[str, bytes] | None = None,
) -> Inspection:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ShuffleError(f"Not a project directory: {source}")
    packages, scripts, anchors, source_roots = discover(source)
    result = Inspection(source, packages=packages, script_roots=scripts, source_roots=source_roots)
    paths, ignored = _walk_tree(source, result)
    nested_roots = {
        path.parent.relative_to(source).as_posix()
        for path in paths
        if path.name in NESTED_PROJECT_FILES and path.parent != source
    }
    resources = _resource_directories(source, paths)
    reference_sets: dict[str, tuple[set[Path], set[Path], bool]] = {}
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
    for path in paths:
        rel = path.relative_to(source).as_posix()
        if rel.casefold() in seen:
            result.diagnostics.append(
                Diagnostic(
                    "CASE_COLLISION", rel, f"Conflicts with {seen[rel.casefold()]} on Windows."
                )
            )
        seen[rel.casefold()] = rel
        data = path.read_bytes()
        if _contents is not None:
            _contents[rel] = data
        tree = None
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
            if any(rel == root or rel.startswith(root + "/") for root in nested_roots):
                role, reason = "preserved", "nested project"
            if module and package:
                role, reason = "preserved", "package initialization entry"
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
        if path.suffix == ".py":
            reference_sets[rel] = referenced(path, source, data=data, tree=tree)
        if rel in ignored:
            role, reason = "preserved", "matched .gitignore rule"
        result.files.append(
            FileRecord(rel, digest(data), len(data), module, package, role, owner, reason)
        )
    result.excluded.sort()
    _apply_references(source, result, reference_sets)
    _preserve_compiled_fallbacks(result)
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
