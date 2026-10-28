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
