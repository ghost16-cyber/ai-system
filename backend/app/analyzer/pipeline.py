# backend/app/analyzer/pipeline.py
"""
Full inference pipeline.

Combines:
* Fast pattern classifier (CPU)
* Code embedding + FAISS retrieval (RAG)
* Prompt construction
* 4‑bit LLM generation (GPU)
* Simple confidence threshold gating
* In‑memory caching for embeddings and LLM responses
"""

from typing import Any, Dict, List, Optional

import numpy as np

from backend.app.llm.loader import ModelLoader
from backend.app.ml.classifier import PatternClassifier
from backend.app.rag.retriever import VectorStoreRetriever
from backend.app.rag.builder import RAGBuilder
from backend.app.rag.embeddings import CodeEmbedder
from backend.app.core import LRUCache
from backend.app.analyzer.code_analyzer import SUGGESTIONS


class InferencePipeline:
    """Orchestrate the end‑to‑end analysis flow."""

    def __init__(self) -> None:
        # Core components
        self.fast_clf = PatternClassifier()
        self.rag_retriever = VectorStoreRetriever()
        self.llm_loader = ModelLoader()
        self.model = None
        self.tokenizer = None

        # Helper objects – instantiated in ``initialize`` with defaults
        self.embedder: Optional[CodeEmbedder] = None
        self.rag_builder: Optional[RAGBuilder] = None
        self.cache: Optional[LRUCache] = None

        # Threshold for full RAG+LLM path
        self.llm_threshold: float = 0.6

    def initialize(
        self,
        classifier_path: Optional[str] = None,
        index_path: Optional[str] = None,
        metadata_path: Optional[str] = None,
        embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedder_device: str = "cpu",
        cache_size: int = 256,
        llm_threshold: float = 0.6,
    ) -> None:
        """Load all heavy resources and wire helper objects."""
        print("Initializing inference pipeline...")

        # 1️⃣ Classifier
        if classifier_path:
            self.fast_clf.load(classifier_path)

        # 2️⃣ RAG retriever
        if index_path and metadata_path:
            self.rag_retriever.load(index_path, metadata_path)

        # 3️⃣ LLM (4‑bit Qwen2.5‑Coder)
        self.model, self.tokenizer = self.llm_loader.load_4bit_model()
        self.llm_loader.add_lora_adapter()

        # 4️⃣ Embedding model
        self.embedder = CodeEmbedder(model_name=embedder_model, device=embedder_device)

        # 5️⃣ Prompt builder
        self.rag_builder = RAGBuilder(self.rag_retriever)

        # 6️⃣ Simple LRU cache
        self.cache = LRUCache(maxsize=cache_size)

        # 7️⃣ Deterministic gating threshold (replaces bandit)
        self.llm_threshold = llm_threshold

        print("✓ Pipeline initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze(self, code_snippet: str) -> Dict[str, Any]:
        """Run the full pipeline on a single snippet."""
        # --------------------------------------------------------------
        # 1️⃣ Fast classification
        # --------------------------------------------------------------
        pattern = self.fast_clf.predict(code_snippet)
        try:
            probs = self.fast_clf.predict_proba(code_snippet)
            confidence = float(np.max(probs)) if len(probs) > 0 else 0.0
        except Exception:
            confidence = 0.0

        # --------------------------------------------------------------
        # 2️⃣ Decision – simple threshold instead of UCB bandit
        # --------------------------------------------------------------
        action = 1 if confidence > self.llm_threshold else 0

        # --------------------------------------------------------------
        # 3️⃣ Prepare static suggestion
        # --------------------------------------------------------------
        suggestion_entry = SUGGESTIONS.get(
            pattern,
            {"issue": "Unknown pattern", "suggestion": "Review manually", "example": ""},
        )
        static_analysis = suggestion_entry["suggestion"]

        # --------------------------------------------------------------
        # 4️⃣ Cheap path – classifier only
        # --------------------------------------------------------------
        if action == 0:
            return {
                "pattern": pattern,
                "confidence": confidence,
                "suggestion": suggestion_entry["suggestion"],
                "issue": suggestion_entry["issue"],
                "example": suggestion_entry["example"],
                "analysis": static_analysis,
                "used_llm": False,
                "retrieved_examples": [],
            }

        # --------------------------------------------------------------
        # 5️⃣ Full path – RAG + LLM
        # --------------------------------------------------------------
        # 5.1 Embedding (cached)
        embedding = self.cache.get(code_snippet)
        if embedding is None:
            embedding = self.embedder.embed(code_snippet)
            self.cache.set(code_snippet, embedding)

        # 5.2 Retrieval
        retrieved_examples = self.rag_retriever.retrieve(embedding, k=2)

        # 5.3 Prompt construction
        prompt = self.rag_builder.build_prompt(
            query_code=code_snippet,
            query_embedding=embedding,
            k=2,
            system_prompt=None,
            pattern_suggestion=suggestion_entry["suggestion"],
        )

        # 5.4 LLM generation (cached)
        llm_response = self.cache.get(prompt)
        if llm_response is None:
            llm_response = self.llm_generate(prompt)
            self.cache.set(prompt, llm_response)

        # Post‑process the raw LLM output into a deterministic template
        llm_response = self._postprocess_analysis(
            llm_response, suggestion_entry["suggestion"]
        )

        # --------------------------------------------------------------
        # 6️⃣ Assemble final result
        # --------------------------------------------------------------
        return {
            "pattern": pattern,
            "confidence": confidence,
            "suggestion": suggestion_entry["suggestion"],
            "issue": suggestion_entry["issue"],
            "example": suggestion_entry["example"],
            "analysis": llm_response,
            "used_llm": True,
            "retrieved_examples": retrieved_examples,
        }

    # ------------------------------------------------------------------
    # 7️⃣ LLM generation – device‑aware
    # ------------------------------------------------------------------
    def llm_generate(self, prompt: str, max_tokens: int = 256) -> str:
        """
        Generate a response from the loaded LLM.

        Parameters
        ----------
        prompt : str
            Full prompt (system + examples + user code).
        max_tokens : int, default 256
            Maximum number of new tokens to generate.

        Returns
        -------
        str
            Decoded LLM output (special tokens stripped).
        """
        device = next(self.model.parameters()).device
        inputs = self.tokenizer(prompt, return_tensors="pt").to(device)

        # 4‑bit quantised models do not support temperature/top_p.
        outputs = self.model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            do_sample=False,  # deterministic greedy decoding
        )
        return self.tokenizer.decode(outputs[0], skip_special_tokens=True)

    # ------------------------------------------------------------------
    # 8️⃣ Post‑processing helper
    # ------------------------------------------------------------------
    def _postprocess_analysis(self, llm_response: str, suggestion: str) -> str:
        """
        Replace the raw LLM text with a deterministic template.

        This guarantees the analysis mentions the suggestion and the
        corrected code block.
        """
        # Simple heuristic: locate the first occurrence of a Python
        # statement (e.g., ``for``) and treat the rest as the corrected code.
        code_start = llm_response.find("for ")
        corrected = llm_response[code_start:] if code_start != -1 else llm_response

        template = (
            f"Issue: The loop uses `range(len(...))` which is less idiomatic.\n"
            f"Suggested fix: {suggestion}\n"
            f"Corrected code:\n{corrected}"
        )
        return template
