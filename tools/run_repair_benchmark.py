from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.benchmark.test_output_parser import parse_pytest_output
from app.benchmark.trace_compactor import compact_orchestrator_trace
from app.advisors.shadow import run_shadow_repair_advisor


DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"
DEFAULT_RUNS_DIR = ROOT / "benchmarks" / ".runs"


@dataclass
class CaseResult:
    case_id: str
    bug_type: str
    difficulty: str
    multi_file: bool
    expected_source_file: str | None
    status: str
    fixed: bool
    initial_pytest: dict[str, Any]
    final_pytest: dict[str, Any]
    orchestrator_status: str | None
    final_response: str | None
    tool_actions: list[str]
    proposed_patch: dict[str, Any] | None
    compact_trace_path: str | None
    patch_applied: bool
    tests_rerun_after_patch: bool
    loop_prevention_count: int
    irrelevant_file_reads: int
    unsafe_action_blocks: int
    patch_quality: str
    patch_touched_expected_file: bool
    patch_touched_unexpected_file: bool
    patch_changed_lines: int | None
    syntax_valid_after_patch: bool | None
    tests_passed_after_patch: bool
    confidence_before_patch: float | None
    confidence_after_patch: float | None
    apply_decision: str | None
    fallback_reason: str | None
    duration_seconds: float
    advisor_shadow: dict[str, Any] | None = None
    error: str | None = None


