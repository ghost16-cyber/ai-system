from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib

from .advisor_heads import (
    HEAD_FIELDS,
    average_confidence,
    calibrated_head_prediction,
    predict_head,
    prediction_tuple_to_dict,
)
from .repair_features import build_repair_feature_text
from .repair_labels import (
    AdvisorFieldPrediction,
    RepairAdvisorInput,
    RepairAdvisorPrediction,
)

DEFAULT_MODEL_PATH = Path("models/repair_advisor/repair_advisor.joblib")


class RepairAdvisor:
    """Shadow advisor that predicts a separate value & confidence for each head."""

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH):
        self.model_path = Path(model_path)
        self.model: Any | None = None
        if self.model_path.exists():
            self.model = joblib.load(self.model_path)

        self.version = 1
        self.heads: dict[str, Any] = {}
        if isinstance(self.model, dict) and isinstance(self.model.get("heads"), dict):
            self.version = int(self.model.get("version", 2) or 2)
            self.heads = {
                head: self.model["heads"][head]
                for head in HEAD_FIELDS
                if head in self.model["heads"]
            }

    @property
    def available(self) -> bool:
        return self.model is not None

    # ------------------------------------------------------------------
    def predict(self, input_data: RepairAdvisorInput) -> RepairAdvisorPrediction:
        """Return a multi-head prediction with per-head confidences."""
        if self.model is None:
            return self._fallback_prediction(input_data)

        text = build_repair_feature_text(input_data)

        try:
            if self.heads:
                predictions = {
                    head: calibrated_head_prediction(
                        head=head,
                        prediction=predict_head(self.heads[head], text),
                        input_data=input_data,
                    )
                    for head in HEAD_FIELDS
                    if head in self.heads
                }
                if len(predictions) != len(HEAD_FIELDS):
                    missing = sorted(set(HEAD_FIELDS) - set(predictions))
                    raise ValueError(f"Advisor bundle missing heads: {missing}")

                overall = average_confidence(list(predictions.values()))

                return RepairAdvisorPrediction(
                    bug_type=AdvisorFieldPrediction(
                        value=predictions["bug_type"].value or "unknown",
                        confidence=predictions["bug_type"].confidence,
                    ),
                    source_file=AdvisorFieldPrediction(
                        value=predictions["source_file"].value,
                        confidence=predictions["source_file"].confidence,
                    ),
                    difficulty=AdvisorFieldPrediction(
                        value=predictions["difficulty"].value or "unknown",
                        confidence=predictions["difficulty"].confidence,
                    ),
                    patch_risk=AdvisorFieldPrediction(
                        value=predictions["patch_risk"].value or "unknown",
                        confidence=predictions["patch_risk"].confidence,
                    ),
                    should_use_slm=True,
                    overall_confidence=overall,
                    reasons=[
                        "multi_head_advisor_prediction",
                        f"version={self.version}",
                        f"model_path={self.model_path.as_posix()}",
                    ],
                )

            parsed, confidence = self._predict_legacy_model(text)
            return RepairAdvisorPrediction(
                bug_type=AdvisorFieldPrediction(
                    value=parsed.get("bug_type", "unknown"),
                    confidence=confidence,
                ),
                source_file=AdvisorFieldPrediction(
                    value=parsed.get("source_file"),
                    confidence=confidence,
                ),
                difficulty=AdvisorFieldPrediction(
                    value=parsed.get("difficulty", "unknown"),
                    confidence=confidence,
                ),
                patch_risk=AdvisorFieldPrediction(
                    value=parsed.get("patch_risk", "unknown"),
                    confidence=confidence,
                ),
                should_use_slm=True,
                overall_confidence=confidence,
                reasons=[
                    "legacy_advisor_model_prediction",
                    f"model_path={self.model_path.as_posix()}",
                ],
            )
        except Exception as exc:  # defensive fallback
            return self._fallback_prediction(input_data, str(exc))

    # ------------------------------------------------------------------
    def _fallback_prediction(
        self,
        input_data: RepairAdvisorInput,
        error_message: str | None = None,
    ) -> RepairAdvisorPrediction:
        source_file = self._guess_source_file(
            input_data.candidate_files or [], input_data.imported_modules
        )
        reasons = ["fallback_no_model_available"]
        if error_message:
            reasons = ["advisor_model_error", error_message]

        return RepairAdvisorPrediction(
            bug_type=AdvisorFieldPrediction(value="unknown", confidence=0.0),
            source_file=AdvisorFieldPrediction(value=source_file, confidence=0.0),
            difficulty=AdvisorFieldPrediction(value="unknown", confidence=0.0),
            patch_risk=AdvisorFieldPrediction(value="unknown", confidence=0.0),
            should_use_slm=True,
            overall_confidence=0.0,
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _guess_source_file(candidate_files: list[str], imported_modules: list[str] | None) -> str | None:
        modules = imported_modules or []
        for module in modules:
            module_path = module.replace(".", "/") + ".py"
            for candidate in candidate_files:
                if candidate.endswith(module_path):
                    return candidate
        for candidate in candidate_files:
            if candidate.startswith("src/") and candidate.endswith(".py"):
                return candidate
        for candidate in candidate_files:
            if candidate.endswith(".py") and not candidate.startswith("tests/"):
                return candidate
        return None

    # ------------------------------------------------------------------
    def _predict_legacy_model(self, text: str) -> tuple[dict[str, str | None], float]:
        raw_prediction = self.model.predict([text])[0]
        confidence = 0.5
        if hasattr(self.model, "predict_proba"):
            try:
                probabilities = self.model.predict_proba([text])
                if probabilities and isinstance(probabilities, list):
                    confidence = round(
                        sum(float(max(proba[0])) for proba in probabilities) / len(probabilities),
                        4,
                    )
            except Exception:
                confidence = 0.5
        return self._parse_prediction_label(raw_prediction), confidence

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_prediction_label(
        label: str | dict[str, str | None] | tuple[str | None, ...] | list[str | None],
    ) -> dict[str, str | None]:
        """
        Normalise whatever the model returns into a dict with the four keys.
        Supports dict, tuple/list in HEAD_FIELDS order, JSON string, or plain string.
        """
        if isinstance(label, dict):
            return {
                "bug_type": str(label.get("bug_type", "unknown")),
                "source_file": label.get("source_file"),
                "difficulty": str(label.get("difficulty", "unknown")),
                "patch_risk": str(label.get("patch_risk", "unknown")),
            }

        if isinstance(label, (list, tuple)):
            if len(label) == len(HEAD_FIELDS):
                return prediction_tuple_to_dict(list(label))

        if isinstance(label, str):
            try:
                parsed = json.loads(label)
                if isinstance(parsed, dict):
                    return {
                        "bug_type": str(parsed.get("bug_type", "unknown")),
                        "source_file": parsed.get("source_file"),
                        "difficulty": str(parsed.get("difficulty", "unknown")),
                        "patch_risk": str(parsed.get("patch_risk", "unknown")),
                    }
            except json.JSONDecodeError:
                pass
            return {"bug_type": str(label), "source_file": None, "difficulty": "unknown", "patch_risk": "unknown"}

        return {"bug_type": str(label), "source_file": None, "difficulty": "unknown", "patch_risk": "unknown"}
