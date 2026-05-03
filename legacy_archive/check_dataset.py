import pandas as pd

df = pd.read_csv("code_patterns.csv")
print("=" * 60)
print("DATASET STATUS")
print("=" * 60)
print(f"Total samples: {len(df)}")
print(f"Unique patterns: {df['label'].nunique()}")
print(f"\nPattern distribution:")
print(df['label'].value_counts().sort_values(ascending=False))
print("=" * 60)