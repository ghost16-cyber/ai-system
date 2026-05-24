from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import StaticRule


class MissingDocstringRule(StaticRule):
    rule_ids = ("missing_docstring",)
    category = "maintainability"
    severity = "low"
    message = "Public definition is missing a docstring."
    suggestion = "Add a concise docstring describing the public definition's purpose."

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._check_public_definition(statement)

    def _check_public_definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        if node.name.startswith("_") or ast.get_docstring(node) is not None:
            return

        self.report(
            node,
            message=f"Public definition `{node.name}` is missing a docstring.",
        )
