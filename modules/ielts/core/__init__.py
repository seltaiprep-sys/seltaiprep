# modules/ielts/core/__init__.py
"""IELTS Core - Shared components for all modules"""

# Configuration
from .config import settings

# Scoring
from .base_scoring import BaseScoring, round_ielts_band, get_band_description, get_cefr_level

# Database / Cache
from .database import db_manager

# GPU Management
from .gpu_manager import gpu_manager

# Model Loading
from .model_loader import model_loader

# Schemas - Core
from .schemas import (
    BandScore,
    TestResult,
    Question,
    AudioSegment,
    APIResponse,
    HealthStatus,
)

# Schemas - Speaking
from .schemas import (
    PronunciationResult,
    EmotionResult,
    GrammarResult,
    CoherenceResult,
    SpeakingResult,
)

# Schemas - Listening
from .schemas import (
    ListeningTest,
    ListeningSection,
    Section1Scenario,
)

# Schemas - Writing
from .schemas import (
    WritingTask,
    WritingCriteria,
    WritingResult,
)

# Schemas - Reading
from .schemas import (
    ReadingTest,
    ReadingPassage,
)

__all__ = [
    # Config
    "settings",
    # Scoring
    "BaseScoring",
    "round_ielts_band",
    "get_band_description",
    "get_cefr_level",
    # Database
    "db_manager",
    # GPU
    "gpu_manager",
    # Models
    "model_loader",
    # Core Schemas
    "BandScore",
    "TestResult",
    "Question",
    "AudioSegment",
    "APIResponse",
    "HealthStatus",
    # Speaking Schemas
    "PronunciationResult",
    "EmotionResult",
    "GrammarResult",
    "CoherenceResult",
    "SpeakingResult",
    # Listening Schemas
    "ListeningTest",
    "ListeningSection",
    "Section1Scenario",
    # Writing Schemas
    "WritingTask",
    "WritingCriteria",
    "WritingResult",
    # Reading Schemas
    "ReadingTest",
    "ReadingPassage",
]