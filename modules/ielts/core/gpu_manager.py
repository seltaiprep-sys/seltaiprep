# modules/ielts/core/gpu_manager.py
"""Central GPU memory manager"""

import gc
import threading
import logging
from contextlib import contextmanager

import torch

from .config import settings

logger = logging.getLogger(__name__)


class GPUManager:
    """Central GPU memory manager"""

    _lock = threading.Lock()
    _gpu_available = torch.cuda.is_available()

    @classmethod
    def get_device(cls) -> torch.device:
        """Return active device"""
        return torch.device("cuda" if cls._gpu_available else "cpu")

    @classmethod
    def is_available(cls) -> bool:
        """Check CUDA availability"""
        return cls._gpu_available

    @classmethod
    def get_memory_info(cls) -> dict | None:
        """Return GPU memory usage info"""
        if not cls._gpu_available:
            return None

        try:
            free, total = torch.cuda.mem_get_info()
            used = total - free

            return {
                "free_mb": round(free / (1024 * 1024), 2),
                "used_mb": round(used / (1024 * 1024), 2),
                "total_mb": round(total / (1024 * 1024), 2),
                "usage_percent": round((used / total) * 100, 2),
            }

        except Exception as e:
            logger.warning(f"GPU memory check failed: {e}")
            return None

    @classmethod
    def memory_safe(cls) -> bool:
        """Check if GPU usage is safe"""
        if not cls._gpu_available:
            return True

        info = cls.get_memory_info()
        if not info:
            return True

        # FIXED: settings.max_gpu_memory_percent is 0-1 (0.9 = 90%)
        # info["usage_percent"] is already 0-100
        return info["usage_percent"] < (settings.max_gpu_memory_percent * 100)

    @classmethod
    def cleanup(cls, aggressive: bool = False) -> None:
        """Cleanup GPU memory"""
        if not cls._gpu_available:
            return

        with cls._lock:
            try:
                gc.collect()
                torch.cuda.empty_cache()

                if aggressive:
                    torch.cuda.synchronize()
                    torch.cuda.reset_peak_memory_stats()

                logger.debug("GPU memory cleaned")

            except Exception as e:
                logger.warning(f"GPU cleanup failed: {e}")

    @classmethod
    def safe_cleanup_if_needed(cls) -> None:
        """Auto cleanup if memory too high"""
        if not cls.memory_safe():
            logger.warning("High GPU memory usage detected")
            cls.cleanup(aggressive=True)

    @staticmethod
    @contextmanager
    def autocast():
        """Mixed precision context manager"""
        if torch.cuda.is_available() and settings.use_mixed_precision:
            with torch.amp.autocast("cuda"): # FIXED: torch.cuda.amp.autocast() → torch.amp.autocast("cuda")
                yield
        else:
            yield


# Singleton
gpu_manager = GPUManager()