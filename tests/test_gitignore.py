from __future__ import annotations

import subprocess

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan, inspect_project


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_ignored_implementations_are_copied_without_relocation(project, mode):
    source = project(
        {
            ".gitignore": "demo/generated/\nlocal.py\n",
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "demo/generated/invalid.py": "intentionally invalid generated source !",
            "demo/generated/data.json": "{}",
            "local.py": "VALUE=2\n",
        }
    )
    # Explicit selection does not override the default protection.
    plan = build_plan(source, mode=mode, include=["demo/**", "local.py"], seed=42)
    assert plan.ok, plan.to_dict()
    output = apply_plan(plan)
    for path in ("demo/generated/invalid.py", "demo/generated/data.json", "local.py"):
        record = next(f for f in plan.inspection.files if f.path == path)
        assert record.role == "preserved"
        assert record.reason == "matched .gitignore rule"
        assert plan.mapping[path] == path
        assert (output / "shuffled" / path).read_bytes() == (source / path).read_bytes()
    assert plan.mapping["demo/worker.py"] != "demo/worker.py"
    assert plan.configuration["respect_gitignore"] is True


def test_nested_rules_negation_anchoring_and_excluded_parent(project):
    source = project(
        {
            ".gitignore": "*.generated.py\n/root_only.py\nblocked/\n!blocked/revive.py\n",
            "main.py": "pass\n",
            "root_only.py": "pass\n",
            "lib/.gitignore": "!keep.generated.py\nprivate.py\n",
            "lib/keep.generated.py": "pass\n",
            "lib/hidden.generated.py": "pass\n",
            "lib/private.py": "pass\n",
            "lib/root_only.py": "pass\n",
            "blocked/.gitignore": "!revive.py\n",
            "blocked/revive.py": "pass\n",
        }
    )
    result = inspect_project(source)
    assert result.ok
    ignored = {f.path for f in result.files if f.reason == "matched .gitignore rule"}
    assert ignored == {
        "root_only.py",
        "lib/hidden.generated.py",
        "lib/private.py",
        "blocked/.gitignore",
        "blocked/revive.py",
    }
    assert next(f for f in result.files if f.path == "lib/root_only.py").role == "script"


def test_gitignore_semantics_match_git_check_ignore_without_requiring_git_for_tool(project):
    source = project(
        {
            ".gitignore": "# comment\n/cache/*\n!/cache/keep.py\nblocked/\n!blocked/keep.py\n**/scratch?.py\n\\#literal.py\n",
            "main.py": "pass\n",
            "cache/a.py": "pass\n",
            "cache/keep.py": "pass\n",
            "blocked/keep.py": "pass\n",
            "lib/.gitignore": "*.tmp.py\n!safe.tmp.py\n",
            "lib/unsafe.tmp.py": "pass\n",
            "lib/safe.tmp.py": "pass\n",
            "lib/scratch1.py": "pass\n",
            "#literal.py": "pass\n",
        }
    )
    import shutil

    if shutil.which("git") is None:
        pytest.skip("Optional reference comparison requires Git")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    result = inspect_project(source)
    paths = [f.path for f in result.files]
    reference = subprocess.run(
        ["git", "-C", str(source), "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(paths) + "\0",
        capture_output=True,
        text=True,
        check=False,
    )
    assert reference.returncode in (0, 1), reference.stderr
    assert {f.path for f in result.files if f.reason == "matched .gitignore rule"} == set(
        filter(None, reference.stdout.split("\0"))
    )


def test_changed_gitignore_requires_a_new_plan(project):
    source = project({".gitignore": "", "main.py": "pass\n"})
    plan = build_plan(source)
    (source / ".gitignore").write_text("main.py\n")
    with pytest.raises(ShuffleError, match="changed"):
        apply_plan(plan)
