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

from .models import VERSION, Plan, ShuffleError
from .scan import digest, read_tree


def _remove_tree(path: Path) -> None:
    def writable_retry(function, filename, error):
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)

    if path.exists():
        shutil.rmtree(path, onerror=writable_retry)


def _publish_entry(source: Path, target: Path) -> None:
    # Windows scanners may hold a directory briefly after its files are written.
    # Retry only sharing/access errors, never replace or merge an existing target.
    for attempt in range(5):
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


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
    selection = {key: plan.configuration.get(key) for key in ("include", "exclude")}
    fresh = read_tree(source, **selection)
    if (
        not fresh.ok
        or fresh.files != plan.inspection.files
        or fresh.excluded != plan.inspection.excluded
        or fresh.directories != plan.inspection.directories
    ):
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
            (original / directory).mkdir(parents=True, exist_ok=True)
        for directory in plan.directories:
            (shuffled / directory).mkdir(parents=True, exist_ok=True)
        for record in plan.inspection.files:
            src = source / record.path
            data = src.read_bytes()
            if digest(data) != record.sha256 or src.is_symlink():
                raise ShuffleError("Input changed while writing snapshots.")
            target = original / record.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(stat.S_IMODE(src.stat().st_mode))
        for relative, data in sorted(plan.contents.items()):
            target = shuffled / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for record in plan.inspection.files:
            mode = stat.S_IMODE((source / record.path).stat().st_mode)
            (shuffled / plan.mapping[record.path]).chmod(mode)
            if record.path in plan.contents:
                (shuffled / record.path).chmod(mode)
        summary = plan.to_dict()
        summary.update(
            {
                "schema_version": 2,
                "tool_version": VERSION,
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                    "libcst": version("libcst"),
                    "pathspec": version("pathspec"),
                    "tomlkit": version("tomlkit"),
                },
                "original_pythonpath": ["src"]
                if any(f.module and f.path.startswith("src/") for f in plan.inspection.files)
                else [],
                "entry_commands_scope": "preserved script entry paths; package imports and configured console entry points retain their names",
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
                    "checks": [
                        "syntax-compilation",
                        "unique-portable-targets",
                        "logical-route-targets",
                        "preserved-auxiliary-content",
                        "input-inventory",
                        "paired-file-inventory",
                    ],
                },
                "runtime_validation": {"status": "not-run", "responsibility": "user"},
                "snapshots": {"original": "original", "shuffled": "shuffled"},
                "working_directory": "snapshot root",
                "output_hashes": {
                    path: digest(data) for path, data in sorted(plan.contents.items())
                },
            }
        )
        (temporary / "manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        final = read_tree(source, **selection)
        if (
            not final.ok
            or final.files != fresh.files
            or final.excluded != fresh.excluded
            or final.directories != fresh.directories
        ):
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
