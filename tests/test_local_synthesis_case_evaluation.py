from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.project_analysis.model_synthesis import (
    CanonicalSynthesisBlocked,
    FakeSynthesisGateway,
)
from tools import run_local_synthesis_case


CASE = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "real_repos"
    / "real_001_inventory_line_total"
)
SOURCE_PATH = "app/services/pricing.py"
TEST_PATH = "tests/test_pricing.py"
FIXED_SOURCE = """from dataclasses import dataclass


@dataclass
class LineItem:
    price: int
    quantity: int


def line_total(item: LineItem) -> int:
    return item.price * item.quantity
"""


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _repair_gateway(*, path: str = SOURCE_PATH) -> FakeSynthesisGateway:
    def response(raw_request: str) -> str:
        request = json.loads(raw_request)
        file_identities = request["evidence"]["file_identities"]
        return json.dumps(
            {
                "contract_version": "astra.project-synthesis.response.v1",
                "request_id": request["request_id"],
                "summary": "Correct the bounded inventory line-total calculation.",
                "operations": [
                    {
                        "operation": "modify",
                        "path": path,
                        "expected_sha256": file_identities[path],
                        "strategy": "complete_content",
                        "replacements": [],
                        "content": FIXED_SOURCE,
                        "rationale": "The line total is price multiplied by quantity.",
                        "affected_symbols": ["line_total"],
                        "evidence_references": [path],
                    }
                ],
                "assumptions": [],
                "uncertainties": [],
                "model_confidence": "high",
                "requires_clarification": False,
                "clarification_question": None,
                "recommended_validation": [
                    {
                        "action": "pytest",
                        "target": TEST_PATH,
                        "reason": "Run the exact pre-existing failing test.",
                    }
                ],
            },
            separators=(",", ":"),
        )

    return FakeSynthesisGateway(response=response)


def test_one_case_evaluation_repairs_only_disposable_copy() -> None:
    before = _directory_hash(CASE)
    gateway = _repair_gateway()

    report = run_local_synthesis_case.evaluate_case(CASE, gateway)

    assert report["status"] == "verified"
    assert report["verified_repair_success"] is True
    assert report["initial_test"]["status"] == "failed"
    assert report["final_test"]["status"] == "passed"
    assert report["application"]["changed_paths"] == [SOURCE_PATH]
    assert report["application"]["touched_expected_file"] is True
    assert report["application"]["touched_unexpected_evaluation_file"] is False
    assert report["retrieval"]["evidence_count"] <= 3
    assert "metadata.json" not in report["retrieval"]["paths"]
    assert report["disposable_workspace_only"] is True
    assert report["original_case_unchanged"] is True
    assert report["advisory_only"] is True
    assert report["authority_granted"] is False
    assert gateway.call_count == 1
    assert _directory_hash(CASE) == before


def test_disposable_gateway_is_built_after_schema_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "disposable.db"
    sentinel = object()

    def build(*, database_path):
        assert Path(database_path) == database
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0] > 0
            assert connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'local_ai_models'"
            ).fetchone() is not None
        return sentinel

    monkeypatch.setattr(
        run_local_synthesis_case,
        "build_synthesis_gateway_from_environment",
        build,
    )

    assert run_local_synthesis_case._build_disposable_gateway(database) is sentinel


def test_out_of_scope_generated_patch_is_rejected_without_source_change() -> None:
    before = _directory_hash(CASE)
    gateway = _repair_gateway(path=TEST_PATH)

    with pytest.raises(CanonicalSynthesisBlocked) as caught:
        run_local_synthesis_case.evaluate_case(CASE, gateway)

    assert caught.value.code == "scope_violation"
    assert gateway.call_count == 1
    assert _directory_hash(CASE) == before


def test_confirmation_gate_performs_no_provider_or_evaluation_work(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("confirmation gate was bypassed")

    monkeypatch.setattr(
        run_local_synthesis_case,
        "evaluate_case",
        unexpected_call,
    )

    exit_code = run_local_synthesis_case.main(
        ["--case-id", "real_001_inventory_line_total"]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["status"] == "confirmation_required"
    assert report["authority_granted"] is False


def test_main_preserves_typed_admission_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def blocked(_case):
        raise CanonicalSynthesisBlocked(
            "Conservative GPU admission was blocked.",
            code="insufficient_vram",
            diagnostic={
                "admission_outcome": "blocked_due_to_vram",
                "provider_readiness_reason": (
                    "Conservative GPU admission was blocked."
                ),
                "estimated_required_bytes": 20,
                "available_bytes": 10,
                "safety_reserve_bytes": 5,
                "validation_error_reason": "invalid_exact_replacements_shape",
            },
        )

    monkeypatch.setattr(run_local_synthesis_case, "evaluate_case", blocked)

    exit_code = run_local_synthesis_case.main([
        "--case-id",
        "real_001_inventory_line_total",
        "--confirm-advisory-generation",
        "--confirm-disposable-apply-and-test",
    ])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["failure_classification"] == "insufficient_vram"
    assert report["diagnostic"] == {
        "provider_readiness_reason": (
            "Conservative GPU admission was blocked."
        ),
        "admission_outcome": "blocked_due_to_vram",
        "estimated_required_bytes": 20,
        "available_bytes": 10,
        "safety_reserve_bytes": 5,
        "validation_error_reason": "invalid_exact_replacements_shape",
    }


def test_main_preserves_safe_syntax_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        run_local_synthesis_case,
        "evaluate_case",
        lambda _case: (_ for _ in ()).throw(
            run_local_synthesis_case.LocalSynthesisEvaluationError(
                "syntax_invalid",
                "The synthesized Python content does not compile.",
                diagnostic={
                    "syntax_error_type": "SyntaxError",
                    "syntax_error_line": 7,
                    "syntax_error_offset": 3,
                    "syntax_error_reason": "unexpected indent",
                    "unsafe_source": "must not escape",
                },
            )
        ),
    )

    exit_code = run_local_synthesis_case.main([
        "--case-id",
        "real_001_inventory_line_total",
        "--confirm-advisory-generation",
        "--confirm-disposable-apply-and-test",
    ])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["failure_classification"] == "syntax_invalid"
    assert report["diagnostic"] == {
        "syntax_error_type": "SyntaxError",
        "syntax_error_line": 7,
        "syntax_error_offset": 3,
        "syntax_error_reason": "unexpected indent",
    }


def test_anchor_mismatch_exposes_hashes_without_source_text() -> None:
    with pytest.raises(
        run_local_synthesis_case.LocalSynthesisEvaluationError
    ) as caught:
        run_local_synthesis_case._apply_exact_replacements(
            "VALUE = 1\n",
            ({
                "start_line": 1,
                "end_line": 1,
                "expected_text": "VALUE = 1",
                "replacement_text": "VALUE = 2\n",
            },),
        )

    assert caught.value.code == "replacement_anchor_mismatch"
    diagnostic = caught.value.diagnostic
    assert diagnostic["replacement_start_line"] == 1
    assert diagnostic["replacement_end_line"] == 1
    assert diagnostic["actual_ends_with_newline"] is True
    assert diagnostic["expected_ends_with_newline"] is False
    assert "VALUE" not in json.dumps(diagnostic)


@pytest.mark.parametrize("case_id", ("../real_001_inventory_line_total", "x"))
def test_case_resolution_rejects_unsafe_or_invalid_identity(case_id: str) -> None:
    with pytest.raises(
        run_local_synthesis_case.LocalSynthesisEvaluationError
    ) as caught:
        run_local_synthesis_case.resolve_case("real", case_id)

    assert caught.value.code == "invalid_case_identity"
