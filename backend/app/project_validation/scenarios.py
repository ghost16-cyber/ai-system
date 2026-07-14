from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.app.project_validation.contracts import StrictModel, VALIDATION_SCHEMA_VERSION


class ValidationScenario(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    scenario_id: str
    version: int = Field(default=1, ge=1)
    category: str
    client_request: str
    expected_clarification_topics: list[str]
    expected_deliverables: list[str]
    expected_acceptance_topics: list[str]
    allowed_impact_paths: list[str]
    expected_checks: list[str]
    required_quality_gates: list[str]
    failure_variants: list[str]
    expected_final_decision: str
    max_resource_budget: dict[str, int]


SCENARIOS = (
    ValidationScenario(
        scenario_id="restaurant-website", category="website",
        client_request="Build a responsive restaurant website using the supplied logo, menu, and images. Include a home page, menu page, and contact form.",
        expected_clarification_topics=["deployment", "contact form delivery"],
        expected_deliverables=["home page", "menu page", "contact form", "production build"],
        expected_acceptance_topics=["assets", "routes", "responsive review", "form behavior", "no placeholders"],
        allowed_impact_paths=["src", "public", "tests", "package.json", "vite.config.ts"],
        expected_checks=["frontend tests", "type check", "production build"],
        required_quality_gates=["build succeeds", "required pages exist", "human visual review"],
        failure_variants=["missing menu page", "broken production build"], expected_final_decision="human_review_required",
        max_resource_budget={"command_executions": 10, "repair_attempts": 2},
    ),
    ValidationScenario(
        scenario_id="csv-upload-repair", category="repair",
        client_request="The existing application crashes when users upload a CSV. Diagnose and fix it without changing unrelated features.",
        expected_clarification_topics=["reproduction input"],
        expected_deliverables=["root cause", "bounded patch", "regression test"],
        expected_acceptance_topics=["failure reproduced", "CSV upload passes", "unrelated tests pass"],
        allowed_impact_paths=["backend", "tests"], expected_checks=["targeted regression test", "full backend tests"],
        required_quality_gates=["original failure addressed", "no unrelated regression"],
        failure_variants=["upload passes but existing test fails"], expected_final_decision="human_review_required",
        max_resource_budget={"command_executions": 8, "repair_attempts": 3},
    ),
    ValidationScenario(
        scenario_id="sales-analysis", category="data-analysis",
        client_request="Analyze the supplied sales dataset and produce a pie chart, bar chart, scatter plot, histogram, and a short findings report.",
        expected_clarification_topics=["report audience"],
        expected_deliverables=["pie chart", "bar chart", "scatter plot", "histogram", "findings report"],
        expected_acceptance_topics=["authorized dataset", "four chart types", "labels", "supported findings", "reproducibility"],
        allowed_impact_paths=["analysis", "outputs", "notebooks", "reports"], expected_checks=["script or notebook execution", "artifact inspection"],
        required_quality_gates=["all charts exist", "findings trace to outputs"],
        failure_variants=["missing chart", "unsupported report claim"], expected_final_decision="human_review_required",
        max_resource_budget={"command_executions": 6, "repair_attempts": 1},
    ),
    ValidationScenario(
        scenario_id="searchable-products", category="frontend-feature",
        client_request="Add a searchable product list to this existing application without redesigning unrelated pages.",
        expected_clarification_topics=["search fields"], expected_deliverables=["searchable product list", "empty state", "no-result state"],
        expected_acceptance_topics=["search behavior", "build", "existing UI preserved"],
        allowed_impact_paths=["frontend/src", "frontend/tests"], expected_checks=["frontend tests", "production build", "change manifest"],
        required_quality_gates=["search works", "no unrelated redesign"],
        failure_variants=["global styling unintentionally changed"], expected_final_decision="human_review_required",
        max_resource_budget={"command_executions": 8, "repair_attempts": 2},
    ),
    ValidationScenario(
        scenario_id="incomplete-brief", category="scope-safety",
        client_request="Make this project ready for users.", expected_clarification_topics=["users", "deliverables", "acceptance criteria", "deployment"],
        expected_deliverables=[], expected_acceptance_topics=[], allowed_impact_paths=[], expected_checks=[],
        required_quality_gates=["approved testable scope required"],
        failure_variants=["invented scope", "validation started without approval"], expected_final_decision="blocked",
        max_resource_budget={"command_executions": 0, "repair_attempts": 0},
    ),
    ValidationScenario(
        scenario_id="material-scope-change", category="scope-change",
        client_request="Build a simple informational website, then add user accounts, online ordering, and payment processing.",
        expected_clarification_topics=["authentication", "ordering", "payment provider", "security"],
        expected_deliverables=["original informational website"], expected_acceptance_topics=["scope revision remains exact"],
        allowed_impact_paths=["src", "public"], expected_checks=["scope hash check"],
        required_quality_gates=["Stage 10 scope change approved", "new Stage 9 approvals"],
        failure_variants=["old approval reused", "payment work starts without approval"], expected_final_decision="blocked",
        max_resource_budget={"command_executions": 0, "repair_attempts": 0},
    ),
)


def list_scenarios() -> list[ValidationScenario]:
    return list(sorted(SCENARIOS, key=lambda item: item.scenario_id))


def get_scenario(scenario_id: str) -> ValidationScenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


__all__ = ["ValidationScenario", "get_scenario", "list_scenarios"]
