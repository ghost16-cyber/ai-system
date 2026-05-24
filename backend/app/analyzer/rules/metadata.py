from __future__ import annotations

from backend.app.schemas.api import RuleMetadataResponse


RULE_METADATA: tuple[RuleMetadataResponse, ...] = (
    RuleMetadataResponse(
        rule_id="syntax_error",
        category="correctness",
        severity="high",
        description="Python source cannot be parsed due to invalid syntax.",
        suggestion="Fix the syntax error before running further analysis.",
    ),
    RuleMetadataResponse(
        rule_id="bare_except",
        category="reliability",
        severity="medium",
        description="Bare `except` catches every exception, including unexpected failures.",
        suggestion="Catch the specific exception types that can be handled safely.",
    ),
    RuleMetadataResponse(
        rule_id="dangerous_eval",
        category="security",
        severity="high",
        description="`eval()` can run arbitrary Python code.",
        suggestion="Avoid dynamic code execution; use a parser or explicit logic instead.",
    ),
    RuleMetadataResponse(
        rule_id="dangerous_exec",
        category="security",
        severity="high",
        description="`exec()` can run arbitrary Python code.",
        suggestion="Avoid dynamic code execution; use a parser or explicit logic instead.",
    ),
    RuleMetadataResponse(
        rule_id="mutable_default_argument",
        category="correctness",
        severity="medium",
        description="Function argument has a mutable default value.",
        suggestion="Use `None` as the default and create the collection inside the function.",
    ),
    RuleMetadataResponse(
        rule_id="bad_none_comparison",
        category="style",
        severity="low",
        description="Equality comparison with `None` is less idiomatic than identity comparison.",
        suggestion="Use `is None` or `is not None`.",
        fix_available=True,
    ),
    RuleMetadataResponse(
        rule_id="redundant_boolean_comparison",
        category="style",
        severity="low",
        description="Explicit comparison with a boolean literal is usually unnecessary.",
        suggestion="Use the boolean expression directly, adding `not` when needed.",
        fix_available=True,
    ),
    RuleMetadataResponse(
        rule_id="missing_docstring",
        category="maintainability",
        severity="low",
        description="Public top-level definition is missing a docstring.",
        suggestion="Add a concise docstring describing the public definition's purpose.",
    ),
    RuleMetadataResponse(
        rule_id="unused_import",
        category="maintainability",
        severity="low",
        description="A module-level imported name is not used in the module.",
        suggestion="Remove the unused import if it is not needed.",
    ),
    RuleMetadataResponse(
        rule_id="inefficient_loop",
        category="performance",
        severity="low",
        description="Loop uses an index only to retrieve each item from a collection.",
        suggestion="Iterate over the collection items directly instead of using `range(len(...))`.",
    ),
)


def get_rule_metadata() -> list[RuleMetadataResponse]:
    return [metadata.model_copy() for metadata in RULE_METADATA]
