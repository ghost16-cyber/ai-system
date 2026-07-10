from __future__ import annotations

from typing import Any

from backend.app.training_data.schemas import TrainingLabel

LABEL_SET: tuple[str, ...] = (
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
)

TASK_TYPE_LABEL_MAP = {
    "general_chat": "general",
    "general": "general",
    "code_repair": "code",
    "code": "code",
    "rag": "rag",
    "rag_search": "rag",
    "runtime": "runtime",
    "runtime_check": "runtime",
    "safety": "safety",
    "pytorch_training": "training",
    "classical_ml": "training",
    "training": "training",
    "debugging": "debugging",
    "testing": "testing",
}


def suggest_label(
    user_message: str,
    *,
    routed_task_type: str | None = None,
    routed_specialist: str | None = None,
    rag_used: bool = False,
    source_paths: list[str] | None = None,
) -> TrainingLabel:
    text = user_message.lower()
    task_label = TASK_TYPE_LABEL_MAP.get((routed_task_type or "").strip())
    if task_label in LABEL_SET and task_label != "general":
        return task_label  # type: ignore[return-value]

    if _has_any(text, ("test", "pytest", "lint", "build", "failing check", "ci")):
        return "testing"
    if _has_any(text, ("react", "ui", "vite", "css", "frontend", "tsx", "component")):
        return "frontend"
    if _has_any(text, ("fastapi", "endpoint", "backend", "api", "sqlite", "database")):
        return "backend"
    if _has_any(text, ("bug", "debug", "traceback", "exception", "failure", "error")):
        return "debugging"
    if _has_any(text, ("token", "secret", "password", "credential", "permission")):
        return "safety"
    if _has_any(text, ("cuda", "gpu", "vram", "runtime", "ollama", "local model")):
        return "runtime"
    if _has_any(text, ("train", "training", "dataset", "fine tune", "model")):
        return "training"
    if rag_used and _looks_project_context_heavy(text, source_paths or []):
        return "rag"
    if _has_any(text, ("rag", "retrieval", "index", "source", "grounding")):
        return "rag"
    if _has_any(text, ("hello", "hi", "thanks", "what can", "explain astra")):
        return "general"
    if task_label == "general":
        return "general"
    if routed_specialist == "general_specialist":
        return "general"
    return "unknown"


def normalize_label(value: Any) -> TrainingLabel | None:
    if isinstance(value, str) and value in LABEL_SET:
        return value  # type: ignore[return-value]
    return None


def _looks_project_context_heavy(text: str, source_paths: list[str]) -> bool:
    project_terms = ("repo", "project", "file", "implementation", "where", "how does")
    return bool(source_paths) or _has_any(text, project_terms)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
