from __future__ import annotations

from pathlib import Path

from backend.app.database.migrations import apply_schema_migrations, assert_schema_compatible
from backend.app.project_artifacts import (
    ProjectArtifact,
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control.contracts import content_hash

from .contracts import ScaffoldBlueprint, ScaffoldDetectionResult, ScaffoldRenderResult
from .detector import detect_scaffold_context
from .registry import BlueprintRegistry, select_blueprint
from .renderer import render_blueprint
from .validators import (
    BlueprintNotFoundError,
    validate_identifier,
    validate_no_conflicting_destinations,
    validate_no_duplicate_template_references,
    validate_no_unresolved_placeholders,
    validate_render_integrity,
    validate_render_result,
    validate_required_inputs,
)


class ProjectScaffoldingError(RuntimeError):
    """A service-level scaffolding failure (as opposed to a validators.py-level
    ScaffoldValidationError, which is used for deterministic input/output
    rejections)."""


class ProjectScaffoldingService:
    """Deterministic, model-free scaffold rendering.

    Composes the existing registry/detector/renderer/validators into one
    render pipeline: load a registered blueprint, resolve inputs, render
    every template, validate every destination and hash, and return an
    immutable, fully validated `ScaffoldRenderResult` (Stage 1's own
    contract -- there is deliberately only one canonical manifest
    representation, `GeneratedFileManifest`, not a second Stage-2-specific
    wrapper). Never touches the filesystem, a ProjectRun, the coordinator, or
    any worker -- wiring this into the canonical pipeline is later (Stage 3+)
    work. Persisting a render as a durable artifact is `ScaffoldPersistenceService`,
    below -- a separate class, since persistence needs I/O dependencies
    (`control`, `artifacts`) this pure rendering service deliberately does not.
    """

    def __init__(self, *, registry: BlueprintRegistry | None = None) -> None:
        self._registry = registry

    def render(
        self,
        *,
        category: str,
        inputs: dict[str, str],
        repository_root: str | Path | None = None,
        requested_version: int | None = None,
        detected: ScaffoldDetectionResult | None = None,
    ) -> ScaffoldRenderResult:
        """Load a registered blueprint for `category` and render it.

        `detected` may be supplied directly (e.g. by a caller that already
        ran detection); otherwise it is computed from `repository_root` via
        the existing detector. Neither is required to match a category-only
        blueprint (one with `framework=None`).
        """
        validate_identifier(category, label="category")
        resolved_detection = detected
        if resolved_detection is None:
            resolved_detection = (
                detect_scaffold_context(repository_root)
                if repository_root is not None
                else ScaffoldDetectionResult()
            )
        blueprint = select_blueprint(
            resolved_detection, category, requested_version, registry=self._registry
        )
        if blueprint is None:
            version_clause = (
                f" version {requested_version}" if requested_version is not None else ""
            )
            raise BlueprintNotFoundError(
                f"No registered scaffold blueprint matches category {category!r}{version_clause}."
            )
        return self.render_blueprint(blueprint, inputs)

    def render_blueprint(
        self, blueprint: ScaffoldBlueprint, inputs: dict[str, str]
    ) -> ScaffoldRenderResult:
        """Render an already-selected blueprint directly (no registry lookup)."""
        validate_identifier(blueprint.blueprint_id, label="blueprint_id")
        validate_identifier(blueprint.category, label="category")
        validate_no_duplicate_template_references(blueprint)
        validate_required_inputs(blueprint, inputs)

        render_result = render_blueprint(blueprint, inputs)
        return validate_render_result(blueprint, render_result)


class ScaffoldPersistenceService:
    """Persists a validated `ScaffoldRenderResult` as an immutable,
    content-addressed `SCAFFOLD_MANIFEST` project artifact.

    Read-only with respect to `ProjectRun` lifecycle state: `persist()` calls
    `control.get_project(...)` only to read the project's current
    plan/scope/manifest revision identities to bind against -- it never calls
    `control.execute()`, so it cannot transition a project's lifecycle, and it
    performs no filesystem writes, no worker dispatch, and creates no
    approval or patch-preview artifact.

    Follows `CanonicalRepairService`'s exact constructor/initialize shape
    (`project_repair/service.py`) and produces no new artifact table or
    lifecycle: the artifact itself, written through the existing
    `ProjectArtifactStore`, is the durable and auditable record -- there is
    no separate scaffold-specific bookkeeping table, matching "do not create
    a parallel artifact system."
    """

    def __init__(
        self, database_path: str | Path, control, artifacts: ProjectArtifactStore
    ) -> None:
        self.database_path = Path(database_path)
        self.control = control
        self.artifacts = artifacts

    def initialize(self) -> None:
        apply_schema_migrations(self.database_path)
        assert_schema_compatible(self.database_path)

    def persist(
        self,
        render_result: ScaffoldRenderResult,
        inputs: dict[str, str],
        *,
        project_run_id: str,
        category: str,
        work_unit_id: str | None = None,
        coordinator_intent_id: str | None = None,
        actor_id: str | None = None,
    ) -> ProjectArtifact:
        """Persist `render_result` bound to `project_run_id`'s current
        canonical identities.

        Idempotent by construction: `build_project_artifact` derives both
        `artifact_id` and `content_hash` purely from `artifact_type` +
        `binding` + `payload` (never from `created_at`), so persisting the
        same render under the same binding twice returns the same immutable
        row (`ProjectArtifactStore.put` short-circuits on an existing
        `artifact_id`/matching content). `revision_number=1` activates the
        store's existing revision-collision check, so the same binding with
        genuinely different content fails closed instead of silently
        overwriting or duplicating.

        Never trusts that the caller already validated `render_result` --
        re-runs the render-result-level Stage 2A checks (conflicting
        destinations, unresolved placeholders, hash integrity) itself before
        writing anything.
        """
        validate_no_conflicting_destinations(render_result)
        validate_no_unresolved_placeholders(render_result)
        validate_render_integrity(render_result)
        run = self.control.get_project(project_run_id)
        input_hash = content_hash(inputs)
        manifest_payload = render_result.manifest.model_dump(
            mode="json", exclude={"rendered_at"}
        )
        payload: dict[str, object] = {
            "category": category,
            "blueprint_id": render_result.blueprint_id,
            "blueprint_version": render_result.blueprint_version,
            "input_hash": input_hash,
            "manifest": manifest_payload,
            "operations": list(render_result.operations),
        }
        if actor_id is not None:
            payload["actor_id"] = actor_id
        binding = ProjectArtifactBinding(
            project_run_id=project_run_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            work_unit_id=work_unit_id,
            coordinator_intent_id=coordinator_intent_id,
            authority_hash=content_hash({
                "category": category,
                "blueprint_id": render_result.blueprint_id,
                "blueprint_version": render_result.blueprint_version,
                "input_hash": input_hash,
            }),
        )
        artifact = build_project_artifact(
            artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST,
            binding=binding,
            payload=payload,
            revision_number=1,
        )
        return self.artifacts.put(artifact)


__all__ = [
    "ProjectScaffoldingError",
    "ProjectScaffoldingService",
    "ScaffoldPersistenceService",
]
