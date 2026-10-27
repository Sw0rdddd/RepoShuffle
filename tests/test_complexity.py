from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import PurePosixPath

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan
from reposhuffle.cli import main


def many(project, count: int = 120):
    files = {"demo/__init__.py": ""}
    files.update({f"demo/file_{index:03}.py": "VALUE=1\n" for index in range(count)})
    return project(files)


@pytest.mark.parametrize("complexity", [0, 0.25, 0.5, 0.7, 0.75, 1])
def test_complexity_range_is_accepted_and_reported(project, complexity):
    source = many(project, 24)
    plan = build_plan(source, mode="random", seed=42, complexity=complexity)
    assert plan.ok, plan.to_dict()
    assert plan.configuration["complexity"] == complexity
    assert plan.layout["complexity"] == complexity
    assert plan.layout["max_depth"] == 3
    assert int(plan.layout["files_per_directory_target"]) >= 4


def test_default_complexity_is_point_seven(project):
    source = many(project, 24)
    plan = build_plan(source, mode="random", seed=42)
    assert plan.configuration["complexity"] == 0.7
    assert plan.layout["files_per_directory_target"] == 6
    output = apply_plan(plan)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    assert manifest["configuration"]["complexity"] == 0.7
    assert manifest["layout"] == plan.layout


@pytest.mark.parametrize("complexity", [0.5, 0.7, 1])
def test_seed_and_complexity_produce_different_reproducible_shapes(project, complexity):
    source = many(project, 120)
    plans = [
        build_plan(source, mode="random", seed=seed, complexity=complexity) for seed in range(6)
    ]
    assert all(plan.ok for plan in plans)
    for seed, plan in enumerate(plans):
        assert (
            plan.to_dict()
            == build_plan(source, mode="random", seed=seed, complexity=complexity).to_dict()
        )
    shapes = {json.dumps(plan.layout["directories_by_depth"], sort_keys=True) for plan in plans}
    assert len(shapes) > 1
    assert len({tuple(sorted(plan.mapping.items())) for plan in plans}) == len(plans)


def test_zero_complexity_is_shallow(project):
    source = many(project, 60)
    for seed in (0, 7, 42):
        plan = build_plan(source, mode="random", seed=seed, complexity=0, max_depth=3)
        assert plan.ok, plan.to_dict()
        assert set(plan.layout["files_by_depth"]) == {"0", "1"}
        assert set(plan.layout["directories_by_depth"]) == {"1"}
        assert plan.layout["root_files"] > 0
        assert plan.layout["children_per_directory"]["max"] == 0


def test_complexity_increases_directory_count_and_target_depth(project):
    source = many(project, 200)
    shallow = build_plan(source, mode="random", seed=3, complexity=0)
    rich = build_plan(source, mode="random", seed=3, complexity=1)
    assert shallow.configuration["files_per_directory_target"] == 10
    assert rich.configuration["files_per_directory_target"] == 4
    assert shallow.layout["numbered_directories"] < rich.layout["numbered_directories"]
    assert max(map(int, shallow.layout["files_by_depth"])) == 1
    assert max(map(int, rich.layout["files_by_depth"])) == 3


@pytest.mark.parametrize("depth", [1, 2, 3, 5])
def test_max_depth_remains_a_hard_cap(project, depth):
    source = many(project, 150)
    plan = build_plan(source, mode="random", seed=11, complexity=1, max_depth=depth)
    assert plan.ok, plan.to_dict()
    assert max(map(int, plan.layout["files_by_depth"])) <= depth
    assert {int(level) for level in plan.layout["directories_by_depth"]} <= set(range(1, depth + 1))


def test_default_layout_keeps_directories_dense(project):
    source = many(project, 200)
    plan = build_plan(source, mode="random", seed=9)
    stats = plan.layout["files_per_directory"]
    assert stats["min"] >= 2
    assert stats["max"] <= plan.layout["files_per_directory_target"]
    assert stats["max"] - stats["min"] <= 1
    assert plan.layout["moved_files"] == 200


def test_rich_layout_has_mixed_nodes_and_multiple_levels(project):
    source = many(project, 120)
    plan = build_plan(source, mode="random", seed=0, complexity=1)
    assert plan.ok, plan.to_dict()
    assert set(plan.layout["directories_by_depth"]) == {"1", "2", "3"}
    assert plan.layout["files_by_depth"]["0"] > 0
    assert plan.layout["children_per_directory"]["max"] >= 2
    loads = Counter(
        str(PurePosixPath(plan.mapping[f.path]).parent)
        for f in plan.inspection.files
        if f.role == "source"
    )
    directories = {
        str(parent)
        for target in plan.mapping.values()
        for parent in PurePosixPath(target).parents
        if parent.name.startswith("module_")
    }
    assert all(loads[directory] > 0 for directory in directories)


def test_equal_filenames_remain_separate(project):
    source = project(
        {
            "demo/__init__.py": "",
            **{
                f"demo/part_{index}/{name}": "VALUE=1\n"
                for index in range(4)
                for name in ("main.py", "worker.py", "extra.py")
            },
        }
    )
    plan = build_plan(source, mode="random", seed=3, complexity=0.7)
    assert plan.ok, plan.to_dict()
    by_parent = defaultdict(set)
    for record in plan.inspection.files:
        if record.role == "source":
            target = PurePosixPath(plan.mapping[record.path])
            assert target.name.casefold() not in by_parent[str(target.parent)]
            by_parent[str(target.parent)].add(target.name.casefold())
    assert len(by_parent) == 4


@pytest.mark.parametrize(
    "value",
    [-0.1, 1.1, float("nan"), float("inf"), -float("inf"), True, False, "0.5", object()],
)
def test_invalid_complexity_is_rejected(project, value):
    source = project({"main.py": "pass\n"})
    with pytest.raises(ShuffleError, match="complexity"):
        build_plan(source, mode="random", complexity=value)


def test_complexity_is_random_only(project):
    source = project({"main.py": "pass\n"})
    with pytest.raises(ShuffleError, match="random"):
        build_plan(source, mode="flat", complexity=0.5)
    plan = build_plan(source, mode="flat")
    assert plan.ok
    assert plan.configuration["complexity"] is None


def test_small_inputs_do_not_create_empty_directories(project):
    for count, directories in ((1, 0), (2, 0), (6, 0), (7, 1)):
        source = project(
            {
                "demo/__init__.py": "",
                **{f"demo/file_{index}.py": "VALUE=1\n" for index in range(count)},
            }
        )
        plan = build_plan(source, mode="random", seed=1, complexity=0.7)
        assert plan.ok, plan.to_dict()
        assert plan.layout["numbered_directories"] == directories
        assert plan.layout["files_per_directory"]["min"] >= (1 if count == 1 else 2)


def test_cli_complexity_and_validation(project, capsys):
    source = many(project, 20)
    assert (
        main(
            [
                "shuffle",
                str(source),
                "--dry-run",
                "--mode",
                "random",
                "--seed",
                "1",
                "--complexity",
                "0.9",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["configuration"]["complexity"] == 0.9
    assert payload["layout"]["complexity"] == 0.9

    assert main(["shuffle", str(source), "--dry-run", "--complexity", "1.2"]) == 2
    assert "complexity" in capsys.readouterr().err

    assert main(["shuffle", str(source), "--dry-run", "--mode", "flat", "--complexity", "0.5"]) == 2
    assert "random" in capsys.readouterr().err

    assert main(["inspect", str(source), "--complexity", "0.5"]) == 2
    assert "requires --mode random" in capsys.readouterr().err
