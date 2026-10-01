# modules/pte/__init__.py
"""PTE Module – API, Generators, Managers, Utils, Models.

v5.0 — POOL-PRIMARY:
  • PTETestBankManager is DEPRECATED (removed from public API)
  • PTETestPoolManager is now the primary shared-pool manager
"""

from .api import pte_blueprint, init_pte_api, create_pte_api
from .generators import PTEReading, PTEListening, PTESpeakingWriting

# ─── Managers ─────────────────────────────────────────────────────────
# PTETestBankManager / pte_test_bank_manager removed in v5.0.
# Use pte_test_pool_manager for all generation + serving.
from .managers import PTESubscriptionManager, pte_subscription_manager
from .managers import PTETestPoolManager, pte_test_pool_manager

# ─── Utils ────────────────────────────────────────────────────────────
from .utils import (
    PTEAIGenerator,
    pte_ai_generator,
    PTEImageGenerator,
    pte_image_generator,
    MATPLOTLIB_AVAILABLE,
    PTEScoring,
    pte_scoring,
)

# ─── Service ──────────────────────────────────────────────────────────
from .service import PTEService, create_pte_service

# ─── Models ───────────────────────────────────────────────────────────
from .models import (
    db,
    PTETestSession,
    PTETestResult,
    PTESubscription,
    PTESetting,
    # v5.0 pool models
    PTETestPool,
    PTEGenerationLock,
    PTEUserPoolProgress,
)

__all__ = [
    # API
    'pte_blueprint',
    'init_pte_api',
    'create_pte_api',

    # Generators
    'PTEReading',
    'PTEListening',
    'PTESpeakingWriting',

    # Managers
    'PTESubscriptionManager',
    'pte_subscription_manager',
    'PTETestPoolManager',
    'pte_test_pool_manager',

    # Utils
    'PTEAIGenerator',
    'pte_ai_generator',
    'PTEImageGenerator',
    'pte_image_generator',
    'MATPLOTLIB_AVAILABLE',
    'PTEScoring',
    'pte_scoring',

    # Service
    'PTEService',
    'create_pte_service',

    # Models
    'db',
    'PTETestSession',
    'PTETestResult',
    'PTESubscription',
    'PTESetting',
    'PTETestPool',
    'PTEGenerationLock',
    'PTEUserPoolProgress',
]