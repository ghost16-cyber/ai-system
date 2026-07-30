from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "benchmarks" / "real_repo_stress"


@dataclass(frozen=True)
class StressCase:
    case_id: str
    bug_type: str
    difficulty: str
    goal: str
    files: dict[str, str]
    expected_source_file: str
    expected_test_file: str
    expected_changed_files: tuple[str, ...]
    expected_failure_category: str


CASES: tuple[StressCase, ...] = (
    StressCase(
        case_id="stress_001_missing_dependency",
        bug_type="dependency_error",
        difficulty="hard",
        goal="Fix the failing missing dependency test without installing packages.",
        files={
            "app/__init__.py": "",
            "app/reporting.py": (
                "import definitely_missing_package\n\n\n"
                "def render_report(value: str) -> str:\n"
                "    return definitely_missing_package.render(value)\n"
            ),
            "tests/test_reporting.py": (
                "from app.reporting import render_report\n\n\n"
                "def test_render_report():\n"
                "    assert render_report('sales') == 'sales'\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/reporting.py",
        expected_test_file="tests/test_reporting.py",
        expected_changed_files=("app/reporting.py",),
        expected_failure_category="no_patch_proposed",
    ),
    StressCase(
        case_id="stress_002_ambiguous_source_file",
        bug_type="ambiguous_source",
        difficulty="hard",
        goal="Fix the failing normalize test; there are two similar modules.",
        files={
            "app/__init__.py": "",
            "app/normalize.py": "def normalize(value: str) -> str:\n    return value.strip()\n",
            "app/normalizer.py": "def normalize(value: str) -> str:\n    return value\n",
            "tests/test_normalize.py": (
                "from app.normalize import normalize\n\n\n"
                "def test_normalize_lowercases():\n"
                "    assert normalize(' Name ') == 'name'\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/normalize.py",
        expected_test_file="tests/test_normalize.py",
        expected_changed_files=("app/normalize.py",),
        expected_failure_category="wrong_file_selected",
    ),
    StressCase(
        case_id="stress_003_no_obvious_assertion",
        bug_type="opaque_failure",
        difficulty="hard",
        goal="Fix the failing smoke test with a vague failure message.",
        files={
            "app/__init__.py": "",
            "app/state.py": "def is_ready(value: int) -> bool:\n    return value > 10\n",
            "tests/test_state.py": (
                "from app.state import is_ready\n\n\n"
                "def test_state_ready():\n"
                "    assert is_ready(5), 'state check failed'\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/state.py",
        expected_test_file="tests/test_state.py",
        expected_changed_files=("app/state.py",),
        expected_failure_category="no_patch_proposed",
    ),
    StressCase(
        case_id="stress_004_syntax_trap_patch",
        bug_type="syntax_trap",
        difficulty="hard",
        goal="Fix the failing parser test; avoid introducing syntax errors.",
        files={
            "app/__init__.py": "",
            "app/parser.py": (
                "def parse_flag(value: str) -> bool:\n"
                "    if value == 'yes':\n"
                "        return False\n"
                "    return False\n"
            ),
            "tests/test_parser.py": (
                "from app.parser import parse_flag\n\n\n"
                "def test_yes_is_true():\n"
                "    assert parse_flag('yes') is True\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/parser.py",
        expected_test_file="tests/test_parser.py",
        expected_changed_files=("app/parser.py",),
        expected_failure_category="patch_invalid",
    ),
    StressCase(
        case_id="stress_005_wrong_file_temptation",
        bug_type="wrong_file_selected",
        difficulty="hard",
        goal="Fix the failing greeting test; do not edit the test helper.",
        files={
            "app/__init__.py": "",
            "app/greeting.py": "def greet(name: str) -> str:\n    return f'Hello {name}'\n",
            "tests/helper.py": "EXPECTED_GREETING = 'hello palla'\n",
            "tests/test_greeting.py": (
                "from app.greeting import greet\n"
                "from tests.helper import EXPECTED_GREETING\n\n\n"
                "def test_greet_lowercase():\n"
                "    assert greet('palla') == EXPECTED_GREETING\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/greeting.py",
        expected_test_file="tests/test_greeting.py",
        expected_changed_files=("app/greeting.py",),
        expected_failure_category="wrong_file_selected",
    ),
)


def main() -> None:
    if DEFAULT_CASES_DIR.exists():
        shutil.rmtree(DEFAULT_CASES_DIR)
    DEFAULT_CASES_DIR.mkdir(parents=True, exist_ok=True)

    for case in CASES:
        case_dir = DEFAULT_CASES_DIR / case.case_id
        case_dir.mkdir(parents=True)
        for relative_path, content in case.files.items():
            target = case_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        metadata = {
            "case_id": case.case_id,
            "bug_type": case.bug_type,
            "difficulty": case.difficulty,
            "goal": case.goal,
            "expected_changed_files": list(case.expected_changed_files),
            "expected_source_file": case.expected_source_file,
            "expected_test_file": case.expected_test_file,
            "expected_failure_category": case.expected_failure_category,
            "multi_file": False,
            "real_repo": True,
            "stress_case": True,
            "relevant_files": sorted(path for path in case.files if path.endswith(".py")),
        }
        (case_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"Generated {len(CASES)} real-repo stress cases in {DEFAULT_CASES_DIR}")


if __name__ == "__main__":
    main()
