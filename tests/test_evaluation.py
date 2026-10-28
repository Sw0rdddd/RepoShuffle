from __future__ import annotations

import json
from pathlib import Path

import pytest

from reposhuffle import __version__
from scripts import evaluate as harness
from scripts.evaluate import compare, junit_result


def test_junit_keeps_duplicate_nodes_and_collection_changes(tmp_path):
    xml = tmp_path / "tests.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.demo" name="same"/>'
        '<testcase classname="tests.demo" name="same"><failure/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    original = {"complete": True, "cases": {"tests.demo::same": ["passed"]}}
    result = junit_result(xml, 1)
    assert result["cases"]["tests.demo::same"] == ["passed", "failure"]
    assert compare(original, result)["new_failures"] == ["tests.demo::same"]
    assert not junit_result(xml, 3221225725)["complete"]
    assert not compare(original, junit_result(xml, None))["comparable"]


def test_snapshot_parameter_paths_are_normalized_exactly(tmp_path):
    xml = tmp_path / "tests.xml"
    snapshot = tmp_path / "original"
    xml.write_text(
        f'<testsuite><testcase classname="demo" name="test[{snapshot}/file.py]"/></testsuite>',
        encoding="utf-8",
    )
    result = junit_result(xml, 0, snapshot)
    assert "demo::test[<snapshot>/file.py]" in result["cases"]


def test_moved_doctest_classname_uses_exact_module_mapping(tmp_path):
    xml = tmp_path / "tests.xml"
    xml.write_text(
        '<testsuite><testcase classname="demo._code.worker" name="demo.worker.function"/>'
        '<testcase classname="new.module" name="test_new"/></testsuite>',
        encoding="utf-8",
    )
    result = junit_result(xml, 0, module_aliases={"demo._code.worker": "demo.worker"})
    assert "demo.worker::demo.worker.function" in result["cases"]
    assert "new.module::test_new" in result["cases"]


def test_acceptable_requires_baseline_artifacts_and_complete_comparison():
    snapshot = {
        "exit": 0,
        "source_import": {"exit": 0},
        **{name: {"exit": 0} for name in ("wheel", "install", "installed_import")},
    }
    comparison = {
        "comparable": True,
        **{
            key: []
            for key in (
                "added_nodes",
                "removed_nodes",
                "changed_nodes",
                "new_failures",
            )
        },
    }
    report = {
        "setup": {"exit": 0},
        "original": snapshot,
        "variants": {"flat-42": {"plan_ok": True, "tests": snapshot, "comparison": comparison}},
    }
    assert harness.acceptable(report)
    report["original"] = {**snapshot, "wheel": {"exit": 1}}
    assert not harness.acceptable(report)
    report["original"] = snapshot
    report["variants"]["flat-42"]["comparison"] = {"comparable": False}
    assert not harness.acceptable(report)


