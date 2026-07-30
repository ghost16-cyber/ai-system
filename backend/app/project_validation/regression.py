from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.project_validation.contracts import RegressionResult


def evaluate_regression(
    *, run_id: str, snapshot_diff: dict, allowed_paths: list[str] | None = None, regressed_tests: list[str] | None = None,
) -> RegressionResult:
    allowed = tuple(path.rstrip("/") for path in (allowed_paths or []))
    changed = list(snapshot_diff.get("modified", []))
    created = list(snapshot_diff.get("created", []))
    deleted = list(snapshot_diff.get("deleted", []))
    all_changes = changed + created + deleted
    unexpected = [] if not allowed else [path for path in all_changes if not any(path == prefix or path.startswith(prefix + "/") for prefix in allowed)]
    test_failures = list(regressed_tests or [])
    blocking = bool(deleted or unexpected or test_failures)
    parts: list[str] = []
    if test_failures:
        parts.append(f"{len(test_failures)} previously passing test(s) regressed")
    if deleted:
        parts.append(f"{len(deleted)} file(s) were deleted")
    if unexpected:
        parts.append(f"{len(unexpected)} change(s) were outside the approved impact boundary")
    summary = "; ".join(parts) if parts else "No blocking regression was detected from the available baseline evidence."
    return RegressionResult(
        regression_id=f"regression-{uuid4().hex}", run_id=run_id,
        changed_files=changed, created_files=created, deleted_files=deleted,
        unexpected_changes=unexpected, tests_regressed=test_failures,
        blocking=blocking, summary=summary, evaluated_at=datetime.now(timezone.utc),
    )


__all__ = ["evaluate_regression"]
