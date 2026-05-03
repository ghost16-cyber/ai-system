# repo_scanner/llm_engine/test_llm.py
from __future__ import annotations

# Relative import works when the file is executed as a module:
#   python -m repo_scanner.llm_engine.test_llm
from .reasoning_engine import RepoReasoningLLM, LLMConfig


def main() -> None:
    # Minimal, deterministic request – good for a quick sanity‑check
    llm = RepoReasoningLLM(
        LLMConfig(
            max_new_tokens=40,   # keep generation short
            temperature=0.0,     # deterministic output
        )
    )
    response = llm.reason("Say only: LLM OK")
    print(response)


if __name__ == "__main__":
    main()