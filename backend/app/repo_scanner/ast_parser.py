import ast


def parse_python_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    data = {
        "functions": [],
        "classes": [],
        "imports": [],
        "lines": len(source.splitlines()),
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            data["functions"].append(node.name)

        elif isinstance(node, ast.ClassDef):
            data["classes"].append(node.name)

        elif isinstance(node, ast.Import):
            for n in node.names:
                data["imports"].append(n.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                data["imports"].append(node.module)

    data["functions"] = sorted(set(data["functions"]))
    data["classes"] = sorted(set(data["classes"]))
    data["imports"] = sorted(set(data["imports"]))
    return data
