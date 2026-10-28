from __future__ import annotations

import os
import subprocess
import sys
import zipfile

import pytest

from reposhuffle import apply_plan, build_plan

BACKENDS = {
    "setuptools-cfg": (
        "setuptools.build_meta",
        "setuptools",
        "setuptools>=68",
        '[project]\nname="delivery-demo"\nversion="1.0"\n',
    ),
    "poetry": (
        "poetry.core.masonry.api",
        "poetry.core",
        "poetry-core>=2",
        (
            '[tool.poetry]\nname="delivery-demo"\nversion="1.0"\ndescription="test"\n'
            'authors=["Test <test@example.com>"]\n'
            'packages=[{include="demo",from="lib"},{include="peer",from="lib"}]\n'
        ),
    ),
    "hatch": (
        "hatchling.build",
        "hatchling",
        "hatchling>=1.26",
        (
            '[project]\nname="delivery-demo"\nversion="1.0"\n'
            '[tool.hatch.build.targets.wheel]\npackages=["lib/demo","lib/peer"]\n'
        ),
    ),
    "hatch-single": (
        "hatchling.build",
        "hatchling",
        "hatchling>=1.26",
        (
            '[project]\nname="delivery-demo"\nversion="1.0"\n'
            '[tool.hatch.build.targets.wheel]\npackages=["lib/demo"]\n'
        ),
    ),
    "flit": (
        "flit_core.buildapi",
        "flit_core",
        "flit_core>=3.10",
        (
            '[project]\nname="delivery-demo"\nversion="1.0"\ndescription="test"\n'
            '[tool.flit.module]\nname="demo"\n'
        ),
    ),
}


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize("backend", BACKENDS)
def test_sdist_to_wheel_is_self_contained(project, tmp_path, backend, mode):
    build_backend, module, requirement, metadata = BACKENDS[backend]
    pytest.importorskip(module)
    base = "src" if backend == "flit" else "lib"
    files = {
        "pyproject.toml": (
            f'[build-system]\nrequires=["{requirement}"]\nbuild-backend="{build_backend}"\n'
            + metadata
        ),
        f"{base}/demo/__init__.py": "from .worker import VALUE\n",
        f"{base}/demo/worker.py": "VALUE=7\n",
        "LICENSE": "Test fixture\n",
    }
    if backend not in {"flit", "hatch-single"}:
        files[f"{base}/peer/__init__.py"] = ""
        files[f"{base}/peer/worker.py"] = "VALUE=11\n"
    if backend == "setuptools-cfg":
        files["setup.cfg"] = "[options]\npackage_dir=\n    =lib\npackages=demo, peer\n"
    source = project(files)
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.inspection.packages["demo"] == f"{base}/demo"
    if backend in {"poetry", "hatch"}:
        assert plan.mapping[f"{base}/demo/worker.py"].startswith(f"{base}/_internal_code/")
        assert plan.mapping[f"{base}/peer/worker.py"].startswith(f"{base}/_internal_code/")
    output = apply_plan(plan)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    artifacts = tmp_path / "artifacts"

    def run(*args, cwd):
        result = subprocess.run(
            [sys.executable, "-B", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(
        "-m",
        "build",
        "--sdist",
        "--no-isolation",
        "--outdir",
        str(artifacts),
        cwd=output / "shuffled",
    )
    run(
        "-m",
        "pip",
        "wheel",
        "--no-build-isolation",
        "--no-deps",
        "--wheel-dir",
        str(artifacts),
        str(next(artifacts.glob("*.tar.gz"))),
        cwd=tmp_path,
    )
    site = tmp_path / "site"
    with zipfile.ZipFile(next(artifacts.glob("*.whl"))) as wheel:
        wheel.extractall(site)
    source.rename(source.with_name("unavailable"))
    code = (
        f"import sys; sys.path.insert(0, {str(site)!r}); "
        "import demo; assert demo.VALUE==7; "
        "import importlib.resources as r; assert r.files('demo').is_dir(); "
        "print(demo.__file__)"
    )
    assert str(site) in run("-I", "-c", code, cwd=tmp_path)


def test_setup_cfg_find_and_version_anchor(project):
    source = project(
        {
            "setup.cfg": (
                "[metadata]\nversion=attr: demo.version.VERSION\n"
                "[options]\npackages=find:\npackage_dir=\n    =lib\n"
                "[options.packages.find]\nwhere=lib\ninclude=demo*,peer*\n"
            ),
            "lib/demo/__init__.py": "",
            "lib/demo/version.py": "VERSION='1.0'\n",
            "lib/demo/worker.py": "VALUE=1\n",
            "lib/peer/__init__.py": "",
            "lib/peer/worker.py": "VALUE=2\n",
        }
    )
    plan = build_plan(source)
    assert plan.mapping["lib/demo/version.py"] == "lib/demo/version.py"
    assert b"_internal_code" in plan.contents["setup.cfg"]
