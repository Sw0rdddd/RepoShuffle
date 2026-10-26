"""Reorganize implementation files, preserving public names and auxiliary trees."""

from __future__ import annotations

import collections
import random
import secrets
from pathlib import Path, PurePosixPath

from .models import Diagnostic, FileRecord, Inspection, Plan, ShuffleError
from .project import include_storage_packages
from .rewrite import bootstrap, script_entry
from .scan import decode_python, digest, read_tree


def inspect_project(
    source: str | Path, *, include: list[str] | None = None, exclude: list[str] | None = None
) -> Inspection:
    return read_tree(Path(source), include=include, exclude=exclude)


class NumberedLayout:
    def __init__(self, seed: int, depth: int):
        self.rng = random.Random(seed)
        self.next_directory = 1
        self.depth = depth

    def targets(self, zone: str, names: dict[str, str]) -> dict[str, str]:
        # Group first: roughly six files per directory, except where equal file
        # names require more groups. Every created directory receives real code.
        families: dict[str, list[str]] = {}
        for path, name in names.items():
            families.setdefault(name.casefold(), []).append(path)
        count = max(1, (len(names) + 5) // 6, max(map(len, families.values())))
        directories: list[tuple[str, int]] = []
        for _ in range(count):
            parents = [(zone, 0)] * max(1, count // 2)
            parents.extend((path, depth) for path, depth in directories if depth < self.depth)
            parent, depth = self.rng.choice(parents)
            directory = f"{parent}/module_{self.next_directory}"
            self.next_directory += 1
            directories.append((directory, depth + 1))
        loads = [0] * count
        groups = list(families.values())
        self.rng.shuffle(groups)
        groups.sort(key=len, reverse=True)
        result = {}
        for family in groups:
            self.rng.shuffle(family)
            candidates = list(range(count))
            self.rng.shuffle(candidates)
            candidates.sort(key=lambda i: loads[i])
            for path, index in zip(family, candidates, strict=False):
                result[path] = f"{directories[index][0]}/{names[path]}"
                loads[index] += 1
        return result


def _prefix(plan: Plan, directory: str) -> str:
    root = PurePosixPath(directory)
    entries = {
        PurePosixPath(path).relative_to(root).parts[0]
        for path in [*(f.path for f in plan.inspection.files), *plan.inspection.directories]
        if PurePosixPath(path) != root and PurePosixPath(path).is_relative_to(root)
    }
    prefix, index = "_reposhuffle", 0
    while any(name.startswith(prefix) for name in entries):
        index += 1
        prefix = f"_reposhuffle{index}"
    return prefix


def _flat_names(files: list[FileRecord]) -> dict[str, str]:
    counts = collections.Counter(PurePosixPath(f.path).name.casefold() for f in files)
    reserved = {PurePosixPath(f.path).name.casefold() for f in files} | {"__init__.py"}
    allocated: set[str] = set()
    result = {}
    for record in sorted(files, key=lambda f: f.path):
        path = PurePosixPath(record.path)
        if counts[path.name.casefold()] == 1 and path.name != "__init__.py":
            name = path.name
        else:
            stem = "_package" if path.name == "__init__.py" else path.stem
            index = 1
            while True:
                name = f"{stem}_{index}{path.suffix}"
                if name.casefold() not in reserved | allocated:
                    break
                index += 1
        allocated.add(name.casefold())
        result[record.path] = name
    return result


def _generated(plan: Plan, path: str, data: bytes, reason: str) -> None:
    if path in plan.contents:
        raise ShuffleError(f"Generated file collides with an existing file: {path}")
    plan.generated[path] = reason
    plan.contents[path] = data


def _move(plan: Plan, records: list[FileRecord], zone: str, layout: NumberedLayout) -> None:
    names = _flat_names(records)
    if plan.mode == "random":
        names = {
            record.path: names[record.path]
            if record.is_package
            else PurePosixPath(record.path).name
            for record in records
        }
        targets = layout.targets(zone, names)
    else:
        targets = {path: f"{zone}/{name}" for path, name in names.items()}
    used = {p.casefold() for p in plan.contents}
    for record in records:
        target = targets[record.path]
        if target.casefold() in used:
            raise ShuffleError(f"Target collision: {target}")
        used.add(target.casefold())
        data = plan.contents.pop(record.path)
        plan.mapping[record.path] = target
        plan.contents[target] = data
        plan.changes.append({"kind": "move", "path": record.path, "target": target})
        # Empty physical packages make storage discoverable by standard build backends.
        for parent in reversed(PurePosixPath(target).parents):
            if not parent.is_relative_to(PurePosixPath(zone)):
                continue
            init = str(parent / "__init__.py")
            if init not in plan.contents:
                _generated(plan, init, b"", "implementation storage package")


def _package(plan: Plan, name: str, root: str, layout: NumberedLayout) -> None:
    files = [f for f in plan.inspection.files if f.owner == name and f.module]
    moving = [f for f in files if f.role == "source"]
    if not moving:
        return
    prefix = _prefix(plan, root)
    zone = f"{root}/{prefix}_code"
    _move(plan, moving, zone, layout)
    for record in moving:
        if record.is_package:
            # Keep package discovery and resource roots in wheels; the finder
            # executes the real initializer at its moved location, not this marker.
            _generated(plan, record.path, b"", "package resource directory marker")
    routes = {}
    for record in files:
        if record.module == name:
            continue
        relative = str(PurePosixPath(plan.mapping[record.path]).relative_to(root))
        resource_dir = str(PurePosixPath(record.path).parent.relative_to(root))
        routes[record.module] = (relative, record.is_package, resource_dir)
        # Intermediate namespace packages need no fabricated __init__.py.
        pieces = record.module.split(".")
        for i in range(len(name.split(".")) + 1, len(pieces)):
            ancestor = ".".join(pieces[:i])
            resource = "/".join(pieces[len(name.split(".")) : i])
            routes.setdefault(ancestor, (None, True, resource))
    resource_roots = {
        str(PurePosixPath(root) / resource)
        for _, package, resource in routes.values()
        if package and resource != "."
    }
    plan.directories.extend(resource_roots)
    for directory in sorted(resource_roots, key=len, reverse=True):
        if not any(path.startswith(directory + "/") for path in plan.contents):
            # A Python marker survives wheel packaging without fabricating an
            # __init__.py for a namespace package or writing at import time.
            marker = prefix + "_resources"
            while f"{directory}/{marker}.py" in plan.mapping:
                marker += "_"
            _generated(plan, f"{directory}/{marker}.py", b"", "namespace resource directory marker")
    runtime_name, route_name = prefix + "_runtime", prefix + "_routes"
    runtime = Path(__file__).with_name("runtime.py").read_bytes()
    _generated(plan, f"{root}/{runtime_name}.py", runtime, "self-contained logical module loader")
    _generated(
        plan,
        f"{root}/{route_name}.py",
        ("ROUTES = " + repr(dict(sorted(routes.items()))) + "\n").encode(),
        "logical module routing table",
    )
    entry = f"{root}/__init__.py"
    plan.contents[entry] = bootstrap(plan.contents[entry], runtime_name, route_name, prefix)
    plan.changes.append({"kind": "package-bootstrap", "path": entry})


def _scripts(plan: Plan, layout: NumberedLayout) -> None:
    groups: dict[str, list[FileRecord]] = {}
    for record in plan.inspection.files:
        if record.role == "script":
            groups.setdefault(record.owner or ".", []).append(record)
    for root, files in sorted(groups.items()):
        prefix = _prefix(plan, root)
        zone = str(PurePosixPath(root) / (prefix + "_code"))
        original = {f.path: plan.contents[f.path] for f in files}
        _move(plan, files, zone, layout)
        for record in files:
            _generated(
                plan,
                record.path,
                script_entry(record.path, plan.mapping[record.path], original[record.path]),
                "stable script entry",
            )
            plan.entry_commands.append("python " + repr(record.path))


def build_plan(
    source: str | Path,
    *,
    mode: str = "random",
    seed: int | None = None,
    max_depth: int = 3,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Plan:
    """Render in memory, without importing input modules or executing build hooks."""
    if mode not in {"flat", "random"}:
        raise ShuffleError("mode must be 'flat' or 'random'")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 5:
        raise ShuffleError("max_depth must be an integer between 1 and 5")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ShuffleError("seed must be an integer")
    inspection = inspect_project(source, include=include, exclude=exclude)
    plan = Plan(
        inspection,
        mode,
        seed if seed is not None else secrets.randbits(64),
        configuration={
            "max_depth": max_depth,
            "include": include,
            "exclude": exclude,
            "strategy": "preserve-logical-modules",
            "scope": "implementation-only",
            "layout_version": 2,
            "random_group_target": 6,
            "respect_gitignore": True,
        },
    )
    if not plan.ok:
        return plan
    for record in inspection.files:
        data = (inspection.source / record.path).read_bytes()
        if digest(data) != record.sha256:
            raise ShuffleError(f"Input changed during planning: {record.path}")
        plan.mapping[record.path] = record.path
        plan.contents[record.path] = data
        if record.module:
            plan.modules[record.module] = record.module
    populated = {
        str(parent) for record in inspection.files for parent in PurePosixPath(record.path).parents
    }
    plan.directories = [path for path in inspection.directories if path not in populated]
    layout = NumberedLayout(plan.seed, max_depth)
    try:
        for name, root in inspection.packages.items():
            _package(plan, name, root, layout)
        _scripts(plan, layout)
        include_storage_packages(plan)
        plan.directories = sorted(set(plan.directories))
        changed = {c["target"] for c in plan.changes if c["kind"] == "move"}
        changed.update(
            c["path"]
            for c in plan.changes
            if c["kind"] in {"package-bootstrap", "build-configuration"}
        )
        changed.update(plan.generated)
        for path in changed:
            if path.endswith(".py"):
                compile(decode_python(plan.contents[path]), path, "exec")
        if len({p.casefold() for p in plan.contents}) != len(plan.contents):
            raise ShuffleError("Case-insensitive output collision")
        for record in inspection.files:
            if (
                record.role == "preserved"
                and record.path not in changed
                and digest(plan.contents[record.path]) != record.sha256
            ):
                raise ShuffleError(f"Unnecessary auxiliary modification: {record.path}")
    except (SyntaxError, ValueError, UnicodeError) as exc:
        plan.diagnostics.append(Diagnostic("RENDER_ERROR", ".", str(exc)))
    if plan.ok:
        plan._validated = plan._fingerprint()
    return plan
