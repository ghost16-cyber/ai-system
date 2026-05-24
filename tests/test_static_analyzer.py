from backend.app.analyzer import add_validated_fixes, analyze_python_code


def test_clean_python_has_no_static_findings():
    result = analyze_python_code(
        "def add(left, right):\n"
        "    return left + right\n"
    )

    assert result.parse_success is True
    assert result.issues == []


def test_ast_rules_detect_high_confidence_patterns():
    result = analyze_python_code(
        "def risky(items=[]):\n"
        "    try:\n"
        "        value = eval('input')\n"
        "        exec('value = 1')\n"
        "        if value == None:\n"
        "            return value == True\n"
        "    except:\n"
        "        return items\n"
    )

    assert result.parse_success is True
    assert {issue.rule_id for issue in result.issues} == {
        "mutable_default_argument",
        "dangerous_eval",
        "dangerous_exec",
        "bad_none_comparison",
        "redundant_boolean_comparison",
        "bare_except",
    }
    assert all(issue.source == "static_rule" for issue in result.issues)


def test_method_named_eval_is_not_treated_as_builtin_eval():
    result = analyze_python_code("parser.eval(value)\n")

    assert result.issues == []


def test_syntax_error_stops_further_rule_analysis():
    result = analyze_python_code("def broken(:\n    eval('input')\n")

    assert result.parse_success is False
    assert [issue.rule_id for issue in result.issues] == ["syntax_error"]


def test_none_comparison_receives_a_validated_deterministic_fix():
    code = "if value != None:\n    print(value)\n"
    result = add_validated_fixes(code, analyze_python_code(code))

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.rule_id == "bad_none_comparison"
    assert issue.suggested_code == "if value is not None:\n    print(value)\n"
    assert issue.validation.status == "passed"
    assert issue.validation.checks == [
        "ast_parse_passed",
        "target_finding_removed",
        "no_new_high_risk_finding",
    ]


def test_non_fixable_findings_remain_guidance_only():
    code = "value = eval(user_input)\n"
    result = add_validated_fixes(code, analyze_python_code(code))

    issue = result.issues[0]
    assert issue.rule_id == "dangerous_eval"
    assert issue.suggested_code is None
    assert issue.validation.status == "not_available"


def test_chained_none_comparison_is_not_auto_rewritten():
    code = "if lower == None == upper:\n    pass\n"
    result = add_validated_fixes(code, analyze_python_code(code))

    assert any(issue.rule_id == "bad_none_comparison" for issue in result.issues)
    assert all(issue.suggested_code is None for issue in result.issues)
