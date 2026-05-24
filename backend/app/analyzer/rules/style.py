from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import (
    StaticRule,
    is_boolean_literal,
    is_none_literal,
)


class ComparisonStyleRule(StaticRule):
    rule_ids = ("bad_none_comparison", "redundant_boolean_comparison")
    category = "style"
    severity = "low"
    message = "Comparison can be simplified."
    suggestion = "Use the idiomatic Python comparison form."

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]

        for operator, left, right in zip(node.ops, operands, operands[1:]):
            if isinstance(operator, (ast.Eq, ast.NotEq)) and (
                is_none_literal(left) or is_none_literal(right)
            ):
                self.report(
                    node,
                    rule_id="bad_none_comparison",
                    message="Compare with `None` using identity rather than equality.",
                    suggestion="Use `is None` or `is not None`.",
                )

            elif isinstance(operator, (ast.Eq, ast.NotEq)) and (
                is_boolean_literal(left) or is_boolean_literal(right)
            ):
                self.report(
                    node,
                    rule_id="redundant_boolean_comparison",
                    message="Explicit comparison with a boolean literal is usually unnecessary.",
                    suggestion="Use the boolean expression directly, adding `not` when needed.",
                )

        self.generic_visit(node)