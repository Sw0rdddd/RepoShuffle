"""Publish paired snapshots and keep experiment metadata outside agent views."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from .contents import SnapshotContents, copy_verified, digest
from .inventory import read_inventory
from .models import VERSION, Inspection, Plan, ShuffleError


def _remove_tree(path: Path) -> None:
    def writable_retry(function, filename, error):
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)

    if path.exists():
        shutil.rmtree(path, onerror=writable_retry)


def _copy_entry(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
        _remove_tree(source)
    else:
        shutil.copy2(source, target)
        source.unlink()


def _publish_entry(source: Path, target: Path) -> None:
    # Windows scanners may hold a directory briefly after its files are written.
    # Retry only sharing/access errors, never replace or merge an existing target.
    # A scanner can hold a directory for seconds. Retry briefly, then copy
    # instead of sleeping for a long backoff while the user waits.
    for attempt in range(8):
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32}:
                raise
            if attempt == 7:
                try:
                    _copy_entry(source, target)
                except BaseException:
                    if target.exists():
                        _remove_tree(target)
                    raise
                return
            time.sleep(min(0.05 * (2**attempt), 0.25))


def _same_inventory(current: Inspection, expected: Inspection) -> bool:
    return (
        current.ok
        and [(f.path, f.sha256, f.size) for f in current.files]
        == [(f.path, f.sha256, f.size) for f in expected.files]
        and current.excluded == expected.excluded
        and current.directories == expected.directories
    )


def apply_plan(plan: Plan, output: str | Path | None = None) -> Path:
    """Publish paired snapshots. Refuse stale input and existing/overlapping output."""
    if not plan.ok:
        raise ShuffleError(
            "Plan contains blocking diagnostics; inspect plan.to_dict() before applying."
        )
    if not plan._validated or plan._validated != plan._fingerprint():
        raise ShuffleError("Plan is unvalidated or changed after validation; build a new plan.")
    source = plan.inspection.source
    raw_output = (
        Path(output).expanduser()
        if output is not None
        else source.with_name(source.name + "-shuffled")
    )
    if os.path.lexists(raw_output):
        raise ShuffleError(f"Output already exists: {raw_output}")
    destination = raw_output.resolve()
    if (
        destination == source
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise ShuffleError("Output and input must not contain one another.")
    if not destination.parent.is_dir():
        raise ShuffleError("Output parent directory must already exist.")
    fresh = read_inventory(source, inventory_excludes=plan.inspection.inventory_excludes)
    if not _same_inventory(fresh, plan.inspection):
        raise ShuffleError("Input inventory changed since planning; build a new plan.")
    expected_targets = set(plan.mapping.values()) | set(plan.generated)
    if set(plan.contents) != expected_targets:
        raise ShuffleError("Plan has incomplete rendered contents.")
    for path in [*plan.mapping, *expected_targets, *plan.inspection.directories, *plan.directories]:
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in path or ":" in path:
            raise ShuffleError("Plan contains an unsafe output path.")
    temporary = Path(tempfile.mkdtemp(prefix=".reposhuffle-", dir=destination.parent))
    try:
        original = temporary / "original"
        shuffled = temporary / "shuffled"
        original.mkdir()
        shuffled.mkdir()
        for directory in plan.inspection.directories:
            (original / directory).mkdir()
        output_directories = set(plan.directories)
        for relative in plan.contents:
            output_directories.update(
                str(parent) for parent in PurePosixPath(relative).parents if str(parent) != "."
            )
        for directory in sorted(output_directories):
            (shuffled / directory).mkdir()
        modes: dict[str, int] = {}
        for record in plan.inspection.files:
            src = source / record.path
            metadata = src.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ShuffleError("Input changed while writing snapshots.")
            mode = stat.S_IMODE(metadata.st_mode)
            target = original / record.path
            copy_verified(src, target, record.sha256)
            target.chmod(mode)
            modes[plan.mapping[record.path]] = mode
            if record.path in plan.contents:
                modes[record.path] = mode
        for relative in sorted(plan.contents):
            target = shuffled / relative
            if isinstance(plan.contents, SnapshotContents):
                plan.contents.write_to(relative, target)
            else:
                target.write_bytes(plan.contents[relative])
            if relative in modes:
                target.chmod(modes[relative])
        summary = plan.to_dict()
        summary.update(
            {
                "schema_version": 4,
                "tool_version": VERSION,
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                    "libcst": version("libcst"),
                    "pathspec": version("pathspec"),
                    "tomlkit": version("tomlkit"),
                },
                "entry_commands_scope": "adapter-provided preserved entry commands; verify with the original project build/test procedure",
                "input_fingerprint": digest(
                    json.dumps(
                        {
                            "files": [(f.path, f.sha256) for f in plan.inspection.files],
                            "directories": plan.inspection.directories,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                ),
                "files": [asdict(f) for f in plan.inspection.files],
                "excluded": plan.inspection.excluded,
                "conversion": "success",
                "static_validation": {
                    "status": "passed",
                    "checks": [*plan.static_checks, "input-inventory", "paired-file-inventory"],
                },
                "runtime_validation": {"status": "not-run", "responsibility": "user"},
                "snapshots": {"original": "original", "shuffled": "shuffled"},
                "working_directory": "snapshot root",
                "output_hashes": dict(plan._content_hashes()),
            }
        )
        (temporary / "manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        final = read_inventory(source, inventory_excludes=plan.inspection.inventory_excludes)
        if not _same_inventory(final, fresh):
            raise ShuffleError("Input changed during publication.")
        # Reserve without replacing another process's output; publish manifest last.
        destination.mkdir()
        try:
            for name in ("original", "shuffled", "manifest.json"):
                _publish_entry(temporary / name, destination / name)
        except BaseException:
            _remove_tree(destination)
            raise
        return destination
    finally:
        _remove_tree(temporary)
