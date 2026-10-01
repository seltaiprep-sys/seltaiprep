# modules/ukvi/managers/subscription_manager.py
"""UKVI Subscription & free limit management"""

from ast import Dict
import logging
from datetime import datetime, timezone
from ..models import UKVIUser, UKVISubscription

logger = logging.getLogger(__name__)

FREE_LIMIT = 2 # number of free interviews allowed


class UKVISubscriptionManager:
    def __init__(self, db):
        self.db = db

    def _get_or_create_subscription(self, user_id: int) -> UKVISubscription:
        sub = UKVISubscription.query.filter_by(user_id=user_id).first()
        if not sub:
            sub = UKVISubscription(user_id=user_id, status='active')
            self.db.session.add(sub)
            self.db.session.commit()
        return sub

    def can_start_test(self, user_id: int) -> Dict:
        """Check if user can start a new interview (free or paid)."""
        sub = self._get_or_create_subscription(user_id)

        # Check if subscription is active
        if sub.status == 'active' and sub.subscription_end and sub.subscription_end > datetime.now(timezone.utc):
            if sub.tests_remaining > 0 or sub.tests_remaining == -1: # -1 = unlimited
                return {'allowed': True, 'remaining': sub.tests_remaining}

        # Free tier: check usage
        usage = sub.get_free_usage()
        used = usage.get('ukvi_interview', 0)
        if used < FREE_LIMIT:
            return {'allowed': True, 'free_used': used, 'free_limit': FREE_LIMIT}

        return {
            'allowed': False,
            'error': f'Free limit ({FREE_LIMIT}) reached. Please subscribe.',
            'free_used': used,
            'free_limit': FREE_LIMIT,
            'requires_subscription': True
        }

    def increment_usage(self, user_id: int, test_type: str):
        """Increment free usage counter."""
        sub = self._get_or_create_subscription(user_id)
        usage = sub.get_free_usage()
        usage[test_type] = usage.get(test_type, 0) + 1
        sub.set_free_usage(usage)
        self.db.session.commit()

    def get_status(self, user_id: int) -> Dict:
        sub = self._get_or_create_subscription(user_id)
        usage = sub.get_free_usage()
        used = usage.get('ukvi_interview', 0)

        if sub.status == 'active' and sub.subscription_end and sub.subscription_end > datetime.now(timezone.utc):
            return {
                'has_subscription': True,
                'plan': sub.plan,
                'tests_remaining': sub.tests_remaining,
                'tests_taken': sub.tests_taken,
                'days_remaining': (sub.subscription_end - datetime.now(timezone.utc)).days,
                'free_used': used,
                'free_limit': FREE_LIMIT,
            }
        else:
            return {
                'has_subscription': False,
                'free_used': used,
                'free_limit': FREE_LIMIT,
                'remaining_free': max(0, FREE_LIMIT - used),
            }