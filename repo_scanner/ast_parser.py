import ast

def parse_python_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    data = {
        "functions": [],
        "classes": [],
        "imports": []
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            data["functions"].append(node.name)

        elif isinstance(node, ast.ClassDef):
            data["classes"].append(node.name)

        elif isinstance(node, ast.Import):
            for n in node.names:
                data["imports"].append(n.name)

        elif isinstance(node, ast.ImportFrom):
            data["imports"].append(node.module)

    return data