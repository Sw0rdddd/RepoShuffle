from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

import pytest

from reposhuffle import apply_plan, build_plan


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize("container", ["src", "backend", "lib"])
@pytest.mark.parametrize("prefixed", [False, True])
def test_sibling_packages_share_storage_and_keep_imports(
    project, run_module, mode, container, prefixed
):
    prefix = container + "." if prefixed else ""
    files = {
        f"{container}/__init__.py": "",
        f"{container}/alpha/__init__.py": "from .worker import VALUE\n",
        f"{container}/alpha/models.py": "VALUE=7\n",
        f"{container}/alpha/worker.py": "from .models import VALUE\n",
        f"{container}/beta/__init__.py": "",
        f"{container}/beta/models.py": "VALUE=11\n",
        f"{container}/beta/worker.py": f"from {prefix}alpha.worker import VALUE\n",
        f"{container}/beta/anchor.py": "from pathlib import Path\nDATA=Path(__file__).with_name('data.txt').read_text()\n",
        f"{container}/beta/data.txt": "resource",
        "pyproject.toml": f"[tool.setuptools.packages.find]\nwhere=['{container}']\n",
        "main.py": (
            f"import sys; sys.path.insert(0, {container!r})\n"
            "from importlib import import_module\n"
            f"from {prefix}alpha import VALUE\n"
            f"from {prefix}beta.worker import VALUE as OTHER\n"
            f"from {prefix}beta.anchor import DATA\n"
            f"print(VALUE, OTHER, import_module('{prefix}beta.models').VALUE, DATA)\n"
        ),
    }
    source = project(files)
    expected = run_module(source, "main")
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.inspection.source_roots == {"alpha": container, "beta": container}
    assert plan.configuration["layout_version"] == 4
    targets = [plan.mapping[f.path] for f in plan.inspection.files if f.role == "source"]
    assert all(path.startswith(f"{container}/_reposhuffle_code/") for path in targets)
    assert len({path.casefold() for path in targets}) == len(targets)
    assert plan.mapping[f"{container}/beta/anchor.py"] == f"{container}/beta/anchor.py"
    assert plan.mapping == build_plan(source, mode=mode, seed=42).mapping
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == expected == "7 7 11 resource\n"


def test_random_allocation_mixes_package_origins(project):
    source = project(
        {
            **{f"src/{name}/__init__.py": "" for name in ("alpha", "beta", "gamma")},
            **{
                f"src/{name}/file_{i}.py": "VALUE=1\n"
                for name in ("alpha", "beta", "gamma")
                for i in range(12)
            },
        }
    )
    plan = build_plan(source, seed=42)
    origins = defaultdict(set)
    for record in plan.inspection.files:
        if record.role == "source":
            origins[str(PurePosixPath(plan.mapping[record.path]).parent)].add(record.owner)
    assert origins
    assert all(len(owners) > 1 for owners in origins.values())


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_explicit_source_boundaries_stay_separate(project, mode):
    source = project(
        {
            "pyproject.toml": '[tool.reposhuffle]\nsource-dirs=["backend", "lib"]\n',
            **{
                f"{area}/{name}/__init__.py": ""
                for area, name in (
                    ("backend", "alpha"),
                    ("backend", "beta"),
                    ("lib", "gamma"),
                    ("lib", "delta"),
                )
            },
            **{
                f"{area}/{name}/worker.py": "VALUE=1\n"
                for area, name in (
                    ("backend", "alpha"),
                    ("backend", "beta"),
                    ("lib", "gamma"),
                    ("lib", "delta"),
                )
            },
        }
    )
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    for record in plan.inspection.files:
        if record.role == "source":
            area = record.path.split("/")[0]
            assert plan.mapping[record.path].startswith(f"{area}/_reposhuffle_code/")
