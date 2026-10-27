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


def test_comparison_does_not_confuse_equal_counts_with_equal_nodes():
    original = {"complete": True, "cases": {"a": ["passed"]}}
    transformed = {"complete": True, "cases": {"b": ["passed"]}}
    result = compare(original, transformed)
    assert result["added_nodes"] == ["b"]
    assert result["removed_nodes"] == ["a"]
