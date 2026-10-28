"""Byte-preserving Java relocation within Maven/Gradle source-set boundaries.

Java package declarations determine binary names. Standard recursive source
collection can compile files independently of their physical package paths.
No target scripts, effective POMs or Gradle models are executed during planning.
"""

from __future__ import annotations

import collections
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from ..contents import SnapshotContents, file_hash
from ..inventory import EXCLUDED_DIRS, _walk_tree, matches
from ..layout import NumberedLayout
from ..models import Diagnostic, FileRecord, Inspection, Plan, ShuffleError, SourceUnit
from ..transform import relocate, storage_prefix

_BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts"}
_SETTINGS = {"settings.gradle", "settings.gradle.kts"}
_ARTIFACTS = {"build", "target", "out"}
_AUXILIARY = {
    "test",
    "tests",
    "fixtures",
    "examples",
    "docs",
    "doc",
    "benchmarks",
    "buildSrc",
    "build-logic",
}


def _configuration(source: Path) -> list[str] | None:
    path = source / "reposhuffle.toml"
    if not path.is_file():
        return None
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ShuffleError(f"Invalid reposhuffle.toml: {exc}") from exc
    section = config.get("java", {})
    if not isinstance(section, dict):
        raise ShuffleError("java must be a table in reposhuffle.toml")
    roots = section.get("source-dirs")
    if roots is not None and (
        not isinstance(roots, list)
        or not roots
        or any(not isinstance(root, str) or not root or Path(root).is_absolute() for root in roots)
    ):
        raise ShuffleError("java.source-dirs must be a nonempty list of relative paths")
    return roots


def _inside(source: Path, project: Path, raw: str) -> str:
    raw = raw.replace("${project.basedir}", str(project)).replace("${basedir}", str(project))
    if "${" in raw:
        raise ValueError(f"Unresolved source directory: {raw}; configure java.source-dirs")
    path = (project / raw).resolve()
    if not path.is_relative_to(source) or path == source:
        raise ValueError(f"Source directory must be strictly inside the input: {raw}")
    return path.relative_to(source).as_posix()


def _projects(source: Path) -> tuple[dict[Path, str], list[str]]:
    """Find build boundaries, pruning caches/output before the content inventory."""
    projects = {}
    excluded = []
    for directory, dirs, files in os.walk(source, followlinks=False):
        current = Path(directory)
        names = set(files)
        if "pom.xml" in names and names.intersection(_BUILD_FILES - {"pom.xml"}):
            raise ShuffleError(f"Ambiguous Maven/Gradle project: {current.relative_to(source)}")
        if names.intersection(_BUILD_FILES | _SETTINGS):
            projects[current] = "maven" if "pom.xml" in names else "gradle"
        standard_root = (current / "src/main/java").is_dir()
        if current not in projects and standard_root:
            ancestors = [base for base in projects if current.is_relative_to(base)]
            if ancestors and projects[max(ancestors, key=lambda base: len(base.parts))] == "gradle":
                projects[current] = "gradle"
        unit = current in projects or standard_root
        kept = []
        for name in sorted(dirs):
            path = current / name
            if name in EXCLUDED_DIRS or (unit and name in _ARTIFACTS):
                if name in _ARTIFACTS:
                    excluded.append(path.relative_to(source).as_posix())
                continue
            if (
                name not in _AUXILIARY
                and not path.is_symlink()
                and not getattr(path, "is_junction", lambda: False)()
            ):
                kept.append(name)
        dirs[:] = kept
    return projects, sorted(excluded)


def _maven_root(project: Path, source: Path, configured: bool, result: Inspection) -> str:
    path = project / "pom.xml"
    relative = path.relative_to(source).as_posix()
    try:
        document = ET.fromstring(path.read_bytes())
        # Normalize namespace once so the rules apply to ordinary and fixture POMs.
        for element in document.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        for plugin in document.findall(".//plugin"):
            artifact = plugin.findtext("artifactId", "")
            if (
                artifact == "build-helper-maven-plugin"
                and not configured
                and any(goal.text == "add-source" for goal in plugin.findall(".//goal"))
            ):
                raise ValueError(
                    "Additional generated/custom source roots require java.source-dirs"
                )
        if not configured and document.find(".//profiles//sourceDirectory") is not None:
            raise ValueError("Profile-dependent source roots require java.source-dirs")
        raw = document.findtext("build/sourceDirectory", "src/main/java").strip()
        if not raw and not configured:
            raise ValueError("Empty Maven sourceDirectory; configure java.source-dirs")
        return _inside(source, project, raw) if not configured else ""
    except (ET.ParseError, ValueError) as exc:
        result.diagnostics.append(Diagnostic("JAVA_BUILD_CONFIGURATION", relative, str(exc)))
        return ""


