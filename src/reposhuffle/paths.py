"""Project selection shared by all language adapters."""

from pathlib import Path

from .models import ShuffleError


def select_project(source: str | Path, subproject: str | Path | None = None) -> Path:
    root = Path(source).expanduser().resolve()
    if subproject is None:
        return root
    relative = Path(subproject)
    selected = (root / relative).resolve()
    if relative.is_absolute() or not selected.is_relative_to(root):
        raise ShuffleError("Subproject must be a relative path inside the input workspace.")
    if not selected.is_dir():
        raise ShuffleError("Subproject directory does not exist.")
    return selected
