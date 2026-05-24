import ast

from backend.app.analyzer.rules.registry import (
    get_active_rules,
    get_registered_rule_ids,
)


def test_rule_registry_contains_release_5_rules():
    assert get_registered_rule_ids() == {
        "bare_except",
        "dangerous_eval",
        "dangerous_exec",
        "mutable_default_argument",
        "bad_none_comparison",
        "redundant_boolean_comparison",
    }


def test_rule_registry_returns_fresh_rule_instances():
    first = get_active_rules()
    second = get_active_rules()

    assert first
    assert second
    assert first is not second
    assert all(left is not right for left, right in zip(first, second))


def test_registered_rules_can_run_against_ast_tree():
    tree = ast.parse(
        "def risky(items=[]):\n"
        "    try:\n"
        "        if eval('1') == None:\n"
        "            return True\n"
        "    except:\n"
        "        return items\n"
    )

    issues = []

    for rule in get_active_rules():
        issues.extend(rule.check(tree))

    rule_ids = {issue.rule_id for issue in issues}

    assert {
        "mutable_default_argument",
        "dangerous_eval",
        "bad_none_comparison",
        "bare_except",
    } <= rule_ids