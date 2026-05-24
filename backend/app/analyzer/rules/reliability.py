from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import StaticRule


class BareExceptRule(StaticRule):
    rule_ids = ("bare_except",)
    category = "reliability"
    severity = "medium"
    message = "Bare `except` catches every exception, including unexpected failures."
    suggestion = "Catch the specific exception types that can be handled safely."

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.report(node)
        self.generic_visit(node)