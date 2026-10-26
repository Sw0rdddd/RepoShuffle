from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from reposhuffle import Inspection, Plan, ShuffleError, apply_plan, build_plan, inspect_project


def test_no_dependency_on_original_repository(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=3\n",
            "main.py": "from demo.worker import VALUE\nprint(VALUE)\n",
        }
    )
    plan = build_plan(source)
    output = apply_plan(plan)
    source.rename(source.with_name("unavailable-original"))
    assert run_module(output / "shuffled", "main") == "3\n"
    for path, data in plan.contents.items():
        assert str(source).encode() not in data, path


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_unchanged_pytest_scope_works_before_and_after(project, mode):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=3\n",
            "pytest.ini": "[pytest]\ntestpaths=tests\n",
            "tests/conftest.py": "import pytest\n@pytest.fixture\ndef value():\n    return 1\n",
            "tests/test_root.py": "from demo.worker import VALUE\ndef test_value(value):\n    assert value==1 and VALUE==3\n",
            "tests/sub/conftest.py": "import pytest\n@pytest.fixture\ndef value():\n    return 2\n",
            "tests/sub/test_child.py": "from demo.worker import VALUE\ndef test_value(value):\n    assert value==2 and VALUE==3\n",
        }
    )
    plan = build_plan(source, mode=mode)
    assert plan.ok
    output = apply_plan(plan)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for root in (output / "original", output / "shuffled"):
        process = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert "2 passed" in process.stdout
    for record in plan.inspection.files:
        if record.path.startswith("tests/"):
            assert plan.contents[record.path] == (source / record.path).read_bytes()


def test_sys_modules_compatibility_aliases_and_external_imports(project, run_module):
    source = project(
        {
            "demo/__init__.py": "import sys, json\nsys.modules[__name__+'.compat']=json\nfrom .worker import value\n",
            "demo/worker.py": "from .compat import dumps\ndef value():\n    return dumps([1,2])\n",
            "main.py": "from demo import value\nprint(value())\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "[1, 2]\n"


def test_new_public_modules_can_be_added_after_conversion(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "main.py": "import demo\nfrom demo.new import VALUE\nprint(VALUE)\n",
        }
    )
    plan = build_plan(source)
    output = apply_plan(plan)
    (output / "shuffled/demo/new.py").write_text("VALUE=7\n")
    assert run_module(output / "shuffled", "main") == "7\n"


def test_private_support_names_do_not_overwrite_user_files(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/_reposhuffle_runtime.py": "VALUE=9\n",
            "demo/worker.py": "VALUE=1\n",
            "main.py": "from demo._reposhuffle_runtime import VALUE\nprint(VALUE)\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "9\n"


def test_changed_or_manually_constructed_plan_cannot_be_published(project):
    source = project({"main.py": "pass\n"})
    with pytest.raises(ShuffleError, match="unvalidated"):
        apply_plan(Plan(Inspection(source), "flat", 1))
    plan = build_plan(source)
    plan.contents[plan.mapping["main.py"]] = b"raise RuntimeError('modified')\n"
    with pytest.raises(ShuffleError, match="changed after validation"):
        apply_plan(plan)


def test_generated_manifest_contains_actual_checks(project):
    source = project({"main.py": "pass\n"})
    output = apply_plan(build_plan(source))
    report = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert report["tool_version"] == "0.2.0"
    assert report["runtime_validation"]["status"] == "not-run"
    assert report["configuration"]["scope"] == "implementation-only"
    assert "static-dependency-reachability" not in report["static_validation"]["checks"]


def test_git_worktree_file_excluded_and_symlinks_reported(project):
    source = project({"main.py": "pass\n", ".git": "gitdir: external\n"})
    assert ".git" in inspect_project(source).excluded
    try:
        (source / "linked.py").symlink_to(source / "main.py")
    except OSError:
        pytest.skip("Symlinks need additional platform permissions")
    assert "SYMLINK" in {d.rule for d in inspect_project(source).diagnostics}


def test_invalid_source_syntax_but_not_invalid_test_data_is_blocking(project):
    source = project({"main.py": "return 1\n", "tests/data/broken.py": "not valid!"})
    plan = build_plan(source)
    errors = [d for d in plan.inspection.diagnostics if d.severity == "error"]
    assert [d.path for d in errors] == ["main.py"]
