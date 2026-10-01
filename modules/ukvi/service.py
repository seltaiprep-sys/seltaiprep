# modules/ukvi/service.py
"""
UKVI service — core business logic with real UKVI realism + pool.

v3.0 — POOL + ASYNC QUEUE:
  • generate_interview() now returns one of:
    - {'source': 'cache', 'questions': [...]}    — pool HIT
    - {'source': 'shared', 'job_id': '...'}      — same uni generating
    - {'source': 'new', 'job_id': '...'}         — new job created
  • Pool lookup by cache_key (university+course+difficulty)
  • Same university → same pool set (with rephrase for variety)
  • Different university → separate pool entry
  • Async generation handled by background worker (in app.py)

v2.0 — REAL UKVI REALISM:
  • Profile validation (university, course required)
  • Cache key length safety (topic VARCHAR(255))
  • 50% rephrase probability (cost saving)
  • Curated question bank integration (via interview_gen)

v1.0 — Initial version with caching + light edits.
"""

import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Dict, Optional, List

from .models import UKVIUser, UKVITestSession, UKVITestBank
from .generators.interview_generator import UKVIInterviewGenerator
from .evaluators.evaluator import UKVIEvaluator
from .transcriber import UKVITranscriber

logger = logging.getLogger(__name__)


class UKVIService:
    def __init__(self, ai_engine, db):
        self.ai_engine = ai_engine
        self.db = db
        self.interview_gen = UKVIInterviewGenerator(ai_engine)
        self.evaluator = UKVIEvaluator(ai_engine)
        self.transcriber = UKVITranscriber()

    # ═══════════════════════════════════════════════════════════════
    # USER MANAGEMENT
    # ═══════════════════════════════════════════════════════════════
    def _get_or_create_user(self, user_id: int) -> UKVIUser:
        """Fetch UKVIUser; create one if it doesn't exist."""
        user = self.db.session.query(UKVIUser).filter_by(id=user_id).first()
        if not user:
            user = UKVIUser(
                id=user_id,
                username=f"ukvi_user_{user_id}",
                email=f"ukvi_user_{user_id}@example.com",
            )
            self.db.session.add(user)
            self.db.session.commit()
            logger.info(f"Created UKVIUser for main user {user_id}")
        return user

    def _get_user(self, user_id: int) -> UKVIUser:
        """Legacy alias – now creates if missing."""
        return self._get_or_create_user(user_id)

    # ═══════════════════════════════════════════════════════════════
    # PROFILE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════
    def save_profile(self, user_id: int, profile_data: Dict) -> Dict:
        """Save or update the user's profile in the JSON field."""
        user = self._get_or_create_user(user_id)
        current = user.get_profile() if user.profile_data else {}
        current.update(profile_data)
        user.set_profile(current)
        self.db.session.commit()
        logger.info(
            f"Profile saved for UKVI user {user_id} "
            f"(keys: {list(profile_data.keys())})"
        )
        return {'success': True, 'user_id': user_id}

    def get_profile(self, user_id: int) -> Optional[Dict]:
        """Return the user's profile as a dict (or None if empty)."""
        user = self._get_or_create_user(user_id)
        profile = user.get_profile() if user.profile_data else {}
        return profile if profile else None

    # ═══════════════════════════════════════════════════════════════
    # LIGHT REPHRASE (cost-saving)
    # ═══════════════════════════════════════════════════════════════
    def _rephrase_questions(self, questions: List[Dict]) -> List[Dict]:
        """
        Lightly rephrase cached questions to feel fresh (uses cheap AI).
        Returns original list if AI unavailable or fails.
        """
        if not self.ai_engine or not questions:
            return questions

        if len(questions) < 3:
            return questions

        prompt = f"""You are a UKVI examiner. The following questions are good, but I need you to rephrase them slightly.
Keep the same meaning and category, just change the wording to make them sound natural and fresh.
Return ONLY valid JSON list of objects with keys "category" and "question".

Original questions:
{json.dumps(questions, indent=2)}
"""
        try:
            resp = self.ai_engine.generate(prompt, max_tokens=800, temperature=0.5)
            match = re.search(r'\[.*\]', resp, re.DOTALL)
            if match:
                new_qs = json.loads(match.group())
                if new_qs and isinstance(new_qs, list):
                    logger.info(f" Rephrased {len(new_qs)} cached questions")
                    return new_qs
        except Exception as e:
            logger.warning(f"Rephrase failed: {e}, using original questions")

        return questions

    # ═══════════════════════════════════════════════════════════════
    # BUILD CACHE KEY
    # ═══════════════════════════════════════════════════════════════
    @staticmethod
    def build_cache_key(university: str, course: str, difficulty: str = 'medium') -> str:
        """Build safe-length cache key for pool lookup."""
        uni = (university or 'unknown').strip().lower()[:80]
        course = (course or 'unknown').strip().lower()[:80]
        difficulty = (difficulty or 'medium').strip().lower()[:20]
        return f"ukvi_{uni}_{course}_{difficulty}"[:255]

    # ═══════════════════════════════════════════════════════════════
    # POOL LOOKUP (fast path)
    # ═══════════════════════════════════════════════════════════════
    def get_from_pool(self, cache_key: str) -> Optional[List[Dict]]:
        """
        Return questions from pool if a set exists for this cache_key.
        Returns None if no pool entry.
        """
        try:
            pooled = (
                self.db.session.query(UKVITestBank)
                .filter_by(test_type='ukvi_interview', topic=cache_key)
                .first()
            )
            if not pooled:
                return None

            data = pooled.get_test_data() or {}
            questions = data.get('questions', [])
            if not questions:
                return None

            pooled.usage_count = (pooled.usage_count or 0) + 1
            self.db.session.commit()

            logger.info(
                f" [UKVI POOL HIT] cache_key={cache_key} "
                f"usage={pooled.usage_count}"
            )
            return questions
        except Exception as e:
            self.db.session.rollback()
            logger.exception(f"get_from_pool failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # POOL SAVE
    # ═══════════════════════════════════════════════════════════════
    def save_to_pool(self, cache_key: str, difficulty: str, questions: List[Dict]) -> bool:
        """Save a fresh set of questions to pool (1 per cache_key)."""
        try:
            existing = (
                self.db.session.query(UKVITestBank)
                .filter_by(test_type='ukvi_interview', topic=cache_key)
                .first()
            )
            if existing:
                logger.info(f" [UKVI POOL EXISTS] cache_key={cache_key}")
                return True

            pooled = UKVITestBank(
                test_type='ukvi_interview',
                difficulty=difficulty,
                topic=cache_key,
                test_data=json.dumps({'questions': questions}),
                usage_count=0,
            )
            self.db.session.add(pooled)
            self.db.session.commit()
            logger.info(f" [UKVI POOL SAVE] cache_key={cache_key}")
            return True
        except Exception as e:
            self.db.session.rollback()
            logger.exception(f"save_to_pool failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # INTERVIEW GENERATION (POOL-AWARE)
    # ═══════════════════════════════════════════════════════════════
    def generate_interview(
        self,
        user_id: int,
        difficulty: str = "medium",
        personalized: bool = True,
    ) -> Dict:
        """
        Pool-aware interview generation.

        Returns one of:
          {'source': 'cache',  'questions': [...], 'success': True}
          {'source': 'shared', 'job_id': '...',    'success': True}
          {'source': 'new',    'job_id': '...',    'success': True}
          {'error': '...',                         'success': False}
        """
        from .managers import ukvi_pool_manager

        self._get_or_create_user(user_id)
        profile = self.get_profile(user_id) or {}

        # ─── 1. Validate profile ────────────────────────────────
        required = ['university', 'course']
        missing = [k for k in required if not profile.get(k)]
        if missing:
            logger.warning(
                f"UKVI generation blocked for user {user_id}: "
                f"missing fields {missing}"
            )
            return {
                'error': (
                    f'Profile incomplete. Please fill in: '
                    f'{", ".join(missing)}.'
                ),
                'success': False,
                'missing_fields': missing,
            }

        # ─── 2. Build cache key ─────────────────────────────────
        cache_key = self.build_cache_key(
            university=profile.get('university', ''),
            course=profile.get('course', ''),
            difficulty=difficulty,
        )

        # ─── 3. Pool lookup (fast path) ─────────────────────────
        pooled_questions = self.get_from_pool(cache_key)
        if pooled_questions:
            # 50% chance: rephrase for freshness; 50%: shuffle only
            if self.ai_engine and random.random() < 0.5:
                try:
                    pooled_questions = self._rephrase_questions(pooled_questions)
                except Exception as e:
                    logger.warning(f"Rephrase failed: {e}")
            else:
                random.shuffle(pooled_questions)

            return {
                'source': 'cache',
                'questions': pooled_questions,
                'cached': True,
                'ai_generated': False,
                'success': True,
            }

        # ─── 4. Check existing job (share with same university) ─
        existing_job = ukvi_pool_manager.find_active_job(cache_key)
        if existing_job:
            logger.info(
                f" [UKVI SHARED JOB] user={user_id} "
                f"using job_id={existing_job.id} key={cache_key}"
            )
            return {
                'source': 'shared',
                'job_id': existing_job.id,
                'success': True,
            }

        # ─── 5. Create new job ───────────────────────────────────
        job = ukvi_pool_manager.create_job(
            user_id=user_id,
            cache_key=cache_key,
            university=profile.get('university', ''),
            course=profile.get('course', ''),
            difficulty=difficulty,
        )
        logger.info(
            f" [UKVI NEW JOB] user={user_id} job_id={job.id} key={cache_key}"
        )
        return {
            'source': 'new',
            'job_id': job.id,
            'success': True,
        }

    # ═══════════════════════════════════════════════════════════════
    # SYNC GENERATION (used by background worker)
    # ═══════════════════════════════════════════════════════════════
    def generate_questions_sync(
        self,
        user_id: int,
        difficulty: str = 'medium',
        profile: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Synchronous question generation (called from background worker).
        Returns list of question dicts, or empty list on failure.
        """
        if profile is None:
            profile = self.get_profile(user_id) or {}

        try:
            questions = self.interview_gen.generate_questions(
                difficulty=difficulty,
                personalized=True,
                profile=profile,
                visa_type='student',
            )
            return questions or []
        except Exception as e:
            logger.exception(f"generate_questions_sync failed: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # EVALUATION
    # ═══════════════════════════════════════════════════════════════
    def evaluate_response(
        self,
        question: str,
        answer: str,
        category: str = 'general',
    ) -> Dict:
        """Evaluate a single answer (used as fallback)."""
        return self.evaluator.evaluate(question, answer, category)

    def evaluate_interview(
        self,
        evaluations: list,
        profile: Dict,
    ) -> Dict:
        """Final evaluation of the whole interview."""
        return self.evaluator.final_evaluation(evaluations, profile)

    # ═══════════════════════════════════════════════════════════════
    # TRANSCRIPTION
    # ═══════════════════════════════════════════════════════════════
    def transcribe_audio(self, audio_path: str) -> str:
        """Transcribe audio file to text."""
        try:
            return self.transcriber.transcribe(audio_path)
        except Exception as e:
            logger.exception(f"Transcription failed for {audio_path}: {e}")
            return ""