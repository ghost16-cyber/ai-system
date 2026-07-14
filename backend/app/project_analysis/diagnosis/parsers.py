from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from backend.app.folders.safety import safe_relative_path
from backend.app.project_analysis.diagnosis.models import (
    FailureDiagnostic, MAX_DIAGNOSTICS, MAX_FAILING_TESTS, MAX_IDENTICAL_DIAGNOSTICS,
    MAX_TRACEBACK_FRAMES, TracebackFrame,
)


def parse_failure_output(output: str, *, root: str | Path) -> tuple[list[FailureDiagnostic], list[str], list[TracebackFrame]]:
    approved = Path(root).resolve()
    diagnostics: list[FailureDiagnostic] = []
    failing_tests: list[str] = []
    frames: list[TracebackFrame] = []

    for match in re.finditer(r"(?m)^FAILED\s+([^\s]+)(?:\s+-\s+(.+))?$", output):
        test_name = _bounded(match.group(1), 300)
        failing_tests.append(test_name)
        diagnostics.append(_diagnostic("pytest", "pytest_test_failed", match.group(2) or test_name, test_name=test_name,
                                       relative_path=_verified_path(test_name.split("::", 1)[0], approved)))
    for match in re.finditer(r"(?m)^ERROR\s+(?:collecting\s+)?([^\s]+)(?:\s+-\s+(.+))?$", output):
        test_name = _bounded(match.group(1), 300)
        failing_tests.append(test_name)
        diagnostics.append(_diagnostic("pytest", "pytest_collection_failed", match.group(2) or test_name,
                                       test_name=test_name, relative_path=_verified_path(test_name.split("::", 1)[0], approved)))

    for match in re.finditer(r'File ["\']([^"\']+)["\'], line (\d+)(?:, in ([^\n]+))?', output):
        relative = _verified_path(match.group(1), approved)
        if relative:
            frames.append(TracebackFrame(relative_path=relative, line=int(match.group(2)), function=_bounded(match.group(3), 200) if match.group(3) else None))
    syntax = re.search(r"(?m)^(?:SyntaxError|IndentationError):\s*(.+)$", output)
    if syntax:
        path, line = _nearest_frame(frames)
        diagnostics.append(_diagnostic("python", "python_syntax_error", syntax.group(1), relative_path=path,
                                       line=line, exception_type="SyntaxError"))
    imported = re.search(r"(?m)^(ModuleNotFoundError|ImportError):\s*(.+)$", output)
    if imported:
        path, line = _nearest_frame(frames)
        diagnostics.append(_diagnostic("python", "python_import_error", imported.group(2), relative_path=path,
                                       line=line, exception_type=imported.group(1)))
    exception = re.search(r"(?m)^([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.+)$", output)
    if exception and not syntax and not imported:
        path, line = _nearest_frame(frames)
        reason = "python_assertion_failure" if exception.group(1) == "AssertionError" else "python_traceback"
        diagnostics.append(_diagnostic("python", reason, exception.group(2) or exception.group(1),
                                       relative_path=path, line=line, exception_type=exception.group(1)))

    for match in re.finditer(r"(?m)^([^\s:(][^:(]*?\.(?:ts|tsx))(?::|\()(\d+)[,:](\d+)\)?\s*[-:]?\s*error\s+(TS\d+):\s*(.+)$", output, re.I):
        diagnostics.append(_diagnostic("typescript", match.group(4).lower(), match.group(5),
                                       relative_path=_verified_path(match.group(1), approved), line=int(match.group(2)), column=int(match.group(3))))
    for match in re.finditer(r"(?m)^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)(?:\s{2,}([\w@/-]+))?$", output):
        path = _nearest_path_before(output, match.start(), approved)
        if path:
            diagnostics.append(_diagnostic("eslint", "eslint_diagnostic", match.group(4), severity=match.group(3).lower(),
                                           relative_path=path, line=int(match.group(1)), column=int(match.group(2))))
    for match in re.finditer(r"(?mi)(?:\[vite\]|vite).*?(?:error|failed).*?((?:[\w.-]+/)*[\w.-]+\.(?:js|jsx|ts|tsx))(?::(\d+))?", output):
        diagnostics.append(_diagnostic("vite", "vite_build_error", match.group(0),
                                       relative_path=_verified_path(match.group(1), approved), line=int(match.group(2)) if match.group(2) else None))
    json_error = re.search(r"(?mi)(JSONDecodeError|JSON parse error|Unexpected token).*?(?:line\s+(\d+))?.*?(?:column\s+(\d+))?", output)
    if json_error:
        diagnostics.append(_diagnostic("json", "json_parse_failure", json_error.group(0), line=int(json_error.group(2)) if json_error.group(2) else None,
                                       column=int(json_error.group(3)) if json_error.group(3) else None))
    yaml_error = re.search(r"(?mi)(yaml|scannererror|parsererror).*?(?:line\s+(\d+))?.*?(?:column\s+(\d+))?", output)
    if yaml_error:
        diagnostics.append(_diagnostic("yaml", "yaml_parse_failure", yaml_error.group(0), line=int(yaml_error.group(2)) if yaml_error.group(2) else None,
                                       column=int(yaml_error.group(3)) if yaml_error.group(3) else None))
    virtual = re.search(r"(?mi)(virtual validation|prevalidation).*?(failed|error).*", output)
    if virtual:
        diagnostics.append(_diagnostic("astra_virtual_validation", "virtual_validation_failure", virtual.group(0)))

    diagnostics = _deduplicate(diagnostics)[:MAX_DIAGNOSTICS]
    if not diagnostics:
        diagnostics = [_diagnostic("generic", "unsupported_failure_format", "The allowlisted validation command failed in an unsupported bounded format.", severity="unknown")]
    return diagnostics, list(dict.fromkeys(failing_tests))[:MAX_FAILING_TESTS], frames[-MAX_TRACEBACK_FRAMES:]


def _diagnostic(tool: str, reason: str, message: str, **values) -> FailureDiagnostic:
    return FailureDiagnostic(diagnostic_id=uuid4().hex, tool=tool, reason_code=reason,
                             message=_bounded(message, 1000), **values)


def _verified_path(value: str, root: Path) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = Path(raw)
    try:
        if candidate.is_absolute():
            resolved = candidate.resolve()
            relative = resolved.relative_to(root).as_posix()
        else:
            relative = safe_relative_path(raw)
            resolved = (root / relative).resolve()
            resolved.relative_to(root)
        return relative if resolved.exists() and resolved.is_file() else None
    except (ValueError, OSError):
        return None


def _nearest_frame(frames: list[TracebackFrame]) -> tuple[str | None, int | None]:
    return (frames[-1].relative_path, frames[-1].line) if frames else (None, None)


def _nearest_path_before(output: str, position: int, root: Path) -> str | None:
    for line in reversed(output[:position].splitlines()[-4:]):
        match = re.search(r"((?:[\w.-]+/)*[\w.-]+\.(?:js|jsx|ts|tsx))", line)
        if match and (path := _verified_path(match.group(1), root)):
            return path
    return None


def _deduplicate(items: Iterable[FailureDiagnostic]) -> list[FailureDiagnostic]:
    counts: Counter[tuple[str, str, str, str | None, int | None]] = Counter()
    output = []
    for item in items:
        key = (item.tool, item.reason_code, item.message, item.relative_path, item.line)
        counts[key] += 1
        if counts[key] <= MAX_IDENTICAL_DIAGNOSTICS:
            output.append(item)
    return output


def _bounded(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


__all__ = ["parse_failure_output"]
