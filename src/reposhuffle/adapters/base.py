"""Small in-process language boundary; execution belongs to the evaluation harness."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..contents import SnapshotContents
from ..layout import NumberedLayout
from ..models import Inspection, Plan


class LanguageAdapter(Protocol):
    name: str
    modes: frozenset[str]
    markers: frozenset[str]
    suffixes: frozenset[str]
    strategy: str
    static_checks: tuple[str, ...]

    def inspect(
        self,
        source: Path,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        contents: SnapshotContents | None = None,
        source_dirs: list[str] | None = None,
    ) -> Inspection: ...

    def explain(self, plan: Plan) -> dict: ...

    def render(self, plan: Plan, layout: NumberedLayout) -> None: ...

    def validate(self, plan: Plan, changed: set[str]) -> None: ...
