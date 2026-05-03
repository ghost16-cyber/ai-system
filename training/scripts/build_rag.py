# Build RAG Vector Store - Create embeddings and FAISS index
import sys
from pathlib import Path
import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.rag.retriever import VectorStoreRetriever
from src.rag.builder import RAGBuilder
from src.embeddings.embedding import CodeEmbedder
import pandas as pd


def create_real_embeddings(code_snippets):
    """
    Create real embeddings using SentenceTransformer.
    """
    print("Loading embedding model...")
    embedder = CodeEmbedder()
    print(f"Encoding {len(code_snippets)} code snippets...")
    embeddings = embedder.embed(code_snippets)
    print(f"✓ Created embeddings with shape {embeddings.shape}")
    return embeddings


def main():
    print("="*60)
    print("BUILD RAG VECTOR STORE")
    print("="*60)
    
    # Load data
    csv_path = "data/processed/code_patterns.csv"
    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        return
    
    df = pd.read_csv(csv_path)
    code_snippets = df["code_snippet"].astype(str).tolist()
    
    print(f"Processing {len(code_snippets)} code examples...")
    
    # Create real embeddings
    embeddings = create_real_embeddings(code_snippets)
    
    # Build FAISS index
    retriever = VectorStoreRetriever()
    retriever.build_index(embeddings)
    
    # Add metadata
    metadata = [
        RAGBuilder.format_example(
            code=snippet,
            explanation=f"Pattern: {label}"
        )
        for snippet, label in zip(code_snippets, df["label"])
    ]
    retriever.add_metadata(metadata)
    
    # Save
    index_path = "data/models/rag_index.faiss"
    metadata_path = "data/models/rag_metadata.json"
    retriever.save(index_path, metadata_path)
    
    print(f"\n✓ Vector store created!")
    print(f"  Index: {index_path}")
    print(f"  Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
