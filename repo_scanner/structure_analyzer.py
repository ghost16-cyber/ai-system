import os
from pathlib import Path

def analyze_structure(repo_path):
    structure = {
        "has_models": False,
        "has_views": False,
        "has_routes": False,
        "has_tests": False,
        "has_docs": False,
        "has_config": False,
        "notable_directories": [],
    }

    repo_path = Path(repo_path)

    for root, dirs, files in os.walk(repo_path):
        for d in dirs:
            name = d.lower()
            if "model" in name:
                structure["has_models"] = True
            if "view" in name:
                structure["has_views"] = True
            if "route" in name or "api" in name:
                structure["has_routes"] = True
            if name in {"test", "tests", "testing"}:
                structure["has_tests"] = True
            if name in {"doc", "docs", "documentation"}:
                structure["has_docs"] = True

        for file_name in files:
            name = file_name.lower()
            if name in {"config.yaml", "config.yml", "pyproject.toml", "setup.py", "requirements.txt"}:
                structure["has_config"] = True

    for directory in ("src", "tests", "training", "data", "config", "scripts", "repo_scanner"):
        if (repo_path / directory).exists():
            structure["notable_directories"].append(directory)

    return structure
