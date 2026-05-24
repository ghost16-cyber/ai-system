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


class UnusedImportRule(StaticRule):
    rule_ids = ("unused_import",)
    category = "maintainability"
    severity = "low"
    message = "Imported name is not used in this module."
    suggestion = "Remove the unused import if it is not needed."

    def visit_Module(self, node: ast.Module) -> None:
        used_names = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        used_names.update(self._exported_names(node))

        for binding, import_node in self._module_import_bindings(node):
            if binding not in used_names:
                self.report(
                    import_node,
                    message=f"Imported name `{binding}` is not used in this module.",
                )

    def _module_import_bindings(self, node: ast.Module) -> list[tuple[str, ast.alias]]:
        bindings: list[tuple[str, ast.alias]] = []

        for statement in node.body:
            if isinstance(statement, ast.Import):
                for imported in statement.names:
                    binding = imported.asname or imported.name.split(".")[0]
                    bindings.append((binding, imported))

            elif isinstance(statement, ast.ImportFrom):
                if statement.module == "__future__":
                    continue

                for imported in statement.names:
                    if imported.name == "*":
                        continue
                    bindings.append((imported.asname or imported.name, imported))

        return bindings

    def _exported_names(self, node: ast.Module) -> set[str]:
        exports: set[str] = set()

        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in statement.targets
            ):
                continue
            if not isinstance(statement.value, (ast.List, ast.Tuple)):
                continue

            for element in statement.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    exports.add(element.value)

        return exports
