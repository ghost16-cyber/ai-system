from __future__ import annotations

import ast
from dataclasses import dataclass

from backend.app.schemas.api import IssueResponse


@dataclass(frozen=True)
class StaticAnalysisResult:
    parse_success: bool
    issues: list[IssueResponse]


def _issue(
    rule_id: str,
    category: str,
    severity: str,
    message: str,
    suggestion: str,
    node: ast.AST,
) -> IssueResponse:
    return IssueResponse(
        rule_id=rule_id,
        category=category,
        severity=severity,
        message=message,
        suggestion=suggestion,
        line=getattr(node, "lineno", None),
        column=getattr(node, "col_offset", None),
    )


class PythonRuleVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.issues: list[IssueResponse] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.issues.append(
                _issue(
                    "bare_except",
                    "reliability",
                    "medium",
                    "Bare `except` catches every exception, including unexpected failures.",
                    "Catch the specific exception types that can be handled safely.",
                    node,
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            rule_id = f"dangerous_{node.func.id}"
            self.issues.append(
                _issue(
                    rule_id,
                    "security",
                    "high",
                    f"`{node.func.id}()` can run arbitrary Python code.",
                    "Avoid dynamic code execution; use a parser or explicit logic instead.",
                    node,
                )
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for operator, left, right in zip(node.ops, operands, operands[1:]):
            if isinstance(operator, (ast.Eq, ast.NotEq)) and (
                _is_none(left) or _is_none(right)
            ):
                self.issues.append(
                    _issue(
                        "bad_none_comparison",
                        "style",
                        "low",
                        "Compare with `None` using identity rather than equality.",
                        "Use `is None` or `is not None`.",
                        node,
                    )
                )
            elif isinstance(operator, (ast.Eq, ast.NotEq)) and (
                _is_boolean(left) or _is_boolean(right)
            ):
                self.issues.append(
                    _issue(
                        "redundant_boolean_comparison",
                        "style",
                        "low",
                        "Explicit comparison with a boolean literal is usually unnecessary.",
                        "Use the boolean expression directly, adding `not` when needed.",
                        node,
                    )
                )
        self.generic_visit(node)

    def _check_mutable_defaults(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        positional_arguments = [*node.args.posonlyargs, *node.args.args]
        default_arguments = positional_arguments[-len(node.args.defaults) :]
        defaults = zip(default_arguments, node.args.defaults)
        keyword_defaults = zip(node.args.kwonlyargs, node.args.kw_defaults)

        for argument, default in [*defaults, *keyword_defaults]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.issues.append(
                    _issue(
                        "mutable_default_argument",
                        "correctness",
                        "medium",
                        f"Argument `{argument.arg}` has a mutable default value.",
                        "Use `None` as the default and create the collection inside the function.",
                        default,
                    )
                )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_boolean(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def analyze_python_code(code: str) -> StaticAnalysisResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        issue = IssueResponse(
            rule_id="syntax_error",
            category="correctness",
            severity="high",
            message=error.msg,
            suggestion="Fix the syntax error before running further analysis.",
            line=error.lineno,
            column=error.offset,
        )
        return StaticAnalysisResult(parse_success=False, issues=[issue])

    visitor = PythonRuleVisitor()
    visitor.visit(tree)
    issues = sorted(
        visitor.issues,
        key=lambda finding: (
            finding.line or 0,
            finding.column or 0,
            finding.rule_id,
        ),
    )
    return StaticAnalysisResult(parse_success=True, issues=issues)
