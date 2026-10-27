from __future__ import annotations

import pytest

from reposhuffle import apply_plan, build_plan


def test_resource_reader_imports_on_supported_python(tmp_path):
    from reposhuffle.runtime import ResourceReader

    (tmp_path / "data.txt").write_text("resource", encoding="utf-8")
    assert ResourceReader(tmp_path).files().joinpath("data.txt").read_text() == "resource"


@pytest.mark.parametrize("late", [False, True])
def test_routed_namespace_keeps_external_portions_and_resources(project, run_module, late):
    source = project(
        {
            "demo/__init__.py": "from pkgutil import extend_path\n__path__=extend_path(__path__, __name__)\n",
            "demo/plugins/worker.py": "VALUE=7\n",
            "demo/plugins/local.txt": "local",
        }
    )
    output = apply_plan(build_plan(source, seed=42))
    root = output / "shuffled"
    external = root / "external/demo/plugins"
    external.mkdir(parents=True)
    (external / "extra.py").write_text("VALUE=11\n", encoding="utf-8")
    (external / "remote.txt").write_text("remote", encoding="utf-8")
    setup = (
        "import demo.plugins\ndemo.__path__.append(str(Path('external/demo').resolve()))\n"
        if late
        else "sys.path.insert(0, str(Path('external').resolve()))\n"
    )
    (root / "check.py").write_text(
        "import sys, pkgutil\nfrom pathlib import Path\n"
        + setup
        + "import demo.plugins\nfrom demo.plugins.worker import VALUE\n"
        "from demo.plugins.extra import VALUE as OTHER\n"
        "from importlib.resources import files\n"
        "assert (VALUE, OTHER)==(7,11)\n"
        "assert files('demo.plugins').joinpath('local.txt').read_text()=='local'\n"
        "assert files('demo.plugins').joinpath('remote.txt').read_text()=='remote'\n"
        "assert {'worker','extra'} <= {x.name for x in pkgutil.iter_modules(demo.plugins.__path__)}\n"
        "print('ok')\n",
        encoding="utf-8",
    )
    assert run_module(root, "check") == "ok\n"


def test_spawn_imports_canonical_worker(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "def run(queue):\n    queue.put(__name__)\n",
            "main.py": (
                "import multiprocessing as mp\nfrom demo.worker import run\n"
                "if __name__ == '__main__':\n"
                "    ctx=mp.get_context('spawn')\n    queue=ctx.Queue()\n"
                "    process=ctx.Process(target=run,args=(queue,))\n    process.start()\n"
                "    assert queue.get(timeout=5)=='demo.worker'\n"
                "    process.join(5)\n    assert process.exitcode==0\n    print('ok')\n"
            ),
        }
    )
    output = apply_plan(build_plan(source))
    assert run_module(output / "shuffled", "main") == "ok\n"
