# file_analyzer.py – Analyse an entire Python file
import csv
import joblib
import os
from pathlib import Path
from .code_analyzer import SUGGESTIONS

# Load the trained scikit‑learn model
MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "models" / "code_pattern_clf.pkl"
pipeline = joblib.load(str(MODEL_PATH))

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


def analyze_file(file_path, validate=False):
    """
    Run the model over every snippet in *file_path* and print a report.
    
    If validate=True, prompt user to confirm each prediction and collect
    corrected labels for training data improvement.
    """
    if not os.path.exists(file_path):
        print(f"❌  File not found: {file_path}")
        return

    if not file_path.endswith(".py"):
        print("❌  Only .py files are supported.")
        return

    print("=" * 70)
    print(f"ANALYSING FILE: {file_path}")
    if validate:
        print("📝 VALIDATION MODE - You will review predictions")
    print("=" * 70)

    snippets = extract_code_snippets(file_path)
    if not snippets:
        print("⚠️  No code snippets detected.")
        return

    issues_found = []
    good_patterns = []
    validated_count = 0
    corrections = 0

    for line_num, code in snippets:
        prediction = pipeline.predict([code])[0]
        suggestion = SUGGESTIONS.get(prediction, {})

        # ============================================================
        # USER VALIDATION (optional)
        # ============================================================
        if validate:
            print(f"\n{'─' * 70}")
            print(f"Snippet at line {line_num}:")
            print(f"  {code[:60]}{'...' if len(code) > 60 else ''}")
            print(f"\n→ Predicted pattern: {prediction}")
            print(f"   Issue: {suggestion.get('issue', 'Unknown')}")
            
            resp = input("Is this correct? (y/n) [y]: ").strip().lower()
            
            if resp == "n":
                correct = input("  Enter the correct label (or press Enter to skip): ").strip()
                if correct:
                    # Save corrected example to new_examples.csv
                    with open("new_examples.csv", "a", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerow([code, correct])
                    print(f"  ✓ Saved: '{correct}'")
                    corrections += 1
                validated_count += 1
                continue  # Skip this prediction, use user's correction
            
            validated_count += 1

        # ============================================================
        # USE THE EXPLICIT `is_issue` FLAG FROM THE SUGGESTION DICT
        # ============================================================
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
    
    if validate:
        print(f"Snippets validated     : {validated_count}")
        print(f"Corrections collected  : {corrections}")
        if corrections > 0:
            print(f"\n💾  Saved {corrections} corrected examples to new_examples.csv")

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
        print("  python file_analyzer.py <path_to_file.py> --validate")
        print("\nOptions:")
        print("  --validate    Review predictions and collect corrections")
        print("\nExample:")
        print("  python file_analyzer.py bad_loop.py")
        print("  python file_analyzer.py train_classifier.py --validate")
        print("\nSupported patterns: see `code_analyzer.py`")
    else:
        file_path = sys.argv[1]
        validate_mode = "--validate" in sys.argv
        analyze_file(file_path, validate=validate_mode)
