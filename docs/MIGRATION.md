# 📋 Repository Reorganization - Migration Guide

Your repository has been reorganized into a clean, professional structure for the ML/RL system. Here's what changed:

---

## ✅ What Was Created

### 🏗️ Directory Structure
```
✓ src/                    - Core ML/AI modules
  ├── models/            - 4-bit model loading + LoRA
  ├── ml/                - Fast classifiers (CPU-based)
  ├── rag/               - Vector stores + retrieval
  ├── rl/                - Bandit + reward tracking
  ├── inference/         - Pipeline orchestration
  └── utils/             - Monitoring & logging

✓ training/               - Training workflows
  ├── scripts/           - Setup, training, build scripts
  ├── datasets/          - Dataset preparation
  └── logs/              - Training logs

✓ data/                   - Data management
  ├── raw/               - Original files
  ├── processed/         - Cleaned datasets
  └── models/            - Trained models & adapters

✓ scripts/                - Utility scripts
✓ tests/                  - Unit tests
✓ config/                 - Configuration files
✓ notebooks/              - Jupyter notebooks
```

---

## 📦 Core Components Created

### 1. **Models Module** (`src/models/`)
- `loader.py` - Load 4-bit Qwen2.5-Coder with LoRA
- `quantizer.py` - Memory/quantization utilities

### 2. **ML Module** (`src/ml/`)
- `classifier.py` - Fast pattern detection (TF-IDF + SGD)
- `complexity_analyzer.py` - Code quality scoring

### 3. **RAG Module** (`src/rag/`)
- `retriever.py` - FAISS-based vector store
- `builder.py` - Prompt construction with examples

### 4. **RL Module** (`src/rl/`)
- `bandit.py` - Contextual bandit for learning
- `reward_tracker.py` - Track user feedback

### 5. **Inference Module** (`src/inference/`)
- `pipeline.py` - Orchestrate all components

### 6. **Utilities** (`src/utils/`)
- `memory_monitor.py` - GPU/RAM monitoring
- `logger.py` - Unified logging

---

## 📚 Training Scripts Created

```
training/scripts/
├── setup.py              - Initialize all directories
├── check_dataset.py      - Analyze your training data
├── train_classifier.py   - Train the fast classifier
├── setup_lora.py         - Setup 4-bit model + LoRA
└── build_rag.py          - Build vector store
```

---

## 🚀 How to Use

### **Step 1: Initialize** (1 minute)
```bash
python training/scripts/setup.py
```
Creates directories and moves your CSV files to `data/processed/`

### **Step 2: Train Classifier** (30 seconds)
```bash
python training/scripts/train_classifier.py
```
Creates `data/models/pattern_clf.pkl`

### **Step 3: Build RAG** (1 minute)
```bash
python training/scripts/build_rag.py
```
Creates vector store: `data/models/rag_index.faiss` + metadata

### **Step 4: Setup LLM** (2 minutes)
```bash
python training/scripts/setup_lora.py
```
Prepares 4-bit Qwen2.5-Coder + LoRA adapter

### **Step 5: Run System** (ongoing)
```bash
python main.py
```
Runs the full analysis pipeline

---

## 📊 Old vs New File Locations

| Purpose | Old Location | New Location |
|---------|--------------|--------------|
| Pattern detection | `train_classifier.py` | `training/scripts/train_classifier.py` |
| Dataset analysis | `check_dataset.py` | `training/scripts/check_dataset.py` |
| Code analysis | `code_analyzer.py` | `src/inference/code_analyzer.py` |
| File processing | `file_analyzer.py` | `src/inference/file_analyzer.py` |
| CSV data | `code_patterns.csv` | `data/processed/code_patterns.csv` |
| New examples | `new_examples.csv` | `data/processed/new_examples.csv` |

---

## 💾 File Organization Rules

### Source Code (`src/`)
- All ML/AI modules organized by function
- Each module has `__init__.py` for clean imports
- Examples: `from src.ml import PatternClassifier`

### Training (`training/`)
- Training scripts run sequentially
- Dataset utilities for preparation
- Training logs auto-saved

### Data (`data/`)
- `raw/` - Original unprocessed files
- `processed/` - Clean, ready-to-use datasets
- `models/` - All trained models & adapters

### Utilities (`scripts/`)
- CLI tools
- API servers
- Monitoring dashboards

---

## 🔍 Example: Using the New Structure

### Before (scattered files):
```python
import joblib
from train_classifier import pipeline

result = pipeline.predict(code)
```

### After (organized modules):
```python
from src.ml.classifier import PatternClassifier

clf = PatternClassifier()
clf.load("data/models/pattern_clf.pkl")
result = clf.predict(code)
```

---

## 📝 Key Files to Know

| File | Purpose |
|------|---------|
| `main.py` | Entry point - runs full pipeline |
| `README.md` | Getting started guide |
| `STRUCTURE.md` | Detailed architecture |
| `requirements.txt` | Python dependencies |
| `training/scripts/setup.py` | Initialize everything |

---

## ✨ Benefits of New Structure

✅ **Clean Organization** - Easy to find components  
✅ **Scalable** - Add new modules without conflicts  
✅ **Maintainable** - Clear responsibilities for each module  
✅ **Professional** - Industry-standard layout  
✅ **Documented** - Built-in docstrings + guides  
✅ **Testable** - Easy to write unit tests  
✅ **Deployable** - Ready for API/production  

---

## 🚨 Important Notes

### Your Existing Files
- **Keep them** in the root directory for now
- **Run** `python training/scripts/setup.py` to organize them automatically
- Or **manually move** them based on the table above

### First Time Setup
1. Install dependencies: `pip install -r requirements.txt`
2. Run setup: `python training/scripts/setup.py`
3. Follow the Quick Start in `README.md`

### Directory Tree
See full structure in `STRUCTURE.md`

---

## 🎯 Next Actions

1. ✅ **Review** the new structure
2. ✅ **Read** `README.md` for quick start
3. ✅ **Run** `python training/scripts/setup.py`
4. ✅ **Follow** Phase 1 setup in README

---

**Everything is ready to go! Start with:**
```bash
python training/scripts/setup.py
```
