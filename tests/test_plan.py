from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from reposhuffle import ShuffleError, build_plan, inspect_project


def test_discovery_keeps_auxiliary_scopes_and_common_configs(project):
    files = {
        "src/demo/__init__.py": "from .worker import value\n",
        "src/demo/worker.py": "value = 3\n",
        "tests/conftest.py": "import pytest\n",
        "tests/test_worker.py": "from demo import value\n",
        "tests/fixtures/invalid.py": "this is intentionally invalid Python !",
        "docs/conf.py": "raise RuntimeError('not executed')\n",
        "docs/guide.md": "```\n├── src/\n```\n",
        "pyproject.toml": '[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n',
        "Makefile": "test:\n\tpytest\n",
        ".gitignore": "custom-pattern/\n",
    }
    source = project(files)
    inspection = inspect_project(source)
    assert inspection.ok, inspection.to_dict()
    roles = {f.path: f.role for f in inspection.files}
    assert roles["src/demo/worker.py"] == "source"
    assert roles["src/demo/__init__.py"] == "preserved"
    assert all(roles[name] == "preserved" for name in files if not name.startswith("src/"))
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    for name, content in files.items():
        if not name.startswith("src/"):
            assert plan.mapping[name] == name
            assert plan.contents[name] == content.encode()


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_deterministic_read_only_planning(project, mode):
    source = project(
        {
            "widget/__init__.py": "",
            "widget/worker.py": "value=3\n",
            "main.py": "import widget.worker\n",
            **{f"widget/helper_{i}.py": "value=1\n" for i in range(8)},
        }
    )
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    one = build_plan(source, mode=mode, seed=42)
    two = build_plan(source, mode=mode, seed=42)
    assert one.ok, one.to_dict()
    assert one.to_dict() == two.to_dict()
    assert one.contents == two.contents
    assert before == {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    assert all(old == new for old, new in one.modules.items())
    assert one.modules["widget.worker"] == "widget.worker"
    if mode == "random":
        assert one.mapping != build_plan(source, mode=mode, seed=43).mapping


def test_flat_names_use_numeric_suffixes_without_overwriting(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/a/__init__.py": "",
            "demo/a/main.py": "value=1\n",
            "demo/b/__init__.py": "",
            "demo/b/main.py": "value=2\n",
            "demo/main_1.py": "value=3\n",
        }
    )
    plan = build_plan(source, mode="flat", seed=1)
    assert plan.ok, plan.to_dict()
    assert PurePosixPath(plan.mapping["demo/a/main.py"]).name == "main_2.py"
    assert PurePosixPath(plan.mapping["demo/b/main.py"]).name == "main_3.py"
    assert PurePosixPath(plan.mapping["demo/main_1.py"]).name == "main_1.py"
    assert (
        len(
            {
                PurePosixPath(plan.mapping[f.path]).parent
                for f in plan.inspection.files
                if f.role == "source"
            }
        )
        == 1
    )


@pytest.mark.parametrize("seed", [0, 7, 42])
def test_random_numbered_directory_names(project, seed):
    source = project(
        {"demo/__init__.py": "", **{f"demo/file_{i}.py": "value=1\n" for i in range(8)}}
    )
    plan = build_plan(source, seed=seed)
    assert plan.ok
    names = set()
    for record in plan.inspection.files:
        if record.role == "source":
            parts = PurePosixPath(plan.mapping[record.path]).parts
            numbered = [part for part in parts if part.startswith("module_")]
            assert 1 <= len(numbered) <= 3
            names.update(numbered)
    assert names == {f"module_{i}" for i in range(1, len(names) + 1)}


@pytest.mark.parametrize("count", [1, 3, 6, 7, 17, 78])
@pytest.mark.parametrize("seed", [0, 7, 42])
def test_random_groups_are_dense_without_empty_intermediate_directories(project, count, seed):
    from collections import Counter

    source = project(
        {"demo/__init__.py": "", **{f"demo/file_{i}.py": "VALUE=1\n" for i in range(count)}}
    )
    plan = build_plan(source, seed=seed)
    assert plan.ok, plan.to_dict()
    occupancy = Counter(
        PurePosixPath(plan.mapping[f.path]).parent
        for f in plan.inspection.files
        if f.role == "source"
    )
    assert len(occupancy) == (count + 5) // 6
    assert max(occupancy.values()) - min(occupancy.values()) <= 1
    if count > 1:
        assert min(occupancy.values()) >= 2
    directories = {
        str(parent)
        for target in plan.mapping.values()
        for parent in PurePosixPath(target).parents
        if parent.name.startswith("module_")
    }
    assert directories == {str(path) for path in occupancy}


