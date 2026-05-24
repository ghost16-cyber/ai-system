from __future__ import annotations

from backend.app.analyzer.rules.base import StaticRule
from backend.app.analyzer.rules.correctness import MutableDefaultArgumentRule
from backend.app.analyzer.rules.maintainability import MissingDocstringRule, UnusedImportRule
from backend.app.analyzer.rules.performance import InefficientLoopRule
from backend.app.analyzer.rules.reliability import BareExceptRule
from backend.app.analyzer.rules.security import DangerousDynamicExecutionRule
from backend.app.analyzer.rules.style import ComparisonStyleRule


ACTIVE_RULES: tuple[type[StaticRule], ...] = (
    BareExceptRule,
    DangerousDynamicExecutionRule,
    MutableDefaultArgumentRule,
    ComparisonStyleRule,
    MissingDocstringRule,
    UnusedImportRule,
    InefficientLoopRule,
)


def get_active_rules() -> list[StaticRule]:
    """
    Return fresh rule instances for one analysis run.

    Fresh instances prevent issues from one request leaking into another.
    """
    return [rule_class() for rule_class in ACTIVE_RULES]


def get_registered_rule_ids() -> set[str]:
    rule_ids: set[str] = set()

    for rule_class in ACTIVE_RULES:
        rule_ids.update(rule_class.rule_ids)

    return rule_ids
