from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compare_benchmark_runs import compare_reports, load_report


def main() -> None:
    args = parse_args()
    reports = {
        "phase3": _optional_report(args.phase3),
        "phase4": _optional_report(args.phase4),
        "phase5_dry_run": _optional_report(args.phase5_dry_run),
        "phase5_edit_run": _optional_report(args.phase5_edit_run),
    }
    reports = {name: report for name, report in reports.items() if report}

    output = {
        "summaries": {
            name: report.get("summary", {})
            for name, report in reports.items()
        },
        "comparisons": _comparisons(reports),
    }
    print(json.dumps(output, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Phase 3/4/5 benchmark reports.")
    parser.add_argument("--phase3", default="", help="Phase 3 baseline report.json")
    parser.add_argument("--phase4", default="", help="Phase 4 advisor report.json")
    parser.add_argument("--phase5-dry-run", default="", help="Phase 5 real-repo dry-run report.json")
    parser.add_argument("--phase5-edit-run", default="", help="Phase 5 real-repo edit report.json")
    return parser.parse_args()


def _optional_report(path: str) -> dict[str, Any]:
    if not path:
        return {}
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise SystemExit(f"Report not found: {resolved}")
    return load_report(resolved)


def _comparisons(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pairs = (
        ("phase3", "phase4"),
        ("phase4", "phase5_dry_run"),
        ("phase5_dry_run", "phase5_edit_run"),
        ("phase4", "phase5_edit_run"),
    )
    result: dict[str, Any] = {}
    for before, after in pairs:
        if before in reports and after in reports:
            result[f"{before}_to_{after}"] = compare_reports(reports[before], reports[after])
    return result


if __name__ == "__main__":
    main()
