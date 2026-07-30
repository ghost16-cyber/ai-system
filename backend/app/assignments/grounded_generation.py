from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentCodeBlueprintSet,
    AssignmentEvidenceChecklist,
    CorpusGroundingSummary,
    GenerationMode,
    GroundedFileBlueprint,
    GroundedGenerationResult,
    GroundedWorkspaceFilePlan,
    GroundedWorkspaceGenerationPlan,
    GroundedWorkspaceWriteResult,
    GroundingProvenance,
)
from backend.app.rag.corpus_retrieval import CorpusSourceMetadata


MINIMUM_GROUNDING_SCORE = 0.35
SAFE_SOURCE_SUFFIXES = {".java", ".json", ".md", ".py", ".sql", ".txt", ".yaml", ".yml"}
UNSAFE_SOURCE_PARTS = {
    ".env",
    "__pycache__",
    "cache",
    "data",
    "database",
    "dist",
    "generated",
    "models",
    "node_modules",
    "output",
    "outputs",
    "secrets",
}
SUPPORTED_TECHNOLOGIES = {
    "apache storm",
    "dashboard",
    "grafana",
    "kafka",
    "python",
    "pyspark",
    "redis",
    "snowflake",
    "sql",
    "storm",
    "streamlit",
    "watermarking",
    "windowing",
}


def build_grounded_generation_plan(
    brief: AssignmentBrief,
    *,
    assignment_number: int,
    workspace_path: str | Path,
    blueprint_set: AssignmentCodeBlueprintSet,
    corpus_sources: list[CorpusSourceMetadata],
    evidence: AssignmentEvidenceChecklist,
    generation_mode: GenerationMode = "mixed",
) -> GroundedGenerationResult:
    if generation_mode not in {"template_only", "corpus_grounded", "mixed"}:
        raise ValueError("Unsupported assignment generation mode.")

    title, technologies = _assignment_context(brief, assignment_number)
    usable_sources = [] if generation_mode == "template_only" else _usable_sources(corpus_sources)
    blueprint_inputs = [
        (
            item.file_path,
            item.purpose,
            item.technology_area,
            item.generated_content,
        )
        for item in blueprint_set.blueprints
    ]
    blueprint_inputs.extend(
        _supplemental_blueprints(
            assignment_number=assignment_number,
            title=title,
            technologies=technologies,
            brief=brief,
            evidence=evidence,
        )
    )

    grounded_blueprints: list[GroundedFileBlueprint] = []
    warnings: list[str] = []
    unresolved: list[str] = []
    seen_paths: set[str] = set()
    for file_path, purpose, technology_area, content in blueprint_inputs:
        if file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        matching = _matching_sources(
            usable_sources,
            technology_area=technology_area,
            purpose=purpose,
            file_path=file_path,
            assignment_technologies=technologies,
        )
        file_mode = "corpus_grounded" if matching else "template_only"
        file_warnings: list[str] = []
        if generation_mode == "corpus_grounded" and not matching:
            message = f"{file_path} has no usable corpus grounding; safe template fallback used."
            file_warnings.append(message)
            unresolved.append(message)
        grounding = [
            GroundingProvenance(
                source_path=source.source_path,
                chunk_id=source.chunk_id,
                chunk_index=source.chunk_index,
                score=source.score,
                start_line=source.start_line,
                end_line=source.end_line,
                influence=(
                    f"Referenced for {technology_area} structure and terminology; "
                    "starter content remains a vetted Astra template."
                ),
            )
            for source in matching[:3]
        ]
        grounded_blueprints.append(
            GroundedFileBlueprint(
                file_path=file_path,
                purpose=purpose,
                assignment_number=assignment_number,
                technology_area=technology_area,
                generation_mode=file_mode,
                source_ids=[item.chunk_id for item in grounding],
                grounding=grounding,
                generated_content=content.rstrip() + "\n",
                warnings=file_warnings,
            )
        )

    if generation_mode != "template_only" and not any(
        item.generation_mode == "corpus_grounded"
        for item in grounded_blueprints
    ):
        warnings.append("No safe, relevant corpus sources were available; built-in templates were used.")
    unsupported = _unsupported_components(technologies)
    warnings.extend(f"No dedicated adapter for detected component: {item}." for item in unsupported)
    plan_files = [
        GroundedWorkspaceFilePlan(
            path=item.file_path,
            purpose=item.purpose,
            generation_mode=item.generation_mode,
            source_ids=item.source_ids,
            warnings=item.warnings,
        )
        for item in grounded_blueprints
    ]
    directories = sorted(
        {
            Path(item.file_path).parent.as_posix()
            for item in grounded_blueprints
            if Path(item.file_path).parent.as_posix() != "."
        }
    )
    used_source_ids = {
        provenance.chunk_id
        for item in grounded_blueprints
        for provenance in item.grounding
    }
    source_paths = list(
        dict.fromkeys(
            provenance.source_path
            for item in grounded_blueprints
            for provenance in item.grounding
        )
    )
    grounded_count = sum(item.generation_mode == "corpus_grounded" for item in grounded_blueprints)
    summary = CorpusGroundingSummary(
        candidate_source_count=len(corpus_sources),
        usable_source_count=len(used_source_ids),
        grounded_file_count=grounded_count,
        template_file_count=len(grounded_blueprints) - grounded_count,
        excluded_source_count=max(0, len(corpus_sources) - len(used_source_ids)),
        source_paths=source_paths,
    )
    plan = GroundedWorkspaceGenerationPlan(
        assignment_number=assignment_number,
        assignment_title=title,
        workspace_path=str(Path(workspace_path)),
        generation_mode=generation_mode,
        technologies=technologies,
        directories=directories,
        files=plan_files,
        warnings=warnings,
        unresolved_requirements=unresolved,
        evidence_placeholders=[item.title for item in evidence.items if item.status == "missing"],
        report_placeholders=[
            "Dataset Description",
            "Environment Setup",
            "Implementation Steps",
            "Screenshots and Evidence",
            "Analysis Questions",
            "Conclusion",
        ],
        recommended_manual_configuration_steps=[
            "Review every generated file before running it.",
            "Copy .env.example values into a local untracked environment file and fill them manually.",
            "Install and configure required services manually after approval.",
            "Run suggested commands manually; Astra did not execute them.",
        ],
        commands_executed=False,
    )
    return GroundedGenerationResult(
        workspace_generation_plan=plan,
        grounded_file_blueprints=grounded_blueprints,
        corpus_grounding_summary=summary,
        unsupported_components=unsupported,
        generation_warnings=warnings,
        generation_ready=bool(grounded_blueprints),
        generation_mode=generation_mode,
    )


