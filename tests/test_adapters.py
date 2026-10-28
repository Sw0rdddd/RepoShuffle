from dataclasses import replace

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan, inspect_project
from reposhuffle.inventory import read_inventory
from reposhuffle.layout import NumberedLayout
from reposhuffle.models import SourceUnit
from reposhuffle.transform import relocate


def test_shared_layout_golden_preserves_seed_consumption():
    assert NumberedLayout(42, 3, 0.7).targets(
        "store", {"a": "Main.java", "b": "Main.java", "c": "Other.java"}
    ) == {"b": "store/Main.java", "a": "store/module_1/Main.java", "c": "store/module_1/Other.java"}


def test_pipeline_and_publisher_do_not_require_python_semantics(project, monkeypatch):
    """Exercise a third adapter through the actual shared pipeline, not a second tool."""
    from reposhuffle import plan as pipeline

    class TextAdapter:
        name = "text-fixture"
        modes = frozenset({"random"})
        strategy = "unchanged-bytes"
        static_checks = ("fixture-bytes",)

        def inspect(self, source, *, include, exclude, contents, source_dirs):
            inspection = read_inventory(source)
            inspection.language = self.name
            inspection.source_units = [SourceUnit("code", build_system="fixture")]
            inspection.files = [
                replace(record, role="source", owner="code") for record in inspection.files
            ]
            for record in inspection.files:
                contents.defer(record.path, source / record.path, record.sha256)
            return inspection

        def explain(self, plan):
            return {"runtime": "not-run"}

        def render(self, plan, layout):
            relocate(
                plan, layout.targets("code/store", {"code/value.txt": "value.txt"}), "code/store"
            )

        def validate(self, plan, changed):
            assert changed == {"code/store/value.txt"}
            assert plan.contents["code/store/value.txt"] == b"value"

    source = project({"code/value.txt": "value"})
    monkeypatch.setattr(pipeline, "get_adapter", lambda source, language: TextAdapter())
    plan = build_plan(source, language="text-fixture", seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.modules == {}
    assert plan.static_checks == [
        "fixture-bytes",
        "unique-portable-targets",
        "preserved-auxiliary-content",
    ]
    output = apply_plan(plan)
    assert (output / "shuffled/code/store/value.txt").read_bytes() == b"value"
    assert not (output / "shuffled/code/value.txt").exists()


@pytest.mark.parametrize("value", [[], "src", [None], [""]])
def test_source_dirs_are_validated_for_discovery_and_planning(project, value):
    source = project({"main.py": "pass\n"})
    for operation in (build_plan, inspect_project):
        with pytest.raises(ShuffleError, match="source_dirs"):
            operation(source, source_dirs=value)


@pytest.mark.parametrize("language", ["python", "java"])
def test_storage_prefix_avoids_case_insensitive_files_and_directories(project, language):
    if language == "java":
        root = "src/main/java"
        files = {
            "pom.xml": "<project/>",
            f"{root}/demo/Main.java": "package demo; public class Main {}",
        }
    else:
        root = "demo"
        files = {"demo/__init__.py": "", "demo/main.py": "VALUE=42\n"}
    files[f"{root}/_INTERNAL_code/data.txt"] = "keep"
    files[f"{root}/_Internal1_runtime.py"] = "keep"
    source = project(files)
    plan = build_plan(source, language=language, seed=42)
    assert plan.ok, plan.to_dict()
    assert all("/_internal2_code" in zone for zone in plan.storage_roots.values())
    output = apply_plan(plan)
    assert (output / "shuffled" / root / "_INTERNAL_code/data.txt").read_text() == "keep"


@pytest.mark.parametrize("conflict", ["directory-case", "file-directory"])
def test_shared_validation_checks_target_directories(project, monkeypatch, conflict):
    source = project(
        {
            "pom.xml": "<project/>",
            "src/main/java/demo/A.java": "package demo; class A {}",
            "src/main/java/demo/B.java": "package demo; class B {}",
        }
    )

    def targets(self, zone, names):
        if conflict == "directory-case":
            return {
                old: f"{zone}/{'Module' if name == 'A.java' else 'module'}/{name}"
                for old, name in names.items()
            }
        return {
            old: f"{zone}/{'A.java/' if name == 'B.java' else ''}{name}"
            for old, name in names.items()
        }

    monkeypatch.setattr(NumberedLayout, "targets", targets)
    with pytest.raises(ShuffleError, match="collision"):
        build_plan(source, seed=42)


def test_language_selection_does_not_execute_build_files(project):
    source = project(
        {
            "build.gradle": "throw new RuntimeException('must not run')",
            "src/main/java/Demo.java": "public class Demo {}",
        }
    )
    assert build_plan(source).ok
    with pytest.raises(ShuffleError, match="Unknown language"):
        build_plan(source, language="unknown")
