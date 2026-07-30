from __future__ import annotations

from types import SimpleNamespace

from backend.app.benchmark.trace_compactor import compact_orchestrator_trace
from backend.app.orchestrator import Orchestrator, OrchestratorConfig
from backend.app.orchestrator.models import ToolAction
from tools.run_repair_benchmark import (
    build_runtime_plan_summary,
    count_runtime_plan_decisions,
)


def _runtime_context(
    *,
    cuda_available: bool = True,
    cpu_fallback_allowed: bool = True,
) -> dict:
    return {
        "hardware": {
            "gpu": {
                "cuda_available": cuda_available,
                "vram_total_mb": 4096 if cuda_available else None,
            }
        },
        "policy": {
            "low_vram_mode": True,
            "prefer_quantized_models": True,
            "avoid_large_models": True,
            "prefer_rag_over_finetuning": True,
            "prefer_cpu_fallback": cpu_fallback_allowed,
            "cpu_fallback_allowed": cpu_fallback_allowed,
            "max_recommended_local_model_billion_params": 3.0,
        },
    }


class DowngradeThenAuthorizeProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls <= 2:
            return ToolAction(
                action="validate_runtime_plan",
                reason="Request full fine-tuning.",
                args={
                    "task": "large model fine-tuning",
                    "requested_plan": {
                        "strategy": "full_finetuning",
                        "requires_gpu": True,
                    },
                },
            )
        if self.calls == 3:
            return ToolAction(
                action="build_execution_profile",
                reason="Compile safe execution settings.",
                args={"task": "large model fine-tuning"},
            )
        return ToolAction(
            action="authorize_runtime_plan",
            reason="Authorize the active runtime plan.",
            args={},
        )


class BlockedPlanProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="validate_runtime_plan",
            reason="Request a GPU-only plan.",
            args={
                "task": "pytorch_training",
                "requested_plan": {
                    "strategy": "gpu_only_training",
                    "requires_gpu": True,
                    "device": "cuda",
                },
            },
        )


class AuthorizeWithoutValidationProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="authorize_runtime_plan",
            reason="Attempt to bypass runtime validation.",
            args={"plan": {"strategy": "full_finetuning"}},
        )


class ValidateThenAuthorizeWithoutProfileProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls == 1:
            return ToolAction(
                action="validate_runtime_plan",
                reason="Validate a CPU-safe plan.",
                args={
                    "task": "classical_ml",
                    "requested_plan": {
                        "strategy": "sklearn_pipeline",
                        "device": "cpu",
                    },
                },
            )
        return ToolAction(
            action="authorize_runtime_plan",
            reason="Attempt authorization without a profile.",
            args={},
        )


def test_downgraded_plan_is_activated_revalidated_and_authorized(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.app.orchestrator.tools.build_runtime_context",
        lambda **_: _runtime_context(),
    )

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=DowngradeThenAuthorizeProposer(),
        config=OrchestratorConfig(max_steps=4),
    ).run(goal="Fine-tune a large local model")

    assert result.trace["runtime_plan_audits"][0]["decision"] == "downgrade"
    assert result.trace["runtime_plan_audits"][0]["enforcement"] == (
        "recommended_plan_activated"
    )
    assert result.trace["runtime_plan_audits"][1]["decision"] == "allow"
    assert result.trace["active_runtime_plan"]["strategy"] == "rag"
    assert result.trace["tool_history"][1]["output"]["requested_plan"]["strategy"] == "rag"
    assert result.trace["tool_history"][2]["action"] == "build_execution_profile"
    assert result.trace["execution_profile"]["task_type"] == "rag"
    assert result.trace["execution_profile"]["source_plan"]["strategy"] == "rag"
    assert result.trace["tool_history"][3]["action"] == "authorize_runtime_plan"
    assert result.trace["tool_history"][3]["output"]["authorized"] is True
    assert result.trace["repair_trace_events"][0]["runtime_plan_decision"] == (
        "downgrade"
    )
    assert result.trace["repair_trace_events"][0]["runtime_plan_enforced"] == (
        "recommended_plan_activated"
    )
    assert result.trace["repair_trace_events"][2]["execution_profile"]["task_type"] == (
        "rag"
    )


