from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from backend.app.project_artifacts import (
    ProjectArtifact,
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import content_hash
from backend.app.project_coordinator.contracts import (
    CoordinatorIntent,
    CoordinatorIntentType,
)
from backend.app.project_coordinator.evidence import (
    CoordinatorEvidenceError,
    build_handoff_evidence,
    build_work_unit_evidence,
    evidence_reference,
    validate_patch_operations,
)
from backend.app.project_coordinator.service import (
    CoordinatorIntentError,
    ProjectCoordinatorService,
)
from backend.app.project_scaffolding import (
    BlueprintNotFoundError,
    InvalidBlueprintIdentifierError,
    ProjectScaffoldingService,
    ScaffoldPersistenceService,
    ScaffoldRenderError,
    ScaffoldValidationError,
)

if TYPE_CHECKING:
    from backend.app.project_analysis.model_synthesis.orchestrator import (
        CanonicalProviderProfile,
        CanonicalSynthesisOrchestrator,
    )
from backend.app.project_repair import (
    CanonicalRepairService,
    CanonicalRepairServiceError,
    RepairCycleStatus,
)
from backend.app.project_repair.contracts import FailureEvidenceArtifact


class CoordinatorExecutionError(RuntimeError):
    pass


class CoordinatorPolicyBlock(CoordinatorExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class CoordinatorHandlerResult:
    artifact: ProjectArtifact
    command: ProjectCommand


class CoordinatorIntentHandler(Protocol):
    def prepare(self, intent: CoordinatorIntent, run) -> CoordinatorHandlerResult: ...


class _BoundHandler:
    def __init__(
        self,
        control: ProjectControlPlane,
        artifacts: ProjectArtifactStore,
        *,
        orchestrator: CanonicalSynthesisOrchestrator | None = None,
        provider_profile: CanonicalProviderProfile | None = None,
    ) -> None:
        self.control = control
        self.artifacts = artifacts
        self.orchestrator = orchestrator
        self.provider_profile = provider_profile

    def _synthesized_preview(
        self,
        intent: CoordinatorIntent,
        evidence: dict[str, Any],
        purpose: str,
    ) -> ProjectArtifact | None:
        """Durable, exactly-once model synthesis; None when not configured.

        The provider is invoked only from the claimed durable intent, and only
        ever produces a bounded preview artifact that still requires exact user
        approval.
        """
        if self.orchestrator is None or self.provider_profile is None:
            return None
        from backend.app.project_analysis.model_synthesis.orchestrator import (
            CanonicalSynthesisBlocked,
        )
        evidence_artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
            binding=self._binding(intent, authority={"purpose": f"{purpose}_synthesis_evidence"}),
            payload={"evidence": evidence},
        ))
        try:
            if purpose == "repair":
                outcome = self.orchestrator.prepare_repair(
                    intent, evidence_artifact, self.provider_profile
                )
            else:
                outcome = self.orchestrator.prepare_patch(
                    intent, evidence_artifact, self.provider_profile
                )
        except CanonicalSynthesisBlocked as exc:
            if exc.code == "gpu_busy":
                # GPU contention with another exclusive local-generation request
                # (see GPU Admission Unification) is transient infrastructure
                # contention, not a synthesis-domain failure -- let it propagate
                # as a plain RuntimeError so the executor's existing
                # infra-vs-domain classification retries it via
                # release_for_retry, instead of a terminal CoordinatorPolicyBlock.
                raise RuntimeError(
                    f"Bounded {purpose} synthesis is temporarily blocked by GPU contention."
                ) from exc
            raise CoordinatorPolicyBlock(
                f"Bounded {purpose} synthesis was blocked ({exc.code})."
            ) from exc
        if outcome.status != "prepared" or not outcome.artifact_id:
            raise CoordinatorPolicyBlock(
                f"Bounded {purpose} synthesis produced no approvable preview ({outcome.status})."
            )
        preview = self.artifacts.get(outcome.artifact_id)
        if preview is None or preview.content_hash != outcome.artifact_hash:
            raise CoordinatorPolicyBlock(
                "The synthesized preview artifact is missing or does not match its durable result."
            )
        return preview

    def _binding(
        self,
        intent: CoordinatorIntent,
        *,
        authority: dict[str, Any] | None = None,
    ) -> ProjectArtifactBinding:
        return ProjectArtifactBinding(
            project_run_id=intent.project_run_id,
            plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id,
            manifest_hash=intent.manifest_hash,
            coordinator_intent_id=intent.coordinator_intent_id,
            authority_hash=content_hash(authority or {}),
        )

    @staticmethod
    def _command(
        intent: CoordinatorIntent,
        run,
        artifact: ProjectArtifact,
        command_type: ProjectCommandType,
        *,
        payload: dict[str, Any],
        authority: dict[str, Any],
    ) -> ProjectCommand:
        return ProjectCommand(
            command_type=command_type,
            project_run_id=run.project_run_id,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id,
            expected_state_version=intent.expected_project_state_version,
            idempotency_key=f"coordinator:{intent.coordinator_intent_id}",
            plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id,
            manifest_hash=intent.manifest_hash,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type.value,
            artifact_hash=artifact.content_hash,
            artifact_binding_hash=artifact.binding_hash,
            authority_scope={
                **authority,
                "coordinator_intent_id": intent.coordinator_intent_id,
            },
            payload=payload,
        )


