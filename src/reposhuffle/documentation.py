"""Conservative Markdown file-link repair, with warnings for other path mentions."""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from .models import Diagnostic, Plan

# Only unambiguous destinations are edited; unsupported syntax is left for warnings.
_DESTINATION = r"(?P<destination><[^<>\r\n]+>|(?:\\[^\r\n]|[^\s()<>\\]|\([^()\r\n]*\))+)"
_INLINE = re.compile(
    r"!?\[(?:\\.|[^\[\]\\\r\n])*\]\([ \t]*"
    + _DESTINATION
    + r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\([^()\r\n]*\)))?[ \t]*\)"
)
_DEFINITION = re.compile(
    r"^ {0,3}\[(?:\\.|[^\[\]\\\r\n])+\]:[ \t]*"
    + _DESTINATION
    + r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\([^()\r\n]*\)))?[ \t]*\r?$",
    re.MULTILINE,
)
_EXTERNAL = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*:|//)[^\s<>\"')]+")
_PATH = re.compile(r"(?<![\w./%\\-])(?:\.{1,2}/|/)?[\w.%~()\\-]+(?:/[\w.%~()\\-]+)*/?")
_QUOTED_PATH = re.compile(r"`+([^`\r\n]+)`+|<([^<>\r\n]+)>|\"([^\"\r\n]+)\"|'([^'\r\n]+)'")
_UNESCAPE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])")
_DOCUMENTS = {".md", ".markdown", ".rst", ".txt", ".mdx", ".html", ".htm", ".adoc"}


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def _prose(text: str) -> str:
    """Mask code and HTML without changing offsets; never edit example links."""
    text = re.sub(r"<!--[\s\S]*?(?:-->|$)", lambda match: _blank(match[0]), text)
    lines = []
    fence = None
    html = False
    for line in text.splitlines(keepends=True):
        marker = re.match(r"[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})", line)
        if fence:
            lines.append(_blank(line))
            if re.match(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\r?\n?$", line):
                fence = None
        elif marker:
            fence = marker[1]
            lines.append(_blank(line))
        elif html or re.match(r" {0,3}</?[A-Za-z][\w-]*(?:\s|/?>)", line):
            # Conservatively mask an HTML block until a blank line.
            html = bool(line.strip())
            lines.append(_blank(line))
        elif line.startswith(("    ", "\t")) or re.match(r" {0,3}>", line):
            lines.append(_blank(line))
        else:
            lines.append(line)
    text = "".join(lines)
    text = re.sub(
        r"<([A-Za-z][\w-]*)\b[^>]*>[\s\S]*?</\1\s*>",
        lambda match: _blank(match[0]),
        text,
    )
    angles = {
        match.start("destination")
        for pattern in (_INLINE, _DEFINITION)
        for match in pattern.finditer(text)
        if match["destination"].startswith("<")
    }
    text = re.sub(
        r"</?[A-Za-z][\w-]*(?:\s[^>\n]*|/?)>",
        lambda match: match[0] if match.start() in angles else _blank(match[0]),
        text,
    )
    text = re.sub(r"(`+)([\s\S]*?)(?<!`)\1(?!`)", lambda match: _blank(match[0]), text)
    return text


def _local_path(destination: str, document: str) -> tuple[str, str] | None:
    value = destination[1:-1] if destination.startswith("<") else destination
    value = _UNESCAPE.sub(r"\1", value)
    if not value or value.startswith(("#", "/")) or re.match(r"[A-Za-z][\w+.-]*:", value):
        return None
    parts = re.split(r"(?=[?#])", value, maxsplit=1)
    path = unquote(parts[0])
    if "\\" in path:
        return None
    resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(document).parent), path))
    return resolved, parts[1] if len(parts) > 1 else ""


