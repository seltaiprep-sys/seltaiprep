# modules/pte/managers/__init__.py
"""PTE Managers — Subscription & Test Pool Management.

v5.1 — DYNAMIC POOL:
  • PTETestPoolManager supports dynamic cap growth (initial → +step → max)
  • PTETestBankManager is DEPRECATED (replaced by PTETestPoolManager)
  • Kept as a lazy shim so old imports don't crash during rollout
  • Remove the shim once all references are migrated
"""

from .subscription_manager import PTESubscriptionManager, pte_subscription_manager
from .test_pool_manager import (
    PTETestPoolManager,
    pte_test_pool_manager,
    MODULE_POOL_CONFIG,
    KNOWN_MODULES,
    RETRY_AFTER_SECONDS,
    MAX_POOL_SIZE,
)


# ═══════════════════════════════════════════════════════════════════════
# DEPRECATED SHIM — remove once migration is complete
# Kept so that `from modules.pte.managers import pte_test_bank_manager`
# doesn't raise ImportError during the transition.
# ═══════════════════════════════════════════════════════════════════════
def __getattr__(name):
    """
    PEP 562 module-level __getattr__.

    Only triggers for attributes not found above.
    Provides a deprecation warning for the old test-bank API.
    """
    if name in ('PTETestBankManager', 'pte_test_bank_manager'):
        import warnings
        warnings.warn(
            f"'{name}' is DEPRECATED (v5.0). "
            f"Use 'pte_test_pool_manager' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Lazy-import the stub (which itself will warn) so app doesn't crash
        try:
            from .test_bank_manager import PTETestBankManager, pte_test_bank_manager
            if name == 'PTETestBankManager':
                return PTETestBankManager
            return pte_test_bank_manager
        except ImportError:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r} "
                f"(test_bank_manager has been removed)"
            )

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Subscription
    'PTESubscriptionManager',
    'pte_subscription_manager',

    # Test Pool (primary)
    'PTETestPoolManager',
    'pte_test_pool_manager',
    'MODULE_POOL_CONFIG',
    'KNOWN_MODULES',
    'RETRY_AFTER_SECONDS',
    'MAX_POOL_SIZE',

    # Deprecated (via __getattr__ shim)
    # 'PTETestBankManager',
    # 'pte_test_bank_manager',
]