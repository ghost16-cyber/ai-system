import ast

class ASTExtractor(ast.NodeVisitor):
    def __init__(self):
        self.functions = []
        self.classes = []
        self.imports = []
        self.calls = []
        self.current_function = None

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        prev = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = prev

    def visit_ClassDef(self, node):
        self.classes.append(node.name)
        self.generic_visit(node)

    def visit_Import(self, node):
        for n in node.names:
            self.imports.append(n.name)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append(node.module)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.append((self.current_function, node.func.id))
        self.generic_visit(node)


def analyze_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        extractor = ASTExtractor()
        extractor.visit(tree)

        return {
            "functions": extractor.functions,
            "classes": extractor.classes,
            "imports": extractor.imports,
            "calls": extractor.calls
        }

    except Exception:
        return None