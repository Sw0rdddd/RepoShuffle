"""Conservative literal filesystem anchors; never execute Java expressions."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import Inspection

_STRING = r'"(?:\\.|[^"\\])*"'
_CALL = re.compile(
    r"(?:new\s+(?:java\.io\.)?File|(?:java\.nio\.file\.)?(?:Paths\.get|Path\.of))\s*\(([^()]*)\)"
)
_DECLARATION = re.compile(r"\b(?:File|Path|String|var)\s+([\w$]+)\s*=\s*([^;]+);")
_TOKEN = re.compile(_STRING + r"|[\w$]+")
_COMMENT = re.compile(_STRING + r"|//[^\r\n]*|/\*.*?\*/", re.DOTALL)


def source_anchors(inspection: Inspection, projects: dict[Path, str]) -> set[str]:
    """Keep explicitly referenced source files, not entire observed directories."""
    candidates = {record.path for record in inspection.files if record.role == "source"}
    anchors = set()
    for record in inspection.files:
        if not record.path.endswith(".java") or record.reason == "matched .gitignore rule":
            continue
        path = inspection.source / record.path
        owners = [project for project in projects if path.is_relative_to(project)]
        if not owners:
            continue
        project = max(owners, key=lambda owner: len(owner.parts))
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            continue
        text = _COMMENT.sub(lambda match: match[0] if match[0].startswith('"') else " ", text)
        values: dict[str, str] = {}

        def argument(token: str, values=values) -> str | None:
            if token.startswith('"'):
                try:
                    return json.loads(token)
                except ValueError:
                    return None
            return values.get(token)

        def resolve(expression: str, project=project, argument=argument) -> Path | None:
            call = _CALL.fullmatch(expression.strip())
            if call is None:
                return None
            arguments = call[1]
            if _TOKEN.sub("", arguments).replace(",", "").strip():
                return None
            parts = [argument(token[0]) for token in _TOKEN.finditer(arguments)]
            if not parts or any(part is None for part in parts):
                return None
            result = project.joinpath(*parts).resolve()
            return result if result.is_relative_to(inspection.source) else None

        for declaration in _DECLARATION.finditer(text):
            name, expression = declaration.groups()
            result = resolve(expression)
            if result is not None:
                values[name] = str(result)
                relative = result.relative_to(inspection.source).as_posix()
                if relative in candidates:
                    anchors.add(relative)
            elif re.fullmatch(_STRING, expression.strip()):
                value = argument(expression.strip())
                if value is not None:
                    values[name] = value
        for call in _CALL.finditer(text):
            result = resolve(call[0])
            if result is not None:
                relative = result.relative_to(inspection.source).as_posix()
                if relative in candidates:
                    anchors.add(relative)
    return anchors