def write_grounded_workspace(
    workspace_root: str | Path,
    blueprints: list[GroundedFileBlueprint],
    *,
    grounding_summary: CorpusGroundingSummary,
    overwrite: bool = False,
) -> GroundedWorkspaceWriteResult:
    root = Path(workspace_root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValueError("Assignment workspace path must be a directory.")
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    refused: list[str] = []
    warnings: list[str] = []

    for blueprint in blueprints:
        try:
            target = _safe_generated_target(root, blueprint.file_path)
            _validate_generated_content(blueprint.file_path, blueprint.generated_content)
        except ValueError as error:
            refused.append(blueprint.file_path)
            warnings.append(f"Refused {blueprint.file_path}: {error}")
            continue
        relative = target.relative_to(root).as_posix()
        if target.exists() and not overwrite:
            skipped.append(relative)
            conflicts.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, blueprint.generated_content)
        created.append(relative)

    return GroundedWorkspaceWriteResult(
        workspace_path=str(root),
        created_files=created,
        skipped_files=skipped,
        conflicts=conflicts,
        refused_files=refused,
        grounding_summary=grounding_summary,
        warnings=warnings,
        overwrite=overwrite,
        commands_executed=False,
        generated_code_executed=False,
    )


def _assignment_context(brief: AssignmentBrief, assignment_number: int) -> tuple[str, list[str]]:
    sections = [
        section
        for index, section in enumerate(brief.sections, start=1)
        if index == assignment_number or f"assignment {assignment_number}" in section.title.lower()
    ]
    title = sections[0].title if sections else f"Assignment {assignment_number}"
    technologies = list(
        dict.fromkeys(
            technology
            for section in sections
            for technology in section.technologies
        )
    )
    if not technologies:
        technologies = list(brief.technologies)
    return title, technologies


def _usable_sources(sources: list[CorpusSourceMetadata]) -> list[CorpusSourceMetadata]:
    usable: list[CorpusSourceMetadata] = []
    seen: set[tuple[str, str, int]] = set()
    for source in sources:
        path = Path(source.source_path.replace("\\", "/"))
        lowered_parts = {part.lower() for part in path.parts}
        lowered_preview = source.text_preview.lower()
        key = (source.source_path, source.chunk_id, source.chunk_index)
        if key in seen or source.score < MINIMUM_GROUNDING_SCORE:
            continue
        if path.suffix.lower() not in SAFE_SOURCE_SUFFIXES:
            continue
        if lowered_parts & UNSAFE_SOURCE_PARTS:
            continue
        if _contains_sensitive_material(lowered_preview):
            continue
        seen.add(key)
        usable.append(source)
    return usable


