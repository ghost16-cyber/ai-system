from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from backend.app.analyzer.code_analyzer import SUGGESTIONS, analyze_code


app = FastAPI(
    title="AI System Backend",
    description="FastAPI backend for the AI coding assistant system.",
    version="0.2.0",
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


class AnalyzeRequest(BaseModel):
    code: str = Field(..., min_length=1)
    language: str = "python"
    filename: str | None = None


class AnalyzeResponse(BaseModel):
    success: bool
    language: str
    filename: str | None
    issues: list[dict[str, Any]]
    suggestions: list[str]
    metadata: dict[str, Any]


def infer_pattern_from_code(code: str) -> str | None:
    """
    Compatibility fallback.

    Used only when the trained analyzer returns no usable pattern key.
    This keeps the API contract stable while we inspect the analyzer output.
    """
    normalized = code.strip()

    if "for i in range(len(" in normalized:
        return "inefficient_loop"

    if "== True" in normalized or "== False" in normalized:
        return "redundant_bool_compare"

    if "eval(" in normalized:
        return "dangerous_eval"

    if "except Exception" in normalized:
        return "bare_exception"

    if "from " in normalized and " import *" in normalized:
        return "star_import"

    if "open(" in normalized and "encoding=" not in normalized:
        return "missing_encoding"

    return None


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="ai-system-backend",
        version="0.2.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """
    Analyze a single code snippet using the trained analyzer.

    Current engine:
    - scikit-learn classifier
    - deterministic fallback rules
    - suggestion mapping
    """

    result = analyze_code(request.code)

    pattern = (
        result.get("pattern")
        or result.get("prediction")
        or result.get("predicted_pattern")
        or result.get("label")
        or result.get("pattern_label")
        or result.get("class")
        or result.get("category")
    )

    if not pattern or pattern == "unknown":
        pattern = infer_pattern_from_code(request.code) or "unknown"

    suggestion_entry = SUGGESTIONS.get(
        pattern,
        {
            "issue": result.get("issue", "Unknown issue"),
            "suggestion": result.get("suggestion", "Review manually"),
            "example": result.get("example", ""),
            "is_issue": True,
        },
    )

    issue = result.get("issue") or suggestion_entry.get("issue", "Unknown issue")
    suggestion = result.get("suggestion") or suggestion_entry.get(
        "suggestion", "Review manually"
    )
    example = result.get("example") or suggestion_entry.get("example", "")
    is_issue = result.get("is_issue", suggestion_entry.get("is_issue", True))

    issues: list[dict[str, Any]] = []
    suggestions: list[str] = []

    if is_issue:
        issues.append(
            {
                "type": pattern,
                "severity": "medium",
                "message": issue,
                "example": example,
            }
        )
        suggestions.append(suggestion)

    return AnalyzeResponse(
        success=True,
        language=request.language,
        filename=request.filename,
        issues=issues,
        suggestions=suggestions,
        metadata={
            "engine": "trained-code-analyzer",
            "pattern": pattern,
            "is_issue": is_issue,
            "raw_result": result,
            "code_length": len(request.code),
            "line_count": len(request.code.splitlines()),
        },
    )

@app.post("/fix")
def fix_code(request: AnalyzeRequest):
    """
    Return a fixed version of the submitted code when a safe fix is available.
    Falls back to the original code when no automatic fix exists.
    """
    result = analyze_code(request.code)

    fixed_code = result.get("fixed_code")

    return {
        "success": True,
        "language": request.language,
        "filename": request.filename,
        "pattern": result.get("predicted_pattern"),
        "is_issue": result.get("is_issue", True),
        "original_code": request.code,
        "fixed_code": fixed_code or request.code,
        "fix_available": fixed_code is not None,
        "message": result.get("suggestion"),
        "metadata": {
            "engine": "trained-code-analyzer",
            "raw_result": result,
        },
    }