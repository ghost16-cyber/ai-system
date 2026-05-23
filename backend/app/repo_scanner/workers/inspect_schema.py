# repo_scanner/workers/inspect_schema.py

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TargetKind = Literal["file", "directory", "module", "unknown"]


class FileSummary(BaseModel):
    path: str
    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    complexity_score: float = 0.0
    role_hint: str = "unknown"

class DirectorySummary(BaseModel):
    path: str
    files: list[str] = []
    subdirectories: list[str] = []


class InspectResult(BaseModel):
    target: str
    target_kind: TargetKind
    summary: str
    file_summary: FileSummary | None = None
    directory_summary: DirectorySummary | None = None

