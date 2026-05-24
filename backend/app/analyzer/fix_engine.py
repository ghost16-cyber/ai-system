from __future__ import annotations

import ast

from backend.app.schemas.api import FixValidationResponse, IssueResponse

from .static_analyzer import StaticAnalysisResult, analyze_python_code


def add_validated_fixes(code: str, result: StaticAnalysisResult) -> StaticAnalysisResult:
    """Attach validated replacements for findings with safe deterministic fixes."""
    if not result.parse_success:
        return result

    tree = ast.parse(code)
    fixable_comparisons = {
        (node.lineno, node.col_offset): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and _is_simple_none_comparison(node)
    }
    issues: list[IssueResponse] = []

    for issue in result.issues:
        if issue.rule_id != "bad_none_comparison":
            issues.append(issue)
            continue

        node = fixable_comparisons.get((issue.line, issue.column))
        if node is None:
            issues.append(issue)
            continue

        suggested_code = _replace_comparison(code, node)
        validation = _validate_replacement(code, suggested_code)
        issues.append(
            issue.model_copy(
                update={
                    "suggested_code": suggested_code,
                    "validation": validation,
                }
            )
        )

    return StaticAnalysisResult(parse_success=result.parse_success, issues=issues)


def _is_simple_none_comparison(node: ast.Compare) -> bool:
    return (
        len(node.ops) == 1
        and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
        and (
            _is_none(node.left)
            or _is_none(node.comparators[0])
        )
    )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _replace_comparison(code: str, node: ast.Compare) -> str:
    replacement = ast.Compare(
        left=node.left,
        ops=[ast.Is() if isinstance(node.ops[0], ast.Eq) else ast.IsNot()],
        comparators=node.comparators,
    )
    replacement_text = ast.unparse(replacement)
    start = _source_offset(code, node.lineno, node.col_offset)
    end = _source_offset(code, node.end_lineno, node.end_col_offset)
    return f"{code[:start]}{replacement_text}{code[end:]}"


def _source_offset(code: str, line_number: int, byte_column: int) -> int:
    lines = code.splitlines(keepends=True)
    current_line = lines[line_number - 1]
    character_column = len(
        current_line.encode("utf-8")[:byte_column].decode("utf-8")
    )
    return sum(len(line) for line in lines[: line_number - 1]) + character_column


def _validate_replacement(
    original_code: str, suggested_code: str
) -> FixValidationResponse:
    try:
        ast.parse(suggested_code)
    except SyntaxError:
        return FixValidationResponse(
            status="failed",
            checks=["ast_parse_failed"],
            message="The suggested replacement did not parse as valid Python.",
        )

    before = analyze_python_code(original_code)
    after = analyze_python_code(suggested_code)
    before_count = _count_rule(before.issues, "bad_none_comparison")
    after_count = _count_rule(after.issues, "bad_none_comparison")

    if after_count != before_count - 1:
        return FixValidationResponse(
            status="failed",
            checks=["ast_parse_passed", "target_finding_not_removed"],
            message="The suggested replacement did not remove exactly one target finding.",
        )

    new_high_risk_rules = {
        issue.rule_id
        for issue in after.issues
        if issue.severity in {"high", "medium"}
    } - {
        issue.rule_id
        for issue in before.issues
        if issue.severity in {"high", "medium"}
    }
    if new_high_risk_rules:
        return FixValidationResponse(
            status="failed",
            checks=["ast_parse_passed", "new_high_risk_finding_detected"],
            message="The suggested replacement introduced a new higher-risk finding.",
        )

    return FixValidationResponse(
        status="passed",
        checks=["ast_parse_passed", "target_finding_removed", "no_new_high_risk_finding"],
        message="The deterministic replacement parses and removes this finding.",
    )


def _count_rule(issues: list[IssueResponse], rule_id: str) -> int:
    return sum(issue.rule_id == rule_id for issue in issues)
