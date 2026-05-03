import os
from pathlib import Path

def scan_repository(root_path: str):
    print("DEBUG PATH:", root_path)

    repo_data = {
        "files": [],
        "languages": set(),
        "file_types": {}
    }

    for dirpath, _, filenames in os.walk(root_path):
        print("VISITING:", dirpath, "FILES:", filenames)

        for file in filenames:
            full_path = os.path.join(dirpath, file)
            ext = Path(file).suffix

            repo_data["files"].append(full_path)

            if ext not in repo_data["file_types"]:
                repo_data["file_types"][ext] = 0
            repo_data["file_types"][ext] += 1

            if ext == ".py":
                repo_data["languages"].add("python")

    repo_data["languages"] = list(repo_data["languages"])
    return repo_data