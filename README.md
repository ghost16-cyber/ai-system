# 🏆 Code Analyzer - Complete Pattern Detection System

A **practical, non-LLM hybrid system** that detects bad coding patterns and suggests improvements through ML + rules.

## Quick Start

```bash
# 1. Generate dataset (62 labeled examples, 30 patterns)
python save_dataset.py

# 2. Train model (TF-IDF + LinearSVC)
python train_classifier.py

# 3. Choose your tool:
python code_analyzer.py                    # Test suite (10 examples)
python cli_analyzer.py                     # Interactive CLI
python file_analyzer.py train_classifier.py  # Scan entire file
python report_generator.py train_classifier.py  # Quality report
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CODE INPUT                               │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│            FEATURE EXTRACTION (TF-IDF)                       │
│         Character n-grams (3-5 length)                      │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│         ML MODEL (LinearSVC Classifier)                      │
│         Predicts: inefficient_loop, bad_none_check, etc.    │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│      RULE-BASED SUGGESTION ENGINE                           │
│  Pattern → Issue Description → Suggestion → Code Example    │
└─────────────────────────────────────────────────────────────┘
```

---

## 6 Tools Included

### 1️⃣ **save_dataset.py** — Create Dataset
Generates 62 labeled code snippets (2-3 examples per pattern).

```bash
python save_dataset.py
# Output: code_patterns.csv (62 samples, 30 classes)
```

### 2️⃣ **train_classifier.py** — Train Model
TF-IDF + LinearSVC pipeline with 80/20 train-test split.

```bash
python train_classifier.py
# Output: code_pattern_clf.pkl (trained model)
```

### 3️⃣ **code_analyzer.py** — Test Suite
Demonstrates detection with 10 pre-built test cases.

```bash
python code_analyzer.py
```

### 4️⃣ **cli_analyzer.py** — Interactive CLI
Real-time pattern detection with interactive menu.

```bash
python cli_analyzer.py
# Commands: analyze, file, patterns, help, exit
```

### 5️⃣ **file_analyzer.py** — File Scanner
Analyzes entire `.py` files for issues.

```bash
python file_analyzer.py train_classifier.py
# Outputs: All issues with line numbers and suggestions
```

### 6️⃣ **report_generator.py** — Quality Reports
Generates reports with scores and recommendations.

```bash
python report_generator.py save_dataset.py
python report_generator.py save_dataset.py --json report.json
python report_generator.py save_dataset.py --csv issues.csv
```

---

## 30 Code Patterns

### 15 Bad Patterns (Anti-patterns)
| # | Pattern | Issue | Fix |
|---|---------|-------|-----|
| 1 | `bad_none_check` | `x == None` | Use `x is None` |
| 2 | `bare_exception` | `except Exception` | Catch specific type |
| 3 | `dangerous_eval` | `eval(user_input)` | Use `json.loads()` |
| 4 | `inefficient_append` | Loop with `.append()` | Use list comprehension |
| 5 | `inefficient_loop` | `range(len(arr))` | Direct iteration |
| 6 | `len_check_nonzero` | `len(x) > 0` | Use truthiness `if x` |
| 7 | `magic_number` | Hardcoded constant | Extract to named var |
| 8 | `missing_docstring` | Function no docs | Add docstring |
| 9 | `missing_encoding` | `open(file)` | Use `encoding="utf-8"` |
| 10 | `non_pythonic` | `x = x + 1` | Use `x += 1` |
| 11 | `python2_print` | `print "text"` | Use `print()` |
| 12 | `redundant_bool_compare` | `if x == True` | Use `if x` |
| 13 | `shallow_copy` | `b = a` | Use `.copy()` |
| 14 | `star_import` | `from x import *` | Import specific names |
| 15 | `while_true_break` | `while True: if x: break` | Use condition |

### 15 Good Patterns (Best practices)
| # | Pattern | Description |
|---|---------|-------------|
| 16 | `deep_copy` | Proper copy method |
| 17 | `good_bool_check` | Pythonic boolean check |
| 18 | `good_file_open` | Encoding specified |
| 19 | `good_import` | Explicit imports |
| 20 | `good_json_load` | Safe JSON loading |
| 21 | `good_len_check` | Truthiness check |
| 22 | `good_list_creation` | Efficient list |
| 23 | `good_loop` | Direct iteration |
| 24 | `good_none_check` | Use `is None` |
| 25 | `has_docstring` | Documented function |
| 26 | `named_constant` | Descriptive names |
| 27 | `pythonic` | Augmented assignment |
| 28 | `specific_exception` | Catch specific type |
| 29 | `unused_loop_var` | Use `_` convention |
| 30 | `python3_print` | Python 3 print() |

---

## Usage Examples

