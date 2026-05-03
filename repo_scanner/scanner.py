from pathlib import Path

from repo_scanner.ast_parser import parse_python_file
from repo_scanner.language_detector import detect_framework, detect_language
from repo_scanner.structure_analyzer import analyze_structure


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".continue",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def _empty_repo_data(root_path):
    return {
        "root": str(root_path),
        "summary": {
            "total_files": 0,
            "total_directories": 0,
            "total_size_bytes": 0,
        },
        "files": [],
        "languages": {},
        "file_types": {},
        "frameworks": [],
        "structure": {},
        "python": {
            "files_parsed": 0,
            "functions": [],
            "classes": [],
            "imports": [],
            "parse_errors": [],
        },
    }


def _count(counter, key):
    counter[key] = counter.get(key, 0) + 1


def scan_repository(root_path: str, ignore_dirs=None, include_ast=True):
    root = Path(root_path).expanduser().resolve()
    ignore_dirs = set(ignore_dirs or DEFAULT_IGNORE_DIRS)

    if not root.exists():
        raise FileNotFoundError(f"Repository path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {root}")

    repo_data = _empty_repo_data(root)

    for path in root.rglob("*"):
        if any(part in ignore_dirs for part in path.relative_to(root).parts):
            continue

        if path.is_dir():
            repo_data["summary"]["total_directories"] += 1
            continue

        if not path.is_file():
            continue

        relative_path = path.relative_to(root).as_posix()
        extension = path.suffix.lower() or "[no extension]"
        language = detect_language(path)
        size_bytes = path.stat().st_size

        repo_data["summary"]["total_files"] += 1
        repo_data["summary"]["total_size_bytes"] += size_bytes
        _count(repo_data["file_types"], extension)
        _count(repo_data["languages"], language)

        repo_data["files"].append(
            {
                "path": relative_path,
                "name": path.name,
                "extension": extension,
                "language": language,
                "size_bytes": size_bytes,
            }
        )

        if include_ast and language == "python":
            try:
                parsed = parse_python_file(path)
            except (SyntaxError, UnicodeDecodeError) as exc:
                repo_data["python"]["parse_errors"].append(
                    {
                        "path": relative_path,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            repo_data["python"]["files_parsed"] += 1
            repo_data["python"]["functions"].extend(
                {"name": name, "path": relative_path} for name in parsed["functions"]
            )
            repo_data["python"]["classes"].extend(
                {"name": name, "path": relative_path} for name in parsed["classes"]
            )
            repo_data["python"]["imports"].extend(parsed["imports"])

    repo_data["frameworks"] = detect_framework(root)
    repo_data["structure"] = analyze_structure(root)
    repo_data["files"].sort(key=lambda item: item["path"])
    repo_data["languages"] = dict(sorted(repo_data["languages"].items()))
    repo_data["file_types"] = dict(sorted(repo_data["file_types"].items()))
    repo_data["python"]["imports"] = sorted(set(repo_data["python"]["imports"]))
    return repo_data
