from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from backend.app.specialists.model_store import build_model_metadata, save_specialist_model


DEFAULT_MODEL_DIR = Path("models/specialists")


def promote_candidate(
    candidate_dir: str | Path,
    *,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
) -> dict[str, Any]:
    candidate = Path(candidate_dir)
    metadata_path = candidate / "metadata.json"
    gate_path = candidate / "quality_gate_result.json"
    model_path = candidate / "model.joblib"
    missing = [
        str(path)
        for path in (metadata_path, gate_path, model_path)
        if not path.exists()
    ]
    if missing:
        return _write_result(
            candidate,
            {
                "promoted": False,
                "reason": "Required candidate files are missing.",
                "missing_files": missing,
            },
        )

    metadata = _read_json(metadata_path)
    gate = _read_json(gate_path)
    quality_gate_payload = gate.get("quality_gate")
    if not isinstance(quality_gate_payload, dict) or quality_gate_payload.get("passed") is not True:
        return _write_result(
            candidate,
            {
                "promoted": False,
                "reason": "Quality gate did not pass.",
                "quality_gate": quality_gate_payload,
            },
        )

    raw_artifact = joblib.load(model_path)
    pipeline = raw_artifact.get("pipeline") if isinstance(raw_artifact, dict) else raw_artifact
    if pipeline is None:
        return _write_result(
            candidate,
            {
                "promoted": False,
                "reason": "Candidate model artifact does not contain a pipeline.",
            },
        )

    metrics = gate.get("metrics") if isinstance(gate.get("metrics"), dict) else {}
    label_counts = metadata.get("label_distribution")
    if not isinstance(label_counts, dict):
        label_counts = {}
    quality_gate = {
        "specialist": "intent_classifier",
        "passed": True,
        "failures": [],
        "thresholds": quality_gate_payload.get("thresholds", {}),
        "example_count": int(metadata.get("total_examples") or sum(int(v) for v in label_counts.values())),
        "label_counts": label_counts,
        "accuracy": float(metrics.get("accuracy", metadata.get("accuracy", 0.0))),
    }
    promoted_metadata = build_model_metadata(
        specialist="intent_classifier",
        accuracy=quality_gate["accuracy"],
        label_counts={str(key): int(value) for key, value in label_counts.items()},
        train_examples=int(metadata.get("train_examples") or 0),
        test_examples=int(metadata.get("test_examples") or 0),
        quality_gate=quality_gate,
        model_id=str(metadata.get("model_id") or candidate.name),
        lifecycle_status="promoted",
        dataset_id=str(metadata.get("dataset_path") or ""),
        extra_metrics={
            "source_candidate_dir": str(candidate),
            "quality_gate_result": gate,
            **metrics,
        },
    )
    saved = save_specialist_model(
        specialist="intent_classifier",
        pipeline=pipeline,
        metadata=promoted_metadata,
        model_dir=model_dir,
    )
    return _write_result(
        candidate,
        {
            "promoted": True,
            "reason": "Candidate promoted manually.",
            "model_id": saved["metadata"]["model_id"],
            "source_model_path": str(model_path),
            "promoted_model_path": saved["path"],
            "metadata": saved["metadata"],
        },
    )


def _write_result(candidate: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "candidate_dir": str(candidate),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "training_performed": False,
        "backend_started": False,
        "live_routing_called": False,
        **payload,
    }
    output = candidate / "promotion_result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    result["promotion_result_path"] = str(output)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def print_result(result: dict[str, Any]) -> None:
    print(f"Candidate: {result['candidate_dir']}")
    print(f"Promoted: {result['promoted']}")
    print(f"Reason: {result['reason']}")
    if result.get("promoted_model_path"):
        print(f"Promoted model: {result['promoted_model_path']}")
    print(f"Result: {result['promotion_result_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manually promote a gated Astra intent candidate.")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    args = parser.parse_args()
    print_result(promote_candidate(args.candidate_dir, model_dir=args.model_dir))


if __name__ == "__main__":
    main()
