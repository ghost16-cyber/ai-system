# src/rag/builder.py
# RAG Builder – Construct prompts with retrieved examples

from .retriever import VectorStoreRetriever  # optional; keep for type hints


class RAGBuilder:
    """
    Build retrieval‑augmented generation prompts using few‑shot examples.
    """

    def __init__(self, retriever: VectorStoreRetriever):
        self.retriever = retriever

    def build_prompt(
        self,
        query_code: str,
        query_embedding,
        k: int = 2,
        system_prompt: str | None = None,
        pattern_suggestion: str | None = None,
    ) -> str:
        """
        Build a few‑shot prompt with retrieved examples.

        Parameters
        ----------
        query_code : str
            The user‑provided code snippet to analyse.
        query_embedding : Any
            Embedding vector for ``query_code`` – used to retrieve similar examples.
        k : int, default 2
            Number of examples to retrieve.
        system_prompt : str | None, default None
            Optional system instruction. If omitted a default prompt is used.
        pattern_suggestion : str | None, default None
            Optional suggestion that should be shown to the model.

        Returns
        -------
        str
            The assembled prompt ready for LLM generation.
        """
        # 1️⃣ Retrieve similar examples
        examples = self.retriever.retrieve(query_embedding, k=k)

        # 2️⃣ Build the system part of the prompt
        if system_prompt is None:
            system_prompt = (
                "You are an expert Python code analyzer. "
                "When analyzing code patterns, first identify the issue, "
                "then explain the suggested fix, and finally show how to implement it."
            )
        prompt = f"{system_prompt}\n\n"

        # 3️⃣ Add retrieved examples
        for i, example in enumerate(examples, 1):
            prompt += f"Example {i}:\n"
            prompt += f"Code:\n{example.get('code', '')}\n"
            prompt += f"Explanation:\n{example.get('explanation', '')}\n\n"

        # 4️⃣ Add the user's code
        prompt += f"User's Code:\n{query_code}\n"

        # 5️⃣ Add any pattern‑specific guidance
        if pattern_suggestion:
            prompt += f"Suggested Fix: {pattern_suggestion}\n"

        prompt += (
            "Analysis (explain the issue, confirm the suggested fix, and show the improved code):\n"
        )
        return prompt
    

    
        if system_prompt is None:
            system_prompt = (
                "You are an expert Python code analyzer. "
                "When analyzing a code snippet, first identify the issue (if any), "
                "then confirm the suggested fix, and finally show the corrected code. "
                "If the pattern is only a style improvement (e.g., more Pythonic), "
                "state that it does not affect runtime performance."
            )

    @staticmethod
    def format_example(code: str, explanation: str) -> dict:
        """
        Helper to format a single example for storage in the vector store.

        Parameters
        ----------
        code : str
            Example source code.
        explanation : str
            Human‑readable explanation of the pattern.

        Returns
        -------
        dict
            ``{'code': code, 'explanation': explanation}``
        """
        return {"code": code, "explanation": explanation}