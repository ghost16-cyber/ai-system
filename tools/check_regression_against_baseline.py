from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASELINE_PATH = PROJECT_ROOT / "baselines" / "frozen_phase6_baseline.json"
DEFAULT_CANDIDATE_PATH = PROJECT_ROOT / "benchmarks" / ".full_validation"


HARD_BOOL_GATES: tuple[str, ...] = (
    "overall_passed",
    "pytest_passed",
    "controlled_benchmark_passed",
    "real_repo_dry_run_passed",
    "real_repo_edit_passed",
    "stress_taxonomy_passed",
    "unsafe_actions_zero",
)

EXPECTED_STRESS_CATEGORIES: set[str] = {
    "missing_dependency",
    "ambiguous_source_file",
    "no_obvious_assertion",
    "syntax_trap_or_invalid_patch_blocked",
    "wrong_file_blocked",
}


@dataclass
class RegressionFinding:
    severity: str
    key: str
    baseline: Any
    candidate: Any
    message: str


@dataclass
class RegressionReport:
    passed: bool
    regression_detected: bool
    baseline_path: str
    candidate_path: str
    findings: list[dict[str, Any]]
    baseline_summary: dict[str, Any]
    candidate_summary: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a full validation summary against the frozen baseline."
    )
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE_PATH),
        help="Path to frozen baseline summary.json.",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        help=(
            "Path to candidate summary.json. If omitted, the newest "
            "benchmarks/.full_validation/*/summary.json is used."
        ),
    )
    parser.add_argument(
        "--max-duration-ratio",
        type=float,
        default=1.35,
        help="Warn if candidate duration is slower than baseline by this ratio.",
    )
    parser.add_argument(
        "--max-stage-duration-ratio",
        type=float,
        default=1.5,
        help="Warn if an individual stage is slower than baseline by this ratio.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write regression report JSON.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Treat warning findings as failures.",
    )

    args = parser.parse_args()

    baseline_path = _resolve_path(args.baseline)
    candidate_path = (
        _resolve_path(args.candidate)
        if args.candidate
        else _latest_full_validation_summary()
    )

    baseline = _load_json(baseline_path)
    candidate = _load_json(candidate_path)

    findings = detect_regressions(
        baseline=baseline,
        candidate=candidate,
        max_duration_ratio=args.max_duration_ratio,
        max_stage_duration_ratio=args.max_stage_duration_ratio,
    )

    has_errors = any(finding.severity == "error" for finding in findings)
    has_warnings = any(finding.severity == "warning" for finding in findings)

    regression_detected = has_errors or (args.strict_warnings and has_warnings)
    passed = not regression_detected

    report = RegressionReport(
        passed=passed,
        regression_detected=regression_detected,
        baseline_path=str(baseline_path),
        candidate_path=str(candidate_path),
        findings=[asdict(finding) for finding in findings],
        baseline_summary=_compact_summary(baseline),
        candidate_summary=_compact_summary(candidate),
    )

    print(json.dumps(asdict(report), indent=2, sort_keys=True))

    output_path = args.output
    if output_path:
        resolved_output = _resolve_path(output_path)
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(
            json.dumps(asdict(report), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"Regression report: {resolved_output}")

    return 0 if passed else 1


def detect_regressions(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    max_duration_ratio: float,
    max_stage_duration_ratio: float,
) -> list[RegressionFinding]:
    findings: list[RegressionFinding] = []

    findings.extend(_check_hard_bool_gates(baseline, candidate))
    findings.extend(_check_regression_flag(candidate))
    findings.extend(_check_stress_taxonomy(candidate))
    findings.extend(_check_command_stage_presence(baseline, candidate))
    findings.extend(
        _check_duration_regression(
            baseline=baseline,
            candidate=candidate,
            max_duration_ratio=max_duration_ratio,
            max_stage_duration_ratio=max_stage_duration_ratio,
        )
    )

    return findings


def _check_hard_bool_gates(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[RegressionFinding]:
    findings: list[RegressionFinding] = []

    for key in HARD_BOOL_GATES:
        baseline_value = baseline.get(key)
        candidate_value = candidate.get(key)

        if baseline_value is True and candidate_value is not True:
            findings.append(
                RegressionFinding(
                    severity="error",
                    key=key,
                    baseline=baseline_value,
                    candidate=candidate_value,
                    message=f"Hard gate regressed: {key} was true in baseline but is not true in candidate.",
                )
            )

    return findings


def _check_regression_flag(candidate: dict[str, Any]) -> list[RegressionFinding]:
    if candidate.get("regression_detected") is True:
        return [
            RegressionFinding(
                severity="error",
                key="regression_detected",
                baseline=False,
                candidate=True,
                message="Candidate summary already reports regression_detected=true.",
            )
        ]

    return []


def _check_stress_taxonomy(candidate: dict[str, Any]) -> list[RegressionFinding]:
    stress_command = _command_by_name(candidate, "stress_taxonomy")
    if not stress_command:
        return [
            RegressionFinding(
                severity="error",
                key="stress_taxonomy",
                baseline="present",
                candidate="missing",
                message="Candidate summary is missing the stress_taxonomy command.",
            )
        ]

    parsed_summary = stress_command.get("parsed_summary")
    if not isinstance(parsed_summary, dict):
        return [
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.parsed_summary",
                baseline="dict",
                candidate=type(parsed_summary).__name__,
                message="Stress taxonomy command has no parsed summary.",
            )
        ]

    failure_categories = parsed_summary.get("failure_categories")
    if not isinstance(failure_categories, dict):
        return [
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.failure_categories",
                baseline=sorted(EXPECTED_STRESS_CATEGORIES),
                candidate=failure_categories,
                message="Stress taxonomy parsed summary is missing failure_categories.",
            )
        ]

    observed = {
        str(category)
        for category, count in failure_categories.items()
        if isinstance(count, int) and count > 0
    }

    missing = sorted(EXPECTED_STRESS_CATEGORIES - observed)

    findings: list[RegressionFinding] = []

    if missing:
        findings.append(
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.failure_categories",
                baseline=sorted(EXPECTED_STRESS_CATEGORIES),
                candidate=sorted(observed),
                message=f"Stress taxonomy is missing expected categories: {missing}",
            )
        )

    if parsed_summary.get("errors", 0) != 0:
        findings.append(
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.errors",
                baseline=0,
                candidate=parsed_summary.get("errors"),
                message="Stress taxonomy reported runner errors.",
            )
        )

    if parsed_summary.get("patches_applied", 0) != 0:
        findings.append(
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.patches_applied",
                baseline=0,
                candidate=parsed_summary.get("patches_applied"),
                message="Stress taxonomy applied patches when it should only test blocked/failure behavior.",
            )
        )

    if parsed_summary.get("patch_touched_unexpected_file_count", 0) != 0:
        findings.append(
            RegressionFinding(
                severity="error",
                key="stress_taxonomy.patch_touched_unexpected_file_count",
                baseline=0,
                candidate=parsed_summary.get("patch_touched_unexpected_file_count"),
                message="Stress taxonomy touched unexpected files.",
            )
        )

    return findings


