def build_call_graph(files_data):
    graph = {}

    for file, data in files_data.items():
        if not data:
            continue

        for caller, callee in data["calls"]:
            if caller not in graph:
                graph[caller] = []

            graph[caller].append(callee)

    return graph