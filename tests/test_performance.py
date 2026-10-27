from __future__ import annotations

import os

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan


def test_publication_does_not_repeat_semantic_analysis(project, monkeypatch):
    from reposhuffle import scan

    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n"})
    plan = build_plan(source, seed=42)

    def unexpected(*args, **kwargs):
        pytest.fail("publication repeated semantic analysis")

    monkeypatch.setattr(scan, "discover", unexpected)
    monkeypatch.setattr(scan, "referenced", unexpected)
    apply_plan(plan)


def test_scan_reuses_contents_and_ast(project, monkeypatch):
    from reposhuffle import scan

    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n"})
    original = scan.referenced
    seen = set()

    def referenced(path, root, **kwargs):
        assert kwargs.get("data") == path.read_bytes()
        if path.name == "worker.py":
            assert kwargs.get("tree") is not None
        assert path not in seen
        seen.add(path)
        return original(path, root, **kwargs)

    monkeypatch.setattr(scan, "referenced", referenced)
    assert build_plan(source).ok
    assert len(seen) == 2


def test_same_size_same_mtime_change_is_rejected(project):
    source = project({"main.py": "VALUE=1\n"})
    plan = build_plan(source)
    path = source / "main.py"
    before = path.stat()
    path.write_bytes(b"VALUE=2\n")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ShuffleError, match="inventory changed"):
        apply_plan(plan)


@pytest.mark.parametrize("change", ["content", "file", "directory", "ignore"])
def test_input_mutation_during_publication_is_rejected(project, monkeypatch, change):
    from reposhuffle import output

    source = project({"main.py": "VALUE=1\n", ".gitignore": ""})
    plan = build_plan(source)
    original = output.read_inventory
    scans = 0

    def mutate_then_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        if scans == 2:
            if change == "content":
                (source / "main.py").write_bytes(b"VALUE=2\n")
            elif change == "file":
                (source / "extra.txt").write_bytes(b"new")
            elif change == "directory":
                (source / "empty").mkdir()
            else:
                (source / ".gitignore").write_bytes(b"main.py\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(output, "read_inventory", mutate_then_scan)
    with pytest.raises(ShuffleError, match="during publication"):
        apply_plan(plan)
    assert not source.with_name(source.name + "-shuffled").exists()
    assert not list(source.parent.glob(".reposhuffle-*"))


def test_locked_snapshot_falls_back_to_copy(project, monkeypatch):
    from reposhuffle import output

    source = project({"main.py": "VALUE=1\n"})
    plan = build_plan(source)

    def locked(path, target):
        error = PermissionError(13, "in use", str(path))
        error.winerror = 5
        raise error

    monkeypatch.setattr(output.Path, "rename", locked)
    monkeypatch.setattr(output.time, "sleep", lambda seconds: None)
    result = apply_plan(plan)
    assert (result / "original/main.py").read_bytes() == b"VALUE=1\n"
    assert (result / "shuffled/main.py").exists()


def test_snapshot_permissions_and_independence(project):
    import stat

    source = project({"main.py": "VALUE=1\n", "data.txt": b"data"})
    (source / "data.txt").chmod(stat.S_IREAD)
    output = apply_plan(build_plan(source))
    for snapshot in ("original", "shuffled"):
        assert (output / snapshot / "data.txt").stat().st_mode & stat.S_IWRITE == 0
    (output / "original/main.py").write_bytes(b"changed\n")
    assert (source / "main.py").read_bytes() == b"VALUE=1\n"
    assert (output / "shuffled/main.py").read_bytes() != b"changed\n"
