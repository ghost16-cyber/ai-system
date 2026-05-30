from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"


@dataclass(frozen=True)
class RepairCase:
    case_id: str
    bug_type: str
    goal: str
    files: dict[str, str]
    expected_changed_files: tuple[str, ...]
    expected_patch_hint: str


CASES: tuple[RepairCase, ...] = (
    RepairCase(
        case_id="case_001_calculator_demo",
        bug_type="wrong_return_value",
        goal="Fix the failing calculator test.",
        files={
            "calculator.py": "def add(a: int, b: int) -> int:\n    return a - b\n",
            "test_calculator.py": (
                "from calculator import add\n\n\n"
                "def test_add():\n"
                "    assert add(2, 3) == 5\n"
            ),
        },
        expected_changed_files=("calculator.py",),
        expected_patch_hint="return a + b",
    ),
    RepairCase(
        case_id="case_002_off_by_one",
        bug_type="off_by_one",
        goal="Fix the failing list length test.",
        files={
            "sequence_utils.py": (
                "def count_items(values: list[int]) -> int:\n"
                "    return len(values) - 1\n"
            ),
            "test_sequence_utils.py": (
                "from sequence_utils import count_items\n\n\n"
                "def test_count_items():\n"
                "    assert count_items([1, 2, 3]) == 3\n"
            ),
        },
        expected_changed_files=("sequence_utils.py",),
        expected_patch_hint="return len(values)",
    ),
    RepairCase(
        case_id="case_003_mutable_default",
        bug_type="state_leak",
        goal="Fix the failing accumulator test.",
        files={
            "collector.py": (
                "def append_item(item: str, values: list[str] = []) -> list[str]:\n"
                "    values.append(item)\n"
                "    return values\n"
            ),
            "test_collector.py": (
                "from collector import append_item\n\n\n"
                "def test_append_item_does_not_share_state():\n"
                "    assert append_item('a') == ['a']\n"
                "    assert append_item('b') == ['b']\n"
            ),
        },
        expected_changed_files=("collector.py",),
        expected_patch_hint="values is None",
    ),
    RepairCase(
        case_id="case_004_string_to_int",
        bug_type="type_conversion",
        goal="Fix the failing string conversion test.",
        files={
            "parser.py": (
                "def parse_age(value: str) -> int:\n"
                "    return value\n"
            ),
            "test_parser.py": (
                "from parser import parse_age\n\n\n"
                "def test_parse_age_returns_int():\n"
                "    assert parse_age('42') == 42\n"
            ),
        },
        expected_changed_files=("parser.py",),
        expected_patch_hint="return int(value)",
    ),
    RepairCase(
        case_id="case_005_safe_dict_lookup",
        bug_type="missing_default",
        goal="Fix the failing user lookup test.",
        files={
            "users.py": (
                "def get_role(user: dict[str, str]) -> str:\n"
                "    return user['role']\n"
            ),
            "test_users.py": (
                "from users import get_role\n\n\n"
                "def test_get_role_defaults_to_guest():\n"
                "    assert get_role({}) == 'guest'\n"
            ),
        },
        expected_changed_files=("users.py",),
        expected_patch_hint="user.get",
    ),
    RepairCase(
        case_id="case_006_filter_even_numbers",
        bug_type="bad_boolean_logic",
        goal="Fix the failing even number filter test.",
        files={
            "number_utils.py": (
                "def only_even(values: list[int]) -> list[int]:\n"
                "    return [value for value in values if value % 2 == 1]\n"
            ),
            "test_numbers.py": (
                "from number_utils import only_even\n\n\n"
                "def test_only_even():\n"
                "    assert only_even([1, 2, 3, 4]) == [2, 4]\n"
            ),
        },
        expected_changed_files=("number_utils.py",),
        expected_patch_hint="value % 2 == 0",
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
        (case_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "bug_type": case.bug_type,
                    "goal": case.goal,
                    "expected_changed_files": list(case.expected_changed_files),
                    "expected_patch_hint": case.expected_patch_hint,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"Generated {len(CASES)} repair benchmark cases in {DEFAULT_CASES_DIR}")


if __name__ == "__main__":
    main()
