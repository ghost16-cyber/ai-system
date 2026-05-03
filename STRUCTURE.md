# Project Structure & Setup Guide

## 📁 Directory Organization

```
ai-system/
│
├── src/                           # Core ML/AI modules
│   ├── models/                    # 4-bit Qwen2.5-Coder + LoRA
│   │   ├── loader.py             # ModelLoader class
│   │   ├── quantizer.py          # QuantizationManager
│   │   └── __init__.py
│   │
│   ├── ml/                        # Fast pattern detection (CPU)
│   │   ├── classifier.py         # PatternClassifier (SGD + TF-IDF)
│   │   ├── complexity_analyzer.py # Code complexity scoring
│   │   └── __init__.py
│   │
│   ├── rag/                       # Retrieval-Augmented Generation
│   │   ├── retriever.py          # VectorStoreRetriever (FAISS)
│   │   ├── builder.py            # RAGBuilder (prompt construction)
│   │   └── __init__.py
│   │
│   ├── rl/                        # Reinforcement Learning
│   │   ├── bandit.py             # ContextualBandit (learns from feedback)
│   │   ├── reward_tracker.py     # RewardTracker (logs feedback)
│   │   └── __init__.py
│   │
│   ├── inference/                 # Inference orchestration
│   │   ├── pipeline.py           # InferencePipeline (main orchestrator)
│   │   └── __init__.py
│   │
│   └── utils/                     # Utilities
│       ├── memory_monitor.py     # GPU/RAM monitoring
│       ├── logger.py             # Unified logging
│       └── __init__.py
│
├── training/                      # Training & dataset preparation
│   ├── scripts/
│   │   ├── setup.py              # Initialize directories
│   │   ├── check_dataset.py      # Analyze dataset
│   │   ├── train_classifier.py   # Train fast pattern classifier
│   │   ├── setup_lora.py         # Setup 4-bit model + LoRA
│   │   ├── build_rag.py          # Build vector store
│   │   └── retrain.bat           # (Windows) batch retraining
│   │
│   ├── datasets/                  # Dataset preparation utilities
│   │   └── [dataset processing scripts]
│   │
│   └── logs/                      # Training logs
│
├── data/                          # Data storage
│   ├── raw/                       # Original code files
│   ├── processed/                 # Cleaned data (code_patterns.csv)
│   └── models/                    # Trained models
│       ├── pattern_clf.pkl       # Fast classifier
│       ├── qwen-lora-adapter/    # LoRA adapter
│       ├── rag_index.faiss       # Vector store index
│       └── rag_metadata.json     # Retrieval metadata
│
├── scripts/                       # Utility scripts
│   ├── inference_cli.py          # CLI for code analysis
│   ├── monitor.py                # System monitoring
│   └── api.py                    # FastAPI server
│
├── tests/                         # Unit tests
│   ├── test_models.py
│   ├── test_ml.py
│   └── test_rag.py
│
├── config/                        # Configuration files
│   ├── model_config.yaml
│   ├── training_config.yaml
│   └── inference_config.yaml
│
├── notebooks/                     # Jupyter notebooks
│   └── [exploration notebooks]
│
├── logs/                          # System logs
│   └── system.log
│
├── main.py                        # Main entry point
├── requirements.txt               # Python dependencies
└── README.md                      # Project documentation
```

## 🚀 Quick Start

### 1. Setup Environment
```bash
cd ai-system
python training/scripts/setup.py
```

### 2. Train Pattern Classifier
```bash
python training/scripts/train_classifier.py
```

### 3. Build RAG Vector Store
```bash
python training/scripts/build_rag.py
```

### 4. Setup Qwen2.5-Coder + LoRA
```bash
python training/scripts/setup_lora.py
```

### 5. Run Analysis
```bash
python main.py
```

## 📊 Component Responsibilities

| Component | Role | Resources | Location |
|-----------|------|-----------|----------|
| **PatternClassifier** | Fast pattern detection | CPU-only, ~2GB RAM | `src/ml/classifier.py` |
| **ComplexityAnalyzer** | Code quality scoring | CPU, GradientBoosting | `src/ml/complexity_analyzer.py` |
| **ModelLoader** | 4-bit Qwen2.5-Coder | 1.2GB VRAM | `src/models/loader.py` |
| **VectorStoreRetriever** | Similarity search (RAG) | CPU-based FAISS | `src/rag/retriever.py` |
| **ContextualBandit** | Learn from feedback | CPU, minimal memory | `src/rl/bandit.py` |
| **InferencePipeline** | Orchestrate all components | Varies | `src/inference/pipeline.py` |

## 💾 Resource Allocation

- **GPU VRAM (RTX 3050 4GB)**:
  - Qwen2.5-Coder 1.5B (4-bit): ~1.2GB
  - Tiny embedding cache: ~0.3GB
  - Buffer: ~2.5GB free

- **System RAM (32GB)**:
  - Fast ML models: ~2-4GB
  - Vector store: ~2-4GB
  - FAISS index: ~0.5GB
  - System: ~12GB free

## 🔧 Integration Points

### Adding a New Classifier
1. Create class in `src/ml/`
2. Implement `train()`, `predict()`, `save()`, `load()`
3. Import in `src/ml/__init__.py`
4. Add to `InferencePipeline`

### Adding RL Agent
1. Create class in `src/rl/`
2. Implement action selection & update methods
3. Import in `src/rl/__init__.py`
4. Hook into inference pipeline for feedback

## 📝 Example Usage

```python
from src.ml.classifier import PatternClassifier
from src.rag.retriever import VectorStoreRetriever
from src.inference.pipeline import InferencePipeline

# Quick pattern detection
clf = PatternClassifier()
clf.load("data/models/pattern_clf.pkl")
pattern = clf.predict("for i in range(len(x)): print(x[i])")

# Retrieve similar examples
retriever = VectorStoreRetriever()
retriever.load("data/models/rag_index.faiss", "data/models/rag_metadata.json")
examples = retriever.retrieve(embedding, k=3)

# Full pipeline
pipeline = InferencePipeline()
pipeline.initialize()
result = pipeline.analyze(code_snippet)
```

## ⚠️ File Migration Checklist

Your existing files should be organized as follows:
- `code_patterns.csv` → `data/processed/code_patterns.csv`
- `new_examples.csv` → `data/processed/new_examples.csv`
- `train_classifier.py` → `training/scripts/train_classifier.py`
- `code_analyzer.py` → `src/inference/code_analyzer.py`
- `file_analyzer.py` → `src/inference/file_analyzer.py`

(Run `python training/scripts/setup.py` to automate this)
