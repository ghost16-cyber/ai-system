from __future__ import annotations

from pathlib import Path


LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".jsx": "jsx", ".tsx": "tsx", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".markdown": "markdown",
    ".toml": "config", ".ini": "config", ".cfg": "config", ".txt": "text",
}


def detect_language(relative_path: str) -> str:
    path = Path(relative_path)
    name = path.name.lower()
    if name in {"package.json", "tsconfig.json", "jsconfig.json"}:
        return "json"
    if name in {"requirements.txt", "dockerfile", "makefile", "procfile"}:
        return "config"
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "text")


def is_structured_language(language: str) -> bool:
    return language in {"python", "javascript", "typescript", "jsx", "tsx", "json", "yaml", "markdown", "config"}


__all__ = ["detect_language", "is_structured_language"]
