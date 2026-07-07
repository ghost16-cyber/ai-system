from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from collections.abc import Iterable

from backend.app.local_runtime.schemas import ToolStatus


COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("python", [sys.executable, "--version"]),
    ("pip", [sys.executable, "-m", "pip", "--version"]),
    ("git", ["git", "--version"]),
    ("nvidia-smi", ["nvidia-smi", "--version"]),
    ("nvcc", ["nvcc", "--version"]),
    ("ollama", ["ollama", "--version"]),
    ("node", ["node", "--version"]),
    ("npm", ["npm", "--version"]),
)

PYTHON_PACKAGES: tuple[str, ...] = (
    "torch",
    "psutil",
    "numpy",
    "sklearn",
    "transformers",
    "sentence_transformers",
    "faiss",
    "llama_cpp",
    "joblib",
)


def detect_toolchain(
    *,
    commands: Iterable[tuple[str, list[str]]] = COMMANDS,
    packages: Iterable[str] = PYTHON_PACKAGES,
) -> list[ToolStatus]:
    statuses: list[ToolStatus] = []
    statuses.extend(_detect_command(name, command) for name, command in commands)
    statuses.extend(_detect_python_package(name) for name in packages)
    return statuses


def _detect_command(name: str, command: list[str]) -> ToolStatus:
    executable = command[0]
    resolved = executable if executable == sys.executable else shutil.which(executable)
    if resolved is None:
        return ToolStatus(
            name=name,
            kind="command",
            available="missing",
            command=executable,
        )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            encoding="utf-8",
            timeout=3,
        )
    except Exception as error:
        return ToolStatus(
            name=name,
            kind="command",
            available="unknown",
            command=resolved,
            details={"error": f"{type(error).__name__}: {error}"},
        )

    output = (completed.stdout or completed.stderr).strip()
    return ToolStatus(
        name=name,
        kind="command",
        available="available" if completed.returncode == 0 else "unknown",
        version=_first_line(output),
        command=resolved,
        details={"returncode": completed.returncode},
    )


def _detect_python_package(name: str) -> ToolStatus:
    try:
        module = importlib.import_module(name)
    except Exception:
        return ToolStatus(
            name=name,
            kind="python_package",
            available="missing",
        )

    version = getattr(module, "__version__", None)
    return ToolStatus(
        name=name,
        kind="python_package",
        available="available",
        version=str(version) if version is not None else None,
    )


def _first_line(value: str) -> str | None:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None
