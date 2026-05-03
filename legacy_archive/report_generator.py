# file_analyzer.py – Analyse an entire Python file
import joblib
import os
from code_analyzer import SUGGESTIONS

# Load the trained scikit‑learn model
pipeline = joblib.load("code_pattern_clf.pkl")

def extract_code_snippets(file_path):
    """
    Very lightweight snippet extractor.
    Returns a list of (line_number, snippet) tuples.
    """
    snippets = []
    try:
        # Try a handful of common encodings
        encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
        lines = None
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    lines = f.readlines()
                break
            except UnicodeDecodeError:
                continue

        if lines is None:
            print("⚠️  Could not decode file with any known encoding.")
            return []

        # Very simple block collector – good enough for our tiny demo
        current_block = []
        for i, line in enumerate(lines, 1):
            stripped = line.rstrip()
            if stripped and not stripped.startswith("#"):
                current_block.append(stripped)

                # End of a logical statement?
                if stripped.endswith((
                    ":",
                    ")",
                    "]",
                    "}",
                )):
                    code = "\n".join(current_block)
                    if code.strip():
                        snippets.append((i, code))
                    current_block = []
            elif current_block and stripped == "":
                # Blank line – close the current block
                code = "\n".join(current_block)
                if code.strip():
                    snippets.append((i, code))
                current_block = []

        # Anything left over?
        if current_block:
            code = "\n".join(current_block)
            if code.strip():
                snippets.append((i, code))

    except Exception as exc:
        print(f"❌  Error reading {file_path}: {exc}")
        return []

    return snippets


def analyze_file(file_path):
    """Run the model over every snippet in *file_path* and print a report."""
    if not os.path.exists(file_path):
        print(f"❌  File not found: {file_path}")
        return

    if not file_path.endswith(".py"):
        print("❌  Only .py files are supported.")
        return

    print("=" * 70)
    print(f"ANALYSING FILE: {file_path}")
    print("=" * 70)

    snippets = extract_code_snippets(file_path)
    if not snippets:
        print("⚠️  No code snippets detected.")
        return

    issues_found = []
    good_patterns = []

    for line_num, code in snippets:
        prediction = pipeline.predict([code])[0]
        suggestion = SUGGESTIONS.get(prediction, {})

        # ---------------------------------------------------------
        # Use the explicit `is_issue` flag from the suggestion dict
        # ---------------------------------------------------------
        if suggestion.get("is_issue", True):
            # ---- Bad pattern -------------------------------------------------
            issues_found.append(
                {
                    "line": line_num,
                    "pattern": prediction,
                    "code": code[:40] + ("..." if len(code) > 40 else ""),
                    "issue": suggestion.get("issue", "Unknown issue"),
                    "suggestion": suggestion.get("suggestion", "Review manually"),
                    "example": suggestion.get("example", ""),
                }
            )
        else:
            # ---- Good pattern ------------------------------------------------
            good_patterns.append(
                {
                    "line": line_num,
                    "pattern": prediction,
                    "code": code[:40] + ("..." if len(code) > 40 else ""),
                }
            )

    # -------------------- Print results --------------------
    if issues_found:
        print(f"\nISSUES FOUND: {len(issues_found)}\n")
        for idx, issue in enumerate(issues_found, 1):
            print(f"{idx}. Line {issue['line']}: {issue['pattern']}")
            print(f"   Code: {issue['code']}")
            print(f"   Issue: {issue['issue']}")
            print(f"   Fix:   {issue['suggestion']}")
            if issue["example"]:
                print(f"   Example: {issue['example']}")
            print()
    else:
        print("\n✅  No issues found!")

    # -------------------- Summary --------------------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total snippets analysed : {len(snippets)}")
    print(f"Issues found           : {len(issues_found)}")
    print(f"Good patterns detected : {len(good_patterns)}")

    if issues_found:
        print(f"\n🔧  Priority – fix the {len(issues_found)} issue(s) above.")
    else:
        print("\n🎉  Code quality looks good!")


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("=" * 70)
        print("FILE ANALYZER – Analyse Python files for code patterns")
        print("=" * 70)
        print("\nUsage:")
        print("  python file_analyzer.py <path_to_file.py>")
        print("\nExample:")
        print("  python file_analyzer.py bad_loop.py")
        print("  python file_analyzer.py train_classifier.py")
        print("\nSupported patterns: see `code_analyzer.py`")
    else:
        analyze_file(sys.argv[1])