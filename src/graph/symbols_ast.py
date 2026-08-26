"""AST helpers for Python symbol discovery."""

from __future__ import annotations

import ast
from typing import Final

_ROUTE_METHOD_DECORATORS: Final[frozenset[str]] = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put"}
)


def collect_module(
    tree: ast.AST,
    relative_path: str,
    definitions: dict[str, list[tuple[str, int, str]]],
    invocations: dict[str, dict[str, int]],
    routes: list[tuple[str, str, str]],
) -> None:
    """Collect symbol definitions, invocations, and route decorators from one module."""

    for node in ast.walk(tree):
        match node:
            case ast.FunctionDef(name=name, lineno=lineno) | ast.AsyncFunctionDef(name=name, lineno=lineno):
                definitions[name].append((relative_path, lineno, type(node).__name__))
                routes.extend((relative_path, method, route_path) for method, route_path in route_entries(node))
            case ast.ClassDef(name=name, lineno=lineno):
                definitions[name].append((relative_path, lineno, type(node).__name__))
            case ast.Call(func=func):
                callee_name = call_name(func)
                if callee_name is not None:
                    invocations[callee_name][relative_path] += 1


def call_name(node: ast.expr) -> str | None:
    """Return the invoked symbol name when it can be determined cheaply."""

    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=attr):
            return attr
        case _:
            return None


def route_entries(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[tuple[str, str], ...]:
    """Return HTTP route declarations attached to a function node."""

    entries: list[tuple[str, str]] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        route_path = route_path_literal(decorator)
        if route_path is None:
            continue
        decorator_name = decorator.func.attr.lower()
        if decorator_name in _ROUTE_METHOD_DECORATORS:
            entries.append((decorator_name.upper(), route_path))
            continue
        if decorator_name != "route":
            continue
        methods = route_methods(decorator)
        entries.extend((method, route_path) for method in (methods or ("ANY",)))
    return tuple(entries)


def route_path_literal(decorator: ast.Call) -> str | None:
    """Read the route path from positional or keyword decorator arguments."""

    if decorator.args:
        return string_literal(decorator.args[0])
    for keyword in decorator.keywords:
        if keyword.arg in {"path", "rule"}:
            return string_literal(keyword.value)
    return None


def route_methods(decorator: ast.Call) -> tuple[str, ...]:
    """Read explicit route methods from a generic route decorator."""

    for keyword in decorator.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, ast.List | ast.Tuple):
            methods = [method.upper() for item in keyword.value.elts if (method := string_literal(item)) is not None]
            return tuple(sorted(methods))
    return ()


def string_literal(node: ast.AST) -> str | None:
    """Return a constant string literal value when present."""

    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None
