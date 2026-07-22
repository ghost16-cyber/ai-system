from __future__ import annotations

from dataclasses import dataclass

from backend.app.local_ai.provider import (
    OllamaProviderClient,
    ProviderGenerationRequest,
)


@dataclass
class OllamaClient:
    model: str
    base_url: str
    timeout_seconds: int = 90
    temperature: float = 0.1
    num_predict: int = 600

    def generate(self, prompt: str) -> str:
        response = OllamaProviderClient(self.base_url).generate(
            ProviderGenerationRequest(
                model=self.model,
                system_instruction="",
                prompt=prompt,
                timeout_seconds=self.timeout_seconds,
                maximum_output_tokens=self.num_predict,
                temperature=self.temperature,
                structured_json=False,
            )
        )
        return response.response.strip()
