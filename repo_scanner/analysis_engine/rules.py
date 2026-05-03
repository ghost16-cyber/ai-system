# repo_scanner/analysis_engine/rules.py

# ----------------------------------------------------------------------
# Built‑in functions that should be ignored in the call graph
# ----------------------------------------------------------------------
BUILTINS = {
    "print", "len", "range", "str", "int", "list", "dict", "set",
    "isinstance", "Exception"
}


def clean_call_graph(call_graph):
    """
    Remove calls to built‑ins, deduplicate callee lists and drop empty callers.
    """
    cleaned = {}
    for caller, callees in call_graph.items():
        # filter out built‑ins
        filtered = [c for c in callees if c not in BUILTINS]
        if not filtered:
            continue          # drop callers with no remaining callees
        cleaned[caller] = list(set(filtered))   # deduplicate
    return cleaned


def filter_external_calls(call_graph, files_data):
    """
    Keep only calls that target functions defined inside the repository.

    Parameters
    ----------
    call_graph : dict
        Mapping ``caller -> [callee, ...]`` (already cleaned of built‑ins).
    files_data : dict
        ``{file_path: {"functions": [...], ...}}`` from ``build_full_graph``.

    Returns
    -------
    dict
        Call‑graph where each callee list contains only local functions,
        deduplicated, and empty callers removed.
    """
    # Collect every function name defined in the repo
    local_functions = set()
    for data in files_data.values():
        if data and "functions" in data:
            local_functions.update(data["functions"])

    filtered = {}
    for caller, callees in call_graph.items():
        # keep only local functions
        local_callees = [c for c in callees if c in local_functions]
        if not local_callees:
            continue          # drop callers that call nothing local
        filtered[caller] = list(set(local_callees))   # deduplicate
    return filtered


def split_dependencies(dependency_graph):
    """Separate internal vs external imports."""
    internal, external = {}, {}
    for file, deps in dependency_graph.items():
        internal[file] = []
        external[file] = []
        for dep in deps:
            if dep.startswith("src") or dep.startswith("repo_scanner"):
                internal[file].append(dep)
            else:
                external[file].append(dep)
    return internal, external


def find_unused_functions(call_graph):
    """Functions defined but never called (filters test/private/main)."""
    used = set()
    all_funcs = set(call_graph.keys())
    for callers in call_graph.values():
        used.update(callers)
    unused = all_funcs - used
    return [
        f for f in unused
        if f
        and not f.startswith("test_")
        and not f.startswith("_")
        and f != "main"
    ]


def find_entry_points(call_graph):
    """
    Functions never called by another function (ignores private helpers).
    Prioritises functions that look like program entry points (contain
    ``main`` or ``run``) by sorting them first.
    """
    called = set()
    for funcs in call_graph.values():
        called.update(funcs)

    entry_points = [
        f for f in call_graph
        if f not in called and f and not f.startswith("_")
    ]

    # Prioritise “main” / “run” style entry points
    entry_points.sort(key=lambda f: 0 if ("main" in f or "run" in f) else 1)
    return entry_points


def detect_high_coupling(dependency_graph, threshold=10):
    """Files that import many other internal modules."""
    return {
        file: len(deps)
        for file, deps in dependency_graph.items()
        if len(deps) > threshold
    }


def detect_circular_dependencies(dependency_graph):
    visited, stack, cycles = set(), set(), []

    def visit(node):
        if node in stack:
            cycles.append(node)
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        for dep in dependency_graph.get(node, []):
            if dep in dependency_graph:  # only follow local files
                visit(dep)
        stack.remove(node)

    for node in dependency_graph:
        visit(node)
    return cycles