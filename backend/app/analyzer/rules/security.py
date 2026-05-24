from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import StaticRule


class DangerousDynamicExecutionRule(StaticRule):
    rule_ids = ("dangerous_eval", "dangerous_exec")
    category = "security"
    severity = "high"
    message = "Dynamic code execution can run arbitrary Python code."
    suggestion = "Avoid dynamic code execution; use a parser or explicit logic instead."

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            function_name = node.func.id
            self.report(
                node,
                rule_id=f"dangerous_{function_name}",
                message=f"`{function_name}()` can run arbitrary Python code.",
            )

        self.generic_visit(node)