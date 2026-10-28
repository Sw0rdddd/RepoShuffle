from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_module_identity_resource_anchor(project, run_module, mode):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/factory.py": "import sys\nfrom pathlib import Path\ndef component(name):\n    return Path(sys.modules[name].__file__).parent/'templates'/'page.html'\n",
            "demo/views/__init__.py": "from ..factory import component\npage=component(__name__)\n",
            "demo/views/panel.py": "from ..factory import component\npage=component(__name__)\n",
            "demo/views/templates/page.html": "hello",
            "main.py": "from demo.views import page\nfrom demo.views.panel import page as other\nprint(page.read_text(), other.read_text())\n",
        }
    )
    assert run_module(source, "main") == "hello hello\n"
    plan = build_plan(source, mode=mode, seed=42)
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "hello hello\n"
    assert plan.mapping["demo/factory.py"] != "demo/factory.py"
    assert plan.mapping["demo/views/__init__.py"] == "demo/views/__init__.py"
    assert plan.mapping["demo/views/panel.py"] == "demo/views/panel.py"


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_empty_auxiliary_directories_and_package_resource_roots(project, run_module, mode):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/sub/__init__.py": "",
            "demo/sub/worker.py": "VALUE=1\n",
            "main.py": "from importlib.resources import files\nlist(files('demo.sub').iterdir())\nprint(files('demo.sub').is_dir())\n",
        }
    )
    (source / "tests/fixtures/empty").mkdir(parents=True)
    (source / "docs/empty").mkdir(parents=True)
    (source / "demo/assets/empty").mkdir(parents=True)
    plan = build_plan(source, mode=mode, seed=42)
    output = apply_plan(plan)
    for snapshot in ("original", "shuffled"):
        for directory in ("tests/fixtures/empty", "docs/empty", "demo/assets/empty"):
            assert (output / snapshot / directory).is_dir()
    assert run_module(output / "shuffled", "main") == "True\n"
    manifest = json.loads((output / "manifest.json").read_text())
    assert "tests/fixtures/empty" in manifest["directories"]


def test_directory_inventory_changes_invalidate_plan(project):
    source = project({"main.py": "pass\n"})
    (source / "tests/empty").mkdir(parents=True)
    plan = build_plan(source)
    (source / "tests/empty").rmdir()
    with pytest.raises(ShuffleError, match="inventory changed"):
        apply_plan(plan)


