from __future__ import annotations

from collections.abc import Iterable

from .schemas import IntentPrediction, SpecialistRequest


INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "code_repair": (
        "fix",
        "bug",
        "broken",
        "failing",
        "failure",
        "traceback",
        "patch",
        "repair",
        "test failed",
        "lint error",
    ),
    "runtime_check": (
        "runtime",
        "hardware",
        "cuda",
        "vram",
        "gpu",
        "cpu fallback",
        "execution profile",
        "low vram",
    ),
    "report_generation": (
        "report",
        "summary",
        "summarize",
        "overview",
        "status report",
        "write up",
        "document",
    ),
    "rag_search": (
        "rag",
        "retrieve",
        "retrieval",
        "embedding",
        "vector",
        "faiss",
        "search docs",
        "index",
    ),
    "pytorch_training": (
        "pytorch",
        "train",
        "training",
        "fine tune",
        "fine-tune",
        "finetune",
        "batch size",
        "gradient",
        "epoch",
        "model training",
    ),
}


def predict_intent(payload: SpecialistRequest | dict) -> IntentPrediction:
    request = _request(payload)
    text = _combined_text(request).lower()
    scores = {
        label: _keyword_score(text, keywords)
        for label, keywords in INTENT_KEYWORDS.items()
    }
    label, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        label = "general_chat"

    confidence = _confidence(score)
    matched = _matched_keywords(text, INTENT_KEYWORDS.get(label, ()))
    return IntentPrediction(
        label=label,  # type: ignore[arg-type]
        confidence=confidence,
        features={
            "matched_keywords": matched,
            "scores": scores,
            "text_length": len(text),
            "context_keys": sorted(request.context),
        },
        reason=(
            "Rule-based intent specialist matched task keywords."
            if matched
            else "No task-specific keywords matched; using general chat."
        ),
    )


def _request(payload: SpecialistRequest | dict) -> SpecialistRequest:
    if isinstance(payload, SpecialistRequest):
        return payload
    return SpecialistRequest.model_validate(payload)


def _combined_text(request: SpecialistRequest) -> str:
    values = [request.text]
    for key in ("goal", "task", "previous_intent", "project_type"):
        value = request.context.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(value for value in values if value)


def _keyword_score(text: str, keywords: Iterable[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def _confidence(score: int) -> float:
    if score <= 0:
        return 0.35
    return round(min(0.95, 0.58 + score * 0.11), 3)
