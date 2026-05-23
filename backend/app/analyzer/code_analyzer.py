# code_analyzer.py - Inference & Suggestion Engine
import joblib
from pathlib import Path
import re

def fix_range_len_loop(code: str) -> str | None:
    """
    Convert simple loops like:

        for i in range(len(arr)):
            print(arr[i])

    into:

        for item in arr:
            print(item)

    This is intentionally conservative. If the pattern is not simple,
    it returns None instead of guessing.
    """
    pattern = re.compile(
        r"for\s+(?P<index>\w+)\s+in\s+range\s*\(\s*len\s*\(\s*(?P<array>\w+)\s*\)\s*\)\s*:\s*\n"
        r"(?P<indent>\s+)(?P<body>.+)",
        re.DOTALL,
    )

    match = pattern.search(code)

    if not match:
        return None

    index_var = match.group("index")
    array_name = match.group("array")
    indent = match.group("indent")
    body = match.group("body")

    item_var = "item"

    fixed_body = re.sub(
        rf"\b{array_name}\s*\[\s*{index_var}\s*\]",
        item_var,
        body,
    )

    fixed_code = f"for {item_var} in {array_name}:\n{indent}{fixed_body}"

    return fixed_code

def fix_none_check(code: str) -> str | None:
    """
    Convert simple None comparisons:

        if x == None:

    into:

        if x is None:

    Also supports:

        if x != None:

    into:

        if x is not None:

    This is conservative and only rewrites direct comparisons.
    """
    fixed_code = re.sub(
        r"\b(?P<var>[A-Za-z_]\w*)\s*==\s*None\b",
        r"\g<var> is None",
        code,
    )

    fixed_code = re.sub(
        r"\b(?P<var>[A-Za-z_]\w*)\s*!=\s*None\b",
        r"\g<var> is not None",
        fixed_code,
    )

    if fixed_code == code:
        return None

    return fixed_code    

# ------------------------------------------------------------------
# Load the trained scikit‑learn model (TF‑IDF + LinearSVC)
# ------------------------------------------------------------------
# Get the path relative to this module
MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "models" / "code_pattern_clf.pkl"
pipeline = joblib.load(str(MODEL_PATH))   # ← correct: load the saved pipeline

