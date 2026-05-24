from __future__ import annotations

import ast
from dataclasses import dataclass

from backend.app.analyzer.rules.registry import get_active_rules
from backend.app.schemas.api import IssueResponse


@dataclass(frozen=True)
class StaticAnalysisResult:
    parse_success: bool
    issues: list[IssueResponse]


def _syntax_error_issue(error: SyntaxError) -> IssueResponse:
    return IssueResponse(
        rule_id="syntax_error",
        category="correctness",
        severity="high",
        message=error.msg,
        suggestion="Fix the syntax error before running further analysis.",
        line=error.lineno,
        column=error.offset,
    )


def _sort_issues(issues: list[IssueResponse]) -> list[IssueResponse]:
    return sorted(
        issues,
        key=lambda finding: (
            finding.line or 0,
            finding.column or 0,
            finding.rule_id,
        ),
    )


def analyze_python_code(code: str) -> StaticAnalysisResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return StaticAnalysisResult(
            parse_success=False,
            issues=[_syntax_error_issue(error)],
        )

    issues: list[IssueResponse] = []

    for rule in get_active_rules():
        issues.extend(rule.check(tree))

    return StaticAnalysisResult(
        parse_success=True,
        issues=_sort_issues(issues),
    )