# modules/pte/managers/test_bank_manager.py
"""
DEPRECATED (v5.0) — PTE Test Bank Manager.

 This module is kept ONLY as a compatibility shim.
   All user-facing and admin-facing generation now flows through
   `pte_test_pool_manager` (see managers/test_pool_manager.py).

Migration map:
    OLD →  NEW
    ─────────────────────────────────────────────────────────────────
    pte_test_bank_manager.get_test(...) →  pte_test_pool_manager.get_or_generate(...)
    pte_test_bank_manager._generate_test(...) →  generator.generate_full_test(...)
    pte_test_bank_manager._save_to_database(…) → pte_test_pool_manager._save_to_pool(...)
    pte_test_bank_manager.get_bank_stats() →  pte_test_pool_manager.get_pool_stats()

What this stub does:
  • Keeps `PTETestBankManager` and `pte_test_bank_manager` importable
    so existing code doesn't crash with ImportError.
  • Delegates read-only methods (get_bank_stats) to the pool.
  • Raises `NotImplementedError` on write methods (get_test, _generate_test,
    _save_to_database) — callers should migrate immediately.
  • Emits a `DeprecationWarning` on instantiation and on method calls.

Remove this file (and its import in managers/__init__.py) once all
references have been migrated.
"""

import logging
import warnings
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_DEPRECATION_MSG = (
    "PTETestBankManager is DEPRECATED (v5.0). "
    "Use pte_test_pool_manager instead. "
    "See modules/pte/managers/test_pool_manager.py"
)


class PTETestBankManager:
    """DEPRECATED. Compatibility shim only — do not use in new code."""

    MAX_TESTS_PER_TYPE = 0 # No longer applicable
    REFRESH_AFTER_USES = 999 # No longer applicable

    def __init__(self):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        logger.warning(
            " PTETestBankManager instantiated — this class is deprecated. "
            "Use pte_test_pool_manager."
        )

    # ═══════════════════════════════════════════════════════════════════
    # DEPRECATED — write methods (raise on call)
    # ═══════════════════════════════════════════════════════════════════
    def get_test(
        self,
        test_type: str,
        difficulty: str = "medium",
        topic: str = None,
    ) -> Dict[str, Any]:
        """DEPRECATED → use pte_test_pool_manager.get_or_generate()."""
        warnings.warn(
            "PTETestBankManager.get_test() is deprecated. "
            "Use pte_test_pool_manager.get_or_generate().",
            DeprecationWarning,
            stacklevel=2,
        )
        raise NotImplementedError(
            "PTETestBankManager.get_test() has been removed. "
            "Use pte_test_pool_manager.get_or_generate("
            "module=..., difficulty=..., user_id=..., generate_fn=...)."
        )

    def _generate_test(
        self,
        test_type: str,
        difficulty: str,
        topic: str,
    ) -> Optional[Dict]:
        """DEPRECATED → call the generator directly."""
        warnings.warn(
            "PTETestBankManager._generate_test() is deprecated. "
            "Call the generator (PTEReading / PTEListening / PTESpeakingWriting) directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        raise NotImplementedError(
            "PTETestBankManager._generate_test() has been removed. "
            "Import PTEReading / PTEListening / PTESpeakingWriting and call "
            "their generate_full_test() methods."
        )

    def _save_to_database(
        self,
        test_type: str,
        difficulty: str,
        topic: str,
        test_data: Dict,
    ) -> None:
        """DEPRECATED → use pte_test_pool_manager._save_to_pool()."""
        warnings.warn(
            "PTETestBankManager._save_to_database() is deprecated. "
            "Use pte_test_pool_manager._save_to_pool().",
            DeprecationWarning,
            stacklevel=2,
        )
        raise NotImplementedError(
            "PTETestBankManager._save_to_database() has been removed. "
            "Use pte_test_pool_manager._save_to_pool("
            "module=..., difficulty=..., test_data=..., user_id=...)."
        )

    def _get_from_database(
        self,
        test_type: str,
        difficulty: str,
        topic: str,
        mark_usage: bool = True,
    ) -> Optional[Dict]:
        """DEPRECATED → pool serving handles this automatically."""
        warnings.warn(
            "PTETestBankManager._get_from_database() is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )
        raise NotImplementedError(
            "PTETestBankManager._get_from_database() has been removed. "
            "Use pte_test_pool_manager.get_or_generate()."
        )

    # ═══════════════════════════════════════════════════════════════════
    # READ-ONLY — safe to call, delegates to pool
    # ═══════════════════════════════════════════════════════════════════
    def get_bank_stats(self) -> Dict[str, Any]:
        """
        DEPRECATED. Returns pool stats instead of bank stats.
        Kept callable so admin dashboards don't 500 during rollout.
        """
        warnings.warn(
            "PTETestBankManager.get_bank_stats() is deprecated. "
            "Use pte_test_pool_manager.get_pool_stats().",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            " get_bank_stats() called — returning POOL stats instead."
        )
        try:
            from .test_pool_manager import pte_test_pool_manager

            pool_stats = pte_test_pool_manager.get_pool_stats()
            total = sum(s.get('size', 0) for s in pool_stats.values())

            # Reshape for legacy consumers that expect {total_tests, by_type, ...}
            by_type = {mod: s.get('size', 0) for mod, s in pool_stats.items()}

            return {
                'total_tests': total,
                'by_type': by_type,
                'by_difficulty': {}, # not tracked by pool
                'max_tests': 100, # MAX_POOL_SIZE
                'refresh_after': 999,
                'source': 'pool', # ← signal that this is pool data
                'deprecated': True,
            }
        except Exception as e:
            logger.error(f"get_bank_stats() shim failed: {e}", exc_info=True)
            return {
                'total_tests': 0,
                'by_type': {},
                'by_difficulty': {},
                'max_tests': 100,
                'refresh_after': 999,
                'source': 'pool',
                'deprecated': True,
                'error': str(e),
            }

    def cleanup_old_tests(self, days: int = 30) -> int:
        """
        DEPRECATED. No-op — pool items are managed by pte_test_pool_manager.reset_*().
        """
        warnings.warn(
            "PTETestBankManager.cleanup_old_tests() is deprecated. "
            "Pool items are managed by pte_test_pool_manager.reset_pool().",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.info(
            " cleanup_old_tests() called on deprecated shim — no-op. "
            "Use pte_test_pool_manager.reset_pool(module=...) instead."
        )
        return 0


# ─────────────────────────────────────────────────────────────────────
# Singleton — kept so `from .managers import pte_test_bank_manager` works
# ─────────────────────────────────────────────────────────────────────
_logger_done = False


def _make_singleton():
    global _logger_done
    if not _logger_done:
        logger.warning(
            " pte_test_bank_manager imported — DEPRECATED (v5.0). "
            "Migrate to pte_test_pool_manager."
        )
        _logger_done = True
    return PTETestBankManager()


pte_test_bank_manager = _make_singleton()