# ------------------------------------------------------------------
# Rule‑based suggestion engine (maps patterns to fixes)
# `is_issue: True`  → bad pattern (needs a fix)
# `is_issue: False` → good pattern (keep as‑is)
# ------------------------------------------------------------------
SUGGESTIONS = {
    # ------------------- Bad patterns -------------------
    "inefficient_loop": {
        "issue": "Inefficient loop using range(len(...))",
        "suggestion": "Use direct iteration instead",
        "example": "for item in items:",
        "is_issue": True,
    },
    "non_pythonic": {
        "issue": "Non‑pythonic increment",
        "suggestion": "Use augmented assignment",
        "example": "x += 1",
        "is_issue": True,
    },
    "redundant_bool_compare": {
        "issue": "Redundant boolean comparison",
        "suggestion": "Use truthiness check directly",
        "example": "if flag:",
        "is_issue": True,
    },
    "inefficient_append": {
        "issue": "Loop with append is slow",
        "suggestion": "Use list comprehension",
        "example": "result = [process(x) for x in data]",
        "is_issue": True,
    },
    "missing_docstring": {
        "issue": "Function missing docstring",
        "suggestion": "Add a docstring describing the function",
        "example": '"""Compute value."""',
        "is_issue": True,
    },
    "magic_number": {
        "issue": "Magic number – unclear meaning",
        "suggestion": "Extract to a named constant",
        "example": "PI = 3.14159",
        "is_issue": True,
    },
    "len_check_nonzero": {
        "issue": "Verbose length check",
        "suggestion": "Use truthiness of collection",
        "example": "if items:",
        "is_issue": True,
    },
    "bare_exception": {
        "issue": "Bare `except Exception` catches too much",
        "suggestion": "Catch specific exceptions",
        "example": "except ValueError as e:",
        "is_issue": True,
    },
    "shallow_copy": {
        "issue": "Shallow copy – both references point to the same list",
        "suggestion": "Use `.copy()` or `copy.deepcopy()`",
        "example": "b = a.copy()",
        "is_issue": True,
    },
    "while_true_break": {
        "issue": "`while True` with break – unclear exit condition",
        "suggestion": "Use an explicit loop condition",
        "example": "while condition:",
        "is_issue": True,
    },
    "bad_none_check": {
        "issue": "Bad – use `is` for `None` comparison",
        "suggestion": "Use `is None` (or `is not None`)",
        "example": "if x is None:",
        "is_issue": True,
    },
    "star_import": {
        "issue": "Star import pollutes the namespace",
        "suggestion": "Import only the symbols you need",
        "example": "from module import specific_func",
        "is_issue": True,
    },
    "python2_print": {
        "issue": "Python 2 print statement",
        "suggestion": "Use the Python 3 print function",
        "example": 'print("Hello")',
        "is_issue": True,
    },
    "dangerous_eval": {
        "issue": "`eval()` can execute arbitrary code",
        "suggestion": "Use `json.loads()` for JSON or `ast.literal_eval()` for literals",
        "example": "data = json.loads(s)",
        "is_issue": True,
    },
    "missing_encoding": {
        "issue": "File opened without explicit encoding",
        "suggestion": "Always specify `encoding='utf‑8'` (or the appropriate codec)",
        "example": 'with open(file, encoding="utf-8") as f:',
        "is_issue": True,
    },

    # ------------------- Good patterns -------------------
    "good_loop": {
        "issue": "Good pattern – direct iteration",
        "suggestion": "Keep this approach",
        "example": "for item in items: process(item)",
        "is_issue": False,
    },
    "pythonic": {
        "issue": "Pythonic increment",
        "suggestion": "Keep this pattern",
        "example": "x += 1",
        "is_issue": False,
    },
    "good_bool_check": {
        "issue": "Good boolean check",
        "suggestion": "Keep this pattern",
        "example": "if flag:",
        "is_issue": False,
    },
    "good_list_creation": {
        "issue": "Efficient list creation",
        "suggestion": "Keep this approach",
        "example": "result = [x for x in data]",
        "is_issue": False,
    },
    "has_docstring": {
        "issue": "Function has a docstring",
        "suggestion": "Keep documenting your code",
        "example": '"""Do something."""',
        "is_issue": False,
    },
    "named_constant": {
        "issue": "Named constant – descriptive",
        "suggestion": "Keep using descriptive names",
        "example": "PI = 3.14159",
        "is_issue": False,
    },
    "good_len_check": {
        "issue": "Good – use truthiness for length checks",
        "suggestion": "Keep this pattern",
        "example": "if items:",
        "is_issue": False,
    },
    "specific_exception": {
        "issue": "Specific exception handling",
        "suggestion": "Keep catching specific errors",
        "example": "except ValueError:",
        "is_issue": False,
    },
    "deep_copy": {
        "issue": "Proper copy method",
        "suggestion": "Keep using explicit copy",
        "example": "b = a.copy()",
        "is_issue": False,
    },
    "unused_loop_var": {
        "issue": "Using `_` for an unused loop variable",
        "suggestion": "Keep this convention",
        "example": "for _ in range(10):",
        "is_issue": False,
    },
    "good_none_check": {
        "issue": "Good – use `is None` for `None` checks",
        "suggestion": "Keep this pattern",
        "example": "if x is None:",
        "is_issue": False,
    },
    "good_import": {
        "issue": "Explicit imports",
        "suggestion": "Keep importing specific names",
        "example": "from module import specific",
        "is_issue": False,
    },
    "python3_print": {
        "issue": "Python 3 print function",
        "suggestion": "Keep using `print()`",
        "example": 'print("message")',
        "is_issue": False,
    },
    "good_json_load": {
        "issue": "Safe JSON loading",
        "suggestion": "Keep using `json.loads()`",
        "example": "data = json.loads(s)",
        "is_issue": False,
    },
    "good_file_open": {
        "issue": "File opened with explicit encoding",
        "suggestion": "Keep specifying encoding",
        "example": 'with open(file, encoding="utf-8") as f:',
        "is_issue": False,
    },
}

def analyze_code(code_snippet: str) -> dict:
    """
    Analyse a single code snippet.

    Returns a dictionary with:
        - original code
        - predicted pattern label
        - human-readable issue description
        - suggested fix
        - example snippet
        - fixed_code when a safe fix is available
    """
    prediction = pipeline.predict([code_snippet])[0]

    # Pull the suggestion entry (fallback for unknown patterns)
    suggestion = SUGGESTIONS.get(
        prediction,
        {
            "issue": "Unknown pattern",
            "suggestion": "Review the code manually",
            "example": "",
            "is_issue": True,
        },
    )

    fixed_code = None
    example = suggestion["example"]

    if prediction == "inefficient_loop":
        fixed_code = fix_range_len_loop(code_snippet)
    elif prediction == "bad_none_check":
        fixed_code = fix_none_check(code_snippet)

    if fixed_code:
        example = fixed_code

    return {
        "code": code_snippet,
        "predicted_pattern": prediction,
        "issue": suggestion["issue"],
        "suggestion": suggestion["suggestion"],
        "example": example,
        "fixed_code": fixed_code,
        "is_issue": suggestion["is_issue"],
    }

# ------------------------------------------------------------------
# Simple CLI test harness
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("CODE ANALYZER – Pattern Detection & Suggestions")
    print("=" * 70)

    test_cases = [
        "for i in range(len(arr)): print(arr[i])",
        "for x in arr: print(x)",
        "if x is None:",
        "data = eval(user_input)",
        "data = json.loads(s)",
        "my_list = []\nfor i in range(10):\n    my_list.append(i)",
        "result = [x*2 for x in range(10)]",
        "while True:\n    if done:\n        break",
        "def foo():\n    pass",
    ]

    for code in test_cases:
        result = analyze_code(code)
        print(f"\nCode: {result['code']}")
        print(f"   Pattern: {result['predicted_pattern']}")
        print(f"   Issue:    {result['issue']}")
        print(f"   Suggestion: {result['suggestion']}")
        if result["example"]:
            print(f"   Example: {result['example']}")
