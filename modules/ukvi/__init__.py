# modules/ukvi/__init__.py
"""UKVI Module – Initialisation"""

from .api import init_ukvi_api, get_blueprint
from .service import UKVIService
from .managers import UKVISubscriptionManager, UKVITestManager

# Note: models are auto-registered via import of .models in api.py/service.py
# No need to export init_ukvi_models

__all__ = [
    'init_ukvi_api',
    'get_blueprint',
    'UKVIService',
    'UKVISubscriptionManager',
    'UKVITestManager',
]