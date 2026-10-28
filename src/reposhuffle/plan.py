"""Shared read-only planning pipeline; language semantics live in adapters."""

from __future__ import annotations

import secrets
from pathlib import Path, PurePosixPath

from .adapters import get_adapter
from .contents import SnapshotContents
from .documentation import rewrite_documentation
from .layout import (
    DEFAULT_COMPLEXITY,
    NumberedLayout,
    _capacity,
    _layout_stats,
    _validated_complexity,
)
from .models import Diagnostic, Inspection, Plan, ShuffleError
from .paths import select_project


def _validate_source_dirs(source_dirs: list[str] | None) -> None:
    if source_dirs is not None and (
        not isinstance(source_dirs, list)
        or not source_dirs
        or any(
            not isinstance(root, str) or not root or Path(root).is_absolute()
            for root in source_dirs
        )
    ):
        raise ShuffleError("source_dirs must be a nonempty list of relative paths")


def inspect_project(
    source: str | Path,
    *,
    language: str = "auto",
    source_dirs: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    subproject: str | Path | None = None,
) -> Inspection:
    _validate_source_dirs(source_dirs)
    selected = select_project(source, subproject)
    return get_adapter(selected, language).inspect(
        selected, include=include, exclude=exclude, source_dirs=source_dirs
    )


def build_plan(
    source: str | Path,
    *,
    language: str = "auto",
    source_dirs: list[str] | None = None,
    mode: str = "random",
    seed: int | None = None,
    max_depth: int = 3,
    complexity: float | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    subproject: str | Path | None = None,
) -> Plan:
    """Render in memory without importing target code or executing build hooks."""
    if mode not in {"flat", "random"}:
        raise ShuffleError("mode must be 'flat' or 'random'")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 5:
        raise ShuffleError("max_depth must be an integer between 1 and 5")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ShuffleError("seed must be an integer")
    if mode == "random":
        resolved_complexity = (
            DEFAULT_COMPLEXITY if complexity is None else _validated_complexity(complexity)
        )
    elif complexity is not None:
        raise ShuffleError("complexity is only valid for random mode")
    else:
        resolved_complexity = None
    selected = select_project(source, subproject)
    adapter = get_adapter(selected, language)
    if mode not in adapter.modes:
        raise ShuffleError(f"{adapter.name} supports only {', '.join(sorted(adapter.modes))} mode")
    contents = SnapshotContents()
    _validate_source_dirs(source_dirs)
    inspection = adapter.inspect(
        selected, include=include, exclude=exclude, contents=contents, source_dirs=source_dirs
    )
    plan = Plan(
        inspection,
        mode,
        seed if seed is not None else secrets.randbits(64),
        configuration={
            "language": adapter.name,
            "source_dirs": source_dirs,
            "max_depth": max_depth,
            "complexity": resolved_complexity,
            "files_per_directory_target": (
                _capacity(resolved_complexity) if resolved_complexity is not None else None
            ),
            "include": include,
            "exclude": exclude,
            "strategy": adapter.strategy,
            "scope": "implementation-only",
            "layout_version": 5,
            "allocation_scope": "source-root",
            "respect_gitignore": True,
            "subproject": str(subproject) if subproject is not None else None,
        },
    )
    plan.compatibility = adapter.explain(plan)
    if not plan.ok:
        return plan
    plan.contents = contents
    for record in inspection.files:
        plan.mapping[record.path] = record.path
        if record.module:
            plan.modules[record.module] = record.module
    populated = {
        str(parent) for record in inspection.files for parent in PurePosixPath(record.path).parents
    }
    plan.directories = [path for path in inspection.directories if path not in populated]
    layout = NumberedLayout(plan.seed, max_depth, resolved_complexity or 0.0)
    try:
        adapter.render(plan, layout)
        rewrite_documentation(plan)
        plan.directories = sorted(set(plan.directories))
        plan.layout = _layout_stats(plan)
        changed = {c["target"] if c["kind"] == "move" else c["path"] for c in plan.changes}
        changed.update(plan.generated)
        adapter.validate(plan, changed)
        if len({p.casefold() for p in plan.contents}) != len(plan.contents):
            raise ShuffleError("Case-insensitive output collision")
        hashes = dict(plan._content_hashes())
        for record in inspection.files:
            if (
                record.role == "preserved"
                and record.path not in changed
                and hashes[record.path] != record.sha256
            ):
                raise ShuffleError(f"Unnecessary auxiliary modification: {record.path}")
    except (SyntaxError, ValueError, UnicodeError) as exc:
        plan.diagnostics.append(Diagnostic("RENDER_ERROR", ".", str(exc)))
    if plan.ok:
        plan.static_checks = [
            *adapter.static_checks,
            "unique-portable-targets",
            "preserved-auxiliary-content",
        ]
        plan._validated = plan._fingerprint()
    return plan