def _check_command_stage_presence(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[RegressionFinding]:
    baseline_names = _command_names(baseline)
    candidate_names = _command_names(candidate)

    missing = sorted(baseline_names - candidate_names)
    if not missing:
        return []

    return [
        RegressionFinding(
            severity="error",
            key="commands",
            baseline=sorted(baseline_names),
            candidate=sorted(candidate_names),
            message=f"Candidate summary is missing command stages: {missing}",
        )
    ]


def _check_duration_regression(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    max_duration_ratio: float,
    max_stage_duration_ratio: float,
) -> list[RegressionFinding]:
    findings: list[RegressionFinding] = []

    baseline_duration = _as_float(baseline.get("duration_seconds"))
    candidate_duration = _as_float(candidate.get("duration_seconds"))

    if (
        baseline_duration > 0
        and candidate_duration > baseline_duration * max_duration_ratio
    ):
        findings.append(
            RegressionFinding(
                severity="warning",
                key="duration_seconds",
                baseline=baseline_duration,
                candidate=candidate_duration,
                message=(
                    "Candidate total duration is significantly slower than baseline: "
                    f"{candidate_duration:.3f}s vs {baseline_duration:.3f}s."
                ),
            )
        )

    baseline_commands = {
        str(command.get("name")): command
        for command in baseline.get("commands", [])
        if isinstance(command, dict) and command.get("name")
    }

    candidate_commands = {
        str(command.get("name")): command
        for command in candidate.get("commands", [])
        if isinstance(command, dict) and command.get("name")
    }

    for name, baseline_command in baseline_commands.items():
        candidate_command = candidate_commands.get(name)
        if not candidate_command:
            continue

        baseline_stage_duration = _as_float(baseline_command.get("duration_seconds"))
        candidate_stage_duration = _as_float(candidate_command.get("duration_seconds"))

        if (
            baseline_stage_duration > 0
            and candidate_stage_duration
            > baseline_stage_duration * max_stage_duration_ratio
        ):
            findings.append(
                RegressionFinding(
                    severity="warning",
                    key=f"commands.{name}.duration_seconds",
                    baseline=baseline_stage_duration,
                    candidate=candidate_stage_duration,
                    message=(
                        f"Stage {name} is significantly slower than baseline: "
                        f"{candidate_stage_duration:.3f}s vs {baseline_stage_duration:.3f}s."
                    ),
                )
            )

    return findings


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "overall_passed": summary.get("overall_passed"),
        "pytest_passed": summary.get("pytest_passed"),
        "controlled_benchmark_passed": summary.get("controlled_benchmark_passed"),
        "real_repo_dry_run_passed": summary.get("real_repo_dry_run_passed"),
        "real_repo_edit_passed": summary.get("real_repo_edit_passed"),
        "stress_taxonomy_passed": summary.get("stress_taxonomy_passed"),
        "unsafe_actions_zero": summary.get("unsafe_actions_zero"),
        "regression_detected": summary.get("regression_detected"),
        "duration_seconds": summary.get("duration_seconds"),
        "output_dir": summary.get("output_dir"),
    }

    stress_command = _command_by_name(summary, "stress_taxonomy")
    if stress_command:
        parsed = stress_command.get("parsed_summary")
        if isinstance(parsed, dict):
            compact["stress_failure_categories"] = parsed.get("failure_categories")
            compact["stress_unsafe_action_block_count"] = parsed.get(
                "unsafe_action_block_count"
            )
            compact["stress_patches_applied"] = parsed.get("patches_applied")

    return compact


def _command_by_name(summary: dict[str, Any], name: str) -> dict[str, Any] | None:
    commands = summary.get("commands")
    if not isinstance(commands, list):
        return None

    for command in commands:
        if isinstance(command, dict) and command.get("name") == name:
            return command

    return None


def _command_names(summary: dict[str, Any]) -> set[str]:
    commands = summary.get("commands")
    if not isinstance(commands, list):
        return set()

    return {
        str(command.get("name"))
        for command in commands
        if isinstance(command, dict) and command.get("name")
    }


def _latest_full_validation_summary() -> Path:
    if not DEFAULT_CANDIDATE_PATH.exists():
        raise FileNotFoundError(
            f"Full validation directory does not exist: {DEFAULT_CANDIDATE_PATH}"
        )

    candidates = sorted(
        DEFAULT_CANDIDATE_PATH.glob("*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No summary.json files found under {DEFAULT_CANDIDATE_PATH}"
        )

    return candidates[0]


def _resolve_path(path: str | None) -> Path:
    if not path:
        raise ValueError("Path is required.")

    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved

    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object in {path}, got {type(data).__name__}")

    return data


def _as_float(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