class PrepareWorkUnitHandler(_BoundHandler):
    def __init__(
        self,
        control: ProjectControlPlane,
        artifacts: ProjectArtifactStore,
        *,
        orchestrator: CanonicalSynthesisOrchestrator | None = None,
        provider_profile: CanonicalProviderProfile | None = None,
        scaffolding: ProjectScaffoldingService | None = None,
        scaffold_persistence: ScaffoldPersistenceService | None = None,
    ) -> None:
        super().__init__(
            control, artifacts, orchestrator=orchestrator, provider_profile=provider_profile,
        )
        self.scaffolding = scaffolding
        self.scaffold_persistence = scaffold_persistence

    def _scaffolded_preview(
        self,
        intent: CoordinatorIntent,
        run,
        work_unit_id: str,
        source_work_unit: dict[str, Any],
    ) -> tuple[ProjectArtifact, str] | None:
        """Deterministic, model-free patch generation tried before LLM synthesis.

        Returns None only when the deterministic route is genuinely not
        applicable -- scaffolding isn't wired in, no `scaffold_hint` is
        declared on the work unit, or no registered blueprint matches the
        hinted category -- in which case callers fall through to
        `_synthesized_preview` unchanged, so omitting scaffolding entirely
        (the default) preserves today's behavior exactly.

        Once a hinted category *does* resolve to a registered blueprint, the
        deterministic route is being attempted, not skipped: any subsequent
        validation, security, or integrity failure (bad/missing inputs,
        path traversal, conflicting destinations, unresolved placeholders,
        a tampered render) is raised as `CoordinatorPolicyBlock` -- the
        existing canonical fail-closed path -- rather than silently falling
        back to the model.
        """
        if self.scaffolding is None or self.scaffold_persistence is None:
            return None
        hint = source_work_unit.get("scaffold_hint")
        if not isinstance(hint, dict) or not hint.get("category"):
            return None
        category = str(hint["category"])
        inputs = {str(key): str(value) for key, value in dict(hint.get("inputs") or {}).items()}
        try:
            render_result = self.scaffolding.render(
                category=category, inputs=inputs, repository_root=run.repository_root,
            )
        except (BlueprintNotFoundError, InvalidBlueprintIdentifierError):
            # No registered blueprint matches this category at all -- the
            # deterministic route is genuinely not applicable, not attempted
            # and failed. Safe to fall back to model synthesis.
            return None
        except (ScaffoldValidationError, ScaffoldRenderError) as exc:
            # A supported category *was* matched and the deterministic route
            # was attempted, but it failed a validation/security/integrity
            # check. Fail closed: never substitute a silent model guess for
            # a rejected explicit scaffold request.
            raise CoordinatorPolicyBlock(
                f"Deterministic scaffold render for category {category!r} failed "
                f"validation ({type(exc).__name__}): {exc}"
            ) from exc
        operations = validate_patch_operations(render_result.operations)
        manifest = self.scaffold_persistence.persist(
            render_result,
            inputs,
            project_run_id=run.project_run_id,
            category=category,
            work_unit_id=work_unit_id,
            coordinator_intent_id=intent.coordinator_intent_id,
        )
        authority = {"work_unit_id": work_unit_id}
        patch_id = f"patch-{content_hash([intent.coordinator_intent_id, work_unit_id, operations])[:24]}"
        preview = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.PATCH_PREVIEW,
            binding=self._binding(intent, authority=authority),
            payload={
                "patch_id": patch_id,
                "work_unit_id": work_unit_id,
                "operations": list(operations),
                "requires_exact_approval": True,
                "scaffold_manifest_artifact_id": manifest.artifact_id,
                "scaffold_blueprint_id": render_result.blueprint_id,
            },
            evidence_references=(
                {
                    "artifact_id": manifest.artifact_id,
                    "artifact_type": manifest.artifact_type.value,
                    "content_hash": manifest.content_hash,
                },
            ),
        ))
        return preview, patch_id

    def prepare(self, intent: CoordinatorIntent, run) -> CoordinatorHandlerResult:
        plan = self.control.get_plan_revision(intent.plan_revision_id)
        work_unit = next(
            (
                item
                for item in plan.work_units
                if run.work_unit_state.get(str(item.get("work_unit_id") or item.get("id") or ""), {}).get("status")
                == "pending"
            ),
            None,
        )
        if work_unit is None:
            raise CoordinatorPolicyBlock("No pending work unit is bound to the current plan.")
        work_unit_id = str(work_unit.get("work_unit_id") or work_unit.get("id") or "")
        source_work_unit = dict(work_unit)
        plan_artifact_id = run.current_artifact_ids.get("plan")
        plan_artifact = self.artifacts.get(plan_artifact_id) if plan_artifact_id else None
        if plan_artifact is not None:
            source_work_unit = next(
                (
                    dict(item)
                    for item in plan_artifact.payload.get("work_units") or []
                    if isinstance(item, dict)
                    and str(item.get("work_unit_id") or item.get("id") or "")
                    == work_unit_id
                ),
                source_work_unit,
            )
        evidence = build_work_unit_evidence(
            intent=intent, run=run, plan=plan, work_unit=source_work_unit
        )
        raw_operations = (
            source_work_unit.get("patch_operations")
            or source_work_unit.get("changes")
            or source_work_unit.get("operations")
        )
        try:
            operations = validate_patch_operations(raw_operations)
        except CoordinatorEvidenceError as exc:
            scaffolded = self._scaffolded_preview(intent, run, work_unit_id, source_work_unit)
            if scaffolded is not None:
                preview, patch_id = scaffolded
                authority = {"work_unit_id": work_unit_id}
                command = self._command(
                    intent,
                    run,
                    preview,
                    ProjectCommandType.BEGIN_WORK_UNIT,
                    payload={"work_unit_id": work_unit_id, "patch_id": patch_id},
                    authority={**authority, "patch_id": patch_id},
                )
                return CoordinatorHandlerResult(preview, command)
            preview = self._synthesized_preview(intent, evidence, "patch")
            if preview is None:
                raise CoordinatorPolicyBlock(str(exc)) from exc
            authority = {"work_unit_id": work_unit_id}
            patch_id = f"patch-{content_hash([intent.coordinator_intent_id, work_unit_id, preview.artifact_id])[:24]}"
            command = self._command(
                intent,
                run,
                preview,
                ProjectCommandType.BEGIN_WORK_UNIT,
                payload={"work_unit_id": work_unit_id, "patch_id": patch_id},
                authority={**authority, "patch_id": patch_id},
            )
            return CoordinatorHandlerResult(preview, command)
        authority = {"work_unit_id": work_unit_id}
        patch_id = f"patch-{content_hash([intent.coordinator_intent_id, work_unit_id, operations])[:24]}"
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.PATCH_PREVIEW,
            binding=self._binding(intent, authority=authority),
            payload={
                "patch_id": patch_id,
                "work_unit_id": work_unit_id,
                "operations": list(operations),
                "evidence_hash": content_hash(evidence),
                "requires_exact_approval": True,
            },
            evidence_references=(evidence_reference(evidence),),
        ))
        command = self._command(
            intent,
            run,
            artifact,
            ProjectCommandType.BEGIN_WORK_UNIT,
            payload={"work_unit_id": work_unit_id, "patch_id": patch_id},
            authority={**authority, "patch_id": patch_id},
        )
        return CoordinatorHandlerResult(artifact, command)


