"""Find source files read through filesystem paths, without executing code.

The evaluator intentionally understands only common path construction. It is
used to keep a small, evidence-based anchor when a build script or test reads
another source file by path.
"""

from __future__ import annotations

import ast
import doctest
import os
from pathlib import Path


def location_sensitive(tree: ast.Module) -> bool:
    """Include executable doctest examples, not arbitrary mentions in prose."""
    if any(isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(tree)):
        return True
    scanners = {"walk", "glob", "rglob", "iglob", "listdir", "scandir", "iterdir", "iter_modules"}

    def calls_scan(node):
        return any(
            isinstance(call, ast.Call)
            and (
                isinstance(call.func, ast.Name)
                and call.func.id in scanners
                or isinstance(call.func, ast.Attribute)
                and call.func.attr in scanners
            )
            for call in ast.walk(node)
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and calls_scan(node):
            scanners.add(node.name)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        doc = ast.get_docstring(node)
        if not doc:
            continue
        try:
            examples = doctest.DocTestParser().get_examples(doc)
        except ValueError:
            continue
        trees = []
        for example in examples:
            try:
                trees.append(ast.parse(example.source))
            except SyntaxError:
                continue
        # Keeping only the scanning file cannot preserve its neighbours. Do not
        # freeze a whole implementation area to satisfy a layout-dependent example.
        if any(calls_scan(example_tree) for example_tree in trees):
            continue
        if any(
            isinstance(n, ast.Name) and n.id == "__file__"
            for example_tree in trees
            for n in ast.walk(example_tree)
        ):
            return True
    return False


class _Evaluator:
    def __init__(self, filename: Path):
        self.filename = filename
        self.env: dict[str, object] = {}
        self.names: dict[str, str] = {}
        self.references: set[Path] = set()
        self.directories: set[Path] = set()
        self.dynamic = False
        self.scans: set[str] = set()

    def visit(self, tree: ast.Module) -> None:
        self.block(tree.body)

    def block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.statement(statement)

    def bind(self, target: ast.AST, value: object) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
            self.names[target.id] = ""
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = value if isinstance(value, (list, tuple)) else []
            for item, entry in zip(target.elts, values, strict=False):
                self.bind(item, entry)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                self.names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Assign):
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
            self.names[node.name] = ""
            previous_env, previous_names = self.env.copy(), self.names.copy()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg]
                for argument in arguments:
                    self.env[argument.arg] = None
                    self.names[argument.arg] = ""
            self.block(node.body)
            self.env, self.names = previous_env, previous_names
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.expression(item.context_expr)
            self.block(node.body)
        elif isinstance(node, ast.If):
            self.expression(node.test)
            original_env, original_names = self.env.copy(), self.names.copy()
            self.block(node.body)
            left_env, left_names = self.env, self.names
            self.env, self.names = original_env, original_names
            self.block(node.orelse)
            self.env = {key: value for key, value in left_env.items() if self.env.get(key) == value}
            self.names = {
                key: value for key, value in left_names.items() if self.names.get(key) == value
            }
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

    def qualified(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.names.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.qualified(node.value)
            return f"{base}.{node.attr}" if base else ""
        return ""

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
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self.expression(child)
            return None
        name = self.qualified(node.func)
        last = node.func.attr if isinstance(node.func, ast.Attribute) else name.rsplit(".", 1)[-1]
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
            value = (
                arguments[1]
                if len(arguments) > 1
                else next(
                    (self.expression(kw.value) for kw in node.keywords if kw.arg == "location"),
                    None,
                )
            )
            self.record(value)
            self.dynamic = self.dynamic or not isinstance(value, (str, Path))
            return None
        if last in {"SourceFileLoader", "FileLoader"}:
            value = arguments[1] if len(arguments) > 1 else None
            self.record(value)
            self.dynamic = self.dynamic or not isinstance(value, (str, Path))
            return None
        if name == "pathlib.Path":
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
        if last in {"walk", "glob", "rglob", "iglob"}:
            self.scans.add(name or last)
            if isinstance(node.func, ast.Attribute):
                self.expression(node.func.value)
            return None
        if last in {"iter_modules", "listdir", "scandir"}:
            argument = node.args[0] if node.args else None
            if isinstance(argument, (ast.List, ast.Tuple)):
                for entry in argument.elts:
                    self.record_directory(self.expression(entry))
            else:
                value = self.expression(argument)
                self.record_directory(value)
                if value is None:
                    self.scans.add(name or last)
            return None
        if last == "iterdir":
            value = (
                self.expression(node.func.value) if isinstance(node.func, ast.Attribute) else None
            )
            self.record_directory(value)
            return None
        if name in {f"os.path.{part}" for part in ("dirname", "abspath", "realpath", "normpath")}:
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
        if isinstance(node.func, ast.Attribute):
            self.expression(node.func.value)
        for keyword in node.keywords:
            self.expression(keyword.value)
        return None


def referenced(
    path: Path, source: Path, *, data: bytes | None = None, tree: ast.Module | None = None
) -> tuple[set[Path], set[Path], bool, set[str]]:
    """Return anchors, directory anchors, unknown loaders and scan risks."""
    if tree is None:
        try:
            tree = ast.parse(data if data is not None else path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            return set(), set(), False, set()
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

    return (
        resolve(evaluator.references),
        resolve(evaluator.directories),
        evaluator.dynamic,
        evaluator.scans,
    )