def _matching_sources(
    sources: list[CorpusSourceMetadata],
    *,
    technology_area: str,
    purpose: str,
    file_path: str,
    assignment_technologies: list[str],
) -> list[CorpusSourceMetadata]:
    target_terms = _technology_terms(
        " ".join([technology_area, purpose, file_path])
    )
    if not target_terms:
        target_terms = _technology_terms(
            " ".join(assignment_technologies)
        )
    matches = []
    for source in sources:
        source_terms = _technology_terms(f"{source.source_path} {source.text_preview}")
        if target_terms & source_terms:
            matches.append(source)
    return sorted(matches, key=lambda item: (-item.score, item.chunk_id))


def _technology_terms(text: str) -> set[str]:
    lowered = text.lower()
    aliases = {
        "apache storm": ("apache storm", "storm", "topology", "spout", "bolt"),
        "grafana": ("grafana", "dashboard", "datasource"),
        "kafka": ("kafka", "producer", "consumer", "topic"),
        "pyspark": ("pyspark", "spark", "watermark", "window"),
        "redis": ("redis",),
        "snowflake": ("snowflake", "warehouse"),
        "sql": ("sql", "schema", "table"),
        "streamlit": ("streamlit", "dashboard"),
    }
    return {
        name
        for name, markers in aliases.items()
        if any(marker in lowered for marker in markers)
    }


def _supplemental_blueprints(
    *,
    assignment_number: int,
    title: str,
    technologies: list[str],
    brief: AssignmentBrief,
    evidence: AssignmentEvidenceChecklist,
) -> list[tuple[str, str, str, str]]:
    technology_text = " ".join(technologies).lower()
    items = [
        ("README.md", "Assignment summary, setup boundary, and manual run order.", "Documentation", _readme(title, technologies)),
        ("architecture.md", "Architecture components and data-flow placeholders.", "Documentation", _architecture(title, technologies)),
        ("evidence_checklist.md", "Evidence placeholders without fabricated completion claims.", "Evidence", _evidence_document(evidence)),
        ("report_outline.md", "Report headings mapped to requirements and source references.", "Report", _report_outline(brief)),
        ("runbook.md", "Manual command suggestions and troubleshooting checkpoints.", "Runbook", _runbook()),
        (".env.example", "Environment-variable placeholders without credentials.", "Configuration", _environment_example()),
    ]
    if "grafana" in technology_text:
        items.append(("dashboard/grafana_setup.md", "Grafana datasource and panel setup guidance.", "Grafana", _grafana_notes()))
    if "storm" in technology_text:
        items.append(("topology/AssignmentTopology.java", "Apache Storm topology starter with placeholder spout and bolt wiring.", "Apache Storm", _storm_topology()))
    if "snowflake" in technology_text or "sql" in technology_text:
        items.append(("snowflake/schema.sql", "Non-destructive Snowflake table template.", "Snowflake/SQL", _snowflake_schema()))
    return items


def _readme(title: str, technologies: list[str]) -> str:
    software = "\n".join(f"- {item}" for item in technologies) or "- Confirm required software from the brief"
    return f"""# {title} starter workspace

This workspace contains review-first starter files. No generated code or command has been executed.

## Architecture

See `architecture.md`. Confirm every component against the assignment brief.

## Required software

{software}

## Manual setup

1. Review `.env.example`; create your own untracked `.env` manually.
2. Replace placeholders with verified local configuration.
3. Install services and dependencies only after approval.

## Suggested run order

1. Validate configuration.
2. Start external services manually.
3. Run producers or loaders manually.
4. Open dashboards manually.
5. Capture real evidence and record observed outputs.

## Safety and limitations

- Corpus sources were references only and were not executed.
- No credentials or results are included.
- Starter code is unverified until manually reviewed and tested.
"""


def _architecture(title: str, technologies: list[str]) -> str:
    components = " -> ".join(technologies) if technologies else "source -> processing -> storage -> dashboard"
    return f"""# Architecture: {title}

Proposed flow: `{components}`

Document verified inputs, transformations, storage targets, dashboard consumers, windowing/watermarking choices, and failure boundaries here.

Corpus references influenced structure only; they do not prove this architecture works.
"""


def _evidence_document(evidence: AssignmentEvidenceChecklist) -> str:
    lines = ["# Evidence checklist", "", "All items are placeholders until real outputs are captured.", ""]
    for item in evidence.items:
        lines.append(f"- [ ] {item.title}: must prove {item.description}")
    return "\n".join(lines) + "\n"


