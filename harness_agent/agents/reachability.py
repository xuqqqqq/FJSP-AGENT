"""静态可达性辅助：识别只定义未接入主调用流的装饰性 helper。"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Iterable


PRIMARY_ENTRY_FUNCTION_NAMES = frozenset(
    {
        "main",
        "solve",
        "run",
        "run_solver",
        "generate_solution",
        "write_solution",
    }
)
FALLBACK_ENTRY_FUNCTION_NAMES = frozenset(
    {
        "improve",
        "construct_solution",
        "construct_initial_solution",
        "build_solution",
    }
)


@dataclass(frozen=True)
class FunctionReachability:
    defined: frozenset[str]
    roots: frozenset[str]
    reachable: frozenset[str]
    calls_by_function: dict[str, frozenset[str]]


def function_call_count(text: str, function_name: str) -> int:
    return len(re.findall(rf"\b{re.escape(function_name)}\s*\(", text))


def build_function_reachability(
    text: str,
    *,
    primary_entry_names: Iterable[str] = PRIMARY_ENTRY_FUNCTION_NAMES,
    fallback_entry_names: Iterable[str] = FALLBACK_ENTRY_FUNCTION_NAMES,
) -> FunctionReachability | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    defined = frozenset(functions)
    primary_entries = {name for name in primary_entry_names if name in defined}
    top_level_calls = _top_level_called_names(tree) & defined
    roots = set(top_level_calls) | primary_entries
    if not roots:
        roots.update(name for name in fallback_entry_names if name in defined)

    calls_by_function: dict[str, frozenset[str]] = {}
    for name, node in functions.items():
        calls_by_function[name] = frozenset(_called_names_in_function(node) & defined)

    reachable = _reachable_from_roots(roots, calls_by_function)
    return FunctionReachability(
        defined=defined,
        roots=frozenset(roots),
        reachable=frozenset(reachable),
        calls_by_function=calls_by_function,
    )


def unreachable_defined_function_helpers(
    text: str,
    helper_patterns: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    helpers = _defined_helpers_by_pattern(text, helper_patterns)
    reachability = build_function_reachability(text)
    if reachability is None:
        return [
            (label, name)
            for label, name in helpers
            if function_call_count(text, name) < 2
        ]
    return [
        (label, name)
        for label, name in helpers
        if name not in reachability.reachable
    ]


def function_is_reachable_from_entry(text: str, function_name: str) -> bool | None:
    reachability = build_function_reachability(text)
    if reachability is None:
        return None
    return function_name in reachability.reachable


def _defined_helpers_by_pattern(
    text: str,
    helper_patterns: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    helpers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, pattern in helper_patterns:
        for name in re.findall(pattern, text, re.M):
            key = (label, name)
            if key in seen:
                continue
            seen.add(key)
            helpers.append(key)
    return helpers


def _top_level_called_names(tree: ast.Module) -> set[str]:
    collector = _ModuleCallCollector()
    for node in tree.body:
        collector.visit(node)
    return collector.called_names


def _called_names_in_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    collector = _FunctionBodyCallCollector()
    for child in node.body:
        collector.visit(child)
    return collector.called_names


def _reachable_from_roots(
    roots: Iterable[str],
    calls_by_function: dict[str, frozenset[str]],
) -> set[str]:
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        stack.extend(
            callee
            for callee in calls_by_function.get(name, frozenset())
            if callee not in reachable
        )
    return reachable


class _CallNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.called_names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API.
        name = _call_name(node.func)
        if name:
            self.called_names.add(name)
        self.generic_visit(node)


class _ModuleCallCollector(_CallNameCollector):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API.
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor API.
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor API.
        return


class _FunctionBodyCallCollector(_CallNameCollector):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API.
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor API.
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor API.
        return


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None
