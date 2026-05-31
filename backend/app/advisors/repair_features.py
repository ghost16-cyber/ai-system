from __future__ import annotations

from .repair_labels import RepairAdvisorInput

def normalize_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def build_repair_feature_text(input_data: RepairAdvisorInput) -> str:
    """
    Convert repair context into one compact text feature string.

    This is intentionally simple for Phase 3C:
    TF-IDF models work well with explicit path/function/assertion tokens.
    """
    parts: list[str] = []

    parts.append(f"goal: {input_data.goal}")

    if input_data.failing_test_file:
        parts.append(f"failing_test_file: {input_data.failing_test_file}")

    if input_data.failing_test_name:
        parts.append(f"failing_test_name: {input_data.failing_test_name}")

    if input_data.assertion_summary:
        parts.append(f"assertion: {input_data.assertion_summary}")

    imported_modules = normalize_list(input_data.imported_modules)
    if imported_modules:
        parts.append("imports: " + " ".join(imported_modules))

    candidate_files = normalize_list(input_data.candidate_files)
    if candidate_files:
        parts.append("candidate_files: " + " ".join(candidate_files))

    inspected_files = normalize_list(input_data.inspected_files)
    if inspected_files:
        parts.append("inspected_files: " + " ".join(inspected_files))

    tool_actions = normalize_list(input_data.tool_actions)
    if tool_actions:
        parts.append("tool_actions: " + " ".join(tool_actions))

    return "\n".join(parts)
