"""Explain static guarantees separately from runtime evidence."""

from __future__ import annotations

from collections import Counter

from .models import Diagnostic, Plan
from .project import read_config

KNOWN_BACKENDS = ("setuptools.", "poetry.core.", "hatchling.", "flit_core.")


def explain(plan: Plan) -> dict:
    config = read_config(plan.inspection.source)
    backend = config.get("build-system", {}).get("build-backend", "setuptools.build_meta")
    adapted = backend.startswith(KNOWN_BACKENDS)
    if not adapted:
        plan.diagnostics.append(
            Diagnostic(
                "UNVERIFIED_BUILD_BACKEND",
                "pyproject.toml",
                f"No delivery adapter for {backend}; verify the built wheel contains all storage packages.",
                severity="warning",
            )
        )
    preserved = Counter(
        record.reason for record in plan.inspection.files if record.role == "preserved"
    )
    candidates = [record for record in plan.inspection.files if record.path.endswith(".py")]
    moved = sum(record.role in {"source", "script"} for record in candidates)
    risks = Counter(
        diagnostic.rule
        for diagnostic in [*plan.inspection.diagnostics, *plan.diagnostics]
        if diagnostic.severity == "warning"
    )
    return {
        "validation": "static-only",
        "runtime": "not-run",
        "build": {"backend": backend, "selection_adapter": adapted, "artifact_test": "not-run"},
        "imports": {
            "logical_names": "routed",
            "physical_names": "canonical-aliases",
            "direct_file_loaders": "not-intercepted",
            "namespace_paths": "merged-dynamically",
        },
        "selection": {
            "python_files": len(candidates),
            "movable_files": moved,
            "movable_ratio": round(moved / len(candidates), 4) if candidates else 0.0,
            "preserved_by_reason": dict(sorted(preserved.items())),
        },
        "risks": dict(sorted(risks.items())),
    }
