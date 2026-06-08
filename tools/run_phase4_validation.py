from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VALIDATION_ROOT = ROOT / "benchmarks" / ".phase4_validation"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tools.compare_benchmark_runs import compare_reports, load_report


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout_log: str
    stderr_log: str
    report_path: str | None = None
    copied_report_path: str | None = None

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def main() -> None:
    args = parse_args()
    run_root = VALIDATION_ROOT / time.strftime("%Y%m%d-%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []

    steps.append(run_step("pytest", [sys.executable, "-m", "pytest", "-q"], run_root))
    steps.append(run_step("train_advisor", [sys.executable, "tools/train_repair_advisor.py"], run_root))
    steps.append(
        run_step(
            "evaluate_advisor",
            [
                sys.executable,
                "tools/evaluate_repair_advisor.py",
                "--out",
                str(run_root / "advisor_evaluation.json"),
            ],
            run_root,
        )
    )
    if not args.real_repo:
        steps.append(
            run_benchmark_step(
                "controlled_no_edit",
                [
                    sys.executable,
                    "tools/run_repair_benchmark.py",
                    "--proposer",
                    args.proposer,
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                    "--api-base-url",
                    args.api_base_url,
                ],
                run_root,
            )
        )
        steps.append(
            run_benchmark_step(
                "controlled_edit",
                [
                    sys.executable,
                    "tools/run_repair_benchmark.py",
                    "--allow-edits",
                    "--proposer",
                    args.proposer,
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                    "--api-base-url",
                    args.api_base_url,
                ],
                run_root,
            )
        )

    if args.real_repo and not args.skip_real_repo:
        steps.append(
            run_benchmark_step(
                "real_repo_no_edit",
                [
                    sys.executable,
                    "tools/run_real_repo_benchmark.py",
                    "--proposer",
                    args.proposer,
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                    "--api-base-url",
                    args.api_base_url,
                ],
                run_root,
            )
        )

    if (args.real_repo_edits or (not args.real_repo and not args.skip_real_repo)) and not args.skip_real_repo:
        steps.append(
            run_benchmark_step(
                "real_repo_edit",
                [
                    sys.executable,
                    "tools/run_real_repo_benchmark.py",
                    "--allow-edits",
                    "--proposer",
                    args.proposer,
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                    "--api-base-url",
                    args.api_base_url,
                ],
                run_root,
            )
        )

    summary = build_summary(steps, args, run_root)
    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(summary["gates"], indent=2, sort_keys=True))
    print(f"Phase 4 validation: {run_root}")
    if not summary["gates"]["overall_passed"]:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 4 validation commands.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--proposer", choices=["scripted", "slm"], default="slm")
    parser.add_argument("--max-steps", type=int, default=24)
    parser.add_argument("--poll-timeout", type=float, default=300.0)
    parser.add_argument(
        "--real-repo",
        action="store_true",
        help="Run the real-repo dry-run validation path instead of controlled benchmarks.",
    )
    parser.add_argument(
        "--real-repo-edits",
        action="store_true",
        help="Also run real-repo edit-enabled validation.",
    )
    parser.add_argument("--skip-real-repo", action="store_true")
    parser.add_argument(
        "--phase3-baseline",
        default="",
        help="Optional earlier report.json to compare against the controlled edit run.",
    )
    return parser.parse_args()


def run_step(name: str, command: list[str], run_root: Path) -> StepResult:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    duration = round(time.monotonic() - started, 3)
    stdout_log = run_root / f"{name}.stdout.log"
    stderr_log = run_root / f"{name}.stderr.log"
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")
    return StepResult(
        name=name,
        command=command,
        returncode=completed.returncode,
        duration_seconds=duration,
        stdout_log=str(stdout_log.relative_to(run_root)),
        stderr_log=str(stderr_log.relative_to(run_root)),
    )


def run_benchmark_step(name: str, command: list[str], run_root: Path) -> StepResult:
    result = run_step(name, command, run_root)
    stdout = (run_root / result.stdout_log).read_text(encoding="utf-8")
    report_path = _extract_report_path(stdout)
    if report_path and report_path.exists():
        target = run_root / f"{name}.report.json"
        shutil.copy2(report_path, target)
        result.report_path = str(report_path)
        result.copied_report_path = str(target.relative_to(run_root))
    return result


