"""Shared collision-safe storage allocation and audited byte relocation."""

from pathlib import PurePosixPath

from .contents import SnapshotContents
from .models import Plan, ShuffleError


def storage_prefix(plan: Plan, directory: str) -> str:
    root = PurePosixPath(directory)
    entries = {
        PurePosixPath(path).relative_to(root).parts[0]
        for path in [*(f.path for f in plan.inspection.files), *plan.inspection.directories]
        if PurePosixPath(path) != root and PurePosixPath(path).is_relative_to(root)
    }
    prefix, index = "_internal", 0
    while any(name.startswith(prefix) for name in entries):
        index += 1
        prefix = f"_internal{index}"
    return prefix


def relocate(plan: Plan, targets: dict[str, str], zone: str) -> None:
    used = {path.casefold() for path in plan.contents}
    for source, target in targets.items():
        if target.casefold() in used:
            raise ShuffleError(f"Target collision: {target}")
        used.add(target.casefold())
        if isinstance(plan.contents, SnapshotContents):
            plan.contents.move(source, target)
        else:
            plan.contents[target] = plan.contents.pop(source)
        plan.mapping[source] = target
        plan.storage_roots[source] = zone
        plan.changes.append({"kind": "move", "path": source, "target": target})
