from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib import request


@dataclass
class OllamaClient:
    model: str = "qwen2.5-coder:1.5b"
    base_url: str = "http://localhost:11434"
    timeout_seconds: int = 90
    temperature: float = 0.1
    num_predict: int = 600

    def generate(self, prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/api/generate"

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
            },
        }

        encoded = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            url,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str(data.get("response", "")).strip()
