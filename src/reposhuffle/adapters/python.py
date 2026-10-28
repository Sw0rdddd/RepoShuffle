"""Python discovery, logical module routing and build delivery."""

from __future__ import annotations

import collections
import posixpath
from pathlib import Path, PurePosixPath

from ..compatibility import explain
from ..layout import NumberedLayout
from ..models import FileRecord, Plan, ShuffleError
from ..project import _logical_storage_packages, identifier, include_storage_packages, read_config
from ..rewrite import bootstrap, script_entry
from ..scan import decode_python, read_tree
from ..transform import relocate
from ..transform import storage_prefix as _prefix


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
    relocate(plan, {record.path: targets[record.path] for record in records}, zone)
    for record in records:
        target = targets[record.path]
        # Empty physical packages make storage discoverable by standard build backends.
        for parent in reversed(PurePosixPath(target).parents):
            if not parent.is_relative_to(PurePosixPath(zone)):
                continue
            init = str(parent / "__init__.py")
            if init not in plan.contents:
                _generated(plan, init, b"", "implementation storage package")


def _relocate(plan: Plan, layout: NumberedLayout) -> None:
    """Allocate once per source boundary, independently of logical package ownership."""
    packages_by_root: dict[str, list[str]] = collections.defaultdict(list)
    for name, root in plan.inspection.source_roots.items():
        packages_by_root[root].append(name)
    backend = (
        read_config(plan.inspection.source)
        .get("build-system", {})
        .get("build-backend", "setuptools.build_meta")
    )
    shared = not backend.startswith("flit_core.")
    storage_roots = {
        name: root if len(names) > 1 and shared else plan.inspection.packages[name]
        for root, names in packages_by_root.items()
        for name in names
    }
    groups: dict[str, list[FileRecord]] = collections.defaultdict(list)
    for record in plan.inspection.files:
        if record.role == "source":
            groups[storage_roots[record.owner]].append(record)
        elif record.role == "script":
            groups[record.owner or "."].append(record)
    for root, records in sorted(groups.items()):
        zone = str(PurePosixPath(root) / (_prefix(plan, root) + "_code"))
        _move(plan, records, zone, layout)
        aliases = {}
        for record in records:
            parts = PurePosixPath(record.path).relative_to(root).with_suffix("").parts
            canonical = record.module
            if canonical is None:
                if parts == ("__main__",) or not all(identifier(part) for part in parts):
                    continue
                canonical = ".".join(parts)
            physical = PurePosixPath(plan.mapping[record.path]).relative_to(zone).with_suffix("")
            aliases[".".join(physical.parts)] = canonical
        if aliases:
            runtime_name = "_internal_aliases"
            while f"{zone}/{runtime_name}.py" in plan.contents:
                runtime_name += "_"
            logical_storage = next(
                iter(_logical_storage_packages(plan, {PurePosixPath(zone)})),
                PurePosixPath(zone).name,
            )
            runtime = (Path(__file__).parents[1] / "runtime.py").read_bytes()
            _generated(
                plan, f"{zone}/{runtime_name}.py", runtime, "physical import identity router"
            )
            plan.contents[f"{zone}/__init__.py"] = (
                f"from .{runtime_name} import install_aliases as _install\n"
                f"_install(__name__, {dict(sorted(aliases.items()))!r}, {logical_storage!r})\n"
                "del _install\n"
            ).encode()


def _package(plan: Plan, name: str, root: str) -> None:
    files = [f for f in plan.inspection.files if f.owner == name and f.module]
    moving = [f for f in files if f.role == "source"]
    if not moving:
        return
    prefix = _prefix(plan, root)
    for record in moving:
        if record.is_package:
            # Keep package discovery and resource roots in wheels; the finder
            # executes the real initializer at its moved location, not this marker.
            _generated(plan, record.path, b"", "package resource directory marker")
    routes = {}
    for record in files:
        if record.module == name:
            continue
        relative = posixpath.relpath(plan.mapping[record.path], root)
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
    runtime = (Path(__file__).parents[1] / "runtime.py").read_bytes()
    _generated(plan, f"{root}/{runtime_name}.py", runtime, "self-contained logical module loader")
    _generated(
        plan,
        f"{root}/{route_name}.py",
        ("ROUTES = " + repr(dict(sorted(routes.items()))) + "\n").encode(),
        "logical module routing table",
    )
    entry = f"{root}/__init__.py"
    plan.contents[entry] = bootstrap(plan.contents[entry], runtime_name, route_name, prefix, name)
    plan.changes.append({"kind": "package-bootstrap", "path": entry})


def _scripts(plan: Plan) -> None:
    groups: dict[str, list[FileRecord]] = {}
    for record in plan.inspection.files:
        if record.role == "script":
            groups.setdefault(record.owner or ".", []).append(record)
    for root, files in sorted(groups.items()):
        for record in files:
            _generated(
                plan,
                record.path,
                script_entry(
                    record.path, plan.mapping[record.path], plan.contents[plan.mapping[record.path]]
                ),
                "stable script entry",
            )
            plan.entry_commands.append("python " + repr(record.path))


class PythonAdapter:
    name = "python"
    markers = frozenset({"pyproject.toml", "setup.py", "setup.cfg"})
    suffixes = frozenset({".py"})
    modes = frozenset({"flat", "random"})
    strategy = "preserve-logical-modules"
    static_checks = ("syntax-compilation", "logical-route-targets")

    def inspect(self, source, *, include=None, exclude=None, contents=None, source_dirs=None):
        return read_tree(
            source, include=include, exclude=exclude, _contents=contents, source_dirs=source_dirs
        )

    def explain(self, plan):
        plan.configuration["original_pythonpath"] = (
            ["src"]
            if any(f.module and f.path.startswith("src/") for f in plan.inspection.files)
            else []
        )
        return explain(plan)

    def render(self, plan, layout):
        _relocate(plan, layout)
        for name, root in plan.inspection.packages.items():
            _package(plan, name, root)
        _scripts(plan)
        include_storage_packages(plan)

    def validate(self, plan, changed):
        for path in changed:
            if path.endswith(".py"):
                compile(decode_python(plan.contents[path]), path, "exec")
