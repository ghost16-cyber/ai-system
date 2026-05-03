# repo_scanner/llm_engine/reasoning_engine.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


@dataclass
class LLMConfig:
    """Configuration for the Qwen LLM."""

    model_name: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    max_new_tokens: int = 200
    temperature: float = 0.1
    top_p: float = 0.9
    device_map: str = "auto"
    offload_folder: str = "model_offload"
    load_in_4bit: bool = True


class RepoReasoningLLM:
    """Thin wrapper around a Qwen chat model for repository reasoning."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()

        Path(self.config.offload_folder).mkdir(parents=True, exist_ok=True)

        print("[LLM] Loading tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.config.model_name,
            trust_remote_code=True,
        )

        quantization_config = None
        if self.config.load_in_4bit and torch.cuda.is_available():
            print("[LLM] Using 4-bit bitsandbytes quantization.")
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        elif self.config.load_in_4bit:
            print("[LLM] 4-bit quantization requested, but CUDA is unavailable.")

        print("[LLM] Loading model...")
        self.model = AutoModelForCausalLM.from_pretrained(
            self.config.model_name,
            dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map=self.config.device_map,
            offload_folder=self.config.offload_folder,
            low_cpu_mem_usage=True,
            quantization_config=quantization_config,
            trust_remote_code=True,
        )
        self.model.eval()
        print("[LLM] Model loaded.")

    def _model_input_device(self) -> torch.device:
        device = getattr(self.model, "device", None)
        if isinstance(device, torch.device) and device.type != "meta":
            return device

        for parameter in self.model.parameters():
            if parameter.device.type != "meta":
                return parameter.device

        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def _build_model_inputs(self, chat_output) -> tuple[dict, int]:
        device = self._model_input_device()

        if torch.is_tensor(chat_output):
            input_ids = chat_output.to(device)
            return {"input_ids": input_ids}, input_ids.shape[-1]

        if isinstance(chat_output, Mapping) or hasattr(chat_output, "items"):
            model_inputs = {}
            for key, value in chat_output.items():
                model_inputs[key] = value.to(device) if hasattr(value, "to") else value

            input_ids = model_inputs.get("input_ids")
            if input_ids is None or not torch.is_tensor(input_ids):
                raise TypeError("Tokenizer output did not contain tensor input_ids.")

            return model_inputs, input_ids.shape[-1]

        raise TypeError(f"Unsupported tokenizer output type: {type(chat_output).__name__}")

    def reason(self, prompt: str) -> str:
        """Generate a response for the given prompt using the chat template."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise AI coding assistant analyzing a software "
                    "repository."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        print("[LLM] Building prompt...")
        try:
            chat_output = self.tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                return_tensors="pt",
                return_dict=True,
                add_generation_prompt=True,
            )
        except TypeError:
            chat_output = self.tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                return_tensors="pt",
                add_generation_prompt=True,
            )

        model_inputs, prompt_length = self._build_model_inputs(chat_output)

        print("[LLM] Generating...")
        with torch.inference_mode():
            generate_kwargs = dict(
                max_new_tokens=self.config.max_new_tokens,
                do_sample=self.config.temperature > 0,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
                use_cache=True,
            )
            if self.config.temperature > 0:
                generate_kwargs["temperature"] = self.config.temperature
                generate_kwargs["top_p"] = self.config.top_p

            output_ids = self.model.generate(**model_inputs, **generate_kwargs)

        generated_ids = output_ids[0][prompt_length:]
        return self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
