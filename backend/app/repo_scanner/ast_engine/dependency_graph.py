def build_dependency_graph(files_data):
    graph = {}

    for file, data in files_data.items():
        if not data:
            continue

        graph[file] = data["imports"]

    return graph