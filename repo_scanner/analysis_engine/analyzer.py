# repo_scanner/analysis_engine/analyzer.py

from .rules import (
    find_unused_functions,
    find_entry_points,
    detect_high_coupling,
    detect_circular_dependencies,
    clean_call_graph,
    filter_external_calls,
    split_dependencies,
)


def analyze_graph(graph):
    """
    Run static‑analysis rules on the generated graph.

    Returns a dict with:
        - unused_functions
        - entry_points
        - high_coupling_files
        - circular_dependencies
        - internal_dependencies / external_dependencies (for debugging)
    """
    # 1️⃣ Strip built‑ins, deduplicate, drop empty callers
    raw_call_graph = graph["call_graph"]
    call_graph = clean_call_graph(raw_call_graph)

    # 2️⃣ Remove calls to functions that are not defined locally
    call_graph = filter_external_calls(call_graph, graph["files_data"])

    # 3️⃣ Separate internal vs external imports
    internal_deps, external_deps = split_dependencies(graph["dependency_graph"])

    return {
        "unused_functions": find_unused_functions(call_graph),
        "entry_points": find_entry_points(call_graph),
        "high_coupling_files": detect_high_coupling(internal_deps),
        "circular_dependencies": detect_circular_dependencies(internal_deps),
        # expose split graphs for downstream tooling / debugging
        "internal_dependencies": internal_deps,
        "external_dependencies": external_deps,
    }