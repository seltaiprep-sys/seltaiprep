# modules/ielts/core/model_loader.py
"""Central AI model loader with lazy loading and GPU optimization"""

from __future__ import annotations

import threading
import logging
import time
from typing import Any

import torch

from .config import settings
from .gpu_manager import gpu_manager

logger = logging.getLogger(__name__)

# =========================================================
# OPTIONAL IMPORTS (moved to top with availability flags)
# =========================================================

try:
    from faster_whisper import WhisperModel as FasterWhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    ST_AVAILABLE = True
except ImportError:
    ST_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False


# =========================================================
# MODEL LOADER
# =========================================================

class ModelLoader:
    """Central singleton AI model manager"""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._whisper = None
        self._whisper_processor = None
        self._wav2vec2 = None
        self._wav2vec2_processor = None
        self._embedding = None
        self._nlp = None

        # Model version tracking (FIX #5)
        self._model_versions: dict[str, str] = {}

        # Memory cache to avoid frequent CUDA calls
        self._memory_cache = None
        self._memory_cache_time = 0
        self._memory_cache_ttl = 3 # seconds

        self._model_lock = threading.Lock()
        self._initialized = True

        logger.info("ModelLoader initialized")

    # =====================================================
    # DEVICE (FIX #3: Consistent device access)
    # =====================================================

    @property
    def device(self) -> torch.device:
        return gpu_manager.get_device()

    @property
    def is_cuda(self) -> bool:
        """Check if CUDA is available (consistent with GPUManager)"""
        return gpu_manager.is_available()

    # =====================================================
    # WHISPER (FIX #1: Critical failure logging)
    # =====================================================

    @property
    def whisper(self):
        """Whisper model - Faster-Whisper with automatic fallback"""
        whisper = self._whisper
        if whisper is not None:
            return whisper

        with self._model_lock:
            if self._whisper is not None:
                return self._whisper

            gpu_manager.safe_cleanup_if_needed()

            # Try Faster-Whisper first
            if FASTER_WHISPER_AVAILABLE:
                model = self._load_faster_whisper()
                if model is not None:
                    self._whisper = model
                    return self._whisper
                logger.warning("Faster-Whisper returned None, switching to Transformers fallback")

            # Transformers fallback
            self._whisper, self._whisper_processor = self._load_transformers_whisper()

            # FIX #1: Critical failure logging
            if self._whisper is None:
                logger.critical(
                    "CRITICAL: Whisper completely failed to load (both Faster-Whisper and Transformers). "
                    "Speech transcription will be unavailable."
                )
            else:
                logger.info("Whisper loaded successfully via fallback")

            return self._whisper

    @property
    def whisper_processor(self):
        """Whisper processor (only for Transformers fallback)"""
        if not FASTER_WHISPER_AVAILABLE and self._whisper_processor is None:
            _ = self.whisper
        return self._whisper_processor

    def _load_faster_whisper(self):
        """Load Faster-Whisper model"""
        try:
            logger.info("Loading Faster-Whisper...")

            device = "cuda" if self.is_cuda else "cpu" # FIX #3: Consistent device check
            compute_type = (
                "float16" if device == "cuda" and settings.use_mixed_precision else "int8"
            )

            model = FasterWhisperModel(
                getattr(settings, "whisper_model_size", "base"),
                device=device,
                compute_type=compute_type,
                num_workers=2,
            )
            self._model_versions["whisper"] = "faster-whisper" # FIX #5
            logger.info(f"Faster-Whisper loaded ({settings.whisper_model_size})")
            return model

        except Exception as e:
            logger.exception(f"Faster-Whisper load failed: {e}")
            return None

    def _load_transformers_whisper(self):
        """Load Transformers Whisper fallback"""
        try:
            from transformers import WhisperProcessor, WhisperForConditionalGeneration

            model_name = f"openai/whisper-{getattr(settings, 'whisper_model_size', 'base')}"
            processor = WhisperProcessor.from_pretrained(model_name)

            dtype = (
                torch.float16
                if self.is_cuda and settings.use_mixed_precision # FIX #3
                else torch.float32
            )

            model = WhisperForConditionalGeneration.from_pretrained(
                model_name, torch_dtype=dtype
            ).to(self.device)

            model.eval()
            self._model_versions["whisper"] = model_name # FIX #5
            logger.info(f"Transformers Whisper loaded ({model_name})")
            return model, processor

        except Exception as e:
            logger.exception(f"Transformers Whisper load failed: {e}")
            return None, None

    # =====================================================
    # WAV2VEC2
    # =====================================================

    @property
    def wav2vec2(self):
        """Wav2Vec2 model"""
        wav2vec2 = self._wav2vec2
        if wav2vec2 is not None:
            return wav2vec2

        with self._model_lock:
            if self._wav2vec2 is not None:
                return self._wav2vec2

            gpu_manager.safe_cleanup_if_needed()
            self._wav2vec2, self._wav2vec2_processor = self._load_wav2vec2()

            # Critical failure check
            if self._wav2vec2 is None:
                logger.critical("CRITICAL: Wav2Vec2 failed to load. Pronunciation scoring will be unavailable.")

            return self._wav2vec2

    @property
    def wav2vec2_processor(self):
        """Wav2Vec2 processor"""
        if self._wav2vec2_processor is None:
            _ = self.wav2vec2
        return self._wav2vec2_processor

    def _load_wav2vec2(self):
        """Load Wav2Vec2 model"""
        try:
            logger.info("Loading Wav2Vec2...")

            from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC

            model_name = getattr(settings, "wav2vec2_model", "facebook/wav2vec2-base-960h")
            processor = Wav2Vec2Processor.from_pretrained(model_name)
            model = Wav2Vec2ForCTC.from_pretrained(model_name).to(self.device)
            model.eval()

            self._model_versions["wav2vec2"] = model_name # FIX #5
            logger.info(f"Wav2Vec2 loaded ({model_name})")
            return model, processor

        except Exception as e:
            logger.exception(f"Wav2Vec2 load failed: {e}")
            return None, None

    # =====================================================
    # EMBEDDING
    # =====================================================

    @property
    def embedding(self):
        """SentenceTransformer embedding model"""
        embedding = self._embedding
        if embedding is not None:
            return embedding

        if not ST_AVAILABLE:
            logger.error("sentence-transformers not installed")
            return None

        with self._model_lock:
            if self._embedding is not None:
                return self._embedding

            gpu_manager.safe_cleanup_if_needed()

            try:
                model_name = getattr(settings, "embedding_model", "all-MiniLM-L6-v2")
                self._embedding = SentenceTransformer(
                    model_name,
                    device=str(self.device),
                )
                self._model_versions["embedding"] = model_name # FIX #5
                logger.info(f"Embedding model loaded ({model_name})")
            except Exception as e:
                logger.exception(f"Embedding model load failed: {e}")
                self._embedding = None

            return self._embedding

    # =====================================================
    # SPACY
    # =====================================================

    @property
    def nlp(self):
        """spaCy NLP model"""
        if not SPACY_AVAILABLE:
            return None

        nlp = self._nlp
        if nlp is not None:
            return nlp

        with self._model_lock:
            if self._nlp is not None:
                return self._nlp

            try:
                self._nlp = spacy.load("en_core_web_sm")
                self._model_versions["nlp"] = "en_core_web_sm" # FIX #5
                logger.info("spaCy NLP loaded")
            except Exception as e:
                logger.exception(f"spaCy load failed: {e}")
                self._nlp = None

            return self._nlp

    # =====================================================
    # MEMORY (cached to avoid CUDA overhead)
    # =====================================================

    @property
    def memory(self) -> dict | None:
        """Get GPU memory info (cached for 3 seconds)"""
        now = time.time()

        if self._memory_cache and (now - self._memory_cache_time) < self._memory_cache_ttl:
            return self._memory_cache

        self._memory_cache = gpu_manager.get_memory_info()
        self._memory_cache_time = now

        return self._memory_cache

    # =====================================================
    # WARMUP (FIX #4: Returns status dict)
    # =====================================================

    def warmup_all(self) -> dict[str, bool]:
        """Warmup all models. Returns status of each model load."""
        logger.info("Warming up all models...")
        results = {}

        # Whisper
        try:
            _ = self.whisper
            results["whisper"] = self._whisper is not None
            if results["whisper"]:
                logger.info(" Whisper warmed up")
            else:
                logger.warning(" Whisper is None after warmup")
        except Exception as e:
            results["whisper"] = False
            logger.warning(f" Whisper warmup failed: {e}")

        # Wav2Vec2
        try:
            _ = self.wav2vec2
            results["wav2vec2"] = self._wav2vec2 is not None
            if results["wav2vec2"]:
                logger.info(" Wav2Vec2 warmed up")
            else:
                logger.warning(" Wav2Vec2 is None after warmup")
        except Exception as e:
            results["wav2vec2"] = False
            logger.warning(f" Wav2Vec2 warmup failed: {e}")

        # Embedding
        if ST_AVAILABLE:
            try:
                _ = self.embedding
                results["embedding"] = self._embedding is not None
                if results["embedding"]:
                    logger.info(" Embedding warmed up")
                else:
                    logger.warning(" Embedding is None after warmup")
            except Exception as e:
                results["embedding"] = False
                logger.warning(f" Embedding warmup failed: {e}")
        else:
            results["embedding"] = False
            logger.warning(" Embedding skipped (not installed)")

        # spaCy
        if SPACY_AVAILABLE:
            try:
                _ = self.nlp
                results["nlp"] = self._nlp is not None
                if results["nlp"]:
                    logger.info(" spaCy warmed up")
                else:
                    logger.warning(" spaCy is None after warmup")
            except Exception as e:
                results["nlp"] = False
                logger.warning(f" spaCy warmup failed: {e}")
        else:
            results["nlp"] = False
            logger.warning(" spaCy skipped (not installed)")

        all_ok = all(results.values())
        if all_ok:
            logger.info("All models warmed up successfully ")
        else:
            failed = [k for k, v in results.items() if not v]
            logger.warning(f"Warmup complete with failures: {failed}")

        return results

    # =====================================================
    # HEALTH CHECK
    # =====================================================

    def health_check(self) -> dict[str, Any]:
        """Quick health check for all models (no loading triggered)"""
        return {
            "whisper_ok": self._whisper is not None,
            "whisper_type": (
                "faster-whisper" if FASTER_WHISPER_AVAILABLE and self._whisper
                else "transformers" if self._whisper
                else "none"
            ),
            "wav2vec2_ok": self._wav2vec2 is not None,
            "embedding_ok": self._embedding is not None,
            "nlp_ok": self._nlp is not None,
            "gpu_available": self.is_cuda, # FIX #3: Consistent
            "gpu_memory": self.memory,
            "device": str(self.device),
            "model_versions": self._model_versions, # FIX #5
        }

    # =====================================================
    # STATUS
    # =====================================================

    def get_status(self) -> dict[str, Any]:
        """Get detailed model status"""
        return {
            **self.health_check(),
            "faster_whisper_installed": FASTER_WHISPER_AVAILABLE,
            "sentence_transformers_installed": ST_AVAILABLE,
            "spacy_installed": SPACY_AVAILABLE,
            "model_versions": self._model_versions, # FIX #5
        }

    # =====================================================
    # UNLOAD
    # =====================================================

    def unload(self, name: str) -> None:
        """Unload specific model"""
        with self._model_lock:
            logger.info(f"Unloading: {name}")
            if name == "whisper":
                self._whisper = None
                self._whisper_processor = None
            elif name == "wav2vec2":
                self._wav2vec2 = None
                self._wav2vec2_processor = None
            elif name == "embedding":
                self._embedding = None
            elif name == "nlp":
                self._nlp = None
            self._model_versions.pop(name, None) # FIX #5
            gpu_manager.cleanup(aggressive=True)

    def unload_all(self) -> None:
        """Unload all models"""
        with self._model_lock:
            logger.info("Unloading all models")
            self._whisper = None
            self._whisper_processor = None
            self._wav2vec2 = None
            self._wav2vec2_processor = None
            self._embedding = None
            self._nlp = None
            self._memory_cache = None
            self._model_versions.clear() # FIX #5
            gpu_manager.cleanup(aggressive=True)


# Singleton
model_loader = ModelLoader()