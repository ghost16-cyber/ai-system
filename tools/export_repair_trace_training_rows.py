from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_TRACE_DIR = "benchmarks/.runs/traces"
DEFAULT_OUTPUT = "benchmarks/.runs/repair_trace_dataset_latest.jsonl"
DEFAULT_REPORT = "benchmarks/.runs/repair_trace_dataset_report_latest.json"

VALID_ACTIONS = {
    "search_files",
    "read_file",
    "analyze_ast",
    "run_tests",
    "validate_syntax",
    "propose_patch",
    "apply_patch",
    "rollback_patch",
    "final_response",
}

VALID_IDEAL_ACTIONS = (VALID_ACTIONS - {"apply_patch"}) | {
    "inspect_imports",
    "switch_target_file",
    "stop_repeated_reads",
}

VALID_FAILURE_MODES = {
    "none",
    "missing_dependency",
    "stale_read_loop",
    "policy_block",
    "no_patch_proposed",
    "tests_failed_after_patch",
    "max_steps_reached",
    "unknown_failure",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export repair trace events into supervised training rows."
    )
    parser.add_argument("--trace-dir", default=DEFAULT_TRACE_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir).resolve()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()

    events_by_trace = load_trace_events(trace_dir)
    rows = build_rows(events_by_trace)
    write_jsonl(rows, output)
    report = build_report(trace_dir, output, rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nRepair trace dataset written to: {output}")
    print(f"Repair trace dataset report written to: {report_path}")
    return 0 if report["passed"] else 1


def load_trace_events(trace_dir: Path) -> dict[str, list[dict[str, Any]]]:
    if not trace_dir.exists():
        raise FileNotFoundError(f"Trace directory not found: {trace_dir}")

    traces: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(trace_dir.glob("trace_*.jsonl")):
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            item = json.loads(stripped)
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object.")
            events.append(item)
        if events:
            traces[path.name] = sorted(
                events,
                key=lambda event: int(event.get("step_index", 0) or 0),
            )
    return traces


def build_rows(events_by_trace: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trace_name, events in sorted(events_by_trace.items()):
        partial_actions: list[str] = []
        files_read: list[str] = []
        repeated_reads: Counter[str] = Counter()

        for event in events:
            action = str(event.get("next_action") or "")
            row = build_row(
                trace_name=trace_name,
                event=event,
                partial_actions=list(partial_actions),
                files_read=list(files_read),
                repeated_reads=repeated_reads.copy(),
            )
            rows.append(row)

            partial_actions.append(action)
            if action == "read_file":
                for path in event.get("files_touched") or []:
                    if isinstance(path, str):
                        repeated_reads[path] += 1
                        if path not in files_read:
                            files_read.append(path)

    rows.sort(key=lambda row: (row["split"], row["case_id"], row["source_trace"], row["step_index"]))
    return rows


def build_row(
    *,
    trace_name: str,
    event: dict[str, Any],
    partial_actions: list[str],
    files_read: list[str],
    repeated_reads: Counter[str],
) -> dict[str, Any]:
    case_id = str(event.get("case_id") or event.get("task_id") or "unknown")
    advisor_signal = event.get("advisor_signal") if isinstance(event.get("advisor_signal"), dict) else {}
    advisor_next = advisor_signal.get("next_action") if isinstance(advisor_signal, dict) else None
    advisor_next_action = (
        str(advisor_next.get("next_action"))
        if isinstance(advisor_next, dict) and advisor_next.get("next_action")
        else ""
    )
    actual_next_action = str(event.get("next_action") or "")
    failure_mode = normalize_failure_mode(event.get("failure_mode"))
    ideal_next_action = weak_ideal_next_action(
        event=event,
        partial_actions=partial_actions,
        repeated_reads=repeated_reads,
        advisor_next_action=advisor_next_action,
        actual_next_action=actual_next_action,
    )
    intervention_needed = ideal_next_action != actual_next_action or failure_mode not in {"none", ""}

    return {
        "case_id": case_id,
        "step_index": int(event.get("step_index", len(partial_actions) + 1) or 0),
        "timestamp": str(event.get("timestamp") or ""),
        "advisor_runtime_mode": str(event.get("runtime_mode") or "off"),
        "partial_actions": partial_actions,
        "files_read": files_read,
        "test_status": str(event.get("test_status") or ""),
        "error_category": str(event.get("error_category") or ""),
        "advisor_next_action": advisor_next_action,
        "actual_next_action": actual_next_action,
        "ideal_next_action": ideal_next_action,
        "final_status": str(event.get("final_status") or ""),
        "failure_mode": failure_mode,
        "intervention_needed": intervention_needed,
        "split": stable_split(case_id),
        "source_trace": trace_name,
        "label_source": "weak_rules_v1",
    }


def weak_ideal_next_action(
    *,
    event: dict[str, Any],
    partial_actions: list[str],
    repeated_reads: Counter[str],
    advisor_next_action: str,
    actual_next_action: str,
) -> str:
    error_category = str(event.get("error_category") or "")
    test_status = str(event.get("test_status") or "")
    files_touched = [
        str(path)
        for path in event.get("files_touched") or []
        if isinstance(path, str)
    ]

    if actual_next_action == "apply_patch":
        return "run_tests"

    if error_category == "import_error":
        return "inspect_imports"

    if actual_next_action == "read_file":
        for path in files_touched:
            if repeated_reads[path] >= 2:
                return "stop_repeated_reads"
            if repeated_reads[path] >= 1 and not test_status:
                return "run_tests"

    if partial_actions.count("read_file") >= 3 and "run_tests" not in partial_actions:
        return "run_tests"

    if advisor_next_action in VALID_IDEAL_ACTIONS:
        return advisor_next_action

    return actual_next_action if actual_next_action in VALID_IDEAL_ACTIONS else "run_tests"


def normalize_failure_mode(value: Any) -> str:
    if value in {None, ""}:
        return "none"
    text = str(value)
    return text if text in VALID_FAILURE_MODES else "unknown_failure"


def stable_split(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 5
    return "test" if bucket == 0 else "train"


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def build_report(trace_dir: Path, output: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_counts = Counter(str(row["split"]) for row in rows)
    action_counts = Counter(str(row["ideal_next_action"]) for row in rows)
    failure_counts = Counter(str(row["failure_mode"]) for row in rows)
    return {
        "phase": "phase9_repair_trace_dataset_export",
        "passed": bool(rows),
        "trace_dir": str(trace_dir),
        "output": str(output),
        "row_count": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "ideal_next_action_distribution": dict(sorted(action_counts.items())),
        "failure_mode_distribution": dict(sorted(failure_counts.items())),
        "label_source": "weak_rules_v1",
    }


if __name__ == "__main__":
    raise SystemExit(main())
