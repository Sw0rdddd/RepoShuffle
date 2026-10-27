"""Find source files read through filesystem paths, without executing code.

The evaluator intentionally understands only common path construction. It is
used to keep a small, evidence-based anchor when a build script or test reads
another source file by path.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


class _Evaluator:
    def __init__(self, filename: Path):
        self.filename = filename
        self.env: dict[str, object] = {}
        self.references: set[Path] = set()
        self.directories: set[Path] = set()
        self.dynamic = False

    def visit(self, tree: ast.Module) -> None:
        self.block(tree.body)

    def block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.statement(statement)

    def bind(self, target: ast.AST, value: object) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = value if isinstance(value, (list, tuple)) else []
            for item, entry in zip(target.elts, values, strict=False):
                self.bind(item, entry)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            value = self.expression(node.value)
            for target in node.targets:
                self.bind(target, value)
        elif isinstance(node, ast.AnnAssign):
            self.bind(node.target, self.expression(node.value) if node.value else None)
        elif isinstance(node, ast.For):
            values = self.expression(node.iter)
            if isinstance(values, list):
                for value in values:
                    self.bind(node.target, value)
                    self.block(node.body)
            else:
                self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.block(node.body)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.expression(item.context_expr)
            self.block(node.body)
        elif isinstance(node, ast.If):
            self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, ast.Try):
            self.block(node.body)
            for handler in node.handlers:
                self.block(handler.body)
            self.block(node.orelse)
            self.block(node.finalbody)
        elif isinstance(node, (ast.Expr, ast.Return)):
            self.expression(node.value)

    def record(self, value: object) -> None:
        if isinstance(value, Path):
            self.references.add(value)
        elif isinstance(value, str) and value:
            self.references.add(Path(value))

    def record_directory(self, value: object) -> None:
        if isinstance(value, Path):
            self.directories.add(value)
        elif isinstance(value, str) and value:
            self.directories.add(Path(value))

    def expression(self, node: ast.AST | None) -> object:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return str(self.filename) if node.id == "__file__" else self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self.expression(node.value)
            if node.attr in {"pardir", "curdir", "sep"}:
                return {"pardir": "..", "curdir": ".", "sep": os.sep}[node.attr]
            if node.attr == "parent" and isinstance(base, (str, Path)):
                return Path(base).parent
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self.expression(node.left)
            right = self.expression(node.right)
            if isinstance(left, (str, Path)) and isinstance(right, (str, Path)):
                return Path(left) / str(right)
            return None
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self.expression(item) for item in node.elts]
            return values if all(isinstance(value, str) for value in values) else None
        if isinstance(node, ast.JoinedStr):
            parts = [self.expression(value) for value in node.values]
            return "".join(parts) if all(isinstance(part, str) for part in parts) else None
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            for generator in node.generators:
                self.expression(generator.iter)
                for condition in generator.ifs:
                    self.expression(condition)
            self.expression(node.elt)
            return None
        if not isinstance(node, ast.Call):
            return None
        name = _name(node.func)
        last = name.rsplit(".", 1)[-1]
        arguments = [self.expression(argument) for argument in node.args]
        if last == "open":
            value = (
                self.expression(node.func.value) if isinstance(node.func, ast.Attribute) else None
            )
            self.record(arguments[0] if arguments else value)
            return None
        if last in {"read_text", "read_bytes"}:
            value = (
                self.expression(node.func.value) if isinstance(node.func, ast.Attribute) else None
            )
            self.record(value)
            return value
        if last in {"exec", "compile"}:
            self.record(arguments[0] if arguments else None)
            return None
        if last == "spec_from_file_location":
            value = arguments[1] if len(arguments) > 1 else None
            self.record(value)
            self.dynamic = self.dynamic or not isinstance(value, (str, Path))
            return None
        if last in {"SourceFileLoader", "FileLoader"}:
            value = arguments[1] if len(arguments) > 1 else None
            self.record(value)
            self.dynamic = self.dynamic or not isinstance(value, (str, Path))
            return None
        if last == "Path":
            return Path(str(arguments[0])) if arguments and arguments[0] is not None else None
        if last in {"enumerate", "reversed"} and arguments and isinstance(arguments[0], list):
            return (
                list(enumerate(arguments[0]))
                if last == "enumerate"
                else list(reversed(arguments[0]))
            )
        if last == "zip" and arguments and all(isinstance(value, list) for value in arguments):
            return list(zip(*arguments))
        if last in {"join", "joinpath"}:
            base = (
                self.expression(node.func.value)
                if last == "joinpath" and isinstance(node.func, ast.Attribute)
                else None
            )
            values = ([base] if base is not None else []) + arguments
            if values and all(isinstance(value, (str, Path)) for value in values):
                return (
                    os.path.join(*(str(value) for value in values))
                    if last == "join"
                    else Path(values[0]).joinpath(*(str(value) for value in values[1:]))
                )
            if len(values) > 1 and all(isinstance(value, str) for value in values[1:]):
                # A function parameter (for example a package path) may be
                # unknown statically; keep its constant relative suffix.
                return os.path.join(*(str(value) for value in values[1:]))
            return None
        if last in {"iter_modules", "listdir", "scandir"}:
            argument = node.args[0] if node.args else None
            if isinstance(argument, (ast.List, ast.Tuple)):
                for entry in argument.elts:
                    self.record_directory(self.expression(entry))
            else:
                self.record_directory(self.expression(argument))
            return None
        if last == "iterdir":
            value = (
                self.expression(node.func.value) if isinstance(node.func, ast.Attribute) else None
            )
            self.record_directory(value)
            return None
        if last in {"dirname", "abspath", "realpath", "normpath"}:
            value = arguments[0] if arguments else None
            if not isinstance(value, (str, Path)):
                return None
            function = getattr(os.path, last)
            return function(str(value))
        if last in {"resolve", "absolute"}:
            value = (
                self.expression(node.func.value) if isinstance(node.func, ast.Attribute) else None
            )
            return Path(value).resolve() if isinstance(value, (str, Path)) else None
        return None


def referenced(
    path: Path, source: Path, *, data: bytes | None = None, tree: ast.Module | None = None
) -> tuple[set[Path], set[Path], bool]:
    """Return resolved files, listed directories, and whether scanning is dynamic."""
    if tree is None:
        try:
            tree = ast.parse(data if data is not None else path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            return set(), set(), False
    evaluator = _Evaluator(path)
    evaluator.visit(tree)

    def resolve(values: set[Path]) -> set[Path]:
        resolved: set[Path] = set()
        for reference in values:
            candidates = (
                [reference]
                if reference.is_absolute()
                else [path.parent / reference, source / reference]
            )
            for candidate in candidates:
                try:
                    actual = candidate.resolve()
                except (OSError, RuntimeError):
                    continue
                if actual.is_relative_to(source):
                    resolved.add(actual)
        return resolved

    return resolve(evaluator.references), resolve(evaluator.directories), evaluator.dynamic
