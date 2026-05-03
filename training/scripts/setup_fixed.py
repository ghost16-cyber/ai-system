# Setup Training Environment - Prepare dataset and directories
import pandas as pd
from pathlib import Path


def setup_directories():
    """Create necessary directories."""
    dirs = [
        "data/raw",
        "data/processed",
        "data/models",
        "training/logs",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        print(f"??? {d}")


def prepare_dataset():
    """Move or link existing CSV files to processed directory."""
    csv_files = ["code_patterns.csv", "new_examples.csv"]
    
    for csv in csv_files:
        src = Path(csv)
        dst = Path("data/processed") / csv
        
        if src.exists() and not dst.exists():
            import shutil
            shutil.copy(src, dst)
            print(f"??? Copied {csv} to data/processed/")
        elif dst.exists():
            print(f"??? {csv} already in data/processed/")


def main():
    print("Setting up training environment...")
    setup_directories()
    prepare_dataset()
    print("\n??? Setup complete!")


if __name__ == "__main__":
    main()