def main() -> None:
    args = parse_args()
    cases_dir = Path(args.cases_dir).resolve()
    if not cases_dir.exists():
        raise SystemExit(
            f"Cases directory not found: {cases_dir}. "
            "Run tools/generate_repair_benchmark_cases.py first."
        )

    run_root = DEFAULT_RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)

    results = []
    for case_dir in sorted(path for path in cases_dir.iterdir() if path.is_dir()):
        result = run_case(case_dir, run_root, args)
        results.append(result)
        icon = "PASS" if result.fixed else "FAIL"
        print(
            f"[{icon}] {result.case_id}: {result.status} "
            f"actions={','.join(result.tool_actions)}"
        )

    report = build_report(results)
    report_path = run_root / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"Report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run controlled repair benchmarks.")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-edits", action="store_true")
    parser.add_argument("--proposer", choices=["scripted", "slm"], default="slm")
    parser.add_argument("--slm-model", default="qwen2.5-coder:1.5b")
    parser.add_argument("--slm-base-url", default="http://localhost:11434")
    parser.add_argument("--max-steps", type=int, default=18)
    parser.add_argument("--poll-timeout", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    return parser.parse_args()


def run_case(case_dir: Path, run_root: Path, args: argparse.Namespace) -> CaseResult:
    started = time.monotonic()
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
    case_id = str(metadata["case_id"])
    work_dir = run_root / case_id
    shutil.copytree(
        case_dir,
        work_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )

    initial: dict[str, Any] = {}
    initial_output = ""
    initial, initial_output = run_pytest(work_dir)
    write_text(work_dir / "initial_pytest.txt", initial_output)
    try:
        job = post_orchestrate(work_dir, metadata, args)
        completed = poll_job(job["status_url"], args)
        result = completed.get("result") or {}
        trace = fetch_compact_trace(job["status_url"], args)
        if not trace:
            trace = compact_orchestrator_trace(result.get("trace"))
        write_json(work_dir / "compact_trace.json", trace)
        write_text(work_dir / "final_response.txt", str(result.get("final_response") or ""))
        final, final_output = run_pytest(work_dir)
        write_text(work_dir / "final_pytest.txt", final_output)
        fixed = final["status"] == "passed" and bool(args.allow_edits)
        advisor_shadow = build_advisor_shadow(
            metadata=metadata,
            initial_pytest=initial,
            final_pytest=final,
            trace=trace
        )
        metrics = _trace_metrics(trace, metadata)
        return CaseResult(
            case_id=case_id,
            bug_type=str(metadata.get("bug_type", "unknown")),
            difficulty=str(metadata.get("difficulty", "unknown")),
            multi_file=bool(metadata.get("multi_file", False)),
            expected_source_file=_optional_str(metadata.get("expected_source_file")),
            status="fixed" if fixed else "not_fixed",
            fixed=fixed,
            initial_pytest=initial,
            final_pytest=final,
            orchestrator_status=result.get("status"),
            final_response=result.get("final_response"),
            tool_actions=trace.get("tool_actions", []),
            proposed_patch=trace.get("proposed_patch"),
            compact_trace_path=str((work_dir / "compact_trace.json").relative_to(run_root)),
            patch_applied=metrics["patch_applied"],
            tests_rerun_after_patch=metrics["tests_rerun_after_patch"],
            loop_prevention_count=metrics["loop_prevention_count"],
            irrelevant_file_reads=metrics["irrelevant_file_reads"],
            unsafe_action_blocks=metrics["unsafe_action_blocks"],
            patch_quality=str(metrics["patch_quality"]),
            patch_touched_expected_file=bool(metrics["patch_touched_expected_file"]),
            patch_touched_unexpected_file=bool(metrics["patch_touched_unexpected_file"]),
            patch_changed_lines=_optional_int(metrics["patch_changed_lines"]),
            syntax_valid_after_patch=_optional_bool(metrics["syntax_valid_after_patch"]),
            tests_passed_after_patch=bool(metrics["tests_passed_after_patch"]),
            confidence_before_patch=_optional_float(metrics["confidence_before_patch"]),
            confidence_after_patch=_optional_float(metrics["confidence_after_patch"]),
            apply_decision=_optional_str(metrics["apply_decision"]),
            fallback_reason=_optional_str(metrics["fallback_reason"]),
            duration_seconds=round(time.monotonic() - started, 3),
            advisor_shadow=advisor_shadow,
        )
    except Exception as error:
        final, final_output = run_pytest(work_dir)
        write_text(work_dir / "final_pytest.txt", final_output)
        advisor_shadow = build_advisor_shadow(
            metadata=metadata,
            initial_pytest=initial,
            final_pytest=final,
            trace={},
        )
        return CaseResult(
            case_id=case_id,
            bug_type=str(metadata.get("bug_type", "unknown")),
            difficulty=str(metadata.get("difficulty", "unknown")),
            multi_file=bool(metadata.get("multi_file", False)),
            expected_source_file=_optional_str(metadata.get("expected_source_file")),
            status="error",
            fixed=False,
            initial_pytest=initial,
            final_pytest=final,
            orchestrator_status=None,
            final_response=None,
            tool_actions=[],
            proposed_patch=None,
            compact_trace_path=None,
            patch_applied=False,
            tests_rerun_after_patch=False,
            loop_prevention_count=0,
            irrelevant_file_reads=0,
            unsafe_action_blocks=0,
            patch_quality="invalid",
            patch_touched_expected_file=False,
            patch_touched_unexpected_file=False,
            patch_changed_lines=None,
            syntax_valid_after_patch=None,
            tests_passed_after_patch=False,
            confidence_before_patch=None,
            confidence_after_patch=None,
            apply_decision=None,
            fallback_reason=f"{type(error).__name__}: {error}",
            duration_seconds=round(time.monotonic() - started, 3),
            advisor_shadow=advisor_shadow,
            error=f"{type(error).__name__}: {error}",
        )
    
def build_advisor_shadow(
    *,
    metadata: dict[str, Any],
    initial_pytest: dict[str, Any],
    final_pytest: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    failing_test_file = (
        final_pytest.get("failing_test_file")
        or initial_pytest.get("failing_test_file")
        or metadata.get("expected_test_file")
    )

    failing_test_name = (
        final_pytest.get("failing_test_name")
        or initial_pytest.get("failing_test_name")
    )

    assertion_summary = (
        final_pytest.get("assertion_summary")
        or initial_pytest.get("assertion_summary")
    )

    return run_shadow_repair_advisor(
        goal=str(metadata.get("goal", "")),
        failing_test_file=failing_test_file,
        failing_test_name=failing_test_name,
        assertion_summary=assertion_summary,
        imported_modules=metadata.get("imported_modules")
        or metadata.get("imports")
        or [],
        candidate_files=trace.get("candidate_files", []),
        inspected_files=trace.get("inspected_files", []),
        tool_actions=trace.get("tool_actions", []),
    )


def post_orchestrate(
    work_dir: Path,
    metadata: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    payload = {
        "goal": metadata.get("goal", f"Fix {metadata.get('case_id', 'this case')}"),
        "path": work_dir.relative_to(ROOT).as_posix(),
        "allow_edits": args.allow_edits,
        "allow_tests": True,
        "max_steps": args.max_steps,
        "proposer": args.proposer,
        "slm_model": args.slm_model,
        "slm_base_url": args.slm_base_url,
    }
    return request_json(f"{args.api_base_url.rstrip('/')}/orchestrate", method="POST", payload=payload)


def poll_job(status_url: str, args: argparse.Namespace) -> dict[str, Any]:
    url = f"{args.api_base_url.rstrip('/')}{status_url}"
    deadline = time.monotonic() + args.poll_timeout
    while time.monotonic() < deadline:
        job = request_json(url)
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(args.poll_interval)
    raise TimeoutError(f"Timed out waiting for job: {status_url}")


def fetch_compact_trace(status_url: str, args: argparse.Namespace) -> dict[str, Any]:
    url = f"{args.api_base_url.rstrip('/')}{status_url}/trace/compact"
    response = request_json(url)
    return response.get("trace") if isinstance(response.get("trace"), dict) else {}


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {body}") from error


def run_pytest(cwd: Path) -> tuple[dict[str, Any], str]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=cwd,
        capture_output=True,
        env=_test_env(),
        text=True,
        timeout=60,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return parse_pytest_output(output, exit_code=completed.returncode), output


def _test_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TMP"] = "/tmp"
    env["TEMP"] = "/tmp"
    env["TMPDIR"] = "/tmp"
    return env


def build_report(results: list[CaseResult]) -> dict[str, Any]:
    fixed = sum(result.fixed for result in results)
    errored = sum(result.status == "error" for result in results)
    action_counts = [
        len(result.tool_actions)
        for result in results
        if result.tool_actions
    ]
    advisor = _advisor_metrics(results)
    return {
        "summary": {
            "cases_run": len(results),
            "fixed": fixed,
            "failed": len(results) - fixed,
            "errors": errored,
            "fix_rate": round(fixed / len(results), 3) if results else 0,
            "patches_proposed": sum(result.proposed_patch is not None for result in results),
            "patches_applied": sum(result.patch_applied for result in results),
            "tests_rerun_after_patch": sum(result.tests_rerun_after_patch for result in results),
            "average_steps": round(sum(action_counts) / len(action_counts), 2)
            if action_counts
            else 0,
            "loop_prevention_count": sum(result.loop_prevention_count for result in results),
            "irrelevant_file_reads": sum(result.irrelevant_file_reads for result in results),
            "unsafe_action_block_count": sum(result.unsafe_action_blocks for result in results),
            "tests_verified": sum(
                result.final_pytest.get("status") == "passed" for result in results
            ),
            "multi_file_cases": sum(result.multi_file for result in results),
            "multi_file_fixed": sum(result.fixed and result.multi_file for result in results),
            "patch_quality_clean_count": _quality_count(results, "clean"),
            "patch_quality_probably_ok_count": _quality_count(results, "probably_ok"),
            "patch_quality_risky_count": _quality_count(results, "risky"),
            "patch_quality_invalid_count": _quality_count(results, "invalid"),
            "patch_quality_irrelevant_count": _quality_count(results, "irrelevant"),
            "patch_quality_too_large_count": _quality_count(results, "too_large"),
            "patch_touched_expected_file_count": sum(
                result.patch_touched_expected_file for result in results
            ),
            "patch_touched_unexpected_file_count": sum(
                result.patch_touched_unexpected_file for result in results
            ),
            "average_changed_lines": _average_changed_lines(results),
            "max_changed_lines": max(
                (result.patch_changed_lines or 0 for result in results),
                default=0,
            ),
            "syntax_valid_after_patch": sum(
                result.syntax_valid_after_patch is True for result in results
            ),
            "tests_passed_after_patch": sum(result.tests_passed_after_patch for result in results),
            "average_confidence_before_patch": _average_confidence(results, "before"),
            "average_confidence_after_patch": _average_confidence(results, "after"),
            "advisor_available_count": advisor["available_count"],
            "advisor_source_file_accuracy": advisor["source_file_accuracy"],
            "advisor_bug_type_accuracy": advisor["bug_type_accuracy"],
            "advisor_average_confidence": advisor["average_confidence"],
        },
        "advisor": advisor,
        "patch_quality": {
            "clean": _quality_count(results, "clean"),
            "probably_ok": _quality_count(results, "probably_ok"),
            "risky": _quality_count(results, "risky"),
            "invalid": _quality_count(results, "invalid"),
            "irrelevant": _quality_count(results, "irrelevant"),
            "too_large": _quality_count(results, "too_large"),
        },
        "patch_scope": {
            "expected_file_touched": sum(result.patch_touched_expected_file for result in results),
            "unexpected_file_touched": sum(result.patch_touched_unexpected_file for result in results),
            "average_changed_lines": _average_changed_lines(results),
            "max_changed_lines": max(
                (result.patch_changed_lines or 0 for result in results),
                default=0,
            ),
        },
        "cases": [asdict(result) for result in results],
    }


def _advisor_metrics(results: list[CaseResult]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for result in results:
        shadow = result.advisor_shadow or {}
        prediction = shadow.get("prediction") if isinstance(shadow.get("prediction"), dict) else {}
        expected_source = _expected_source_from_result(result)
        predicted_source = prediction.get("source_file")
        predicted_bug_type = prediction.get("bug_type")
        confidence = _optional_float(prediction.get("confidence"))
        rows.append(
            {
                "case_id": result.case_id,
                "available": bool(shadow.get("available")),
                "expected_bug_type": result.bug_type,
                "predicted_bug_type": predicted_bug_type,
                "bug_type_correct": predicted_bug_type == result.bug_type,
                "expected_source_file": expected_source,
                "predicted_source_file": predicted_source,
                "source_file_correct": predicted_source == expected_source,
                "confidence": confidence,
            }
        )

    available = [row for row in rows if row["available"]]
    scored = available or rows
    confidences = [
        row["confidence"]
        for row in rows
        if isinstance(row.get("confidence"), float)
    ]
    return {
        "available_count": len(available),
        "cases": len(rows),
        "source_file_accuracy": _accuracy(scored, "source_file_correct"),
        "bug_type_accuracy": _accuracy(scored, "bug_type_correct"),
        "average_confidence": (
            round(sum(confidences) / len(confidences), 3)
            if confidences
            else 0.0
        ),
        "rows": rows,
    }


def _expected_source_from_result(result: CaseResult) -> str | None:
    return result.expected_source_file


def _accuracy(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(sum(bool(row.get(key)) for row in rows) / len(rows), 3)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value + ("\n" if value and not value.endswith("\n") else ""), encoding="utf-8")


def _trace_metrics(trace: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    history = [
        item
        for item in trace.get("tool_history", [])
        if isinstance(item, dict)
    ]
    actions = [
        str(item.get("action"))
        for item in history
        if item.get("action") is not None
    ]
    try:
        patch_index = actions.index("apply_patch")
    except ValueError:
        patch_index = -1

    validation = trace.get("validation") if isinstance(trace.get("validation"), dict) else {}
    confidence = (
        validation.get("confidence")
        if isinstance(validation.get("confidence"), dict)
        else {}
    )
    patch_scope = (
        validation.get("patch_scope")
        if isinstance(validation.get("patch_scope"), dict)
        else {}
    )
    syntax = validation.get("syntax") if isinstance(validation.get("syntax"), dict) else {}
    tests = validation.get("tests") if isinstance(validation.get("tests"), dict) else {}
    patch = trace.get("proposed_patch") if isinstance(trace.get("proposed_patch"), dict) else None
    changed_lines = _changed_line_count(patch_scope)
    expected = set(_expected_changed_files(metadata))
    patch_path = str(patch.get("path")) if patch and patch.get("path") else None
    patch_applied = any(
        item.get("action") == "apply_patch" and item.get("applied") is True
        for item in history
    )
    patch_touched_expected = patch_path in expected if patch_path else False
    patch_touched_unexpected = bool(patch_path and patch_path not in expected)
    tests_rerun = patch_index >= 0 and "run_tests" in actions[patch_index + 1 :]
    tests_passed_after_patch = patch_applied and tests.get("status") == "passed"
    return {
        "patch_applied": any(
            item.get("action") == "apply_patch" and item.get("applied") is True
            for item in history
        ),
        "tests_rerun_after_patch": tests_rerun,
        "loop_prevention_count": _loop_prevention_count(trace),
        "irrelevant_file_reads": _irrelevant_file_reads(trace, metadata),
        "unsafe_action_blocks": _unsafe_blocks(trace),
        "patch_quality": _patch_quality(
            patch=patch,
            patch_changed_lines=changed_lines,
            patch_touched_expected=patch_touched_expected,
            patch_touched_unexpected=patch_touched_unexpected,
            syntax_valid=syntax.get("valid"),
            tests_passed_after_patch=tests_passed_after_patch,
            patch_applied=patch_applied,
        ),
        "patch_touched_expected_file": patch_touched_expected,
        "patch_touched_unexpected_file": patch_touched_unexpected,
        "patch_changed_lines": changed_lines,
        "syntax_valid_after_patch": syntax.get("valid") if patch_applied else None,
        "tests_passed_after_patch": tests_passed_after_patch,
        "confidence_before_patch": confidence.get("score"),
        "confidence_after_patch": confidence.get("score") if patch_applied else None,
        "apply_decision": confidence.get("decision"),
        "fallback_reason": _fallback_reason(trace),
    }


def _loop_prevention_count(trace: dict[str, Any]) -> int:
    text = " ".join(
        str(value)
        for value in (
            trace.get("final_response"),
            trace.get("stop_reason"),
            *_tool_messages(trace),
        )
        if value
    ).lower()
    return int("repeated" in text or "already inspected" in text)


def _irrelevant_file_reads(trace: dict[str, Any], metadata: dict[str, Any]) -> int:
    expected = set(_expected_changed_files(metadata))
    allowed = set(expected)
    relevant_files = metadata.get("relevant_files")
    if isinstance(relevant_files, list):
        allowed.update(path for path in relevant_files if isinstance(path, str))
    expected_test = metadata.get("expected_test_file")
    if isinstance(expected_test, str):
        allowed.add(expected_test)
    for path in expected:
        path_obj = Path(path)
        allowed.add(path_obj.with_name(f"test_{path_obj.name}").as_posix())
        allowed.add(path_obj.with_name(path_obj.stem + "_test.py").as_posix())
    return sum(
        1
        for item in trace.get("tool_history", [])
        if isinstance(item, dict)
        and item.get("action") == "read_file"
        and isinstance(item.get("path"), str)
        and item["path"] not in allowed
        and not _is_test_file(item["path"])
    )


def _unsafe_blocks(trace: dict[str, Any]) -> int:
    return sum(
        1
        for item in trace.get("tool_history", [])
        if isinstance(item, dict)
        and item.get("allowed") is False
        and (
            item.get("policy_reason")
            or "blocked" in str(item.get("error", "")).lower()
        )
    )


def _is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py")


def _tool_messages(trace: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for item in trace.get("tool_history", []):
        if isinstance(item, dict):
            for key in ("message", "error", "policy_reason"):
                value = item.get(key)
                if isinstance(value, str):
                    messages.append(value)
    return messages


def _expected_changed_files(metadata: dict[str, Any]) -> list[str]:
    return [
        str(path)
        for path in metadata.get("expected_changed_files", [])
        if isinstance(path, str)
    ]


def _changed_line_count(patch_scope: dict[str, Any]) -> int | None:
    value = patch_scope.get("changed_line_budget")
    return int(value) if isinstance(value, int) else None


def _patch_quality(
    *,
    patch: dict[str, Any] | None,
    patch_changed_lines: int | None,
    patch_touched_expected: bool,
    patch_touched_unexpected: bool,
    syntax_valid: Any,
    tests_passed_after_patch: bool,
    patch_applied: bool,
) -> str:
    if not patch:
        return "invalid"
    if patch_touched_unexpected:
        return "irrelevant"
    if patch_changed_lines is not None and patch_changed_lines > 20:
        return "too_large"
    if syntax_valid is False:
        return "invalid"
    if tests_passed_after_patch:
        return "clean" if patch_touched_expected and (patch_changed_lines or 99) <= 5 else "probably_ok"
    if patch_applied:
        return "risky"
    if patch_touched_expected and (patch_changed_lines or 99) <= 5:
        return "probably_ok"
    return "risky"


def _fallback_reason(trace: dict[str, Any]) -> str | None:
    response = trace.get("final_response")
    if not isinstance(response, str):
        return None
    lowered = response.lower()
    if any(token in lowered for token in ("could not", "stopped", "blocked", "too low")):
        return response
    return None


def _quality_count(results: list[CaseResult], label: str) -> int:
    return sum(result.patch_quality == label for result in results)


def _average_changed_lines(results: list[CaseResult]) -> float:
    values = [
        result.patch_changed_lines
        for result in results
        if result.patch_changed_lines is not None
    ]
    return round(sum(values) / len(values), 2) if values else 0.0


def _average_confidence(results: list[CaseResult], phase: str) -> float:
    values = []
    for result in results:
        value = (
            result.confidence_before_patch
            if phase == "before"
            else result.confidence_after_patch
        )
        if value is not None:
            values.append(value)
    return round(sum(values) / len(values), 3) if values else 0.0


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (float, int)) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


if __name__ == "__main__":
    main()
