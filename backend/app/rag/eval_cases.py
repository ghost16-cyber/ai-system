from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RagEvaluationCase:
    case_id: str
    query: str
    expected_paths: tuple[str, ...]
    category: str
    description: str
    expected_terms: tuple[str, ...] = field(default_factory=tuple)


ASTRA_RAG_EVALUATION_CASES: tuple[RagEvaluationCase, ...] = (
    RagEvaluationCase(
        case_id="chat-streaming",
        query="How does chat streaming work?",
        expected_paths=(
            "backend/app/main.py",
            "backend/app/chat_workflow.py",
            "frontend/src/App.tsx",
            "frontend/src/clients/astraClient.ts",
        ),
        expected_terms=("chat_stream", "StreamingResponse", "streamChat"),
        category="chat",
        description="Finds the backend and frontend pieces that stream chat runs.",
    ),
    RagEvaluationCase(
        case_id="project-rag-indexing",
        query="Where is project RAG indexing implemented?",
        expected_paths=("backend/app/rag/project_indexer.py",),
        expected_terms=("build_project_index", "search_project_index"),
        category="rag",
        description="Finds the project-aware index builder and search implementation.",
    ),
    RagEvaluationCase(
        case_id="chat-run-storage",
        query="How are chat runs stored?",
        expected_paths=(
            "backend/app/chat_workflow.py",
            "backend/app/main.py",
        ),
        expected_terms=("store_chat_run", "ChatRunResponse"),
        category="history",
        description="Finds the chat workflow output and endpoint storage path.",
    ),
)


def list_evaluation_cases() -> list[dict[str, object]]:
    return [
        {
            "case_id": item.case_id,
            "query": item.query,
            "expected_paths": list(item.expected_paths),
            "expected_terms": list(item.expected_terms),
            "category": item.category,
            "description": item.description,
        }
        for item in ASTRA_RAG_EVALUATION_CASES
    ]
