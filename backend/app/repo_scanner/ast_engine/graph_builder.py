import os
from .extractor import analyze_file
from .dependency_graph import build_dependency_graph
from .call_graph import build_call_graph

def build_full_graph(repo_path):
    files_data = {}

    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                data = analyze_file(path)

                if data:
                    files_data[path] = data

    dependency_graph = build_dependency_graph(files_data)
    call_graph = build_call_graph(files_data)

    return {
        "files_data": files_data,
        "dependency_graph": dependency_graph,
        "call_graph": call_graph
    }