def rewrite_documentation(plan: Plan) -> None:
    moved = {old: new for old, new in plan.mapping.items() if old != new}
    if not moved:
        return
    directories = {
        str(parent) for old in moved for parent in PurePosixPath(old).parents if str(parent) != "."
    }
    for record in plan.inspection.files:
        # A future adapter may classify text/HTML as implementation, not docs.
        if record.role in {"source", "script"}:
            continue
        document = record.path
        suffix = PurePosixPath(document).suffix.lower()
        if suffix not in _DOCUMENTS:
            continue
        data = plan.contents[document]
        bom = data.startswith(b"\xef\xbb\xbf")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            plan.diagnostics.append(
                Diagnostic(
                    "DOCUMENTATION_ENCODING",
                    document,
                    "Document is not UTF-8; path references were not checked or rewritten.",
                    severity="warning",
                )
            )
            continue
        replacements = []
        handled = []
        protected = record.reason == "matched .gitignore rule" or any(
            part in {"test", "tests", "fixtures"} for part in PurePosixPath(document).parts[:-1]
        )
        if suffix in {".md", ".markdown"} and not protected:
            prose = _prose(text)
            matches = sorted(
                [*_INLINE.finditer(prose), *_DEFINITION.finditer(prose)],
                key=lambda match: match.start("destination"),
            )
            for match in matches:
                start, end = match.span("destination")
                preceding = text[: match.start()]
                backslashes = len(preceding) - len(preceding.rstrip("\\"))
                if backslashes % 2 or (handled and start < handled[-1][1]):
                    continue
                original = text[match.start() : match.end()]
                if not (_INLINE.fullmatch(original) or _DEFINITION.fullmatch(original)):
                    continue
                destination = text[start:end]
                local = _local_path(destination, document)
                if local is None or local[0] not in moved:
                    continue
                old, fragment = local
                relative = posixpath.relpath(moved[old], str(PurePosixPath(document).parent))
                angle = destination.startswith("<")
                if "%" in destination:
                    relative = quote(relative, safe="/.-_~")
                elif not angle:
                    relative = (
                        relative.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                    )
                    relative = relative.replace(" ", "%20").replace("#", "%23").replace("?", "%3F")
                if destination.lstrip("<").startswith("./") and not relative.startswith("."):
                    relative = "./" + relative
                updated = relative + fragment
                if angle:
                    updated = "<" + updated + ">"
                replacements.append((start, end, updated))
                handled.append((start, end))
                line = text.count("\n", 0, start) + 1
                plan.changes.append(
                    {
                        "kind": "documentation-reference",
                        "path": document,
                        "line": line,
                        "old": destination,
                        "new": updated,
                        "source": old,
                        "target": moved[old],
                    }
                )
                if "#" in fragment:
                    plan.diagnostics.append(
                        Diagnostic(
                            "DOCUMENTATION_ANCHOR",
                            document,
                            "File link updated; its anchor or line fragment was retained but not verified.",
                            line=line,
                            severity="warning",
                        )
                    )
        # Warnings also scan examples and non-Markdown documents, but not external URLs.
        warning_text = list(text)
        for start, end in [*handled, *(match.span() for match in _EXTERNAL.finditer(text))]:
            warning_text[start:end] = _blank(text[start:end])
        warning_text = "".join(warning_text)
        candidates = [(match.start(), match[0]) for match in _PATH.finditer(warning_text)]
        for match in _QUOTED_PATH.finditer(warning_text):
            group = next(i for i in range(1, 5) if match[i] is not None)
            candidates.append((match.start(group), match[group]))
        warned = set()
        for start, token in sorted(candidates):
            token = token.lstrip("(").rstrip(".,;)")
            if "/" not in token and not PurePosixPath(token).suffix:
                continue
            local = _local_path(token, document)
            resolved = local[0] if local else ""
            root_path = unquote(_UNESCAPE.sub(r"\1", token)).strip("/")
            if (
                resolved not in moved
                and resolved not in directories
                and root_path not in moved
                and root_path not in directories
            ):
                continue
            line = text.count("\n", 0, start) + 1
            if (line, token) in warned:
                continue
            warned.add((line, token))
            plan.diagnostics.append(
                Diagnostic(
                    "DOCUMENTATION_PATH",
                    document,
                    f"Path reference {token!r} may be affected by relocation; left unchanged for manual review.",
                    line=line,
                    column=start - text.rfind("\n", 0, start),
                    severity="warning",
                )
            )
        if replacements:
            for start, end, updated in reversed(replacements):
                text = text[:start] + updated + text[end:]
            plan.contents[document] = text.encode("utf-8-sig" if bom else "utf-8")
