from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from backend.app.local_ai.contracts import AdmissionOutcome, HardwareAdmissionDecision
from backend.app.local_ai.provider import (
    OllamaProviderClient,
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.service import LocalAIService


MODEL_TAG = "qwen-smoke-test:1.5b"

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "astra_phase5b_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("astra_phase5b_smoke", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_inspect(self, *, timeout_seconds):
    del timeout_seconds
    return ProviderInspection(
        provider_version="fake-smoke", installed_models=(MODEL_TAG,), loaded_models=()
    )


def _fake_inspect_missing_model(self, *, timeout_seconds):
    del self, timeout_seconds
    return ProviderInspection(
        provider_version="fake-smoke", installed_models=(), loaded_models=()
    )


def _fake_inspect_unreachable(self, *, timeout_seconds):
    del self, timeout_seconds
    raise ProviderClientError(
        ProviderErrorCode.UNREACHABLE,
        "The configured local model provider is unreachable.",
    )


def _fake_generate(self, request, *, cancelled=None):
    del cancelled
    start_marker = "<UNTRUSTED_PROJECT_SYNTHESIS_DATA>\n"
    end_marker = "\n</UNTRUSTED_PROJECT_SYNTHESIS_DATA>"
    start = request.prompt.index(start_marker) + len(start_marker)
    end = request.prompt.index(end_marker)
    payload = json.loads(request.prompt[start:end])
    definitions = (request.exact_response_schema or {}).get("$defs", {})
    modify_schema = next(
        value
        for name, value in definitions.items()
        if name.startswith("BoundedModifyExactOperation")
    )
    expected_sha256 = modify_schema["properties"]["expected_sha256"]["const"]
    response = json.dumps({
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": payload["request_id"],
        "summary": "Evidence-backed bounded patch.",
        "operations": [{
            "operation": "modify", "path": "app.py",
            "expected_sha256": expected_sha256,
            "strategy": "exact_replacements",
            "replacements": [{
                "start_line": 1, "end_line": 1,
                "expected_text": "VALUE = 1\n",
                "replacement_text": "VALUE = 2\n",
            }],
            "content": None,
            "rationale": "Apply the minimal evidence-backed repair.",
            "affected_symbols": ["VALUE"],
            "evidence_references": ["app.py"],
        }],
        "assumptions": [], "uncertainties": [], "model_confidence": "high",
        "requires_clarification": False, "clarification_question": None,
        "recommended_validation": [],
    }, separators=(",", ":"))
    return ProviderGenerationResponse(
        model=request.model, response=response,
        metadata={"prompt_eval_count": 5, "eval_count": 2},
    )


def _fake_generate_unreachable(self, request, *, cancelled=None):
    del self, request, cancelled
    raise ProviderClientError(
        ProviderErrorCode.UNREACHABLE,
        "The configured local model provider became unreachable.",
    )


def _fake_admission_preview(self, request, *, report=None):
    del report
    return HardwareAdmissionDecision(
        outcome=AdmissionOutcome.GPU, reason="fake smoke-test admission",
        backend="cuda", device="gpu:0",
        estimated_required_bytes=1, available_bytes=10_000_000, safety_reserve_bytes=0,
    )


def _configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTRA_LOCAL_AI_GENERATION_ENABLED", "1")
    monkeypatch.setenv("ASTRA_PROJECT_SYNTHESIS_ENABLED", "1")
    monkeypatch.setenv("ASTRA_LOCAL_AI_MODEL", MODEL_TAG)
    monkeypatch.setenv("ASTRA_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")
    monkeypatch.setenv("ASTRA_LOCAL_AI_ALLOW_CPU_FALLBACK", "1")


def test_smoke_script_uses_the_local_ai_service_backed_diagnostic_accessor(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Regression test for the Phase5ALocalSynthesisGateway rewire (GPU Admission
    Unification): this fails with AttributeError if the script still references
    the removed `gateway.local_gateway` attribute instead of the supported
    `gateway.local_ai_service.generation_diagnostic(...)` accessor. No real
    Ollama call is made -- OllamaProviderClient is monkeypatched at the class
    level so the script's own internal gateway construction is exercised for
    real, just without any real network/model dependency."""
    monkeypatch.setattr(OllamaProviderClient, "inspect", _fake_inspect)
    monkeypatch.setattr(OllamaProviderClient, "generate", _fake_generate)
    monkeypatch.setattr(LocalAIService, "admission_preview", _fake_admission_preview)
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(sys, "argv", ["astra_phase5b_smoke.py", "--confirm-advisory-generation"])
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    diagnostic = json.loads(captured.out)
    assert diagnostic["exact_model_tag"] == MODEL_TAG
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["provider_reachable"] is True
    assert diagnostic["configured_model_missing"] is False
    assert diagnostic["provider_readiness_reason"] is None


@pytest.mark.parametrize(
    ("inspect", "expected_code"),
    [
        (_fake_inspect_unreachable, "provider_unavailable"),
        (_fake_inspect_missing_model, "model_unavailable"),
    ],
)
def test_smoke_script_reports_typed_readiness_failure_without_generation(
    monkeypatch: pytest.MonkeyPatch, capsys, inspect, expected_code
) -> None:
    """Provider/model readiness failures are bounded and never invoke generation."""
    monkeypatch.setattr(OllamaProviderClient, "inspect", inspect)
    monkeypatch.setattr(
        OllamaProviderClient,
        "generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not run")
        ),
    )
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(sys, "argv", ["astra_phase5b_smoke.py", "--confirm-advisory-generation"])
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    diagnostic = json.loads(captured.err)
    assert diagnostic["generation_failure_classification"] == expected_code
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == MODEL_TAG
    assert diagnostic["provider_reachable"] is (inspect is not _fake_inspect_unreachable)
    assert diagnostic["configured_model_missing"] is (
        inspect is _fake_inspect_missing_model
    )
    assert diagnostic["provider_readiness_reason"] in {
        "provider_unreachable",
        "configured_model_missing",
    }


def test_smoke_script_preserves_capability_inspection_exception_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def fail_capability_inspection(self, *, refresh=False, max_age_seconds=60):
        del self, refresh, max_age_seconds
        raise RuntimeError("unsafe implementation detail must not be emitted")

    monkeypatch.setattr(
        LocalAIService,
        "capability_report",
        fail_capability_inspection,
    )
    monkeypatch.setattr(
        OllamaProviderClient,
        "generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not run")
        ),
    )
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["astra_phase5b_smoke.py", "--confirm-advisory-generation"],
    )
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unsafe implementation detail" not in captured.err
    diagnostic = json.loads(captured.err)
    assert (
        diagnostic["generation_failure_classification"]
        == "readiness_inspection_failed"
    )
    assert diagnostic["provider_readiness_reason"] == (
        "capability_inspection_failed"
    )
    assert diagnostic["readiness_error_type"] == "RuntimeError"
    assert diagnostic["readiness_stage"] == "capability_inspection"
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == MODEL_TAG


def test_outer_handler_merges_nested_readiness_diagnostic() -> None:
    module = _load_smoke_module()
    configuration = module.load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": MODEL_TAG,
    })
    readiness = {
        "provider_reachable": True,
        "configured_model_missing": True,
        "provider_readiness_reason": "configured_model_missing",
        "provider_identity": "ollama",
        "exact_model_tag": MODEL_TAG,
    }
    typed = module.SynthesisGatewayError(
        "The exact configured model is missing.",
        code="model_unavailable",
        diagnostic=readiness,
    )
    try:
        raise RuntimeError("outer wrapper") from typed
    except RuntimeError as exc:
        diagnostic = module._smoke_failure_diagnostic(exc, configuration)

    assert diagnostic["generation_failure_classification"] == "model_unavailable"
    assert diagnostic["provider_reachable"] is True
    assert diagnostic["configured_model_missing"] is True
    assert diagnostic["provider_readiness_reason"] == "configured_model_missing"
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == MODEL_TAG


def test_outer_handler_preserves_typed_admission_diagnostic() -> None:
    module = _load_smoke_module()
    configuration = module.load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": MODEL_TAG,
    })
    typed = module.SynthesisGatewayError(
        "Conservative GPU headroom is insufficient.",
        code="insufficient_vram",
        diagnostic={
            "admission_outcome": "blocked_due_to_vram",
            "provider_readiness_reason": (
                "Conservative GPU headroom is insufficient."
            ),
            "estimated_required_bytes": 2_952_790_016,
            "available_bytes": 2_213_543_936,
            "safety_reserve_bytes": 805_306_368,
        },
    )

    diagnostic = module._smoke_failure_diagnostic(typed, configuration)

    assert diagnostic["generation_failure_classification"] == "insufficient_vram"
    assert diagnostic["admission_outcome"] == "blocked_due_to_vram"
    assert diagnostic["provider_readiness_reason"] == (
        "Conservative GPU headroom is insufficient."
    )
    assert diagnostic["estimated_required_bytes"] == 2_952_790_016
    assert diagnostic["available_bytes"] == 2_213_543_936
    assert diagnostic["safety_reserve_bytes"] == 805_306_368


def test_outer_handler_preserves_canonical_synthesis_block() -> None:
    module = _load_smoke_module()
    configuration = module.load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": MODEL_TAG,
    })
    typed = module.CanonicalSynthesisBlocked(
        "The provider response was malformed or unsafe.",
        code="malformed_or_unsafe",
        diagnostic={
            "validation_error_type": "value_error",
            "validation_error_location": "operations.0.path",
        },
    )

    diagnostic = module._smoke_failure_diagnostic(typed, configuration)

    assert diagnostic["generation_failure_classification"] == (
        "malformed_or_unsafe"
    )
    assert diagnostic["provider_readiness_reason"] == (
        "The provider response was malformed or unsafe."
    )
    assert diagnostic["validation_error_type"] == "value_error"
    assert diagnostic["validation_error_location"] == "operations.0.path"


def test_outer_handler_preserves_successful_readiness_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(OllamaProviderClient, "inspect", _fake_inspect)
    monkeypatch.setattr(
        OllamaProviderClient,
        "generate",
        _fake_generate_unreachable,
    )
    monkeypatch.setattr(
        LocalAIService,
        "admission_preview",
        _fake_admission_preview,
    )
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["astra_phase5b_smoke.py", "--confirm-advisory-generation"],
    )
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    diagnostic = json.loads(captured.err)
    assert (
        diagnostic["generation_failure_classification"]
        == "provider_unreachable"
    )
    assert diagnostic["provider_reachable"] is True
    assert diagnostic["configured_model_missing"] is False
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == MODEL_TAG
    assert (
        diagnostic["provider_error_code"]
        == ProviderErrorCode.UNREACHABLE.value
    )
