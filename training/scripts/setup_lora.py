# Fine-tune 4-bit Qwen2.5-Coder with LoRA
import sys
from pathlib import Path

# Add the project package to the import path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.app.llm.loader import ModelLoader
from backend.app.core.memory_monitor import MemoryMonitor
import pandas as pd
import json


def prepare_instruction_dataset(csv_path):
    """Convert code patterns CSV to instruction format for fine-tuning."""
    df = pd.read_csv(csv_path)
    
    instructions = []
    for _, row in df.iterrows():
        instruction = {
            "prompt": f"Analyze this Python code and identify issues:\n{row['code_snippet']}",
            "completion": f" The pattern is: {row['label']}. This is a {'problematic' if 'bad' in row['label'] else 'good'} coding pattern."
        }
        instructions.append(instruction)
    
    return instructions


def main():
    print("="*60)
    print("FINE-TUNE QWEN2.5-CODER WITH LORA")
    print("="*60)
    
    # Check memory
    MemoryMonitor.print_stats()
    
    # Load model
    loader = ModelLoader(model_id="Qwen/Qwen2.5-coder-1.5B")
    model, tokenizer = loader.load_4bit_model()
    
    # Add LoRA
    model = loader.add_lora_adapter(r=8, lora_alpha=16)
    MemoryMonitor.print_stats()
    
    # Save adapter
    loader.save_adapter("data/models/qwen-lora-adapter")
    
    print("\n✓ LoRA adapter created and saved!")
    print("Next: Run training script to fine-tune on your code patterns")


if __name__ == "__main__":
    main()
