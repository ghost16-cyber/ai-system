from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


RiskLevel = Literal["low", "medium", "high"]


class CommandSuggestion(BaseModel):
    command: str
    working_directory: str
    purpose: str
    risk_level: RiskLevel
    requires_confirmation: bool
    why_safe: str
    expected_output_hint: str
    allowed: bool = True
    executed: bool = False
