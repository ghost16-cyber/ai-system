from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import StaticRule


class MutableDefaultArgumentRule(StaticRule):
    rule_ids = ("mutable_default_argument",)
    category = "correctness"
    severity = "medium"
    message = "Function argument has a mutable default value."
    suggestion = "Use `None` as the default and create the collection inside the function."

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def _check_mutable_defaults(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        positional_arguments = [*node.args.posonlyargs, *node.args.args]
        default_arguments = positional_arguments[-len(node.args.defaults) :]
        defaults = zip(default_arguments, node.args.defaults)
        keyword_defaults = zip(node.args.kwonlyargs, node.args.kw_defaults)

        for argument, default in [*defaults, *keyword_defaults]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.report(
                    default,
                    message=f"Argument `{argument.arg}` has a mutable default value.",
                )