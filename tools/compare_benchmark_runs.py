from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUMMARY_KEYS = (
    "fix_rate",
    "fixed",
    "patches_proposed",
    "patches_applied",
    "tests_rerun_after_patch",
    "irrelevant_file_reads",
    "unsafe_action_block_count",
    "average_steps",
    "patch_quality_clean_count",
    "patch_quality_risky_count",
    "patch_touched_unexpected_file_count",
    "average_confidence_before_patch",
)

SAFETY_KEYS = (
    "unsafe_action_block_count",
    "irrelevant_file_reads",
    "patch_touched_unexpected_file_count",
)


def main() -> None:
    args = parse_args()
    before = load_report(Path(args.before))
    after = load_report(Path(args.after))
    comparison = compare_reports(before, after)
    print(json.dumps(comparison, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two repair benchmark reports.")
    parser.add_argument("before", help="Earlier report.json path")
    parser.add_argument("after", help="Later report.json path")
    return parser.parse_args()


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_summary = before.get("summary", {})
    after_summary = after.get("summary", {})
    before_cases = _case_map(before)
    after_cases = _case_map(after)

    return {
        "metrics": {
            key: {
                "before": before_summary.get(key),
                "after": after_summary.get(key),
                "delta": _delta(before_summary.get(key), after_summary.get(key)),
            }
            for key in SUMMARY_KEYS
            if key in before_summary or key in after_summary
        },
        "improved_cases": _improved_cases(before_cases, after_cases),
        "regressed_cases": _regressed_cases(before_cases, after_cases),
        "unchanged_failures": _unchanged_failures(before_cases, after_cases),
        "safety_regressions": _safety_regressions(before_summary, after_summary),
    }


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(case.get("case_id")): case
        for case in report.get("cases", [])
        if isinstance(case, dict) and case.get("case_id")
    }


def _improved_cases(
    before_cases: dict[str, dict[str, Any]],
    after_cases: dict[str, dict[str, Any]],
) -> list[str]:
    improved = []
    for case_id, after in after_cases.items():
        before = before_cases.get(case_id, {})
        if not before.get("fixed") and after.get("fixed"):
            improved.append(case_id)
    return sorted(improved)


def _regressed_cases(
    before_cases: dict[str, dict[str, Any]],
    after_cases: dict[str, dict[str, Any]],
) -> list[str]:
    regressed = []
    for case_id, before in before_cases.items():
        after = after_cases.get(case_id, {})
        if before.get("fixed") and not after.get("fixed"):
            regressed.append(case_id)
    return sorted(regressed)


def _unchanged_failures(
    before_cases: dict[str, dict[str, Any]],
    after_cases: dict[str, dict[str, Any]],
) -> list[str]:
    unchanged = []
    for case_id, after in after_cases.items():
        before = before_cases.get(case_id, {})
        if not before.get("fixed") and not after.get("fixed"):
            unchanged.append(case_id)
    return sorted(unchanged)


def _safety_regressions(
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    for key in SAFETY_KEYS:
        before = before_summary.get(key)
        after = after_summary.get(key)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after > before:
            regressions.append({"metric": key, "before": before, "after": after})
    return regressions


def _delta(before: Any, after: Any) -> Any:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return round(after - before, 3)
    return None


if __name__ == "__main__":
    main()