class DeterministicVerificationHandler(_BoundHandler):
    _COMMAND_MODES = frozenset({
        "approved_command", "build", "existing_test", "lint", "new_test", "type_check",
    })

    def prepare(self, intent: CoordinatorIntent, run) -> CoordinatorHandlerResult:
        plan = self.control.get_plan_revision(intent.plan_revision_id)
        work_unit = self._active_work_unit(plan, run)
        work_unit_id = str(work_unit.get("work_unit_id") or work_unit.get("id") or "")
        criterion = self._next_criterion(plan, run, work_unit)
        criterion_id = str(criterion.get("criterion_id") or criterion.get("id") or "")
        mode = str(criterion.get("verification_mode") or "deterministic")
        authority = {"work_unit_id": work_unit_id, "criterion_id": criterion_id}

        if mode in self._COMMAND_MODES:
            return self._command_preview(intent, run, criterion, authority)

        outcome, checks = self._evaluate_non_executable(
            run=run, work_unit=work_unit, criterion=criterion
        )
        payload = {
            "criterion_id": criterion_id,
            "work_unit_id": work_unit_id,
            "outcome": outcome,
            "checks": checks,
            "criterion_hash": content_hash(criterion),
            "plan_revision_id": intent.plan_revision_id,
            "scope_revision_id": intent.scope_revision_id,
            "manifest_hash": intent.manifest_hash,
        }
        payload["result_hash"] = content_hash(payload)
        evidence_references: tuple[dict[str, Any], ...] = ()
        if outcome == "failed":
            failure_payload = FailureEvidenceArtifact(
                project_run_id=run.project_run_id,
                execution_attempt_id=f"verification-{intent.coordinator_intent_id}",
                failure_classification="deterministic_verification_failed",
                domain_failure=True,
                result_reference={"result_hash": payload["result_hash"], "checks": checks},
                authority=authority,
            )
            failure = self.artifacts.put(build_project_artifact(
                artifact_type=ProjectArtifactType.FAILURE_EVIDENCE,
                binding=ProjectArtifactBinding(
                    project_run_id=intent.project_run_id,
                    plan_revision_id=intent.plan_revision_id,
                    scope_revision_id=intent.scope_revision_id,
                    manifest_hash=intent.manifest_hash,
                    execution_attempt_id=failure_payload.execution_attempt_id,
                    coordinator_intent_id=intent.coordinator_intent_id,
                    authority_hash=content_hash(authority),
                ),
                payload=failure_payload.model_dump(mode="json"),
            ))
            payload["failure_artifact_id"] = failure.artifact_id
            evidence_references = ({
                "artifact_id": failure.artifact_id,
                "artifact_type": failure.artifact_type.value,
                "content_hash": failure.content_hash,
            },)
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.VERIFIER_RESULT,
            binding=self._binding(intent, authority=authority),
            payload=payload,
            evidence_references=evidence_references,
        ))
        command = self._command(
            intent,
            run,
            artifact,
            ProjectCommandType.RECORD_VERIFIER_RESULT,
            payload=payload,
            authority=authority,
        )
        return CoordinatorHandlerResult(artifact, command)

    def _command_preview(
        self,
        intent: CoordinatorIntent,
        run,
        criterion: dict[str, Any],
        authority: dict[str, Any],
    ) -> CoordinatorHandlerResult:
        raw = criterion.get("command") or (criterion.get("checker_rule") or {}).get("command")
        if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item for item in raw):
            raise CoordinatorPolicyBlock(
                "Executable verification requires an explicit argument-vector command."
            )
        command_id = f"command-{content_hash([intent.coordinator_intent_id, raw])[:24]}"
        payload = {
            "command_id": command_id,
            "criterion_id": authority["criterion_id"],
            "work_unit_id": authority["work_unit_id"],
            "argv": raw[:64],
            "working_directory": ".",
            "network": "disabled",
            "requires_exact_approval": True,
        }
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.COMMAND_PREVIEW,
            binding=self._binding(intent, authority=authority),
            payload=payload,
        ))
        command = self._command(
            intent,
            run,
            artifact,
            ProjectCommandType.RECORD_COMMAND_PREVIEW,
            payload=payload,
            authority={**authority, "command_id": command_id},
        )
        return CoordinatorHandlerResult(artifact, command)

    @staticmethod
    def _active_work_unit(plan, run) -> dict[str, Any]:
        active_ids = {
            key for key, value in run.work_unit_state.items()
            if value.get("status") == "in_progress"
        }
        unit = next(
            (
                item for item in plan.work_units
                if str(item.get("work_unit_id") or item.get("id") or "") in active_ids
            ),
            None,
        )
        if unit is None:
            raise CoordinatorPolicyBlock("Verification has no active work unit.")
        return dict(unit)

    @staticmethod
    def _next_criterion(plan, run, work_unit: dict[str, Any]) -> dict[str, Any]:
        configured = (
            work_unit.get("acceptance_criteria_ids")
            or work_unit.get("criterion_references")
            or work_unit.get("acceptance_criteria")
        )
        allowed = {str(item) for item in configured} if isinstance(configured, (list, tuple)) else None
        for item in plan.acceptance_criteria:
            criterion_id = str(item.get("criterion_id") or item.get("id") or "")
            if allowed is not None and criterion_id not in allowed:
                continue
            state = run.verification_state.get(criterion_id) or {}
            if state.get("outcome") != "passed":
                return dict(item)
        raise CoordinatorPolicyBlock("No pending acceptance criterion remains for the active work unit.")

    @staticmethod
    def _evaluate_non_executable(
        *, run, work_unit: dict[str, Any], criterion: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        mode = str(criterion.get("verification_mode") or "deterministic")
        if mode == "manual_user_verification_required":
            return "manual_required", [{"check": "manual", "passed": False}]
        supported = {
            "deterministic", "file_presence", "structural_code_inspection",
            "configuration", "exact_diff_or_content_assertion",
        }
        if mode not in supported:
            raise CoordinatorPolicyBlock(f"No non-executable verifier exists for {mode}.")
        paths = list(work_unit.get("expected_files") or [])
        rule = criterion.get("checker_rule") or {}
        if rule.get("relative_path"):
            paths = [rule["relative_path"]]
        if not paths:
            raise CoordinatorPolicyBlock("Deterministic verification requires bounded expected files.")
        root = Path(run.repository_root).resolve()
        checks: list[dict[str, Any]] = []
        for raw_path in paths[:100]:
            candidate = (root / str(raw_path)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise CoordinatorPolicyBlock("Verifier path escapes the repository root.") from exc
            passed = candidate.is_file()
            detail = "present" if passed else "missing"
            if passed and mode == "structural_code_inspection":
                try:
                    if candidate.suffix == ".py":
                        ast.parse(candidate.read_text(encoding="utf-8"))
                    elif candidate.suffix == ".json":
                        json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, SyntaxError, UnicodeError, json.JSONDecodeError):
                    passed = False
                    detail = "parse_failed"
            checks.append({"path": str(raw_path), "passed": passed, "detail": detail})
        return ("passed" if all(item["passed"] for item in checks) else "failed"), checks


class PrepareHandoffHandler(_BoundHandler):
    def prepare(self, intent: CoordinatorIntent, run) -> CoordinatorHandlerResult:
        plan = self.control.get_plan_revision(intent.plan_revision_id)
        evidence = build_handoff_evidence(intent=intent, run=run, plan=plan)
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.HANDOFF,
            binding=self._binding(intent, authority={"operation": "handoff"}),
            payload={
                **evidence,
                "handoff_status": "ready_for_exact_finalization",
            },
            evidence_references=(evidence_reference(evidence),),
        ))
        command = self._command(
            intent,
            run,
            artifact,
            ProjectCommandType.REQUEST_HANDOFF,
            payload={
                "final_manifest_hash": intent.manifest_hash,
                "handoff_artifact_id": artifact.artifact_id,
            },
            authority={"handoff_artifact_id": artifact.artifact_id},
        )
        return CoordinatorHandlerResult(artifact, command)


