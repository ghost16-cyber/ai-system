#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run all Phase‑7 stress‑advisor validation gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Tuple

# ----------------------------------------------------------------------
# Default locations (can be overridden from the CLI)
# ----------------------------------------------------------------------
DEFAULT_STRESS_REPORT = "benchmarks/.runs/20260609-115732/report.json"
DEFAULT_FULL_SUMMARY = "benchmarks/.full_validation/20260609-114512/summary.json"


def run_command(name: str, command: List[str]) -> dict[str, Any]:
    """Execute a sub‑process and return a small result dict."""
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "name": name,
        "command": command,
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def build_commands(
    python_executable: str,
    stress_report: Path,
    full_summary: Path,
) -> List[Tuple[str, List[str]]]:
    """All Phase‑7 commands in the order they should run."""
    return [
        # ------------------------------------------------------------------
        # 1️⃣ Direct‑report stress‑advisor diagnostics
        # ------------------------------------------------------------------
        (
            "stress_advisor_diagnostics_direct_report",
            [
                python_executable,
                "tools/evaluate_phase7_stress_advisor.py",
                "--input",
                str(stress_report),
                "--output",
                "benchmarks/.runs/phase7_stress_advisor_diagnostics_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 2️⃣ Full‑summary stress‑advisor diagnostics
        # ------------------------------------------------------------------
        (
            "stress_advisor_diagnostics_full_summary",
            [
                python_executable,
                "tools/evaluate_phase7_stress_advisor.py",
                "--input",
                str(full_summary),
                "--output",
                "benchmarks/.runs/phase7_stress_advisor_diagnostics_full_summary_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 3️⃣ Stress‑fixture validation
        # ------------------------------------------------------------------
        (
            "stress_fixture_validation",
            [
                python_executable,
                "tools/evaluate_phase7_stress_fixtures.py",
                "--output",
                "benchmarks/.runs/phase7_stress_fixture_evaluation_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 4️⃣ Score fixtures against the direct stress report
        # ------------------------------------------------------------------
        (
            "stress_fixture_scoring_direct_report",
            [
                python_executable,
                "tools/evaluate_phase7_stress_advisor_against_fixtures.py",
                "--stress-report",
                str(stress_report),
                "--output",
                "benchmarks/.runs/phase7_stress_fixture_scoring_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 5️⃣ Export training rows (JSONL + CSV)
        # ------------------------------------------------------------------
        (
            "stress_training_export",
            [
                python_executable,
                "tools/export_phase7_stress_advisor_training_rows.py",
                "--jsonl-output",
                "benchmarks/.runs/phase7_stress_advisor_training_rows_latest.jsonl",
                "--csv-output",
                "benchmarks/.runs/phase7_stress_advisor_training_rows_latest.csv",
                "--report-output",
                "benchmarks/.runs/phase7_stress_advisor_training_export_report_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 6️⃣ Validate the exported training rows
        # ------------------------------------------------------------------
        (
            "stress_training_rows_validation",
            [
                python_executable,
                "tools/validate_phase7_stress_training_rows.py",
                "--input",
                "benchmarks/.runs/phase7_stress_advisor_training_rows_latest.jsonl",
                "--output",
                "benchmarks/.runs/phase7_stress_training_rows_validation_report_latest.json",
            ],
        ),
        # ------------------------------------------------------------------
        # 7️⃣ Validate shadow‑training rows (separate file)
        # ------------------------------------------------------------------
       
        # ------------------------------------------------------------------
        # 8️⃣ **NEW** – evaluate the shadow advisor using the stress‑advisor
        #      training rows (the same rows used for the stress‑advisor)
        # ------------------------------------------------------------------
        (
            "shadow_advisor_evaluation",
            [
                python_executable,
                "tools/evaluate_phase7_shadow_advisor.py",
                "--input",
                "benchmarks/.runs/phase7_stress_advisor_training_rows_latest.jsonl",
                "--output",
                "benchmarks/.runs/phase7_shadow_advisor_evaluation_report_latest.json",
            ],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all Phase‑7 stress‑advisor validation gates."
    )
    parser.add_argument(
        "--stress-report",
        default=DEFAULT_STRESS_REPORT,
        help="Path to direct stress benchmark report.json.",
    )
    parser.add_argument(
        "--full-summary",
        default=DEFAULT_FULL_SUMMARY,
        help="Path to full validation summary.json.",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/.runs/phase7_validation_summary.json",
        help="Path to write Phase‑7 validation summary JSON.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use for sub‑commands.",
    )

    args = parser.parse_args()

    stress_report = Path(args.stress_report).resolve()
    full_summary = Path(args.full_summary).resolve()

    commands = build_commands(
        python_executable=args.python,
        stress_report=stress_report,
        full_summary=full_summary,
    )

    results: List[dict[str, Any]] = []

    for name, command in commands:
        print(f"\n=== Running {name} ===")
        print(" ".join(command))
        result = run_command(name, command)
        print(f"passed={result['passed']} exit_code={result['exit_code']}")

        if result["stdout_tail"]:
            print(result["stdout_tail"])
        if result["stderr_tail"]:
            print(result["stderr_tail"])

        results.append(result)

    # Overall pass = every command succeeded
    passed = all(r["passed"] for r in results)

    summary = {
        "phase": "phase7_validation",
        "passed": passed,
        "stress_report": str(stress_report),
        "full_summary": str(full_summary),
        "commands": results,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("\n=== Phase 7 validation summary ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nPhase 7 validation summary written to: {output_path}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())