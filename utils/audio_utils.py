import numpy as np
import librosa
import hashlib
from pathlib import Path
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)
from core.config import settings

class AudioUtils:
    @staticmethod
    def load(filepath: str, target_sr: int = None) -> Tuple[Optional[np.ndarray], int]:
        path = Path(filepath)
        if path.suffix.lower() not in settings.allowed_extensions: return None, 0
        if not path.exists(): return None, 0
        if path.stat().st_size > settings.max_audio_size_mb * 1024 * 1024: return None, 0
        try:
            target_sr = target_sr or settings.sample_rate
            audio, sr = librosa.load(str(path), sr=target_sr, mono=True)
            if len(audio) / sr < settings.min_audio_duration: return None, sr
            if np.any(np.isnan(audio)) or np.any(np.isinf(audio)): return None, sr
            m = np.max(np.abs(audio))
            if m > 0: audio = audio / m
            return audio.astype(np.float32), sr
        except Exception as e:
            logger.exception(f"Load error: {e}")
            return None, 0

    @staticmethod
    def is_silence(audio: np.ndarray, threshold: float = 0.005) -> bool:
        return np.mean(np.abs(audio)) < threshold

    @staticmethod
    def cache_key(audio: np.ndarray) -> str:
        return hashlib.md5(audio.tobytes()[:500000]).hexdigest()