from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.commands.schemas import CommandSuggestion


class ErrorAnalysis(BaseModel):
    error_type: str
    likely_cause: str
    suggested_fix: str
    safe_commands_to_try: list[CommandSuggestion] = Field(default_factory=list)
    files_to_check: list[str] = Field(default_factory=list)
    confidence: float
    needs_user_action: bool = True
    missing_context_questions: list[str] = Field(default_factory=list)
