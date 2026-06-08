from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "benchmarks" / "real_repos"


@dataclass(frozen=True)
class KnownPatch:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class RealRepoCase:
    case_id: str
    repo_family: str
    bug_type: str
    difficulty: str
    goal: str
    files: dict[str, str]
    expected_source_file: str
    expected_test_file: str
    known_patch: KnownPatch
    multi_file: bool = False

    @property
    def relevant_files(self) -> tuple[str, ...]:
        return tuple(sorted(path for path in self.files if path.endswith(".py")))


CASES: tuple[RealRepoCase, ...] = (
    RealRepoCase(
        case_id="real_001_inventory_line_total",
        repo_family="fastapi_inventory_app",
        bug_type="aggregation_bug",
        difficulty="easy",
        goal="Fix the failing inventory line-total calculation.",
        files={
            "app/__init__.py": "",
            "app/services/__init__.py": "",
            "app/services/pricing.py": (
                "from dataclasses import dataclass\n\n\n"
                "@dataclass\n"
                "class LineItem:\n"
                "    price: int\n"
                "    quantity: int\n\n\n"
                "def line_total(item: LineItem) -> int:\n"
                "    return item.price + item.quantity\n"
            ),
            "tests/test_pricing.py": (
                "from app.services.pricing import LineItem, line_total\n\n\n"
                "def test_line_total_uses_quantity():\n"
                "    assert line_total(LineItem(price=7, quantity=3)) == 21\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/services/pricing.py",
        expected_test_file="tests/test_pricing.py",
        known_patch=KnownPatch(
            "app/services/pricing.py",
            "return item.price + item.quantity",
            "return item.price * item.quantity",
        ),
    ),
    RealRepoCase(
        case_id="real_002_inventory_discount_config",
        repo_family="fastapi_inventory_app",
        bug_type="config_constant",
        difficulty="medium",
        goal="Fix the failing ten-percent discount test.",
        files={
            "app/__init__.py": "",
            "app/config.py": "DISCOUNT_RATE = 0.05\n",
            "app/pricing.py": (
                "from app.config import DISCOUNT_RATE\n\n\n"
                "def discounted_price(total: int) -> float:\n"
                "    return total * (1 - DISCOUNT_RATE)\n"
            ),
            "tests/test_discount.py": (
                "from app.pricing import discounted_price\n\n\n"
                "def test_ten_percent_discount():\n"
                "    assert discounted_price(100) == 90\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="app/config.py",
        expected_test_file="tests/test_discount.py",
        known_patch=KnownPatch("app/config.py", "DISCOUNT_RATE = 0.05", "DISCOUNT_RATE = 0.10"),
        multi_file=True,
    ),
    RealRepoCase(
        case_id="real_003_todo_display_name",
        repo_family="flask_todo_app",
        bug_type="none_handling",
        difficulty="medium",
        goal="Fix the failing todo owner display-name test.",
        files={
            "todo_app/__init__.py": "",
            "todo_app/users.py": (
                "def display_name(user: dict[str, str] | None) -> str:\n"
                "    return user['name']\n"
            ),
            "tests/test_users.py": (
                "from todo_app.users import display_name\n\n\n"
                "def test_missing_user_is_anonymous():\n"
                "    assert display_name(None) == 'anonymous'\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="todo_app/users.py",
        expected_test_file="tests/test_users.py",
        known_patch=KnownPatch(
            "todo_app/users.py",
            "return user['name']",
            "if user is None:\n        return 'anonymous'\n    return user['name']",
        ),
    ),
    RealRepoCase(
        case_id="real_004_todo_count_items",
        repo_family="flask_todo_app",
        bug_type="off_by_one",
        difficulty="easy",
        goal="Fix the failing todo count test.",
        files={
            "todo_app/__init__.py": "",
            "todo_app/stats.py": (
                "def count_items(values: list[str]) -> int:\n"
                "    return len(values) - 1\n"
            ),
            "tests/test_stats.py": (
                "from todo_app.stats import count_items\n\n\n"
                "def test_count_items():\n"
                "    assert count_items(['a', 'b', 'c']) == 3\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="todo_app/stats.py",
        expected_test_file="tests/test_stats.py",
        known_patch=KnownPatch("todo_app/stats.py", "return len(values) - 1", "return len(values)"),
    ),
    RealRepoCase(
        case_id="real_005_data_contains_case_insensitive",
        repo_family="data_utils_package",
        bug_type="case_insensitive_matching",
        difficulty="easy",
        goal="Fix the failing data matching case-insensitive test.",
        files={
            "data_utils/__init__.py": "",
            "data_utils/matching.py": (
                "def contains(haystack: str, needle: str) -> bool:\n"
                "    return needle in haystack\n"
            ),
            "tests/test_matching.py": (
                "from data_utils.matching import contains\n\n\n"
                "def test_contains_ignores_case():\n"
                "    assert contains('Hello World', 'world') is True\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="data_utils/matching.py",
        expected_test_file="tests/test_matching.py",
        known_patch=KnownPatch(
            "data_utils/matching.py",
            "return needle in haystack",
            "return needle.lower() in haystack.lower()",
        ),
    ),
    RealRepoCase(
        case_id="real_006_data_safe_int",
        repo_family="data_utils_package",
        bug_type="wrong_exception_handling",
        difficulty="medium",
        goal="Fix the failing safe integer parsing test.",
        files={
            "data_utils/__init__.py": "",
            "data_utils/parser.py": (
                "def safe_int(value: str) -> int:\n"
                "    return int(value)\n"
            ),
            "tests/test_parser.py": (
                "from data_utils.parser import safe_int\n\n\n"
                "def test_safe_int_returns_zero_for_bad_input():\n"
                "    assert safe_int('oops') == 0\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="data_utils/parser.py",
        expected_test_file="tests/test_parser.py",
        known_patch=KnownPatch(
            "data_utils/parser.py",
            "return int(value)",
            "try:\n        return int(value)\n    except ValueError:\n        return 0",
        ),
    ),
    RealRepoCase(
        case_id="real_007_cli_prefix_check",
        repo_family="cli_tools_package",
        bug_type="string_prefix_bug",
        difficulty="easy",
        goal="Fix the failing CLI prefix flag test.",
        files={
            "cli_tools/__init__.py": "",
            "cli_tools/args.py": (
                "def has_prefix(value: str, prefix: str) -> bool:\n"
                "    return value.endswith(prefix)\n"
            ),
            "tests/test_args.py": (
                "from cli_tools.args import has_prefix\n\n\n"
                "def test_has_prefix():\n"
                "    assert has_prefix('--verbose', '--') is True\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="cli_tools/args.py",
        expected_test_file="tests/test_args.py",
        known_patch=KnownPatch(
            "cli_tools/args.py",
            "return value.endswith(prefix)",
            "return value.startswith(prefix)",
        ),
    ),
    RealRepoCase(
        case_id="real_008_accounts_email_helper",
        repo_family="flask_todo_app",
        bug_type="helper_function_bug",
        difficulty="medium",
        goal="Fix the failing account canonical email test.",
        files={
            "todo_app/__init__.py": "",
            "todo_app/accounts.py": (
                "from todo_app.helpers import normalize_email\n\n\n"
                "def canonical_email(value: str) -> str:\n"
                "    return normalize_email(value)\n"
            ),
            "todo_app/helpers.py": (
                "def normalize_email(value: str) -> str:\n"
                "    return value.strip()\n"
            ),
            "tests/test_accounts.py": (
                "from todo_app.accounts import canonical_email\n\n\n"
                "def test_canonical_email_is_lowercase():\n"
                "    assert canonical_email(' USER@Example.COM ') == 'user@example.com'\n"
            ),
            "requirements.txt": "pytest\n",
        },
        expected_source_file="todo_app/helpers.py",
        expected_test_file="tests/test_accounts.py",
        known_patch=KnownPatch(
            "todo_app/helpers.py",
            "return value.strip()",
            "return value.strip().lower()",
        ),
        multi_file=True,
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
            "repo_family": case.repo_family,
            "bug_type": case.bug_type,
            "difficulty": case.difficulty,
            "goal": case.goal,
            "expected_changed_files": [case.known_patch.path],
            "expected_source_file": case.expected_source_file,
            "expected_test_file": case.expected_test_file,
            "known_patch": {
                "path": case.known_patch.path,
                "old": case.known_patch.old,
                "new": case.known_patch.new,
            },
            "multi_file": case.multi_file,
            "real_repo": True,
            "relevant_files": list(case.relevant_files),
        }
        (case_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"Generated {len(CASES)} real-repo benchmark cases in {DEFAULT_CASES_DIR}")


if __name__ == "__main__":
    main()
