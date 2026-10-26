"""Discover source boundaries from standard packaging layouts, not project names."""

from __future__ import annotations

import fnmatch
import keyword
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from .models import Plan, ShuffleError

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
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """Return public package roots, script areas and build-metadata anchors."""
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
    bases = list(find.get("where", []))
    bases += [
        setuptools.get("package-dir", {}).get("", "src" if (source / "src").is_dir() else ".")
    ]
    explicit = tools.get("poetry", {}).get("packages", [])
    bases += [item.get("from", ".") for item in explicit]
    requested = source_dirs or tools.get("reposhuffle", {}).get("source-dirs")
    packages: dict[str, str] = {}
    scripts: list[str] = []

    def package(path: Path, logical: str) -> None:
        rel = path.relative_to(source).as_posix()
        previous = packages.get(logical)
        if previous is not None and previous != rel:
            raise ShuffleError(f"Ambiguous package {logical}: {previous}, {rel}")
        packages[logical] = rel

    def walk_packages(base: Path, prefix: str = "") -> None:
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
                package(child, name)
            else:
                # Namespace ancestors remain namespaces; instrument their real packages.
                walk_packages(child, name + ".")

    if requested:
        for raw in requested:
            path = (source / raw).resolve()
            if not path.is_relative_to(source) or not path.is_dir():
                raise ShuffleError(f"Source directory must exist inside the project: {raw}")
            if (path / "__init__.py").is_file():
                if not identifier(path.name):
                    raise ShuffleError(f"Invalid package directory: {raw}")
                package(path, path.name)
            elif path.name in {"src", "."} or path == source:
                walk_packages(path)
                scripts.append(path.relative_to(source).as_posix())
            else:
                walk_packages(path)
                scripts.append(path.relative_to(source).as_posix())
    else:
        for path, name in mapped.items():
            if (path / "__init__.py").is_file():
                package(path, name)
            else:
                walk_packages(path, name + ".")
        for base in dict.fromkeys(bases):
            path = (source / base).resolve()
            if not path.is_relative_to(source):
                raise ShuffleError("Packaging source root escapes the project")
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
    return dict(sorted(packages.items())), sorted(set(scripts)), anchors


def include_storage_packages(plan: Plan) -> None:
    """Extend explicit setuptools lists; automatic discovery already sees storage."""
    config = read_config(plan.inspection.source)
    backend = config.get("build-system", {}).get("build-backend", "setuptools.build_meta")
    settings = config.get("tool", {}).get("setuptools", {})
    declared = settings.get("packages")
    modules = settings.get("py-modules", [])
    if not backend.startswith("setuptools.") or not (isinstance(declared, list) or modules):
        return
    storage = {
        PurePosixPath(path).parent
        for path, reason in plan.generated.items()
        if reason == "implementation storage package"
    }
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
    if not additions:
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
