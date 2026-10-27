"""Keep unchanged assets on disk; cache only code and rendered replacements."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .models import ShuffleError


@dataclass(frozen=True)
class DeferredFile:
    source: Path
    sha256: str


def file_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def copy_verified(source: Path, target: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    if digest.hexdigest() != expected:
        raise ShuffleError("Input changed while writing snapshots.")


class SnapshotContents(MutableMapping[str, bytes]):
    def __init__(self):
        self._entries: dict[str, bytes | DeferredFile] = {}

    def defer(self, relative: str, source: Path, sha256: str) -> None:
        self._entries[relative] = DeferredFile(source, sha256)

    def __getitem__(self, key: str) -> bytes:
        entry = self._entries[key]
        if isinstance(entry, bytes):
            return entry
        data = entry.source.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise ShuffleError("Input changed since planning; build a new plan.")
        return data

    def __setitem__(self, key: str, value: bytes) -> None:
        self._entries[key] = value

    def __delitem__(self, key: str) -> None:
        del self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def hashes(self) -> list[tuple[str, str]]:
        return [
            (
                name,
                entry.sha256
                if isinstance(entry, DeferredFile)
                else hashlib.sha256(entry).hexdigest(),
            )
            for name, entry in sorted(self._entries.items())
        ]

    def write_to(self, relative: str, target: Path) -> None:
        entry = self._entries[relative]
        if isinstance(entry, DeferredFile):
            copy_verified(entry.source, target, entry.sha256)
        else:
            target.write_bytes(entry)
