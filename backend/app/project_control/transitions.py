from __future__ import annotations

from backend.app.project_control.contracts import ProjectCommandType, ProjectLifecycle, TERMINAL_LIFECYCLES
from backend.app.project_control.errors import ProjectControlError, ProjectControlErrorCode


LEGAL_TRANSITIONS: dict[ProjectLifecycle, frozenset[ProjectLifecycle]] = {
    ProjectLifecycle.SPECIFICATION_PENDING: frozenset({
        ProjectLifecycle.CLARIFICATION_REQUIRED, ProjectLifecycle.MANIFEST_REQUIRED,
        ProjectLifecycle.PLANNING, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.CLARIFICATION_REQUIRED: frozenset({
        ProjectLifecycle.MANIFEST_REQUIRED, ProjectLifecycle.PLANNING,
        ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.MANIFEST_REQUIRED: frozenset({
        ProjectLifecycle.PLANNING, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.PLANNING: frozenset({
        ProjectLifecycle.AWAITING_PLAN_APPROVAL, ProjectLifecycle.CLARIFICATION_REQUIRED,
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.AWAITING_PLAN_APPROVAL: frozenset({
        ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.PLANNING,
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.READY_FOR_WORK: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.AWAITING_PATCH_APPROVAL,
        ProjectLifecycle.VERIFICATION_PENDING,
        ProjectLifecycle.PLANNING, ProjectLifecycle.SCOPE_CHANGE_REQUIRED,
        ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.WORK_IN_PROGRESS: frozenset({
        ProjectLifecycle.AWAITING_PATCH_APPROVAL, ProjectLifecycle.AWAITING_COMMAND_APPROVAL,
        ProjectLifecycle.VERIFICATION_PENDING, ProjectLifecycle.REPAIR_REQUIRED,
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.ROLLBACK_PENDING,
        ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.PLANNING, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.AWAITING_PATCH_APPROVAL: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.SCOPE_CHANGE_REQUIRED,
        ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.AWAITING_COMMAND_APPROVAL: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.VERIFICATION_PENDING,
        ProjectLifecycle.REPAIR_REQUIRED, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.VERIFICATION_PENDING: frozenset({
        ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.REPAIR_REQUIRED,
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.HANDOFF_READY,
        ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.REPAIR_REQUIRED: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.AWAITING_PATCH_APPROVAL,
        ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.PLANNING, ProjectLifecycle.ROLLBACK_PENDING,
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.SCOPE_CHANGE_REQUIRED: frozenset({
        ProjectLifecycle.PLANNING, ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.ROLLBACK_PENDING: frozenset({
        ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.REPAIR_REQUIRED,
        ProjectLifecycle.BLOCKED, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.BLOCKED: frozenset({
        ProjectLifecycle.CLARIFICATION_REQUIRED, ProjectLifecycle.PLANNING,
        ProjectLifecycle.REPAIR_REQUIRED, ProjectLifecycle.ROLLBACK_PENDING,
        ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.HANDOFF_READY: frozenset({
        ProjectLifecycle.HANDED_OFF, ProjectLifecycle.COMPLETED,
        ProjectLifecycle.VERIFICATION_PENDING, ProjectLifecycle.CANCELLED,
    }),
    ProjectLifecycle.HANDED_OFF: frozenset({ProjectLifecycle.COMPLETED}),
    ProjectLifecycle.CANCELLED: frozenset(),
    ProjectLifecycle.COMPLETED: frozenset(),
}


COMMAND_SOURCES: dict[ProjectCommandType, frozenset[ProjectLifecycle]] = {
    ProjectCommandType.ATTACH_SPECIFICATION: frozenset({ProjectLifecycle.SPECIFICATION_PENDING, ProjectLifecycle.CLARIFICATION_REQUIRED}),
    ProjectCommandType.REGISTER_MANIFEST: frozenset({ProjectLifecycle.MANIFEST_REQUIRED, ProjectLifecycle.SPECIFICATION_PENDING, ProjectLifecycle.CLARIFICATION_REQUIRED}),
    ProjectCommandType.PROPOSE_PLAN_REVISION: frozenset({ProjectLifecycle.PLANNING}),
    ProjectCommandType.APPROVE_PLAN: frozenset({ProjectLifecycle.AWAITING_PLAN_APPROVAL}),
    ProjectCommandType.BEGIN_WORK_UNIT: frozenset({ProjectLifecycle.READY_FOR_WORK}),
    ProjectCommandType.RECORD_PATCH_PREVIEW: frozenset({ProjectLifecycle.WORK_IN_PROGRESS}),
    ProjectCommandType.APPROVE_PATCH: frozenset({ProjectLifecycle.AWAITING_PATCH_APPROVAL}),
    ProjectCommandType.BEGIN_PATCH_APPLICATION: frozenset({ProjectLifecycle.WORK_IN_PROGRESS}),
    ProjectCommandType.RECORD_PATCH_RESULT: frozenset({ProjectLifecycle.WORK_IN_PROGRESS}),
    ProjectCommandType.RECORD_COMMAND_PREVIEW: frozenset({ProjectLifecycle.WORK_IN_PROGRESS}),
    ProjectCommandType.APPROVE_COMMAND: frozenset({
        ProjectLifecycle.AWAITING_COMMAND_APPROVAL, ProjectLifecycle.WORK_IN_PROGRESS,
        ProjectLifecycle.VERIFICATION_PENDING,
    }),
    ProjectCommandType.BEGIN_COMMAND_EXECUTION: frozenset({ProjectLifecycle.WORK_IN_PROGRESS}),
    ProjectCommandType.RECORD_COMMAND_RESULT: frozenset({ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.AWAITING_COMMAND_APPROVAL}),
    ProjectCommandType.REQUEST_VERIFICATION: frozenset({ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.READY_FOR_WORK}),
    ProjectCommandType.RECORD_VERIFIER_RESULT: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS,
        ProjectLifecycle.VERIFICATION_PENDING,
    }),
    ProjectCommandType.REQUEST_CLARIFICATION: frozenset(set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES)),
    ProjectCommandType.MARK_BLOCKED: frozenset(set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES) - {ProjectLifecycle.HANDED_OFF}),
    ProjectCommandType.REVISE_SCOPE: frozenset({
        ProjectLifecycle.SCOPE_CHANGE_REQUIRED, ProjectLifecycle.PLANNING,
        ProjectLifecycle.AWAITING_PLAN_APPROVAL, ProjectLifecycle.READY_FOR_WORK,
        ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.REPAIR_REQUIRED, ProjectLifecycle.BLOCKED,
    }),
    ProjectCommandType.INITIATE_REPAIR: frozenset({ProjectLifecycle.REPAIR_REQUIRED, ProjectLifecycle.BLOCKED}),
    ProjectCommandType.RECORD_ROLLBACK_PREVIEW: frozenset({
        ProjectLifecycle.WORK_IN_PROGRESS,
        ProjectLifecycle.REPAIR_REQUIRED,
        ProjectLifecycle.BLOCKED,
    }),
    ProjectCommandType.APPROVE_ROLLBACK: frozenset({ProjectLifecycle.ROLLBACK_PENDING}),
    ProjectCommandType.BEGIN_ROLLBACK: frozenset({ProjectLifecycle.ROLLBACK_PENDING}),
    ProjectCommandType.RECORD_ROLLBACK: frozenset({ProjectLifecycle.ROLLBACK_PENDING, ProjectLifecycle.REPAIR_REQUIRED}),
    ProjectCommandType.COMPLETE_WORK_UNIT: frozenset({ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.VERIFICATION_PENDING}),
    ProjectCommandType.REQUEST_HANDOFF: frozenset({ProjectLifecycle.VERIFICATION_PENDING, ProjectLifecycle.READY_FOR_WORK}),
    ProjectCommandType.FINALIZE_PROJECT: frozenset({ProjectLifecycle.HANDOFF_READY, ProjectLifecycle.HANDED_OFF}),
    ProjectCommandType.CANCEL_PROJECT: frozenset(set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES) - {ProjectLifecycle.HANDED_OFF}),
    ProjectCommandType.ACKNOWLEDGE_EXECUTION_CANCELLATION: frozenset(
        set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES) - {ProjectLifecycle.HANDED_OFF}
    ),
    ProjectCommandType.RECOVER_ATTEMPT: frozenset(set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES)),
    ProjectCommandType.RECONCILE_LEGACY: frozenset(set(ProjectLifecycle) - set(TERMINAL_LIFECYCLES)),
    ProjectCommandType.INITIALIZE_PROJECT: frozenset(),
}


def validate_command_source(command: ProjectCommandType, current: ProjectLifecycle) -> None:
    if current in TERMINAL_LIFECYCLES:
        raise ProjectControlError(
            ProjectControlErrorCode.TERMINAL_PROJECT,
            "The project is terminal and cannot accept another mutation.",
            metadata={"lifecycle_state": current.value},
        )
    if current not in COMMAND_SOURCES.get(command, frozenset()):
        raise ProjectControlError(
            ProjectControlErrorCode.ILLEGAL_TRANSITION,
            "The requested command is not legal from the current project state.",
            metadata={"command": command.value, "lifecycle_state": current.value},
        )


def validate_transition(previous: ProjectLifecycle, resulting: ProjectLifecycle) -> None:
    if previous == resulting:
        return
    if resulting not in LEGAL_TRANSITIONS[previous]:
        raise ProjectControlError(
            ProjectControlErrorCode.ILLEGAL_TRANSITION,
            "The requested lifecycle transition is not legal.",
            metadata={"previous": previous.value, "resulting": resulting.value},
        )
