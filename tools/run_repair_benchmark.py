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


DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"
DEFAULT_RUNS_DIR = ROOT / "benchmarks" / ".runs"


@dataclass
class CaseResult:
    case_id: str
    bug_type: str
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
    duration_seconds: float
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
        metrics = _trace_metrics(trace, metadata)
        return CaseResult(
            case_id=case_id,
            bug_type=str(metadata.get("bug_type", "unknown")),
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
            duration_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as error:
        final, final_output = run_pytest(work_dir)
        write_text(work_dir / "final_pytest.txt", final_output)
        return CaseResult(
            case_id=case_id,
            bug_type=str(metadata.get("bug_type", "unknown")),
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
            duration_seconds=round(time.monotonic() - started, 3),
            error=f"{type(error).__name__}: {error}",
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
        },
        "cases": [asdict(result) for result in results],
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value + ("\n" if value and not value.endswith("\n") else ""), encoding="utf-8")


def _trace_metrics(trace: dict[str, Any], metadata: dict[str, Any]) -> dict[str, int | bool]:
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

    return {
        "patch_applied": any(
            item.get("action") == "apply_patch" and item.get("applied") is True
            for item in history
        ),
        "tests_rerun_after_patch": patch_index >= 0
        and "run_tests" in actions[patch_index + 1 :],
        "loop_prevention_count": _loop_prevention_count(trace),
        "irrelevant_file_reads": _irrelevant_file_reads(trace, metadata),
        "unsafe_action_blocks": _unsafe_blocks(trace),
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
    expected = {
        str(path)
        for path in metadata.get("expected_changed_files", [])
        if isinstance(path, str)
    }
    allowed = set(expected)
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


if __name__ == "__main__":
    main()
