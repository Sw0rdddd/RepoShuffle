"""Reproducible paired Python repository snapshots for agent experiments."""

from .models import VERSION, Diagnostic, Inspection, Plan, ShuffleError
from .output import apply_plan
from .plan import build_plan, inspect_project

__version__ = VERSION
__all__ = [
    "Diagnostic",
    "Inspection",
    "Plan",
    "ShuffleError",
    "__version__",
    "apply_plan",
    "build_plan",
    "inspect_project",
]
