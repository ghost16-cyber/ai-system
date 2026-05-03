# Quantization utilities for 4-bit models
import torch


class QuantizationManager:
    """Monitor and manage GPU quantization settings."""
    
    @staticmethod
    def get_gpu_memory_usage():
        """Get current GPU memory usage in MB."""
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / 1024 / 1024
        return 0
    
    @staticmethod
    def get_gpu_memory_max():
        """Get max GPU memory in MB."""
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 1024 / 1024
        return 0
    
    @staticmethod
    def print_memory_stats():
        """Print current GPU memory stats."""
        if torch.cuda.is_available():
            used = QuantizationManager.get_gpu_memory_usage()
            total = QuantizationManager.get_gpu_memory_max()
            print(f"GPU Memory: {used:.1f}MB / {total:.1f}MB ({used/total*100:.1f}%)")
        else:
            print("CUDA not available")