def _report_outline(brief: AssignmentBrief) -> str:
    requirements = [
        task.description
        for section in brief.sections
        for task in section.tasks
    ]
    mapped = "\n".join(f"- {item}" for item in requirements) or "- Map each brief requirement here"
    return f"""# Report outline

## Assignment requirements
{mapped}

## Dataset and environment
[Add verified configuration and dataset facts.]

## Implementation
[Explain reviewed code and architecture. Cite relevant corpus source paths as references only.]

## Evidence
[Insert real screenshots and explain what each proves.]

## Results and analysis
[Add only measured, verified results.]

## Limitations and conclusion
[Document unresolved requirements and limitations.]
"""


def _runbook() -> str:
    return """# Manual runbook

Astra did not execute these suggestions. Review and approve each command manually.

1. Inspect configuration placeholders and expected service addresses.
2. Suggested: create a virtual environment and install reviewed dependencies.
3. Suggested: start required external services using your approved local process.
4. Suggested: run one producer or loader with test input.
5. Verify expected logs or rows before opening the dashboard.
6. Capture screenshots only from actual outputs.

Troubleshooting checkpoints: environment variables, ports, input schema, service health, permissions, and timestamps.
"""


def _environment_example() -> str:
    return """SNOWFLAKE_ACCOUNT=<set_manually>
SNOWFLAKE_USER=<set_manually>
SNOWFLAKE_PASSWORD=<set_manually>
SNOWFLAKE_WAREHOUSE=<set_manually>
SNOWFLAKE_DATABASE=<set_manually>
SNOWFLAKE_SCHEMA=<set_manually>
KAFKA_BOOTSTRAP=<set_manually>
REDIS_HOST=<set_manually>
REDIS_PORT=<set_manually>
"""


def _grafana_notes() -> str:
    return """# Grafana setup guidance

1. Add the approved datasource manually using local placeholders.
2. Confirm connectivity before creating panels.
3. Map panels to verified fields and timestamps.
4. Capture a screenshot showing datasource health and another showing the final dashboard.

No datasource, credential, or dashboard was created automatically.
"""


def _storm_topology() -> str:
    return """package assignment.topology;

import org.apache.storm.Config;
import org.apache.storm.LocalCluster;
import org.apache.storm.topology.TopologyBuilder;

public final class AssignmentTopology {
    private AssignmentTopology() {}

    public static void main(String[] args) throws Exception {
        TopologyBuilder builder = new TopologyBuilder();
        // TODO: register reviewed spout and bolt implementations.
        Config config = new Config();
        config.setDebug(false);
        try (LocalCluster cluster = new LocalCluster()) {
            cluster.submitTopology("assignment-topology", config, builder.createTopology());
            // TODO: replace with an approved lifecycle for manual testing.
        }
    }
}
"""


def _snowflake_schema() -> str:
    return """-- Review identifiers and data types before running manually.
CREATE TABLE IF NOT EXISTS ASSIGNMENT_EVENTS (
    EVENT_TIME TIMESTAMP_NTZ,
    CATEGORY VARCHAR,
    VALUE FLOAT,
    RAW_PAYLOAD VARIANT
);
"""


def _unsupported_components(technologies: list[str]) -> list[str]:
    unsupported = []
    for technology in technologies:
        lowered = technology.lower().strip()
        if not any(item in lowered or lowered in item for item in SUPPORTED_TECHNOLOGIES):
            unsupported.append(technology)
    return list(dict.fromkeys(unsupported))


def _safe_generated_target(root: Path, file_path: str) -> Path:
    normalized = file_path.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError("generated file path must be relative")
    path = Path(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("generated file path must stay inside the assignment workspace")
    if path.name == ".env" or path.suffix.lower() in {".db", ".sqlite", ".bin", ".exe", ".model", ".pkl"}:
        raise ValueError("generated file type is not allowed")
    target = root.joinpath(path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("generated file path escapes the assignment workspace") from error
    return target


def _validate_generated_content(file_path: str, content: str) -> None:
    if len(content.encode("utf-8")) > 256_000:
        raise ValueError("generated file is too large")
    if "\x00" in content:
        raise ValueError("binary content is not allowed")
    if _contains_sensitive_material(content.lower()) and Path(file_path).name != ".env.example":
        raise ValueError("generated content appears to contain sensitive material")
    for match in re.finditer(
        r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*[\"']?([^\s\"']+)",
        content,
    ):
        value = match.group(2).strip()
        if not _placeholder_value(value):
            raise ValueError("generated content appears to contain a real credential")


def _contains_sensitive_material(lowered: str) -> bool:
    return any(marker in lowered for marker in ("private key", "begin rsa", "api_key=sk-", "password=hunter"))


def _placeholder_value(value: str) -> bool:
    lowered = value.lower().strip("<>{}[]()")
    return (
        lowered.startswith(("set_", "replace", "example", "placeholder"))
        or lowered.startswith(("os.getenv", "os.environ"))
        or lowered.startswith("snowflake_")
        or lowered in {"none", "changeme", ""}
    )


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
