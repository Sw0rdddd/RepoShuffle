from __future__ import annotations

from reposhuffle import build_plan


def test_function_locals_do_not_leak_into_module_scope(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/a.py": "VALUE=1\n",
            "demo/b.py": "VALUE=2\n",
            "tests/check.py": (
                "target='demo/a.py'\n"
                "def unused(target):\n    target='demo/b.py'\n"
                "open(target).read()\n"
            ),
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/a.py"] == "demo/a.py"
    assert plan.mapping["demo/b.py"] != "demo/b.py"


def test_import_aliases_and_shadowed_builtins(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/a.py": "VALUE=1\n",
            "demo/b.py": "VALUE=2\n",
            "tests/check.py": (
                "from pathlib import Path as P\n"
                "P('demo/a.py').read_text()\n"
                "def open(path):\n    return path\n"
                "open('demo/b.py')\n"
            ),
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/a.py"] == "demo/a.py"
    assert plan.mapping["demo/b.py"] != "demo/b.py"


def test_doctest_location_anchor_is_preserved(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/example.py": '"""Example.\n\n>>> from pathlib import Path\n>>> Path(__file__).parent.name\n"""\n',
            "demo/worker.py": "VALUE=1\n",
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/example.py"] == "demo/example.py"
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"


def test_directory_sensitive_doctest_does_not_freeze_its_neighbours(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/scanner.py": (
                "import os\ndef scan(path):\n"
                '    """List files.\n\n    >>> directory = os.path.dirname(__file__)\n'
                '    >>> list(scan(directory))\n    """\n'
                "    yield from os.walk(path)\n"
            ),
            "demo/worker.py": "VALUE=1\n",
        }
    )
    plan = build_plan(source)
    assert plan.mapping["demo/scanner.py"] != "demo/scanner.py"
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"
    assert plan.compatibility["risks"]["SOURCE_SCAN"] == 1


def test_conflicting_branches_do_not_invent_a_known_path(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/a.py": "VALUE=1\n",
            "demo/b.py": "VALUE=2\n",
            "tests/check.py": (
                "if condition:\n    target='demo/a.py'\n"
                "else:\n    target='demo/b.py'\n"
                "open(target)\n"
            ),
        }
    )
    plan = build_plan(source)
    assert all(plan.mapping[f"demo/{name}.py"] != f"demo/{name}.py" for name in ("a", "b"))