def build_summary(
    steps: list[StepResult],
    args: argparse.Namespace,
    run_root: Path,
) -> dict[str, Any]:
    reports = {
        step.name: _load_optional_report(run_root / step.copied_report_path)
        for step in steps
        if step.copied_report_path
    }
    controlled_no_edit = reports.get("controlled_no_edit") or {}
    controlled_edit = reports.get("controlled_edit") or {}
    real_repo_no_edit = reports.get("real_repo_no_edit") or {}
    real_repo_edit = reports.get("real_repo_edit") or {}

    comparisons: dict[str, Any] = {}
    if controlled_no_edit and controlled_edit:
        comparisons["no_edit_to_edit"] = compare_reports(controlled_no_edit, controlled_edit)
    if real_repo_no_edit and real_repo_edit:
        comparisons["real_repo_no_edit_to_edit"] = compare_reports(real_repo_no_edit, real_repo_edit)

    baseline = Path(args.phase3_baseline).resolve() if args.phase3_baseline else None
    if baseline and baseline.exists() and controlled_edit:
        comparisons["phase3_baseline_to_controlled_edit"] = compare_reports(
            load_report(baseline),
            controlled_edit,
        )

    controlled_gates = {
        "pytest_passed": _step_passed(steps, "pytest"),
        "advisor_training_passed": _step_passed(steps, "train_advisor"),
        "advisor_evaluation_passed": _step_passed(steps, "evaluate_advisor"),
        "controlled_no_edit_passed": _step_passed(steps, "controlled_no_edit"),
        "controlled_edit_passed": _step_passed(steps, "controlled_edit"),
        "no_edit_applied_no_patches": _summary_value(controlled_no_edit, "patches_applied") == 0
        if controlled_no_edit
        else False,
        "edit_had_verified_fixes": (_summary_value(controlled_edit, "fixed") or 0) > 0
        if controlled_edit
        else False,
        "unsafe_actions_zero": all(
            (_summary_value(report, "unsafe_action_block_count") or 0) == 0
            for report in (controlled_no_edit, controlled_edit)
            if report
        ),
        "real_repo_passed_or_skipped": args.skip_real_repo or _step_passed(steps, "real_repo_edit"),
    }
    real_repo_gates = {
        "pytest_passed": _step_passed(steps, "pytest"),
        "advisor_training_passed": _step_passed(steps, "train_advisor"),
        "advisor_evaluation_passed": _step_passed(steps, "evaluate_advisor"),
        "real_repo_no_edit_passed": _step_passed(steps, "real_repo_no_edit"),
        "real_repo_no_edit_applied_no_patches": _summary_value(real_repo_no_edit, "patches_applied") == 0
        if real_repo_no_edit
        else False,
        "real_repo_no_edit_generated_report": bool(real_repo_no_edit),
        "unsafe_actions_zero": all(
            (_summary_value(report, "unsafe_action_block_count") or 0) == 0
            for report in (real_repo_no_edit, real_repo_edit)
            if report
        ),
        "real_repo_edit_passed_or_skipped": not args.real_repo_edits
        or _step_passed(steps, "real_repo_edit"),
    }
    gates = real_repo_gates if args.real_repo else controlled_gates
    gates["overall_passed"] = all(gates.values())

    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(ROOT),
        "steps": [asdict(step) | {"passed": step.passed} for step in steps],
        "gates": gates,
        "controlled_no_edit_summary": controlled_no_edit.get("summary", {}),
        "controlled_edit_summary": controlled_edit.get("summary", {}),
        "real_repo_no_edit_summary": real_repo_no_edit.get("summary", {}),
        "real_repo_summary": real_repo_edit.get("summary", {}),
        "comparisons": comparisons,
    }


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["TMP"] = "/tmp"
    env["TEMP"] = "/tmp"
    env["TMPDIR"] = "/tmp"
    return env


def _extract_report_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        if line.startswith("Report:"):
            path = Path(line.split("Report:", 1)[1].strip())
            return path if path.is_absolute() else ROOT / path
    return None


def _load_optional_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _step_passed(steps: list[StepResult], name: str) -> bool:
    return any(step.name == name and step.passed for step in steps)


def _summary_value(report: dict[str, Any], key: str) -> Any:
    summary = report.get("summary")
    return summary.get(key) if isinstance(summary, dict) else None


if __name__ == "__main__":
    main()
