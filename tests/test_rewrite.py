from __future__ import annotations

import pytest

from reposhuffle import apply_plan, build_plan


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize("seed", [0, 42])
def test_logical_imports_initialization_cycles_and_lazy_imports(project, run_module, mode, seed):
    source = project(
        {
            "demo/__init__.py": '"""Docstring."""\nfrom __future__ import annotations\nBASE=2\nfrom .calc import add\n',
            "demo/calc.py": "from . import BASE\ndef add(x):\n    return BASE+x\n",
            "demo/lazy.py": "print('lazy loaded')\nVALUE=8\n",
            "main.py": "import demo.calc\nfrom demo import add\nfrom importlib import import_module\nfrom unittest.mock import patch\ndef later():\n    from demo import lazy\n    return lazy.VALUE\nif __name__ == '__main__':\n    print(add(1), demo.calc.add(2), import_module('demo.calc').__name__)\n    with patch('demo.calc.add', return_value=9):\n        print(demo.calc.add(0))\n    print('before lazy')\n    print(later())\n",
        }
    )
    expected = run_module(source, "main")
    plan = build_plan(source, mode=mode, seed=seed)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert (
        run_module(output / "shuffled", "main")
        == expected
        == "3 4 demo.calc\n9\nbefore lazy\nlazy loaded\n8\n"
    )
    assert plan.contents[plan.mapping["demo/calc.py"]] == (source / "demo/calc.py").read_bytes()
    assert plan.modules["demo.calc"] == "demo.calc"


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_introspection_pickle_metadata_and_package_discovery(project, run_module, mode):
    source = project(
        {
            "demo/__init__.py": "from .models import Item\n",
            "demo/models.py": "class Item:\n    def value(self):\n        return 3\n",
            "demo/nested/__init__.py": "FLAG=True\n",
            "demo/nested/worker.py": "VALUE=7\n",
            "main.py": "import inspect, pickle, pkgutil, importlib.util\nimport demo\nfrom demo.models import Item\nfrom demo.nested import worker\nitem=pickle.loads(pickle.dumps(Item()))\nassert item.value()==3\nassert Item.__module__=='demo.models'\nassert 'class Item:' in inspect.getsource(Item)\nassert importlib.util.find_spec('demo.nested.worker').name=='demo.nested.worker'\nassert {'demo.models','demo.nested','demo.nested.worker'} == {m.name for m in pkgutil.walk_packages(demo.__path__, 'demo.')}\nprint('ok')\n",
        }
    )
    plan = build_plan(source, mode=mode, seed=7)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "ok\n"
    assert not (output / "shuffled/demo/models.py").exists()


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_resources_module_entry_and_unchanged_fixture_paths(project, run_module, mode):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/sub/__init__.py": "",
            "demo/sub/data.txt": "resource",
            "demo/sub/tool.py": "import importlib.resources as r\nimport pkgutil\ndef value():\n    assert pkgutil.get_data('demo.sub', 'data.txt')==b'resource'\n    return r.files('demo.sub').joinpath('data.txt').read_text()\n",
            "demo/__main__.py": "from .sub.tool import value\nprint(value())\n",
            "tests/conftest.py": "# fixture scope stays here\n",
            "tests/data/fixture.json": "{}",
        }
    )
    plan = build_plan(source, mode=mode, seed=1)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "demo") == "resource\n"
    for path in ("tests/conftest.py", "tests/data/fixture.json", "demo/sub/data.txt"):
        assert plan.mapping[path] == path
        assert (output / "shuffled" / path).read_bytes() == (source / path).read_bytes()


def test_namespace_package_children(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/plugins/worker.py": "VALUE=3\n",
            "main.py": "from demo.plugins.worker import VALUE\nprint(VALUE)\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "3\n"


def test_unrelated_runtime_operations_are_not_rejected(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "import logging, os\nlogger=logging.getLogger(__name__)\ndef value(obj, path):\n    os.makedirs(path, exist_ok=True)\n    return getattr(obj, 'answer', 3), hasattr(obj, 'answer')\n",
            "main.py": "from demo.worker import value\nimport tempfile\nwith tempfile.TemporaryDirectory() as path:\n    print(value(object(), path))\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "(3, False)\n"


def test_main_numeric_collision_keeps_original_logical_modules(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/a/__init__.py": "",
            "demo/a/main.py": "VALUE=1\n",
            "demo/b/__init__.py": "",
            "demo/b/main.py": "VALUE=2\n",
            "main.py": "from demo.a.main import VALUE as A\nfrom demo.b.main import VALUE as B\nprint(A,B)\n",
        }
    )
    plan = build_plan(source, mode="flat")
    assert plan.ok
    output = apply_plan(plan)
    assert plan.mapping["demo/a/main.py"].endswith("/main_1.py")
    assert plan.mapping["demo/b/main.py"].endswith("/main_2.py")
    assert run_module(output / "shuffled", "main") == "1 2\n"


def test_script_entry_preserves_imports_arguments_and_real_source_path(project, run_module):
    source = project(
        {
            "runner.py": "import sys\ndef main():\n    return len(sys.argv)\nVALUE=7\nif __name__ == '__main__':\n    print(main())\n",
            "check.py": "import runner, inspect\nassert runner.VALUE==7\nassert 'def main()' in inspect.getsource(runner)\nassert '_internal_code' in runner.__file__\nprint(runner.main())\n",
        }
    )
    plan = build_plan(source, mode="random", seed=1)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "runner") == "1\n"
    assert run_module(output / "shuffled", "check") == "1\n"


def test_excluded_submodule_and_reload_use_one_logical_identity(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/static.py": "VALUE=5\n",
            "demo/models.py": "class Item: pass\n",
            "main.py": "import demo.static, demo.models, importlib, sys\nassert demo.static.VALUE==5\nfirst=demo.models\nassert importlib.reload(first) is first\nassert sys.modules['demo.models'] is first\nprint(first.Item.__module__)\n",
        }
    )
    plan = build_plan(source, exclude=["demo/static.py"])
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    assert plan.mapping["demo/static.py"] == "demo/static.py"
    assert run_module(output / "shuffled", "main") == "demo.models\n"


def test_source_relative_files_stay_with_their_assets(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/reader.py": "from pathlib import Path\ndef read():\n    return (Path(__file__).parent/'data.txt').read_text()\n",
            "demo/worker.py": "from .reader import read\nprint(read())\n",
            "demo/data.txt": "hello",
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/reader.py"] == "demo/reader.py"
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "demo.worker") == "hello\n"
