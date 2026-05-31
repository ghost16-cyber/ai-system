from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib

from .repair_features import build_repair_feature_text
from .repair_labels import (
    RepairAdvisorInput,
    RepairAdvisorPrediction,
)


DEFAULT_MODEL_PATH = Path("models/repair_advisor/repair_advisor.joblib")


class RepairAdvisor:
    """
    Lightweight shadow advisor for repair tasks.

    Phase 3C behavior:
    - predicts useful metadata
    - does not control orchestration
    - safe fallback if model is missing
    """

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH):
        self.model_path = Path(model_path)
        self.model: Any | None = None

        if self.model_path.exists():
            self.model = joblib.load(self.model_path)

    @property
    def available(self) -> bool:
        return self.model is not None

    def predict(self, input_data: RepairAdvisorInput) -> RepairAdvisorPrediction:
        if self.model is None:
            return self._fallback_prediction(input_data)

        text = build_repair_feature_text(input_data)

        try:
            prediction = self.model.predict([text])[0]

            confidence = 0.5
            if hasattr(self.model, "predict_proba"):
                probabilities = self.model.predict_proba([text])[0]
                confidence = float(max(probabilities))

            parsed = self._parse_prediction_label(str(prediction))

            return RepairAdvisorPrediction(
                bug_type=parsed.get("bug_type", "unknown"),
                source_file=parsed.get("source_file"),
                difficulty=parsed.get("difficulty", "unknown"),
                patch_risk=parsed.get("patch_risk", "unknown"),
                should_use_slm=True,
                confidence=round(confidence, 4),
                reasons=[
                    "advisor_model_prediction",
                    f"model_path={self.model_path.as_posix()}",
                ],
            )

        except Exception as exc:
            fallback = self._fallback_prediction(input_data)
            return RepairAdvisorPrediction(
                bug_type=fallback.bug_type,
                source_file=fallback.source_file,
                difficulty=fallback.difficulty,
                patch_risk=fallback.patch_risk,
                should_use_slm=fallback.should_use_slm,
                confidence=0.0,
                reasons=[
                    "advisor_model_error",
                    f"{type(exc).__name__}: {exc}",
                ],
            )

    def _fallback_prediction(
        self,
        input_data: RepairAdvisorInput,
    ) -> RepairAdvisorPrediction:
        candidate_files = input_data.candidate_files or []
        source_file = self._guess_source_file(candidate_files, input_data.imported_modules)

        return RepairAdvisorPrediction(
            bug_type="unknown",
            source_file=source_file,
            difficulty="unknown",
            patch_risk="unknown",
            should_use_slm=True,
            confidence=0.0,
            reasons=["fallback_no_model_available"],
        )

    def _guess_source_file(
        self,
        candidate_files: list[str],
        imported_modules: list[str] | None,
    ) -> str | None:
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

    def _parse_prediction_label(self, label: str) -> dict[str, str | None]:
        """
        Labels are stored as compact JSON strings by the training script.
        """
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

        return {
            "bug_type": label,
            "source_file": None,
            "difficulty": "unknown",
            "patch_risk": "unknown",
        }