def _ant_match(path: str, pattern: str) -> bool:
    if "${" in pattern or any(char in pattern for char in "[]\\"):
        raise ValueError(f"Unsupported compiler source filter: {pattern}")
    expression = ""
    parts = pattern.split("/")
    for index, part in enumerate(parts):
        if part == "**":
            expression += ".*" if index == len(parts) - 1 else "(?:[^/]+/)*"
        else:
            expression += "".join(
                "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
                for char in part
            )
            if index != len(parts) - 1:
                expression += "/"
    return re.fullmatch(expression, path) is not None


def _validate_maven_filters(plan: Plan) -> None:
    """Reject layouts that change declared compiler selection, not whole projects.

    Evaluate each compiler configuration independently, including inherited POM
    scopes. Test compiler filters are deliberately untouched.
    """
    units = [unit for unit in plan.inspection.source_units if unit.build_system == "maven"]
    for record in plan.inspection.files:
        if PurePosixPath(record.path).name != "pom.xml":
            continue
        project = PurePosixPath(record.path).parent
        affected = [unit for unit in units if PurePosixPath(unit.project).is_relative_to(project)]
        if not affected:
            continue
        document = ET.fromstring(plan.contents[record.path])
        for element in document.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        for plugin in document.findall(".//plugin"):
            if plugin.findtext("artifactId") != "maven-compiler-plugin":
                continue
            for configuration in plugin.findall(".//configuration"):
                includes = [
                    node.text.strip()
                    for node in configuration.findall("includes/include")
                    if node.text
                ]
                excludes = [
                    node.text.strip()
                    for node in configuration.findall("excludes/exclude")
                    if node.text
                ]
                if (
                    configuration.find("compilerIncludes") is not None
                    or configuration.find("compilerExcludes") is not None
                ):
                    raise ValueError(f"{record.path}: unsupported compiler source filters")
                if not includes and not excludes:
                    continue

                def selected(path, includes=includes, excludes=excludes):
                    return (
                        not includes or any(_ant_match(path, value) for value in includes)
                    ) and not any(_ant_match(path, value) for value in excludes)

                for unit in affected:
                    root = PurePosixPath(unit.root)
                    for original, target in plan.mapping.items():
                        before = PurePosixPath(original)
                        if original == target or not before.is_relative_to(root):
                            continue
                        old = before.relative_to(root).as_posix()
                        new = PurePosixPath(target).relative_to(root).as_posix()
                        if selected(old) != selected(new):
                            raise ValueError(
                                f"{record.path}: relocation changes compiler source selection for {original}; path filters are not rewritten"
                            )


