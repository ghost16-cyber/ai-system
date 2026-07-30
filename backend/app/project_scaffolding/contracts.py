from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel


SCAFFOLD_BLUEPRINT_VERSION = "astra.project-scaffolding.blueprint.v1"
SCAFFOLD_DETECTION_VERSION = "astra.project-scaffolding.detection.v1"
SCAFFOLD_MANIFEST_VERSION = "astra.project-scaffolding.manifest.v1"
SCAFFOLD_RENDER_RESULT_VERSION = "astra.project-scaffolding.render-result.v1"

MAX_GENERATED_FILES_PER_BLUEPRINT = 25


class OverwritePolicy(StrEnum):
    REFUSE = "refuse"
    SKIP_EXISTING = "skip_existing"
    REQUIRE_EXPLICIT_ALLOW = "require_explicit_allow"


class RequiredInputSpec(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    validation_pattern: str | None = Field(default=None, max_length=200)
    default: str | None = Field(default=None, max_length=2000)
    required: bool = True


class GeneratedFileSpec(StrictModel):
    relative_path_template: str = Field(min_length=1, max_length=500)
    content_template_ref: str = Field(min_length=1, max_length=500)
    executable: bool = False


class ScaffoldBlueprint(StrictModel):
    schema_version: str = SCAFFOLD_BLUEPRINT_VERSION
    blueprint_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    category: str = Field(min_length=1, max_length=120)
    framework: str | None = Field(default=None, max_length=120)
    compatibility_constraints: tuple[str, ...] = ()
    required_inputs: tuple[RequiredInputSpec, ...] = ()
    generated_files: tuple[GeneratedFileSpec, ...] = Field(
        min_length=1, max_length=MAX_GENERATED_FILES_PER_BLUEPRINT
    )
    overwrite_policy: OverwritePolicy = OverwritePolicy.REFUSE
    validation_commands: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_paths_and_inputs(self) -> "ScaffoldBlueprint":
        path_templates = [spec.relative_path_template for spec in self.generated_files]
        if len(path_templates) != len(set(path_templates)):
            raise ValueError("Blueprint generated_files contain duplicate path templates.")
        input_names = [spec.name for spec in self.required_inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("Blueprint required_inputs contain duplicate names.")
        return self


class ScaffoldDetectionResult(StrictModel):
    schema_version: str = SCAFFOLD_DETECTION_VERSION
    frameworks: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    structure_flags: dict[str, bool] = Field(default_factory=dict)
    suggested_category: str | None = None


class GeneratedFileRecord(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1000)
    content_hash: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    content_template_ref: str = Field(min_length=1, max_length=500)


class GeneratedFileManifest(StrictModel):
    schema_version: str = SCAFFOLD_MANIFEST_VERSION
    blueprint_id: str = Field(min_length=1, max_length=160)
    blueprint_version: int = Field(ge=1)
    template_hash: str = Field(min_length=64, max_length=64)
    files: tuple[GeneratedFileRecord, ...] = Field(min_length=1)
    total_byte_size: int = Field(ge=0)
    rendered_at: datetime


class ScaffoldRenderResult(StrictModel):
    schema_version: str = SCAFFOLD_RENDER_RESULT_VERSION
    blueprint_id: str = Field(min_length=1, max_length=160)
    blueprint_version: int = Field(ge=1)
    manifest: GeneratedFileManifest
    operations: tuple[dict[str, Any], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operations_match_manifest(self) -> "ScaffoldRenderResult":
        if len(self.operations) != len(self.manifest.files):
            raise ValueError("Render operations must match the manifest file count exactly.")
        return self


__all__ = [
    "SCAFFOLD_BLUEPRINT_VERSION",
    "SCAFFOLD_DETECTION_VERSION",
    "SCAFFOLD_MANIFEST_VERSION",
    "SCAFFOLD_RENDER_RESULT_VERSION",
    "MAX_GENERATED_FILES_PER_BLUEPRINT",
    "OverwritePolicy",
    "RequiredInputSpec",
    "GeneratedFileSpec",
    "ScaffoldBlueprint",
    "ScaffoldDetectionResult",
    "GeneratedFileRecord",
    "GeneratedFileManifest",
    "ScaffoldRenderResult",
]
