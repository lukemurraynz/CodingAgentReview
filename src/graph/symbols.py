"""Repository-wide Python symbol discovery helpers."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .symbols_ast import collect_module

REGISTRATION_PATTERN: Final[str] = (
    r"(?:add_singleton|add_scoped|add_transient|register_instance|register_type|"
    r"add_keyed_singleton|add_hosted_service)\s*\(\s*[\w.]+\s*,\s*(\w+)\b"
    r"|@inject\s*\n(?:\s*)class\s+(\w+)"
)
REGISTRATION_REGEX: Final[re.Pattern[str]] = re.compile(REGISTRATION_PATTERN)
_EXCLUDED_DIR_NAMES: Final[frozenset[str]] = frozenset({".git", ".venv", "__pycache__", "node_modules"})

type DefinitionSite = tuple[str, int, str]
type InvocationSite = tuple[str, int]
type RegistrationSite = tuple[str, int]
type RouteDefinition = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class InvocationStat:
    total_count: int
    per_path: tuple[InvocationSite, ...]


@dataclass(frozen=True, slots=True)
class ParseIssue:
    path: str
    lineno: int
    column: int
    message: str


@dataclass(frozen=True, slots=True)
class SymbolIndex:
    definitions: dict[str, tuple[DefinitionSite, ...]]
    invocations: dict[str, InvocationStat]
    registrations: dict[str, tuple[RegistrationSite, ...]]
    routes: tuple[RouteDefinition, ...]
    parse_issues: tuple[ParseIssue, ...]

    def invocation_count(self, name: str) -> int:
        """Return the repo-wide call-site count for a symbol name."""

        stat = self.invocations.get(name)
        return 0 if stat is None else stat.total_count

    def is_referenced_elsewhere(self, name: str) -> bool:
        """Return whether the symbol has any repo-wide call-site references."""

        return self.invocation_count(name) > 0


def build_symbol_index(repo_root: str | Path, *, include_tests: bool = False) -> SymbolIndex:
    """Scan a repository root and return a deterministic repo-wide Python symbol index."""

    root = Path(repo_root)
    if not root.is_dir():
        raise ValueError(f"repository root does not exist: {root}")

    definitions: dict[str, list[DefinitionSite]] = defaultdict(list)
    invocations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    registrations: dict[str, list[RegistrationSite]] = defaultdict(list)
    routes: list[RouteDefinition] = []
    parse_issues: list[ParseIssue] = []

    for file_path in _iter_python_files(root, include_tests=include_tests):
        relative_path = file_path.relative_to(root).as_posix()
        source = _read_source(file_path, relative_path, parse_issues)
        if source is None:
            continue
        registrations.update(_merge_registrations(registrations, source, relative_path))
        tree = _parse_tree(source, relative_path, parse_issues)
        if tree is None:
            continue
        collect_module(tree, relative_path, definitions, invocations, routes)

    return SymbolIndex(
        definitions={name: tuple(sorted(sites)) for name, sites in sorted(definitions.items())},
        invocations={
            name: InvocationStat(total_count=sum(per_path.values()), per_path=tuple(sorted(per_path.items())))
            for name, per_path in sorted(invocations.items())
        },
        registrations={name: tuple(sorted(sites)) for name, sites in sorted(registrations.items())},
        routes=tuple(sorted(routes)),
        parse_issues=tuple(
            sorted(parse_issues, key=lambda issue: (issue.path, issue.lineno, issue.column, issue.message))
        ),
    )


def _iter_python_files(root: Path, *, include_tests: bool) -> tuple[Path, ...]:
    files = [
        path
        for path in root.rglob("*.py")
        if path.is_file() and not _is_excluded(path.relative_to(root), include_tests)
    ]
    return tuple(sorted(files))


def _is_excluded(relative_path: Path, include_tests: bool) -> bool:
    parts = set(relative_path.parts)
    if parts.intersection(_EXCLUDED_DIR_NAMES):
        return True
    return not include_tests and ("tests" in parts or relative_path.name.startswith("test_"))


def _read_source(file_path: Path, relative_path: str, parse_issues: list[ParseIssue]) -> str | None:
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as exc:
        parse_issues.append(ParseIssue(path=relative_path, lineno=0, column=0, message=str(exc)))
        return None


def _merge_registrations(
    registrations: dict[str, list[RegistrationSite]], source: str, relative_path: str
) -> dict[str, list[RegistrationSite]]:
    for match in REGISTRATION_REGEX.finditer(source):
        name = match.group(1) or match.group(2)
        if name is not None:
            registrations[name].append((relative_path, source.count("\n", 0, match.start()) + 1))
    return registrations


def _parse_tree(source: str, relative_path: str, parse_issues: list[ParseIssue]) -> ast.AST | None:
    try:
        return ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        parse_issues.append(
            ParseIssue(path=relative_path, lineno=exc.lineno or 0, column=exc.offset or 0, message=exc.msg)
        )
        return None