class JavaAdapter:
    name = "java"
    markers = frozenset(_BUILD_FILES | _SETTINGS)
    suffixes = frozenset({".java"})
    modes = frozenset({"random"})
    strategy = "preserve-java-packages"
    static_checks = ("java-source-bytes", "java-source-filenames", "source-unit-boundaries")

    def inspect(
        self,
        source: Path,
        *,
        include=None,
        exclude=None,
        contents: SnapshotContents | None = None,
        source_dirs: list[str] | None = None,
    ) -> Inspection:
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise ShuffleError(f"Not a project directory: {source}")
        projects, exclusions = _projects(source)
        result = Inspection(source, language=self.name, inventory_excludes=exclusions)
        paths, ignored = _walk_tree(source, result)
        if not result.ok:
            return result
        configured = source_dirs if source_dirs is not None else _configuration(source)
        roots: dict[str, SourceUnit] = {}
        for project, system in sorted(projects.items()):
            root = ""
            if system == "maven":
                root = _maven_root(project, source, configured is not None, result)
            else:
                root = (project / "src/main/java").relative_to(source).as_posix()
                for name in ("build.gradle", "build.gradle.kts"):
                    build = project / name
                    if build.is_file():
                        text = build.read_text(encoding="utf-8-sig")
                        if configured is None and re.search(
                            r"\b(?:srcDir|srcDirs|setSrcDirs|setSource)\b", text
                        ):
                            result.diagnostics.append(
                                Diagnostic(
                                    "JAVA_BUILD_CONFIGURATION",
                                    build.relative_to(source).as_posix(),
                                    "Custom Gradle source collection requires java.source-dirs; "
                                    "dynamic DSL is not executed or rewritten.",
                                )
                            )
                        if re.search(r"\b(?:include|exclude)\s*\(?\s*['\"][^'\"]*\.java", text):
                            result.diagnostics.append(
                                Diagnostic(
                                    "JAVA_BUILD_CONFIGURATION",
                                    build.relative_to(source).as_posix(),
                                    "Path-filtered Java inputs are not safely adaptable statically.",
                                )
                            )
            if root and (source / root).is_dir() and configured is None:
                roots[root] = SourceUnit(
                    root, project.relative_to(source).as_posix(), "main", system
                )
        # Gradle subprojects may inherit all build configuration from their parent.
        for path in paths:
            relative = path.relative_to(source).as_posix()
            if not relative.endswith(".java") or "/src/main/java/" not in "/" + relative:
                continue
            prefix = ("/" + relative).split("/src/main/java/", 1)[0].lstrip("/")
            if _AUXILIARY.intersection(PurePosixPath(prefix).parts):
                continue
            project = source / prefix
            ancestors = [
                (base, system) for base, system in projects.items() if project.is_relative_to(base)
            ]
            if ancestors:
                _, system = max(ancestors, key=lambda item: len(item[0].parts))
                if system == "gradle" and configured is None:
                    root = str(PurePosixPath(prefix) / "src/main/java")
                    roots.setdefault(root, SourceUnit(root, prefix or ".", "main", system))
        if configured is not None:
            roots = {}
            for raw in configured:
                try:
                    root = _inside(source, source, raw)
                    if not (source / root).is_dir():
                        raise ValueError(f"Configured source directory does not exist: {raw}")
                    ancestors = [
                        (base, system)
                        for base, system in projects.items()
                        if (source / root).is_relative_to(base)
                    ]
                    if not ancestors:
                        raise ValueError(
                            f"No Maven/Gradle build boundary for source directory: {raw}"
                        )
                    project, system = max(ancestors, key=lambda item: len(item[0].parts))
                    if any(
                        part in {"test", "tests", "resources", "generated", "build", "target"}
                        for part in PurePosixPath(root).parts
                    ):
                        raise ValueError(
                            f"Test/resource/generated roots cannot be production inputs: {raw}"
                        )
                    roots[root] = SourceUnit(
                        root, project.relative_to(source).as_posix(), "production", system
                    )
                except ValueError as exc:
                    result.diagnostics.append(
                        Diagnostic("JAVA_SOURCE_ROOT", "reposhuffle.toml", str(exc))
                    )
        result.source_units = sorted(roots.values(), key=lambda unit: unit.root)
        for root in roots:
            if any(
                root != other and PurePosixPath(root).is_relative_to(PurePosixPath(other))
                for other in roots
            ):
                result.diagnostics.append(
                    Diagnostic("JAVA_SOURCE_ROOT", root, "Source roots must not overlap")
                )
        seen = {directory.casefold(): directory for directory in result.directories}
        if len(seen) != len(result.directories):
            result.diagnostics.append(Diagnostic("CASE_COLLISION", ".", "Directory case collision"))
        for path in paths:
            relative = path.relative_to(source).as_posix()
            if relative.casefold() in seen:
                result.diagnostics.append(
                    Diagnostic(
                        "CASE_COLLISION", relative, f"Conflicts with {seen[relative.casefold()]}"
                    )
                )
            seen[relative.casefold()] = relative
            sha256, size = file_hash(path)
            if contents is not None:
                contents.defer(relative, path, sha256)
            owners = [
                root
                for root in roots
                if PurePosixPath(relative).is_relative_to(PurePosixPath(root))
            ]
            owner = max(owners, key=len) if owners else None
            role, reason = "preserved", "auxiliary or unselected language file"
            if path.suffix == ".java" and owner:
                role, reason = "source", "Java production source set"
                project_parts = PurePosixPath(relative).relative_to(roots[owner].project).parts
                if (
                    project_parts[0] in _AUXILIARY
                    or (
                        len(project_parts) > 1
                        and project_parts[0] == "src"
                        and project_parts[1]
                        in {
                            "test",
                            "testFixtures",
                            "integrationTest",
                            "componentTest",
                            "functionalTest",
                            "acceptanceTest",
                            "it",
                        }
                    )
                    or (
                        len(project_parts) > 2
                        and project_parts[0] == "src"
                        and project_parts[2] == "resources"
                    )
                ):
                    role, reason = "preserved", "test/resource/auxiliary source scope"
                if path.name in {"module-info.java", "package-info.java"}:
                    role, reason = "preserved", "Java module/package descriptor anchor"
                if include and not matches(relative, include):
                    role, reason = "preserved", "outside explicit include selection"
                if exclude and matches(relative, exclude):
                    role, reason = "preserved", "explicitly excluded from relocation"
            if relative in ignored:
                role, reason = "preserved", "matched .gitignore rule"
            result.files.append(
                FileRecord(relative, sha256, size, role=role, owner=owner, reason=reason)
            )
        if not any(record.role == "source" for record in result.files):
            result.diagnostics.append(
                Diagnostic("NO_IMPLEMENTATION", ".", "No movable Java production sources found")
            )
        if result.source_units:
            result.diagnostics.append(
                Diagnostic(
                    "JAVA_BUILD_ASSUMPTIONS",
                    ".",
                    "Requires recursive source collection independent of package paths. "
                    "Custom plugins, IDE package-path checks, source JARs, annotation processing "
                    "and JPMS need build/runtime verification; no Java compilation was run.",
                    severity="warning",
                )
            )
        return result

    def explain(self, plan: Plan) -> dict:
        candidates = [record for record in plan.inspection.files if record.path.endswith(".java")]
        moved = sum(record.role == "source" for record in candidates)
        return {
            "validation": "static-only",
            "runtime": "not-run",
            "build": {
                "systems": sorted({unit.build_system for unit in plan.inspection.source_units}),
                "artifact_test": "not-run",
                "source_collection": "recursive-required",
            },
            "imports": {
                "logical_names": "unchanged",
                "package_declarations": "unchanged",
                "runtime_loader": "none",
            },
            "selection": {
                "java_files": len(candidates),
                "movable_files": moved,
                "movable_ratio": round(moved / len(candidates), 4) if candidates else 0,
                "preserved_by_reason": dict(
                    collections.Counter(
                        record.reason
                        for record in plan.inspection.files
                        if record.role == "preserved"
                    )
                ),
            },
            "risks": dict(
                collections.Counter(
                    d.rule for d in plan.inspection.diagnostics if d.severity == "warning"
                )
            ),
        }

    def render(self, plan: Plan, layout: NumberedLayout) -> None:
        for unit in plan.inspection.source_units:
            records = [
                record
                for record in plan.inspection.files
                if record.role == "source" and record.owner == unit.root
            ]
            if not records:
                continue
            zone = str(PurePosixPath(unit.root) / (storage_prefix(plan, unit.root) + "_code"))
            targets = layout.targets(
                zone, {record.path: PurePosixPath(record.path).name for record in records}
            )
            relocate(plan, {record.path: targets[record.path] for record in records}, zone)

    def validate(self, plan: Plan, changed: set[str]) -> None:
        _validate_maven_filters(plan)
        hashes = dict(plan._content_hashes())
        for record in plan.inspection.files:
            if record.role != "source":
                continue
            target = plan.mapping[record.path]
            if PurePosixPath(target).name != PurePosixPath(record.path).name:
                raise ShuffleError(f"Java source filename changed: {record.path}")
            if not PurePosixPath(target).is_relative_to(PurePosixPath(record.owner)):
                raise ShuffleError(f"Java source crossed a build boundary: {record.path}")
            if hashes[target] != record.sha256:
                raise ShuffleError(f"Java source bytes changed: {record.path}")
