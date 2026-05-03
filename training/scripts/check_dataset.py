# Check Dataset - Analyze your training data
import pandas as pd
from pathlib import Path


def main():
    csv_path = "data/processed/code_patterns.csv"
    
    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        return
    
    df = pd.read_csv(csv_path)
    
    print("=" * 60)
    print("DATASET STATUS")
    print("=" * 60)
    print(f"Total samples: {len(df)}")
    print(f"Unique patterns: {df['label'].nunique()}")
    print(f"\nPattern distribution:")
    print(df['label'].value_counts().sort_values(ascending=False))
    print("=" * 60)


if __name__ == "__main__":
    main()
