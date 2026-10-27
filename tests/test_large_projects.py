from __future__ import annotations

import tracemalloc

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan, inspect_project
from reposhuffle.cli import main
from reposhuffle.contents import DeferredFile, SnapshotContents


def test_large_unchanged_asset_is_streamed_and_snapshots_are_independent(project):
    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=7\n"})
    asset = source / "model.bin"
    with asset.open("wb") as stream:
        for _ in range(24):
            stream.write(b"x" * 1024 * 1024)
    tracemalloc.start()
    try:
        plan = build_plan(source)
        assert isinstance(plan.contents, SnapshotContents)
        assert isinstance(plan.contents._entries["model.bin"], DeferredFile)
        output = apply_plan(plan)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 12 * 1024 * 1024
    for snapshot in ("original", "shuffled"):
        copied = output / snapshot / "model.bin"
        assert copied.stat().st_size == asset.stat().st_size
        assert copied.stat().st_ino != asset.stat().st_ino


def test_deferred_asset_changes_are_rejected(project):
    source = project({"main.py": "pass\n", "asset.bin": b"before"})
    plan = build_plan(source)
    (source / "asset.bin").write_bytes(b"after!")
    with pytest.raises(ShuffleError, match="changed"):
        plan.contents["asset.bin"]
    with pytest.raises(ShuffleError, match="changed"):
        apply_plan(plan)


@pytest.mark.parametrize(
    "native", ["worker.pyx", "worker.pxd", "worker.abi3.so", "worker.cp313-win_amd64.pyd"]
)
def test_native_sibling_is_preserved_without_freezing_the_package(project, native):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            f"demo/{native}": b"native fixture, never loaded",
            "demo/other.py": "VALUE=2\n",
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/worker.py"] == "demo/worker.py"
    assert plan.mapping["demo/other.py"] != "demo/other.py"


def test_workspace_subproject_uses_its_own_packaging_boundary(project, capsys):
    source = project(
        {
            "main.py": "pass\n",
            "services/api/pyproject.toml": '[tool.setuptools.packages.find]\nwhere=["lib"]\n',
            "services/api/lib/demo/__init__.py": "",
            "services/api/lib/demo/worker.py": "VALUE=7\n",
            "services/other/run.py": "VALUE=9\n",
        }
    )
    assert inspect_project(source).files
    inspection = inspect_project(source, subproject="services/api")
    assert inspection.packages == {"demo": "lib/demo"}
    plan = build_plan(source, subproject="services/api")
    assert plan.inspection.source == source / "services/api"
    assert plan.mapping["lib/demo/worker.py"] != "lib/demo/worker.py"
    assert all(not name.startswith("services/") for name in plan.mapping)
    assert main(["shuffle", str(source), "--subproject", "services/api", "--dry-run"]) == 0
    assert '"subproject": "services' in capsys.readouterr().out
    with pytest.raises(ShuffleError, match="relative path"):
        build_plan(source, subproject="../outside")
