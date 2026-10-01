# modules/pte/managers/subscription_manager.py
"""PTE Subscription Manager - Handles subscription status, free limits, and activation"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
import json
from sqlalchemy.orm.attributes import flag_modified

from ..models import PTESubscription, db, create_default_pte_subscription, PTESetting

logger = logging.getLogger(__name__)


# ─── CONSTANTS ─────────────────────────────────────────────────────────
# Plan keys जसलाई "unlimited tests" मानिन्छ (999 वा माथिको tests)
UNLIMITED_PLAN_KEYS = {'45days', 'annual'}
UNLIMITED_TEST_THRESHOLD = 999


class PTESubscriptionManager:
    """Manages PTE subscriptions, free test usage, and plan activation."""

    DEFAULT_FREE_LIMITS = {
        'pte_reading': 2,
        'pte_listening': 2,
        'pte_speaking_writing': 2
    }

    def __init__(self):
        self._increment_cache = {}
        self._cache_ttl_seconds = 300
        logger.info("PTE Subscription Manager initialized")

    # ═══════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════
    def _get_free_limit(self, test_type: str) -> int:
        """Get free test limit for a module — DB-backed with default fallback."""
        try:
            stored = PTESetting.get_value(f'pte_free_limit_{test_type}', None)
            if stored is not None:
                return int(stored)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid stored free limit for {test_type}: {e}")
        return self.DEFAULT_FREE_LIMITS.get(test_type, 2)

    def _get_all_free_limits(self) -> Dict[str, int]:
        """Get DB-backed free limits for all modules."""
        return {
            mod: self._get_free_limit(mod)
            for mod in self.DEFAULT_FREE_LIMITS.keys()
        }

    def _get_free_usage(self, user_id: int, test_type: str) -> int:
        """Read the per-module free usage count for a user."""
        sub = PTESubscription.query.filter_by(user_id=user_id).first()
        if not sub:
            return 0
        usage = sub.free_usage or {}
        if isinstance(usage, str):
            try:
                usage = json.loads(usage)
            except Exception:
                usage = {}
        if not isinstance(usage, dict):
            usage = {}
        return int(usage.get(test_type, 0) or 0)

    def _cleanup_cache(self):
        """Remove stale entries from the idempotency cache."""
        now_ts = datetime.now(timezone.utc).timestamp()
        self._increment_cache = {
            k: v for k, v in self._increment_cache.items()
            if now_ts - v < self._cache_ttl_seconds
        }

    def _is_unlimited(self, plan_key: Optional[str], tests_remaining: int) -> bool:
        """Return True if the plan/subscription allows unlimited tests."""
        if plan_key in UNLIMITED_PLAN_KEYS:
            return True
        if (tests_remaining or 0) >= UNLIMITED_TEST_THRESHOLD:
            return True
        return False

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: STATUS
    # ═══════════════════════════════════════════════════════════════════
    def get_subscription_status(self, user_id: int) -> Dict[str, Any]:
        """Return full subscription status for a user."""
        sub = PTESubscription.query.filter_by(user_id=user_id).first()
        if not sub:
            sub = create_default_pte_subscription(user_id)

        now = datetime.now(timezone.utc)
        end = sub.subscription_end
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        has_sub = sub.status == 'active' and end and end > now

        # Normalize free_usage to dict
        usage = sub.free_usage or {}
        if isinstance(usage, str):
            try:
                usage = json.loads(usage)
            except Exception:
                usage = {}
        if not isinstance(usage, dict):
            usage = {}

        # DB-backed per-module limits and per-module breakdown
        free_limits = self._get_all_free_limits()

        per_module = {}
        for mod, limit in free_limits.items():
            used = int(usage.get(mod, 0) or 0)
            per_module[mod] = {
                'used': used,
                'limit': limit,
                'remaining': max(0, limit - used),
                'exceeded': used >= limit,
            }

        # Global free limit = sum of per-module limits
        global_limit = sum(free_limits.values())
        free_tests_used = sub.free_tests_used or 0

        return {
            'has_subscription': has_sub,
            'plan': sub.plan,
            'status': sub.status,
            'free_tests_used': free_tests_used,
            'free_limit': global_limit if not has_sub else 999,
            'free_tests_remaining': (
                max(0, global_limit - free_tests_used) if not has_sub else 999
            ),
            'free_usage': usage,
            'free_limits': free_limits, #  DB-backed, not hardcoded
            'per_module': per_module,
            'tests_remaining': sub.tests_remaining or 0,
            'tests_taken': sub.tests_taken or 0,
            'days_remaining': (end - now).days if end else 0,
            'subscription_start': sub.subscription_start.isoformat() if sub.subscription_start else None,
            'subscription_end': sub.subscription_end.isoformat() if sub.subscription_end else None,
            'amount_paid_npr': sub.amount_paid_npr or 0,
        }

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: ACCESS CHECK
    # ═══════════════════════════════════════════════════════════════════
    def can_access_test(self, user_id: int, test_type: str) -> Tuple[bool, Optional[str], bool]:
        """
        Check whether a user can start a new test of the given type.

        Returns:
            (allowed: bool, error_message: Optional[str], requires_subscription: bool)
        """
        status = self.get_subscription_status(user_id)

        if status.get('has_subscription'):
            tests_remaining = status.get('tests_remaining', 0) or 0
            plan = status.get('plan', '')

            # Unlimited plans always allowed
            if self._is_unlimited(plan, tests_remaining):
                logger.debug(f"User {user_id} has unlimited plan ({plan})")
                return True, None, False

            if tests_remaining > 0:
                logger.debug(
                    f"User {user_id} has subscription ({plan}), "
                    f"{tests_remaining} tests left"
                )
                return True, None, False

            logger.warning(f"User {user_id} subscription active but 0 tests remaining")
            return False, "No tests remaining. Please renew your subscription.", True

        # Free tier
        limit = self._get_free_limit(test_type)
        used = self._get_free_usage(user_id, test_type)
        logger.info(f"Free usage for {test_type} (user {user_id}): {used}/{limit}")

        if used >= limit:
            return False, f"Free limit reached for {test_type} ({limit}). Please subscribe.", True

        return True, None, False

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: USAGE INCREMENT
    # ═══════════════════════════════════════════════════════════════════
    def increment_free_usage(self, user_id: int, test_type: str,
                             session_id: Optional[int] = None) -> bool:
        """
        Increment free test usage — idempotent per session_id.

        Caller MUST pass session_id to make this idempotent.
        Without session_id, double-submit will double-count.
        """
        cache_key = None
        try:
            self._cleanup_cache()

            if session_id:
                cache_key = f"{user_id}:{test_type}:{session_id}"
                if cache_key in self._increment_cache:
                    logger.info(f"Free usage already counted for {cache_key} — skipping")
                    return True

            sub = PTESubscription.query.filter_by(user_id=user_id).first()
            if not sub:
                sub = create_default_pte_subscription(user_id)

            usage = sub.free_usage or {}
            if isinstance(usage, str):
                try:
                    usage = json.loads(usage)
                except Exception:
                    usage = {}
            if not isinstance(usage, dict):
                usage = {}

            # Reassign to trigger MutableDict
            new_usage = dict(usage)
            new_usage[test_type] = int(new_usage.get(test_type, 0) or 0) + 1
            sub.free_usage = new_usage
            flag_modified(sub, 'free_usage')
            sub.free_tests_used = (sub.free_tests_used or 0) + 1

            # Set cache BEFORE commit (so concurrent request is blocked)
            if cache_key:
                self._increment_cache[cache_key] = datetime.now(timezone.utc).timestamp()

            try:
                db.session.commit()
            except Exception:
                if cache_key and cache_key in self._increment_cache:
                    del self._increment_cache[cache_key]
                raise

            logger.info(
                f"Incremented free usage for user {user_id}, {test_type}: "
                f"now {new_usage[test_type]}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to increment free usage: {e}", exc_info=True)
            db.session.rollback()
            if cache_key and cache_key in self._increment_cache:
                del self._increment_cache[cache_key]
            return False

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: SUBSCRIPTION TEST CONSUMPTION
    # ═══════════════════════════════════════════════════════════════════
    def consume_subscription_test(self, user_id: int,
                                  session_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Decrement tests_remaining when a subscriber submits a test.
        Idempotent per session_id.

        Returns:
            {'success': bool, 'tests_remaining': int, 'was_free': bool}
        """
        cache_key = None
        try:
            self._cleanup_cache()

            sub = PTESubscription.query.filter_by(user_id=user_id).first()
            if not sub:
                sub = create_default_pte_subscription(user_id)

            if session_id:
                cache_key = f"consume:{user_id}:{session_id}"
                if cache_key in self._increment_cache:
                    logger.info(f"Subscription already consumed for {cache_key} — skipping")
                    return {
                        'success': True,
                        'tests_remaining': sub.tests_remaining or 0,
                        'was_free': False,
                        'already_consumed': True,
                    }

            if sub.status == 'active' and (sub.tests_remaining or 0) > 0:
                sub.tests_remaining = (sub.tests_remaining or 0) - 1
                sub.tests_taken = (sub.tests_taken or 0) + 1

                if cache_key:
                    self._increment_cache[cache_key] = datetime.now(timezone.utc).timestamp()

                try:
                    db.session.commit()
                except Exception:
                    if cache_key and cache_key in self._increment_cache:
                        del self._increment_cache[cache_key]
                    raise

                logger.info(
                    f"Consumed 1 test for user {user_id}: "
                    f"{sub.tests_remaining} remaining"
                )
                return {
                    'success': True,
                    'tests_remaining': sub.tests_remaining,
                    'was_free': False,
                }

            # Free tier — no-op (increment_free_usage handles it)
            return {
                'success': True,
                'tests_remaining': 0,
                'was_free': True,
            }
        except Exception as e:
            logger.error(f"consume_subscription_test failed: {e}", exc_info=True)
            db.session.rollback()
            if cache_key and cache_key in self._increment_cache:
                del self._increment_cache[cache_key]
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: PLAN CONFIG
    # ═══════════════════════════════════════════════════════════════════
    def _get_plan_config(self, plan_key: str) -> Dict[str, Any]:
        default_config = {
            'free': {'days': 30, 'tests': 3, 'amount': 0},
            '1day': {'days': 1, 'tests': 1, 'amount': 199},
            '7days': {'days': 7, 'tests': 3, 'amount': 499},
            '15days': {'days': 15, 'tests': 5, 'amount': 899},
            '30days': {'days': 30, 'tests': 15, 'amount': 1499},
            '45days': {'days': 45, 'tests': 999, 'amount': 1999},
            'monthly': {'days': 30, 'tests': 15, 'amount': 1499},
            'annual': {'days': 365, 'tests': 999, 'amount': 9999},
        }

        try:
            stored = PTESetting.get_value(f'pte_plan_{plan_key}', None)
            if stored:
                config = json.loads(stored)
                if all(k in config for k in ['days', 'tests', 'amount']):
                    return config
        except Exception as e:
            logger.warning(f"Could not load plan config for {plan_key}: {e}")

        return default_config.get(plan_key, default_config['free'])

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC: ACTIVATION / DEACTIVATION
    # ═══════════════════════════════════════════════════════════════════
    def activate_subscription(self, user_id: int, plan_key: str,
                              days: Optional[int] = None,
                              tests: Optional[int] = None,
                              amount: int = 0,
                              reset_free_usage: bool = False) -> Dict[str, Any]:
        """
        Activate (or renew) a subscription for a user.

        Args:
            user_id: user id
            plan_key: plan identifier (e.g., '30days')
            days: override plan days
            tests: override plan tests
            amount: NPR amount paid
            reset_free_usage: if True, clears per-module free usage counters
                              so the user gets fresh free quota after expiry.
        """
        try:
            sub = PTESubscription.query.filter_by(user_id=user_id).first()
            if not sub:
                sub = create_default_pte_subscription(user_id)

            config = self._get_plan_config(plan_key)

            sub.plan = plan_key
            sub.status = 'active'
            sub.tests_remaining = tests if tests is not None else config['tests']
            sub.tests_taken = 0
            sub.subscription_start = datetime.now(timezone.utc)
            sub.subscription_end = (
                datetime.now(timezone.utc) + timedelta(days=days or config['days'])
            )
            sub.amount_paid_npr = amount or config['amount']

            # Optional: reset free usage counters
            if reset_free_usage:
                sub.free_usage = {}
                flag_modified(sub, 'free_usage')
                sub.free_tests_used = 0

            db.session.commit()

            # Clear cached increment entries for this user
            self._increment_cache = {
                k: v for k, v in self._increment_cache.items()
                if not k.startswith(f"{user_id}:") and
                   not k.startswith(f"consume:{user_id}:")
            }

            logger.info(f"PTE subscription activated for user {user_id}: {plan_key}")
            return {
                'success': True,
                'plan': sub.plan,
                'tests_remaining': sub.tests_remaining,
                'subscription_end': sub.subscription_end.isoformat(),
            }
        except Exception as e:
            logger.error(f"Subscription activation failed for user {user_id}: {e}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def deactivate_subscription(self, user_id: int) -> bool:
        """Mark a user's subscription as inactive."""
        try:
            sub = PTESubscription.query.filter_by(user_id=user_id).first()
            if sub:
                sub.status = 'inactive'
                db.session.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Subscription deactivation failed: {e}")
            db.session.rollback()
            return False

    # ═══════════════════════════════════════════════════════════════════
    # LEGACY / UTILITY
    # ═══════════════════════════════════════════════════════════════════
    def use_test(self, user_id: int) -> bool:
        """Deprecated — use consume_subscription_test() instead."""
        result = self.consume_subscription_test(user_id)
        return result.get('success', False) and not result.get('was_free', True)

    def get_user_test_history(self, user_id: int, limit: int = 10) -> Dict[str, Any]:
        """Return recent test results for a user."""
        try:
            from ..models import PTETestResult
            results = (
                PTETestResult.query
                .filter_by(user_id=user_id)
                .order_by(PTETestResult.created_at.desc())
                .limit(limit)
                .all()
            )
            return {
                'total': PTETestResult.query.filter_by(user_id=user_id).count(),
                'results': [
                    {
                        'id': r.id,
                        'test_type': r.test_type,
                        'score': r.score,
                        'band_score': r.band_score,
                        'created_at': r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in results
                ],
            }
        except Exception as e:
            logger.error(f"Failed to get test history for user {user_id}: {e}")
            return {'total': 0, 'results': [], 'error': str(e)}


# ---------- Singleton ----------
pte_subscription_manager = PTESubscriptionManager()