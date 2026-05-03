# src/embeddings/embedding.py
"""
Code embedding utilities.

Uses SentenceTransformer to produce dense vector representations of code snippets.
The embeddings are L2‑normalized (unit length) to work well with FAISS L2 index.
"""

from typing import Union
import numpy as np


try:
    from sentence_transformers import SentenceTransformer
except ImportError as exc:
    raise ImportError(
        "SentenceTransformer is required for code embeddings. "
        "Install with `pip install sentence-transformers`."
    ) from exc


class CodeEmbedder:
    """
    Lightweight wrapper around a SentenceTransformer model.

    Parameters
    ----------
    model_name : str, optional
        HuggingFace model identifier. Default is a small, fast model.
    device : str, optional
        ``cpu`` or ``cuda``. ``cpu`` is safe for all environments.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", device: str = "cpu"):
        self.device = device
        self.model = SentenceTransformer(model_name, device=device)

    def embed(self, text: Union[str, list[str]]) -> np.ndarray:
        """
        Encode a single string or a list of strings into a 1‑D NumPy array.

        Returns
        -------
        np.ndarray
            Shape ``(dim,)`` for a single string or ``(n, dim)`` for a list.
        """
        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embedding.astype(np.float32)