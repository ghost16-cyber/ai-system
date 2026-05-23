# repo_scanner/intelligence/critic.py
"""
Critic module: evaluates the usefulness of inspection results.
Returns a reward signal that feeds back into priority scoring.
"""

from __future__ import annotations

from typing import Any, Dict


def evaluate_inspection(result: Dict[str, Any]) -> float:
    """
    Evaluate how useful an inspection result is.
    Multi-signal reward: combines complexity, structure, and centrality.
    
    Prevents overfitting by:
      * Normalizing complexity (no single signal dominates)
      * Counting functions/classes (richness matters)
      * Penalizing empty targets
    """
    score = 0.0

    # -------------------------
    # 1. Complexity (normalized, capped)
    # -------------------------
    complexity = result.get("complexity_score", 0)
    # Normalize to 0-2 range (prevents domination)
    score += min(complexity / 5, 2.0)

    # -------------------------
    # 2. Structural richness (count-based)
    # -------------------------
    file_summary = result.get("file_summary", {})
    if file_summary:
        # Count functions and classes (more granular than binary)
        functions = file_summary.get("functions", [])
        classes = file_summary.get("classes", [])
        
        # Weight classes slightly higher (more impactful than functions)
        score += len(functions) * 0.2
        score += len(classes) * 0.3

    # -------------------------
    # 3. Imports = centrality (critical nodes)
    # -------------------------
    imports = result.get("imports", [])
    # Count imports (more imports = more central)
    if len(imports) > 0:
        score += min(len(imports) / 10, 1.0)

    # -------------------------
    # 4. Penalty for weak/empty targets
    # -------------------------
    if complexity == 0 and not file_summary:
        score -= 1.5  # Strong penalty to avoid re-inspecting useless files

    return score