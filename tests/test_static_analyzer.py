from backend.app.analyzer import add_validated_fixes, analyze_python_code


def test_clean_python_has_no_static_findings():
    result = analyze_python_code(
        "def add(left, right):\n"
        "    \"\"\"Add two values.\"\"\"\n"
        "    return left + right\n"
    )

    assert result.parse_success is True
    assert result.issues == []


def test_ast_rules_detect_high_confidence_patterns():
    result = analyze_python_code(
        "def risky(items=[]):\n"
        "    \"\"\"Return a risky sample value.\"\"\"\n"
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


def test_missing_docstring_flags_public_top_level_definitions():
    result = analyze_python_code(
        "def parse_value():\n"
        "    return 1\n"
        "\n"
        "async def fetch_value():\n"
        "    return 1\n"
        "\n"
        "class PublicService:\n"
        "    pass\n"
    )

    issues = [
        issue for issue in result.issues if issue.rule_id == "missing_docstring"
    ]

    assert [issue.line for issue in issues] == [1, 4, 7]
    assert all(issue.category == "maintainability" for issue in issues)
    assert all(issue.severity == "low" for issue in issues)
    assert all(issue.suggested_code is None for issue in issues)


def test_missing_docstring_ignores_private_documented_and_nested_definitions():
    result = analyze_python_code(
        "def _private_helper():\n"
        "    pass\n"
        "\n"
        "def documented():\n"
        "    \"\"\"Describe the public function.\"\"\"\n"
        "    def nested_without_docstring():\n"
        "        pass\n"
        "    return nested_without_docstring\n"
        "\n"
        "class DocumentedService:\n"
        "    \"\"\"Describe the public class.\"\"\"\n"
        "    def method_without_docstring(self):\n"
        "        pass\n"
    )

    assert not any(
        issue.rule_id == "missing_docstring" for issue in result.issues
    )


def test_unused_import_flags_unreferenced_module_level_bindings():
    result = analyze_python_code(
        "import os\n"
        "import json as codec\n"
        "from pathlib import Path\n"
        "\n"
        "Path('demo.py')\n"
    )

    issues = [issue for issue in result.issues if issue.rule_id == "unused_import"]

    assert [issue.line for issue in issues] == [1, 2]
    assert [issue.message for issue in issues] == [
        "Imported name `os` is not used in this module.",
        "Imported name `codec` is not used in this module.",
    ]
    assert all(issue.category == "maintainability" for issue in issues)
    assert all(issue.suggested_code is None for issue in issues)


def test_unused_import_ignores_used_exports_wildcards_future_and_local_imports():
    result = analyze_python_code(
        "from __future__ import annotations\n"
        "from helpers import *\n"
        "from service import Handler\n"
        "import os\n"
        "\n"
        "__all__ = ['Handler']\n"
        "print(os.path)\n"
        "\n"
        "def documented():\n"
        "    \"\"\"Load an optional implementation.\"\"\"\n"
        "    import optional_dependency\n"
        "    return optional_dependency\n"
    )

    assert not any(issue.rule_id == "unused_import" for issue in result.issues)


def test_inefficient_loop_flags_direct_read_iteration_by_index():
    result = analyze_python_code(
        "for index in range(len(items)):\n"
        "    print(items[index])\n"
    )

    issues = [issue for issue in result.issues if issue.rule_id == "inefficient_loop"]

    assert len(issues) == 1
    assert issues[0].line == 1
    assert issues[0].category == "performance"
    assert issues[0].severity == "low"
    assert issues[0].suggested_code is None


def test_inefficient_loop_ignores_index_use_mutation_and_unused_index():
    result = analyze_python_code(
        "for index in range(len(items)):\n"
        "    print(index, items[index])\n"
        "\n"
        "for index in range(len(items)):\n"
        "    items[index] = transform(items[index])\n"
        "\n"
        "for index in range(len(items)):\n"
        "    pass\n"
    )

    assert not any(issue.rule_id == "inefficient_loop" for issue in result.issues)


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
