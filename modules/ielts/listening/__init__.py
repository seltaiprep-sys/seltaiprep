"""IELTS Listening Module

Exposes all public components for the IELTS Listening test generation system.
Now supports partial generation: Section 1 generated immediately, others in background.
"""

from .api import (
    ListeningAPI,
    create_listening_api,
    rate_limit
)

# Also expose other components if needed
from .service import ListeningService
from .repository import ListeningRepository
from .audio_generator import audio_generator
from .accent_mixer import accent_mixer # This should now work
from .timing_config import timing_config
from .topics import topic_registry

# New: expose the section generator for direct use
from .section_generator import SectionGenerator

__all__ = [
    'ListeningAPI',
    'create_listening_api',
    'rate_limit',
    'ListeningService',
    'ListeningRepository',
    'audio_generator',
    'accent_mixer',
    'timing_config',
    'topic_registry',
    'SectionGenerator', # Added for flexibility
]