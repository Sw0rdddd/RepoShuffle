"""Reorganize implementation files, preserving public names and auxiliary trees."""

from __future__ import annotations

import collections
import math
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


DEFAULT_COMPLEXITY = 0.7
_COMPLEXITY_BRANCH = 2.0
_COMPLEXITY_CAPACITY = (10.0, 4.0)


def _capacity(complexity: float) -> int:
    high, low = _COMPLEXITY_CAPACITY
    return max(1, round(high + (low - high) * complexity))


def _validated_complexity(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShuffleError("complexity must be a finite number between 0 and 1")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ShuffleError("complexity must be a finite number between 0 and 1")
    return value


class NumberedLayout:
    def __init__(self, seed: int, depth: int, complexity: float):
        self.rng = random.Random(seed)
        self.next_directory = 1
        self.depth = depth
        self.complexity = complexity
        self.capacity = _capacity(complexity)

    def _weights(self) -> list[float]:
        # complexity=0 collapses to depth 1. Higher values favour deeper
        # levels; the seeded jitter makes the same complexity produce
        # different trees without hiding the overall trend.
        ratio = _COMPLEXITY_BRANCH * self.complexity
        weights = [ratio ** (level - 1) for level in range(1, self.depth + 1)]
        if self.complexity:
            jitter = 0.3 * self.complexity
            weights = [weight * self.rng.uniform(1 - jitter, 1 + jitter) for weight in weights]
        return weights

    def _quotas(self, count: int) -> list[int]:
        if count <= 0:
            return [0] * self.depth
        weights = self._weights()
        total = sum(weights)
        ideals = [count * weight / total for weight in weights]
        threshold = max(1.0, 0.05 * count)
        used = max((i + 1 for i, ideal in enumerate(ideals) if ideal >= threshold), default=1)
        used = min(used, count, self.depth)
        used_weights = weights[:used]
        used_total = sum(used_weights)
        remaining = count - used
        shares = [remaining * weight / used_total for weight in used_weights]
        extra = [math.floor(share) for share in shares]
        left = remaining - sum(extra)
        order = sorted(range(used), key=lambda i: (shares[i] - extra[i], -i), reverse=True)
        for index in order[:left]:
            extra[index] += 1
        return [1 + amount for amount in extra] + [0] * (self.depth - used)

    def _directories(self, zone: str, quotas: list[int]) -> list[str]:
        levels: dict[int, list[str]] = {0: [zone]}
        directories: list[str] = []
        for depth, amount in enumerate(quotas, start=1):
            if amount <= 0:
                break
            parents = levels[depth - 1]
            children: list[str] = []
            child_counts = dict.fromkeys(parents, 0)
            for _ in range(amount):
                least = min(child_counts.values())
                choices = [parent for parent, total in child_counts.items() if total == least]
                parent = self.rng.choice(choices)
                directory = f"{parent}/module_{self.next_directory}"
                self.next_directory += 1
                child_counts[parent] += 1
                children.append(directory)
            directories.extend(children)
            levels[depth] = children
        return directories

    def targets(self, zone: str, names: dict[str, str]) -> dict[str, str]:
        if not names:
            return {}
        families: dict[str, list[str]] = {}
        for path, name in names.items():
            families.setdefault(name.casefold(), []).append(path)
        max_family = max(map(len, families.values()))
        count = max(0, math.ceil(len(names) / self.capacity) - 1, max_family - 1)
        slots = [zone, *self._directories(zone, self._quotas(count))]
        loads = [0] * len(slots)
        used = [set() for _ in slots]
        result: dict[str, str] = {}
        for family in sorted(families, key=lambda key: (-len(families[key]), key)):
            members = list(families[family])
            self.rng.shuffle(members)
            for path in members:
                available = [i for i in range(len(slots)) if family not in used[i]]
                least = min(loads[i] for i in available)
                choices = [i for i in available if loads[i] == least]
                index = self.rng.choice(choices)
                used[index].add(family)
                loads[index] += 1
                result[path] = f"{slots[index]}/{names[path]}"
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


def _layout_stats(plan: Plan) -> dict[str, object]:
    moved = [f for f in plan.inspection.files if f.role in {"source", "script"}]
    files_by_depth: collections.Counter[int] = collections.Counter()
    directories: dict[int, set[str]] = collections.defaultdict(set)
    loads: collections.Counter[str] = collections.Counter()
    zones: set[str] = set()
    numbered: set[str] = set()
    for record in moved:
        target = plan.mapping.get(record.path)
        if target is None:
            continue
        parent = PurePosixPath(target).parent
        parts = parent.parts
        zone_index = next(
            (
                i
                for i, part in enumerate(parts)
                if part.startswith("_reposhuffle") and part.endswith("_code")
            ),
            None,
        )
        if zone_index is None:
            depth = 0
            zones.add(str(parent))
        else:
            zone = PurePosixPath(*parts[: zone_index + 1])
            relative = parts[zone_index + 1 :]
            depth = len(relative)
            directory = zone
            for level, part in enumerate(relative, start=1):
                directory = directory / part
                directories[level].add(str(directory))
                numbered.add(str(directory))
            zones.add(str(zone))
        files_by_depth[depth] += 1
        loads[str(parent)] += 1
    branches: collections.Counter[str] = collections.Counter()
    for directory in numbered:
        parent = str(PurePosixPath(directory).parent)
        if parent in numbered:
            branches[parent] += 1

    def summarize(values: list[int]) -> dict[str, object]:
        if not values:
            return {"min": 0, "max": 0, "mean": 0.0}
        return {"min": min(values), "max": max(values), "mean": round(sum(values) / len(values), 2)}

    return {
        "complexity": plan.configuration.get("complexity"),
        "max_depth": plan.configuration.get("max_depth"),
        "files_per_directory_target": plan.configuration.get("files_per_directory_target"),
        "moved_files": len(moved),
        "preserved_files": len(plan.inspection.files) - len(moved),
        "zones": len(zones),
        "numbered_directories": len(numbered),
        "files_by_depth": {str(depth): files_by_depth[depth] for depth in sorted(files_by_depth)},
        "directories_by_depth": {
            str(depth): len(paths) for depth, paths in sorted(directories.items())
        },
        "files_per_directory": summarize(list(loads.values())),
        "children_per_directory": summarize([branches.get(directory, 0) for directory in numbered]),
        "root_files": files_by_depth.get(0, 0),
    }


def build_plan(
    source: str | Path,
    *,
    mode: str = "random",
    seed: int | None = None,
    max_depth: int = 3,
    complexity: float | None = None,
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
    if mode == "random":
        resolved_complexity = (
            DEFAULT_COMPLEXITY if complexity is None else _validated_complexity(complexity)
        )
    elif complexity is not None:
        raise ShuffleError("complexity is only valid for random mode")
    else:
        resolved_complexity = None
    capacity = _capacity(resolved_complexity) if resolved_complexity is not None else None
    contents: dict[str, bytes] = {}
    inspection = read_tree(Path(source), include=include, exclude=exclude, _contents=contents)
    plan = Plan(
        inspection,
        mode,
        seed if seed is not None else secrets.randbits(64),
        configuration={
            "max_depth": max_depth,
            "complexity": resolved_complexity,
            "files_per_directory_target": capacity,
            "include": include,
            "exclude": exclude,
            "strategy": "preserve-logical-modules",
            "scope": "implementation-only",
            "layout_version": 3,
            "respect_gitignore": True,
        },
    )
    if not plan.ok:
        return plan
    # Render the exact bytes that were analyzed and hashed. apply_plan rechecks
    # the input before and after writing; no cross-operation cache is used.
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
        for name, root in inspection.packages.items():
            _package(plan, name, root, layout)
        _scripts(plan, layout)
        include_storage_packages(plan)
        plan.directories = sorted(set(plan.directories))
        plan.layout = _layout_stats(plan)
        changed = {c["target"] for c in plan.changes if c["kind"] == "move"}
        changed.update(
            c["path"]
            for c in plan.changes
            if c["kind"] in {"package-bootstrap", "build-configuration", "setup-configuration"}
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
