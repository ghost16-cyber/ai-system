from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"


@dataclass(frozen=True)
class KnownPatch:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class RepairCase:
    case_id: str
    bug_type: str
    difficulty: str
    goal: str
    files: dict[str, str]
    expected_source_file: str
    expected_test_file: str
    expected_patch_hint: str
    known_patch: KnownPatch
    multi_file: bool = False

    @property
    def expected_changed_files(self) -> tuple[str, ...]:
        return (self.known_patch.path,)

    @property
    def relevant_files(self) -> tuple[str, ...]:
        return tuple(
            sorted(path for path in self.files if path.endswith(".py"))
        )


CASES: tuple[RepairCase, ...] = (
    RepairCase(
        case_id="case_001_calculator_demo",
        bug_type="wrong_return_value",
        difficulty="easy",
        goal="Fix the failing calculator test.",
        files={
            "calculator.py": "def add(a: int, b: int) -> int:\n    return a - b\n",
            "test_calculator.py": (
                "from calculator import add\n\n\n"
                "def test_add():\n"
                "    assert add(2, 3) == 5\n"
            ),
        },
        expected_source_file="calculator.py",
        expected_test_file="test_calculator.py",
        expected_patch_hint="return a + b",
        known_patch=KnownPatch("calculator.py", "return a - b", "return a + b"),
    ),
    RepairCase(
        case_id="case_002_off_by_one",
        bug_type="off_by_one",
        difficulty="easy",
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
        expected_source_file="sequence_utils.py",
        expected_test_file="test_sequence_utils.py",
        expected_patch_hint="return len(values)",
        known_patch=KnownPatch("sequence_utils.py", "return len(values) - 1", "return len(values)"),
    ),
    RepairCase(
        case_id="case_003_mutable_default",
        bug_type="state_leak",
        difficulty="medium",
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
        expected_source_file="collector.py",
        expected_test_file="test_collector.py",
        expected_patch_hint="values is None",
        known_patch=KnownPatch(
            "collector.py",
            "def append_item(item: str, values: list[str] = []) -> list[str]:\n"
            "    values.append(item)\n"
            "    return values",
            "def append_item(item: str, values: list[str] | None = None) -> list[str]:\n"
            "    if values is None:\n"
            "        values = []\n"
            "    values.append(item)\n"
            "    return values",
        ),
    ),
    RepairCase(
        case_id="case_004_string_to_int",
        bug_type="type_conversion",
        difficulty="easy",
        goal="Fix the failing string conversion test.",
        files={
            "parser.py": "def parse_age(value: str) -> int:\n    return value\n",
            "test_parser.py": (
                "from parser import parse_age\n\n\n"
                "def test_parse_age_returns_int():\n"
                "    assert parse_age('42') == 42\n"
            ),
        },
        expected_source_file="parser.py",
        expected_test_file="test_parser.py",
        expected_patch_hint="return int(value)",
        known_patch=KnownPatch("parser.py", "return value", "return int(value)"),
    ),
    RepairCase(
        case_id="case_005_safe_dict_lookup",
        bug_type="missing_default",
        difficulty="easy",
        goal="Fix the failing user lookup test.",
        files={
            "users.py": "def get_role(user: dict[str, str]) -> str:\n    return user['role']\n",
            "test_users.py": (
                "from users import get_role\n\n\n"
                "def test_get_role_defaults_to_guest():\n"
                "    assert get_role({}) == 'guest'\n"
            ),
        },
        expected_source_file="users.py",
        expected_test_file="test_users.py",
        expected_patch_hint="user.get",
        known_patch=KnownPatch("users.py", "return user['role']", "return user.get('role', 'guest')"),
    ),
    RepairCase(
        case_id="case_006_filter_even_numbers",
        bug_type="bad_boolean_logic",
        difficulty="easy",
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
        expected_source_file="number_utils.py",
        expected_test_file="test_numbers.py",
        expected_patch_hint="value % 2 == 0",
        known_patch=KnownPatch(
            "number_utils.py",
            "return [value for value in values if value % 2 == 1]",
            "return [value for value in values if value % 2 == 0]",
        ),
    ),
    RepairCase(
        case_id="case_007_wrong_comparison_operator",
        bug_type="wrong_comparison_operator",
        difficulty="easy",
        goal="Fix the failing adulthood boundary test.",
        files={
            "eligibility.py": "def is_adult(age: int) -> bool:\n    return age > 18\n",
            "test_eligibility.py": (
                "from eligibility import is_adult\n\n\n"
                "def test_eighteen_is_adult():\n"
                "    assert is_adult(18) is True\n"
            ),
        },
        expected_source_file="eligibility.py",
        expected_test_file="test_eligibility.py",
        expected_patch_hint="age >= 18",
        known_patch=KnownPatch("eligibility.py", "return age > 18", "return age >= 18"),
    ),
    RepairCase(
        case_id="case_008_boolean_logic_bug",
        bug_type="boolean_logic",
        difficulty="easy",
        goal="Fix the failing access-control boolean test.",
        files={
            "access.py": (
                "def can_edit(is_owner: bool, is_admin: bool) -> bool:\n"
                "    return is_owner and is_admin\n"
            ),
            "test_access.py": (
                "from access import can_edit\n\n\n"
                "def test_owner_or_admin_can_edit():\n"
                "    assert can_edit(True, False) is True\n"
                "    assert can_edit(False, True) is True\n"
            ),
        },
        expected_source_file="access.py",
        expected_test_file="test_access.py",
        expected_patch_hint="or",
        known_patch=KnownPatch("access.py", "return is_owner and is_admin", "return is_owner or is_admin"),
    ),
    RepairCase(
        case_id="case_009_missing_none_handling",
        bug_type="none_handling",
        difficulty="medium",
        goal="Fix the failing optional display-name test.",
        files={
            "profiles.py": (
                "def display_name(user: dict[str, str] | None) -> str:\n"
                "    return user['name']\n"
            ),
            "test_profiles.py": (
                "from profiles import display_name\n\n\n"
                "def test_missing_user_is_anonymous():\n"
                "    assert display_name(None) == 'anonymous'\n"
            ),
        },
        expected_source_file="profiles.py",
        expected_test_file="test_profiles.py",
        expected_patch_hint="user is None",
        known_patch=KnownPatch(
            "profiles.py",
            "return user['name']",
            "if user is None:\n        return 'anonymous'\n    return user['name']",
        ),
    ),
    RepairCase(
        case_id="case_010_list_sorting_bug",
        bug_type="sorting",
        difficulty="easy",
        goal="Fix the failing ascending sort test.",
        files={
            "sorting.py": "def sort_scores(values: list[int]) -> list[int]:\n    return sorted(values, reverse=True)\n",
            "test_sorting.py": (
                "from sorting import sort_scores\n\n\n"
                "def test_sort_scores_ascending():\n"
                "    assert sort_scores([3, 1, 2]) == [1, 2, 3]\n"
            ),
        },
        expected_source_file="sorting.py",
        expected_test_file="test_sorting.py",
        expected_patch_hint="sorted(values)",
        known_patch=KnownPatch("sorting.py", "return sorted(values, reverse=True)", "return sorted(values)"),
    ),
    RepairCase(
        case_id="case_011_incorrect_default_return",
        bug_type="incorrect_default_return",
        difficulty="easy",
        goal="Fix the failing missing-index test.",
        files={
            "searching.py": (
                "def find_index(values: list[str], target: str) -> int:\n"
                "    for index, value in enumerate(values):\n"
                "        if value == target:\n"
                "            return index\n"
                "    return 0\n"
            ),
            "test_searching.py": (
                "from searching import find_index\n\n\n"
                "def test_missing_value_returns_negative_one():\n"
                "    assert find_index(['a', 'b'], 'z') == -1\n"
            ),
        },
        expected_source_file="searching.py",
        expected_test_file="test_searching.py",
        expected_patch_hint="return -1",
        known_patch=KnownPatch("searching.py", "return 0", "return -1"),
    ),
    RepairCase(
        case_id="case_012_path_string_normalization",
        bug_type="string_normalization",
        difficulty="easy",
        goal="Fix the failing slug normalization test.",
        files={
            "slugs.py": "def make_slug(value: str) -> str:\n    return value.lower().replace(' ', '')\n",
            "test_slugs.py": (
                "from slugs import make_slug\n\n\n"
                "def test_make_slug_uses_hyphens():\n"
                "    assert make_slug('Hello World') == 'hello-world'\n"
            ),
        },
        expected_source_file="slugs.py",
        expected_test_file="test_slugs.py",
        expected_patch_hint="replace(' ', '-')",
        known_patch=KnownPatch(
            "slugs.py",
            "return value.lower().replace(' ', '')",
            "return value.lower().replace(' ', '-')",
        ),
    ),
    RepairCase(
        case_id="case_013_datetime_formatting",
        bug_type="date_formatting",
        difficulty="easy",
        goal="Fix the failing ISO date formatting test.",
        files={
            "dates.py": (
                "from datetime import date\n\n\n"
                "def format_date(value: date) -> str:\n"
                "    return value.strftime('%m/%d/%Y')\n"
            ),
            "test_dates.py": (
                "from datetime import date\n\n"
                "from dates import format_date\n\n\n"
                "def test_format_date_iso():\n"
                "    assert format_date(date(2026, 5, 30)) == '2026-05-30'\n"
            ),
        },
        expected_source_file="dates.py",
        expected_test_file="test_dates.py",
        expected_patch_hint="%Y-%m-%d",
        known_patch=KnownPatch("dates.py", "return value.strftime('%m/%d/%Y')", "return value.strftime('%Y-%m-%d')"),
    ),
    RepairCase(
        case_id="case_014_dataclass_field_bug",
        bug_type="dataclass_field",
        difficulty="medium",
        goal="Fix the failing invoice line-total test.",
        files={
            "invoice.py": (
                "from dataclasses import dataclass\n\n\n"
                "@dataclass\n"
                "class LineItem:\n"
                "    price: int\n"
                "    quantity: int\n\n\n"
                "def line_total(item: LineItem) -> int:\n"
                "    return item.price + item.quantity\n"
            ),
            "test_invoice.py": (
                "from invoice import LineItem, line_total\n\n\n"
                "def test_line_total_multiplies_price_by_quantity():\n"
                "    assert line_total(LineItem(price=5, quantity=3)) == 15\n"
            ),
        },
        expected_source_file="invoice.py",
        expected_test_file="test_invoice.py",
        expected_patch_hint="price * quantity",
        known_patch=KnownPatch("invoice.py", "return item.price + item.quantity", "return item.price * item.quantity"),
    ),
    RepairCase(
        case_id="case_015_recursion_base_case",
        bug_type="recursion_base_case",
        difficulty="medium",
        goal="Fix the failing factorial base-case test.",
        files={
            "maths.py": (
                "def factorial(n: int) -> int:\n"
                "    if n == 0:\n"
                "        return 0\n"
                "    return n * factorial(n - 1)\n"
            ),
            "test_maths.py": (
                "from maths import factorial\n\n\n"
                "def test_factorial_zero():\n"
                "    assert factorial(0) == 1\n\n\n"
                "def test_factorial_three():\n"
                "    assert factorial(3) == 6\n"
            ),
        },
        expected_source_file="maths.py",
        expected_test_file="test_maths.py",
        expected_patch_hint="return 1",
        known_patch=KnownPatch("maths.py", "return 0", "return 1"),
    ),
    RepairCase(
        case_id="case_016_aggregation_bug",
        bug_type="aggregation",
        difficulty="easy",
        goal="Fix the failing total aggregation test.",
        files={
            "totals.py": "def total(values: list[int]) -> int:\n    return values[0]\n",
            "test_totals.py": (
                "from totals import total\n\n\n"
                "def test_total_sums_all_values():\n"
                "    assert total([2, 3, 4]) == 9\n"
            ),
        },
        expected_source_file="totals.py",
        expected_test_file="test_totals.py",
        expected_patch_hint="sum(values)",
        known_patch=KnownPatch("totals.py", "return values[0]", "return sum(values)"),
    ),
    RepairCase(
        case_id="case_017_duplicate_handling",
        bug_type="duplicate_handling",
        difficulty="easy",
        goal="Fix the failing unique-list test.",
        files={
            "dedupe.py": "def unique(values: list[str]) -> list[str]:\n    return values\n",
            "test_dedupe.py": (
                "from dedupe import unique\n\n\n"
                "def test_unique_preserves_order():\n"
                "    assert unique(['a', 'b', 'a']) == ['a', 'b']\n"
            ),
        },
        expected_source_file="dedupe.py",
        expected_test_file="test_dedupe.py",
        expected_patch_hint="dict.fromkeys",
        known_patch=KnownPatch("dedupe.py", "return values", "return list(dict.fromkeys(values))"),
    ),
    RepairCase(
        case_id="case_018_case_insensitive_matching",
        bug_type="case_insensitive_matching",
        difficulty="easy",
        goal="Fix the failing case-insensitive contains test.",
        files={
            "matching.py": "def contains(haystack: str, needle: str) -> bool:\n    return needle in haystack\n",
            "test_matching.py": (
                "from matching import contains\n\n\n"
                "def test_contains_ignores_case():\n"
                "    assert contains('Hello World', 'hello') is True\n"
            ),
        },
        expected_source_file="matching.py",
        expected_test_file="test_matching.py",
        expected_patch_hint="lower",
        known_patch=KnownPatch("matching.py", "return needle in haystack", "return needle.lower() in haystack.lower()"),
    ),
    RepairCase(
        case_id="case_019_empty_input_handling",
        bug_type="empty_input_handling",
        difficulty="medium",
        goal="Fix the failing empty average test.",
        files={
            "averages.py": "def average(values: list[int]) -> float:\n    return sum(values) / len(values)\n",
            "test_averages.py": (
                "from averages import average\n\n\n"
                "def test_average_empty_is_zero():\n"
                "    assert average([]) == 0\n"
            ),
        },
        expected_source_file="averages.py",
        expected_test_file="test_averages.py",
        expected_patch_hint="if not values",
        known_patch=KnownPatch(
            "averages.py",
            "return sum(values) / len(values)",
            "if not values:\n        return 0\n    return sum(values) / len(values)",
        ),
    ),
    RepairCase(
        case_id="case_020_wrong_exception_handling",
        bug_type="exception_handling",
        difficulty="medium",
        goal="Fix the failing safe integer parser test.",
        files={
            "safe_parse.py": "def safe_int(value: str) -> int:\n    return int(value)\n",
            "test_safe_parse.py": (
                "from safe_parse import safe_int\n\n\n"
                "def test_safe_int_returns_zero_for_bad_input():\n"
                "    assert safe_int('bad') == 0\n"
            ),
        },
        expected_source_file="safe_parse.py",
        expected_test_file="test_safe_parse.py",
        expected_patch_hint="except ValueError",
        known_patch=KnownPatch(
            "safe_parse.py",
            "return int(value)",
            "try:\n        return int(value)\n    except ValueError:\n        return 0",
        ),
    ),
    RepairCase(
        case_id="case_021_clamp_bounds_bug",
        bug_type="bounds_logic",
        difficulty="easy",
        goal="Fix the failing clamp upper-bound test.",
        files={
            "bounds.py": "def clamp(value: int, low: int, high: int) -> int:\n    return max(low, value)\n",
            "test_bounds.py": (
                "from bounds import clamp\n\n\n"
                "def test_clamp_caps_high_value():\n"
                "    assert clamp(10, 0, 5) == 5\n"
            ),
        },
        expected_source_file="bounds.py",
        expected_test_file="test_bounds.py",
        expected_patch_hint="min(value, high)",
        known_patch=KnownPatch("bounds.py", "return max(low, value)", "return max(low, min(value, high))"),
    ),
    RepairCase(
        case_id="case_022_string_prefix_bug",
        bug_type="wrong_string_method",
        difficulty="easy",
        goal="Fix the failing prefix check test.",
        files={
            "prefixes.py": "def has_prefix(value: str, prefix: str) -> bool:\n    return value.endswith(prefix)\n",
            "test_prefixes.py": (
                "from prefixes import has_prefix\n\n\n"
                "def test_has_prefix_uses_start():\n"
                "    assert has_prefix('report-2026', 'report') is True\n"
            ),
        },
        expected_source_file="prefixes.py",
        expected_test_file="test_prefixes.py",
        expected_patch_hint="startswith",
        known_patch=KnownPatch("prefixes.py", "return value.endswith(prefix)", "return value.startswith(prefix)"),
    ),
    RepairCase(
        case_id="case_023_imported_constant_bug",
        bug_type="imported_constant_bug",
        difficulty="medium",
        goal="Fix the failing discount price test.",
        files={
            "config.py": "DISCOUNT_RATE = 0.05\n",
            "pricing.py": (
                "from config import DISCOUNT_RATE\n\n\n"
                "def discounted_price(price: float) -> float:\n"
                "    return price * (1 - DISCOUNT_RATE)\n"
            ),
            "test_pricing.py": (
                "from pricing import discounted_price\n\n\n"
                "def test_discounted_price_uses_ten_percent_discount():\n"
                "    assert discounted_price(100) == 90\n"
            ),
        },
        expected_source_file="config.py",
        expected_test_file="test_pricing.py",
        expected_patch_hint="0.10",
        known_patch=KnownPatch("config.py", "DISCOUNT_RATE = 0.05", "DISCOUNT_RATE = 0.10"),
        multi_file=True,
    ),
    RepairCase(
        case_id="case_024_helper_function_bug",
        bug_type="helper_function_bug",
        difficulty="medium",
        goal="Fix the failing normalized email test.",
        files={
            "helpers.py": "def normalize_email(value: str) -> str:\n    return value.strip()\n",
            "accounts.py": (
                "from helpers import normalize_email\n\n\n"
                "def canonical_email(value: str) -> str:\n"
                "    return normalize_email(value)\n"
            ),
            "test_accounts.py": (
                "from accounts import canonical_email\n\n\n"
                "def test_canonical_email_lowercases():\n"
                "    assert canonical_email(' USER@Example.COM ') == 'user@example.com'\n"
            ),
        },
        expected_source_file="helpers.py",
        expected_test_file="test_accounts.py",
        expected_patch_hint="lower",
        known_patch=KnownPatch("helpers.py", "return value.strip()", "return value.strip().lower()"),
        multi_file=True,
    ),
    RepairCase(
        case_id="case_025_dataclass_validation_bug",
        bug_type="dataclass_validation_bug",
        difficulty="medium",
        goal="Fix the failing signup validation test.",
        files={
            "models.py": (
                "from dataclasses import dataclass\n\n\n"
                "@dataclass\n"
                "class SignupRules:\n"
                "    min_age: int = 21\n"
            ),
            "validators.py": (
                "from models import SignupRules\n\n\n"
                "def can_signup(age: int, rules: SignupRules | None = None) -> bool:\n"
                "    rules = rules or SignupRules()\n"
                "    return age >= rules.min_age\n"
            ),
            "test_validators.py": (
                "from validators import can_signup\n\n\n"
                "def test_eighteen_year_old_can_signup():\n"
                "    assert can_signup(18) is True\n"
            ),
        },
        expected_source_file="models.py",
        expected_test_file="test_validators.py",
        expected_patch_hint="18",
        known_patch=KnownPatch("models.py", "min_age: int = 21", "min_age: int = 18"),
        multi_file=True,
    ),
    RepairCase(
        case_id="case_026_imported_tax_rate_bug",
        bug_type="imported_constant_bug",
        difficulty="medium",
        goal="Fix the failing sales tax test.",
        files={
            "tax_config.py": "TAX_RATE = 0.05\n",
            "checkout.py": (
                "from tax_config import TAX_RATE\n\n\n"
                "def total_with_tax(subtotal: float) -> float:\n"
                "    return subtotal * (1 + TAX_RATE)\n"
            ),
            "test_checkout.py": (
                "from checkout import total_with_tax\n\n\n"
                "def test_total_with_tax_uses_eight_percent():\n"
                "    assert total_with_tax(100) == 108\n"
            ),
        },
        expected_source_file="tax_config.py",
        expected_test_file="test_checkout.py",
        expected_patch_hint="0.08",
        known_patch=KnownPatch("tax_config.py", "TAX_RATE = 0.05", "TAX_RATE = 0.08"),
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
        (case_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "bug_type": case.bug_type,
                    "difficulty": case.difficulty,
                    "goal": case.goal,
                    "expected_changed_files": list(case.expected_changed_files),
                    "expected_source_file": case.expected_source_file,
                    "expected_test_file": case.expected_test_file,
                    "expected_patch_hint": case.expected_patch_hint,
                    "relevant_files": list(case.relevant_files),
                    "known_patch": {
                        "path": case.known_patch.path,
                        "old": case.known_patch.old,
                        "new": case.known_patch.new,
                    },
                    "multi_file": case.multi_file,
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
