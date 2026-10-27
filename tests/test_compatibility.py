from __future__ import annotations

import json

from reposhuffle import apply_plan, build_plan


def test_scan_risk_does_not_disable_mixing(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "tests/check.py": "import os\nfor root, dirs, files in os.walk('demo'):\n    pass\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"
    report = plan.compatibility
    assert report["risks"] == {"SOURCE_SCAN": 1}
    assert report["runtime"] == "not-run"
    assert report["selection"]["movable_files"] == 1
    output = apply_plan(plan)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["compatibility"] == report


def test_unknown_backend_is_reported_without_blocking(project):
    source = project(
        {
            "pyproject.toml": '[build-system]\nbuild-backend="custom_backend.build"\n',
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert not plan.compatibility["build"]["selection_adapter"]
    assert plan.compatibility["risks"]["UNVERIFIED_BUILD_BACKEND"] == 1
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"


def test_preservation_reasons_are_counted(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/anchor.py": "PATH=__file__\n",
            "demo/worker.py": "VALUE=1\n",
        }
    )
    selection = build_plan(source).compatibility["selection"]
    assert selection["preserved_by_reason"]["source-relative filesystem anchor"] == 1
    assert selection["movable_ratio"] == 0.3333
