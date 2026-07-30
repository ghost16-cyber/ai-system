from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.orchestrator import Orchestrator, OrchestratorConfig
from app.orchestrator.models import AdvisorOutput, ToolAction


class StaticAdvisor:
    def __init__(self, output: AdvisorOutput):
        self.name = output.name
        self.output = output

    def analyze(self, state, policy):
        return self.output


class StopProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="final_response",
            reason="Stop after advisor setup.",
            args={"message": "done"},
        )


class ReadAlphaProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="read_file",
            reason="Read alpha.",
            args={"path": "alpha.py"},
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Phase 8 controlled advisor influence gates."
    )
    parser.add_argument(
        "--output",
        default="benchmarks/.runs/phase8_controlled_influence_report_latest.json",
        help="Path to write the Phase 8 controlled influence report.",
    )
    args = parser.parse_args()

    report = build_report()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nPhase 8 controlled influence report written to: {output_path}")
    return 0 if report["passed"] else 1


def build_report() -> dict[str, Any]:
    scenarios = [
        scenario_default_off(),
        scenario_ranking_boost(),
        scenario_shadow_logs_only(),
        scenario_guarded_safe_override(),
        scenario_guarded_patch_override_blocked(),
    ]
    summary = {
        "default_mode_off": _scenario_passed(scenarios, "default_off"),
        "ranking_boost_existing_candidate_only": _scenario_passed(
            scenarios, "ranking_boost_existing_candidate_only"
        ),
        "shadow_does_not_override": _scenario_passed(scenarios, "shadow_logs_only"),
        "guarded_safe_override_allowed": _scenario_passed(
            scenarios, "guarded_safe_override_allowed"
        ),
        "guarded_patch_override_blocked": _scenario_passed(
            scenarios, "guarded_patch_override_blocked"
        ),
    }
    return {
        "phase": "phase8_controlled_influence",
        "passed": all(item["passed"] for item in scenarios),
        "summary": summary,
        "scenarios": scenarios,
        "target_invariants": {
            "advisor_can_influence_priority": True,
            "advisor_cannot_bypass_rules": True,
            "advisor_cannot_skip_verification": True,
            "advisor_cannot_directly_apply_patches": True,
            "kill_switch_default_off": True,
        },
    }


def scenario_default_off() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
        result = Orchestrator(
            workspace_root=root,
            proposer=StopProposer(),
            config=OrchestratorConfig(max_steps=1),
        ).run(goal="Inspect alpha")
    advisor_names = [item["name"] for item in result.trace["advisor_outputs"]]
    passed = (
        OrchestratorConfig().advisor_runtime_mode == "off"
        and "repair_runtime" not in advisor_names
        and result.trace["advisor_action_audits"] == []
    )
    return {
        "name": "default_off",
        "passed": passed,
        "advisor_runtime_mode": OrchestratorConfig().advisor_runtime_mode,
        "advisor_names": advisor_names,
        "advisor_action_audits": result.trace["advisor_action_audits"],
    }


def scenario_ranking_boost() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
        (root / "beta.py").write_text("print('beta')\n", encoding="utf-8")
        result = Orchestrator(
            workspace_root=root,
            proposer=StopProposer(),
            advisors=[
                StaticAdvisor(
                    AdvisorOutput(
                        name="file_relevance",
                        label="ranked_files",
                        confidence=0.7,
                        data={"top_files": ["alpha.py", "beta.py"]},
                    )
                ),
                StaticAdvisor(
                    AdvisorOutput(
                        name="repair_runtime",
                        label="runtime_signal",
                        confidence=0.9,
                        data={
                            "source_file": "beta.py",
                            "source_file_confidence": 0.9,
                            "next_action": {
                                "next_action": "switch_target_file",
                                "mapped_action": "read_file",
                                "path": "beta.py",
                                "confidence": 0.9,
                            },
                        },
                    )
                ),
            ],
            config=OrchestratorConfig(max_steps=1, advisor_runtime_mode="ranking_boost"),
        ).run(goal="Inspect alpha beta")
    passed = result.trace["candidate_files"][:2] == ["beta.py", "alpha.py"]
    return {
        "name": "ranking_boost_existing_candidate_only",
        "passed": passed,
        "candidate_files": result.trace["candidate_files"],
    }


def scenario_shadow_logs_only() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
        result = Orchestrator(
            workspace_root=root,
            proposer=ReadAlphaProposer(),
            advisors=[_run_tests_advisor()],
            config=OrchestratorConfig(max_steps=1, advisor_runtime_mode="shadow"),
        ).run(goal="Read alpha")
    audit = result.trace["advisor_action_audits"][0]
    passed = (
        result.trace["tool_history"][0]["action"] == "read_file"
        and audit["advisor_recommended"] == "run_tests"
        and audit["override_applied"] is False
    )
    return {
        "name": "shadow_logs_only",
        "passed": passed,
        "tool_actions": [item["action"] for item in result.trace["tool_history"]],
        "advisor_action_audits": result.trace["advisor_action_audits"],
    }


def scenario_guarded_safe_override() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
        (root / "test_alpha.py").write_text(
            "def test_alpha():\n    assert True\n",
            encoding="utf-8",
        )
        result = Orchestrator(
            workspace_root=root,
            proposer=ReadAlphaProposer(),
            advisors=[_run_tests_advisor()],
            config=OrchestratorConfig(max_steps=1, advisor_runtime_mode="guarded_action"),
        ).run(goal="Fix failing tests")
    audit = result.trace["advisor_action_audits"][0]
    passed = (
        result.trace["tool_history"][0]["action"] == "run_tests"
        and audit["override_applied"] is True
        and result.trace["tool_history"][0]["output"]["status"] == "passed"
    )
    return {
        "name": "guarded_safe_override_allowed",
        "passed": passed,
        "tool_actions": [item["action"] for item in result.trace["tool_history"]],
        "advisor_action_audits": result.trace["advisor_action_audits"],
    }


def scenario_guarded_patch_override_blocked() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
        result = Orchestrator(
            workspace_root=root,
            proposer=ReadAlphaProposer(),
            advisors=[
                StaticAdvisor(
                    AdvisorOutput(
                        name="repair_runtime",
                        label="runtime_signal",
                        confidence=1.0,
                        data={
                            "next_action": {
                                "next_action": "apply_patch",
                                "mapped_action": "apply_patch",
                                "confidence": 1.0,
                            }
                        },
                    )
                )
            ],
            config=OrchestratorConfig(max_steps=1, advisor_runtime_mode="guarded_action"),
        ).run(goal="Patch alpha")
    audit = result.trace["advisor_action_audits"][0]
    tool_actions = [item["action"] for item in result.trace["tool_history"]]
    passed = tool_actions == ["read_file"] and audit["override_applied"] is False
    return {
        "name": "guarded_patch_override_blocked",
        "passed": passed,
        "tool_actions": tool_actions,
        "advisor_action_audits": result.trace["advisor_action_audits"],
    }


def _run_tests_advisor() -> StaticAdvisor:
    return StaticAdvisor(
        AdvisorOutput(
            name="repair_runtime",
            label="runtime_signal",
            confidence=0.9,
            data={
                "next_action": {
                    "next_action": "run_tests",
                    "mapped_action": "run_tests",
                    "confidence": 0.9,
                }
            },
        )
    )


def _scenario_passed(scenarios: list[dict[str, Any]], name: str) -> bool:
    for scenario in scenarios:
        if scenario["name"] == name:
            return bool(scenario["passed"])
    return False


if __name__ == "__main__":
    raise SystemExit(main())
