from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


IntentLabel = Literal[
    "code_repair",
    "runtime_check",
    "report_generation",
    "rag_search",
    "pytorch_training",
    "general_chat",
]

ErrorLabel = Literal[
    "missing_import",
    "syntax_error",
    "dependency_missing",
    "cuda_oom",
    "npm_build_error",
    "pytest_failure",
    "unknown_error",
]


class SpecialistRequest(BaseModel):
    text: str = Field(default="")
    context: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    specialist: str | None = None
    use_slm_intent: bool = False


class SpecialistPrediction(BaseModel):
    specialist: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    features: dict[str, Any] = Field(default_factory=dict)
    reason: str
    model_version: str = "rules_v1"
    advisory_only: bool = True


class IntentPrediction(SpecialistPrediction):
    specialist: Literal["intent_classifier"] = "intent_classifier"
    label: IntentLabel


class ErrorClassification(SpecialistPrediction):
    specialist: Literal["error_classifier"] = "error_classifier"
    label: ErrorLabel


class SpecialistEvaluationRequest(BaseModel):
    dataset_path: str | None = None


class SpecialistFeedback(BaseModel):
    specialist: str
    text: str
    expected_label: str
    predicted_label: str | None = None
    user_corrected_label: str | None = None
    source: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpecialistTrainRequest(BaseModel):
    dataset_path: str | None = None
    feedback_path: str | None = None
    model_dir: str | None = None
    dataset_id: str | None = None
    dataset_registry_path: str | None = None
    training_job_store_path: str | None = None
    thresholds: dict[str, float | int] = Field(default_factory=dict)


class SpecialistModelLifecycleRequest(BaseModel):
    model_dir: str | None = None


class SpecialistDatasetRegisterRequest(BaseModel):
    dataset_path: str
    dataset_id: str | None = None
    registry_path: str | None = None


class SpecialistDatasetLifecycleRequest(BaseModel):
    registry_path: str | None = None