class PrepareRepairHandler(_BoundHandler):
    def __init__(
        self,
        control: ProjectControlPlane,
        artifacts: ProjectArtifactStore,
        repair: CanonicalRepairService,
        *,
        orchestrator: CanonicalSynthesisOrchestrator | None = None,
        provider_profile: CanonicalProviderProfile | None = None,
    ) -> None:
        super().__init__(
            control,
            artifacts,
            orchestrator=orchestrator,
            provider_profile=provider_profile,
        )
        self.repair = repair

    def prepare(self, intent: CoordinatorIntent, run) -> CoordinatorHandlerResult:
        failure_artifact_id = str(
            run.repair_state.get("failure_artifact_id")
            or run.current_artifact_ids.get("failure_evidence")
            or ""
        )
        if not failure_artifact_id:
            raise CoordinatorPolicyBlock("Repair preparation lacks canonical failure evidence.")
        failure = self.artifacts.get(failure_artifact_id)
        if failure is None or failure.artifact_type != ProjectArtifactType.FAILURE_EVIDENCE:
            raise CoordinatorPolicyBlock("Canonical failure evidence is missing or invalid.")
        plan = self.control.get_plan_revision(intent.plan_revision_id)
        work_unit = next(
            (
                item for item in plan.work_units
                if run.work_unit_state.get(str(item.get("work_unit_id") or item.get("id") or ""), {}).get("status")
                == "in_progress"
            ),
            None,
        )
        if work_unit is None:
            raise CoordinatorPolicyBlock("Repair preparation has no active work unit.")
        work_unit_id = str(work_unit.get("work_unit_id") or work_unit.get("id") or "")
        try:
            cycle = self.repair.begin_diagnosis(
                project_run_id=run.project_run_id,
                failure_artifact_id=failure_artifact_id,
                work_unit_id=work_unit_id,
            )
        except CanonicalRepairServiceError as exc:
            raise CoordinatorPolicyBlock(str(exc)) from exc

        raw_work_unit = dict(work_unit)
        plan_artifact_id = run.current_artifact_ids.get("plan")
        plan_artifact = self.artifacts.get(plan_artifact_id) if plan_artifact_id else None
        if plan_artifact is not None:
            raw_work_unit = next(
                (
                    dict(item)
                    for item in plan_artifact.payload.get("work_units") or []
                    if isinstance(item, dict)
                    and str(item.get("work_unit_id") or item.get("id") or "")
                    == work_unit_id
                ),
                raw_work_unit,
            )
        raw_operations = (
            raw_work_unit.get("repair_operations")
            or failure.payload.get("suggested_repair_operations")
        )
        evidence = build_work_unit_evidence(
            intent=intent,
            run=run,
            plan=plan,
            work_unit={
                **raw_work_unit,
                "failure_evidence": failure.payload,
                "failure_artifact_id": failure_artifact_id,
            },
        )
        diagnosis_strategy = "deterministic"
        diagnosis_confidence = "high"
        try:
            operations = validate_patch_operations(raw_operations)
        except CoordinatorEvidenceError as exc:
            synthesized = self._synthesized_preview(intent, evidence, "repair")
            if synthesized is None:
                raise CoordinatorPolicyBlock(
                    "No deterministic bounded repair is available and synthesis is not configured."
                ) from exc
            operations = validate_patch_operations(synthesized.payload.get("operations"))
            diagnosis_strategy = "model_assisted"
            diagnosis_confidence = "bounded"
        repair_scope = tuple(dict.fromkeys(
            str(item.get("path") or "") for item in operations if item.get("path")
        ))
        diagnosis = self.repair.record_diagnosis(
            cycle,
            coordinator_intent_id=intent.coordinator_intent_id,
            root_causes=({
                "reason_code": str(failure.payload.get("failure_classification") or "domain_failure"),
                "explanation": "The approved execution failed and a bounded repair operation is present in the immutable plan.",
                "evidence_artifact_id": failure_artifact_id,
            },),
            repair_scope=repair_scope,
            strategy=diagnosis_strategy,
            confidence=diagnosis_confidence,
        )
        patch_id = f"repair-patch-{content_hash([cycle.repair_cycle_id, operations])[:24]}"
        preview = self.repair.record_preview(
            cycle,
            coordinator_intent_id=intent.coordinator_intent_id,
            diagnosis_artifact_id=diagnosis.artifact_id,
            patch_id=patch_id,
            operations=operations,
        )
        authority = {
            "repair_cycle_id": cycle.repair_cycle_id,
            "failure_artifact_id": failure_artifact_id,
            "patch_id": patch_id,
            "work_unit_id": work_unit_id,
        }
        command = self._command(
            intent,
            run,
            preview,
            ProjectCommandType.INITIATE_REPAIR,
            payload={
                **authority,
                "diagnosis_artifact_id": diagnosis.artifact_id,
            },
            authority=authority,
        )
        return CoordinatorHandlerResult(preview, command)