def test_initialize_submodules_only_when_declared(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        harness.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    harness.initialize_submodules(tmp_path, 30)
    assert not calls
    (tmp_path / ".gitmodules").write_text('[submodule "fixtures"]\n', encoding="utf-8")
    harness.initialize_submodules(tmp_path, 30)
    assert calls == [
        (
            (["git", "-C", str(tmp_path), "submodule", "update", "--init", "--recursive"],),
            {"check": True, "timeout": 30},
        )
    ]


def test_evaluation_isolates_pytest_collection_root(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "demo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (source / "pkg" / "worker.py").write_text("value = 7\n", encoding="utf-8")
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps(
            {
                "demo": {
                    "revision": "a" * 40,
                    "module": "pkg",
                    "tests": ["tests"],
                    "dependencies": [],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        harness.subprocess,
        "check_output",
        lambda command, **kwargs: "a" * 40 if command[-1] == "HEAD" else "",
    )
    monkeypatch.setattr(harness.venv.EnvBuilder, "create", lambda *args: None)
    snapshots = []

    def run(command, cwd, log, env, timeout):
        if "pytest" in command:
            assert f"--rootdir={cwd}" in command
            assert f"--confcutdir={cwd}" in command
            snapshots.append(cwd.name)
            xml = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
            Path(xml).write_text(
                '<testsuite><testcase classname="tests.demo" name="test_value"/></testsuite>',
                encoding="utf-8",
            )
        return {"exit": 0, "timeout": False, "seconds": 0}

    monkeypatch.setattr(harness, "run", run)
    assert (
        harness.main(
            [
                "--profiles",
                str(profiles),
                "--repos-root",
                str(tmp_path / "sources"),
                "--output",
                str(tmp_path / "results"),
                "--modes",
                "flat",
                "--seeds",
                "42",
            ]
        )
        == 0
    )
    assert snapshots == ["original", "shuffled"]


def test_append_keeps_prior_results_and_never_replaces_projects(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps({name: {"revision": name * 40} for name in ("a", "b")}), encoding="utf-8"
    )
    output = tmp_path / "report"
    output.mkdir()
    before = {"revision": "a" * 40, "tool_version": __version__}
    (output / "summary.json").write_text(json.dumps({"a": before}), encoding="utf-8")

    def evaluate(name, profile, args):
        assert args.repos_root == Path("repos/python/sources")
        (args.output / name).mkdir()
        return {"revision": profile["revision"], "tool_version": __version__}

    monkeypatch.setattr(harness, "evaluate", evaluate)
    monkeypatch.setattr(harness, "acceptable", lambda report: True)
    command = ["--profiles", str(profiles), "--output", str(output), "--projects", "b", "--append"]
    assert harness.main(command) == 0
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result["a"] == before
    assert set(result) == {"a", "b"}
    with pytest.raises(SystemExit):
        harness.main(command)


def test_append_rejects_missing_or_stale_summary(tmp_path):
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"a": {"revision": "a" * 40}}), encoding="utf-8")
    output = tmp_path / "report"
    command = ["--profiles", str(profiles), "--output", str(output), "--append"]
    with pytest.raises(SystemExit):
        harness.main(command)
    output.mkdir()
    (output / "summary.json").write_text(
        json.dumps({"a": {"revision": "a" * 40, "tool_version": "old"}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        harness.main(command)


def test_java_merges_report_scopes_without_collapsing_duplicate_nodes(tmp_path):
    paths = []
    for module in ("a", "b"):
        path = tmp_path / module / "target/surefire-reports/TEST-demo.xml"
        path.parent.mkdir(parents=True)
        path.write_text(
            '<testsuite><testcase classname="demo.Test" name="same"/>'
            '<testcase classname="demo.Test" name="same"/></testsuite>',
            encoding="utf-8",
        )
        paths.append(path)
    result = harness.junit_results(paths, 0, tmp_path)
    assert result["complete"]
    assert result["counts"] == {"passed": 4}
    assert len(result["cases"]) == 2
    assert all(outcomes == ["passed", "passed"] for outcomes in result["cases"].values())
    assert not harness.junit_results([], 0, tmp_path)["complete"]
    assert not harness.junit_results(paths, 2, tmp_path)["complete"]


def test_java_evaluation_uses_shared_pairing_and_strict_artifacts(tmp_path, monkeypatch):
    import zipfile

    source = tmp_path / "sources/demo"
    (source / "src/main/java/demo").mkdir(parents=True)
    (source / "src/main/java/demo/Main.java").write_text(
        "package demo; public class Main {}", encoding="utf-8"
    )
    (source / "pom.xml").write_text("<project/>", encoding="utf-8")
    profile = {
        "language": "java",
        "revision": "a" * 40,
        "jdk": 17,
        "build_system": "maven",
        "build_version": "3.9.9",
        "tasks": ["clean", "verify"],
        "reports": ["target/surefire-reports/TEST-*.xml"],
        "artifacts": ["target/*.jar"],
    }
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"demo": profile}), encoding="utf-8")
    monkeypatch.setattr(
        harness.subprocess,
        "check_output",
        lambda command, **kwargs: "a" * 40 if command[-1] == "HEAD" else "",
    )
    monkeypatch.setattr(harness.shutil, "which", lambda command: "mvn")
    commands = []

    def run(command, cwd, log, env, timeout):
        if command == ["java", "-version"]:
            log.write_text('openjdk version "17.0.12"', encoding="utf-8")
        elif command[-1] == "--version":
            log.write_text("Apache Maven 3.9.9", encoding="utf-8")
        else:
            commands.append(command)
            assert command[-2:] == ["clean", "verify"]
            xml = cwd / "target/surefire-reports/TEST-demo.xml"
            xml.parent.mkdir(parents=True)
            xml.write_text(
                '<testsuite><testcase classname="demo.Test" name="value"/></testsuite>',
                encoding="utf-8",
            )
            with zipfile.ZipFile(cwd / "target/demo.jar", "w") as archive:
                archive.writestr("demo/Main.class", b"class-fixture")
            inputs = (
                cwd
                / "target/maven-status/maven-compiler-plugin/compile/default-compile/inputFiles.lst"
            )
            inputs.parent.mkdir(parents=True)
            inputs.write_text(
                "\n".join(str(path.resolve()) for path in cwd.glob("src/main/java/**/*.java")),
                encoding="utf-8",
            )
        return {"exit": 0, "timeout": False, "seconds": 0}

    monkeypatch.setattr(harness, "run", run)
    output = tmp_path / "results"
    assert (
        harness.main(
            [
                "--profiles",
                str(profiles),
                "--repos-root",
                str(tmp_path / "sources"),
                "--output",
                str(output),
                "--seeds",
                "42",
                "123",
            ]
        )
        == 0
    )
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))["demo"]
    assert list(report["variants"]) == ["random-42", "random-123"]
    assert len(commands) == 3
    assert harness.acceptable(report)
    assert list((output / "demo/original-reports").rglob("TEST-*.xml"))
    report["variants"]["random-42"]["comparison"]["artifact_classes_equal"] = False
    assert not harness.acceptable(report)
    report["variants"] = {}
    assert not harness.acceptable(report)


