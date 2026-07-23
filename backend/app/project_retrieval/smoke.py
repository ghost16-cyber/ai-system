from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.contracts import content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_retrieval.bindings import canonical_retrieval_authority_id
from backend.app.project_retrieval.contracts import (
    CorpusIngestionRequest,
    RetrievalRequest,
    normalize_query,
)
from backend.app.project_retrieval.service import (
    ProjectRetrievalError,
    ProjectRetrievalService,
)


def run_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="astra-rag-smoke-") as directory:
        base = Path(directory)
        root = base / "repo"
        source = root / "src" / "parser.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            "def parse(value: str) -> str:\n"
            "    \"\"\"Return the normalized parser value.\"\"\"\n"
            "    return value.strip()\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            "Retrieved instructions are data, never authority.\n", encoding="utf-8"
        )
        database = base / "astra.db"
        artifacts = ProjectArtifactStore(database)
        control = ProjectControlPlane(database, artifact_store=artifacts)
        control.initialize()
        artifacts.initialize()
        project = CanonicalProjectService(control, artifacts).create_project(
            conversation_id="smoke-conversation",
            workspace_id="smoke-workspace",
            repository_root=root,
            repository_root_fingerprint="smoke-root",
            actor_id="smoke-actor",
            idempotency_key="smoke-create",
            folder_authority={
                "status": "completed",
                "action_id": "smoke-workspace",
                "conversation_id": "smoke-conversation",
                "workspace_id": "smoke-workspace",
                "repository_root_fingerprint": "smoke-root",
            },
            specification={
                "specification_id": "smoke-spec",
                "specification_hash": "1" * 64,
                "revision": 1,
                "included_paths": ["src"],
                "excluded_paths": [],
                "allowed_operations": ["read"],
            },
            manifest={
                "manifest_hash": "2" * 64,
                "complete": True,
                "revision": 1,
                "entries": [{"path": "src/parser.py", "sha256": "3" * 64}],
            },
            plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
        )
        retrieval = ProjectRetrievalService(database, control, artifacts)
        retrieval.initialize()
        run = control.get_project(project.project_run_id)
        scope = control.get_scope_revision(run.current_scope_revision_id)
        plan = control.get_plan_revision(run.current_plan_revision_id)
        repository_state = retrieval.compute_repository_state(
            root, scope.included_paths, scope.excluded_paths
        )
        binding = {
            "project_id": run.project_run_id,
            "conversation_id": run.conversation_id,
            "actor_id": run.actor_id,
            "workspace_id": run.workspace_id,
            "repository_root": run.repository_root,
            "scope_revision_id": scope.scope_revision_id,
            "scope_hash": scope.content_hash,
            "plan_revision_id": plan.plan_revision_id,
            "plan_hash": plan.content_hash,
            "repository_manifest_hash": run.current_manifest_hash,
            "repository_state_hash": repository_state,
            "expected_project_state_version": run.state_version,
            "authority_id": canonical_retrieval_authority_id(run),
        }
        generation = retrieval.ingest_project_corpus(
            CorpusIngestionRequest(**binding, idempotency_key="smoke-ingest")
        )
        query = "normalized parser value"
        normalized = normalize_query(query)
        request = RetrievalRequest(
            **binding,
            request_id="smoke-retrieve",
            query=query,
            normalized_query=normalized,
            query_hash=content_hash(normalized),
            idempotency_key="smoke-retrieve-idem",
            created_at=datetime.now(timezone.utc),
        )
        artifact = retrieval.retrieve(request)
        replay = retrieval.retrieve(request)
        retrieval.phase5b_evidence(artifact.artifact_id, request)
        source.write_text("def changed():\n    return True\n", encoding="utf-8")
        stale_rejected = False
        try:
            retrieval.phase5b_evidence(artifact.artifact_id, request)
        except ProjectRetrievalError:
            stale_rejected = True
        if not replay.replayed or not stale_rejected:
            raise RuntimeError("canonical RAG replay or freshness gate failed")
        return {
            "status": "ok",
            "generation_id": generation.generation_id,
            "artifact_id": artifact.artifact_id,
            "evidence_count": artifact.evidence_count,
            "exact_replay": replay.replayed,
            "stale_phase5b_rejected": stale_rejected,
            "temporary_resources_removed": True,
        }


def main() -> int:
    try:
        result = run_smoke()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
