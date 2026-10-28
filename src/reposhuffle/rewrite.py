"""Minimal entry instrumentation. Business code and logical imports stay intact."""

from __future__ import annotations

import os
from pathlib import PurePosixPath

import libcst as cst


def bootstrap(
    data: bytes, runtime: str, routes: str, prefix: str, logical_root: str | None = None
) -> bytes:
    module = cst.parse_module(data)
    body = list(module.body)
    offset = 0
    if body and isinstance(body[0], cst.SimpleStatementLine):
        first = body[0].body[0]
        if isinstance(first, cst.Expr) and isinstance(
            first.value, (cst.SimpleString, cst.ConcatenatedString)
        ):
            offset = 1
    while (
        offset < len(body)
        and isinstance(body[offset], cst.SimpleStatementLine)
        and all(
            isinstance(n, cst.ImportFrom)
            and cst.helpers.get_full_name_for_node(n.module) == "__future__"
            for n in body[offset].body
        )
    ):
        offset += 1
    code = (
        f"from .{runtime} import install as _internal_install\n"
        f"from .{routes} import ROUTES as _internal_routes\n"
        f"_internal_install(__name__, __file__, _internal_routes, {prefix!r}, {logical_root!r})\n"
        f"del _internal_install, _internal_routes\n"
        f"globals().pop({runtime!r}, None)\n"
        f"globals().pop({routes!r}, None)\n"
    )
    statements = list(cst.parse_module(code).body)
    return module.with_changes(body=body[:offset] + statements + body[offset:]).bytes


def script_entry(old: str, target: str, data: bytes) -> bytes:
    relative = os.path.relpath(target, str(PurePosixPath(old).parent)).replace("\\", "/")
    header = data.splitlines(keepends=True)[0] if data.startswith(b"#!") else b""
    code = f"""# Generated entry; implementation lives at {relative}.
def _internal_launch():
    from pathlib import Path
    from importlib.machinery import SourceFileLoader
    scope = globals()
    filename = str((Path(__file__).resolve().parent / {relative!r}).resolve())
    scope["__file__"] = filename
    loader = SourceFileLoader(scope["__name__"], filename)
    scope["__loader__"] = loader
    if scope.get("__spec__") is not None:
        scope["__spec__"].origin = filename
        scope["__spec__"].loader = loader
    del scope["_internal_launch"]
    exec(compile(Path(filename).read_bytes(), filename, "exec", dont_inherit=True), scope)
_internal_launch()
"""
    return header + code.encode("utf-8")
