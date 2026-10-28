"""Built-in language selection, deliberately without dynamic plugin loading."""

from __future__ import annotations

from pathlib import Path

from ..inventory import _walk_tree
from ..models import Inspection, ShuffleError
from .base import LanguageAdapter
from .java import JavaAdapter
from .python import PythonAdapter

_ADAPTERS = {adapter.name: adapter for adapter in (PythonAdapter, JavaAdapter)}


def languages() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


def get_adapter(source: Path, language: str = "auto") -> LanguageAdapter:
    adapters = _ADAPTERS
    if language != "auto":
        if language not in adapters:
            raise ShuffleError(f"Unknown language: {language}; choose auto, {', '.join(adapters)}")
        return adapters[language]()
    # Root build metadata is authoritative; avoid a redundant full inventory
    # for standard projects, especially Java trees containing old build output.
    detected = {
        name
        for name, adapter in adapters.items()
        if any((source / marker).is_file() for marker in adapter.markers)
    }
    if not detected:
        inventory = Inspection(source)
        paths, ignored = _walk_tree(source, inventory)
        auxiliary = {"test", "tests", "docs", "doc", "examples", "fixtures", "build", "target"}
        for path in paths:
            relative = path.relative_to(source)
            if relative.as_posix() in ignored or auxiliary.intersection(relative.parts[:-1]):
                continue
            detected.update(
                name for name, adapter in adapters.items() if path.suffix in adapter.suffixes
            )
    if len(detected) > 1:
        choices = " or ".join(f"--language {name}" for name in sorted(detected))
        raise ShuffleError(f"Multiple languages detected; choose {choices}")
    # Retain discovery of empty/auxiliary-only Python inputs for API compatibility.
    return adapters[next(iter(detected), "python")]()
