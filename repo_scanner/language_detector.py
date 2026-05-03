import json
import os

def detect_framework(repo_path):
    framework = []

    # Python
    req_file = os.path.join(repo_path, "requirements.txt")
    if os.path.exists(req_file):
        content = open(req_file).read().lower()
        if "django" in content:
            framework.append("django")
        if "flask" in content:
            framework.append("flask")
        if "fastapi" in content:
            framework.append("fastapi")

    # Node
    pkg_file = os.path.join(repo_path, "package.json")
    if os.path.exists(pkg_file):
        pkg = json.load(open(pkg_file))
        deps = str(pkg).lower()

        if "react" in deps:
            framework.append("react")
        if "next" in deps:
            framework.append("nextjs")

    return framework