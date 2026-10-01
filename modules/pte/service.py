# modules/pte/service.py
"""PTE Service - Core business logic for PTE test operations.

Responsibilities:
  • Raw test generation (pool-friendly — no session, no quota)
  • Legacy generators (backward-compat)
  • Session persistence
  • Session retrieval / resume
  • Subscription status (read-only)
  • Module access check (subscription + free usage)
  • Cleanup of old sessions

Scoring is handled by `modules.pte.utils.scoring.PTEScoring`.
Submit logic lives in `modules/pte/api.py`.

Pool flow:
  api.py
    → pool_manager.get_or_generate(
          module=..., generate_fn=service.generate_*_raw)
    → service.generate_*_raw() returns raw test_data (no session)
    → pool saves it → serves from pool
    → api.py creates per-user session from pool test
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from .models import db, PTETestSession, PTESubscription
from .generators import PTEReading, PTEListening, PTESpeakingWriting

logger = logging.getLogger(__name__)


class PTEService:
    """Core service for PTE test operations – AI engine injected."""

    def __init__(self, ai_engine=None):
        self.ai_engine = ai_engine

    # ═══════════════════════════════════════════════════════════════════
    # RAW GENERATORS — Pool-friendly
    # ═══════════════════════════════════════════════════════════════════
    #
    # These methods ONLY generate.
    # They do NOT:
    # • create a PTETestSession
    # • check/increment subscription usage
    # • touch user quota
    #
    # The pool manager calls them and decides what to do with the result.

    def generate_reading_test_raw(
        self, user_id: int, difficulty: str = "medium"
    ) -> Dict:
        """Generate a Reading test WITHOUT creating a session."""
        try:
            logger.info(
                f" Raw Reading generation for user {user_id} "
                f"(difficulty={difficulty})"
            )
            return PTEReading.generate_full_test(difficulty)
        except Exception as e:
            logger.error(f"Reading raw generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def generate_listening_test_raw(
        self,
        user_id: int,
        difficulty: str = "medium",
        generate_audio: bool = True,
    ) -> Dict:
        """Generate a Listening test WITHOUT creating a session."""
        try:
            logger.info(
                f" Raw Listening generation for user {user_id} "
                f"(difficulty={difficulty}, audio={generate_audio})"
            )
            listening = PTEListening()
            return listening.generate_full_test(
                difficulty, generate_audio=generate_audio
            )
        except Exception as e:
            logger.error(f"Listening raw generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def generate_speaking_writing_test_raw(
        self,
        user_id: int,
        difficulty: str = "medium",
        generate_images: bool = True,
        generate_audio: bool = True,
    ) -> Dict:
        """Generate a Speaking & Writing test WITHOUT creating a session."""
        try:
            logger.info(
                f" Raw S&W generation for user {user_id} "
                f"(difficulty={difficulty}, images={generate_images}, "
                f"audio={generate_audio})"
            )
            sw = PTESpeakingWriting()
            return sw.generate_full_test(
                difficulty,
                generate_images=generate_images,
                generate_audio=generate_audio,
            )
        except Exception as e:
            logger.error(f"S&W raw generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def generate_full_test_raw(
        self, user_id: int, difficulty: str = "medium"
    ) -> Dict:
        """
        Generate a complete Full Test (all 3 sections) WITHOUT a session.
        For pool use only.

        Returns:
            {
              'success': True,
              'sections': {
                 'speaking_writing': {'questions': [...], 'duration': '...', 'label': '...'},
                 'reading': {'questions': [...], 'duration': '...', 'label': '...'},
                 'listening': {'questions': [...], 'duration': '...', 'label': '...'},
              },
              'duration': '~2.5 hours',
              'question_count': N,
            }
        """
        try:
            logger.info(
                f" Raw FULL TEST generation for user {user_id} "
                f"(difficulty={difficulty})"
            )

            # ─── Speaking & Writing ──────────────────────────────────
            sw_result = self.generate_speaking_writing_test_raw(user_id, difficulty)
            if not sw_result.get('success') or not sw_result.get('questions'):
                err = sw_result.get('error', 'no questions')
                logger.error(f" Full test: S&W section failed — {err}")
                return {
                    'success': False,
                    'error': f'Speaking & Writing generation failed: {err}',
                }

            # ─── Reading ──────────────────────────────────────────────
            reading_result = self.generate_reading_test_raw(user_id, difficulty)
            if not reading_result.get('success') or not reading_result.get('questions'):
                err = reading_result.get('error', 'no questions')
                logger.error(f" Full test: Reading section failed — {err}")
                return {
                    'success': False,
                    'error': f'Reading generation failed: {err}',
                }

            # ─── Listening ────────────────────────────────────────────
            listening_result = self.generate_listening_test_raw(user_id, difficulty)
            if not listening_result.get('success') or not listening_result.get('questions'):
                err = listening_result.get('error', 'no questions')
                logger.error(f" Full test: Listening section failed — {err}")
                return {
                    'success': False,
                    'error': f'Listening generation failed: {err}',
                }

            # ─── Assemble ─────────────────────────────────────────────
            sections = {
                'speaking_writing': {
                    'questions': sw_result['questions'],
                    'duration': sw_result.get('duration', '30-40 min'),
                    'label': 'Speaking & Writing',
                },
                'reading': {
                    'questions': reading_result['questions'],
                    'duration': reading_result.get('duration', '30-40 min'),
                    'label': 'Reading',
                },
                'listening': {
                    'questions': listening_result['questions'],
                    'duration': listening_result.get('duration', '20-25 min'),
                    'label': 'Listening',
                },
            }

            total_q = (
                len(sw_result['questions'])
                + len(reading_result['questions'])
                + len(listening_result['questions'])
            )

            logger.info(
                f" Raw FULL TEST generated: "
                f"S&W={len(sw_result['questions'])}, "
                f"R={len(reading_result['questions'])}, "
                f"L={len(listening_result['questions'])}, "
                f"total={total_q}"
            )

            return {
                'success': True,
                'sections': sections,
                'duration': '~2.5 hours',
                'question_count': total_q,
            }

        except Exception as e:
            logger.error(f"Full test raw generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════════════════════
    # LEGACY WRAPPERS — backward compatibility
    # ═══════════════════════════════════════════════════════════════════
    #
    # These generate AND save a session (old flow). api.py no longer uses
    # them — but keep them so any external caller doesn't break.

    def generate_reading_test(self, user_id: int, difficulty: str = "medium") -> Dict:
        """LEGACY: Generate + save session. Prefer generate_reading_test_raw + pool."""
        try:
            test_data = PTEReading.generate_full_test(difficulty)
            return self._save_test(user_id, 'pte_reading', difficulty, test_data)
        except Exception as e:
            logger.error(f"Reading generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def generate_listening_test(
        self,
        user_id: int,
        difficulty: str = "medium",
        generate_audio: bool = True,
    ) -> Dict:
        """LEGACY: Generate + save session. Prefer generate_listening_test_raw + pool."""
        try:
            listening = PTEListening()
            test_data = listening.generate_full_test(
                difficulty, generate_audio=generate_audio
            )
            return self._save_test(user_id, 'pte_listening', difficulty, test_data)
        except Exception as e:
            logger.error(f"Listening generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def generate_speaking_writing_test(
        self,
        user_id: int,
        difficulty: str = "medium",
        generate_images: bool = True,
        generate_audio: bool = True,
    ) -> Dict:
        """LEGACY: Generate + save session. Prefer *_raw + pool."""
        try:
            sw = PTESpeakingWriting()
            test_data = sw.generate_full_test(
                difficulty,
                generate_images=generate_images,
                generate_audio=generate_audio,
            )
            return self._save_test(
                user_id, 'pte_speaking_writing', difficulty, test_data
            )
        except Exception as e:
            logger.error(f"Speaking/Writing generation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════════════════════
    # SESSION PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════

    def _save_test(
        self,
        user_id: int,
        test_type: str,
        difficulty: str,
        test_data: Dict,
    ) -> Dict:
        """Persist a freshly generated test as an in-progress session."""
        if not test_data.get('success', True):
            test_data['success'] = True

        questions = test_data.get('questions', [])
        if not questions:
            return {
                'success': False,
                'error': 'No questions generated',
                'test_type': test_type,
            }

        session = PTETestSession(
            user_id=user_id,
            test_type=test_type,
            difficulty=difficulty,
            test_data=test_data,
            status='in_progress',
        )
        db.session.add(session)
        db.session.commit()

        logger.info(
            f" Saved new {test_type} session id={session.id} "
            f"for user {user_id} ({len(questions)} questions)"
        )

        result = {
            'success': True,
            'test_id': session.id,
            'test_type': test_type,
            'questions': questions,
            'total_questions': test_data.get('total_questions', len(questions)),
            'expected_questions': test_data.get('expected_questions'),
            'duration': test_data.get('duration', '30-40 minutes'),
            'difficulty': difficulty,
            'year': 2026,
        }

        if test_data.get('audio_urls'):
            result['audio_urls'] = test_data['audio_urls']
        if test_data.get('audio_generated') is not None:
            result['audio_generated'] = test_data['audio_generated']

        return result

    def create_session_from_test_data(
        self,
        user_id: int,
        test_type: str,
        test_data: Dict,
        difficulty: str = "medium",
        pool_id: Optional[int] = None,
    ) -> int:
        """
        Create a per-user session from a pool test.
        Called by api.py after pool_manager returns a test.

        Returns: session_id
        """
        session_test_data = dict(test_data or {})
        if pool_id is not None:
            session_test_data['pool_id'] = pool_id

        session = PTETestSession(
            user_id=user_id,
            test_type=test_type,
            difficulty=difficulty,
            test_data=session_test_data,
            status='in_progress',
            answers_so_far={},
            current_question_index=0,
            start_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        db.session.add(session)
        db.session.commit()

        logger.info(
            f" Session #{session.id} created from pool "
            f"(user={user_id}, type={test_type}, pool_id={pool_id})"
        )
        return session.id

    # ═══════════════════════════════════════════════════════════════════
    # TEST RETRIEVAL / RESUME
    # ═══════════════════════════════════════════════════════════════════

    def get_test(self, test_id: int) -> Optional[Dict]:
        """Return the raw test_data payload for a session."""
        session = PTETestSession.query.get(test_id)
        return session.test_data if session else None

    def get_active_session(
        self, user_id: int, test_type: str
    ) -> Optional[PTETestSession]:
        """
        Lightweight — return the most recent in-progress session
        without any validation/deletion. Use `get_resume_session()` for
        validated retrieval.
        """
        return (
            PTETestSession.query
            .filter(
                PTETestSession.user_id == user_id,
                PTETestSession.test_type == test_type,
                PTETestSession.status.in_(['in_progress', 'paused']),
            )
            .order_by(PTETestSession.start_time.desc())
            .first()
        )

    def get_resume_session(
        self, user_id: int, test_type: str
    ) -> Optional[PTETestSession]:
        """
        Return the most recent in-progress session for the user.
        Corrupted sessions (no questions) are deleted automatically.
        """
        sessions = (
            PTETestSession.query
            .filter(
                PTETestSession.user_id == user_id,
                PTETestSession.test_type == test_type,
                PTETestSession.status.in_(['in_progress', 'paused']),
            )
            .order_by(PTETestSession.start_time.desc())
            .all()
        )

        for session in sessions:
            test_data = session.test_data or {}
            # Support both standalone and full test shapes
            sections = test_data.get('sections')
            if sections and isinstance(sections, dict):
                has_any = any(
                    sec and isinstance(sec, dict) and sec.get('questions')
                    for sec in sections.values()
                )
                if has_any:
                    return session
            else:
                questions = test_data.get('questions') or []
                if questions and len(questions) > 0:
                    return session

            logger.warning(
                f"Deleting corrupted PTE session {session.id} "
                f"(no questions) for user {user_id}"
            )
            db.session.delete(session)
            db.session.commit()

        return None

    # ═══════════════════════════════════════════════════════════════════
    # SUBSCRIPTION STATUS (read-only)
    # ═══════════════════════════════════════════════════════════════════

    def get_subscription_status(self, user_id: int) -> Dict:
        """Read-only subscription snapshot for a user."""
        sub = PTESubscription.query.filter_by(user_id=user_id).first()
        if not sub:
            from .models import create_default_pte_subscription
            sub = create_default_pte_subscription(user_id)

        now = datetime.now(timezone.utc)
        end = sub.subscription_end
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        has_sub = bool(sub.status == 'active' and end and end > now)

        return {
            'has_subscription': has_sub,
            'plan': sub.plan,
            'status': sub.status,
            'free_tests_used': sub.free_tests_used or 0,
            'free_limit': getattr(sub, 'free_tests_limit', None) or 3,
            'tests_remaining': sub.tests_remaining or 0,
            'tests_taken': sub.tests_taken or 0,
            'days_remaining': (end - now).days if end else 0,
            'subscription_start': (
                sub.subscription_start.isoformat()
                if sub.subscription_start else None
            ),
            'subscription_end': (
                sub.subscription_end.isoformat()
                if sub.subscription_end else None
            ),
        }

    def get_module_access(self, user_id: int, test_type: str) -> Dict:
        """
        Combined read-only check — subscription + free usage for one module.

        Returns:
            {
              'has_subscription': bool,
              'free_used': int,
              'free_limit': int,
              'free_remaining': int,
              'can_access': bool,
            }
        """
        try:
            sub = PTESubscription.query.filter_by(user_id=user_id).first()
            if not sub:
                from .models import create_default_pte_subscription
                sub = create_default_pte_subscription(user_id)

            now = datetime.now(timezone.utc)
            end = sub.subscription_end
            if end and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            has_sub = bool(sub.status == 'active' and end and end > now)

            usage = sub.free_usage or {}
            if isinstance(usage, str):
                import json as _json
                try:
                    usage = _json.loads(usage)
                except Exception:
                    usage = {}
            if not isinstance(usage, dict):
                usage = {}

            used = int(usage.get(test_type, 0) or 0)
            limit = int(getattr(sub, 'free_tests_limit', None) or 3)
            remaining = max(0, limit - used)

            return {
                'has_subscription': has_sub,
                'free_used': used,
                'free_limit': limit,
                'free_remaining': remaining,
                'can_access': has_sub or (used < limit),
            }
        except Exception as e:
            logger.exception(f"get_module_access failed: {e}")
            return {
                'has_subscription': False,
                'free_used': 0,
                'free_limit': 0,
                'free_remaining': 0,
                'can_access': False,
            }

    # ═══════════════════════════════════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════════════════════════════════

    def cleanup_old_sessions(self, days: int = 7) -> int:
        """Delete completed/abandoned sessions older than `days`."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        old = (
            PTETestSession.query
            .filter(
                PTETestSession.status.in_(['completed', 'abandoned']),
                PTETestSession.ended_at < cutoff,
            )
            .all()
        )
        count = len(old)
        for session in old:
            db.session.delete(session)
        db.session.commit()

        if count:
            logger.info(f" Cleaned up {count} old PTE sessions (>{days} days)")

        return count


# ═══════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════

def create_pte_service(ai_engine=None) -> PTEService:
    """Factory used by `init_pte_api()`."""
    return PTEService(ai_engine)


__all__ = ['PTEService', 'create_pte_service']