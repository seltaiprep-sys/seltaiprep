"""
IELTS Speaking Module - Full exports with graceful fallbacks
Now exports the updated API with subscription manager support.
"""

import logging

logger = logging.getLogger(__name__)


# ============= CORE GENERATORS =============
from .test_generator import (
    create_speaking_test_generator,
    create_speaking_test, # proper signature (voice, audio_cache_dir)
    SpeakingTest,
)
from .transcriber import create_transcriber

# ============= TTS =============
from .examiner_voice import create_examiner_voice, ExaminerVoice

# ============= BAND CALCULATOR =============
try:
    from .scoring import create_band_calculator, IELTSBandCalculator
except ImportError as e:
    logger.warning(f"scoring module unavailable: {e}")
    create_band_calculator = None
    IELTSBandCalculator = None

# ============= CONTENT-AWARE EXAMINER =============
from .content_aware_examiner import ContentAwareExaminer

# ============= ANALYZERS =============
try:
    from .coherence import create_coherence_analyzer
except ImportError as e:
    logger.warning(f"coherence module unavailable: {e}")
    create_coherence_analyzer = None

try:
    from .grammar import create_grammar_analyzer
except ImportError as e:
    logger.warning(f"grammar module unavailable: {e}")
    create_grammar_analyzer = None

try:
    from .emotion import create_emotion_detector
except ImportError as e:
    logger.warning(f"emotion module unavailable: {e}")
    create_emotion_detector = None

try:
    from .pronunciation import create_pronunciation_scorer
except ImportError as e:
    logger.warning(f"pronunciation module unavailable: {e}")
    create_pronunciation_scorer = None

# ============= AUDIO & RECORDING =============
try:
    from .recorder import create_recorder
except ImportError as e:
    logger.warning(f"recorder module unavailable: {e}")
    create_recorder = None

try:
    from .vad import create_vad
except ImportError as e:
    logger.warning(f"vad module unavailable: {e}")
    create_vad = None

# ============= PROGRESS TRACKER =============
try:
    from .progress_tracker import create_progress_tracker
except ImportError as e:
    logger.warning(f"progress_tracker module unavailable: {e}")
    create_progress_tracker = None

# ============= FULL TEST ORCHESTRATOR =============
try:
    from .integrated_mock_test import IntegratedSpeakingMockTest
except ImportError as e:
    logger.warning(f"integrated_mock_test module unavailable: {e}")
    IntegratedSpeakingMockTest = None

# ============= DATABASE =============
try:
    from .repository import create_speaking_repository
except ImportError as e:
    logger.warning(f"repository module unavailable: {e}")
    create_speaking_repository = None

# ============= UPDATED API (with subscription manager) =============
try:
    from .api import create_speaking_api, IELTSSpeakingAPI
    IELTSSpeakingAI = IELTSSpeakingAPI # backward compat alias
except ImportError as e:
    logger.warning(f"api module unavailable: {e}")
    create_speaking_api = None
    IELTSSpeakingAPI = None
    IELTSSpeakingAI = None

# ============= PUBLIC API =============
__all__ = [
    # Core
    'create_speaking_test_generator',
    'create_speaking_test',
    'SpeakingTest',
    'create_transcriber',
    # TTS
    'create_examiner_voice',
    'ExaminerVoice',
    # Scoring
    'create_band_calculator',
    'IELTSBandCalculator',
    # Examiner
    'ContentAwareExaminer',
    # Analyzers
    'create_coherence_analyzer',
    'create_grammar_analyzer',
    'create_emotion_detector',
    'create_pronunciation_scorer',
    # Audio
    'create_recorder',
    'create_vad',
    # Persistence
    'create_progress_tracker',
    'create_speaking_repository',
    # Orchestrator
    'IntegratedSpeakingMockTest',
    # API
    'create_speaking_api',
    'IELTSSpeakingAPI',
    'IELTSSpeakingAI',
]

logger.info(
    "[IELTS Speaking Module] All exports loaded "
    "(API updated with subscription manager)"
)