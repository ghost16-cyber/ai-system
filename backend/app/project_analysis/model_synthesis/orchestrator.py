from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.app.project_artifacts import (
    ProjectArtifact, ProjectArtifactBinding, ProjectArtifactStore, ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_analysis.model_synthesis.contracts import (
    SynthesisResponse, parse_synthesis_response, response_contract_description,
)
from backend.app.project_analysis.model_synthesis.gateway import SynthesisGateway, SynthesisGatewayError
from backend.app.project_control.contracts import StrictModel, canonical_json, content_hash
from backend.app.project_coordinator.contracts import CoordinatorIntent, CoordinatorIntentType
from backend.app.project_models import (
    ProjectModelInvocationStatus, ProjectModelInvocationStore, build_project_model_invocation,
)


MAX_CANONICAL_EVIDENCE_BYTES = 196_608


class CanonicalProviderProfile(StrictModel):
    schema_version: Literal["astra.project-synthesis.provider-profile.v1"] = "astra.project-synthesis.provider-profile.v1"
    provider: str = Field(min_length=1, max_length=120)
    model_profile: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class CanonicalSynthesisOutcome(StrictModel):
    schema_version: Literal["astra.project-synthesis.outcome.v1"] = "astra.project-synthesis.outcome.v1"
    status: Literal["prepared", "clarification_required"]
    invocation_id: str
    artifact_id: str | None = None
    artifact_hash: str | None = None
    clarification: str | None = None
    replayed: bool = False


class CanonicalSynthesisBlocked(RuntimeError):
    def __init__(self, message: str, *, code: str, invocation_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.invocation_id = invocation_id


class CanonicalSynthesisOrchestrator:
    """Provider-neutral, durable synthesis that can only create preview artifacts."""

    def __init__(
        self,
        *,
        invocations: ProjectModelInvocationStore,
        artifacts: ProjectArtifactStore,
        gateway: SynthesisGateway,
        lease_owner: str = "canonical-synthesis",
    ) -> None:
        self.invocations = invocations
        self.artifacts = artifacts
        self.gateway = gateway
        self.lease_owner = lease_owner

    def prepare_patch(
        self,
        intent: CoordinatorIntent,
        evidence_artifact: ProjectArtifact,
        provider_profile: CanonicalProviderProfile,
    ) -> CanonicalSynthesisOutcome:
        if intent.intent_type != CoordinatorIntentType.PREPARE_WORK_UNIT:
            raise CanonicalSynthesisBlocked("Patch synthesis requires a work-unit preparation intent.", code="intent_mismatch")
        return self._prepare("patch", intent, evidence_artifact, provider_profile)

    def prepare_repair(
        self,
        intent: CoordinatorIntent,
        evidence_artifact: ProjectArtifact,
        provider_profile: CanonicalProviderProfile,
    ) -> CanonicalSynthesisOutcome:
        if intent.intent_type != CoordinatorIntentType.PREPARE_REPAIR:
            raise CanonicalSynthesisBlocked("Repair synthesis requires a repair preparation intent.", code="intent_mismatch")
        return self._prepare("repair", intent, evidence_artifact, provider_profile)

    def _prepare(
        self,
        purpose: Literal["patch", "repair"],
        intent: CoordinatorIntent,
        evidence_artifact: ProjectArtifact,
        provider_profile: CanonicalProviderProfile,
    ) -> CanonicalSynthesisOutcome:
        self._validate_binding(intent, evidence_artifact)
        if not provider_profile.enabled:
            raise CanonicalSynthesisBlocked("The configured synthesis provider is disabled.", code="provider_disabled")
        if provider_profile.provider != self.gateway.provider or provider_profile.model_profile != self.gateway.model:
            raise CanonicalSynthesisBlocked("The gateway does not match the exact provider profile.", code="provider_profile_mismatch")
        evidence = evidence_artifact.payload.get("evidence", evidence_artifact.payload)
        if not isinstance(evidence, dict):
            raise CanonicalSynthesisBlocked("Canonical synthesis evidence must be a structured object.", code="invalid_evidence")
        if len(canonical_json(evidence).encode("utf-8")) > MAX_CANONICAL_EVIDENCE_BYTES:
            raise CanonicalSynthesisBlocked("Canonical synthesis evidence exceeds the byte limit.", code="evidence_too_large")
        evidence_hash = content_hash(evidence)
        request_id = f"synthesis-{content_hash([intent.coordinator_intent_id, purpose, evidence_hash])[:24]}"
        request_payload = {
            "contract_version": "astra.canonical-project-synthesis.request.v1",
            "request_id": request_id,
            "project_run_id": intent.project_run_id,
            "coordinator_intent_id": intent.coordinator_intent_id,
            "purpose": purpose,
            "plan_revision_id": intent.plan_revision_id,
            "scope_revision_id": intent.scope_revision_id,
            "manifest_hash": intent.manifest_hash,
            "expected_project_state_version": intent.expected_project_state_version,
            "evidence_artifact_id": evidence_artifact.artifact_id,
            "evidence_artifact_hash": evidence_artifact.content_hash,
            "evidence": evidence,
            "provider_profile": provider_profile.model_dump(mode="json"),
            "output_contract": response_contract_description(),
            "project_rag_enabled": False,
        }
        candidate = build_project_model_invocation(
            project_run_id=intent.project_run_id,
            coordinator_intent_id=intent.coordinator_intent_id,
            purpose=f"prepare_{purpose}", evidence_hash=evidence_hash,
            provider=provider_profile.provider, model_profile=provider_profile.model_profile,
            request_payload=request_payload,
            idempotency_key=f"canonical-synthesis:{intent.coordinator_intent_id}:{purpose}:{evidence_hash}",
        )
        invocation = self.invocations.create(candidate)
        if invocation.status == ProjectModelInvocationStatus.SUCCEEDED:
            return self._replay(invocation)
        if invocation.status == ProjectModelInvocationStatus.FAILED:
            raise CanonicalSynthesisBlocked(
                invocation.error_message or "The durable synthesis invocation failed.",
                code=invocation.failure_classification or "provider_failure",
                invocation_id=invocation.invocation_id,
            )
        if invocation.status == ProjectModelInvocationStatus.CLAIMED:
            raise CanonicalSynthesisBlocked(
                "The exact synthesis invocation is already leased; it will not be submitted twice.",
                code="invocation_in_progress", invocation_id=invocation.invocation_id,
            )
        claimed, token = self.invocations.claim(invocation.invocation_id, lease_owner=self.lease_owner)
        try:
            generated = self.gateway.generate(canonical_json(request_payload))
            response = parse_synthesis_response(generated.raw_response)
            if response.request_id != request_id:
                raise CanonicalSynthesisBlocked("The provider response is bound to a different request.", code="stale_provider_response")
            if response.requires_clarification:
                question = self._safe_clarification(response)
                completed = self.invocations.succeed(
                    claimed.invocation_id, lease_owner=self.lease_owner, lease_token=token,
                    result_payload={"status": "clarification_required", "question": question},
                    result_reference={"status": "clarification_required", "clarification": question},
                    usage=generated.usage,
                )
                return CanonicalSynthesisOutcome(
                    status="clarification_required", invocation_id=completed.invocation_id,
                    clarification=question,
                )
            operations = self._validate_operations(response, evidence)
            artifact_type = ProjectArtifactType.REPAIR_PREVIEW if purpose == "repair" else ProjectArtifactType.PATCH_PREVIEW
            artifact = self.artifacts.put(build_project_artifact(
                artifact_type=artifact_type,
                binding=ProjectArtifactBinding(
                    project_run_id=intent.project_run_id,
                    plan_revision_id=intent.plan_revision_id,
                    scope_revision_id=intent.scope_revision_id,
                    manifest_hash=intent.manifest_hash,
                    coordinator_intent_id=intent.coordinator_intent_id,
                    authority_hash=content_hash({
                        "purpose": purpose, "evidence_artifact_id": evidence_artifact.artifact_id,
                        "evidence_artifact_hash": evidence_artifact.content_hash,
                        "provider_profile_hash": content_hash(provider_profile.model_dump(mode="json")),
                    }),
                ),
                payload={
                    "purpose": purpose, "request_id": request_id,
                    "invocation_id": claimed.invocation_id, "summary": response.summary,
                    "operations": operations, "assumptions": response.assumptions,
                    "uncertainties": response.uncertainties,
                    "provider": generated.provider, "model_profile": generated.model,
                    "evidence_hash": evidence_hash, "requires_exact_approval": True,
                    "project_rag_enabled": False,
                },
                evidence_references=({
                    "artifact_id": evidence_artifact.artifact_id,
                    "artifact_type": evidence_artifact.artifact_type.value,
                    "content_hash": evidence_artifact.content_hash,
                },),
            ))
            completed = self.invocations.succeed(
                claimed.invocation_id, lease_owner=self.lease_owner, lease_token=token,
                result_payload={"artifact_id": artifact.artifact_id, "artifact_hash": artifact.content_hash},
                result_reference={
                    "status": "prepared", "artifact_id": artifact.artifact_id,
                    "artifact_hash": artifact.content_hash,
                }, usage=generated.usage,
            )
            return CanonicalSynthesisOutcome(
                status="prepared", invocation_id=completed.invocation_id,
                artifact_id=artifact.artifact_id, artifact_hash=artifact.content_hash,
            )
        except SynthesisGatewayError as exc:
            self.invocations.fail(
                claimed.invocation_id, lease_owner=self.lease_owner, lease_token=token,
                failure_classification=exc.code, error_message=str(exc),
            )
            raise CanonicalSynthesisBlocked(str(exc), code=exc.code, invocation_id=claimed.invocation_id) from exc
        except CanonicalSynthesisBlocked as exc:
            self.invocations.fail(
                claimed.invocation_id, lease_owner=self.lease_owner, lease_token=token,
                failure_classification=exc.code, error_message=str(exc),
            )
            exc.invocation_id = claimed.invocation_id
            raise
        except Exception as exc:
            self.invocations.fail(
                claimed.invocation_id, lease_owner=self.lease_owner, lease_token=token,
                failure_classification="malformed_or_unsafe", error_message=str(exc),
            )
            raise CanonicalSynthesisBlocked(
                "The provider response was malformed or unsafe; no preview was created.",
                code="malformed_or_unsafe", invocation_id=claimed.invocation_id,
            ) from exc

    def _replay(self, invocation) -> CanonicalSynthesisOutcome:
        reference = dict(invocation.result_reference or {})
        if reference.get("status") == "clarification_required":
            return CanonicalSynthesisOutcome(
                status="clarification_required", invocation_id=invocation.invocation_id,
                clarification=str(reference.get("clarification") or ""), replayed=True,
            )
        artifact_id = str(reference.get("artifact_id") or "")
        artifact = self.artifacts.get(artifact_id) if artifact_id else None
        if artifact is None or artifact.content_hash != reference.get("artifact_hash"):
            raise CanonicalSynthesisBlocked(
                "The successful invocation references a missing or corrupt preview artifact.",
                code="corrupt_result_reference", invocation_id=invocation.invocation_id,
            )
        return CanonicalSynthesisOutcome(
            status="prepared", invocation_id=invocation.invocation_id,
            artifact_id=artifact.artifact_id, artifact_hash=artifact.content_hash, replayed=True,
        )

    @staticmethod
    def _validate_binding(intent: CoordinatorIntent, artifact: ProjectArtifact) -> None:
        binding = artifact.binding
        if (
            binding.project_run_id != intent.project_run_id
            or binding.plan_revision_id != intent.plan_revision_id
            or binding.scope_revision_id != intent.scope_revision_id
            or binding.manifest_hash != intent.manifest_hash
        ):
            raise CanonicalSynthesisBlocked("The evidence artifact binding is stale.", code="stale_evidence_binding")

    @staticmethod
    def _safe_clarification(response: SynthesisResponse) -> str:
        question = " ".join(str(response.clarification_question or "").split())[:500]
        if not question or any(value in question.lower() for value in ("approve patch", "approve command", "password", "secret", "token")):
            raise CanonicalSynthesisBlocked("The provider returned an unsafe clarification.", code="unsafe_clarification")
        return question

    @staticmethod
    def _validate_operations(response: SynthesisResponse, evidence: dict[str, Any]) -> list[dict[str, Any]]:
        allowed = set(evidence.get("allowed_modify_paths") or ()) | set(evidence.get("allowed_create_paths") or ()) | set(evidence.get("allowed_delete_paths") or ())
        if not allowed:
            work_unit = evidence.get("work_unit") if isinstance(evidence.get("work_unit"), dict) else {}
            allowed = set(work_unit.get("expected_files") or work_unit.get("paths") or ())
        operations = [item.model_dump(mode="json") for item in response.operations]
        if any(item["path"] not in allowed for item in operations):
            raise CanonicalSynthesisBlocked("The provider proposed a path outside exact canonical evidence.", code="scope_violation")
        if any(not item.get("evidence_references") for item in operations):
            raise CanonicalSynthesisBlocked("Every synthesized file operation requires explicit evidence references.", code="missing_evidence_reference")
        return operations


__all__ = [
    "CanonicalProviderProfile", "CanonicalSynthesisBlocked", "CanonicalSynthesisOrchestrator",
    "CanonicalSynthesisOutcome", "MAX_CANONICAL_EVIDENCE_BYTES",
]
