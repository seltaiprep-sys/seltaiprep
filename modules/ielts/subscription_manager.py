"""IELTS Subscription Manager - Handles free limits and subscriptions

FIXES APPLIED:
1. Uses `flag_modified()` when assigning to `subscription.free_usage` so
   SQLAlchemy actually persists JSON column changes.
2. Uses the LIVE `db.session` (not a stale reference captured at startup).
3. Re-fetches the subscription in the current session to avoid detached-object
   issues when called from background threads or after long request chains.
4. Ensures the default subscription is flushed before modification.
5. NEW: `_get_or_create_subscription` normalizes free-tier rows so they can
   never be treated as paid. If `plan` is empty/'free'/'trial', the row is
   forced to status='inactive', tests_remaining=0, and free_usage is
   initialized to zeros. This kills the bug where a default subscription
   was created with status='active' and the submit code decremented
   `tests_remaining` instead of `free_usage`.
6. NEW: `can_access_test` and `get_status` now require a real paid plan
   (plan not in {'', 'free', 'trial'}) before treating a user as subscribed.
"""

import logging
import json
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm.attributes import flag_modified

from models import get_subscription_model, get_user_model

logger = logging.getLogger(__name__)

# Plans that are NOT considered paid subscriptions.
_FREE_PLAN_NAMES = ('', 'free', 'trial', 'none', 'default')


