from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliveryLimits:
    max_work_units: int = 20
    max_acceptance_criteria: int = 20
    max_clarifications: int = 3
    max_specification_revisions: int = 4
    max_plan_revisions: int = 4
    max_applied_patches: int = 10
    max_command_executions: int = 15
    max_scope_change_cycles: int = 3
    max_planning_model_calls: int = 2
    max_verification_model_calls: int = 2
    max_model_evidence_items: int = 20
    max_handoff_file_references: int = 50
    max_error_chars: int = 4_000
    max_output_chars: int = 12_000
    max_audit_metadata_chars: int = 4_000


STAGE9_LIMITS = DeliveryLimits()


__all__ = ["DeliveryLimits", "STAGE9_LIMITS"]