### Command Line - Interactive CLI
```bash
python cli_analyzer.py

analyzer> analyze
Paste code snippet (press Enter twice when done):
for i in range(len(arr)): print(arr[i])

[output]
ANALYSIS RESULT
Pattern: inefficient_loop
Issue: Inefficient loop using range(len(...))
Suggestion: Use direct iteration instead
Example: for item in items:
```

### Command Line - File Analysis
```bash
python file_analyzer.py bad_loop.py

ANALYZING FILE: bad_loop.py
================================================

ISSUES FOUND: 1

1. Line 3: inefficient_loop
   Code: for i in range(len(...
   Issue: Inefficient loop using range(len(...))
   Fix: Use direct iteration instead
```

### Command Line - Report Generation
```bash
python report_generator.py save_dataset.py

CODE QUALITY REPORT - save_dataset.py
================================================================================
Improvement Score: 51%
Total snippets analyzed: 37
Issues found:            18
Good patterns:           19

RECOMMENDATIONS
Priority fixes (by frequency):
  • Fix 'inefficient_append' (2 occurrences) - Lines: 34, 118
  • Fix 'magic_number' (2 occurrences) - Lines: 50, 54
  • Fix 'star_import' (2 occurrences) - Lines: 98, 102

Score: 51% | Status: Fair
```

### Python API
```python
from code_analyzer import analyze_code

code = "for i in range(len(arr)): print(arr[i])"
result = analyze_code(code)

print(result['predicted_pattern'])  # inefficient_loop
print(result['issue'])              # Inefficient loop using range(len(...))
print(result['suggestion'])         # Use direct iteration instead
print(result['example'])            # for item in items:
```

```python
from file_analyzer import analyze_file

analyze_file("my_script.py")
```

```python
from report_generator import generate_report, print_report

report = generate_report("my_script.py")
print_report(report)
```

---

## How Real Tools Work (Hybrid Approach)

This system combines **ML + Rules** — how production code analysis tools actually work:

1. **ML Component** (Detection)
   - Learns from labeled code examples
   - Generalizes to unseen patterns
   - Fast inference

2. **Rules Component** (Suggestion)
   - Exact, deterministic fixes
   - Domain-specific best practices
   - Reliable recommendations

**Why hybrid?**
- ML catches patterns humans might miss
- Rules ensure suggestions are correct
- Combines generalization + reliability

---

## Files Included

```
save_dataset.py          Generate dataset (62 samples, 30 patterns)
train_classifier.py      Train TF-IDF + LinearSVC model
code_analyzer.py         Test suite + pattern detection API
cli_analyzer.py          Interactive CLI tool
file_analyzer.py         Analyze entire Python files
report_generator.py      Generate quality reports
code_pattern_clf.pkl     Trained model (persisted)
code_patterns.csv        Dataset (62 labeled samples)
README.md                This documentation
```

---

## Technical Details

**Dataset:**
- 62 code snippets
- 30 classes (patterns)
- 2-3 examples per class
- Format: CSV (code_snippet, label)

**Feature Extraction:**
- TF-IDF Vectorizer
- Character n-grams (3-5 length)
- min_df=1, max_features=5000
- Sublinear TF scaling

**Model:**
- LinearSVC classifier
- Fast training and inference
- Works well with sparse features
- max_iter=2000

**Train/Test:**
- 80% train (49 samples)
- 20% test (13 samples)
- No stratification (small dataset)

**Accuracy:**
- ~23% (expected with 30 classes and 62 samples)
- Production systems use 10,000+ examples

---

## Extending the System

**Add more patterns:**
1. Add rows to `save_dataset.py`
2. Add suggestions to `code_analyzer.py` SUGGESTIONS dict
3. Run `python save_dataset.py && python train_classifier.py`

**Improve accuracy:**
- Expand dataset to 500+ examples per pattern
- Try different vectorizers (word-level, AST-based)
- Tune SVM hyperparameters (C, gamma)
- Use cross-validation

**Production deployment:**
- [ ] Deploy as web API (Flask/FastAPI)
- [ ] Add support for multiple languages
- [ ] Integrate with CI/CD (GitHub Actions)
- [ ] Build VS Code extension
- [ ] Add severity levels (critical/warning/info)
- [ ] Track improvements over time

---

## Key Insights

1. **Small datasets work** — 60 samples can train a functional classifier
2. **Character n-grams are effective** — Captures syntax better than words
3. **Hybrid systems are practical** — ML + rules = best of both worlds
4. **Class balance matters** — 30 classes × 62 samples requires careful splits
5. **Real tools use heuristics** — Not just ML, but domain knowledge too

---

**Created:** May 2026  
**Phase:** ✅ 2 — Full toolset (CLI, file analyzer, reports, APIs)  
**Status:** Production-ready for demo / proof-of-concept
