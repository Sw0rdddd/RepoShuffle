from __future__ import annotations

from reposhuffle import build_plan


def roles(plan):
    return {record.path: record.role for record in plan.inspection.files}


def test_build_script_keeps_release_module(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/release.py": "VERSION='1.0'\n",
            "demo/worker.py": "VALUE=1\n",
            "setup.py": (
                "import os\n"
                "from pathlib import Path\n"
                "dir_setup = os.path.dirname(os.path.realpath(__file__))\n"
                "exec(Path(os.path.join(dir_setup, 'demo', 'release.py')).read_text())\n"
            ),
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["demo/release.py"] == "demo/release.py"
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"
    assert roles(plan)["demo/release.py"] == "preserved"


def test_preserved_test_keeps_bootstrap_and_dynamic_siblings(project):
    source = project(
        {
            "pyproject.toml": (
                "[build-system]\nrequires=['setuptools']\n"
                "build-backend='setuptools.build_meta'\n"
                "[tool.setuptools.packages.find]\nwhere=['lib']\n"
            ),
            "lib/demo/__init__.py": "",
            "lib/demo/worker.py": "VALUE=1\n",
            "lib/demo/testing/__init__.py": "",
            "lib/demo/testing/bootstrap.py": (
                "import importlib.util\n"
                "import os\n"
                "def load(name):\n"
                "    path = os.path.join(os.path.dirname(__file__), name + '.py')\n"
                "    spec = importlib.util.spec_from_file_location(name, path)\n"
                "    return spec\n"
            ),
            "lib/demo/testing/plugin_base.py": "VALUE=2\n",
            "tests/conftest.py": (
                "import os\n"
                "bootstrap = os.path.join(os.path.dirname(__file__), '..', 'lib', "
                "'demo', 'testing', 'bootstrap.py')\n"
                "with open(bootstrap) as stream:\n"
                "    stream.read()\n"
            ),
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    for path in (
        "lib/demo/testing/bootstrap.py",
        "lib/demo/testing/plugin_base.py",
    ):
        assert plan.mapping[path] == path
        assert roles(plan)[path] == "preserved"
    assert plan.mapping["lib/demo/worker.py"] != "lib/demo/worker.py"


def test_constant_directory_list_keeps_read_sources(project):
    source = project(
        {
            "main.py": "pass\n",
            "a.py": "VALUE=1\n",
            "sub/b.py": "VALUE=2\n",
            "tools/standalone.py": (
                "import os\n"
                "from os import path\n"
                "_dir = path.dirname(__file__)\n"
                "_base = path.join(_dir, path.pardir)\n"
                "FILES = ['a.py', 'sub/b.py']\n"
                "def generate():\n"
                "    for index, name in enumerate(FILES):\n"
                "        with open(os.path.join(_base, name)) as stream:\n"
                "            stream.read()\n"
            ),
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    for path in ("a.py", "sub/b.py"):
        assert plan.mapping[path] == path
        assert roles(plan)[path] == "preserved"
    assert plan.mapping["main.py"] != "main.py"


def test_source_reader_and_python_target_stay_together(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/loader.py": (
                "from pathlib import Path\n"
                "def load():\n"
                "    return (Path(__file__).parent / 'payload.py').read_text()\n"
            ),
            "demo/payload.py": "VALUE=1\n",
            "demo/worker.py": "VALUE=2\n",
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["demo/loader.py"] == "demo/loader.py"
    assert plan.mapping["demo/payload.py"] == "demo/payload.py"
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"


def test_directory_listing_keeps_discovered_modules(project):
    source = project(
        {
            "demo/__init__.py": (
                "import os, pkgutil\n"
                "def find(management_dir):\n"
                "    command_dir = os.path.join(management_dir, 'commands')\n"
                "    return [name for _, name, _ in pkgutil.iter_modules([command_dir])]\n"
            ),
            "demo/commands/__init__.py": "",
            "demo/commands/migrate.py": "VALUE=1\n",
            "demo/worker.py": "VALUE=2\n",
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["demo/commands/migrate.py"] == "demo/commands/migrate.py"
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"


def test_data_reads_do_not_prevent_moving_source(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/reader.py": "def read():\n    return open('data.json').read()\n",
            "demo/data.json": "{}",
            "demo/worker.py": "VALUE=1\n",
        }
    )
    plan = build_plan(source, seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["demo/reader.py"] != "demo/reader.py"
    assert plan.mapping["demo/data.json"] == "demo/data.json"