class IELTSSubscriptionManager:
    """Manages IELTS free test limits and subscriptions"""

    # Free test limits per module (2 tests each)
    DEFAULT_FREE_LIMITS = {
        'listening': 2,
        'reading': 2,
        'writing': 2,
        'speaking': 2
    }

    def __init__(self, db_session=None):
        # NOTE: db_session is accepted for backward-compatibility but not stored.
        # We always use the live `db.session` via the `db` property below.
        self.module = 'ielts'
        self.Subscription = get_subscription_model(self.module)
        self.User = get_user_model(self.module)
        logger.info(" IELTSSubscriptionManager initialized (dynamic session mode)")

    # ============================================================
    # Dynamic session property – always returns the LIVE session
    # ============================================================
    @property
    def db(self):
        """
        Always return the CURRENT request's db.session.
        This avoids the stale-session bug where commits were going to a
        session that was created at app startup.
        """
        from models import db as _db
        return _db.session

    # ============================================================
    # INTERNAL HELPERS
    # ============================================================

    @staticmethod
    def _is_paid_plan(plan) -> bool:
        """Return True only if `plan` looks like a real paid plan."""
        return (plan or '').strip().lower() not in _FREE_PLAN_NAMES

    def _normalize_free_tier_row(self, subscription):
        """
         Defensive: if a subscription row is clearly a free-tier default
        (plan is '', 'free', or 'trial'), force it to look inactive and
        unpaid. This prevents the paid branch in submit handlers from
        running for free users.

        Never touches rows with a real paid plan.
        """
        try:
            plan_lower = (subscription.plan or '').strip().lower()
            if plan_lower not in _FREE_PLAN_NAMES:
                return # Real paid plan — do not touch

            changed = False

            if subscription.status == 'active':
                subscription.status = 'inactive'
                changed = True

            if subscription.subscription_end is not None:
                # A free-tier row should have no active window.
                subscription.subscription_end = None
                changed = True

            if (subscription.tests_remaining or 0) > 0:
                subscription.tests_remaining = 0
                changed = True

            if subscription.free_usage is None:
                subscription.free_usage = {k: 0 for k in self.DEFAULT_FREE_LIMITS}
                flag_modified(subscription, "free_usage")
                changed = True

            if changed:
                logger.info(
                    f" Normalized free-tier subscription row for user "
                    f"{subscription.user_id} (plan={plan_lower!r}, "
                    f"status={subscription.status!r}, "
                    f"tests_remaining={subscription.tests_remaining})"
                )
        except Exception as e:
            logger.warning(f"normalize_free_tier_row failed: {e}")

    def _get_or_create_subscription(self, user_id: int):
        """
        Get the subscription for a user in the CURRENT session.
        Creates one if it doesn't exist, then normalizes free-tier rows.
        """
        session = self.db
        subscription = session.query(self.Subscription).filter_by(user_id=user_id).first()
        if not subscription:
            from models import create_default_subscription_for_user
            subscription = create_default_subscription_for_user(user_id, self.module)
            session.add(subscription)
            try:
                session.flush()
            except Exception as e:
                logger.warning(f"flush() after create_default_subscription failed: {e}")
            logger.info(f" Created default subscription for user {user_id}")

        # Always normalize — covers both freshly-created rows and
        # stale rows created before this fix existed.
        self._normalize_free_tier_row(subscription)

        return subscription

    def _normalise_usage(self, raw_usage) -> Dict:
        """Return the free_usage as a plain dict, no matter how it's stored."""
        usage = raw_usage or {}
        if isinstance(usage, str):
            try:
                usage = json.loads(usage)
            except Exception:
                usage = {}
        if not isinstance(usage, dict):
            usage = {}
        return usage

    def _get_aware_end(self, subscription):
        """Convert subscription_end to timezone-aware datetime if naive"""
        end = subscription.subscription_end
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end

    def _has_paid_subscription(self, subscription) -> bool:
        """
        Return True only when the subscription is a *real* paid subscription:
        - status == 'active'
        - plan is not free/trial/empty
        - subscription_end is in the future (if set)
        - tests_remaining > 0
        """
        if subscription is None:
            return False
        if not self._is_paid_plan(subscription.plan):
            return False
        if subscription.status != 'active':
            return False
        end = self._get_aware_end(subscription)
        if end is not None and end <= datetime.now(timezone.utc):
            return False
        if (subscription.tests_remaining or 0) <= 0:
            return False
        return True

    # ============================================================
    # PUBLIC API
    # ============================================================

    def get_free_usage(self, user_id: int, test_type: str) -> int:
        """Get free usage count for a specific test type."""
        subscription = self._get_or_create_subscription(user_id)
        usage = self._normalise_usage(subscription.free_usage)
        return usage.get(test_type, 0)

    def increment_free_usage(self, user_id: int, test_type: str) -> bool:
        """
        Increment free usage for a test type.

         CRITICAL FIXES:
        - Re-fetches the subscription in the live session.
        - Uses flag_modified() so SQLAlchemy actually persists JSON changes.
        - Commits on the live session.
        """
        try:
            session = self.db
            subscription = self._get_or_create_subscription(user_id)

            usage = self._normalise_usage(subscription.free_usage)

            old_count = usage.get(test_type, 0)
            usage[test_type] = old_count + 1

            # Assign AND explicitly flag the JSON field as modified.
            # Without flag_modified, SQLAlchemy will NOT emit an UPDATE.
            subscription.free_usage = usage
            flag_modified(subscription, "free_usage")

            session.commit()

            logger.info(
                f" User {user_id} used free test: {test_type} "
                f"(was {old_count}, now {usage[test_type]})"
            )
            return True

        except Exception as e:
            logger.error(
                f" increment_free_usage failed for user {user_id}, test {test_type}: {e}",
                exc_info=True,
            )
            try:
                self.db.rollback()
            except Exception:
                pass
            return False

    def get_free_remaining(self, user_id: int, test_type: str) -> int:
        """Get remaining free tests for a specific test type."""
        used = self.get_free_usage(user_id, test_type)
        limit = self.DEFAULT_FREE_LIMITS.get(test_type, 2)
        return max(0, limit - used)

    def reset_free_usage(self, user_id: int, test_type: str = None) -> bool:
        """
        Reset free usage for a user (debugging/admin).
        If test_type is None, resets ALL modules.
        """
        session = self.db
        subscription = self._get_or_create_subscription(user_id)

        if test_type:
            usage = self._normalise_usage(subscription.free_usage)
            usage[test_type] = 0
            subscription.free_usage = usage
            logger.info(f" Reset free usage for user {user_id}, module {test_type}")
        else:
            subscription.free_usage = {}
            logger.info(f" Reset ALL free usage for user {user_id}")

        flag_modified(subscription, "free_usage")
        session.commit()
        return True

    # ============================================================
    # ACCESS CONTROL
    # ============================================================
    def can_access_test(self, user_id: int, test_type: str) -> tuple:
        """
        Check if user can access a test.

        Rules:
        - Free limit (2 per module) is ALWAYS enforced first.
        - If free limit is exhausted but user has an *active paid* subscription
          with tests remaining, allow access (uses subscription test).
        - Otherwise, block.

        Returns: (allowed: bool, error_message: str, requires_subscription: bool)
        """
        subscription = self._get_or_create_subscription(user_id)

        # ─── 1. Always check free usage first ─────────────
        used = self.get_free_usage(user_id, test_type)
        limit = self.DEFAULT_FREE_LIMITS.get(test_type, 2)

        if used < limit:
            remaining = limit - used
            logger.debug(
                f"User {user_id} can access {test_type} "
                f"(free: {used}/{limit}, remaining: {remaining})"
            )
            return True, None, True

        # ─── 2. Free limit exhausted – check PAID subscription only ──
        # Uses the strict paid check (plan must be a real plan).
        if self._has_paid_subscription(subscription):
            logger.info(
                f"User {user_id} accessing {test_type} via subscription "
                f"(free limit {limit}/{limit} exhausted, "
                f"{subscription.tests_remaining} tests left)"
            )
            return True, None, False

        # ─── 3. No free tests and no paid subscription tests ──
        logger.info(
            f"User {user_id} blocked from {test_type}: free limit reached "
            f"and no paid subscription tests"
        )
        return (
            False,
            f'Free limit reached ({limit}) for {test_type}. '
            f'Please subscribe or renew your plan.',
            True,
        )

    # ============================================================
    # STATUS / DASHBOARD
    # ============================================================
    def get_status(self, user_id: int) -> Dict:
        """Get user's subscription and free usage status (for the dashboard)."""
        session = self.db

        # Force a fresh read so dashboard always reflects recent increments
        try:
            session.expire_all()
        except Exception:
            pass

        subscription = self._get_or_create_subscription(user_id)

        # Strict paid check — a free-tier row will never appear as subscribed.
        has_subscription = self._has_paid_subscription(subscription)

        usage = self._normalise_usage(subscription.free_usage)

        # Build per-module status
        module_status = {}
        for mod, limit in self.DEFAULT_FREE_LIMITS.items():
            used = usage.get(mod, 0)
            module_status[mod] = {
                'free_used': used,
                'free_limit': limit,
                'free_remaining': max(0, limit - used),
                'requires_subscription': used >= limit and not has_subscription,
                'has_subscription': has_subscription,
            }

        return {
            'has_subscription': has_subscription,
            'plan': subscription.plan if has_subscription else None,
            'tests_remaining': subscription.tests_remaining if has_subscription else 0,
            'tests_taken': subscription.tests_taken or 0,
            'free_usage': usage,
            'free_limits': self.DEFAULT_FREE_LIMITS,
            'modules': module_status,
        }

    # ============================================================
    # SUBSCRIPTION ACTIVATION
    # ============================================================
    def activate_subscription(self, user_id: int, plan_key: str) -> Dict:
        """Activate a subscription plan for a user."""
        from app import PLAN_CONFIG, PRICE_CONFIG, get_subscription_end

        plan = PLAN_CONFIG.get(plan_key)
        if not plan:
            return {'success': False, 'error': 'Invalid plan'}

        if not self._is_paid_plan(plan_key):
            return {'success': False, 'error': 'Cannot activate a free plan'}

        session = self.db
        subscription = self._get_or_create_subscription(user_id)

        subscription.plan = plan_key
        subscription.status = 'active'
        subscription.tests_remaining = plan['tests']
        subscription.tests_taken = 0
        subscription.subscription_start = datetime.now(timezone.utc)
        subscription.subscription_end = get_subscription_end(plan['days'])
        subscription.amount_paid_npr = PRICE_CONFIG.get(self.module, {}).get(plan_key, 0)

        session.commit()
        logger.info(f" User {user_id} activated subscription: {plan_key}")
        return {'success': True, 'tests_remaining': subscription.tests_remaining}


# ─── Singleton instance ──────────────────────────────────
_ielts_subscription_manager = None


def get_ielts_subscription_manager(db_session=None):
    """
    Return the singleton manager.

    The `db_session` argument is accepted for backward compatibility but
    ignored – the manager always uses the live db.session internally via
    the `db` property.
    """
    global _ielts_subscription_manager
    if _ielts_subscription_manager is None:
        _ielts_subscription_manager = IELTSSubscriptionManager(db_session)
    return _ielts_subscription_manager