from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pandas as pd


OUTPUT_PATH = Path("data/specialists/intent_examples_curated.csv")
VALID_LABELS = (
    "backend",
    "frontend",
    "debugging",
    "testing",
    "rag",
    "training",
    "runtime",
    "general",
)
REQUIRED_COLUMNS = ("user_message", "final_label", "label_status", "source")


EXAMPLE_TOPICS: dict[str, list[str]] = {
    "backend": [
        "add a FastAPI route for training dataset status",
        "validate the chat run response schema",
        "persist RAG evaluation results in local JSON",
        "read chat history from SQLite safely",
        "return clean errors from the label endpoint",
        "wire a service layer for intent examples",
        "add pagination to the training examples API",
        "make the export endpoint reject unsupported formats",
        "store grounding metadata on chat runs",
        "load project index status from the backend",
        "add a request model for manual training examples",
        "keep the feedback endpoint backward compatible",
        "summarize specialist model audit records",
        "add database migration columns for sources",
        "protect API paths from missing workspace roots",
        "return source distributions from the dataset status route",
        "make the chat stream save exactly one run",
        "add validation to the RAG evaluation request",
        "implement a service for confirmed examples",
        "expose combined dataset metrics through FastAPI",
        "include label counts in the backend response",
        "handle malformed JSONL rows without crashing",
        "add a route for candidate model reports",
        "persist router comparison metrics",
        "update schemas for curated training rows",
    ],
    "frontend": [
        "show training dataset counts on the System page",
        "add a React dropdown for corrected labels",
        "make the RAG evaluation details easier to scan",
        "fix the History page source list layout",
        "add a Vite UI button to export CSV",
        "render grounding status in the chat metadata panel",
        "style failed evaluation cases with a warning tone",
        "add TypeScript types for training examples",
        "make the System card wrap long file paths",
        "show source paths in a compact component",
        "disable the confirm button while saving a label",
        "add a simple notes input for example review",
        "make the label distribution fit on mobile",
        "refresh frontend data after export completes",
        "show a loading spinner for RAG evaluation",
        "keep the chat composer usable on small screens",
        "add empty states for no training examples",
        "display selected specialist confidence in the UI",
        "make the settings page keep RAG toggle state",
        "add a source count metric to chat results",
        "fix CSS spacing in the training review panel",
        "show latest path hit rate on the Project RAG card",
        "add React state for dataset export notices",
        "render per-turn grounding in History details",
        "make long assistant responses wrap cleanly",
    ],
    "debugging": [
        "debug why the chat stream stores two runs",
        "find the traceback from the training export endpoint",
        "investigate why RAG evaluation says index missing",
        "fix the failing pytest around source metadata",
        "the System page crashes after running evaluation",
        "debug a FastAPI 422 from the label endpoint",
        "figure out why the Vite build fails on imports",
        "track down a SQLite column missing error",
        "find why grounding status is always none",
        "debug the rejected model artifact save failure",
        "the JSONL logger is duplicating chat run IDs",
        "investigate a KeyError in label distribution",
        "fix a broken source path display in History",
        "debug why npm run build fails on the WSL path",
        "the RAG search returns empty results for project files",
        "find why the classifier predicts only general",
        "debug an exception when reading malformed CSV",
        "fix a crash when latest evaluation is null",
        "trace why chat fallback does not store metadata",
        "investigate failing assertions in test_training_data",
        "debug why source_count is zero with RAG enabled",
        "find the cause of an import error in scripts",
        "fix the model store rejecting metadata unexpectedly",
        "debug why filtered training examples are missing",
        "the export route returns a path but no file exists",
    ],
    "testing": [
        "add pytest coverage for training dataset status",
        "write tests for duplicate chat run logging",
        "add a fixture for malformed intent examples",
        "test the RAG evaluation missing-index response",
        "write assertions for grounding source paths",
        "add tests for CSV export row counts",
        "mock the label policy for frontend examples",
        "add pytest cases for invalid label statuses",
        "test that chat workflow still saves one run",
        "add a regression test for source deduplication",
        "write tests for the combined dataset merge priority",
        "add coverage for suspicious dataset detection",
        "test the sklearn quality gate failure path",
        "add fixtures for manual training examples",
        "write tests for FastAPI route validation",
        "test History rendering with RAG sources",
        "add unit tests for text redaction",
        "write pytest cases for label correction",
        "test JSONL persistence after review updates",
        "add a test for exported confirmed examples only",
        "write a fixture for project RAG chunks",
        "test frontend TypeScript contracts by building",
        "add coverage for router comparison metrics",
        "test that rejected models are not promoted",
        "add assertions for label distribution output",
    ],
    "rag": [
        "evaluate whether RAG retrieves chat workflow files",
        "add source-grounded snippets to the chat prompt",
        "inspect why project context search misses backend files",
        "run RAG evaluation for the built-in cases",
        "improve chunking for long TypeScript files",
        "show missing expected paths from retrieval results",
        "search the project index for training data code",
        "explain how RAG context is compacted for prompts",
        "verify line ranges in retrieved source snippets",
        "add a case for source-aware chat grounding",
        "find documents related to specialist model promotion",
        "check whether embeddings are needed before training",
        "review the retrieval scores for frontend queries",
        "compare returned paths against expected RAG paths",
        "inspect the project index file list",
        "add a RAG case for chat history storage",
        "explain why a query received weak grounding",
        "audit source paths used in the latest chat run",
        "search for where RAG indexing is implemented",
        "summarize retrieved context before answering",
        "find source snippets for the System page card",
        "check whether vector search should replace lexical search",
        "evaluate source grounding on backend questions",
        "show which files support the answer",
        "refresh the RAG index before evaluation",
    ],
    "training": [
        "build a curated intent dataset for Astra labels",
        "train a sklearn router candidate from confirmed rows",
        "evaluate macro F1 for the intent classifier",
        "export corrected examples for specialist training",
        "review label distribution before model promotion",
        "compare the sklearn router against rule-based routing",
        "reject the candidate if recall is too low",
        "add examples for backend and frontend labels",
        "audit noisy Hugging Face examples before training",
        "merge curated rows with StackOverflow seed data",
        "save the model as candidate but do not promote it",
        "inspect per-label precision and recall",
        "create training rows from chat feedback",
        "mark useful examples as confirmed",
        "correct mislabeled runtime examples",
        "build a balanced dataset for intent routing",
        "evaluate whether the model overpredicts general",
        "add notes to bad training examples",
        "prepare a dataset for specialist model training",
        "check the quality gate thresholds",
        "review examples where sklearn beats rules",
        "export a CSV of confirmed labels only",
        "train on curated data after audit passes",
        "summarize candidate model accuracy",
        "measure recall for every Astra label",
    ],
    "runtime": [
        "start the FastAPI server with uvicorn",
        "debug Ollama Qwen connection settings",
        "check whether CUDA is available for local models",
        "fall back to CPU when the GPU is out of memory",
        "fix npm failing because the port is already used",
        "inspect Node and Vite runtime versions",
        "choose safe settings for low VRAM mode",
        "restart the backend server on a different port",
        "verify Qwen model selection in Ollama",
        "explain why uvicorn is not reachable",
        "check GPU memory before running training",
        "make the frontend dev server use another port",
        "diagnose a server startup timeout",
        "inspect local runtime context for this machine",
        "handle npm build issues on the WSL path",
        "choose CPU fallback for the sklearn experiment",
        "check if the backend API is online",
        "debug CUDA out of memory during inference",
        "verify Ollama base URL configuration",
        "start Vite without conflicting with port 5173",
        "inspect performance constraints before training",
        "explain the runtime safety decision",
        "check available RAM and GPU VRAM",
        "run the app without cloud calls",
        "fix server deployment settings for local preview",
    ],
    "general": [
        "explain the next safe step for this phase",
        "summarize what Astra can currently do",
        "plan the work before changing code",
        "give me a high-level architecture overview",
        "what should we prioritize after RAG evaluation",
        "explain the difference between these phases",
        "summarize the backend and frontend responsibilities",
        "help me decide whether to train yet",
        "describe the current specialist workflow",
        "what are the risks in this approach",
        "give a concise status update for the project",
        "explain how chat memory works at a high level",
        "plan a safe implementation sequence",
        "summarize the dataset quality concerns",
        "what should the user review manually",
        "explain the quality gate in plain language",
        "outline a roadmap for improving routing",
        "summarize recent changes without code details",
        "what is the safest next experiment",
        "explain why we should not promote the model yet",
        "give me a project health summary",
        "plan how to clean noisy examples",
        "describe the current data flow",
        "explain the purpose of source grounding",
        "help me choose the next validation step",
    ],
}

PREFIXES = (
    "Can you",
    "Please",
    "Help me",
    "I need to",
    "Let's",
)


def build_curated_dataset(output_path: str | Path = OUTPUT_PATH, *, per_label: int = 50) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for label in VALID_LABELS:
        topics = EXAMPLE_TOPICS[label]
        for index in range(per_label):
            topic = topics[index % len(topics)]
            prefix = PREFIXES[(index // len(topics)) % len(PREFIXES)]
            message = f"{prefix} {topic}."
            normalized = normalize_message(message)
            if normalized in seen:
                raise ValueError(f"Duplicate curated message: {message}")
            if not 15 <= len(message) <= 220:
                raise ValueError(f"Curated message length out of range: {message}")
            seen.add(normalized)
            rows.append(
                {
                    "user_message": message,
                    "final_label": label,
                    "label_status": "confirmed",
                    "source": "curated",
                }
            )
    frame = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return frame


def normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def main() -> None:
    frame = build_curated_dataset()
    distribution = Counter(frame["final_label"])
    print(f"Wrote {len(frame)} examples to {OUTPUT_PATH}")
    print("Final label distribution:")
    for label in VALID_LABELS:
        print(f"- {label}: {distribution[label]}")


if __name__ == "__main__":
    main()
