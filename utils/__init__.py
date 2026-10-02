"""
Utils package
=============
"""

import logging
logger = logging.getLogger(__name__)

try:
    from .audio_utils import AudioUtils
    AUDIO_UTILS_AVAILABLE = True
except ImportError:
    AudioUtils = None
    AUDIO_UTILS_AVAILABLE = False

try:
    from .bill_generator import generate_bill_pdf
    BILL_GENERATOR_AVAILABLE = True
except ImportError:
    generate_bill_pdf = None
    BILL_GENERATOR_AVAILABLE = False

__all__ = ["AudioUtils", "generate_bill_pdf"]
