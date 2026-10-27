"""Discover source boundaries from standard packaging layouts, not project names."""

from __future__ import annotations

import fnmatch
import keyword
from pathlib import Path, PurePosixPath

import libcst as cst

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from .models import Diagnostic, Plan, ShuffleError

PRESERVED_DIRS = {
    "tests",
    "test",
    "docs",
    "doc",
    "examples",
    "assets",
    "fixtures",
    "data",
    "benchmarks",
    "questions",
    "repos",
    "node_modules",
    "build",
    "dist",
    "scripts",
    "tools",
    "venv",
    "env",
}


def identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def read_config(source: Path) -> dict:
    path = source / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ShuffleError(f"Invalid pyproject.toml: {exc}") from exc


def discover(
    source: Path, source_dirs: list[str] | None = None
) -> tuple[dict[str, str], list[str], dict[str, str], dict[str, str]]:
    """Return public packages, script areas, metadata anchors and source boundaries."""
    config = read_config(source)
    tools = config.get("tool", {})
    setuptools = tools.get("setuptools", {})
    declared = setuptools.get("packages", {})
    find = declared.get("find", {}) if isinstance(declared, dict) else {}
    package_dirs = setuptools.get("package-dir", {})
    mapped = {}
    for name, raw in package_dirs.items():
        if not name:
            continue
        path = (source / raw).resolve()
        if not path.is_relative_to(source) or not path.is_dir():
            raise ShuffleError(f"Package directory must exist inside the project: {raw}")
        mapped[path] = name
    configured_where = list(find.get("where", [])) if isinstance(find, dict) else []
    explicit = tools.get("poetry", {}).get("packages", [])
    if configured_where:
        # An explicit setuptools source root is authoritative. Adding the
        # default root as well would expose the same directory as both
        # `pkg` and a container-prefixed `lib.pkg`.
        bases = configured_where
    elif package_dirs:
        default = package_dirs.get("")
        bases = [default] if default else []
    elif explicit:
        bases = [item.get("from", ".") for item in explicit]
    else:
        bases = ["src" if (source / "src").is_dir() else "."]
    requested = source_dirs or tools.get("reposhuffle", {}).get("source-dirs")
    packages: dict[str, str] = {}
    source_roots: dict[str, str] = {}
    scripts: list[str] = []

    def package(path: Path, logical: str, area: Path) -> None:
        rel = path.relative_to(source).as_posix()
        previous = packages.get(logical)
        if previous is not None and previous != rel:
            raise ShuffleError(f"Ambiguous package {logical}: {previous}, {rel}")
        packages[logical] = rel
        source_roots[logical] = area.relative_to(source).as_posix()

    def walk_packages(base: Path, prefix: str = "", area: Path | None = None) -> None:
        area = base if area is None else area
        if not base.is_dir():
            return
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.is_symlink() or child.name.startswith("."):
                continue
            if not prefix and child.name in PRESERVED_DIRS:
                continue
            if not identifier(child.name):
                continue
            name = mapped.get(child.resolve(), prefix + child.name)
            if (child / "__init__.py").is_file():
                package(child, name, area)
            else:
                # Namespace ancestors remain namespaces; instrument their real packages.
                walk_packages(child, name + ".", area)

    if requested:
        for raw in requested:
            path = (source / raw).resolve()
            if not path.is_relative_to(source) or not path.is_dir():
                raise ShuffleError(f"Source directory must exist inside the project: {raw}")
            if (path / "__init__.py").is_file():
                if not identifier(path.name):
                    raise ShuffleError(f"Invalid package directory: {raw}")
                package(path, path.name, path)
            elif path.name in {"src", "."} or path == source:
                walk_packages(path)
                scripts.append(path.relative_to(source).as_posix())
            else:
                walk_packages(path)
                scripts.append(path.relative_to(source).as_posix())
    else:
        for path, name in mapped.items():
            if (path / "__init__.py").is_file():
                package(path, name, path)
            else:
                walk_packages(path, name + ".")
        roots: list[Path] = []
        for base in dict.fromkeys(bases):
            path = (source / base).resolve()
            if not path.is_relative_to(source):
                raise ShuffleError("Packaging source root escapes the project")
            roots.append(path)
        roots.sort(key=lambda path: len(path.parts))
        selected: list[Path] = []
        for path in roots:
            if any(path.is_relative_to(root) for root in selected):
                continue
            selected.append(path)
        for path in selected:
            if path not in mapped:
                walk_packages(path)
        scripts = ["."] + [
            path.name
            for path in sorted(source.iterdir())
            if path.is_dir()
            and not path.is_symlink()
            and not path.name.startswith(".")
            and path.name not in (PRESERVED_DIRS - {"scripts", "tools"})
        ]

    # Static build frontends read these modules without importing dependencies.
    anchors: dict[str, str] = {}
    dynamic = setuptools.get("dynamic", {})
    for value in dynamic.values():
        attr = value.get("attr") if isinstance(value, dict) else None
        if isinstance(attr, str):
            anchors[attr.rpartition(".")[0]] = "build metadata attribute"
    if isinstance(declared, list):
        allowed = set(declared)
        packages = {
            name: path
            for name, path in packages.items()
            if name in allowed or any(p.startswith(name + ".") for p in allowed)
        }
    includes = find.get("include")
    excludes = find.get("exclude", [])
    if includes:
        packages = {
            n: p
            for n, p in packages.items()
            if any(
                fnmatch.fnmatchcase(n, pat) or fnmatch.fnmatchcase(n + ".child", pat)
                for pat in includes
            )
        }
    packages = {
        n: p
        for n, p in packages.items()
        if not any(fnmatch.fnmatchcase(n, pat) for pat in excludes)
    }
    return (
        dict(sorted(packages.items())),
        sorted(set(scripts)),
        anchors,
        {name: source_roots[name] for name in sorted(packages)},
    )