@pytest.mark.parametrize("system", ["maven", "gradle"])
def test_compiler_inputs_record_actual_paths_not_class_filename_guesses(tmp_path, system):
    snapshot = tmp_path / "snapshot"
    source = snapshot / "src/main/java/UnexpectedFilename.java"
    source.parent.mkdir(parents=True)
    source.write_text("class EntirelyDifferentBinaryName {}", encoding="utf-8")
    if system == "maven":
        report = (
            snapshot
            / "target/maven-status/maven-compiler-plugin/compile/default-compile/inputFiles.lst"
        )
        payload = str(source.resolve()) + "\n"
    else:
        report = tmp_path / "traces/compile-one.json"
        payload = json.dumps([str(source.resolve())])
    report.parent.mkdir(parents=True)
    report.write_text(payload, encoding="utf-8")
    assert harness.compiler_inputs(snapshot, system, tmp_path / "traces")["files"] == [
        "src/main/java/UnexpectedFilename.java"
    ]
    if system == "maven":
        assert (tmp_path / "traces" / report.relative_to(snapshot)).read_text(
            encoding="utf-8"
        ) == payload
    report.unlink()
    assert harness.compiler_inputs(snapshot, system, tmp_path / "traces")["exit"] == 1


@pytest.mark.parametrize("missing", ["original", "shuffled"])
def test_java_acceptance_rejects_uncompiled_moves_despite_matching_jar_and_tests(missing):
    original_path = "src/main/java/excluded/Unused.java"
    target = "src/main/java/_internal_code/Unused.java"
    mapping = {original_path: target}
    original = {
        "exit": 0,
        "build_tool": {"exit": 0},
        "artifact_inventory": {"exit": 0, "jars": {"demo.jar": ["demo/Main.class"]}},
        "compiler_inputs": {"exit": 0, "files": ["src/main/java/demo/Main.java", original_path]},
    }
    shuffled = {
        **original,
        "compiler_inputs": {"exit": 0, "files": ["src/main/java/demo/Main.java", target]},
    }
    comparison = {
        "comparable": True,
        "artifact_classes_equal": True,
        **{key: [] for key in ("added_nodes", "removed_nodes", "changed_nodes", "new_failures")},
    }
    variant = {
        "plan_ok": True,
        "layout": {"moved_files": 1},
        "tests": shuffled,
        "comparison": comparison,
        "compiled_relocation": harness.compiled_relocation(mapping, original, shuffled),
    }
    report = {
        "language": "java",
        "setup": {"exit": 0},
        "original": original,
        "variants": {"random-42": variant},
    }
    assert harness.acceptable(report)
    snapshot = original if missing == "original" else shuffled
    snapshot["compiler_inputs"]["files"] = ["src/main/java/demo/Main.java"]
    variant["compiled_relocation"] = harness.compiled_relocation(mapping, original, shuffled)
    assert not variant["compiled_relocation"]["complete"]
    assert not harness.acceptable(report)
    del variant["compiled_relocation"]
    assert not harness.acceptable(report)


def test_java_profiles_pin_ten_real_repositories_and_explicit_build_scopes():
    import re

    profiles = json.loads(Path("evaluation/java.json").read_text(encoding="utf-8"))
    assert len(profiles) == 10
    assert {profile["build_system"] for profile in profiles.values()} == {"maven", "gradle"}
    for profile in profiles.values():
        assert profile["language"] == "java"
        assert re.fullmatch(r"[0-9a-f]{40}", profile["revision"])
        assert profile["url"].startswith("https://github.com/")
        assert profile["source_dirs"] and profile["reports"] and profile["artifacts"]
        assert "clean" in profile["tasks"]
        assert not any(
            "skip" in task.lower() or task in {"-x", "--exclude-task"} for task in profile["tasks"]
        )


def test_comparison_does_not_confuse_equal_counts_with_equal_nodes():
    original = {"complete": True, "cases": {"a": ["passed"]}}
    transformed = {"complete": True, "cases": {"b": ["passed"]}}
    result = compare(original, transformed)
    assert result["added_nodes"] == ["b"]
    assert result["removed_nodes"] == ["a"]
