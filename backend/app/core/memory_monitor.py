# Memory Monitor - Track GPU and RAM usage
import torch
import psutil


class MemoryMonitor:
    """Monitor GPU VRAM and system RAM usage."""
    
    @staticmethod
    def get_gpu_memory_mb():
        """Get GPU memory usage in MB."""
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / 1024 / 1024
        return 0
    
    @staticmethod
    def get_gpu_total_mb():
        """Get total GPU memory in MB."""
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 1024 / 1024
        return 0
    
    @staticmethod
    def get_gpu_percent():
        """Get GPU memory usage as percentage."""
        used = MemoryMonitor.get_gpu_memory_mb()
        total = MemoryMonitor.get_gpu_total_mb()
        if total == 0:
            return 0
        return (used / total) * 100
    
    @staticmethod
    def get_ram_mb():
        """Get system RAM usage in MB."""
        return psutil.virtual_memory().used / 1024 / 1024
    
    @staticmethod
    def get_ram_total_mb():
        """Get total system RAM in MB."""
        return psutil.virtual_memory().total / 1024 / 1024
    
    @staticmethod
    def get_ram_percent():
        """Get RAM usage as percentage."""
        return psutil.virtual_memory().percent
    
    @staticmethod
    def print_stats():
        """Print detailed memory statistics."""
        gpu_used = MemoryMonitor.get_gpu_memory_mb()
        gpu_total = MemoryMonitor.get_gpu_total_mb()
        gpu_pct = MemoryMonitor.get_gpu_percent()
        
        ram_used = MemoryMonitor.get_ram_mb()
        ram_total = MemoryMonitor.get_ram_total_mb()
        ram_pct = MemoryMonitor.get_ram_percent()
        
        print("\n" + "="*50)
        print("MEMORY STATISTICS")
        print("="*50)
        print(f"GPU VRAM: {gpu_used:.1f}MB / {gpu_total:.1f}MB ({gpu_pct:.1f}%)")
        print(f"System RAM: {ram_used:.1f}MB / {ram_total:.1f}MB ({ram_pct:.1f}%)")
        print("="*50 + "\n")
    
    @staticmethod
    def check_available(min_gpu_mb=500, min_ram_mb=2000):
        """Check if sufficient memory is available."""
        gpu_available = MemoryMonitor.get_gpu_memory_mb() < min_gpu_mb
        ram_available = psutil.virtual_memory().available / 1024 / 1024 > min_ram_mb
        return gpu_available and ram_available
