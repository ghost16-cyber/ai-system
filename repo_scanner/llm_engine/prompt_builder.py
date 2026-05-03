# repo_scanner/llm_engine/prompt_builder.py
from __future__ import annotations

import json
from typing import Any, List, Dict


def trim_list(items: List[Any], limit: int = 20) -> List[Any]:
    """Return at most ``limit`` items from a list."""
    return items[:limit] if isinstance(items, list) else []


def trim_dict_items(data: Dict[str, Any], limit: int = 20) -> Dict[str, Any]:
    """Return a dict containing at most ``limit`` key/value pairs."""
    if not isinstance(data, dict):
        return {}
    return dict(list(data.items())[:limit])


def compact_files(files: List[Dict[str, Any]], limit: int = 30) -> List[str]:
    """
    Convert a list of file dicts (as produced by ``scan_repository``)
    into a short list of file paths.
    """
    if not isinstance(files, list):
        return []
    result: List[str] = []
    for f in files[:limit]:
        if isinstance(f, dict):
            path = f.get("path")
            if isinstance(path, str):
                result.append(path)
    return result


def build_repo_reasoning_prompt(
    scan_summary: Dict[str, Any],
    graph_analysis: Dict[str, Any],
    user_task: str = "Analyze this repository and suggest the next best engineering steps.",
) -> str:
    """
    Convert scanner + graph-analysis output into a compact LLM prompt.
    The prompt is deliberately short because LLMs have limited context windows.
    """
    # Repository-level context (trimmed)
    compact_context = {
        "repository": {
            "path": scan_summary.get("repository"),
            "total_files": scan_summary.get("total_files"),
            "total_directories": scan_summary.get("total_directories"),
            "size_bytes": scan_summary.get("size_bytes"),
            "languages": scan_summary.get("languages"),
            "frameworks": scan_summary.get("frameworks"),
            "structure": scan_summary.get("structure"),
            "python_files_parsed": scan_summary.get("python_files_parsed"),
            "python_parse_errors": scan_summary.get("python_parse_errors"),
            "files_sample": compact_files(scan_summary.get("files", []), limit=30),
        },
        "analysis": {
            "unused_functions_sample": trim_list(
                graph_analysis.get("unused_functions", []), 20
            ),
            "entry_points_sample": trim_list(
                graph_analysis.get("entry_points", []), 20
            ),
            "high_coupling_files_sample": trim_dict_items(
                graph_analysis.get("high_coupling_files", {}), 20
            ),
            "circular_dependencies_sample": trim_list(
                graph_analysis.get("circular_dependencies", []), 20
            ),
        },
    }

    # Final prompt
    return f"""You are an AI coding assistant analyzing a software repository.
User task: {user_task}
Repository analysis context: {json.dumps(compact_context, indent=2)}
Respond in this exact structure:
1. Repository identity:
   - What kind of project is this?
2. Current architecture:
   - What major components seem to exist?
3. Risks:
   - What are the most important technical risks?
4. Recommended next steps:
   - Give 3 to 5 concrete engineering steps.
5. Files or areas to inspect next:
   - Name the most important areas based on the analysis.
Rules:
- Be concrete.
- Do not invent files not shown in the context.
- Clearly separate facts from assumptions.
- Prefer practical next steps over generic advice.
""".strip()


def build_structured_repo_decision_prompt(
    scan_summary: Dict[str, Any],
    graph_analysis: Dict[str, Any],
    user_task: str = "Analyze this repository and recommend what to inspect or build next.",
) -> str:
    compact_context = {
        "repository": {
            "path": scan_summary.get("repository"),
            "total_files": scan_summary.get("total_files"),
            "total_directories": scan_summary.get("total_directories"),
            "size_bytes": scan_summary.get("size_bytes"),
            "languages": scan_summary.get("languages"),
            "frameworks": scan_summary.get("frameworks"),
            "structure": scan_summary.get("structure"),
            "python_files_parsed": scan_summary.get("python_files_parsed"),
            "python_parse_errors": scan_summary.get("python_parse_errors"),
            "files_sample": compact_files(scan_summary.get("files", []), limit=30),
        },
        "analysis": {
            "unused_functions_sample": trim_list(
                graph_analysis.get("unused_functions", []), 20
            ),
            "entry_points_sample": trim_list(
                graph_analysis.get("entry_points", []), 20
            ),
            "high_coupling_files_sample": trim_dict_items(
                graph_analysis.get("high_coupling_files", {}), 20
            ),
            "circular_dependencies_sample": trim_list(
                graph_analysis.get("circular_dependencies", []), 20
            ),
        },
    }

    return f"""You are an AI coding assistant analyzing a software repository.
User task:
{user_task}

Repository analysis context:
{json.dumps(compact_context, indent=2)}

Return ONLY valid JSON.
Do not use markdown.
Do not include explanations outside JSON.
The JSON must match this exact schema:
{{
  "repo_identity": "short repository classification",
  "architecture_summary": "short architecture summary",
  "confidence": 0.0,
  "risks": [
    {{
      "risk": "technical risk",
      "severity": "low|medium|high",
      "evidence": "evidence from context"
    }}
  ],
  "recommended_actions": [
    {{
      "action_type": "inspect_file|inspect_module|refactor|add_tests|improve_docs|optimize|fix_bug|continue_analysis",
      "action": "specific recommended action",
      "priority": 1,
      "target_area": "file, folder, module, or subsystem",
      "requires_file_edit": false,
      "rationale": "why this action matters"
    }}
  ],
  "inspect_next": [
    "specific file, folder, module, or subsystem"
  ],
  "assumptions": [
    "assumption made because context is incomplete"
  ]
}}
Rules:
- Use only the provided context.
- Do not invent files.
- Do not invent frameworks; an empty frameworks list means no framework was detected.
- Use lowercase enum values exactly as shown.
- Priority must be 1 to 5.
- Confidence must be between 0.0 and 1.0.
- If unsure, put the uncertainty in assumptions.""".strip()
