"""Language-independent, deterministic file allocation and layout statistics."""

from __future__ import annotations

import collections
import math
import random
from pathlib import PurePosixPath

from .models import Plan, ShuffleError

DEFAULT_COMPLEXITY = 0.7
_COMPLEXITY_BRANCH = 2.0
_COMPLEXITY_CAPACITY = (10.0, 4.0)


def _capacity(complexity: float) -> int:
    high, low = _COMPLEXITY_CAPACITY
    return max(1, round(high + (low - high) * complexity))


def _validated_complexity(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShuffleError("complexity must be a finite number between 0 and 1")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ShuffleError("complexity must be a finite number between 0 and 1")
    return value


class NumberedLayout:
    def __init__(self, seed: int, depth: int, complexity: float):
        self.rng = random.Random(seed)
        self.next_directory = 1
        self.depth = depth
        self.complexity = complexity
        self.capacity = _capacity(complexity)

    def _weights(self) -> list[float]:
        # complexity=0 collapses to depth 1. Higher values favour deeper
        # levels; the seeded jitter makes the same complexity produce
        # different trees without hiding the overall trend.
        ratio = _COMPLEXITY_BRANCH * self.complexity
        weights = [ratio ** (level - 1) for level in range(1, self.depth + 1)]
        if self.complexity:
            jitter = 0.3 * self.complexity
            weights = [weight * self.rng.uniform(1 - jitter, 1 + jitter) for weight in weights]
        return weights

    def _quotas(self, count: int) -> list[int]:
        if count <= 0:
            return [0] * self.depth
        weights = self._weights()
        total = sum(weights)
        ideals = [count * weight / total for weight in weights]
        threshold = max(1.0, 0.05 * count)
        used = max((i + 1 for i, ideal in enumerate(ideals) if ideal >= threshold), default=1)
        used = min(used, count, self.depth)
        used_weights = weights[:used]
        used_total = sum(used_weights)
        remaining = count - used
        shares = [remaining * weight / used_total for weight in used_weights]
        extra = [math.floor(share) for share in shares]
        left = remaining - sum(extra)
        order = sorted(range(used), key=lambda i: (shares[i] - extra[i], -i), reverse=True)
        for index in order[:left]:
            extra[index] += 1
        return [1 + amount for amount in extra] + [0] * (self.depth - used)

    def _directories(self, zone: str, quotas: list[int]) -> list[str]:
        levels: dict[int, list[str]] = {0: [zone]}
        directories: list[str] = []
        for depth, amount in enumerate(quotas, start=1):
            if amount <= 0:
                break
            parents = levels[depth - 1]
            children: list[str] = []
            child_counts = dict.fromkeys(parents, 0)
            for _ in range(amount):
                least = min(child_counts.values())
                choices = [parent for parent, total in child_counts.items() if total == least]
                parent = self.rng.choice(choices)
                directory = f"{parent}/module_{self.next_directory}"
                self.next_directory += 1
                child_counts[parent] += 1
                children.append(directory)
            directories.extend(children)
            levels[depth] = children
        return directories

    def targets(self, zone: str, names: dict[str, str]) -> dict[str, str]:
        if not names:
            return {}
        families: dict[str, list[str]] = {}
        for path, name in names.items():
            families.setdefault(name.casefold(), []).append(path)
        max_family = max(map(len, families.values()))
        count = max(0, math.ceil(len(names) / self.capacity) - 1, max_family - 1)
        slots = [zone, *self._directories(zone, self._quotas(count))]
        loads = [0] * len(slots)
        used = [set() for _ in slots]
        result: dict[str, str] = {}
        for family in sorted(families, key=lambda key: (-len(families[key]), key)):
            members = list(families[family])
            self.rng.shuffle(members)
            for path in members:
                available = [i for i in range(len(slots)) if family not in used[i]]
                least = min(loads[i] for i in available)
                choices = [i for i in available if loads[i] == least]
                index = self.rng.choice(choices)
                used[index].add(family)
                loads[index] += 1
                result[path] = f"{slots[index]}/{names[path]}"
        return result


def _layout_stats(plan: Plan) -> dict[str, object]:
    moved = [f for f in plan.inspection.files if f.role in {"source", "script"}]
    files_by_depth: collections.Counter[int] = collections.Counter()
    directories: dict[int, set[str]] = collections.defaultdict(set)
    loads: collections.Counter[str] = collections.Counter()
    zones: set[str] = set()
    numbered: set[str] = set()
    for record in moved:
        target = plan.mapping.get(record.path)
        if target is None:
            continue
        parent = PurePosixPath(target).parent
        zone = PurePosixPath(plan.storage_roots[record.path])
        relative = parent.relative_to(zone).parts
        depth = len(relative)
        directory = zone
        for level, part in enumerate(relative, start=1):
            directory = directory / part
            directories[level].add(str(directory))
            numbered.add(str(directory))
        zones.add(str(zone))
        files_by_depth[depth] += 1
        loads[str(parent)] += 1
    branches: collections.Counter[str] = collections.Counter()
    for directory in numbered:
        parent = str(PurePosixPath(directory).parent)
        if parent in numbered:
            branches[parent] += 1

    def summarize(values: list[int]) -> dict[str, object]:
        if not values:
            return {"min": 0, "max": 0, "mean": 0.0}
        return {"min": min(values), "max": max(values), "mean": round(sum(values) / len(values), 2)}

    return {
        "complexity": plan.configuration.get("complexity"),
        "max_depth": plan.configuration.get("max_depth"),
        "files_per_directory_target": plan.configuration.get("files_per_directory_target"),
        "moved_files": len(moved),
        "preserved_files": len(plan.inspection.files) - len(moved),
        "zones": len(zones),
        "numbered_directories": len(numbered),
        "files_by_depth": {str(depth): files_by_depth[depth] for depth in sorted(files_by_depth)},
        "directories_by_depth": {
            str(depth): len(paths) for depth, paths in sorted(directories.items())
        },
        "files_per_directory": summarize(list(loads.values())),
        "children_per_directory": summarize([branches.get(directory, 0) for directory in numbered]),
        "root_files": files_by_depth.get(0, 0),
    }
