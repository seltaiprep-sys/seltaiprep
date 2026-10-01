"""IELTS configuration with Pydantic v2 best practices"""

import warnings
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central IELTS configuration - all modules use this"""

    # =====================================================
    # Pydantic config
    # =====================================================

    model_config = SettingsConfigDict(
        env_prefix="IELTS_",
        extra="ignore",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # =====================================================
    # AUDIO SETTINGS
    # =====================================================

    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    max_audio_duration: int = Field(default=300, ge=1)
    max_audio_size_mb: int = Field(default=50, ge=1)
    min_audio_duration: float = Field(default=0.5, ge=0.1)

    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm"]
    )

    frame_length: int = Field(default=2048, ge=256)
    hop_length: int = Field(default=512, ge=64)

    # =====================================================
    # MODELS
    # =====================================================

    whisper_model_size: Literal[
        "tiny", "tiny.en",
        "base", "base.en",
        "small", "small.en",
        "medium", "medium.en",
        "large-v3"
    ] = Field(
        default="base",
        description="Whisper model size (validated at startup)",
    )

    wav2vec2_model: str = Field(
        default="facebook/wav2vec2-base-960h",
        description="HuggingFace Wav2Vec2 model name",
    )

    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        min_length=3,
        description="SentenceTransformer embedding model",
    )

    # FIXED: safe cross-platform path
    model_cache_dir: Path = Field(
        default_factory=lambda: Path("models_cache").resolve(),
        description="Directory to cache downloaded models",
    )

    # =====================================================
    # GPU SETTINGS
    # =====================================================

    use_mixed_precision: bool = True

    gpu_timeout_seconds: int = Field(default=30, ge=1)

    max_gpu_memory_percent: float = Field(
        default=0.85,
        ge=0.1,
        le=1.0,
    )

    max_concurrent_models: int = Field(
        default=2,
        ge=1,
        le=10,
    )

    # =====================================================
    # DATABASE / CACHE
    # =====================================================

    db_url: str = Field(default="sqlite:///ielts.db")

    redis_url: str = Field(default="redis://localhost:6379/0")

    enable_cache: bool = True

    cache_ttl: int = Field(default=3600, ge=1)

    # =====================================================
    # IELTS SCORING WEIGHTS
    # =====================================================

    fluency_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    lexical_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    grammar_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    pronunciation_weight: float = Field(default=0.25, ge=0.0, le=1.0)

    # =====================================================
    # VALIDATION (COMBINED - FIXED)
    # =====================================================

    @model_validator(mode="after")
    def validate_all(self) -> "Settings":
        """Validate weights + log warnings"""

        # ---- IELTS weights validation ----
        total = (
            self.fluency_weight +
            self.lexical_weight +
            self.grammar_weight +
            self.pronunciation_weight
        )

        if abs(total - 1.0) > 0.01:
            warnings.warn(
                f"IELTS weights sum to {total:.3f}, expected 1.0"
            )

        # NOTE:
        # Do NOT create directories here (side effects removed)
        # Directory creation should be done in app startup

        return self

    # =====================================================
    # HELPER PROPERTIES
    # =====================================================

    @property
    def model_cache_str(self) -> str:
        """String version of cache path"""
        return str(self.model_cache_dir)

    @property
    def is_production(self) -> bool:
        """Detect production environment"""
        return "postgresql" in self.db_url.lower()


# =====================================================
# SINGLETON INSTANCE
# =====================================================

settings = Settings()