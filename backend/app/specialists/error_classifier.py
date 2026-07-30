from __future__ import annotations

from .schemas import ErrorClassification, SpecialistRequest


ERROR_PATTERNS: dict[str, tuple[str, ...]] = {
    "cuda_oom": (
        "cuda out of memory",
        "torch.cuda.outofmemoryerror",
        "cublas_status_alloc_failed",
        "outofmemoryerror",
        "cuda oom",
    ),
    "syntax_error": (
        "syntaxerror",
        "invalid syntax",
        "unexpected eof",
        "unterminated string",
        "expected ':'",
    ),
    "missing_import": (
        "modulenotfounderror",
        "importerror",
        "cannot import name",
        "no module named",
    ),
    "dependency_missing": (
        "cannot find module",
        "module not found",
        "package not found",
        "command not found",
        "not recognized as an internal or external command",
        "eresolve",
        "unable to resolve dependency",
    ),
    "npm_build_error": (
        "npm err!",
        "vite build",
        "failed to compile",
        "webpack",
        "tsc",
        "typescript error",
        "eslint",
    ),
    "pytest_failure": (
        "failed",
        "pytest",
        "assertionerror",
        "short test summary info",
        "collected",
        "assert ",
    ),
}


def classify_error(payload: SpecialistRequest | dict) -> ErrorClassification:
    request = _request(payload)
    text = _combined_text(request).lower()
    scores = {
        label: sum(1 for pattern in patterns if pattern in text)
        for label, patterns in ERROR_PATTERNS.items()
    }

    label, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        label = "unknown_error"

    matched = [
        pattern
        for pattern in ERROR_PATTERNS.get(label, ())
        if pattern in text
    ]
    return ErrorClassification(
        label=label,  # type: ignore[arg-type]
        confidence=_confidence(score, label),
        features={
            "matched_patterns": matched,
            "scores": scores,
            "text_length": len(text),
            "context_keys": sorted(request.context),
        },
        reason=(
            "Rule-based error specialist matched known error patterns."
            if matched
            else "No known error pattern matched."
        ),
    )


def _request(payload: SpecialistRequest | dict) -> SpecialistRequest:
    if isinstance(payload, SpecialistRequest):
        return payload
    return SpecialistRequest.model_validate(payload)


def _combined_text(request: SpecialistRequest) -> str:
    values = [request.text]
    for key in ("stderr", "stdout", "error", "command", "tool_output"):
        value = request.context.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(value for value in values if value)


def _confidence(score: int, label: str) -> float:
    if label == "unknown_error":
        return 0.25
    return round(min(0.96, 0.60 + score * 0.12), 3)
