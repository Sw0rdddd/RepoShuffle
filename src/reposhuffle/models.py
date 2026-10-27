"""Public, serializable inspection and planning results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VERSION = "0.3.3"


@dataclass(frozen=True)
class Diagnostic:
    rule: str
    path: str
    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size: int
    module: str | None = None
    is_package: bool = False
    role: str = "preserved"
    owner: str | None = None
    reason: str = "outside selected implementation areas"


@dataclass
class Inspection:
    source: Path
    files: list[FileRecord] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    packages: dict[str, str] = field(default_factory=dict)
    script_roots: list[str] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    source_roots: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "ok": self.ok,
            "files": [asdict(f) for f in self.files],
            "excluded": self.excluded,
            "packages": self.packages,
            "script_roots": self.script_roots,
            "source_roots": self.source_roots,
            "directories": self.directories,
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }


@dataclass
class Plan:
    inspection: Inspection
    mode: str
    seed: int
    configuration: dict[str, Any] = field(default_factory=dict)
    mapping: dict[str, str] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    generated: dict[str, str] = field(default_factory=dict)
    # Validated output bytes are private; no writes occur while building a plan.
    contents: MutableMapping[str, bytes] = field(default_factory=dict, repr=False)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    entry_commands: list[str] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    layout: dict[str, Any] = field(default_factory=dict)
    compatibility: dict[str, Any] = field(default_factory=dict)
    _validated: str = field(default="", repr=False)

    def _content_hashes(self) -> list[tuple[str, str]]:
        from .contents import SnapshotContents

        if isinstance(self.contents, SnapshotContents):
            return self.contents.hashes()
        return [
            (path, hashlib.sha256(self.contents[path]).hexdigest())
            for path in sorted(self.contents)
        ]

    def _fingerprint(self) -> str:
        payload = {
            "plan": self.to_dict(),
            "files": [asdict(f) for f in self.inspection.files],
            "rendered": self._content_hashes(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()

    @property
    def ok(self) -> bool:
        return self.inspection.ok and not any(d.severity == "error" for d in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.inspection.source),
            "mode": self.mode,
            "seed": self.seed,
            "configuration": self.configuration,
            "ok": self.ok,
            "mapping": self.mapping,
            "modules": self.modules,
            "dependencies": self.dependencies,
            "generated": self.generated,
            "entry_commands": self.entry_commands,
            "directories": self.directories,
            "input_directories": self.inspection.directories,
            "layout": self.layout,
            "compatibility": self.compatibility,
            "changes": self.changes,
            "diagnostics": [asdict(d) for d in self.inspection.diagnostics + self.diagnostics],
        }


class ShuffleError(Exception):
    """An input or plan cannot safely be published."""
