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
            "python_parse_errors_count": len(
                scan_summary.get("python_parse_errors", [])
            ),
            "files_sample": compact_files(scan_summary.get("files", []), limit=20),
        },
        "analysis": {
            "unused_functions_sample": trim_list(
                graph_analysis.get("unused_functions", []), 10
            ),
            "entry_points_sample": trim_list(
                graph_analysis.get("entry_points", []), 10
            ),
            "high_coupling_files_sample": trim_dict_items(
                graph_analysis.get("high_coupling_files", {}), 10
            ),
            "circular_dependencies_sample": trim_list(
                graph_analysis.get("circular_dependencies", []), 5
            ),
        },
    }

    return f"""
You are an AI coding assistant analyzing a software repository.

User task:
{user_task}

Repository analysis context:
{json.dumps(compact_context, indent=2)}

Return ONLY compact valid JSON.
Do not use markdown fences.
Do not include explanations outside JSON.
Do not invent files, folders, bugs, vulnerabilities, or errors not shown in context.
Use short strings.

Limits:
- Max 2 risks.
- Max 3 recommended_actions.
- Max 5 inspect_next items.
- Max 3 assumptions.

The JSON must match this exact shape:

{{
  "repo_identity": "short repository classification",
  "architecture_summary": "short architecture summary",
  "confidence": 0.0,
  "risks": [
    {{
      "risk": "technical risk",
      "severity": "low",
      "evidence": "evidence from context"
    }}
  ],
  "recommended_actions": [
    {{
      "action_type": "inspect_module",
      "action": "specific recommended action",
      "priority": 1,
      "target_area": "specific file/folder/module/subsystem",
      "requires_file_edit": false,
      "rationale": "short reason"
    }}
  ],
  "inspect_next": ["specific file/folder/module/subsystem"],
  "assumptions": ["short uncertainty note"]
}}

Allowed severity values:
- low
- medium
- high

Allowed action_type values:
- inspect_file
- inspect_module
- refactor
- add_tests
- improve_docs
- optimize
- fix_bug
- continue_analysis

Important:
- If evidence is weak, set confidence below 0.7.
- If no real security evidence exists, do not claim security vulnerabilities.
- Never list "no known vulnerabilities", "no specific evidence", or absence of evidence as a risk.
- A risk must cite an actual scanner or graph signal from context.
- If no risks are proven by context, return "risks": [].
- Prefer inspect_module or continue_analysis before refactor/fix_bug unless context proves an edit is needed.
- For inspect_file, inspect_module, and continue_analysis actions, set requires_file_edit to false.
""".strip()


def build_json_repair_prompt(
    broken_output: str,
    parse_error: str,
) -> str:
    required_fixes: list[str] = []
    if "Security risks require positive evidence" in parse_error:
        required_fixes.append(
            'Set "risks" to [] and remove unsupported security-risk assumptions.'
        )
    if "actions must set requires_file_edit to false" in parse_error:
        required_fixes.append(
            "Set requires_file_edit to false for inspect_file, inspect_module, and continue_analysis actions."
        )
    if "Ungrounded file references" in parse_error:
        required_fixes.append(
            "Remove ungrounded file references from recommended_actions and inspect_next."
        )

    required_fix_text = "\n".join(f"- {fix}" for fix in required_fixes)
    if not required_fix_text:
        required_fix_text = "- Fix every validation error shown below."

    return f"""
Fix the following broken LLM output into valid compact JSON only.
Do not use markdown.
Do not explain.
Do not add new facts.
Preserve the original meaning as much as possible, except invalid items must be removed or corrected.
The parse error is binding.
Do not keep any risk, action, or field that caused the parse error.

The JSON must have exactly these top-level keys:
repo_identity, architecture_summary, confidence, risks, recommended_actions, inspect_next, assumptions

Required fixes:
{required_fix_text}

Constraints:
- confidence must be 0.0 to 1.0
- risks must be a list
- recommended_actions must be a list
- inspect_next must be a list of strings
- assumptions must be a list of strings
- max 2 risks
- max 3 recommended_actions
- max 5 inspect_next items
- max 3 assumptions
- inspect_file, inspect_module, and continue_analysis actions must set requires_file_edit to false
- do not list absence of evidence as a risk
- if a security risk has no positive evidence, remove that risk
- if evidence says "no evidence", "no specific evidence", "no known", or "not found", remove that risk
- if unsure whether a risk is valid, return "risks": []
- remove file-like targets named in ungrounded file reference errors

Parse error:
{parse_error}

Broken output:
{broken_output}
""".strip()
