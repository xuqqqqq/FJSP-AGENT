"""Incumbent 源码的通用静态能力审计。

审计器只提取 Python 结构事实，不判断某个 FJSP 方法是否正确，也不向
Main Agent 暴露整份源码。算法含义仍由 Main 结合实例画像和知识卡判断。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


MAX_AUDIT_FILES = 6
MAX_FUNCTIONS = 80
MAX_CLASSES = 32
MAX_CONFIGURATIONS = 96
MAX_LOOPS = 80
MAX_CALL_EDGES = 160
MAX_IDENTIFIERS = 120

_CONTROL_NAME_PARTS = (
    "beam",
    "branch",
    "width",
    "limit",
    "budget",
    "timeout",
    "deadline",
    "iteration",
    "restart",
    "mode",
    "rule",
    "policy",
    "tenure",
    "tabu",
    "pool",
    "elite",
    "population",
    "generation",
    "sample",
    "seed",
    "temperature",
    "machine_option",
)

_PRIMARY_SEARCH_CONTROL_PARTS = (
    "beam",
    "branch",
    "machine_option",
    "restart",
    "mode",
    "rule",
    "policy",
    "tabu",
    "tenure",
    "population",
    "generation",
)

_SEARCH_SYMBOL_PARTS = (
    "search",
    "beam",
    "construct",
    "candidate",
    "neighbor",
    "optimize",
    "solve",
    "restart",
    "tabu",
)


def build_incumbent_capability_audit(
    incumbent_code_context: dict[str, Any] | None,
    *,
    project_root: Path,
) -> dict[str, Any] | None:
    """审计 incumbent Python 文件，返回可交给 Main 的有界结构化事实。"""

    context = incumbent_code_context if isinstance(incumbent_code_context, dict) else {}
    root = project_root.resolve()
    reports: list[dict[str, Any]] = []
    for item in context.get("files") or []:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("relative_path") or "").strip()
        if not relative_path.lower().endswith(".py"):
            continue
        source_path = (root / relative_path).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            continue
        if not source_path.is_file():
            continue
        reports.append(
            _audit_python_file(
                source_path,
                relative_path=Path(relative_path).as_posix(),
                sha256=item.get("sha256"),
            )
        )
        if len(reports) >= MAX_AUDIT_FILES:
            break
    if not reports:
        return None

    parsed = [item for item in reports if item.get("parse_status") == "ok"]
    return {
        "schema_version": 1,
        "source": "promoted_incumbent_static_python_ast",
        "purpose": (
            "向 Main Agent 提供 incumbent 的结构事实，用于区分已实现机制、实现规模限制和未知项；"
            "报告不包含完整源码，也不替代 evaluator 或运行时剖析。"
        ),
        "files": reports,
        "summary": {
            "file_count": len(reports),
            "parsed_file_count": len(parsed),
            "function_count": sum(len(item.get("functions") or []) for item in parsed),
            "class_count": sum(len(item.get("classes") or []) for item in parsed),
            "configuration_count": sum(len(item.get("configurations") or []) for item in parsed),
            "loop_count": sum(len(item.get("loops") or []) for item in parsed),
            "call_edge_count": sum(len(item.get("call_edges") or []) for item in parsed),
        },
        "interpretation_rules": [
            "把 functions、call_edges 和 loops 当作机制存在与可达性的静态证据，不把函数名本身当作语义正确证明。",
            "把 configurations 中的表达式和集合规模用于判断搜索覆盖、分支和预算策略是否可能过弱。",
            "若报告已显示某机制存在，应诊断其规模、可达性或实现限制，不得再次把它描述为完全缺失。",
            "静态报告无法证明运行时热点、候选多样性、合法性或 makespan 收益；这些必须写成待证伪假设。",
        ],
        "limitations": [
            "不执行 incumbent，不测量各阶段耗时、状态扩展数、去重率或候选多样性。",
            "动态反射、运行时生成调用和跨文件间接调用可能无法由 AST 完整恢复。",
            "参数表达式按源码文本记录，不推断其在具体实例上的最终运行值。",
        ],
    }


def compact_incumbent_capability_audit(value: dict[str, Any] | None) -> dict[str, Any]:
    """按 Main 诊断优先级压缩审计，确保控制参数不会被通用截断丢失。"""

    audit = value if isinstance(value, dict) else {}
    files: list[dict[str, Any]] = []
    for raw in audit.get("files") or []:
        if not isinstance(raw, dict):
            continue
        functions = [item for item in raw.get("functions") or [] if isinstance(item, dict)]
        internal_names = {str(item.get("name") or "") for item in functions}
        configurations = sorted(
            [item for item in raw.get("configurations") or [] if isinstance(item, dict)],
            key=lambda item: (_configuration_priority(str(item.get("name") or "")), int(item.get("line") or 0)),
        )
        loops = sorted(
            [item for item in raw.get("loops") or [] if isinstance(item, dict)],
            key=lambda item: (_loop_priority(item), int(item.get("line") or 0)),
        )
        functions = sorted(
            functions,
            key=lambda item: (_symbol_priority(str(item.get("qualified_name") or "")), int(item.get("line") or 0)),
        )
        internal_edges = [
            item
            for item in raw.get("call_edges") or []
            if isinstance(item, dict)
            and str(item.get("callee") or "").split(".")[-1] in internal_names
        ]
        files.append(
            {
                "relative_path": raw.get("relative_path"),
                "sha256": raw.get("sha256"),
                "line_count": raw.get("line_count"),
                "parse_status": raw.get("parse_status"),
                "entrypoints": list(raw.get("entrypoints") or [])[:16],
                "has_main_guard": bool(raw.get("has_main_guard")),
                # 搜索控制和循环是发现“已有但规模不足”的首要证据，必须排在
                # 函数索引和调用图之前，避免有界 packet 压缩时被裁掉。
                "configurations": [dict(item) for item in configurations[:64]],
                "loops": [
                    {
                        "scope": item.get("scope"),
                        "kind": item.get("kind"),
                        "line": item.get("line"),
                        "target": item.get("target"),
                        "control": item.get("control"),
                        "calls": list(item.get("calls") or [])[:12],
                    }
                    for item in loops[:40]
                ],
                "functions": [
                    {
                        "name": item.get("name"),
                        "qualified_name": item.get("qualified_name"),
                        "line": item.get("line"),
                        "end_line": item.get("end_line"),
                        "args": list(item.get("args") or [])[:16],
                        "loop_count": item.get("loop_count"),
                        "branch_count": item.get("branch_count"),
                        "calls": [
                            call
                            for call in (item.get("calls") or [])
                            if str(call).split(".")[-1] in internal_names
                        ][:12],
                    }
                    for item in functions[:48]
                ],
                "classes": [dict(item) for item in (raw.get("classes") or [])[:24] if isinstance(item, dict)],
                "internal_call_edges": [dict(item) for item in internal_edges[:96]],
                "truncated": raw.get("truncated") or {},
                "error": raw.get("error"),
            }
        )
        if len(files) >= MAX_AUDIT_FILES:
            break
    return {
        "schema_version": audit.get("schema_version") or 1,
        "source": audit.get("source"),
        "purpose": audit.get("purpose"),
        "summary": audit.get("summary") or {},
        "files": files,
        "interpretation_rules": list(audit.get("interpretation_rules") or [])[:6],
        "limitations": list(audit.get("limitations") or [])[:6],
    }


def _configuration_priority(name: str) -> int:
    normalized = name.lower()
    if any(part in normalized for part in _PRIMARY_SEARCH_CONTROL_PARTS):
        return 0
    if any(part in normalized for part in _CONTROL_NAME_PARTS):
        return 1
    return 2


def _symbol_priority(name: str) -> int:
    normalized = name.lower()
    return 0 if any(part in normalized for part in _SEARCH_SYMBOL_PARTS) else 1


def _loop_priority(item: dict[str, Any]) -> int:
    control_and_calls = " ".join(
        [
            str(item.get("control") or ""),
            *(str(value) for value in item.get("calls") or []),
        ]
    ).lower()
    if any(part in control_and_calls for part in _PRIMARY_SEARCH_CONTROL_PARTS):
        return 0
    text = f"{item.get('scope') or ''} {control_and_calls}".lower()
    return 1 if any(part in text for part in _SEARCH_SYMBOL_PARTS) else 2


def _audit_python_file(path: Path, *, relative_path: str, sha256: Any) -> dict[str, Any]:
    try:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return {
            "relative_path": relative_path,
            "sha256": sha256,
            "parse_status": "read_error",
            "error": str(exc)[:500],
        }
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "relative_path": relative_path,
            "sha256": sha256,
            "line_count": len(source.splitlines()),
            "parse_status": "syntax_error",
            "error": f"line {exc.lineno}: {exc.msg}"[:500],
        }

    visitor = _AuditVisitor(source)
    visitor.visit(tree)
    entrypoints = [
        item["qualified_name"]
        for item in visitor.functions
        if item["name"] in {"main", "solve", "run", "optimize"}
    ][:16]
    identifiers = _bounded_unique(
        [
            *(item["qualified_name"] for item in visitor.functions),
            *(item["qualified_name"] for item in visitor.classes),
            *(item["name"] for item in visitor.configurations),
            *(item["callee"] for item in visitor.call_edges),
        ],
        limit=MAX_IDENTIFIERS,
    )
    return {
        "relative_path": relative_path,
        "sha256": sha256,
        "line_count": len(source.splitlines()),
        "parse_status": "ok",
        "entrypoints": entrypoints,
        "has_main_guard": visitor.has_main_guard,
        "functions": visitor.functions[:MAX_FUNCTIONS],
        "classes": visitor.classes[:MAX_CLASSES],
        "configurations": visitor.configurations[:MAX_CONFIGURATIONS],
        "loops": visitor.loops[:MAX_LOOPS],
        "call_edges": visitor.call_edges[:MAX_CALL_EDGES],
        "identifier_index": identifiers,
        "truncated": {
            "functions": len(visitor.functions) > MAX_FUNCTIONS,
            "classes": len(visitor.classes) > MAX_CLASSES,
            "configurations": len(visitor.configurations) > MAX_CONFIGURATIONS,
            "loops": len(visitor.loops) > MAX_LOOPS,
            "call_edges": len(visitor.call_edges) > MAX_CALL_EDGES,
        },
    }


class _AuditVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.scope: list[str] = []
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.configurations: list[dict[str, Any]] = []
        self.loops: list[dict[str, Any]] = []
        self.call_edges: list[dict[str, Any]] = []
        self.has_main_guard = False

    def visit_If(self, node: ast.If) -> None:  # noqa: N802 - ast visitor API
        if _is_main_guard(node.test):
            self.has_main_guard = True
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor API
        qualified = self._qualified(node.name)
        self.classes.append(
            {
                "name": node.name,
                "qualified_name": qualified,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "bases": [_expr_text(self.source, item) for item in node.bases[:8]],
            }
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast visitor API
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        qualified = self._qualified(node.name)
        calls = _bounded_unique(
            [_call_name(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)],
            limit=32,
        )
        self.functions.append(
            {
                "name": node.name,
                "qualified_name": qualified,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "args": [item.arg for item in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]][:24],
                "async": is_async,
                "loop_count": sum(isinstance(item, (ast.For, ast.AsyncFor, ast.While)) for item in ast.walk(node)),
                "branch_count": sum(isinstance(item, (ast.If, ast.IfExp, ast.Match)) for item in ast.walk(node)),
                "calls": calls,
            }
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast visitor API
        for target in node.targets:
            self._record_assignment(target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast visitor API
        if node.value is not None:
            self._record_assignment(node.target, node.value, node.lineno)
        self.generic_visit(node)

    def _record_assignment(self, target: ast.expr, value: ast.expr, line: int) -> None:
        name = _assignment_name(target)
        if not name or not _is_configuration(name, value):
            return
        literals = _literal_values(value)
        collection_size = len(value.elts) if isinstance(value, (ast.List, ast.Tuple, ast.Set)) else None
        self.configurations.append(
            {
                "scope": self._scope_name(),
                "name": name,
                "line": line,
                "expression": _expr_text(self.source, value),
                "literal_values": literals,
                "collection_size": collection_size,
            }
        )

    def visit_For(self, node: ast.For) -> None:  # noqa: N802 - ast visitor API
        self._record_loop(node, kind="for", control=node.iter, target=node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802 - ast visitor API
        self._record_loop(node, kind="async_for", control=node.iter, target=node.target)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802 - ast visitor API
        self._record_loop(node, kind="while", control=node.test, target=None)
        self.generic_visit(node)

    def _record_loop(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        *,
        kind: str,
        control: ast.expr,
        target: ast.expr | None,
    ) -> None:
        self.loops.append(
            {
                "scope": self._scope_name(),
                "kind": kind,
                "line": node.lineno,
                "target": _expr_text(self.source, target) if target is not None else "",
                "control": _expr_text(self.source, control),
                "calls": _bounded_unique(
                    [_call_name(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)],
                    limit=24,
                ),
            }
        )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        self.call_edges.append(
            {
                "caller": self._scope_name(),
                "callee": _call_name(node.func),
                "line": node.lineno,
            }
        )
        self.generic_visit(node)

    def _qualified(self, name: str) -> str:
        return ".".join([*self.scope, name]) if self.scope else name

    def _scope_name(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"


def _is_main_guard(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == "__main__"
    )


def _assignment_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.Tuple, ast.List)):
        return ", ".join(_assignment_name(item) for item in node.elts if _assignment_name(item))
    return ""


def _is_configuration(name: str, value: ast.expr) -> bool:
    normalized = name.lower()
    if name.isupper() or any(part in normalized for part in _CONTROL_NAME_PARTS):
        return True
    return isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and len(getattr(value, "elts", [])) <= 32


def _literal_values(node: ast.AST, *, limit: int = 24) -> list[Any]:
    values: list[Any] = []
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, (str, int, float, bool, type(None))):
            if item.value not in values:
                values.append(item.value)
        if len(values) >= limit:
            break
    return values


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return type(node).__name__


def _expr_text(source: str, node: ast.AST | None, *, max_chars: int = 240) -> str:
    if node is None:
        return ""
    text = ast.get_source_segment(source, node)
    if text is None:
        try:
            text = ast.unparse(node)
        except (AttributeError, ValueError):
            text = type(node).__name__
    text = " ".join(text.split())
    return text[:max_chars]


def _bounded_unique(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if value and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result