def test_dense_groups_keep_equal_filenames_separate(project):
    source = project(
        {
            "demo/__init__.py": "",
            **{
                f"demo/part_{i}/{name}": "VALUE=1\n"
                for i in range(4)
                for name in ("main.py", "worker.py", "extra.py")
            },
        }
    )
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    targets = [plan.mapping[f.path] for f in plan.inspection.files if f.role == "source"]
    assert len(set(targets)) == 12
    assert len({str(PurePosixPath(path).parent) for path in targets}) == 4
    assert all(PurePosixPath(path).name in {"main.py", "worker.py", "extra.py"} for path in targets)


def test_source_areas_are_not_hardcoded(project):
    source = project(
        {
            "backend/components/__init__.py": "",
            "backend/components/value.py": "VALUE=1\n",
            "lib/runner.py": "print(1)\n",
        }
    )
    plan = build_plan(source, mode="flat")
    assert plan.ok, plan.to_dict()
    records = {f.path: f for f in plan.inspection.files}
    assert records["backend/components/value.py"].role == "source"
    assert records["lib/runner.py"].role == "script"
    assert plan.mapping["lib/runner.py"] != "lib/runner.py"


def test_include_and_exclude_keep_files_not_delete_them(project):
    source = project(
        {"demo/__init__.py": "", "demo/a.py": "x=1\n", "demo/b.py": "x=2\n", "demo/c.py": "x=3\n"}
    )
    plan = build_plan(source, include=["demo/*.py"], exclude=["demo/b.py"])
    assert plan.ok
    assert plan.mapping["demo/a.py"] != "demo/a.py"
    assert plan.mapping["demo/b.py"] == "demo/b.py"
    assert plan.contents["demo/b.py"] == b"x=2\n"
    other = build_plan(source, include=["demo/a.py"])
    assert other.mapping["demo/c.py"] == "demo/c.py"


def test_build_metadata_and_resource_anchors_are_explicit(project):
    source = project(
        {
            "src/demo/__init__.py": "",
            "src/demo/version.py": "VERSION='1.0'\n",
            "src/demo/reader.py": "from pathlib import Path\nx=Path(__file__).parent\n",
            "src/demo/worker.py": "value=1\n",
            "pyproject.toml": '[tool.setuptools.dynamic]\nversion={attr="demo.version.VERSION"}\n',
        }
    )
    plan = build_plan(source)
    assert plan.ok
    reasons = {f.path: f.reason for f in plan.inspection.files}
    assert reasons["src/demo/version.py"] == "build metadata attribute"
    assert reasons["src/demo/reader.py"] == "source-relative filesystem anchor"
    assert plan.mapping["src/demo/worker.py"] != "src/demo/worker.py"


@pytest.mark.parametrize(
    "kwargs",
    [{"mode": "bad"}, {"max_depth": 0}, {"max_depth": True}, {"seed": "x"}, {"seed": True}],
)
def test_invalid_options(project, kwargs):
    with pytest.raises(ShuffleError):
        build_plan(project({"main.py": "pass\n"}), **kwargs)


def test_explicit_source_root_in_project_configuration(project):
    source = project(
        {
            "implementation/widget/__init__.py": "",
            "implementation/widget/worker.py": "VALUE=1\n",
            "implementation/tool.py": "print(1)\n",
            "tests/test_worker.py": "pass\n",
            "pyproject.toml": '[tool.reposhuffle]\nsource-dirs=["implementation"]\n',
        }
    )
    plan = build_plan(source, mode="flat")
    assert plan.ok, plan.to_dict()
    assert plan.modules["widget.worker"] == "widget.worker"
    assert plan.mapping["implementation/widget/worker.py"] != "implementation/widget/worker.py"
    assert plan.mapping["implementation/tool.py"] != "implementation/tool.py"
    assert plan.mapping["tests/test_worker.py"] == "tests/test_worker.py"


def test_cache_and_vcs_files_excluded(project):
    source = project(
        {
            "main.py": "pass\n",
            ".git/config": "secret",
            ".venv/a.py": "bad",
            "__pycache__/x.pyc": b"binary",
        }
    )
    result = inspect_project(source)
    assert [f.path for f in result.files] == ["main.py"]
    assert result.excluded == [".git/", ".venv/", "__pycache__/"]
