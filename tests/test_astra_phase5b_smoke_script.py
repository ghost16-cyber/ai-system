from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import backend.app.local_ai.service as local_ai_service_module
from backend.app.local_ai.contracts import AdmissionOutcome, HardwareAdmissionDecision
from backend.app.local_ai.provider import (
    OllamaProviderClient,
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


def _fake_generate(self, request, *, cancelled=None):
    del cancelled
    start_marker = "<UNTRUSTED_PROJECT_SYNTHESIS_DATA>\n"
    end_marker = "\n</UNTRUSTED_PROJECT_SYNTHESIS_DATA>"
    start = request.prompt.index(start_marker) + len(start_marker)
    end = request.prompt.index(end_marker)
    payload = json.loads(request.prompt[start:end])
    response = json.dumps({
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": payload["request_id"],
        "summary": "Fake smoke-test synthesis.",
        "operations": [{
            "operation": "modify", "path": "app.py", "expected_sha256": "3" * 64,
            "strategy": "complete_content", "replacements": [], "content": "VALUE = 2\n",
            "rationale": "Fake smoke-test change.", "affected_symbols": ["VALUE"],
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


def _fake_admission_preview(self, request, *, report=None):
    del report
    return HardwareAdmissionDecision(
        outcome=AdmissionOutcome.GPU, reason="fake smoke-test admission",
        backend="cuda", device="gpu:0",
        estimated_required_bytes=1, available_bytes=10_000_000, safety_reserve_bytes=0,
    )


def _seed_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh temp database has no pre-existing operator-enabled row -- fake
    the seed default itself (an isolated, in-test-only override) so this
    disposable smoke run can reach a real generation instead of failing on
    'model_profile_not_enabled', without touching the live database or any
    real enable/disable action."""
    original = local_ai_service_module.default_model_profiles

    def patched(configuration=None):
        profiles = original(configuration)
        return tuple(
            profile.model_copy(update={"enabled": True})
            if profile.model_profile_id == "configured-local-model"
            else profile
            for profile in profiles
        )

    monkeypatch.setattr(local_ai_service_module, "default_model_profiles", patched)


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
    _seed_enabled_by_default(monkeypatch)
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(sys, "argv", ["astra_phase5b_smoke.py", "--confirm-advisory-generation"])
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    diagnostic = json.loads(captured.out)
    assert diagnostic["exact_model_tag"] == MODEL_TAG
    assert diagnostic["provider_identity"] == "ollama"


def test_smoke_script_reports_bounded_diagnostic_on_failure_without_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Even when the model profile is not enabled (the real default state),
    the script must fail closed with its own bounded diagnostic -- not an
    unrelated AttributeError from a stale attribute reference."""
    monkeypatch.setattr(OllamaProviderClient, "inspect", _fake_inspect)
    monkeypatch.setattr(OllamaProviderClient, "generate", _fake_generate)
    _configure_environment(monkeypatch)

    module = _load_smoke_module()
    monkeypatch.setattr(sys, "argv", ["astra_phase5b_smoke.py", "--confirm-advisory-generation"])
    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    diagnostic = json.loads(captured.err)
    assert diagnostic["generation_failure_classification"] == "smoke_internal_failure"
