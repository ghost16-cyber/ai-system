from __future__ import annotations

from typing import Any

from .repair_advisor import RepairAdvisor
from .repair_labels import RepairAdvisorInput


def run_shadow_repair_advisor(
    *,
    goal: str,
    failing_test_file: str | None = None,
    failing_test_name: str | None = None,
    assertion_summary: str | None = None,
    imported_modules: list[str] | None = None,
    candidate_files: list[str] | None = None,
    inspected_files: list[str] | None = None,
    tool_actions: list[str] | None = None,
) -> dict[str, Any]:
    """
    Run advisor in shadow mode.

    This must never mutate files, choose tools, or override the orchestrator.
    It only returns prediction metadata for traces/reports.
    """
    advisor = RepairAdvisor()

    input_data = RepairAdvisorInput(
        goal=goal,
        failing_test_file=failing_test_file,
        failing_test_name=failing_test_name,
        assertion_summary=assertion_summary,
        imported_modules=imported_modules or [],
        candidate_files=candidate_files or [],
        inspected_files=inspected_files or [],
        tool_actions=tool_actions or [],
    )

    prediction = advisor.predict(input_data)

    return {
        "enabled": True,
        "available": advisor.available,
        "prediction": prediction.to_dict(),
    }
