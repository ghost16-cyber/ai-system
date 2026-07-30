from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.chat_runtime.contracts import (
    ChatResponseMode,
    ChatRuntimeFailure,
    ChatRuntimeFailureReason,
    ChatRuntimeGenerationSummary,
    ChatRuntimeLineage,
    ChatRuntimeRetrievalSummary,
    citation_from_evidence,
)
from backend.app.chat_runtime.memory import (
    ChatWorkingMemory,
    build_chat_working_memory,
)
from backend.app.chat_runtime.prompts import (
    build_chat_context_items,
    build_chat_system_instruction,
    build_chat_user_content,
    build_corpus_context_item,
)
from backend.app.local_ai.generation_contracts import (
    MAX_CONTEXT_ITEMS,
    ChatGenerationTargetStatus,
    GenerationFailureReason,
    GenerationParameters,
    GenerationPurpose,
    LocalAIExecutionRequest,
    LocalAIExecutionState,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.project_control import ProjectControlPlane
from backend.app.project_control.contracts import content_hash
from backend.app.project_control.errors import ProjectControlError
from backend.app.project_retrieval.bindings import (
    RetrievalBindingError,
    canonical_retrieval_authority_id,
)
from backend.app.project_retrieval.contracts import (
    MAX_QUERY_CHARS,
    RetrievalRequest,
    normalize_query,
)
from backend.app.project_retrieval.service import (
    ProjectRetrievalError,
    ProjectRetrievalService,
)
from backend.app.schemas.api import ChatRunResponse


LOCAL_CHAT_ACTOR_ID = "local-user"


class ChatRuntimeError(RuntimeError):
    """Raised only for programmer-error-shaped conditions; normal absence and
    failure paths return typed ChatRuntimeAnswer results instead."""


@dataclass(frozen=True)
class ChatRuntimeAnswer:
    """The result of one canonical chat generation attempt.

    assistant_response is None on any fallback path -- chat_workflow.py's
    existing deterministic assistant-response construction (greeting,
    capability summary, routing narration, etc.) fills the text in that case,
    exactly as it already does when the legacy SLM gateway fell back.
    """

    response_mode: ChatResponseMode
    assistant_response: str | None
    used_real_slm: bool
    provider: str
    model: str | None
    fallback_reason: str | None
    latency_ms: int | None
    lineage: ChatRuntimeLineage


class CanonicalChatRuntimeService:
    """The exclusive path from chat to local generation and project retrieval.

    Composes already-implemented canonical authorities (LocalAIService,
    ProjectRetrievalService, ProjectControlPlane) -- it creates no new
    authority of its own: no project mutation, no approval, no model
    enablement, no corpus ingestion. Every failure to reach a real generation
    is a typed ChatRuntimeFailure, never a raised exception surfaced to chat.
    """

    def __init__(
        self,
        *,
        local_ai_service: LocalAIService,
        project_control: ProjectControlPlane,
        project_retrieval_service: ProjectRetrievalService | None,
    ) -> None:
        self._local_ai = local_ai_service
        self._project_control = project_control
        self._project_retrieval = project_retrieval_service

    def build_working_memory(
        self,
        *,
        conversation_id: str,
        latest_message: str,
        previous_turns: list[ChatRunResponse],
        project_run_id: str | None,
    ) -> ChatWorkingMemory | None:
        active_project = None
        if project_run_id is not None and self._project_control is not None:
            try:
                candidate = self._project_control.get_read_model(project_run_id)
                if candidate.conversation_id == conversation_id:
                    active_project = candidate
            except ProjectControlError:
                active_project = None
        return build_chat_working_memory(
            conversation_id=conversation_id,
            latest_message=latest_message,
            previous_turns=previous_turns,
            active_project=active_project,
        )

    def answer(
        self,
        *,
        chat_request_id: str,
        chat_run_id: str,
        conversation_id: str,
        message: str,
        project_run_id: str | None,
        specialist: str,
        intent: str,
        confidence: float,
        safety_decision: str,
        runtime_decision: str,
        memory_summary: str | None,
        use_rag: bool,
        request_fingerprint: str,
        timeout_seconds: int = 30,
        corpus_context: str | None = None,
    ) -> ChatRuntimeAnswer:
        now = datetime.now(timezone.utc)
        retrieval_summary, retrieval_evidence, retrieval_failure = self._retrieve(
            chat_request_id=chat_request_id,
            conversation_id=conversation_id,
            message=message,
            project_run_id=project_run_id,
            use_rag=use_rag,
            request_fingerprint=request_fingerprint,
            now=now,
        )

        system_instruction = build_chat_system_instruction(
            specialist=specialist,
            intent=intent,
            confidence=confidence,
            safety_decision=safety_decision,
            runtime_decision=runtime_decision,
        )
        user_content = build_chat_user_content(message, memory_summary=memory_summary)
        context_items = build_chat_context_items(retrieval_evidence)
        if corpus_context:
            context_items = (
                (build_corpus_context_item(corpus_context),) + context_items
            )[:MAX_CONTEXT_ITEMS]

        generation_summary, failure, generation_result = self._generate(
            chat_request_id=chat_request_id,
            conversation_id=conversation_id,
            system_instruction=system_instruction,
            user_content=user_content,
            context_items=context_items,
            request_fingerprint=request_fingerprint,
            timeout_seconds=timeout_seconds,
        )
        del retrieval_failure  # retrieval failing never blocks generation

        if generation_summary is not None:
            response_mode = ChatResponseMode.LOCAL_AI
            assistant_response = generation_result
            used_real_slm = True
            provider = generation_summary.provider_identity
            model = generation_summary.exact_model_tag
            fallback_reason = None
            latency_ms = generation_summary.duration_ms
            lineage_failure = None
        else:
            response_mode = ChatResponseMode.DETERMINISTIC_FALLBACK
            assistant_response = None
            used_real_slm = False
            provider = "fallback"
            model = None
            fallback_reason = failure.reason.value if failure is not None else None
            latency_ms = None
            lineage_failure = failure

        lineage = ChatRuntimeLineage(
            chat_request_id=chat_request_id,
            chat_run_id=chat_run_id,
            request_fingerprint=request_fingerprint,
            response_mode=response_mode,
            retrieval=retrieval_summary,
            generation=generation_summary,
            failure=lineage_failure,
            created_at=now,
        )
        return ChatRuntimeAnswer(
            response_mode=response_mode,
            assistant_response=assistant_response,
            used_real_slm=used_real_slm,
            provider=provider,
            model=model,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            lineage=lineage,
        )

    def _retrieve(
        self,
        *,
        chat_request_id: str,
        conversation_id: str,
        message: str,
        project_run_id: str | None,
        use_rag: bool,
        request_fingerprint: str,
        now: datetime,
    ) -> tuple[ChatRuntimeRetrievalSummary | None, tuple, ChatRuntimeFailure | None]:
        """Project-bound retrieval only. No canonical project selected means
        no retrieval occurs -- there is no generic workspace scan fallback."""

        if not use_rag or project_run_id is None or self._project_retrieval is None:
            return None, (), None
        try:
            run = self._project_control.get_project(project_run_id)
            if run.conversation_id != conversation_id:
                raise RetrievalBindingError("conversation_mismatch")
            scope = self._project_control.get_scope_revision(run.current_scope_revision_id)
            plan = (
                self._project_control.get_plan_revision(run.current_plan_revision_id)
                if run.current_plan_revision_id is not None
                else None
            )
            repository_state_hash = self._project_retrieval.compute_repository_state(
                Path(run.repository_root), scope.included_paths, scope.excluded_paths
            )
            normalized_query = normalize_query(message[:MAX_QUERY_CHARS])
            retrieval_request = RetrievalRequest(
                project_id=run.project_run_id,
                conversation_id=run.conversation_id,
                actor_id=run.actor_id,
                workspace_id=run.workspace_id,
                repository_root=run.repository_root,
                scope_revision_id=scope.scope_revision_id,
                scope_hash=scope.content_hash,
                plan_revision_id=plan.plan_revision_id if plan is not None else None,
                plan_hash=plan.content_hash if plan is not None else None,
                repository_manifest_hash=run.current_manifest_hash,
                repository_state_hash=repository_state_hash,
                expected_project_state_version=run.state_version,
                authority_id=canonical_retrieval_authority_id(run),
                request_id=f"chat-retrieval:{chat_request_id}"[:200],
                query=message[:MAX_QUERY_CHARS],
                normalized_query=normalized_query,
                query_hash=content_hash(normalized_query),
                idempotency_key=f"chat-retrieval:{chat_request_id}:{request_fingerprint}"[:200],
                created_at=now,
            )
            artifact = self._project_retrieval.retrieve(retrieval_request)
        except (ProjectRetrievalError, RetrievalBindingError, ProjectControlError, ValueError) as error:
            failure = ChatRuntimeFailure(
                reason=ChatRuntimeFailureReason.RETRIEVAL_UNAVAILABLE,
                detail=f"Project-bound retrieval was unavailable: {error}"[:500],
                occurred_at=now,
            )
            return None, (), failure

        citations = tuple(
            citation_from_evidence(item) for item in artifact.evidence
        )
        summary = ChatRuntimeRetrievalSummary(
            project_run_id=project_run_id,
            retrieval_artifact_id=artifact.artifact_id,
            retrieval_artifact_hash=artifact.artifact_hash,
            evidence_count=artifact.evidence_count,
            citations=citations,
            replayed=artifact.replayed,
            invalidated=artifact.invalidated,
        )
        return summary, artifact.evidence, None

    def _generate(
        self,
        *,
        chat_request_id: str,
        conversation_id: str,
        system_instruction: str,
        user_content: str,
        context_items: tuple,
        request_fingerprint: str,
        timeout_seconds: int,
    ) -> tuple[ChatRuntimeGenerationSummary | None, ChatRuntimeFailure | None, str | None]:
        now = datetime.now(timezone.utc)
        target = self._local_ai.resolve_chat_generation_target()
        if target.status != ChatGenerationTargetStatus.RESOLVED:
            return (
                None,
                ChatRuntimeFailure(
                    reason=ChatRuntimeFailureReason(target.status.value),
                    detail=f"Chat generation target could not be resolved: {target.status.value}",
                    occurred_at=now,
                ),
                None,
            )

        bounded_timeout = max(1, min(timeout_seconds, self._local_ai.configuration.generation_timeout_seconds))
        execution_request = LocalAIExecutionRequest(
            request_id=f"chat-generation:{chat_request_id}"[:200],
            idempotency_key=f"chat-generation:{chat_request_id}:{request_fingerprint}"[:160],
            actor_id=LOCAL_CHAT_ACTOR_ID,
            model_profile_id=target.model_profile_id,
            exact_model_tag=target.exact_model_tag,
            expected_configuration_version=target.configuration_version,
            purpose=GenerationPurpose.CHAT,
            system_instruction=system_instruction,
            user_content=user_content,
            context=context_items,
            timeout_seconds=bounded_timeout,
            parameters=GenerationParameters(
                maximum_output_tokens=min(target.maximum_output_tokens or 1024, 1024)
            ),
            allow_cpu_fallback=self._local_ai.configuration.allow_cpu_fallback,
            prefer_gpu=True,
            conversation_id=conversation_id,
        )
        result = self._local_ai.execute_generation(execution_request)

        if result.state == LocalAIExecutionState.SUCCEEDED and result.generation_result is not None:
            generation = result.generation_result
            structured = generation.structured_output or {}
            response_text = structured.get("response")
            if not isinstance(response_text, str) or not response_text:
                return (
                    None,
                    ChatRuntimeFailure(
                        reason=ChatRuntimeFailureReason.MALFORMED_PROVIDER_RESPONSE,
                        detail="Generation succeeded but its structured output had no response text.",
                        occurred_at=now,
                    ),
                    None,
                )
            summary = ChatRuntimeGenerationSummary(
                generation_id=generation.generation_id,
                scheduler_job_id=result.scheduler_job.job_id,
                provenance_execution_id=(
                    result.provenance.execution_id if result.provenance is not None else None
                ),
                model_profile_id=target.model_profile_id,
                exact_model_tag=generation.exact_model_tag,
                configuration_version=target.configuration_version,
                provider_identity=generation.provider_identity,
                duration_ms=generation.duration_ms,
                replayed=generation.replayed,
                response_hash=generation.raw_response_hash,
            )
            return summary, None, response_text

        if result.state == LocalAIExecutionState.BLOCKED:
            reason = ChatRuntimeFailureReason.ADMISSION_BLOCKED
            detail = "Generation admission was blocked by hardware or policy limits."
        elif result.state == LocalAIExecutionState.CANCELLED:
            reason = ChatRuntimeFailureReason.GENERATION_CANCELLED_BY_SCHEDULER
            detail = "Generation was cancelled by the scheduler before completion."
        elif result.state == LocalAIExecutionState.IN_PROGRESS:
            reason = ChatRuntimeFailureReason.GENERATION_IN_PROGRESS
            detail = "An equivalent generation is already in progress."
        elif result.generation_result is not None and result.generation_result.failure_reason is not None:
            reason = ChatRuntimeFailureReason(result.generation_result.failure_reason.value)
            detail = result.generation_result.user_message or "Local-AI generation failed."
        else:
            reason = ChatRuntimeFailureReason.INTERNAL_FAILURE
            detail = "Local-AI generation failed with no further detail."
        return None, ChatRuntimeFailure(reason=reason, detail=detail[:500], occurred_at=now), None


__all__ = [
    "CanonicalChatRuntimeService",
    "ChatRuntimeAnswer",
    "ChatRuntimeError",
    "LOCAL_CHAT_ACTOR_ID",
]