class ProjectCoordinatorExecutor:
    """Claims one durable intent and emits at most one artifact-bound command."""

    def __init__(
        self,
        service: ProjectCoordinatorService,
        control: ProjectControlPlane,
        artifacts: ProjectArtifactStore,
        *,
        handlers: dict[CoordinatorIntentType, CoordinatorIntentHandler] | None = None,
        projector=None,
        orchestrator: CanonicalSynthesisOrchestrator | None = None,
        provider_profile: CanonicalProviderProfile | None = None,
        scaffolding: ProjectScaffoldingService | None = None,
        scaffold_persistence: ScaffoldPersistenceService | None = None,
    ) -> None:
        self.service = service
        self.control = control
        self.artifacts = artifacts
        self.projector = projector
        self.orchestrator = orchestrator
        self.provider_profile = provider_profile
        repair = CanonicalRepairService(service.database_path, control, artifacts)
        repair.initialize()
        self.repair = repair
        self.handlers = handlers or {
            CoordinatorIntentType.PREPARE_WORK_UNIT: PrepareWorkUnitHandler(
                control, artifacts,
                orchestrator=orchestrator, provider_profile=provider_profile,
                scaffolding=scaffolding, scaffold_persistence=scaffold_persistence,
            ),
            CoordinatorIntentType.RUN_DETERMINISTIC_VERIFICATION: DeterministicVerificationHandler(control, artifacts),
            CoordinatorIntentType.PREPARE_REPAIR: PrepareRepairHandler(
                control,
                artifacts,
                repair,
                orchestrator=orchestrator,
                provider_profile=provider_profile,
            ),
            CoordinatorIntentType.PREPARE_HANDOFF: PrepareHandoffHandler(control, artifacts),
        }

    def run_once(self, worker_id: str) -> bool:
        intent = self.service.claim_next(worker_id, lease_seconds=60)
        if intent is None:
            return False
        lease_token = str(intent.lease_token or "")
        try:
            run = self.control.get_project(intent.project_run_id)
            if not self.service._binding_is_current(intent, run):
                if self._recover_applied(intent, worker_id, lease_token):
                    return True
                self.service.cancel_stale_for_project(intent.project_run_id)
                self.service.fail(
                    intent.coordinator_intent_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    failure_classification="stale_project_binding",
                )
                self.service.reconcile(intent.project_run_id)
                return True
            handler = self.handlers.get(intent.intent_type)
            if handler is None:
                raise CoordinatorPolicyBlock(
                    f"No coordinator handler is enabled for {intent.intent_type.value}."
                )
            result = handler.prepare(intent, run)
            transition = self.control.execute(result.command)
            refreshed = self.control.get_project(intent.project_run_id)
            repair_cycle_id = str(refreshed.repair_state.get("repair_cycle_id") or "")
            if (
                repair_cycle_id
                and result.command.command_type == ProjectCommandType.RECORD_VERIFIER_RESULT
            ):
                self.repair.finish_cycle(
                    repair_cycle_id,
                    status=(
                        RepairCycleStatus.COMPLETED
                        if result.command.payload.get("outcome") == "passed"
                        else RepairCycleStatus.FAILED
                    ),
                )
            completed = self.service.complete(
                intent.coordinator_intent_id,
                worker_id=worker_id,
                lease_token=lease_token,
                result_reference={
                    "artifact_id": result.artifact.artifact_id,
                    "artifact_hash": result.artifact.content_hash,
                    "event_id": transition.event_id,
                    "command_type": result.command.command_type.value,
                },
            )
            if completed.result_reference is None:
                raise CoordinatorExecutionError("Coordinator completion lost its result reference.")
            self._project_and_reconcile(intent.project_run_id)
            return True
        except (CoordinatorPolicyBlock, CoordinatorEvidenceError) as exc:
            self._record_policy_block(intent, worker_id, lease_token, str(exc))
            return True
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, (ProjectControlError, CoordinatorIntentError)):
                raise
            self.service.release_for_retry(
                intent.coordinator_intent_id,
                worker_id=worker_id,
                lease_token=lease_token,
                failure_classification=f"infrastructure:{type(exc).__name__}",
            )
            return False

    def _record_policy_block(
        self,
        intent: CoordinatorIntent,
        worker_id: str,
        lease_token: str,
        reason: str,
    ) -> None:
        run = self.control.get_project(intent.project_run_id)
        authority = {"coordinator_intent_id": intent.coordinator_intent_id}
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
            binding=ProjectArtifactBinding(
                project_run_id=intent.project_run_id,
                plan_revision_id=intent.plan_revision_id,
                scope_revision_id=intent.scope_revision_id,
                manifest_hash=intent.manifest_hash,
                coordinator_intent_id=intent.coordinator_intent_id,
                authority_hash=content_hash(authority),
            ),
            payload={"decision": "blocked", "reason": " ".join(reason.split())[:1000]},
        ))
        command = _BoundHandler._command(
            intent,
            run,
            artifact,
            ProjectCommandType.MARK_BLOCKED,
            payload={"reason": " ".join(reason.split())[:1000], "pending_user_action": "revise_plan"},
            authority=authority,
        )
        transition = self.control.execute(command)
        self.service.complete(
            intent.coordinator_intent_id,
            worker_id=worker_id,
            lease_token=lease_token,
            result_reference={
                "artifact_id": artifact.artifact_id,
                "artifact_hash": artifact.content_hash,
                "event_id": transition.event_id,
                "command_type": command.command_type.value,
            },
        )
        self._project_and_reconcile(intent.project_run_id)

    def _recover_applied(
        self,
        intent: CoordinatorIntent,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        request_id = f"coordinator:{intent.coordinator_intent_id}"
        event = next(
            (item for item in self.control.list_events(intent.project_run_id) if item.request_id == request_id),
            None,
        )
        artifact = next(
            (
                item for item in self.artifacts.list_for_project(intent.project_run_id)
                if item.binding.coordinator_intent_id == intent.coordinator_intent_id
            ),
            None,
        )
        if event is None or artifact is None:
            return False
        self.service.complete(
            intent.coordinator_intent_id,
            worker_id=worker_id,
            lease_token=lease_token,
            result_reference={
                "artifact_id": artifact.artifact_id,
                "artifact_hash": artifact.content_hash,
                "event_id": event.event_id,
                "recovered": True,
            },
        )
        self._project_and_reconcile(intent.project_run_id)
        return True

    def _project_and_reconcile(self, project_run_id: str) -> None:
        if self.projector is not None:
            self.projector.rebuild_project(project_run_id)
        self.service.reconcile(project_run_id)


__all__ = [
    "CoordinatorExecutionError",
    "CoordinatorHandlerResult",
    "CoordinatorIntentHandler",
    "CoordinatorPolicyBlock",
    "DeterministicVerificationHandler",
    "PrepareHandoffHandler",
    "PrepareRepairHandler",
    "PrepareWorkUnitHandler",
    "ProjectCoordinatorExecutor",
]