def test_subpackage_initializer_and_compiled_fallback_stay(project, run_module):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/sub/__init__.py": "FLAG=1\n",
            "demo/sub/worker.py": "VALUE=2\n",
            "demo/sub/_row_cy.py": "VALUE=3\n",
            "main.py": "from demo.sub import FLAG\nfrom demo.sub.worker import VALUE\nprint(FLAG, VALUE)\n",
        }
    )
    plan = build_plan(source, mode="random", seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["demo/sub/__init__.py"] == "demo/sub/__init__.py"
    assert plan.mapping["demo/sub/_row_cy.py"] == "demo/sub/_row_cy.py"
    assert plan.mapping["demo/sub/worker.py"] != "demo/sub/worker.py"
    output = apply_plan(plan)
    assert run_module(output / "shuffled", "main") == "1 2\n"


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_named_package_directory_mapping(project, run_module, mode):
    source = project(
        {
            "lib/__init__.py": "from .worker import VALUE\n",
            "lib/worker.py": "VALUE=7\n",
            "pyproject.toml": '[tool.setuptools.package-dir]\ndemo="lib"\n',
        }
    )
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.inspection.packages == {"demo": "lib"}
    assert plan.modules["demo.worker"] == "demo.worker"
    apply_plan(plan)


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize(
    "setup_import,setup_call",
    [
        ("from setuptools import setup", "setup"),
        ("from setuptools import setup as configure", "configure"),
        ("import setuptools as st", "st.setup"),
    ],
)
def test_setup_py_packages_include_storage(project, tmp_path, mode, setup_import, setup_call):
    source = project(
        {
            "setup.py": (
                f"{setup_import}\n"
                f"{setup_call}(name='setup-demo', version='1.0', packages=['demo'])\n"
            ),
            "demo/__init__.py": "from .worker import VALUE\n",
            "demo/worker.py": "VALUE=7\n",
        }
    )
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    assert "_internal_storage_packages" in plan.contents["setup.py"].decode()
    output = apply_plan(plan)
    wheels = tmp_path / "wheels"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    build = subprocess.run(
        [sys.executable, "-B", "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheels)],
        cwd=output / "shuffled",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    import zipfile

    site = tmp_path / "site"
    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
        archive.extractall(site)
    code = (
        "import sys; sys.path.insert(0, "
        + repr(str(site))
        + "); import demo; assert demo.VALUE==7; print(demo.__file__)"
    )
    check = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert str(site) in check.stdout


@pytest.mark.parametrize("mode", ["flat", "random"])
@pytest.mark.parametrize(
    "layout",
    [
        "module",
        "src-module",
        "named-package",
        "explicit-packages",
        "shared-root",
        "shared-src",
        "shared-finder",
        "shared-namespace",
    ],
)
def test_wheel_contains_moved_implementation(project, tmp_path, mode, layout):
    header = '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="boundary-demo"\nversion="1.0"\n'
    if layout == "shared-namespace":
        config = '[tool.setuptools]\npackages=["ns.demo", "ns.peer"]\n[tool.setuptools.package-dir]\nns="lib"\n'
        files = {
            "lib/demo/__init__.py": "from .worker import VALUE\n",
            "lib/demo/worker.py": "from ns.peer.worker import VALUE\n",
            "lib/peer/__init__.py": "",
            "lib/peer/worker.py": "VALUE=7\n",
        }
    elif layout.startswith("shared-"):
        base = "src/" if layout != "shared-root" else ""
        config = '[tool.setuptools]\npackages=["demo", "peer"]\n'
        if layout == "shared-finder":
            config = '[tool.setuptools.packages.find]\nwhere=["src"]\ninclude=["demo*", "peer*"]\n'
        elif base:
            config += '[tool.setuptools.package-dir]\n""="src"\n'
        files = {
            base + "demo/__init__.py": "from .worker import VALUE\n",
            base + "demo/worker.py": "from peer.worker import VALUE\n",
            base + "peer/__init__.py": "",
            base + "peer/worker.py": "VALUE=7\n",
        }
    elif layout in {"module", "src-module"}:
        base = "src/" if layout == "src-module" else ""
        config = '[tool.setuptools]\npy-modules=["demo"]\n'
        if base:
            config += '[tool.setuptools.package-dir]\n""="src"\n'
        files = {base + "demo.py": "VALUE=7\n"}
    else:
        base = "lib" if layout == "named-package" else "demo"
        config = (
            '[tool.setuptools.package-dir]\ndemo="lib"\n'
            if layout == "named-package"
            else '[tool.setuptools]\npackages=["demo"]\n'
        )
        files = {
            f"{base}/__init__.py": "from .worker import VALUE\n",
            f"{base}/worker.py": "VALUE=7\n",
        }
    if layout == "named-package":
        files.update(
            {
                "lib/sub/__init__.py": "",
                "lib/sub/worker.py": "VALUE=2\n",
                "lib/ns/worker.py": "VALUE=3\n",
            }
        )
    files["pyproject.toml"] = header + config
    source = project(files)
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    wheels = tmp_path / "wheels"
    build = subprocess.run(
        [sys.executable, "-B", "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheels)],
        cwd=output / "shuffled",
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    # Extract the wheel into an isolated site directory; do not import from the source tree.
    import zipfile

    site = tmp_path / "site"
    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
        archive.extractall(site)
    code = (
        "import sys; sys.path.insert(0, "
        + repr(str(site))
        + "); import demo; assert demo.VALUE==7; print(demo.__file__)"
    )
    if layout == "shared-namespace":
        code = code.replace("import demo;", "from ns import demo;")
    if layout == "named-package":
        code += "; from importlib.resources import files; import demo.sub, demo.ns.worker; assert files('demo.sub').is_dir(); assert files('demo.ns').is_dir(); list(files('demo.sub').iterdir()); list(files('demo.ns').iterdir())"
    check = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=tmp_path,
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert str(site) in check.stdout
