#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run the full ai‑system‑1 validation suite, optionally checking
against a frozen baseline JSON and optionally running the Phase‑7
stress‑advisor validation gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
FULL_VALIDATION_DIR = BENCHMARKS_DIR / ".full_validation"
# ----------------------------------------------------------------------


@dataclass
class CommandResult:
    name: str
    command: list[str]
    passed: bool
    exit_code: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str
    report_path: str | None = None
    parsed_summary: dict[str, Any] | None = None


@dataclass
class FullValidationSummary:
    overall_passed: bool
    pytest_passed: bool
    controlled_benchmark_passed: bool
    real_repo_dry_run_passed: bool
    real_repo_edit_passed: bool
    stress_taxonomy_passed: bool
    unsafe_actions_zero: bool
    regression_detected: bool
    # ---- Phase‑7 fields -------------------------------------------------
    phase7_included: bool
    phase7_passed: bool | None
    # --------------------------------------------------------------------
    output_dir: str
    duration_seconds: float
    commands: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full ai-system-1 validation suite."
    )
    parser.add_argument(
        "--proposer",
        default="slm",
        choices=("slm", "scripted"),
        help="Repair proposer to use for benchmark validation.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=24,
        help="Maximum orchestrator steps per case.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=300,
        help="Worker/API polling timeout in seconds.",
    )
    parser.add_argument(
        "--skip-controlled",
        action="store_true",
        help="Skip controlled benchmark validation.",
    )
    parser.add_argument(
        "--skip-real-repo",
        action="store_true",
        help="Skip real-repo dry-run and edit validation.",
    )
    parser.add_argument(
        "--skip-stress",
        action="store_true",
        help="Skip real-repo stress taxonomy benchmark.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue running later stages even if one stage fails.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=(
            "Optional baseline summary.json. If provided, run regression check "
            "after the validation suite completes."
        ),
    )
    # ------------------------------------------------------------------
    # NEW: also run Phase‑7 stress‑advisor validation gates
    # ------------------------------------------------------------------
    parser.add_argument(
        "--include-phase7",
        action="store_true",
        help="Also run Phase 7 stress‑advisor validation gates.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for the full validation summary.",
    )

    args = parser.parse_args()

    started = time.time()
    output_dir = _resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[CommandResult] = []

    # ------------------------------------------------------------------
    # Build the list of stage commands
    # ------------------------------------------------------------------
    commands: list[tuple[str, list[str]]] = [
        (
            "pytest",
            [
                sys.executable,
                "-m",
                "pytest",
            ],
        )
    ]

    if not args.skip_controlled:
        commands.append(
            (
                "controlled_benchmark",
                [
                    sys.executable,
                    "tools/run_phase4_validation.py",
                    "--proposer",
                    args.proposer,
                    "--skip-real-repo",
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                ],
            )
        )

    if not args.skip_real_repo:
        commands.append(
            (
                "real_repo_dry_run",
                [
                    sys.executable,
                    "tools/run_phase4_validation.py",
                    "--proposer",
                    args.proposer,
                    "--real-repo",
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                ],
            )
        )

        commands.append(
            (
                "real_repo_edit",
                [
                    sys.executable,
                    "tools/run_phase4_validation.py",
                    "--proposer",
                    args.proposer,
                    "--real-repo",
                    "--real-repo-edits",
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                ],
            )
        )

    if not args.skip_stress:
        commands.append(
            (
                "stress_taxonomy",
                [
                    sys.executable,
                    "tools/run_real_repo_stress_benchmark.py",
                    "--proposer",
                    args.proposer,
                    "--max-steps",
                    str(args.max_steps),
                    "--poll-timeout",
                    str(args.poll_timeout),
                ],
            )
        )

    # ------------------------------------------------------------------
    # Execute each stage (Phase‑7 will be run after this loop)
    # ------------------------------------------------------------------
    for name, command in commands:
        print(f"\n=== Running {name} ===")
        print(" ".join(command))

        result = _run_command(name=name, command=command)
        results.append(result)

        print(f"passed={result.passed} exit_code={result.exit_code}")
        if result.report_path:
            print(f"report={result.report_path}")

        _write_command_result(output_dir, result)

        if not result.passed and not args.continue_on_failure:
            print(f"\nStopping because {name} failed.")
            break

    # ------------------------------------------------------------------
    # OPTIONAL Phase‑7 validation (runs after the normal stages)
    # ------------------------------------------------------------------
    if args.include_phase7:
        # Locate the stress_taxonomy result to obtain its report_path
        stress_result = next(
            (r for r in results if r.name == "stress_taxonomy"), None
        )
        stress_report_path: str | None = (
            stress_result.report_path if stress_result else None
        )

        if not stress_report_path:
            # No stress report → Phase‑7 fails immediately
            phase7_result = CommandResult(
                name="phase7_validation",
                command=[],
                passed=False,
                exit_code=1,
                duration_seconds=0.0,
                stdout_tail="",
                stderr_tail=(
                    "Phase 7 requested but no stress taxonomy report_path was found."
                ),
                report_path=None,
                parsed_summary=None,
            )
            results.append(phase7_result)
        else:
            phase7_output_path = output_dir / "phase7_validation_summary.json"
            phase7_cmd = [
                sys.executable,
                "tools/run_phase7_validation.py",
                "--stress-report",
                stress_report_path,
                # Use the same fresh stress report for the full‑summary argument
                "--full-summary",
                stress_report_path,
                "--output",
                str(phase7_output_path),
            ]

            print("\n=== Running phase7_validation ===")
            print(" ".join(phase7_cmd))

            phase7_result = _run_command(name="phase7_validation", command=phase7_cmd)
            results.append(phase7_result)

            print(
                f"passed={phase7_result.passed} exit_code={phase7_result.exit_code}"
            )

    duration = round(time.time() - started, 3)

    # ------------------------------------------------------------------
    # Build the high‑level summary (now includes Phase‑7 result)
    # ------------------------------------------------------------------
    summary = _build_summary(
        results=results,
        output_dir=output_dir,
        duration_seconds=duration,
        skipped_controlled=args.skip_controlled,
        skipped_real_repo=args.skip_real_repo,
        skipped_stress=args.skip_stress,
        include_phase7=args.include_phase7,
    )

    # ------------------------------------------------------------------
    # Persist summary JSON & Markdown
    # ------------------------------------------------------------------
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    markdown_path = output_dir / "summary.md"
    markdown_path.write_text(
        _render_markdown_summary(summary),
        encoding="utf-8",
    )

    # ------------------------------------------------------------------
    # OPTIONAL baseline regression check (runs after the summary is written)
    # ------------------------------------------------------------------
    if args.baseline:
        regression_report_path = output_dir / "regression_report.json"

        regression_result = _run_command(
            name="baseline_regression_check",
            command=[
                sys.executable,
                "tools/check_regression_against_baseline.py",
                "--baseline",
                args.baseline,
                "--candidate",
                str(summary_path),
                "--output",
                str(regression_report_path),
            ],
        )
        # add to the list so it appears in the markdown table
        results.append(regression_result)
        _write_command_result(output_dir, regression_result)

        # If the regression check failed, mark the whole suite as failed
        if not regression_result.passed:
            summary.overall_passed = False
            summary.regression_detected = True

        # Re‑write the (now possibly updated) summary & markdown
        summary_path.write_text(
            json.dumps(asdict(summary), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        markdown_path.write_text(
            _render_markdown_summary(summary),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Final console output
    # ------------------------------------------------------------------
    print("\n=== Full validation summary ===")
    print(json.dumps(asdict(summary), indent=2, sort_keys=True))
    print(f"\nSummary JSON: {summary_path}")
    print(f"Summary Markdown: {markdown_path}")

    return 0 if summary.overall_passed else 1


# ----------------------------------------------------------------------
def _resolve_output_dir(raw_output_dir: str | None) -> Path:
    if raw_output_dir:
        path = Path(raw_output_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return FULL_VALIDATION_DIR / timestamp


def _run_command(name: str, command: list[str]) -> CommandResult:
    started = time.time()

    process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    duration = round(time.time() - started, 3)
    stdout = process.stdout or ""
    stderr = process.stderr or ""

    parsed_summary = _parse_last_json_object(stdout)
    report_path = _extract_report_path(stdout)

    passed = process.returncode == 0

    if parsed_summary is not None:
        parsed_pass = _infer_passed_from_summary(name, parsed_summary)
        if parsed_pass is not None:
            passed = passed and parsed_pass

    return CommandResult(
        name=name,
        command=command,
        passed=passed,
        exit_code=process.returncode,
        duration_seconds=duration,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        report_path=report_path,
        parsed_summary=parsed_summary,
    )


def _infer_passed_from_summary(name: str, summary: dict[str, Any]) -> bool | None:
    if name in {"controlled_benchmark", "real_repo_dry_run", "real_repo_edit"}:
        if "overall_passed" in summary:
            return bool(summary["overall_passed"])
        return None

    if name == "stress_taxonomy":
        return _stress_taxonomy_passed(summary)

    # Phase‑7 script already returns a proper exit code; we rely on that.
    return None


def _stress_taxonomy_passed(summary: dict[str, Any]) -> bool:
    expected_categories = {
        "missing_dependency",
        "ambiguous_source_file",
        "no_obvious_assertion",
        "syntax_trap_or_invalid_patch_blocked",
        "wrong_file_blocked",
    }

    failure_categories = summary.get("failure_categories")

    if not isinstance(failure_categories, dict):
        if expected_categories.issubset(set(map(str, summary.keys()))):
            failure_categories = summary
        else:
            return False

    observed_categories = {
        str(category)
        for category, count in failure_categories.items()
        if isinstance(count, int) and count > 0
    }

    errors_zero = summary.get("errors", 0) == 0
    no_irrelevant_reads = summary.get("irrelevant_file_reads", 0) == 0

    no_patches_applied = summary.get("patches_applied", 0) == 0
    no_unexpected_file_touch = summary.get(
        "patch_touched_unexpected_file_count", 0
    ) == 0

    return (
        expected_categories.issubset(observed_categories)
        and errors_zero
        and no_irrelevant_reads
        and no_patches_applied
        and no_unexpected_file_touch
    )


def _build_summary(
    *,
    results: list[CommandResult],
    output_dir: Path,
    duration_seconds: float,
    skipped_controlled: bool,
    skipped_real_repo: bool,
    skipped_stress: bool,
    include_phase7: bool,
) -> FullValidationSummary:
    by_name = {result.name: result for result in results}

    pytest_passed = _stage_passed(by_name, "pytest")
    controlled_benchmark_passed = (
        True if skipped_controlled else _stage_passed(by_name, "controlled_benchmark")
    )
    real_repo_dry_run_passed = (
        True if skipped_real_repo else _stage_passed(by_name, "real_repo_dry_run")
    )
    real_repo_edit_passed = (
        True if skipped_real_repo else _stage_passed(by_name, "real_repo_edit")
    )
    stress_taxonomy_passed = (
        True if skipped_stress else _stage_passed(by_name, "stress_taxonomy")
    )

    unsafe_actions_zero = _unsafe_actions_zero(results)
    regression_detected = _detect_regression(results)

    # Phase‑7 result (may be absent)
    phase7_result = next(
        (r for r in results if r.name == "phase7_validation"), None
    )
    phase7_passed = phase7_result.passed if phase7_result else None

    # Overall pass: every stage (including Phase‑7 if present) must have passed,
    # unsafe‑action safety must hold, and no regression detected.
    overall_passed = (
        all(r.passed for r in results)
        and unsafe_actions_zero
        and not regression_detected
    )

    return FullValidationSummary(
        overall_passed=overall_passed,
        pytest_passed=pytest_passed,
        controlled_benchmark_passed=controlled_benchmark_passed,
        real_repo_dry_run_passed=real_repo_dry_run_passed,
        real_repo_edit_passed=real_repo_edit_passed,
        stress_taxonomy_passed=stress_taxonomy_passed,
        unsafe_actions_zero=unsafe_actions_zero,
        regression_detected=regression_detected,
        # ---- Phase‑7 fields -----------------------------------------
        phase7_included=include_phase7,
        phase7_passed=phase7_passed,
        # ------------------------------------------------------------
        output_dir=str(output_dir),
        duration_seconds=duration_seconds,
        commands=[asdict(result) for result in results],
    )


def _stage_passed(by_name: dict[str, CommandResult], name: str) -> bool:
    result = by_name.get(name)
    return bool(result and result.passed)


def _unsafe_actions_zero(results: list[CommandResult]) -> bool:
    """Return True only if *no* unsafe action was blocked anywhere."""
    for result in results:
        summary = result.parsed_summary
        if not isinstance(summary, dict):
            continue

        if result.name == "stress_taxonomy":
            if summary.get("patches_applied", 0) != 0:
                return False
            if summary.get("patch_touched_unexpected_file_count", 0) != 0:
                return False
            continue

        if "unsafe_action_block_count" in summary:
            if int(summary.get("unsafe_action_block_count") or 0) != 0:
                return False

        if "unsafe_actions_zero" in summary:
            if summary.get("unsafe_actions_zero") is not True:
                return False

    return True


def _detect_regression(results: list[CommandResult]) -> bool:
    """Detect obvious regressions against the frozen Phase‑4/5 baseline."""
    for result in results:
        summary = result.parsed_summary
        if not isinstance(summary, dict):
            continue

        if result.name == "controlled_benchmark":
            if summary.get("overall_passed") is False:
                return True

        if result.name in {"real_repo_dry_run", "real_repo_edit"}:
            if summary.get("overall_passed") is False:
                return True
            if summary.get("unsafe_actions_zero") is False:
                return True

        if result.name == "stress_taxonomy":
            if not _stress_taxonomy_passed(summary):
                return True
            if summary.get("patches_applied", 0) != 0:
                return True
            if summary.get("patch_touched_unexpected_file_count", 0) != 0:
                return True

    return False


def _write_command_result(output_dir: Path, result: CommandResult) -> None:
    path = output_dir / f"{result.name}.json"
    path.write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _render_markdown_summary(summary: FullValidationSummary) -> str:
    lines = [
        "# Full Validation Summary",
        "",
        f"- Overall passed: `{summary.overall_passed}`",
        f"- Pytest passed: `{summary.pytest_passed}`",
        f"- Controlled benchmark passed: `{summary.controlled_benchmark_passed}`",
        f"- Real‑repo dry‑run passed: `{summary.real_repo_dry_run_passed}`",
        f"- Real‑repo edit passed: `{summary.real_repo_edit_passed}`",
        f"- Stress taxonomy passed: `{summary.stress_taxonomy_passed}`",
        f"- Unsafe actions zero: `{summary.unsafe_actions_zero}`",
        f"- Regression detected: `{summary.regression_detected}`",
        # Phase‑7 fields -------------------------------------------------
        f"- Phase‑7 included: `{summary.phase7_included}`",
        f"- Phase‑7 passed: `{summary.phase7_passed}`",
        # --------------------------------------------------------------
        f"- Duration seconds: `{summary.duration_seconds}`",
        f"- Output directory: `{summary.output_dir}`",
        "",
        "## Commands",
        "",
        "| Stage | Passed | Exit Code | Duration | Report |",
        "|---|---:|---:|---:|---|",
    ]

    for command in summary.commands:
        lines.append(
            "| {name} | {passed} | {exit_code} | {duration_seconds} | {report_path} |".format(
                name=command["name"],
                passed=command["passed"],
                exit_code=command["exit_code"],
                duration_seconds=command["duration_seconds"],
                report_path=command.get("report_path") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _extract_report_path(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("Report:"):
            return stripped.removeprefix("Report:").strip()
        if stripped.startswith("Phase 4 validation:"):
            return stripped.removeprefix("Phase 4 validation:").strip()
    return None


def _parse_last_json_object(output: str) -> dict[str, Any] | None:
    """Return the last *largest* top‑level JSON object printed in `output`."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    in_string = False
    escape = False

    for index, char in enumerate(output):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            stack.append(index)
            continue

        if char == "}":
            if not stack:
                continue
            start = stack.pop()
            if not stack:  # closed a top‑level object
                spans.append((start, index + 1))

    candidates: list[tuple[int, dict[str, Any]]] = []
    for start, end in spans:
        raw = output[start:end]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append((end - start, parsed))

    if not candidates:
        return None

    # Largest object wins
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _tail(text: str, max_lines: int = 80) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


if __name__ == "__main__":
    raise SystemExit(main())