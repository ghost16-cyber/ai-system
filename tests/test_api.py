import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as test_client:
        yield test_client


@pytest.fixture
def workspace_client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(
        create_app(tmp_path / "workspace-test.db", workspace_root=workspace)
    ) as test_client:
        yield test_client, workspace


def test_health_endpoint_reports_ready_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["phase"] == "release-4-feedback"
    assert response.json()["database"] == "ready"


def test_analyze_endpoint_records_python_request_without_storing_source(client):
    code = "print('hello')\n"
    response = client.post(
        "/analyze",
        json={"code": code, "language": "python", "filename": "demo.py"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["filename"] == "demo.py"
    assert data["issues"] == []
    assert data["suggestions"] == []
    assert data["patch_proposals"] == []
    assert data["metadata"]["engine"] == "python-ast-static-analyzer"
    assert data["metadata"]["suggestion_engine"] == "deterministic-validated-fixes"
    assert data["metadata"]["validated_fix_count"] == 0
    assert data["metadata"]["parse_success"] is True
    assert data["metadata"]["code_stored"] is False
    assert data["metadata"]["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()

    history = client.get("/history").json()["items"]
    assert len(history) == 1
    assert history[0]["analysis_id"] == data["analysis_id"]
    assert history[0]["code_sha256"] == data["metadata"]["code_sha256"]
    assert history[0]["issue_count"] == 0
    assert "code" not in history[0]


def test_analyze_endpoint_returns_static_findings_and_stores_count(client):
    code = (
        "def risky(values=[]):\n"
        "    \"\"\"Return a risky sample value.\"\"\"\n"
        "    try:\n"
        "        if eval('1') == None:\n"
        "            return True\n"
        "    except:\n"
        "        return values\n"
    )
    response = client.post("/analyze", json={"code": code, "language": "python"})

    assert response.status_code == 200
    data = response.json()
    rule_ids = {issue["rule_id"] for issue in data["issues"]}

    assert rule_ids == {
        "mutable_default_argument",
        "dangerous_eval",
        "bad_none_comparison",
        "bare_except",
    }
    assert all(issue["source"] == "static_rule" for issue in data["issues"])
    assert data["metadata"]["parse_success"] is True
    assert data["metadata"]["validated_fix_count"] == 1
    none_issue = next(
        issue for issue in data["issues"] if issue["rule_id"] == "bad_none_comparison"
    )
    assert "is None" in none_issue["suggested_code"]
    assert none_issue["validation"]["status"] == "passed"
    eval_issue = next(
        issue for issue in data["issues"] if issue["rule_id"] == "dangerous_eval"
    )
    assert eval_issue["suggested_code"] is None
    assert eval_issue["validation"]["status"] == "not_available"

    history = client.get("/history?limit=1").json()["items"]
    assert history[0]["issue_count"] == 4
    assert history[0]["phase"] == "release-4-feedback"


def test_analyze_endpoint_reports_syntax_error_without_crashing(client):
    response = client.post(
        "/analyze",
        json={"code": "def broken(:\n    pass\n", "language": "python"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["parse_success"] is False
    assert data["metadata"]["validated_fix_count"] == 0
    assert [issue["rule_id"] for issue in data["issues"]] == ["syntax_error"]
    assert data["issues"][0]["severity"] == "high"


def test_history_limit_returns_latest_analysis(client):
    for filename in ("one.py", "two.py"):
        response = client.post(
            "/analyze",
            json={"code": "x = 1", "language": "python", "filename": filename},
        )
        assert response.status_code == 200

    response = client.get("/history?limit=1")

    assert response.status_code == 200
    assert [item["filename"] for item in response.json()["items"]] == ["two.py"]


def test_analyze_endpoint_rejects_empty_code(client):
    response = client.post("/analyze", json={"code": "", "language": "python"})

    assert response.status_code == 422


def test_analyze_endpoint_rejects_non_python_language(client):
    response = client.post("/analyze", json={"code": "let x = 1;", "language": "js"})

    assert response.status_code == 400


def test_analyze_file_creates_compact_validated_patch_proposal(workspace_client):
    test_client, workspace = workspace_client
    code = "if value == None:\n    print(value)\n"
    (workspace / "sample.py").write_text(code, encoding="utf-8")

    response = test_client.post("/analyze-file", json={"path": "sample.py"})

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "sample.py"
    assert [issue["rule_id"] for issue in data["issues"]] == ["bad_none_comparison"]
    assert data["issues"][0]["validation"]["status"] == "passed"
    assert data["patch_proposals"] == [
        {
            "proposal_id": data["patch_proposals"][0]["proposal_id"],
            "analysis_id": data["analysis_id"],
            "finding_id": data["issues"][0]["finding_id"],
            "path": "sample.py",
            "original_file_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "start_line": 1,
            "end_line": 1,
            "replacement": "if value is None:\n",
            "validation_status": "passed",
            "status": "proposed",
        }
    ]
    assert data["metadata"]["code_stored"] is False
    assert data["metadata"]["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()

    history = test_client.get("/history").json()["items"]
    assert history[0]["filename"] == "sample.py"
    assert "code" not in history[0]

    with sqlite3.connect(test_client.app.state.analysis_repository.database_path) as connection:
        stored = connection.execute(
            "SELECT path, replacement, original_file_sha256 FROM patch_proposals"
        ).fetchone()
    assert stored == (
        "sample.py",
        "if value is None:\n",
        hashlib.sha256(code.encode()).hexdigest(),
    )
    assert code not in stored[1]


def test_analyze_file_does_not_propose_unvalidated_guidance(workspace_client):
    test_client, workspace = workspace_client
    (workspace / "unsafe.py").write_text("value = eval(user_input)\n", encoding="utf-8")

    response = test_client.post("/analyze-file", json={"path": "unsafe.py"})

    assert response.status_code == 200
    assert response.json()["patch_proposals"] == []
    with sqlite3.connect(test_client.app.state.analysis_repository.database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM patch_proposals").fetchone()[0]
    assert count == 0


def test_analyze_file_rejects_paths_outside_workspace_and_non_python_files(
    workspace_client, tmp_path
):
    test_client, workspace = workspace_client
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("print('outside')\n", encoding="utf-8")
    (workspace / "escaped.py").symlink_to(outside_file)
    (workspace / "notes.txt").write_text("not Python\n", encoding="utf-8")

    traversal = test_client.post("/analyze-file", json={"path": "../outside.py"})
    symlink_escape = test_client.post("/analyze-file", json={"path": "escaped.py"})
    non_python = test_client.post("/analyze-file", json={"path": "notes.txt"})
    missing = test_client.post("/analyze-file", json={"path": "missing.py"})

    assert traversal.status_code == 400
    assert symlink_escape.status_code == 400
    assert non_python.status_code == 400
    assert missing.status_code == 404


def test_rules_endpoint_lists_supported_deterministic_capabilities(client):
    response = client.get("/rules")

    assert response.status_code == 200
    rules = {item["rule_id"]: item for item in response.json()["items"]}

    assert set(rules) == {
        "syntax_error",
        "bare_except",
        "dangerous_eval",
        "dangerous_exec",
        "mutable_default_argument",
        "bad_none_comparison",
        "redundant_boolean_comparison",
        "missing_docstring",
        "unused_import",
        "inefficient_loop",
    }
    assert rules["bad_none_comparison"]["fix_available"] is True
    assert rules["redundant_boolean_comparison"]["fix_available"] is True
    assert rules["unused_import"]["fix_available"] is False
    assert rules["syntax_error"]["source"] == "static_rule"


def test_tools_endpoint_lists_only_available_coordinator_tools(client):
    response = client.get("/tools")

    assert response.status_code == 200
    tools = {item["name"]: item for item in response.json()["items"]}

    assert set(tools) == {
        "analyze_code",
        "analyze_file",
        "analyze_project",
        "get_rules",
        "get_metrics",
        "orchestrate",
    }
    assert tools["analyze_code"]["input_schema"]["language"] == "python"
    assert all(
        item["read_only"] is True
        for name, item in tools.items()
        if name != "orchestrate"
    )
    assert tools["orchestrate"]["read_only"] is False
    assert tools["analyze_project"]["execution"] == "job_backed"
    assert tools["orchestrate"]["execution"] == "job_backed"
    assert tools["analyze_file"]["execution"] == "synchronous"


def test_metrics_aggregate_findings_parse_failures_and_validated_fixes(client):
    submissions = [
        "value = 1\n",
        "if value == None:\n    print(value)\n",
        "value = eval(user_input)\n",
        "def broken(:\n    pass\n",
    ]
    for code in submissions:
        response = client.post("/analyze", json={"code": code, "language": "python"})
        assert response.status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "release-4-feedback"
    assert data["total_analyses"] == 4
    assert data["analyses_with_findings"] == 3
    assert data["clean_analyses"] == 1
    assert data["total_findings"] == 3
    assert data["average_findings_per_analysis"] == 0.75
    assert data["parse_failures"] == 1
    assert data["validated_fixes"] == 1
    assert data["fixable_findings"] == 1
    assert data["validated_fix_rate"] == 0.33
    assert data["findings_without_fix"] == 2
    assert data["fixes_by_rule"] == {"bad_none_comparison": 1}
    assert data["findings_by_rule"] == {
        "bad_none_comparison": 1,
        "dangerous_eval": 1,
        "syntax_error": 1,
    }
    assert data["findings_by_severity"] == {"high": 2, "low": 1}
    assert data["validation_statuses"] == {"not_available": 2, "passed": 1}
    assert data["total_feedback"] == 0
    assert data["suggestion_acceptance_rate"] is None


def test_startup_upgrades_release_2_database_for_metrics(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE analyses (
                analysis_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                code_sha256 TEXT NOT NULL,
                language TEXT NOT NULL,
                filename TEXT,
                code_length INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                issue_count INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL
            )
            """
        )

    with TestClient(create_app(database_path)) as upgraded_client:
        response = upgraded_client.get("/metrics")

    assert response.status_code == 200
    assert response.json()["total_analyses"] == 0
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analyses)").fetchall()
        }
    assert {"parse_success", "validated_fix_count"} <= columns


def test_feedback_records_helpfulness_and_validated_suggestion_decision(client):
    analysis = client.post(
        "/analyze",
        json={"code": "if value != None:\n    print(value)\n", "language": "python"},
    ).json()
    finding_id = analysis["issues"][0]["finding_id"]

    response = client.post(
        "/feedback",
        json={
            "analysis_id": analysis["analysis_id"],
            "finding_id": finding_id,
            "helpful": True,
            "suggestion_accepted": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["finding_id"] == finding_id
    assert response.json()["helpful"] is True
    assert response.json()["suggestion_accepted"] is True

    metrics = client.get("/metrics").json()
    assert metrics["total_feedback"] == 1
    assert metrics["helpful_feedback"] == 1
    assert metrics["accepted_suggestions"] == 1
    assert metrics["suggestion_acceptance_rate"] == 1.0


def test_revised_feedback_replaces_existing_judgment_in_metrics(client):
    analysis = client.post(
        "/analyze",
        json={"code": "if value == None:\n    pass\n", "language": "python"},
    ).json()
    feedback_target = {
        "analysis_id": analysis["analysis_id"],
        "finding_id": analysis["issues"][0]["finding_id"],
    }

    first = client.post(
        "/feedback",
        json={**feedback_target, "helpful": True, "suggestion_accepted": True},
    )
    revised = client.post(
        "/feedback",
        json={**feedback_target, "helpful": False, "suggestion_accepted": False},
    )

    assert first.status_code == 200
    assert revised.status_code == 200
    metrics = client.get("/metrics").json()
    assert metrics["total_feedback"] == 1
    assert metrics["helpful_feedback"] == 0
    assert metrics["unhelpful_feedback"] == 1
    assert metrics["accepted_suggestions"] == 0
    assert metrics["rejected_suggestions"] == 1
    assert metrics["suggestion_acceptance_rate"] == 0.0


def test_feedback_acceptance_requires_a_validated_fix(client):
    analysis = client.post(
        "/analyze",
        json={"code": "value = eval(user_input)\n", "language": "python"},
    ).json()

    response = client.post(
        "/feedback",
        json={
            "analysis_id": analysis["analysis_id"],
            "finding_id": analysis["issues"][0]["finding_id"],
            "helpful": False,
            "suggestion_accepted": False,
        },
    )

    assert response.status_code == 400
    assert "validated suggestion" in response.json()["detail"]

    helpfulness_only = client.post(
        "/feedback",
        json={
            "analysis_id": analysis["analysis_id"],
            "finding_id": analysis["issues"][0]["finding_id"],
            "helpful": False,
        },
    )
    assert helpfulness_only.status_code == 200
    assert helpfulness_only.json()["suggestion_accepted"] is None


def test_feedback_rejects_finding_from_another_analysis(client):
    first = client.post(
        "/analyze",
        json={"code": "if one == None:\n    pass\n", "language": "python"},
    ).json()
    second = client.post(
        "/analyze",
        json={"code": "if two == None:\n    pass\n", "language": "python"},
    ).json()

    response = client.post(
        "/feedback",
        json={
            "analysis_id": first["analysis_id"],
            "finding_id": second["issues"][0]["finding_id"],
            "helpful": True,
        },
    )

    assert response.status_code == 404

def test_analyze_endpoint_suggests_fix_for_true_boolean_comparison(client):
    response = client.post(
        "/analyze",
        json={
            "code": "if flag == True:\n    print(flag)\n",
            "language": "python",
            "filename": "bool_true.py",
        },
    )

    assert response.status_code == 200

    data = response.json()
    issue = data["issues"][0]

    assert issue["rule_id"] == "redundant_boolean_comparison"
    assert issue["suggested_code"] == "if flag:\n    print(flag)\n"
    assert issue["validation"]["status"] == "passed"
    assert data["patch_proposals"] == []
    assert data["metadata"]["validated_fix_count"] == 1


def test_analyze_endpoint_suggests_fix_for_false_boolean_comparison(client):
    response = client.post(
        "/analyze",
        json={
            "code": "if flag == False:\n    print(flag)\n",
            "language": "python",
            "filename": "bool_false.py",
        },
    )

    assert response.status_code == 200

    data = response.json()
    issue = data["issues"][0]

    assert issue["rule_id"] == "redundant_boolean_comparison"
    assert issue["suggested_code"] == "if not flag:\n    print(flag)\n"
    assert issue["validation"]["status"] == "passed"
    assert data["metadata"]["validated_fix_count"] == 1

def test_metrics_reports_fix_coverage_for_multiple_fixable_rules(client):
    submissions = [
        "if value == None:\n    print(value)\n",
        "if flag == True:\n    print(flag)\n",
        "value = eval(user_input)\n",
    ]

    for code in submissions:
        response = client.post(
            "/analyze",
            json={"code": code, "language": "python"},
        )
        assert response.status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200

    data = response.json()

    assert data["total_findings"] == 3
    assert data["validated_fixes"] == 2
    assert data["fixable_findings"] == 2
    assert data["findings_without_fix"] == 1
    assert data["validated_fix_rate"] == 0.67
    assert data["fixes_by_rule"] == {
        "bad_none_comparison": 1,
        "redundant_boolean_comparison": 1,
    }
