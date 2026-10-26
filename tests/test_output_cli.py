from __future__ import annotations

import json

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan
from reposhuffle.cli import main


def test_default_output_and_manifest_no_leaks(project):
    source = project({"main.py": "print(2)\n", "data.json": b'{"value":2}'})
    plan = build_plan(source, mode="random", seed=2)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert output == source.with_name("project-shuffled")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 2
    assert manifest["runtime_validation"]["status"] == "not-run"
    assert manifest["static_validation"]["status"] == "passed"
    assert set(manifest["mapping"]) == {"main.py", "data.json"}
    assert (output / "original/main.py").read_bytes() == (source / "main.py").read_bytes()
    assert {p.name for p in output.iterdir()} == {"original", "shuffled", "manifest.json"}
    assert not (output / "shuffled/manifest.json").exists()
    assert not (output / "original/.git").exists()
    assert not list(source.rglob("__pycache__"))


def test_explicit_output(project, tmp_path):
    source = project({"main.py": "pass\n"})
    output = tmp_path / "custom-pair"
    assert apply_plan(build_plan(source, mode="flat"), output) == output
    assert not source.with_name("project-shuffled").exists()


def test_existing_output_is_never_overwritten(project, tmp_path):
    source = project({"main.py": "pass\n"})
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep").write_text("keep")
    with pytest.raises(ShuffleError, match="already exists"):
        apply_plan(build_plan(source), output)
    assert (output / "keep").read_text() == "keep"


def test_overlap_is_rejected(project):
    source = project({"main.py": "pass\n"})
    with pytest.raises(ShuffleError, match="contain"):
        apply_plan(build_plan(source), source / "output")
    assert not (source / "output").exists()


@pytest.mark.parametrize("change", ["content", "added", "removed"])
def test_stale_inventory_rejected(project, tmp_path, change):
    source = project({"main.py": "pass\n"})
    plan = build_plan(source)
    if change == "content":
        (source / "main.py").write_text("print(1)\n")
    elif change == "added":
        (source / "new.txt").write_text("new")
    else:
        (source / "main.py").unlink()
    output = tmp_path / "output"
    with pytest.raises(ShuffleError, match="changed"):
        apply_plan(plan, output)
    assert not output.exists()


def test_failed_plan_cannot_be_applied(project):
    source = project({"main.py": "return 1\n"})
    with pytest.raises(ShuffleError, match="blocking"):
        apply_plan(build_plan(source))


def test_target_code_is_never_executed(project, tmp_path):
    marker = tmp_path / "TARGET_EXECUTED"
    source = project({"main.py": f"raise RuntimeError({str(marker)!r})\n"})
    plan = build_plan(source, mode="flat")
    assert plan.ok, plan.to_dict()
    apply_plan(plan)
    assert not marker.exists()


def test_publication_failure_cleans_only_own_output(project, tmp_path, monkeypatch):
    import reposhuffle.output as output_module

    source = project({"main.py": "pass\n"})
    plan = build_plan(source)
    target = tmp_path / "result"
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep")
    rename = output_module.Path.rename

    def fail_manifest(path, destination):
        if path.name == "manifest.json":
            raise OSError("simulated publication failure")
        return rename(path, destination)

    monkeypatch.setattr(output_module.Path, "rename", fail_manifest)
    with pytest.raises(OSError, match="simulated"):
        apply_plan(plan, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".reposhuffle-*"))
    assert sentinel.read_text() == "keep"
    assert (source / "main.py").read_text() == "pass\n"


def test_cli_inspect_dry_run_and_custom_output(project, tmp_path, capsys):
    source = project({"main.py": "pass\n"})
    assert main(["inspect", str(source)]) == 0
    assert "discovery-only" in json.loads(capsys.readouterr().out)["scope"]
    assert main(["shuffle", str(source), "--dry-run", "--mode", "flat", "--seed", "4"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert not source.with_name("project-shuffled").exists()
    output = tmp_path / "cli-pair"
    assert main(["shuffle", str(source), "--mode", "flat", "-o", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == str(output)
    assert (output / "manifest.json").is_file()


def test_cli_blocking_diagnostics_are_json(project, capsys):
    source = project({"main.py": "return 1\n"})
    assert main(["inspect", str(source), "--mode", "random"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert not payload["ok"]
    assert "PYTHON_SYNTAX" in {d["rule"] for d in payload["diagnostics"]}


def test_cli_missing_input(capsys, tmp_path):
    assert main(["shuffle", str(tmp_path / "missing")]) == 2
    assert not json.loads(capsys.readouterr().err)["ok"]
