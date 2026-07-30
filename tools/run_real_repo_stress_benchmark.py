from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRESS_DIR = ROOT / "benchmarks" / "real_repo_stress"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    args = sys.argv[1:]
    if "--cases-dir" not in args:
        args = ["--cases-dir", str(DEFAULT_STRESS_DIR), *args]
    if "--max-patch-changed-lines" not in args:
        args = ["--max-patch-changed-lines", "8", *args]
    if "--rollback-on-test-failure" not in args:
        args = ["--rollback-on-test-failure", *args]

    from tools.run_repair_benchmark import main as run_repair_benchmark_main

    original = sys.argv
    try:
        sys.argv = ["run_repair_benchmark.py", *args]
        run_repair_benchmark_main()
    finally:
        sys.argv = original


if __name__ == "__main__":
    main()
