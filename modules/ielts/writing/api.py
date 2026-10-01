"""API entry point for IELTS Writing module - PURE AI, NO FALLBACKS
with Resumable Generation.

 v6 POOL-NATIVE + USER_ID NORMALIZATION FIX:
  (E) CRITICAL — `GenerationState.user_id` is an INTEGER column in
        PostgreSQL, but automatic pool generation passes the string
        `'bank_generator'`. All queries and inserts now normalize
        user_id via `_coerce_user_id_for_state()` so the string IDs
        map to 0 (system) instead of crashing with a DataError.
        Fixes: "invalid input syntax for type integer: 'bank_generator'"

 v5 POOL-NATIVE + CRITICAL FIXES + ACCURACY TELEMETRY:
  (A) Rate limiter is now thread-safe (threading.Lock), uses UTC
        timestamps, and detects `user_id` whether passed by keyword
        OR positionally.
  (B) `submit()` no longer hardcodes Band 0 for under-length
        essays. It ALWAYS delegates to the evaluator (graduated
        penalty: 149 words → up to 7.5, not 0).
  (C) New `_find_or_create_active_state()` helper — `get_task1()`
        and `get_task2()` REUSE the most recent in_progress state.
  (D) `evaluate()` logs which path was used (AI vs fallback) so
        accuracy regressions can be diagnosed from logs.
"""

import logging
import uuid
import json
import time
import threading
from typing import Dict, List, Optional, Any
from functools import wraps
from datetime import datetime, timedelta, timezone

# ============================================
# DYNAMIC MODEL IMPORTS
# ============================================
from models import db, GenerationState
from models import (
    get_test_session_model,
)

_MODULE = 'ielts'

logger = logging.getLogger(__name__)


# ============================================
# SESSION HELPERS
# ============================================

def _current_user_id_from_session():
    """Best-effort lookup of the authenticated user id from Flask session."""
    try:
        from flask import session as flask_session
        for key in ('user_id', 'user', 'uid', 'current_user_id', 'id'):
            val = flask_session.get(key)
            if val is not None and val != '':
                return val
    except Exception:
        pass
    return None


def _coerce_test_id(test_id) -> Optional[int]:
    """Normalize a test_id to an integer suitable for FK columns."""
    if test_id is None or test_id == '':
        return None
    if isinstance(test_id, int):
        return test_id
    try:
        return int(str(test_id).strip())
    except (ValueError, TypeError):
        return None


def _coerce_user_id(user_id) -> int:
    """
     v6: Normalize user_id for the INTEGER generation_states.user_id column.

    Non-numeric IDs like 'bank_generator', 'system', 'anonymous' map to 0.
    This prevents:
        psycopg2.errors.InvalidTextRepresentation:
        invalid input syntax for type integer: "bank_generator"
    """
    if user_id is None or user_id == '' or user_id == 'anonymous':
        return 0
    if isinstance(user_id, int):
        return user_id
    try:
        return int(str(user_id).strip())
    except (ValueError, TypeError):
        logger.debug(f"[state] non-numeric user_id '{user_id}' → 0 (system)")
        return 0


# ============================================
# RATE LIMITING (thread-safe + UTC + positional-arg aware)
# ============================================

_rate_limit_store = {}
_rate_limit_lock = threading.Lock()


