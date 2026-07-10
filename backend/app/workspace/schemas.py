from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceInspection(BaseModel):
    root_path: str
    detected_files: list[str] = Field(default_factory=list)
    detected_directories: list[str] = Field(default_factory=list)
    detected_languages: list[str] = Field(default_factory=list)
    detected_frameworks_tools: list[str] = Field(default_factory=list)
    important_files: list[str] = Field(default_factory=list)
    missing_recommended_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0