def _storage_directories(plan: Plan) -> set[PurePosixPath]:
    return {
        PurePosixPath(path).parent
        for path, reason in plan.generated.items()
        if reason == "implementation storage package"
    }


def _logical_storage_packages(plan: Plan, storage: set[PurePosixPath]) -> set[str]:
    additions = set()
    for directory in storage:
        owners = [
            (name, PurePosixPath(root))
            for name, root in plan.inspection.packages.items()
            if directory.is_relative_to(root)
        ]
        if owners:
            name, root = max(owners, key=lambda item: len(item[1].parts))
            additions.add(".".join((name, *directory.relative_to(root).parts)))
        else:
            bases = [
                PurePosixPath(base)
                for base in plan.inspection.source_roots.values()
                if directory.is_relative_to(PurePosixPath(base))
            ]
            if bases:
                base = max(bases, key=lambda path: len(path.parts))
                name, root = next(
                    (name, PurePosixPath(root))
                    for name, root in plan.inspection.packages.items()
                    if plan.inspection.source_roots[name] == str(base)
                )
                suffix = root.relative_to(base).parts
                namespace = name.split(".")[: -len(suffix)] if suffix else name.split(".")
                additions.add(".".join((*namespace, *directory.relative_to(base).parts)))
    return additions


def _include_setup_packages(plan: Plan, additions: set[str]) -> None:
    if not additions or "setup.py" not in plan.contents:
        return
    data = plan.contents["setup.py"]
    try:
        module = cst.parse_module(data)
    except (cst.ParserSyntaxError, UnicodeError) as exc:
        plan.diagnostics.append(Diagnostic("SETUP_SYNTAX", "setup.py", str(exc)))
        return
    name = "_reposhuffle_storage_packages"
    while name in data.decode("utf-8"):
        name += "_"
    changed = False

    class SetupTransformer(cst.CSTTransformer):
        def leave_Call(self, original_node, updated_node):
            nonlocal changed
            if not (
                isinstance(original_node.func, cst.Name) and original_node.func.value == "setup"
            ):
                return updated_node
            arguments = []
            for argument in updated_node.args:
                if argument.keyword and argument.keyword.value == "packages":
                    value = cst.BinaryOperation(
                        left=cst.parse_expression(f"({module.code_for_node(argument.value)})"),
                        operator=cst.Add(),
                        right=cst.Name(name),
                    )
                    argument = argument.with_changes(value=value)
                    changed = True
                arguments.append(argument)
            return updated_node.with_changes(args=arguments)

    transformed = module.visit(SetupTransformer())
    if not changed:
        return
    assignment = cst.parse_statement(f"{name} = {sorted(additions)!r}\n")
    body = list(transformed.body)
    offset = 0
    if body and isinstance(body[0], cst.SimpleStatementLine):
        first = body[0].body[0]
        if isinstance(first, cst.Expr) and isinstance(
            first.value, (cst.SimpleString, cst.ConcatenatedString)
        ):
            offset = 1
    while (
        offset < len(body)
        and isinstance(body[offset], cst.SimpleStatementLine)
        and all(
            isinstance(node, cst.ImportFrom)
            and cst.helpers.get_full_name_for_node(node.module) == "__future__"
            for node in body[offset].body
        )
    ):
        offset += 1
    plan.contents["setup.py"] = transformed.with_changes(
        body=body[:offset] + [assignment] + body[offset:]
    ).bytes
    plan.changes.append({"kind": "setup-configuration", "path": "setup.py"})


def include_storage_packages(plan: Plan) -> None:
    """Extend explicit setuptools lists; automatic discovery already sees storage."""
    config = read_config(plan.inspection.source)
    backend = config.get("build-system", {}).get("build-backend", "setuptools.build_meta")
    settings = config.get("tool", {}).get("setuptools", {})
    declared = settings.get("packages")
    modules = settings.get("py-modules", [])
    storage = _storage_directories(plan)
    additions = _logical_storage_packages(plan, storage)
    if backend.startswith("setuptools.") and (isinstance(declared, list) or modules):
        base = PurePosixPath(settings.get("package-dir", {}).get("", "."))
        for module in modules:
            source = str(base / (module.replace(".", "/") + ".py"))
            target = PurePosixPath(plan.mapping.get(source, source))
            ancestors = [directory for directory in storage if target.is_relative_to(directory)]
            if not ancestors:
                continue
            zone = min(ancestors, key=lambda path: len(path.parts))
            additions.update(
                ".".join(directory.relative_to(base).parts)
                for directory in storage
                if directory.is_relative_to(zone) and directory.is_relative_to(base)
            )
    _include_setup_packages(plan, additions)
    if not additions or not backend.startswith("setuptools."):
        return
    restricted_finder = isinstance(declared, dict) and bool(declared.get("find", {}).get("include"))
    if not (isinstance(declared, list) or modules or restricted_finder):
        return
    import tomlkit

    document = tomlkit.parse(plan.contents["pyproject.toml"].decode("utf-8"))
    table = document["tool"]["setuptools"]
    if isinstance(declared, dict):
        # Preserve an existing finder when py-modules and package discovery coexist.
        finder = table["packages"]["find"]
        if "include" not in finder:
            finder["include"] = ["*"]
        patterns = finder["include"]
        for name in sorted(additions):
            patterns.append(name)
    else:
        if "packages" not in table:
            table["packages"] = []
        packages = table["packages"]
        for name in sorted(additions - set(declared or [])):
            packages.append(name)
    plan.contents["pyproject.toml"] = tomlkit.dumps(document).encode("utf-8")
    plan.changes.append({"kind": "build-configuration", "path": "pyproject.toml"})
