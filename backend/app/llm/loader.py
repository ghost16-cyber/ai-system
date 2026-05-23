# src/models/loader.py
# Model Loading & Quantization - 4-bit Qwen2.5‑Coder with LoRA
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, PeftModel


class ModelLoader:
    """Load and initialise 4‑bit Qwen2.5‑Coder with optional LoRA adapters."""

    def __init__(self, model_id: str = "Qwen/Qwen2.5-coder-1.5B"):
        self.model_id = model_id
        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # 1️⃣ 4‑bit loading (modern API)
    # ------------------------------------------------------------------
    def load_4bit_model(self):
        """
        Load the model in 4‑bit quantisation using ``bitsandbytes``.

        Returns
        -------
        tuple[AutoModelForCausalLM, AutoTokenizer]
            The model (already on the appropriate device) and its tokenizer.
        """
        print(f"Loading {self.model_id} in 4‑bit...")

        # ---- 1️⃣ Build the BitsAndBytes config ---------------------------------
        # The defaults below are the same as the ones used by `bitsandbytes` in
        # the official Qwen2 examples.
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",  # “nf4” gives the best quality/size trade‑off
        )

        # ---- 2️⃣ Choose device map ------------------------------------------------
        # If a CUDA device is present we let 🤗 Accelerate place sub‑modules
        # automatically; otherwise we stay on CPU (useful for laptops without a GPU).
        device_map = "auto" if torch.cuda.is_available() else "cpu"

        # ---- 3️⃣ Load the model ---------------------------------------------------
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            quantization_config=bnb_config,
            device_map=device_map,
            torch_dtype=torch.float16,   # keep the weights in FP16 on‑device
        )

        # ---- 4️⃣ Load the tokenizer -----------------------------------------------
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)

        print("✓ Model loaded successfully")
        return self.model, self.tokenizer

    # ------------------------------------------------------------------
    # 2️⃣ LoRA adapter handling (unchanged, but works on CPU too)
    # ------------------------------------------------------------------
    def add_lora_adapter(self, r: int = 8, lora_alpha: int = 16):
        """
        Attach a LoRA adapter for cheap fine‑tuning.

        Parameters
        ----------
        r : int
            Rank of the low‑rank matrices.
        lora_alpha : int
            Scaling factor.
        """
        lora_config = LoraConfig(
            r=r,
            lora_alpha=lora_alpha,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        self.model = get_peft_model(self.model, lora_config)
        print(f"✓ LoRA adapter added (r={r}, alpha={lora_alpha})")
        return self.model

    # ------------------------------------------------------------------
    # 3️⃣ Save / load LoRA adapters (unchanged)
    # ------------------------------------------------------------------
    def save_adapter(self, path: str):
        """Save LoRA adapter to disk."""
        self.model.save_pretrained(path)
        print(f"✓ Adapter saved to {path}")

    def load_adapter(self, path: str):
        """Load a saved LoRA adapter."""
        self.model = PeftModel.from_pretrained(self.model, path)
        print(f"✓ Adapter loaded from {path}")
        return self.model


# ----------------------------------------------------------------------
# QuantizationManager – unchanged (kept for backward compatibility)
# ----------------------------------------------------------------------
class QuantizationManager:
    """Monitor and manage GPU quantization and memory usage."""

    @staticmethod
    def get_gpu_memory_usage() -> float:
        """Get current GPU memory usage in MB."""
        return torch.cuda.memory_allocated() / 1024 / 1024

    @staticmethod
    def get_gpu_memory_max() -> float:
        """Get max GPU memory in MB."""
        return torch.cuda.get_device_properties(0).total_memory / 1024 / 1024

    @staticmethod
    def print_memory_stats() -> None:
        """Print current GPU memory stats."""
        used = QuantizationManager.get_gpu_memory_usage()
        total = QuantizationManager.get_gpu_memory_max()
        print(f"GPU Memory: {used:.1f}MB / {total:.1f}MB ({used/total*100:.1f}%)")


# ----------------------------------------------------------------------
# Self‑test entry point (unchanged)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    loader = ModelLoader()
    model, tokenizer = loader.load_4bit_model()
    model = loader.add_lora_adapter()
    QuantizationManager.print_memory_stats()