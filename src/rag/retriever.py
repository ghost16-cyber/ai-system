# Vector Store Retriever - FAISS-based few-shot example retrieval
import faiss
import numpy as np
import json
from pathlib import Path


class VectorStoreRetriever:
    """
    Retrieve similar code examples for RAG prompting.
    Uses FAISS for efficient CPU-based similarity search.
    """
    
    def __init__(self, index_path=None, metadata_path=None):
        self.index = None
        self.metadata = []
        self.embedding_dim = 768
        
        if index_path and metadata_path:
            self.load(index_path, metadata_path)
    
    def build_index(self, embeddings):
        """Build FAISS index from embeddings."""
        embeddings = np.array(embeddings, dtype=np.float32)
        self.embedding_dim = embeddings.shape[1]
        
        self.index = faiss.IndexFlatL2(self.embedding_dim)
        self.index.add(embeddings)
        print(f"✓ FAISS index built with {len(embeddings)} vectors")
    
    def retrieve(self, query_embedding, k=3):
        """Retrieve top-k similar examples."""
        if self.index is None:
            raise ValueError("Index not built. Call build_index() first.")
        
        query = np.array([query_embedding], dtype=np.float32)
        distances, indices = self.index.search(query, k)
        
        results = []
        for idx in indices[0]:
            if idx < len(self.metadata):
                results.append(self.metadata[int(idx)])
        
        return results
    
    def add_metadata(self, metadata_list):
        """Add metadata for embeddings (original code + explanation)."""
        self.metadata.extend(metadata_list)
    
    def save(self, index_path, metadata_path):
        """Save FAISS index and metadata."""
        if self.index:
            faiss.write_index(self.index, index_path)
        
        with open(metadata_path, 'w') as f:
            json.dump(self.metadata, f)
        
        print(f"✓ Index saved to {index_path}")
        print(f"✓ Metadata saved to {metadata_path}")
    
    def load(self, index_path, metadata_path):
        """Load FAISS index and metadata."""
        self.index = faiss.read_index(index_path)
        
        with open(metadata_path, 'r') as f:
            self.metadata = json.load(f)
        
        print(f"✓ Index loaded from {index_path}")
        print(f"✓ Metadata loaded ({len(self.metadata)} examples)")