def test_blocked_plan_stops_orchestration_and_is_traceable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.app.orchestrator.tools.build_runtime_context",
        lambda **_: _runtime_context(
            cuda_available=False,
            cpu_fallback_allowed=False,
        ),
    )

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=BlockedPlanProposer(),
        config=OrchestratorConfig(max_steps=3),
    ).run(goal="Run a GPU-only training plan")

    assert result.status == "blocked"
    assert len(result.trace["tool_history"]) == 1
    assert result.trace["validation"]["runtime_plan"]["decision"] == "block"
    assert result.trace["runtime_plan_audits"][0]["enforcement"] == "task_stopped"
    assert result.trace["repair_trace_events"][0]["runtime_plan_decision"] == "block"
    assert result.trace["active_runtime_plan"] is None
    assert "not allowed" in result.final_response.lower()


def test_runtime_plan_authorization_cannot_bypass_validation(tmp_path):
    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=AuthorizeWithoutValidationProposer(),
        config=OrchestratorConfig(max_steps=2),
    ).run(goal="Execute an AI workload")

    assert result.status == "blocked"
    assert result.trace["tool_history"][0]["action"] == "authorize_runtime_plan"
    assert result.trace["tool_history"][0]["allowed"] is False
    assert "validate_runtime_plan must run" in result.trace["tool_history"][0][
        "policy_reason"
    ]


def test_runtime_plan_authorization_requires_execution_profile(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.app.orchestrator.tools.build_runtime_context",
        lambda **_: _runtime_context(),
    )
    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ValidateThenAuthorizeWithoutProfileProposer(),
        config=OrchestratorConfig(max_steps=2),
    ).run(goal="Build a classical ML baseline")

    assert result.status == "blocked"
    assert result.trace["tool_history"][1]["action"] == "authorize_runtime_plan"
    assert "build_execution_profile must run" in result.trace["tool_history"][1][
        "policy_reason"
    ]


def test_compact_trace_keeps_runtime_gate_audit_and_decision():
    compact = compact_orchestrator_trace(
        {
            "task_id": "task-12b",
            "goal": "Run local model",
            "status": "completed",
            "tool_history": [
                {
                    "action": "validate_runtime_plan",
                    "allowed": True,
                    "success": True,
                    "output": {
                        "decision": "downgrade",
                        "blocked_signals": ["large_local_model"],
                        "recommended_plan": {"strategy": "quantized_inference"},
                    },
                }
            ],
            "validation": {
                "runtime_plan": {
                    "allowed": False,
                    "decision": "downgrade",
                    "reason": "Model too large.",
                    "blocked_signals": ["large_local_model"],
                    "recommended_plan": {"strategy": "quantized_inference"},
                }
            },
            "runtime_plan_audits": [
                {
                    "decision": "downgrade",
                    "enforcement": "recommended_plan_activated",
                }
            ],
            "active_runtime_plan": {"strategy": "quantized_inference"},
            "execution_profile": {
                "profile_version": "runtime_execution_profile_v1",
                "task_type": "local_slm",
                "strategy": "quantized_inference",
                "runtime": "ollama",
                "device": "cuda",
                "settings": {"max_context_tokens": 4096},
            },
        }
    )

    assert compact["validation"]["runtime_plan"]["decision"] == "downgrade"
    assert compact["tool_history"][0]["blocked_signals"] == ["large_local_model"]
    assert compact["runtime_plan_audits"][0]["enforcement"] == (
        "recommended_plan_activated"
    )
    assert compact["active_runtime_plan"]["strategy"] == "quantized_inference"
    assert compact["execution_profile"]["runtime"] == "ollama"


def test_benchmark_counts_all_runtime_plan_decisions():
    results = [
        SimpleNamespace(
            runtime_plan_decisions=["allow"],
            runtime_plan_followed=True,
        ),
        SimpleNamespace(
            runtime_plan_decisions=["downgrade", "allow"],
            runtime_plan_followed=True,
        ),
        SimpleNamespace(
            runtime_plan_decisions=["block"],
            runtime_plan_followed=True,
        ),
        SimpleNamespace(runtime_plan_decisions=[], runtime_plan_followed=False),
    ]
    counts = count_runtime_plan_decisions(results)
    summary = build_runtime_plan_summary(results)

    assert counts == {"allow": 2, "downgrade": 1, "block": 1}
    assert summary == {
        "runtime_plan_validations": 4,
        "runtime_plan_decision_counts": {
            "allow": 2,
            "downgrade": 1,
            "block": 1,
        },
        "runtime_plan_followed_count": 3,
    }
