from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.project_retrieval.evaluation.runner import (
    run_evaluation,
    write_reports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--output-dir", default=".work/rag-evaluation")
    parser.add_argument("--learned", action="store_true")
    arguments = parser.parse_args()
    del arguments.repository_root
    run = run_evaluation(deterministic_only=not arguments.learned)
    outputs = write_reports(run, Path(arguments.output_dir))
    print(json.dumps({
        "status": "ok" if run.guardrails_passed else "failed",
        "run_id": run.run_id,
        "outputs": [path.as_posix() for path in outputs],
        "guardrail_failures": run.guardrail_failures,
    }, sort_keys=True))
    return 0 if run.guardrails_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
