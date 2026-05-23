import json
from pathlib import Path


EXTENSION_LANGUAGES = {
    ".py": "python",
    ".ipynb": "jupyter_notebook",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".mdx": "mdx",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".csv": "csv",
    ".txt": "text",
    ".bat": "batch",
    ".ps1": "powershell",
}


def detect_language(file_path):
    return EXTENSION_LANGUAGES.get(Path(file_path).suffix.lower(), "unknown")


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8").lower()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lower()


def detect_framework(repo_path):
    repo_path = Path(repo_path)
    frameworks = set()

    req_file = repo_path / "requirements.txt"
    if req_file.exists():
        content = _read_text(req_file)
        if "django" in content:
            frameworks.add("django")
        if "flask" in content:
            frameworks.add("flask")
        if "fastapi" in content:
            frameworks.add("fastapi")
        if "torch" in content:
            frameworks.add("pytorch")
        if "transformers" in content:
            frameworks.add("transformers")
        if "faiss" in content:
            frameworks.add("faiss")

    pyproject_file = repo_path / "pyproject.toml"
    if pyproject_file.exists():
        content = _read_text(pyproject_file)
        if "django" in content:
            frameworks.add("django")
        if "flask" in content:
            frameworks.add("flask")
        if "fastapi" in content:
            frameworks.add("fastapi")

    pkg_file = repo_path / "package.json"
    if pkg_file.exists():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pkg = {}
        deps = json.dumps(pkg).lower()

        if "react" in deps:
            frameworks.add("react")
        if "next" in deps:
            frameworks.add("nextjs")
        if "vue" in deps:
            frameworks.add("vue")
        if "svelte" in deps:
            frameworks.add("svelte")

    return sorted(frameworks)
