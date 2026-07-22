from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from backend.app.database.migrations import assert_schema_compatible
from backend.app.local_ai.config import LocalAIConfiguration, load_local_ai_configuration
from backend.app.local_ai.generation_contracts import (
    GenerationFailureReason,
    GenerationState,
    GenerationUsage,
    LocalGenerationRequest,
    LocalGenerationResult,
)
from backend.app.local_ai.provider import (
    CancellationCheck,
    LocalModelProviderClient,
    OllamaProviderClient,
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationRequest,
)
from backend.app.project_control.contracts import canonical_json, content_hash


MAX_STRUCTURED_OUTPUT_BYTES = 524_288


class LocalGenerationGateway:
    """Durable advisory-only structured generation; never project authority."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        configuration: LocalAIConfiguration | None = None,
        provider_client: LocalModelProviderClient | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.configuration = configuration or load_local_ai_configuration()
        self.provider_client = provider_client or OllamaProviderClient(
            self.configuration.endpoint_identity
        )

    def initialize(self) -> None:
        assert_schema_compatible(self.database_path)

    def generate(
        self,
        request: LocalGenerationRequest,
        target_schema: type[BaseModel],
        *,
        cancelled: CancellationCheck | None = None,
    ) -> LocalGenerationResult:
        started_at = _now()
        started_clock = time.monotonic()
        generation_id = f"generation-{uuid4().hex}"
        request_payload = request.model_dump(mode="json")
        request_fingerprint = content_hash(request_payload)
        input_hash = content_hash(
            {
                "system_instruction": request.system_instruction,
                "user_content": request.user_content,
            }
        )
        context_hash = content_hash(
            [item.model_dump(mode="json") for item in request.context]
        )

        replay = self._claim_or_replay(
            generation_id=generation_id,
            request=request,
            request_fingerprint=request_fingerprint,
            input_hash=input_hash,
            context_hash=context_hash,
            started_at=started_at,
        )
        if replay is not None:
            return replay

        failure = self._preflight_failure(request, target_schema)
        if failure is not None:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                failure,
                _message(failure),
            )

        self._audit(
            "local_generation_readiness_check",
            generation_id,
            {"request_id": request.request_id, "model": request.exact_model_tag},
        )
        try:
            inspection = self.provider_client.inspect(
                timeout_seconds=self.configuration.connection_timeout_seconds
            )
        except ProviderClientError as exc:
            reason = _provider_failure(exc.code, readiness=True)
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                reason,
                exc.safe_message,
                diagnostic=exc.diagnostic,
            )
        except Exception:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.INTERNAL_FAILURE,
                _message(GenerationFailureReason.INTERNAL_FAILURE),
            )

        if request.exact_model_tag not in inspection.installed_models:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.EXACT_MODEL_UNAVAILABLE,
                _message(GenerationFailureReason.EXACT_MODEL_UNAVAILABLE),
                diagnostic={"installed_model_count": len(inspection.installed_models)},
            )

        prompt = _render_prompt(request)
        self._audit(
            "local_generation_started",
            generation_id,
            {
                "request_id": request.request_id,
                "request_fingerprint": request_fingerprint,
                "input_hash": input_hash,
                "context_hash": context_hash,
            },
        )
        try:
            provider_result = self.provider_client.generate(
                ProviderGenerationRequest(
                    model=request.exact_model_tag,
                    system_instruction=request.system_instruction,
                    prompt=prompt,
                    timeout_seconds=request.timeout_seconds,
                    maximum_output_tokens=request.parameters.maximum_output_tokens,
                    temperature=request.parameters.temperature,
                    top_p=request.parameters.top_p,
                    seed=request.parameters.seed,
                ),
                cancelled=cancelled,
            )
        except ProviderClientError as exc:
            reason = _provider_failure(exc.code, readiness=False)
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                reason,
                exc.safe_message,
                diagnostic=exc.diagnostic,
            )
        except Exception:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.INTERNAL_FAILURE,
                _message(GenerationFailureReason.INTERNAL_FAILURE),
            )

        if provider_result.model != request.exact_model_tag:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE,
                "The provider reported a different model than the exact configured tag.",
                diagnostic={"reported_model_mismatch": True},
            )
        raw = provider_result.response
        raw_bytes = raw.encode("utf-8")
        response_hash = hashlib.sha256(raw_bytes).hexdigest()
        if len(raw_bytes) > MAX_STRUCTURED_OUTPUT_BYTES:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.INVALID_STRUCTURED_OUTPUT,
                "The structured model output exceeded the bounded response size.",
                response_hash=response_hash,
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.INVALID_STRUCTURED_OUTPUT,
                _message(GenerationFailureReason.INVALID_STRUCTURED_OUTPUT),
                response_hash=response_hash,
            )
        if not isinstance(parsed, dict):
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.INVALID_STRUCTURED_OUTPUT,
                "The structured model output must be one JSON object.",
                response_hash=response_hash,
            )
        try:
            validated = target_schema.model_validate(parsed)
        except ValidationError as exc:
            return self._fail(
                generation_id,
                request,
                started_at,
                started_clock,
                GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED,
                _message(GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED),
                response_hash=response_hash,
                diagnostic={"validation_error_count": exc.error_count()},
            )

        completed_at = _now()
        result = LocalGenerationResult(
            generation_id=generation_id,
            request_id=request.request_id,
            provider_identity=self.configuration.provider_type,
            endpoint_identity=self.configuration.endpoint_identity,
            exact_model_tag=request.exact_model_tag,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_clock),
            state=GenerationState.SUCCEEDED,
            raw_response_hash=response_hash,
            structured_output=validated.model_dump(mode="json"),
            usage=_usage(provider_result.metadata),
            user_message="Structured local-model generation completed.",
        )
        if not self._finish(generation_id, "completed", result, None, {}):
            return self._persistence_failure_result(
                generation_id, request, started_at, started_clock
            )
        self._audit(
            "local_generation_completed",
            generation_id,
            {"request_id": request.request_id, "response_hash": response_hash},
        )
        return result

    def _preflight_failure(
        self, request: LocalGenerationRequest, target_schema: type[BaseModel]
    ) -> GenerationFailureReason | None:
        if not self.configuration.generation_enabled:
            return GenerationFailureReason.LOCAL_AI_DISABLED
        if self.configuration.provider_type != "ollama":
            return GenerationFailureReason.PROVIDER_UNSUPPORTED
        configured_model = self.configuration.model_for_role(request.purpose.value)
        if configured_model is None or request.exact_model_tag != configured_model:
            return GenerationFailureReason.EXACT_MODEL_UNAVAILABLE
        if request.timeout_seconds > self.configuration.generation_timeout_seconds:
            return GenerationFailureReason.INVALID_REQUEST
        if (
            request.parameters.maximum_output_tokens
            > self.configuration.maximum_output_tokens
        ):
            return GenerationFailureReason.INVALID_REQUEST
        context_char_limit = self.configuration.maximum_context_tokens * 4
        if (
            len(request.system_instruction)
            + len(request.user_content)
            + sum(len(item.content) for item in request.context)
            > context_char_limit
        ):
            return GenerationFailureReason.REQUEST_TOO_LARGE
        expected = _schema_identity(target_schema)
        if expected is None or expected != request.expected_response_schema_identity:
            return GenerationFailureReason.INVALID_REQUEST
        return None

    def _claim_or_replay(
        self,
        *,
        generation_id: str,
        request: LocalGenerationRequest,
        request_fingerprint: str,
        input_hash: str,
        context_hash: str,
        started_at: datetime,
    ) -> LocalGenerationResult | None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT generation_id, request_fingerprint, status, result_json "
                    "FROM local_ai_generation_invocations WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if row is not None:
                    connection.execute("COMMIT")
                    if row["request_fingerprint"] != request_fingerprint:
                        self._audit(
                            "local_generation_idempotency_conflict",
                            str(row["generation_id"]),
                            {"request_id": request.request_id},
                        )
                        return self._ephemeral_failure(
                            str(row["generation_id"]),
                            request,
                            GenerationFailureReason.IDEMPOTENCY_CONFLICT,
                            "The idempotency key is already bound to another request.",
                        )
                    if row["status"] == "completed" and row["result_json"]:
                        stored = LocalGenerationResult.model_validate_json(
                            row["result_json"]
                        )
                        replay = stored.model_copy(
                            update={
                                "replayed": True,
                                "replay_source_generation_id": stored.generation_id,
                            }
                        )
                        self._audit(
                            "local_generation_replayed",
                            stored.generation_id,
                            {"request_id": request.request_id},
                        )
                        return replay
                    return self._ephemeral_failure(
                        str(row["generation_id"]),
                        request,
                        GenerationFailureReason.PERSISTENCE_FAILURE,
                        "A previous non-successful invocation is not a successful replay.",
                    )
                connection.execute(
                    "INSERT INTO local_ai_generation_invocations "
                    "(generation_id, request_id, idempotency_key, request_fingerprint, "
                    "purpose, provider_identity, endpoint_identity, exact_model_tag, "
                    "input_hash, context_hash, expected_schema_identity, status, "
                    "started_at, diagnostic_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', ?, '{}', ?)",
                    (
                        generation_id,
                        request.request_id,
                        request.idempotency_key,
                        request_fingerprint,
                        request.purpose.value,
                        self.configuration.provider_type,
                        self.configuration.endpoint_identity,
                        request.exact_model_tag,
                        input_hash,
                        context_hash,
                        request.expected_response_schema_identity,
                        started_at.isoformat(),
                        started_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
        except (sqlite3.Error, ValueError):
            return self._ephemeral_failure(
                generation_id,
                request,
                GenerationFailureReason.PERSISTENCE_FAILURE,
                _message(GenerationFailureReason.PERSISTENCE_FAILURE),
            )
        return None

    def _fail(
        self,
        generation_id: str,
        request: LocalGenerationRequest,
        started_at: datetime,
        started_clock: float,
        reason: GenerationFailureReason,
        user_message: str,
        *,
        response_hash: str | None = None,
        diagnostic: dict[str, Any] | None = None,
    ) -> LocalGenerationResult:
        result = LocalGenerationResult(
            generation_id=generation_id,
            request_id=request.request_id,
            provider_identity=self.configuration.provider_type,
            endpoint_identity=self.configuration.endpoint_identity,
            exact_model_tag=request.exact_model_tag,
            started_at=started_at,
            completed_at=_now(),
            duration_ms=_duration_ms(started_clock),
            state=GenerationState.FAILED,
            raw_response_hash=response_hash,
            failure_reason=reason,
            user_message=user_message,
        )
        terminal_status = {
            GenerationFailureReason.GENERATION_TIMEOUT: "timed_out",
            GenerationFailureReason.GENERATION_CANCELLED: "cancelled",
        }.get(reason, "failed")
        if not self._finish(
            generation_id, terminal_status, result, reason.value, diagnostic or {}
        ):
            return self._persistence_failure_result(
                generation_id, request, started_at, started_clock
            )
        event = (
            "local_generation_timeout"
            if reason == GenerationFailureReason.GENERATION_TIMEOUT
            else "local_generation_cancelled"
            if reason == GenerationFailureReason.GENERATION_CANCELLED
            else "local_generation_failed"
        )
        self._audit(
            event,
            generation_id,
            {"request_id": request.request_id, "failure_reason": reason.value},
        )
        return result

    def _finish(
        self,
        generation_id: str,
        status: str,
        result: LocalGenerationResult,
        failure: str | None,
        diagnostic: dict[str, Any],
    ) -> bool:
        safe_diagnostic = _safe_diagnostic(diagnostic)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE local_ai_generation_invocations SET status = ?, "
                    "completed_at = ?, duration_ms = ?, response_hash = ?, "
                    "failure_classification = ?, result_json = ?, diagnostic_json = ? "
                    "WHERE generation_id = ? AND status = 'started'",
                    (
                        status,
                        result.completed_at.isoformat(),
                        result.duration_ms,
                        result.raw_response_hash,
                        failure,
                        result.model_dump_json(),
                        canonical_json(safe_diagnostic),
                        generation_id,
                    ),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            return False

    def _audit(
        self, event_type: str, generation_id: str, metadata: dict[str, Any]
    ) -> None:
        bounded = _safe_diagnostic(metadata)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO local_ai_audit_events "
                    "(event_id, event_type, aggregate_id, event_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"local-ai-event-{uuid4().hex}",
                        event_type,
                        generation_id,
                        canonical_json(bounded),
                        _now().isoformat(),
                    ),
                )
        except sqlite3.Error:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ephemeral_failure(
        self,
        generation_id: str,
        request: LocalGenerationRequest,
        reason: GenerationFailureReason,
        message: str,
    ) -> LocalGenerationResult:
        now = _now()
        return LocalGenerationResult(
            generation_id=generation_id,
            request_id=request.request_id,
            provider_identity=self.configuration.provider_type,
            endpoint_identity=self.configuration.endpoint_identity,
            exact_model_tag=request.exact_model_tag,
            started_at=now,
            completed_at=now,
            duration_ms=0,
            state=GenerationState.FAILED,
            failure_reason=reason,
            user_message=message,
        )

    def _persistence_failure_result(
        self,
        generation_id: str,
        request: LocalGenerationRequest,
        started_at: datetime,
        started_clock: float,
    ) -> LocalGenerationResult:
        return LocalGenerationResult(
            generation_id=generation_id,
            request_id=request.request_id,
            provider_identity=self.configuration.provider_type,
            endpoint_identity=self.configuration.endpoint_identity,
            exact_model_tag=request.exact_model_tag,
            started_at=started_at,
            completed_at=_now(),
            duration_ms=_duration_ms(started_clock),
            state=GenerationState.FAILED,
            failure_reason=GenerationFailureReason.PERSISTENCE_FAILURE,
            user_message=_message(GenerationFailureReason.PERSISTENCE_FAILURE),
        )


def _render_prompt(request: LocalGenerationRequest) -> str:
    context = [item.model_dump(mode="json") for item in request.context]
    if not context:
        return request.user_content
    return (
        request.user_content
        + "\n\nUNTRUSTED_CONTEXT_JSON:\n"
        + canonical_json(context)
    )


def _schema_identity(target_schema: type[BaseModel]) -> str | None:
    field = target_schema.model_fields.get("schema_version")
    default = field.default if field is not None else None
    return default if isinstance(default, str) else None


def _usage(metadata: dict[str, int]) -> GenerationUsage:
    return GenerationUsage(
        prompt_eval_count=metadata.get("prompt_eval_count"),
        eval_count=metadata.get("eval_count"),
        prompt_eval_duration_ns=metadata.get("prompt_eval_duration"),
        eval_duration_ns=metadata.get("eval_duration"),
    )


def _provider_failure(
    code: ProviderErrorCode, *, readiness: bool
) -> GenerationFailureReason:
    return {
        ProviderErrorCode.UNREACHABLE: GenerationFailureReason.PROVIDER_UNREACHABLE,
        ProviderErrorCode.TIMEOUT: (
            GenerationFailureReason.PROVIDER_UNREACHABLE
            if readiness
            else GenerationFailureReason.GENERATION_TIMEOUT
        ),
        ProviderErrorCode.CANCELLED: GenerationFailureReason.GENERATION_CANCELLED,
        ProviderErrorCode.REJECTED: GenerationFailureReason.PROVIDER_REJECTED_REQUEST,
        ProviderErrorCode.MALFORMED_RESPONSE: GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE,
    }[code]


def _safe_diagnostic(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:16]:
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, bool) or isinstance(item, int):
            result[key] = item
        elif isinstance(item, str):
            result[key] = item[:300]
    return result


def _message(reason: GenerationFailureReason) -> str:
    return {
        GenerationFailureReason.LOCAL_AI_DISABLED: "Local model generation is disabled by canonical configuration.",
        GenerationFailureReason.PROVIDER_UNSUPPORTED: "The configured local model provider is unsupported.",
        GenerationFailureReason.PROVIDER_UNREACHABLE: "The configured local model provider is unreachable.",
        GenerationFailureReason.EXACT_MODEL_UNAVAILABLE: "The exact configured model is unavailable.",
        GenerationFailureReason.INVALID_REQUEST: "The generation request is invalid for canonical configuration.",
        GenerationFailureReason.REQUEST_TOO_LARGE: "The generation request exceeds a configured bound.",
        GenerationFailureReason.GENERATION_TIMEOUT: "The local model request timed out.",
        GenerationFailureReason.GENERATION_CANCELLED: "The local model request was cancelled.",
        GenerationFailureReason.PROVIDER_REJECTED_REQUEST: "The local model provider rejected the request.",
        GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE: "The local model returned a malformed response envelope.",
        GenerationFailureReason.INVALID_STRUCTURED_OUTPUT: "The local model did not return one strict JSON object.",
        GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED: "The local model output did not satisfy the expected schema.",
        GenerationFailureReason.IDEMPOTENCY_CONFLICT: "The idempotency key is bound to another request.",
        GenerationFailureReason.PERSISTENCE_FAILURE: "The generation invocation could not be persisted safely.",
        GenerationFailureReason.INTERNAL_FAILURE: "Local model generation failed safely.",
    }[reason]


def _duration_ms(started_clock: float) -> int:
    return max(0, int((time.monotonic() - started_clock) * 1000))


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["LocalGenerationGateway", "MAX_STRUCTURED_OUTPUT_BYTES"]
