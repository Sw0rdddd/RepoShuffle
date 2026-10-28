"""Reproducible paired repository snapshots using shared layouts and language adapters."""

from .models import VERSION, Diagnostic, Inspection, Plan, ShuffleError, SourceUnit
from .output import apply_plan
from .plan import build_plan, inspect_project

__version__ = VERSION
__all__ = [
    "Diagnostic",
    "Inspection",
    "Plan",
    "ShuffleError",
    "SourceUnit",
    "__version__",
    "apply_plan",
    "build_plan",
    "inspect_project",
]
