# Main Entry Point - Initialize and run the full system
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.inference.pipeline import InferencePipeline
from src.utils.memory_monitor import MemoryMonitor
from src.utils.logger import setup_logger




def main():
    # Setup logging
    logger = setup_logger("ai-system", "logs/system.log")
    logger.info("Starting AI Code Analysis System")
    
    # Check resources
    MemoryMonitor.print_stats()
    
    # Initialize pipeline
    logger.info("Initializing inference pipeline...")
    pipeline = InferencePipeline()
    
    try:
        pipeline.initialize(
            classifier_path="data/models/pattern_clf.pkl",
            index_path="data/models/rag_index.faiss",
            metadata_path="data/models/rag_metadata.json"
        )
    except FileNotFoundError as e:
        logger.error(f"Missing model files: {e}")
        logger.info("Run training scripts first:")
        logger.info("  1. python training/scripts/setup.py")
        logger.info("  2. python training/scripts/train_classifier.py")
        logger.info("  3. python training/scripts/build_rag.py")
        return
    
    # Example analysis
    test_code = """
for i in range(len(items)):
    print(items[i])
"""
    
    logger.info(f"Analyzing code...\n{test_code}")
    result = pipeline.analyze(test_code)
    logger.info(f"Result: {result}")
    
    MemoryMonitor.print_stats()


if __name__ == "__main__":
    main()
