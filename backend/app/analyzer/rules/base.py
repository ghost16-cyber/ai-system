from __future__ import annotations

import ast
from typing import ClassVar

from backend.app.schemas.api import IssueResponse


class StaticRule(ast.NodeVisitor):
    """
    Base class for deterministic AST rules.

    Each rule visits the parsed AST and returns IssueResponse objects.
    Rules must not store submitted source code.
    """

    rule_ids: ClassVar[tuple[str, ...]]
    category: ClassVar[str]
    severity: ClassVar[str]
    message: ClassVar[str]
    suggestion: ClassVar[str]
    source: ClassVar[str] = "static_rule"

    def __init__(self) -> None:
        self.issues: list[IssueResponse] = []

    def check(self, tree: ast.AST) -> list[IssueResponse]:
        self.issues = []
        self.visit(tree)
        return self.issues

    def report(
        self,
        node: ast.AST,
        *,
        rule_id: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        message: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.issues.append(
            IssueResponse(
                rule_id=rule_id or self.rule_ids[0],
                source=self.source,
                category=category or self.category,
                severity=severity or self.severity,
                message=message or self.message,
                suggestion=suggestion or self.suggestion,
                line=getattr(node, "lineno", None),
                column=getattr(node, "col_offset", None),
            )
        )


def is_none_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def is_boolean_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)