from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from reposhuffle import apply_plan, build_plan


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize("physical_first", [False, True])
@pytest.mark.parametrize("shared", [False, True])
def test_physical_import_uses_logical_identity(project, run_module, mode, physical_first, shared):
    files = {
        "demo/__init__.py": "",
        "demo/models.py": "class Item: pass\nSINGLETON=object()\n",
    }
    if shared:
        files["peer/__init__.py"] = ""
        files["peer/worker.py"] = "VALUE=1\n"
    source = project(files)
    plan = build_plan(source, mode=mode, seed=42)
    physical = ".".join(PurePosixPath(plan.mapping["demo/models.py"]).with_suffix("").parts)
    output = apply_plan(plan)
    names = [physical, "demo.models"] if physical_first else ["demo.models", physical]
    (output / "shuffled/check.py").write_text(
        "import importlib, pickle\n"
        f"a=importlib.import_module({names[0]!r})\n"
        f"b=importlib.import_module({names[1]!r})\n"
        "assert a is b and a.Item is b.Item and a.SINGLETON is b.SINGLETON\n"
        "assert a.__name__ == a.__spec__.name == 'demo.models'\n"
        "assert a.Item.__module__ == 'demo.models'\n"
        "assert type(pickle.loads(pickle.dumps(a.Item()))) is a.Item\n"
        "assert importlib.reload(a) is b\nprint('ok')\n",
        encoding="utf-8",
    )
    assert run_module(output / "shuffled", "check") == "ok\n"


@pytest.mark.parametrize("physical_first", [False, True])
def test_standalone_script_has_one_import_identity(project, run_module, physical_first):
    source = project({"runner.py": "class Item: pass\nSINGLETON=object()\n"})
    plan = build_plan(source, seed=42)
    physical = ".".join(PurePosixPath(plan.mapping["runner.py"]).with_suffix("").parts)
    output = apply_plan(plan)
    names = [physical, "runner"] if physical_first else ["runner", physical]
    (output / "shuffled/check.py").write_text(
        "import importlib\n"
        f"a=importlib.import_module({names[0]!r})\nb=importlib.import_module({names[1]!r})\n"
        "assert a is b and a.Item is b.Item and a.SINGLETON is b.SINGLETON\n"
        "assert a.__spec__.name == a.__name__ == 'runner'\nprint('ok')\n",
        encoding="utf-8",
    )
    assert run_module(output / "shuffled", "check") == "ok\n"


def test_container_prefixed_physical_alias_and_support_name_collision(project, run_module):
    source = project(
        {
            "src/__init__.py": "",
            "src/demo/__init__.py": "",
            "src/demo/_internal_aliases.py": "VALUE=7\n",
            "src/peer/__init__.py": "",
            "src/peer/worker.py": "VALUE=11\n",
            "pyproject.toml": '[tool.setuptools.packages.find]\nwhere=["src"]\n',
        }
    )
    plan = build_plan(source, mode="flat")
    assert "src/_internal_code/_internal_aliases_.py" in plan.generated
    physical = ".".join(
        PurePosixPath(plan.mapping["src/demo/_internal_aliases.py"]).with_suffix("").parts
    )
    output = apply_plan(plan)
    (output / "shuffled/check.py").write_text(
        "import importlib\n"
        f"a=importlib.import_module({physical!r})\n"
        "b=importlib.import_module('src.demo._internal_aliases')\n"
        "assert a is b and a.VALUE==7\nprint('ok')\n",
        encoding="utf-8",
    )
    assert run_module(output / "shuffled", "check") == "ok\n"
