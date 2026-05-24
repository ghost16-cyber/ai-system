from __future__ import annotations

import ast

from backend.app.analyzer.rules.base import StaticRule


class InefficientLoopRule(StaticRule):
    rule_ids = ("inefficient_loop",)
    category = "performance"
    severity = "low"
    message = "Loop indexes into a collection only to retrieve each item."
    suggestion = "Iterate over the collection items directly instead of using `range(len(...))`."

    def visit_For(self, node: ast.For) -> None:
        names = self._direct_iteration_candidate(node)
        if names is not None:
            index_name, collection_name = names
            usage = _LoopIndexUsage(index_name, collection_name)

            for statement in [*node.body, *node.orelse]:
                usage.visit(statement)

            if usage.direct_accesses > 0 and not usage.has_unsupported_usage:
                self.report(
                    node,
                    message=(
                        f"Loop index `{index_name}` is only used to read "
                        f"`{collection_name}` items."
                    ),
                )

        self.generic_visit(node)

    def _direct_iteration_candidate(self, node: ast.For) -> tuple[str, str] | None:
        if not isinstance(node.target, ast.Name):
            return None
        if not isinstance(node.iter, ast.Call) or node.iter.keywords:
            return None
        if not isinstance(node.iter.func, ast.Name) or node.iter.func.id != "range":
            return None
        if len(node.iter.args) != 1:
            return None

        length_call = node.iter.args[0]
        if not isinstance(length_call, ast.Call) or length_call.keywords:
            return None
        if not isinstance(length_call.func, ast.Name) or length_call.func.id != "len":
            return None
        if len(length_call.args) != 1 or not isinstance(length_call.args[0], ast.Name):
            return None

        return node.target.id, length_call.args[0].id


class _LoopIndexUsage(ast.NodeVisitor):
    def __init__(self, index_name: str, collection_name: str) -> None:
        self.index_name = index_name
        self.collection_name = collection_name
        self.direct_accesses = 0
        self.has_unsupported_usage = False

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == self.collection_name
            and isinstance(node.slice, ast.Name)
            and node.slice.id == self.index_name
        ):
            self.direct_accesses += 1
            self.visit(node.value)
            return

        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id == self.index_name:
            self.has_unsupported_usage = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.has_unsupported_usage = True

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.has_unsupported_usage = True

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.has_unsupported_usage = True

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.has_unsupported_usage = True
