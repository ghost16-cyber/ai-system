# repo_scanner/llm_engine/feedback_builder.py
from __future__ import annotations

from typing import Any, Dict, List


def build_inspection_feedback_context(
    inspect_results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Convert raw inspection results into a compact, prioritized structure that
    highlights the most useful signals for the LLM.

    For each result we keep:
      * target – the file / directory / module name
      * kind   – the target kind (file, directory, etc.)
      * summary – a short human‑readable summary
      * role & complexity (for files)
      * a few representative functions / classes (for files)
      * a short list of files (for directories)
    """
    compact: List[Dict[str, Any]] = []

    for r in inspect_results:
        entry: Dict[str, Any] = {
            "target": r.get("target"),
            "kind": r.get("target_kind"),
            "summary": r.get("summary"),
        }

        # File‑specific details
        fs = r.get("file_summary")
        if fs:
            entry.update(
                {
                    "role": fs.get("role_hint"),
                    "complexity": fs.get("complexity_score"),
                    "functions": fs.get("functions", [])[:5],
                    "classes": fs.get("classes", [])[:5],
                }
            )

        # Directory‑specific details
        ds = r.get("directory_summary")
        if ds:
            entry["files"] = ds.get("files", [])[:8]

        compact.append(entry)

    return compact



