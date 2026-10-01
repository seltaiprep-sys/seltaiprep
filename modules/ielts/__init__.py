"""
IELTS module - contains all IELTS-specific functionality.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# SUBSCRIPTION MANAGER
# ============================================================
try:
    from .subscription_manager import get_ielts_subscription_manager, IELTSSubscriptionManager
except ImportError as e:
    logger.warning(f"Subscription manager not available: {e}")
    get_ielts_subscription_manager = None
    IELTSSubscriptionManager = None

# ============================================================
# LISTENING
# ============================================================
try:
    from .listening import create_listening_api, ListeningAPI
except ImportError as e:
    logger.warning(f"Listening API not available: {e}")
    create_listening_api = None
    ListeningAPI = None

# ============================================================
# READING
# ============================================================
try:
    from .reading.api import create_reading_api
except ImportError as e:
    logger.warning(f"Reading API not available: {e}")
    create_reading_api = None

# ============================================================
# WRITING
# ============================================================
try:
    from .writing import create_writing_api, WritingAPI
except ImportError as e:
    logger.warning(f"Writing API not available: {e}")
    create_writing_api = None
    WritingAPI = None

# ============================================================
# SPEAKING
# ============================================================
try:
    from .speaking.api import create_speaking_api, IELTSSpeakingAPI
except ImportError as e:
    logger.warning(f"Speaking API not available: {e}")
    create_speaking_api = None
    IELTSSpeakingAPI = None

# ============================================================
# LISTENING UTILITIES
# ============================================================
try:
    from .listening.accent_mixer import accent_mixer
except ImportError as e:
    logger.warning(f"accent_mixer not available: {e}")
    accent_mixer = None

# ============================================================
# EXPORTS
# ============================================================
__all__ = [
    'get_ielts_subscription_manager',
    'IELTSSubscriptionManager',
    'create_listening_api',
    'ListeningAPI',
    'create_reading_api',
    'create_writing_api',
    'WritingAPI',
    'create_speaking_api',
    'IELTSSpeakingAPI',
    'accent_mixer'
]