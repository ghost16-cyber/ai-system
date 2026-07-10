from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TrainingExampleSource = Literal["chat_run", "manual", "imported"]
TrainingLabel = Literal[
    "general",
    "code",
    "rag",
    "runtime",
    "safety",
    "training",
    "frontend",
    "backend",
    "debugging",
    "testing",
    "unknown",
]
TrainingLabelStatus = Literal[
    "unlabeled",
    "suggested",
    "confirmed",
    "corrected",
    "rejected",
]
UsefulnessRating = Literal["good", "okay", "bad"]
TrainingExportFormat = Literal["jsonl", "csv"]


class TrainingExample(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime | None = None
    source: TrainingExampleSource
    chat_run_id: str | None = None
    user_message: str
    assistant_response: str | None = None
    routed_task_type: str | None = None
    routed_specialist: str | None = None
    routing_confidence: float | None = None
    rag_used: bool = False
    rag_skip_reason: str | None = None
    grounding_status: str | None = None
    source_paths: list[str] = Field(default_factory=list)
    safety_status: str | None = None
    suggested_label: TrainingLabel | None = None
    corrected_label: TrainingLabel | None = None
    final_label: TrainingLabel | None = None
    label_status: TrainingLabelStatus = "unlabeled"
    usefulness_rating: UsefulnessRating | None = None
    notes: str | None = None


class TrainingExampleCreateRequest(BaseModel):
    user_message: str = Field(..., min_length=1)
    assistant_response: str | None = None
    source: TrainingExampleSource = "manual"
    routed_task_type: str | None = None
    routed_specialist: str | None = None
    routing_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rag_used: bool = False
    rag_skip_reason: str | None = None
    grounding_status: str | None = None
    source_paths: list[str] = Field(default_factory=list)
    safety_status: str | None = None
    suggested_label: TrainingLabel | None = None
    corrected_label: TrainingLabel | None = None
    final_label: TrainingLabel | None = None
    label_status: TrainingLabelStatus | None = None
    usefulness_rating: UsefulnessRating | None = None
    notes: str | None = None


class TrainingExampleLabelRequest(BaseModel):
    corrected_label: TrainingLabel | None = None
    label_status: TrainingLabelStatus
    usefulness_rating: UsefulnessRating | None = None
    notes: str | None = None


class TrainingExportRequest(BaseModel):
    format: TrainingExportFormat = "jsonl"
