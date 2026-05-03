import os

def analyze_structure(repo_path):
    structure = {
        "has_models": False,
        "has_views": False,
        "has_routes": False
    }

    for root, dirs, files in os.walk(repo_path):
        for d in dirs:
            if "model" in d.lower():
                structure["has_models"] = True
            if "view" in d.lower():
                structure["has_views"] = True
            if "route" in d.lower() or "api" in d.lower():
                structure["has_routes"] = True

    return structure