from __future__ import annotations

import ast
import re

from backend.app.schemas.api import FixValidationResponse, IssueResponse

from .static_analyzer import StaticAnalysisResult, analyze_python_code


def add_validated_fixes(code: str, result: StaticAnalysisResult) -> StaticAnalysisResult:
    """Attach validated replacements for findings with safe deterministic fixes."""
    if not result.parse_success:
        return result

    tree = ast.parse(code)

    fixable_none_comparisons = {
        (node.lineno, node.col_offset): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and _is_simple_none_comparison(node)
    }

    issues: list[IssueResponse] = []

    for issue in result.issues:
        suggested_code = None

        if issue.rule_id == "bad_none_comparison":
            node = fixable_none_comparisons.get((issue.line, issue.column))
            if node is not None:
                suggested_code = _replace_none_comparison(code, node)

        elif issue.rule_id == "redundant_boolean_comparison":
            suggested_code = _fix_redundant_boolean_comparison(code, issue)

        if suggested_code is None:
            issues.append(issue)
            continue

        validation = _validate_replacement(code, suggested_code, issue.rule_id)

        if validation.status != "passed":
            issues.append(issue)
            continue

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
        and (_is_none(node.left) or _is_none(node.comparators[0]))
    )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _replace_none_comparison(code: str, node: ast.Compare) -> str:
    replacement = ast.Compare(
        left=node.left,
        ops=[ast.Is() if isinstance(node.ops[0], ast.Eq) else ast.IsNot()],
        comparators=node.comparators,
    )
    replacement_text = ast.unparse(replacement)
    start = _source_offset(code, node.lineno, node.col_offset)
    end = _source_offset(code, node.end_lineno, node.end_col_offset)
    return f"{code[:start]}{replacement_text}{code[end:]}"


def _fix_redundant_boolean_comparison(code: str, finding: IssueResponse) -> str | None:
    """
    Convert simple boolean comparisons:

        if flag == True:
        if True == flag:
        if flag != False:
        if False != flag:

    into:

        if flag:

    And:

        if flag == False:
        if False == flag:
        if flag != True:
        if True != flag:

    into:

        if not flag:

    This intentionally handles only simple variable-name comparisons.
    """
    line = finding.line

    if line is None:
        return None

    lines = code.splitlines(keepends=True)

    if line < 1 or line > len(lines):
        return None

    original_line = lines[line - 1]

    replacements = [
        (r"\b(?P<name>[A-Za-z_]\w*)\s*==\s*True\b", r"\g<name>"),
        (r"\bTrue\s*==\s*(?P<name>[A-Za-z_]\w*)\b", r"\g<name>"),
        (r"\b(?P<name>[A-Za-z_]\w*)\s*!=\s*False\b", r"\g<name>"),
        (r"\bFalse\s*!=\s*(?P<name>[A-Za-z_]\w*)\b", r"\g<name>"),
        (r"\b(?P<name>[A-Za-z_]\w*)\s*==\s*False\b", r"not \g<name>"),
        (r"\bFalse\s*==\s*(?P<name>[A-Za-z_]\w*)\b", r"not \g<name>"),
        (r"\b(?P<name>[A-Za-z_]\w*)\s*!=\s*True\b", r"not \g<name>"),
        (r"\bTrue\s*!=\s*(?P<name>[A-Za-z_]\w*)\b", r"not \g<name>"),
    ]

    fixed_line = original_line

    for pattern, replacement in replacements:
        fixed_line = re.sub(pattern, replacement, fixed_line)

    if fixed_line == original_line:
        return None

    fixed_lines = lines.copy()
    fixed_lines[line - 1] = fixed_line

    return "".join(fixed_lines)


def _source_offset(code: str, line_number: int, byte_column: int) -> int:
    lines = code.splitlines(keepends=True)
    current_line = lines[line_number - 1]
    character_column = len(
        current_line.encode("utf-8")[:byte_column].decode("utf-8")
    )
    return sum(len(line) for line in lines[: line_number - 1]) + character_column


def _validate_replacement(
    original_code: str,
    suggested_code: str,
    target_rule_id: str,
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

    before_count = _count_rule(before.issues, target_rule_id)
    after_count = _count_rule(after.issues, target_rule_id)

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