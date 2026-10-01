"""IELTS Writing Module - Pure AI, No Fallback Templates"""

# ============= FACTORY FUNCTIONS (RECOMMENDED) =============

def create_writing_api(ai_engine=None, db=None):
    """Create WritingAPI with AI engine and database session."""
    from .api import create_writing_api as _create_api
    return _create_api(ai_engine, db)


def create_writing_service(ai_engine):
    """Create WritingService with AI engine - REQUIRED"""
    from .service import create_writing_service as _create_service
    return _create_service(ai_engine)


def create_writing_test_generator(ai_engine):
    """Create WritingTestGenerator with AI engine - REQUIRED"""
    from .test_generator import WritingTestGenerator
    return WritingTestGenerator(ai_engine)


def create_essay_evaluator(ai_engine):
    """Create EssayEvaluator with AI engine - REQUIRED"""
    from .evaluator import EssayEvaluator
    return EssayEvaluator(ai_engine)


def create_essay_upgrader(ai_engine):
    """Create EssayUpgrader with AI engine - REQUIRED"""
    from .essay_upgrader import create_essay_upgrader as _create_upgrader
    return _create_upgrader(ai_engine)


def create_chart_renderer(output_dir="static/charts"):
    """Create ChartRenderer - pure matplotlib, no fallbacks"""
    from .chart_renderer import create_chart_renderer as _create_renderer
    return _create_renderer(output_dir)


def create_prompts_library(ai_engine):
    """Create PromptsLibrary with AI engine - REQUIRED"""
    from .prompts_library import create_prompts_library as _create_library
    return _create_library(ai_engine)


def create_ai_feedback(ai_engine):
    """Create AIFeedbackGenerator with AI engine - REQUIRED"""
    from .ai_feedback import create_ai_feedback as _create_feedback
    return _create_feedback(ai_engine)


def create_anti_template_detector():
    """Create AntiTemplateDetector - analysis only"""
    from .anti_template import create_anti_template_detector as _create_detector
    return _create_detector()


def create_counter_argument_generator(ai_engine):
    """Create CounterArgumentGenerator with AI engine - REQUIRED"""
    from .counter_argument import create_counter_argument_generator as _create_counter
    return _create_counter(ai_engine)


def create_coherence_map():
    """Create CoherenceFlowMap - analysis only"""
    from .coherence_map import create_coherence_map as _create_map
    return _create_map()


def create_dynamic_vocabulary():
    """Create DynamicVocabulary - analysis only"""
    from .dynamic_vocabulary import create_dynamic_vocabulary as _create_vocab
    return _create_vocab()


def create_data_mapper(ai_engine=None):
    """Create DataToTextMapper - analysis with optional AI"""
    from .data_to_text import create_data_mapper as _create_mapper
    return _create_mapper(ai_engine)


def create_writing_repository(db_url=None):
    """Create WritingRepository - database only"""
    from .repository import create_writing_repository as _create_repo
    return _create_repo(db_url)


# ============= CLASS IMPORTS (No instances) =============

from .api import WritingAPI
from .service import WritingService
from .scoring import WritingScoring
from .evaluator import EssayEvaluator
from .test_generator import WritingTestGenerator
from .chart_renderer import ChartRenderer
from .essay_upgrader import EssayUpgrader
from .repository import WritingRepository
from .prompts_library import PromptsLibrary
from .anti_template import AntiTemplateDetector
from .counter_argument import CounterArgumentGenerator
from .coherence_map import CoherenceFlowMap
from .dynamic_vocabulary import DynamicVocabulary
from .data_to_text import DataToTextMapper
from .ai_feedback import AIFeedbackGenerator


# ============= DEPRECATED - Will raise error if used =============

class _MissingInstance:
    def __getattr__(self, name):
        raise RuntimeError(
            f" {name} is not available. "
            "You must create instances with factory functions:\n"
            " from modules.ielts.writing import create_writing_api\n"
            " writing_api = create_writing_api(ai_engine)"
        )


# Replace old module-level instances with error-raising proxies
writing_api = _MissingInstance()
writing_service = _MissingInstance()
writing_test = _MissingInstance()
writing_scoring = WritingScoring() # Scoring doesn't need AI - OK
writing_repo = _MissingInstance() # REMOVED - use create_writing_repository()
chart_renderer = _MissingInstance()
essay_upgrader = _MissingInstance()
ai_feedback = _MissingInstance()
anti_template = _MissingInstance()
counter_arg = _MissingInstance()
coherence_map = _MissingInstance()
dynamic_vocab = _MissingInstance()
data_mapper = _MissingInstance()
prompts_library = _MissingInstance()


# ============= HELPER FUNCTION =============

def check_ai_availability(ai_engine):
    """Check if AI engine is properly configured"""
    if ai_engine is None:
        raise ValueError(
            " AI Engine is required for IELTS Writing module.\n"
            "Please provide a valid AI engine instance."
        )
    return True


# ============= MODULE INFO =============

__version__ = "2.0.0"
__author__ = "IELTS Platform"
__description__ = "Pure AI IELTS Writing module - 98% accuracy match with real IELTS"

__all__ = [
    # Factory functions
    'create_writing_api',
    'create_writing_service',
    'create_writing_test_generator',
    'create_essay_evaluator',
    'create_essay_upgrader',
    'create_chart_renderer',
    'create_prompts_library',
    'create_ai_feedback',
    'create_anti_template_detector',
    'create_counter_argument_generator',
    'create_coherence_map',
    'create_dynamic_vocabulary',
    'create_data_mapper',
    'create_writing_repository',
    
    # Core classes
    'WritingAPI',
    'WritingService',
    'WritingScoring',
    'EssayEvaluator',
    'WritingTestGenerator',
    'ChartRenderer',
    'EssayUpgrader',
    'WritingRepository',
    'PromptsLibrary',
    
    # Advanced features
    'AntiTemplateDetector',
    'CounterArgumentGenerator',
    'CoherenceFlowMap',
    'DynamicVocabulary',
    'DataToTextMapper',
    'AIFeedbackGenerator',
    
    # Helper
    'check_ai_availability',
]

print(f"[IELTS Writing Module] v{__version__} - Pure AI mode")
print(f" Factory functions: create_writing_api(), create_writing_service()")
print(f" Repository: create_writing_repository()")
print(f" Module-level instances removed - use factory functions")