def rate_limit(max_requests: int = 30, window_seconds: int = 60):
    """
    Thread-safe per-user rate limiter.

    Keyed by (client_id, function_name). Uses UTC timestamps so the
    window is stable across timezone changes. Detects `user_id` whether
    it arrives as a keyword argument OR as the first positional arg.

    NOTE: Still process-local. For multi-process deployments
    (gunicorn -w N) move this store to Redis via flask-limiter.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # Try kwargs first, then fall back to first positional arg
            client_id = kwargs.get('user_id')
            if not client_id and args:
                client_id = args[0]
            client_id = client_id or 'anonymous'

            now_ts = datetime.now(timezone.utc).timestamp()
            key = f"{client_id}:{func.__name__}"

            with _rate_limit_lock:
                # Drop expired entries
                cutoff = now_ts - window_seconds
                for k in [k for k, v in _rate_limit_store.items()
                          if v['timestamp'] < cutoff]:
                    del _rate_limit_store[k]

                entry = _rate_limit_store.get(key)
                if entry and entry['count'] >= max_requests:
                    logger.warning(
                        f"Rate limit exceeded for {client_id} on {func.__name__}"
                    )
                    return {
                        'error': (f'Rate limit exceeded. Max {max_requests} '
                                  f'requests per {window_seconds} seconds.'),
                        'success': False,
                    }
                if entry:
                    entry['count'] += 1
                    entry['timestamp'] = now_ts
                else:
                    _rate_limit_store[key] = {'count': 1, 'timestamp': now_ts}

            return func(self, *args, **kwargs)
        return wrapper
    return decorator


# ============================================
# MAIN API CLASS
# ============================================

class WritingAPI:
    """Public API for IELTS Writing tests - PURE AI mode with Resumable Generation."""

    def __init__(self, ai_engine=None, db=None):
        self.ai_engine = ai_engine
        self.db = db
        self.module = _MODULE

        if not self.ai_engine:
            raise ValueError(" AI Engine is required. No fallback templates available.")

        if not self.db:
            raise ValueError(" Database session is required for resumable generation.")

        from .service import create_writing_service
        from .evaluator import EssayEvaluator

        self.service = create_writing_service(ai_engine)
        self.evaluator = EssayEvaluator(ai_engine)

        logger.info("WritingAPI initialized with pure AI mode and resumable generation")

    # ==================== USER ID HELPERS ====================

    def _coerce_user_id_for_state(self, user_id) -> int:
        """
         v6: Normalize user_id to int for generation_states.user_id.

        Non-numeric IDs (e.g. 'bank_generator') → 0 (system).
        """
        return _coerce_user_id(user_id)

    # ==================== TEST RECORD PERSISTENCE ====================

    def _persist_test_record(self, test_data: Dict, difficulty: str,
                             topic: Optional[str] = None) -> Optional[int]:
        """Save the generated writing test as a WritingTestRecord; return integer PK."""
        try:
            self.service._ensure_repository()
            repo = getattr(self.service, 'repo', None)
            if not repo:
                logger.warning("[persist_test] No repository available — skipping WritingTestRecord")
                return None

            task1 = test_data.get('task1', {}) or {}
            task2 = test_data.get('task2', {}) or {}

            record_data = {
                'topic': topic or 'general',
                'difficulty': difficulty,
                'exam_type': 'ielts',
                'task1_prompt': task1.get('prompt') or task1.get('question') or '',
                'task1_chart_type': task1.get('chart_type') or 'unknown',
                'task1_chart_data': task1.get('chart_data') or {},
                'task2_prompt': task2.get('prompt') or task2.get('question') or '',
                'serial_number': _coerce_test_id(test_data.get('serial')) or
                                 int(uuid.uuid4().int % 10**9),
            }

            record_id = repo.save_test(record_data, module=self.module)
            if record_id:
                logger.info(f"[persist_test] WritingTestRecord saved: id={record_id}")
            return record_id

        except Exception as e:
            logger.warning(f"[persist_test] Failed to persist test record: {e}")
            return None

    # ==================== GENERATION STATE ====================

    def _get_or_create_state(self, user_id: str, test_id: str = None,
                            difficulty: str = "medium",
                            topic: str = None) -> Dict:
        """
        Get existing generation state or create a new one.

         v6: user_id is normalized to int before every query/insert.
        """
        try:
            # v6: normalize user_id for the INTEGER column
            user_id_int = self._coerce_user_id_for_state(user_id)

            if test_id:
                state = GenerationState.query.filter_by(
                    user_id=user_id_int,
                    test_id=test_id,
                    module='ielts_writing'
                ).first()

                if state:
                    return {
                        'success': True,
                        'state': state,
                        'exists': True,
                        'test_id': test_id
                    }

            if not test_id:
                test_id = f"w_{uuid.uuid4().hex[:12]}"

            new_state = GenerationState(
                user_id=user_id_int, # int, not string
                module='ielts_writing',
                test_id=test_id,
                difficulty=difficulty,
                total_sections=2,
                generated_sections=[],
                current_section=1,
                status='in_progress'
            )

            self.db.session.add(new_state)
            self.db.session.commit()

            logger.info(
                f"New generation state created: {test_id} "
                f"for user {user_id_int} (orig={user_id!r})"
            )

            return {
                'success': True,
                'state': new_state,
                'exists': False,
                'test_id': test_id
            }

        except Exception as e:
            logger.exception(f"Failed to get/create state: {e}")
            self.db.session.rollback()
            return {
                'success': False,
                'error': str(e)
            }

    def _find_or_create_active_state(self, user_id: str,
                                     difficulty: str = "medium",
                                     topic: str = None) -> Dict:
        """
         v5: Reuse the most recent in_progress state if one exists
        for the user; otherwise create a new one.

         v6: user_id normalized to int before query.
        """
        try:
            user_id_int = self._coerce_user_id_for_state(user_id)

            existing = GenerationState.query.filter_by(
                user_id=user_id_int,
                module='ielts_writing',
                status='in_progress'
            ).order_by(GenerationState.created_at.desc()).first()

            if existing:
                logger.info(
                    f"[state] Reusing active state {existing.test_id} "
                    f"for user {user_id_int}"
                )
                return {
                    'success': True,
                    'state': existing,
                    'exists': True,
                    'test_id': existing.test_id,
                }
        except Exception as e:
            logger.warning(f"[state] active lookup failed: {e}")

        return self._get_or_create_state(
            user_id=user_id, difficulty=difficulty, topic=topic
        )

    def _update_state_section(self, state, section_num: int,
                             section_data: Dict = None,
                             status: str = None) -> bool:
        """Update generation state for a section"""
        try:
            if section_num not in state.generated_sections:
                state.generated_sections.append(section_num)
                state.generated_sections = sorted(state.generated_sections)

            if section_data:
                if not state.section_data:
                    state.section_data = {}
                state.section_data[str(section_num)] = section_data

            state.current_section = section_num + 1
            state.updated_at = datetime.now(timezone.utc)

            if status:
                state.status = status

            if len(state.generated_sections) >= state.total_sections:
                state.status = 'completed'
                state.completed_at = datetime.now(timezone.utc)

            self.db.session.commit()
            return True

        except Exception as e:
            logger.exception(f"Failed to update state: {e}")
            self.db.session.rollback()
            return False

    # ==================== RESUME LOGIC ====================

    def _generate_with_retry(self, factory, section_name: str,
                             max_attempts: int = 2) -> Dict:
        """Call factory() up to max_attempts times with exponential backoff."""
        last_result = {'error': f'{section_name} generation failed'}
        for attempt in range(1, max_attempts + 1):
            try:
                res = factory()
                if res and not res.get('error'):
                    return res
                last_result = res or last_result
                logger.warning(f"[resume] {section_name} attempt {attempt} returned error: "
                               f"{res.get('error') if isinstance(res, dict) else res}")
            except Exception as e:
                logger.warning(f"[resume] {section_name} attempt {attempt} raised: {e}")
                last_result = {'error': str(e)}

            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
        return last_result

    def _resume_from_state(self, state) -> Dict:
        """Resume generation from existing state"""
        try:
            generated = state.generated_sections or []
            total = state.total_sections

            if len(generated) >= total:
                state.status = 'completed'
                state.completed_at = datetime.now(timezone.utc)
                self.db.session.commit()

                return {
                    'success': True,
                    'resumed': True,
                    'completed': True,
                    'task1': state.section_data.get('1') if state.section_data else None,
                    'task2': state.section_data.get('2') if state.section_data else None,
                    'session_id': state.test_id,
                    'test_id': state.test_id,
                    'message': 'All sections already generated'
                }

            missing_sections = [i for i in range(1, total + 1) if i not in generated]

            logger.info(f"Resuming from state: generated={generated}, missing={missing_sections}")

            for section_num in missing_sections:
                if section_num == 1:
                    result = self._generate_with_retry(
                        lambda: self.service.get_task1(state.difficulty or 'medium'),
                        section_name="Task 1",
                        max_attempts=2,
                    )
                    if result and not result.get('error'):
                        self._update_state_section(state, 1, result)
                        logger.info(f"Task 1 generated via resume for state {state.test_id}")
                    else:
                        state.status = 'failed'
                        state.error_message = f"Failed to generate Task 1: {result.get('error')}"
                        self.db.session.commit()
                        return {
                            'success': False,
                            'error': f"Failed to generate Task 1: {result.get('error')}",
                            'resumed': True
                        }

                elif section_num == 2:
                    result = self._generate_with_retry(
                        lambda: self.service.get_task2(state.difficulty or 'medium'),
                        section_name="Task 2",
                        max_attempts=2,
                    )
                    if result and not result.get('error'):
                        self._update_state_section(state, 2, result)
                        logger.info(f"Task 2 generated via resume for state {state.test_id}")
                    else:
                        state.status = 'failed'
                        state.error_message = f"Failed to generate Task 2: {result.get('error')}"
                        self.db.session.commit()
                        return {
                            'success': False,
                            'error': f"Failed to generate Task 2: {result.get('error')}",
                            'resumed': True
                        }

            state.status = 'completed'
            state.completed_at = datetime.now(timezone.utc)
            self.db.session.commit()

            return {
                'success': True,
                'resumed': True,
                'completed': True,
                'task1': state.section_data.get('1') if state.section_data else None,
                'task2': state.section_data.get('2') if state.section_data else None,
                'session_id': state.test_id,
                'test_id': state.test_id,
                'message': 'Generation resumed and completed successfully'
            }

        except Exception as e:
            logger.exception(f"Resume failed: {e}")
            state.status = 'failed'
            state.error_message = str(e)
            self.db.session.commit()
            return {
                'success': False,
                'error': str(e),
                'resumed': True
            }

    # ==================== PUBLIC API METHODS ====================

    @rate_limit(max_requests=30, window_seconds=60)
    def start_test(self, difficulty: str = "medium", topic: str = None,
                   exam_type: str = "ielts", user_id: str = None,
                   resume: bool = True, force_new: bool = False,
                   auto_generate: bool = True, preserve_user_essay: bool = True) -> Dict:
        """
        Start a new writing test with resume support.

         POOL-NATIVE: Does NOT save to legacy TestBank.
        Pool persistence is handled by the caller (app.py) via
        `ielts_test_pool_manager.get_or_generate()`.

         v6: user_id normalized for GenerationState queries.
        """
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            # v6: normalize once at the top
            user_id_int = self._coerce_user_id_for_state(user_id) if user_id else 0

            # ---------- RESUME LOGIC ----------
            if user_id and resume and not force_new:
                existing_state = GenerationState.query.filter_by(
                    user_id=user_id_int, # int
                    module='ielts_writing',
                    status='in_progress'
                ).first()

                if existing_state:
                    logger.info(f"Found existing generation state for user {user_id_int}, resuming...")
                    result = self._resume_from_state(existing_state)
                    if result.get('success'):
                        record_id = self._persist_test_record(
                            test_data={'task1': result.get('task1'),
                                       'task2': result.get('task2'),
                                       'serial': None},
                            difficulty=difficulty,
                            topic=topic,
                        )
                        result['test_id'] = record_id
                        result['session_id'] = existing_state.test_id
                        return result

            # ---------- GENERATE NEW TEST ----------
            result = self.service.start_test(
                difficulty=difficulty,
                topic=topic,
                exam_type=exam_type,
                force_new=force_new,
                auto_generate=auto_generate,
                preserve_user_essay=preserve_user_essay
            )

            if result.get('error'):
                raise ValueError(result.get('error'))

            # Persist a WritingTestRecord → integer test_id for the FK
            record_id = self._persist_test_record(
                test_data=result,
                difficulty=difficulty,
                topic=topic,
            )

            # Save generation state
            session_test_id = None
            if user_id:
                state_result = self._get_or_create_state(
                    user_id=user_id,
                    difficulty=difficulty,
                    topic=topic
                )

                if state_result.get('success'):
                    state = state_result['state']
                    if result.get('task1'):
                        self._update_state_section(state, 1, result['task1'])
                    if result.get('task2'):
                        self._update_state_section(state, 2, result['task2'])
                    session_test_id = state.test_id

            result['test_id'] = record_id
            result['session_id'] = session_test_id
            result['source'] = 'on_demand'

            logger.info(
                f"Test started: difficulty={difficulty}, user={user_id_int}, "
                f"test_id={record_id}, session_id={session_test_id}"
            )
            return result

        except Exception as e:
            logger.exception(f"Start test failed: {e}")
            return {
                'error': str(e),
                'success': False,
                'message': f"Failed to generate test: {str(e)}"
            }

    @rate_limit(max_requests=50, window_seconds=60)
    def get_task1(self, difficulty: str = "medium", topic: str = None,
                  user_id: str = None) -> Dict:
        """
        Get Task 1 only.

         v6: user_id normalized for GenerationState queries.
        """
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            user_id_int = self._coerce_user_id_for_state(user_id) if user_id else 0

            if user_id:
                existing_state = GenerationState.query.filter_by(
                    user_id=user_id_int, # int
                    module='ielts_writing',
                    status='in_progress'
                ).first()

                if existing_state and existing_state.section_data and '1' in existing_state.section_data:
                    logger.info(f"Returning cached Task 1 for user {user_id_int}")
                    return {
                        **existing_state.section_data['1'],
                        'from_cache': True,
                        'session_id': existing_state.test_id,
                    }

            result = self.service.get_task1(difficulty, topic)

            if not result.get('chart_data') or not result.get('prompt'):
                raise ValueError(" Task 1 generation returned incomplete data")

            if user_id:
                # v5: reuse active state if one exists
                state_result = self._find_or_create_active_state(
                    user_id=user_id,
                    difficulty=difficulty,
                    topic=topic
                )
                if state_result.get('success'):
                    self._update_state_section(state_result['state'], 1, result)
                    result['session_id'] = state_result['test_id']

            logger.info(f"Task 1 generated: {result.get('chart_type')}")
            return result

        except Exception as e:
            logger.exception(f"Get Task 1 failed: {e}")
            return {
                'error': str(e),
                'success': False,
                'message': f"Failed to generate Task 1: {str(e)}"
            }

    @rate_limit(max_requests=50, window_seconds=60)
    def get_task2(self, difficulty: str = "medium", topic: str = None,
                  user_id: str = None) -> Dict:
        """
        Get Task 2 only.

         v6: user_id normalized for GenerationState queries.
        """
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            user_id_int = self._coerce_user_id_for_state(user_id) if user_id else 0

            if user_id:
                existing_state = GenerationState.query.filter_by(
                    user_id=user_id_int, # int
                    module='ielts_writing',
                    status='in_progress'
                ).first()

                if existing_state and existing_state.section_data and '2' in existing_state.section_data:
                    logger.info(f"Returning cached Task 2 for user {user_id_int}")
                    return {
                        **existing_state.section_data['2'],
                        'from_cache': True,
                        'session_id': existing_state.test_id,
                    }

            result = self.service.get_task2(difficulty, topic)

            if not result.get('prompt'):
                raise ValueError(" Task 2 generation returned no prompt")

            if user_id:
                # v5: reuse active state if one exists
                state_result = self._find_or_create_active_state(
                    user_id=user_id,
                    difficulty=difficulty,
                    topic=topic
                )
                if state_result.get('success'):
                    self._update_state_section(state_result['state'], 2, result)
                    result['session_id'] = state_result['test_id']

            logger.info(f"Task 2 generated: {result.get('topic')}")
            return result

        except Exception as e:
            logger.exception(f"Get Task 2 failed: {e}")
            return {
                'error': str(e),
                'success': False,
                'message': f"Failed to generate Task 2: {str(e)}"
            }

    @rate_limit(max_requests=20, window_seconds=60)
    def evaluate(self, essay: str, task_type: str = "task2",
                 prompt: str = "", chart_data: dict = None,
                 expected_features: list = None, session_id: str = None,
                 user_id: str = None) -> Dict:
        """
        Evaluate an essay.

         v6: user_id normalized for GenerationState queries.
        """
        try:
            if not essay or len(essay.strip()) < 20:
                return {
                    'error': 'Essay too short',
                    'overall_band': 0.0,
                    'feedback': 'BAND 0 - No meaningful response provided. Please write a proper essay (at least 150 words for Task 1, 250 for Task 2).',
                    'word_count': len(essay.split()) if essay else 0,
                    'success': False
                }

            result = self.service.evaluate_essay(
                essay=essay,
                task_type=task_type,
                prompt=prompt,
                chart_data=chart_data,
                expected_features=expected_features,
                session_id=session_id
            )

            if 'overall_band' not in result:
                result['overall_band'] = 0.0
                result['error'] = 'Incomplete evaluation result'

            # v5: accuracy telemetry — track which path was used
            evaluator_tag = result.get('evaluator', 'unknown')
            is_ai = 'ai' in str(evaluator_tag).lower()
            result['_accuracy_source'] = evaluator_tag

            if is_ai:
                logger.info(
                    f"[Accuracy] AI-based score: band={result.get('overall_band')}"
                )
            else:
                logger.warning(
                    f"[Accuracy] FALLBACK score used: source={evaluator_tag}, "
                    f"band={result.get('overall_band')} — accuracy may be lower"
                )

            if user_id and result.get('success', True):
                try:
                    user_id_int = self._coerce_user_id_for_state(user_id)

                    state = GenerationState.query.filter_by(
                        user_id=user_id_int, # int
                        module='ielts_writing',
                        status='in_progress'
                    ).first()

                    if state:
                        eval_key = f"evaluation_{task_type}"
                        if not state.section_data:
                            state.section_data = {}
                        state.section_data[eval_key] = result
                        self.db.session.commit()
                except Exception as e:
                    logger.warning(f"Could not save evaluation to state: {e}")

            logger.info(f"Evaluation complete: task_type={task_type}, band={result.get('overall_band')}")
            return result

        except Exception as e:
            logger.exception(f"Evaluation failed: {e}")
            return {
                'error': str(e),
                'overall_band': 0.0,
                'feedback': f"Evaluation failed: {str(e)}",
                'success': False
            }

    # ==================== UPGRADE METHODS ====================

    @rate_limit(max_requests=10, window_seconds=60)
    def upgrade(self, essay: str, current_band: float = 5.5,
                task_type: str = "task2", prompt: str = "",
                chart_data: dict = None, auto_generate: bool = False,
                preserve_user_essay: bool = True) -> Dict:
        """Upgrade an essay to a higher band"""
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available for upgrade")

            if not essay or len(essay.strip()) < 50:
                return {
                    'error': 'Essay too short to upgrade',
                    'upgraded_essay': essay,
                    'original_band': current_band,
                    'success': False
                }

            result = self.service.upgrade_essay(
                essay=essay,
                current_band=current_band,
                task_type=task_type,
                prompt=prompt,
                chart_data=chart_data,
                auto_generate=auto_generate,
                preserve_user_essay=preserve_user_essay
            )

            logger.info(f"Upgrade complete: task_type={task_type}, from_band={current_band}")
            return result

        except Exception as e:
            logger.exception(f"Upgrade failed: {e}")
            return {
                'error': str(e),
                'upgraded_essay': essay,
                'original_band': current_band,
                'success': False
            }

    @rate_limit(max_requests=10, window_seconds=60)
    def upgrade_async(self, essay: str, current_band: float = 5.5,
                      task_type: str = "task2", prompt: str = "",
                      chart_data: dict = None, auto_generate: bool = False,
                      preserve_user_essay: bool = True) -> Dict:
        """Upgrade essay with parallel processing"""
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available for upgrade")

            result = self.service.upgrade_essay_async(
                essay=essay,
                current_band=current_band,
                task_type=task_type,
                prompt=prompt,
                chart_data=chart_data,
                auto_generate=auto_generate,
                preserve_user_essay=preserve_user_essay
            )

            logger.info(f"Async upgrade complete: task_type={task_type}")
            return result

        except Exception as e:
            logger.exception(f"Async upgrade failed: {e}")
            return {
                'error': str(e),
                'upgraded_essay': essay,
                'original_band': current_band,
                'success': False
            }

    # ==================== SUBMIT ====================

    @rate_limit(max_requests=20, window_seconds=60)
    def submit(self, user_id: str, test_id,
               task1_essay: str = "", task1_prompt: str = "",
               task1_chart_data: dict = None,
               task1_expected_features: list = None,
               task2_essay: str = "", task2_prompt: str = "",
               session_id: str = None,
               auto_generate: bool = False,
               preserve_user_essay: bool = True) -> Dict:
        """
        Submit both Task 1 and Task 2 essays together.

         POOL-NATIVE: Does NOT mark TestBank usage.
        Pool progress is recorded by the caller (app.py) via
        `ielts_test_pool_manager.record_user_progress()`.

         v5: ALWAYS delegates to the evaluator — no more hardcoded
        Band 0 for under-length essays.

         v6: user_id normalized for GenerationState queries.
        """
        fk_test_id = _coerce_test_id(test_id)

        result = {
            'task1_band': 0,
            'task2_band': 0,
            'user_task1_band': 0,
            'user_task2_band': 0,
            'user_overall_band': 0,
            'overall_band': 0,
            'task1_feedback': '',
            'task2_feedback': '',
            'task1_detailed': {},
            'task2_detailed': {},
            'success': True,
        }

        try:
            # v6: normalize user_id once
            user_id_int = self._coerce_user_id_for_state(user_id)

            # ---------- TASK 1 ----------
            # v5: ALWAYS delegate to the evaluator — it handles
            # under-length essays gracefully with a graduated
            # penalty (149 words → up to 7.5, not 0).
            r1 = self.evaluate(
                task1_essay or "", 'task1', task1_prompt,
                task1_chart_data, task1_expected_features, session_id,
                user_id=user_id,
            )
            result['task1_band'] = r1.get('overall_band', 0)
            result['task1_feedback'] = r1.get('feedback', '')
            result['task1_detailed'] = r1

            self.service.save_essay(
                user_id, fk_test_id, 'task1',
                task1_essay or "", task1_prompt, r1
            )

            # ---------- TASK 2 ----------
            r2 = self.evaluate(
                task2_essay or "", 'task2', task2_prompt,
                None, None, session_id,
                user_id=user_id,
            )
            result['task2_band'] = r2.get('overall_band', 0)
            result['task2_feedback'] = r2.get('feedback', '')
            result['task2_detailed'] = r2

            self.service.save_essay(
                user_id, fk_test_id, 'task2',
                task2_essay or "", task2_prompt, r2
            )

            # ---------- OVERALL BAND ----------
            t1 = result['task1_band']
            t2 = result['task2_band']
            raw_overall = (t1 + 2 * t2) / 3
            overall = round(raw_overall * 2) / 2

            result['overall_band'] = overall
            result['user_task1_band'] = t1
            result['user_task2_band'] = t2
            result['user_overall_band'] = overall

            # ---------- OPTIONAL: UPGRADED ESSAYS ----------
            if auto_generate:
                result['upgraded1'] = self._build_upgrade_block(
                    essay=task1_essay,
                    band=t1,
                    task_type='task1',
                    prompt=task1_prompt,
                    chart_data=task1_chart_data,
                    preserve_user_essay=preserve_user_essay,
                )
                result['upgraded2'] = self._build_upgrade_block(
                    essay=task2_essay,
                    band=t2,
                    task_type='task2',
                    prompt=task2_prompt,
                    chart_data=None,
                    preserve_user_essay=preserve_user_essay,
                )

            # ---------- UPDATE GENERATION STATE ----------
            if session_id:
                try:
                    state = GenerationState.query.filter_by(
                        user_id=user_id_int, # int
                        test_id=session_id,
                        module='ielts_writing'
                    ).first()

                    if state:
                        state.status = 'completed'
                        state.completed_at = datetime.now(timezone.utc)
                        self.db.session.commit()
                except Exception as e:
                    logger.warning(f"Could not update generation state: {e}")

            logger.info(
                f"Submit complete: user={user_id_int}, fk_test_id={fk_test_id}, "
                f"overall={overall}"
            )
            return result

        except Exception as e:
            logger.exception(f"Submit failed: {e}")
            result['error'] = str(e)
            result['success'] = False
            return result

    def _build_upgrade_block(self, essay: str, band: float, task_type: str,
                             prompt: str = "", chart_data: dict = None,
                             preserve_user_essay: bool = True) -> Optional[Dict]:
        """Build an upgrade payload for a single essay."""
        try:
            if not essay or len(essay.strip().split()) < 50:
                return None

            upgrade_result = self.service.upgrade_essay(
                essay=essay,
                current_band=band,
                task_type=task_type,
                prompt=prompt or "",
                chart_data=chart_data,
                auto_generate=False,
                preserve_user_essay=preserve_user_essay,
            )
            if not upgrade_result or upgrade_result.get('error'):
                return None

            if 'is_model_essay' not in upgrade_result:
                upgrade_result['is_model_essay'] = bool(
                    upgrade_result.get('short_response', False)
                    or upgrade_result.get('upgrader') in ('model-essay-for-short-response',
                                                          'ai-chart-matched',
                                                          'ai-offtopic-fixed')
                )
            return upgrade_result
        except Exception as e:
            logger.warning(f"[submit] Could not build upgrade block for {task_type}: {e}")
            return None

    # ==================== OTHER METHODS ====================

    @rate_limit(max_requests=30, window_seconds=60)
    def evaluate_single(self, user_id: str, test_id, essay: str,
                        task_type: str, prompt: str = "",
                        chart_data: dict = None, session_id: str = None) -> Dict:
        """Evaluate a single essay and save to repository"""
        try:
            result = self.evaluate(
                essay, task_type, prompt, chart_data,
                expected_features=None, session_id=session_id,
                user_id=user_id,
            )

            self.service.save_essay(
                user_id, _coerce_test_id(test_id), task_type,
                essay, prompt, result
            )

            logger.info(f"Single evaluation saved: user={user_id}, task={task_type}")
            return result

        except Exception as e:
            logger.exception(f"Single evaluation failed: {e}")
            return {'error': str(e), 'overall_band': 0, 'success': False}

    @rate_limit(max_requests=50, window_seconds=60)
    def history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """Get user's essay history"""
        try:
            return self.service.get_history(user_id, limit)
        except Exception as e:
            logger.exception(f"History failed: {e}")
            return []

    @rate_limit(max_requests=30, window_seconds=60)
    def get_progress(self, user_id: str) -> Dict:
        """Get user's progress statistics"""
        try:
            return self.service.get_progress(user_id)
        except Exception as e:
            logger.exception(f"Progress failed: {e}")
            return {
                'error': str(e),
                'total_essays': 0,
                'average_band': 0,
                'task1_count': 0,
                'task2_count': 0
            }

    @rate_limit(max_requests=100, window_seconds=60)
    def get_test(self, test_id: int) -> Optional[Dict]:
        """Get test by ID"""
        try:
            return self.service.repo.get_test(test_id)
        except Exception as e:
            logger.exception(f"Get test failed: {e}")
            return None

    @rate_limit(max_requests=100, window_seconds=60)
    def get_essay(self, essay_id: int) -> Optional[Dict]:
        """Get essay by ID"""
        try:
            return self.service.get_essay(essay_id)
        except Exception as e:
            logger.exception(f"Get essay failed: {e}")
            return None

    @rate_limit(max_requests=20, window_seconds=60)
    def get_session_info(self, session_id: str) -> Dict:
        """Get current session information"""
        try:
            return self.service.get_session_info(session_id)
        except Exception as e:
            logger.exception(f"Get session failed: {e}")
            return {'error': str(e), 'session_id': session_id}

    @rate_limit(max_requests=20, window_seconds=60)
    def clear_session(self, session_id: str) -> Dict:
        """Clear a session"""
        try:
            success = self.service.clear_session(session_id)
            return {'success': success, 'session_id': session_id}
        except Exception as e:
            logger.exception(f"Clear session failed: {e}")
            return {'success': False, 'error': str(e)}

    @rate_limit(max_requests=10, window_seconds=60)
    def compare_essays(self, essay1: str, essay2: str, task_type: str = "task2",
                       prompt: str = "") -> Dict:
        """Compare two essays"""
        try:
            result1 = self.evaluate(essay1, task_type, prompt)
            result2 = self.evaluate(essay2, task_type, prompt)

            comparison = {
                'essay1_band': result1.get('overall_band', 0),
                'essay2_band': result2.get('overall_band', 0),
                'band_difference': round(result2.get('overall_band', 0) - result1.get('overall_band', 0), 1),
                'essay1_feedback': result1.get('feedback', ''),
                'essay2_feedback': result2.get('feedback', ''),
                'essay1_word_count': result1.get('word_count', 0),
                'essay2_word_count': result2.get('word_count', 0),
                'improvements': []
            }

            if result2.get('vocabulary_score', 0) > result1.get('vocabulary_score', 0):
                comparison['improvements'].append('Vocabulary improved')
            if result2.get('cohesion_score', 0) > result1.get('cohesion_score', 0):
                comparison['improvements'].append('Cohesion improved')
            if result2.get('grammar_score', 0) > result1.get('grammar_score', 0):
                comparison['improvements'].append('Grammar improved')

            return comparison

        except Exception as e:
            logger.exception(f"Compare essays failed: {e}")
            return {'error': str(e)}

    @rate_limit(max_requests=20, window_seconds=60)
    def get_generation_status(self, test_id: str) -> Dict:
        """Check the status of a generation process"""
        try:
            state = GenerationState.query.filter_by(
                test_id=test_id,
                module='ielts_writing'
            ).first()

            if not state:
                return {
                    'success': False,
                    'error': f'Generation state not found for test_id: {test_id}'
                }

            progress = 0
            if state.total_sections > 0:
                progress = int((len(state.generated_sections or []) / state.total_sections) * 100)

            return {
                'success': True,
                'test_id': state.test_id,
                'status': state.status,
                'progress': progress,
                'generated_sections': state.generated_sections or [],
                'total_sections': state.total_sections,
                'created_at': state.created_at.isoformat() if state.created_at else None,
                'updated_at': state.updated_at.isoformat() if state.updated_at else None,
                'is_complete': len(state.generated_sections or []) >= state.total_sections,
                'has_error': state.status == 'failed',
                'error_message': state.error_message
            }

        except Exception as e:
            logger.exception(f"Get generation status failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    @rate_limit(max_requests=10, window_seconds=60)
    def cleanup_old_states(self, hours: int = 48):
        """Delete old generation states"""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            deleted = GenerationState.query.filter(
                GenerationState.updated_at < cutoff,
                GenerationState.status.in_(['completed', 'failed', 'abandoned'])
            ).delete()

            self.db.session.commit()
            logger.info(f"Cleaned up {deleted} old generation states (older than {hours} hours)")

            return {
                'success': True,
                'deleted_count': deleted
            }

        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")
            self.db.session.rollback()
            return {
                'success': False,
                'error': str(e)
            }

    # ==================== MODULE INTEGRATION ====================

    def get_blueprint(self):
        """Get Flask Blueprint for this API."""
        try:
            from flask import Blueprint, request, jsonify

            bp = Blueprint('writing_api', __name__, url_prefix='/api/writing')

            def _resolve_user_id(data: dict):
                return (data.get('user_id')
                        or _current_user_id_from_session()
                        or None)

            @bp.route('/start', methods=['POST'])
            def start_test_route():
                data = request.json or {}
                user_id = _resolve_user_id(data)
                result = self.start_test(
                    difficulty=data.get('difficulty', 'medium'),
                    topic=data.get('topic'),
                    exam_type=data.get('exam_type', 'ielts'),
                    user_id=user_id,
                    resume=data.get('resume', True),
                    force_new=data.get('force_new', False),
                    auto_generate=data.get('auto_generate', True),
                    preserve_user_essay=data.get('preserve_user_essay', True)
                )
                return jsonify(result)

            @bp.route('/task1', methods=['POST'])
            def task1_route():
                data = request.json or {}
                user_id = _resolve_user_id(data)
                result = self.get_task1(
                    difficulty=data.get('difficulty', 'medium'),
                    topic=data.get('topic'),
                    user_id=user_id
                )
                return jsonify(result)

            @bp.route('/task2', methods=['POST'])
            def task2_route():
                data = request.json or {}
                user_id = _resolve_user_id(data)
                result = self.get_task2(
                    difficulty=data.get('difficulty', 'medium'),
                    topic=data.get('topic'),
                    user_id=user_id
                )
                return jsonify(result)

            @bp.route('/evaluate', methods=['POST'])
            def evaluate_route():
                data = request.json or {}
                user_id = _resolve_user_id(data)
                result = self.evaluate(
                    essay=data.get('essay', ''),
                    task_type=data.get('task_type', 'task2'),
                    prompt=data.get('prompt', ''),
                    chart_data=data.get('chart_data'),
                    session_id=data.get('session_id'),
                    user_id=user_id
                )
                return jsonify(result)

            @bp.route('/upgrade', methods=['POST'])
            def upgrade_route():
                data = request.json or {}
                result = self.upgrade(
                    essay=data.get('essay', ''),
                    current_band=data.get('current_band', 5.5),
                    task_type=data.get('task_type', 'task2'),
                    prompt=data.get('prompt', ''),
                    chart_data=data.get('chart_data'),
                    auto_generate=data.get('auto_generate', False),
                    preserve_user_essay=data.get('preserve_user_essay', True)
                )
                return jsonify(result)

            @bp.route('/upgrade_async', methods=['POST'])
            def upgrade_async_route():
                data = request.json or {}
                result = self.upgrade_async(
                    essay=data.get('essay', ''),
                    current_band=data.get('current_band', 5.5),
                    task_type=data.get('task_type', 'task2'),
                    prompt=data.get('prompt', ''),
                    chart_data=data.get('chart_data'),
                    auto_generate=data.get('auto_generate', False),
                    preserve_user_essay=data.get('preserve_user_essay', True)
                )
                return jsonify(result)

            @bp.route('/submit', methods=['POST'])
            def submit_route():
                data = request.json or {}
                user_id = _resolve_user_id(data)
                if not user_id:
                    return jsonify({
                        'error': 'Missing user_id (not in body and no authenticated session)',
                        'success': False,
                    }), 401

                result = self.submit(
                    user_id=user_id,
                    test_id=data.get('test_id'),
                    task1_essay=data.get('task1_essay', ''),
                    task1_prompt=data.get('task1_prompt', ''),
                    task1_chart_data=data.get('task1_chart_data'),
                    task1_expected_features=data.get('task1_expected_features'),
                    task2_essay=data.get('task2_essay', ''),
                    task2_prompt=data.get('task2_prompt', ''),
                    session_id=data.get('session_id'),
                    auto_generate=data.get('auto_generate', False),
                    preserve_user_essay=data.get('preserve_user_essay', True),
                )
                return jsonify(result)

            @bp.route('/status/<test_id>', methods=['GET'])
            def status_route(test_id):
                result = self.get_generation_status(test_id)
                return jsonify(result)

            @bp.route('/history/<user_id>', methods=['GET'])
            def history_route(user_id):
                limit = request.args.get('limit', 20, type=int)
                result = self.history(user_id, limit)
                return jsonify(result)

            @bp.route('/progress/<user_id>', methods=['GET'])
            def progress_route(user_id):
                result = self.get_progress(user_id)
                return jsonify(result)

            return bp

        except ImportError:
            logger.warning("Flask not available for blueprint")
            return None


# ==================== FACTORY FUNCTIONS ====================

def create_writing_api(ai_engine=None, db=None):
    """Factory function to create WritingAPI with AI engine and database"""
    if not ai_engine:
        raise ValueError(" AI Engine required to create WritingAPI")

    if not db:
        raise ValueError(" Database session required for resumable generation")

    return WritingAPI(ai_engine, db)


__all__ = [
    'WritingAPI',
    'create_writing_api',
    'rate_limit',
]