"""Public API for IELTS Speaking module - PURE AI, NO FALLBACKS
with Resumable Generation. Pool-native (no legacy TestBank).

 v6 POOL-NATIVE:
  - Removed legacy TEST_BANK_MANAGER save path.
  - Removed bank_id tracking (pool handles per-user seen tracking).
  - Removed UserTestBankUsage marking.
  - Kept: retake, resume, full-test snapshot, free conversation,
    evaluation and scoring.

Pool persistence is handled by the caller (app.py) via
`ielts_test_pool_manager.get_or_generate()` for module='ielts_speaking'.
"""

import json
import copy
import time
import hashlib
import logging
import re
import uuid
import os
import numpy as np
from typing import Dict, Optional, Any, List
from functools import wraps
from datetime import datetime, timedelta, timezone

from flask import session as flask_session

# ============================================
# DYNAMIC MODEL IMPORTS
# ============================================
from models import db as _global_db, GenerationState
from models import (
    get_test_session_model,
    get_user_model,
)

from modules.ielts.subscription_manager import get_ielts_subscription_manager

_MODULE = 'ielts'
logger = logging.getLogger(__name__)

MIN_WORDS_PER_RESPONSE = 20

_USAGE_INCREMENT_CACHE = {}
_USAGE_CACHE_TTL_SECONDS = 300

_FT_PHASE_ORDER = ['listening', 'reading', 'writing', 'speaking']


# ============================================
# FULL-TEST HELPERS
# ============================================

def _ft_get_state():
    try:
        return flask_session.get('full_ielts_test')
    except Exception:
        return None


def _ft_next_phase(state):
    for p in _FT_PHASE_ORDER:
        if not state.get('completed_sections', {}).get(p):
            return p
    return None


def _ft_save_speaking_phase(state, band, answers=None):
    try:
        state.setdefault('answers', {})['speaking'] = answers or {}
        state.setdefault('scores', {})['speaking'] = {
            'band_score': band,
            'score_pct': None,
            'correct': None,
            'total': None,
        }
        state.setdefault('completed_sections', {})['speaking'] = True

        sched = state.get('speaking_schedule') or {}
        sched['completed_at'] = datetime.now(timezone.utc).isoformat()
        state['speaking_schedule'] = sched

        state['phase'] = _ft_next_phase(state) or 'done'

        flask_session['full_ielts_test'] = state
        flask_session.modified = True

        return state['phase']
    except Exception as e:
        logger.error(f"Failed to save speaking phase to full test session: {e}")
        return None


def _ft_load_speaking_snapshot():
    try:
        ft = flask_session.get('full_ielts_test') or {}
        parent_id = ft.get('parent_session_id')
        if not parent_id:
            return None, None

        TestSession = get_test_session_model(_MODULE)
        parent = _global_db.session.get(TestSession, int(parent_id))
        if not parent:
            return None, None

        td = parent.test_data or {}
        if isinstance(td, str):
            try:
                td = json.loads(td)
            except Exception:
                td = {}

        snap = (td.get('snapshot') or {}).get('speaking')
        if not snap:
            return None, parent

        if not (snap.get('part1') or snap.get('part2')):
            return None, parent

        return snap, parent
    except Exception as e:
        logger.warning(f"Could not load full-test speaking snapshot: {e}")
        return None, None


# ============================================
# RETAKE — clone last completed speaking test
# ============================================

def _validate_speaking_test_data(test_data) -> bool:
    if not test_data or not isinstance(test_data, dict):
        return False
    return bool(test_data.get('part1') and test_data.get('part2'))


def _clone_completed_speaking_test(user_id_int: int, difficulty: str = 'medium'):
    if not user_id_int or user_id_int == 0:
        return None

    try:
        TestSession = get_test_session_model(_MODULE)
    except Exception as e:
        logger.error(f"Retake: cannot get TestSession model: {e}")
        return None

    try:
        last_completed = TestSession.query.filter(
            TestSession.user_id == user_id_int,
            TestSession.test_type == 'speaking',
            TestSession.status == 'completed'
        ).order_by(TestSession.last_updated.desc()).first()
    except Exception as e:
        logger.error(f"Retake: query failed for user={user_id_int} (speaking): {e}")
        return None

    if not last_completed:
        logger.info(f"Retake: no prior completed speaking test for user {user_id_int}")
        return None

    src = last_completed.test_data
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except Exception:
            logger.warning(f"Retake: cannot parse test_data for session {last_completed.id}")
            return None

    if not _validate_speaking_test_data(src):
        logger.warning(
            f"Retake: session {last_completed.id} has unusable speaking data "
            f"(missing part1/part2) — skipping retake"
        )
        return None

    cloned_data = copy.deepcopy(src)
    cloned_data['retake_of'] = last_completed.id
    cloned_data['retake_at'] = datetime.now(timezone.utc).isoformat()

    try:
        new_session = TestSession(
            user_id=user_id_int,
            test_type='speaking',
            difficulty=difficulty or last_completed.difficulty or 'medium',
            test_data=cloned_data,
            status='in_progress',
            answers_so_far={},
            current_question_index=0,
            start_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        _global_db.session.add(new_session)
        _global_db.session.commit()
    except Exception as e:
        _global_db.session.rollback()
        logger.error(f"Retake: could not create clone session: {e}", exc_info=True)
        return None

    logger.info(
        f" [speaking] retake — new session {new_session.id} "
        f"cloned from completed session {last_completed.id}"
    )

    return {
        'success': True,
        'session_id': new_session.id,
        'part1': cloned_data.get('part1'),
        'part2': cloned_data.get('part2'),
        'part3': cloned_data.get('part3'),
        'difficulty': new_session.difficulty,
        'source': 'retake',
        'retake': True,
        'original_test_id': last_completed.id,
        'message': 'Retaking your previous speaking test.',
    }


# ============================================
# RATE LIMITING
# ============================================

_rate_limit_store = {}


def rate_limit(max_requests: int = 30, window_seconds: int = 60):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            client_id = kwargs.get('user_id', 'anonymous')
            now = datetime.now()
            key = f"{client_id}:{func.__name__}"
            for k in list(_rate_limit_store.keys()):
                if now - _rate_limit_store[k]['timestamp'] > timedelta(seconds=window_seconds):
                    del _rate_limit_store[k]
            if key in _rate_limit_store:
                if _rate_limit_store[key]['count'] >= max_requests:
                    logger.warning(f"Rate limit exceeded for {client_id} on {func.__name__}")
                    return {'error': f'Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds.'}
                _rate_limit_store[key]['count'] += 1
                _rate_limit_store[key]['timestamp'] = now
            else:
                _rate_limit_store[key] = {'count': 1, 'timestamp': now}
            return func(self, *args, **kwargs)
        return wrapper
    return decorator


# ============================================
# HELPERS
# ============================================

def _safe_set_json_column(obj, attr, new_value):
    setattr(obj, attr, new_value)


def _usage_cache_key(module: str, user_id, session_id) -> str:
    return f"{module}:{user_id}:{session_id or 'nosession'}"


def _is_usage_duplicate(key: str) -> bool:
    now_ts = time.time()
    for k in list(_USAGE_INCREMENT_CACHE.keys()):
        if now_ts - _USAGE_INCREMENT_CACHE[k] > _USAGE_CACHE_TTL_SECONDS:
            del _USAGE_INCREMENT_CACHE[k]
    return key in _USAGE_INCREMENT_CACHE


def _mark_usage_done(key: str) -> None:
    _USAGE_INCREMENT_CACHE[key] = time.time()


def _increment_speaking_usage(api, user_id, session_id=None, module: str = _MODULE) -> Dict:
    result = {'ok': False, 'branch': None, 'skipped_reason': None}
    if not user_id or user_id == 'anonymous':
        result['skipped_reason'] = 'anonymous'
        return result

    try:
        user_id_int = api._normalize_user_id(user_id)
        if user_id_int == 0:
            result['skipped_reason'] = 'invalid_user_id'
            return result

        cache_key = _usage_cache_key(module, user_id_int, session_id)
        if _is_usage_duplicate(cache_key):
            logger.info(f" Speaking usage already counted for {cache_key} — skipping (idempotency)")
            result['skipped_reason'] = 'idempotent'
            return result

        from models import get_subscription_model
        Subscription = get_subscription_model(module)

        sub = Subscription.query.filter_by(user_id=user_id_int).first()
        if sub is None:
            try:
                from models import create_default_subscription_for_user
                sub = create_default_subscription_for_user(user_id_int, module)
            except Exception as e:
                logger.warning(f"Could not create default subscription for user {user_id_int}: {e}")

        has_active = False
        if sub is not None:
            sub_end = sub.subscription_end
            if sub_end and sub_end.tzinfo is None:
                sub_end = sub_end.replace(tzinfo=timezone.utc)
            has_active = bool(
                sub.status == 'active'
                and sub_end
                and sub_end > datetime.now(timezone.utc)
                and (sub.plan or '').strip().lower()
                    not in ('', 'free', 'trial', 'none', 'default')
                and (sub.tests_remaining or 0) > 0
            )

        if has_active:
            sub.tests_remaining = (sub.tests_remaining or 0) - 1
            sub.tests_taken = (sub.tests_taken or 0) + 1
            api.db.session.commit()
            logger.info(
                f" Subscription test used for speaking (user {user_id_int}), "
                f"{sub.tests_remaining} left"
            )
            result.update(ok=True, branch='paid')
        else:
            if api.sub_manager is not None:
                api.sub_manager.increment_free_usage(user_id_int, 'speaking')
                logger.info(f" Free usage incremented for speaking (user {user_id_int})")
                result.update(ok=True, branch='free')
            else:
                logger.warning(" No subscription manager — usage not tracked")
                result['skipped_reason'] = 'no_sub_manager'
                return result

        _mark_usage_done(cache_key)
        return result
    except Exception as e:
        logger.error(f" Failed to handle speaking usage for user {user_id}: {e}", exc_info=True)
        result['skipped_reason'] = f'error: {e}'
        return result


# ============================================
# MAIN API CLASS
# ============================================

class IELTSSpeakingAPI:
    def __init__(self, ai_engine=None, db=None, repository=None):
        if not ai_engine:
            raise ValueError(" AI Engine is required. No fallback templates available.")

        self.ai_engine = ai_engine
        self.db = db
        self.module = _MODULE

        from .transcriber import create_transcriber, WhisperTranscriber
        from .pronunciation import create_pronunciation_scorer, PronunciationScorer, PronunciationResult
        from .grammar import create_grammar_analyzer, SpokenGrammarAnalyzer
        from .coherence import create_coherence_analyzer, CoherenceAnalyzer
        from .emotion import create_emotion_detector, VoiceEmotionDetector
        from .scoring import create_band_calculator, IELTSBandCalculator
        from .test_generator import create_speaking_test_generator, create_speaking_test, SpeakingTest
        from .repository import create_speaking_repository, SpeakingRepository

        try:
            self.transcriber = create_transcriber()
        except Exception as e:
            logger.warning(f"Transcriber factory failed: {e}. Falling back to class.")
            self.transcriber = WhisperTranscriber()

        try:
            self.pronunciation = create_pronunciation_scorer()
        except Exception as e:
            logger.warning(f"Pronunciation factory failed: {e}. Falling back to class.")
            self.pronunciation = PronunciationScorer()

        try:
            self.grammar = create_grammar_analyzer()
        except Exception as e:
            logger.warning(f"Grammar factory failed: {e}. Falling back to class.")
            self.grammar = SpokenGrammarAnalyzer()

        try:
            self.coherence = create_coherence_analyzer()
        except Exception as e:
            logger.warning(f"Coherence factory failed: {e}. Falling back to class.")
            self.coherence = CoherenceAnalyzer()

        try:
            self.emotion = create_emotion_detector()
        except Exception as e:
            logger.warning(f"Emotion factory failed: {e}. Falling back to class.")
            self.emotion = VoiceEmotionDetector()

        try:
            self.scoring = create_band_calculator()
        except Exception as e:
            logger.warning(f"Scoring factory failed: {e}. Falling back to class.")
            self.scoring = IELTSBandCalculator()

        self.test_generator = SpeakingTest(ai_engine)

        self.repository = repository
        self._repo_initialized = False
        self.sid = hashlib.md5(str(time.time()).encode()).hexdigest()[:12]

        try:
            self.sub_manager = get_ielts_subscription_manager(db)
        except Exception as e:
            logger.warning(f"Subscription manager init failed: {e}")
            self.sub_manager = None

        logger.info("IELTSSpeakingAPI initialized with pure AI mode and subscription manager")

    def _ensure_repository(self):
        if not self._repo_initialized and self.repository is None:
            try:
                from .repository import create_speaking_repository
                self.repository = create_speaking_repository()
                self._repo_initialized = True
            except Exception as e:
                logger.warning(f"Repository initialization failed: {e}")
                self.repository = None
                self._repo_initialized = True

    def _normalize_user_id(self, user_id) -> int:
        if user_id is None or user_id == 'anonymous':
            return 0
        try:
            return int(user_id)
        except (ValueError, TypeError):
            return 0

    def _get_user_id_from_request(self, data: Dict) -> str:
        uid = data.get('user_id')
        if not uid:
            try:
                from flask_login import current_user
                if hasattr(current_user, 'id') and current_user.is_authenticated:
                    uid = str(current_user.id)
            except Exception:
                pass
        return uid or 'anonymous'

    # ============================================
    # GENERATION STATE MANAGEMENT
    # ============================================

    def _get_or_create_state(self, user_id, test_id: str = None,
                             difficulty: str = "medium",
                             part: int = 1,
                             topic: str = None) -> Dict:
        if not self.db:
            return {'success': False, 'error': 'Database not available'}

        try:
            user_id_int = self._normalize_user_id(user_id)

            if test_id:
                state = GenerationState.query.filter_by(
                    user_id=user_id_int,
                    test_id=test_id,
                    module='ielts_speaking'
                ).first()
                if state:
                    return {'success': True, 'state': state, 'exists': True, 'test_id': test_id}

            if not test_id:
                test_id = f"s_{uuid.uuid4().hex[:12]}"

            new_state = GenerationState(
                user_id=user_id_int,
                module='ielts_speaking',
                test_id=test_id,
                difficulty=difficulty,
                total_sections=3,
                generated_sections=[],
                current_section=1,
                status='in_progress'
            )

            self.db.session.add(new_state)
            self.db.session.commit()

            logger.info(f"New speaking generation state created: {test_id} for user {user_id_int}")

            return {
                'success': True,
                'state': new_state,
                'exists': False,
                'test_id': test_id
            }

        except Exception as e:
            logger.exception(f"Failed to get/create state: {e}")
            if self.db:
                self.db.session.rollback()
            return {'success': False, 'error': str(e)}

    def _update_state_section(self, state, section_num: int,
                              section_data: Dict = None,
                              status: str = None) -> bool:
        if not self.db:
            return False

        try:
            sections = list(state.generated_sections or [])
            if section_num not in sections:
                sections.append(section_num)
                sections = sorted(sections)
            state.generated_sections = sections

            if section_data:
                section_map = dict(state.section_data or {})
                section_map[str(section_num)] = section_data
                state.section_data = section_map

            state.current_section = section_num + 1
            state.updated_at = datetime.now(timezone.utc)

            if status:
                state.status = status

            if len(state.generated_sections) >= (state.total_sections or 3):
                state.status = 'completed'
                state.completed_at = datetime.now(timezone.utc)

            self.db.session.commit()
            return True

        except Exception as e:
            logger.exception(f"Failed to update state: {e}")
            self.db.session.rollback()
            return False

    def _resume_from_state(self, state) -> Dict:
        try:
            generated = list(state.generated_sections or [])
            total = state.total_sections or 3

            def _build_test_data():
                sd = state.section_data or {}
                return {
                    'part1': sd.get('1'),
                    'part2': sd.get('2'),
                    'part3': sd.get('3'),
                }

            if len(generated) >= total:
                state.status = 'completed'
                state.completed_at = datetime.now(timezone.utc)
                self.db.session.commit()

                test_data = _build_test_data()
                return {
                    'success': True,
                    'resumed': True,
                    'completed': True,
                    'test_data': test_data,
                    'part1': test_data.get('part1'),
                    'part2': test_data.get('part2'),
                    'part3': test_data.get('part3'),
                    'test_id': state.test_id,
                    'message': 'All parts already generated'
                }

            missing_sections = [i for i in range(1, total + 1) if i not in generated]
            difficulty = state.difficulty or 'medium'

            logger.info(f"Resuming speaking state: generated={generated}, missing={missing_sections}")

            for section_num in missing_sections:
                try:
                    if section_num == 1:
                        result = self.test_generator.generate_part1(topic=None, difficulty=difficulty)
                    elif section_num == 2:
                        result = self.test_generator.generate_part2(category=None, difficulty=difficulty)
                    elif section_num == 3:
                        result = self.test_generator.generate_part3(part2_topic=None, difficulty=difficulty)
                    else:
                        continue

                    if result and not result.get('error'):
                        self._update_state_section(state, section_num, result)
                        logger.info(f"Part {section_num} generated via resume for state {state.test_id}")
                    else:
                        err = result.get('error') if result else 'unknown error'
                        state.status = 'failed'
                        state.error_message = f"Failed to generate Part {section_num}: {err}"
                        self.db.session.commit()
                        return {
                            'success': False,
                            'error': f"Failed to generate Part {section_num}: {err}",
                            'resumed': True
                        }
                except Exception as e:
                    state.status = 'failed'
                    state.error_message = f"Failed to generate Part {section_num}: {e}"
                    self.db.session.commit()
                    return {'success': False, 'error': str(e), 'resumed': True}

            state.status = 'completed'
            state.completed_at = datetime.now(timezone.utc)
            state.test_data = _build_test_data()
            self.db.session.commit()

            test_data = _build_test_data()
            return {
                'success': True,
                'resumed': True,
                'completed': True,
                'test_data': test_data,
                'part1': test_data.get('part1'),
                'part2': test_data.get('part2'),
                'part3': test_data.get('part3'),
                'test_id': state.test_id,
                'message': 'Generation resumed and completed successfully'
            }

        except Exception as e:
            logger.exception(f"Resume failed: {e}")
            try:
                state.status = 'failed'
                state.error_message = str(e)
                self.db.session.commit()
            except Exception:
                pass
            return {'success': False, 'error': str(e), 'resumed': True}

    # ============================================
    # PUBLIC API METHODS
    # ============================================

    @rate_limit(max_requests=30, window_seconds=60)
    def analyze(self, audio=None, sr: int = 16000, text: Optional[str] = None,
                topic: Optional[str] = None, user_id: str = "anonymous",
                test_id: str = None) -> Dict[str, Any]:
        t0 = time.time()

        if user_id and user_id != 'anonymous' and self.db:
            try:
                user_id_int = self._normalize_user_id(user_id)
                existing_state = GenerationState.query.filter_by(
                    user_id=user_id_int,
                    module='ielts_speaking',
                    status='in_progress'
                ).first()

                if existing_state:
                    logger.info(f"Found existing speaking state for user {user_id}, resuming...")
                    resume_result = self._resume_from_state(existing_state)
                    if resume_result.get('success') and resume_result.get('test_data'):
                        return {
                            'success': True,
                            'resumed': True,
                            'test_data': resume_result.get('test_data'),
                            'part1': resume_result.get('part1'),
                            'part2': resume_result.get('part2'),
                            'part3': resume_result.get('part3'),
                            'test_id': resume_result.get('test_id'),
                            'message': 'Resumed from previous session'
                        }
            except Exception as e:
                logger.warning(f"State check failed: {e}")

        if text is None and audio is not None:
            try:
                text = self.transcriber.transcribe(audio, sr) or ""
            except Exception as e:
                logger.warning(f"Transcription failed: {e}")
                text = ""

        text = (text or "").strip()
        word_count = len(text.split()) if text else 0

        if not text or word_count == 0:
            return self._empty_analysis_result(
                reason="BAND 0 — No speech detected / empty response.",
                session_id=self.sid, user_id=user_id, test_id=test_id,
            )

        if word_count < MIN_WORDS_PER_RESPONSE:
            return self._empty_analysis_result(
                reason=f"BAND 0 — Response too short ({word_count} words). "
                       f"At least {MIN_WORDS_PER_RESPONSE} words required.",
                session_id=self.sid, user_id=user_id, test_id=test_id,
                word_count=word_count,
            )

        if audio is not None and len(audio) > 0:
            try:
                pron_result = self.pronunciation.score(audio, text, sr)
                pron_score = pron_result.score
            except Exception as e:
                logger.warning(f"Pronunciation failed: {e}")
                pron_result = None
                pron_score = 0.0
        else:
            pron_result = None
            pron_score = 0.0

        try:
            coh_result = self.coherence.analyze(text, topic)
            coh_score = coh_result.score
        except Exception as e:
            logger.warning(f"Coherence failed: {e}")
            coh_result = None
            coh_score = 0.0

        try:
            gram_result = self.grammar.analyze(text)
            gram_score = gram_result.score
        except Exception as e:
            logger.warning(f"Grammar failed: {e}")
            gram_result = None
            gram_score = 0.0

        words_lower = text.lower().split()
        unique_words = len(set(words_lower))
        ttr = unique_words / word_count if word_count > 0 else 0
        lex_score = min(8.5, max(0.0, ttr * 12 + 2))

        if audio is not None and len(audio) > 0:
            try:
                emotion_result = self.emotion.analyze(audio, sr)
            except Exception as e:
                logger.warning(f"Emotion failed: {e}")
                emotion_result = None
        else:
            emotion_result = None

        band_result = self.scoring.calculate(
            fluency=coh_score, lexical=lex_score, grammar=gram_score,
            pronunciation=pron_score, word_count=word_count,
            audio_duration=(len(audio) / sr) if audio is not None and sr > 0 else 0.0,
        )

        result = {
            **band_result,
            'text': text,
            'word_count': word_count,
            'session_id': self.sid,
            'processing_time': round(time.time() - t0, 2),
            'success': True,
            'pronunciation_details': {
                'phoneme_score': pron_result.phoneme_score if pron_result else 0.0,
                'vowel_score': pron_result.vowel_score if pron_result else 0.0,
                'stress_score': pron_result.stress_score if pron_result else 0.0,
                'rhythm_score': pron_result.rhythm_score if pron_result else 0.0,
                'intonation_score': pron_result.intonation_score if pron_result else 0.0,
            } if pron_result else {},
            'grammar_details': {
                'errors': gram_result.errors if (gram_result and hasattr(gram_result, 'errors')) else [],
                'error_density': gram_result.error_density if (gram_result and hasattr(gram_result, 'error_density')) else 0,
                'complexity': gram_result.complexity if (gram_result and hasattr(gram_result, 'complexity')) else 0,
            },
            'coherence_details': coh_result.details if (coh_result and hasattr(coh_result, 'details')) else {},
            'lexical_details': {
                'type_token_ratio': round(ttr, 3),
                'unique_words': unique_words,
            }
        }

        if emotion_result:
            result['emotion'] = {
                'primary': emotion_result.primary_emotion if hasattr(emotion_result, 'primary_emotion') else 'neutral',
                'confidence': emotion_result.emotions.get('confidence', 0) if hasattr(emotion_result, 'emotions') else 0,
                'nervousness': emotion_result.emotions.get('nervousness', 0) if hasattr(emotion_result, 'emotions') else 0,
                'hesitation': emotion_result.emotions.get('hesitation', 0) if hasattr(emotion_result, 'emotions') else 0,
            }

        self._ensure_repository()
        if self.repository:
            try:
                db_data = {
                    'session_id': self.sid,
                    'user_id': str(user_id) if user_id else 'anonymous',
                    'topic': topic,
                    'overall_band': band_result['overall_band'],
                    'fluency_coherence': coh_score,
                    'lexical_resource': lex_score,
                    'grammar_accuracy': gram_score,
                    'pronunciation': pron_score,
                    'response_text': text,
                    'word_count': word_count,
                    'duration_seconds': len(audio) / sr if audio is not None and sr > 0 else 0,
                    'feedback': str(band_result.get('band_descriptor', '')),
                    'improvement_tips': band_result.get('feedback', {}).get('tips', []) if isinstance(band_result.get('feedback'), dict) else [],
                }
                self.repository.save_session(db_data)
            except Exception as e:
                logger.warning(f"Failed to save session: {e}")

        if user_id and user_id != 'anonymous' and self.db and test_id:
            try:
                user_id_int = self._normalize_user_id(user_id)
                state = GenerationState.query.filter_by(
                    user_id=user_id_int, test_id=test_id, module='ielts_speaking'
                ).first()
                if state:
                    state.status = 'completed'
                    state.completed_at = datetime.now(timezone.utc)
                    state.test_data = result
                    self.db.session.commit()
            except Exception as e:
                logger.warning(f"Could not update generation state: {e}")

        return result

    def _empty_analysis_result(self, reason: str, session_id: str,
                               user_id: str = 'anonymous', test_id: str = None,
                               word_count: int = 0) -> Dict[str, Any]:
        band_result = self.scoring.calculate(
            fluency=0.0, lexical=0.0, grammar=0.0, pronunciation=0.0,
            word_count=word_count, audio_duration=0.0,
        )
        return {
            **band_result,
            'text': '',
            'word_count': word_count,
            'session_id': session_id,
            'processing_time': 0,
            'success': True,
            'empty_response': True,
            'reason': reason,
            'pronunciation_details': {},
            'grammar_details': {},
            'coherence_details': {},
            'lexical_details': {'type_token_ratio': 0, 'unique_words': 0},
        }

    @rate_limit(max_requests=20, window_seconds=60)
    def analyze_file(self, filepath: str, topic: Optional[str] = None,
                     user_id: str = "anonymous") -> Dict:
        try:
            import librosa
            audio, sr = librosa.load(filepath, sr=16000)
            return self.analyze(audio=audio, sr=sr, topic=topic, user_id=user_id)
        except Exception as e:
            logger.error(f"Failed to load audio file: {e}")
            return {'error': f'Failed to load audio: {str(e)}', 'success': False}

    # ============================================
    # GENERATE TEST (POOL-NATIVE v6)
    # ============================================

    @rate_limit(max_requests=30, window_seconds=60)
    def generate_test(self, difficulty: str = "medium", topic: str = None,
                      user_id: str = None, resume: bool = True,
                      force_new: bool = False,
                      retake: bool = False,
                      skip_subscription_check: bool = False) -> Dict:
        """
         POOL-NATIVE: no legacy TestBank use.
        Pool persistence is handled by the caller (app.py).
        This method only generates + saves a TestSession.
        """
        TestSession = get_test_session_model(_MODULE)

        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            user_id_int = self._normalize_user_id(user_id) if user_id else 0

            # ─── 0. RETAKE ───
            if retake and not force_new and user_id_int:
                cloned = _clone_completed_speaking_test(user_id_int, difficulty)
                if cloned:
                    logger.info(
                        f" [speaking] served via retake for user {user_id_int} "
                        f"(session={cloned['session_id']})"
                    )
                    return cloned
                logger.info(
                    f"Retake requested for speaking but no usable prior completed "
                    f"test — falling through to normal generation for user {user_id_int}"
                )

            # ─── 1. Resume check ───
            if user_id and resume and self.db and not retake:
                try:
                    existing_state = GenerationState.query.filter_by(
                        user_id=user_id_int,
                        module='ielts_speaking',
                        status='in_progress'
                    ).first()
                    if existing_state and not force_new:
                        logger.info(f"Found existing speaking state for user {user_id}, resuming...")
                        return self._resume_from_state(existing_state)
                except Exception as e:
                    logger.warning(f"Resume check failed: {e}")

            # ─── 2. Subscription gate ───
            if (not skip_subscription_check
                    and user_id and user_id != 'anonymous'
                    and self.sub_manager):
                try:
                    allowed, error, requires_sub = self.sub_manager.can_access_test(user_id_int, 'speaking')
                    if not allowed:
                        return {
                            'success': False,
                            'error': error,
                            'requires_subscription': requires_sub,
                            'redirect_to': '/subscription?module=ielts'
                        }
                except (ValueError, TypeError):
                    logger.warning(f"Invalid user_id type: {user_id}, skipping subscription check")
            elif skip_subscription_check:
                logger.info(" Skipping speaking subscription gate (full-test mode)")

            # ═══════════════════════════════════════════════════════
            # 3. GENERATE ON-DEMAND (pool handles persistence)
            # ═══════════════════════════════════════════════════════
            logger.info(f"🆕 [speaking] Generating fresh test for user {user_id}")
            result = self.test_generator.generate_complete_test(difficulty, topic)
            if not result or result.get('error'):
                raise ValueError((result or {}).get('error') or 'Empty test generation result')

            if user_id and user_id != 'anonymous' and self.db:
                state_result = self._get_or_create_state(
                    user_id=user_id, difficulty=difficulty, part=1, topic=topic
                )
                if state_result.get('success'):
                    state = state_result['state']
                    if result.get('part1'):
                        self._update_state_section(state, 1, result['part1'])
                    if result.get('part2'):
                        self._update_state_section(state, 2, result['part2'])
                    if result.get('part3'):
                        self._update_state_section(state, 3, result['part3'])
                    if len(state.generated_sections or []) >= 3:
                        state.status = 'completed'
                        state.completed_at = datetime.now(timezone.utc)
                        self.db.session.commit()
                    result['test_id'] = state.test_id

            result['source'] = 'on_demand'
            logger.info(
                f"Speaking test generated on-demand: "
                f"difficulty={difficulty}, user={user_id}"
            )
            return result

        except Exception as e:
            logger.exception(f"Generate test failed: {e}")
            return {
                'error': str(e),
                'success': False,
                'message': f"Failed to generate test: {str(e)}"
            }

    # ---------- INDIVIDUAL PART GENERATION ----------

    @rate_limit(max_requests=30, window_seconds=60)
    def generate_part1(self, topic: str = None, difficulty: str = "medium", user_id: str = None) -> Dict:
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            if user_id and user_id != 'anonymous' and self.sub_manager:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    allowed, error, requires_sub = self.sub_manager.can_access_test(user_id_int, 'speaking')
                    if not allowed:
                        return {'success': False, 'error': error,
                                'requires_subscription': requires_sub,
                                'redirect_to': '/subscription?module=ielts'}
                except (ValueError, TypeError):
                    pass

            if user_id and user_id != 'anonymous' and self.db:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    state = GenerationState.query.filter_by(
                        user_id=user_id_int, module='ielts_speaking', status='in_progress'
                    ).first()
                    if state and state.section_data and '1' in state.section_data:
                        return {**state.section_data['1'], 'from_cache': True, 'test_id': state.test_id}
                except Exception as e:
                    logger.warning(f"Cache lookup failed: {e}")

            result = self.test_generator.generate_part1(topic, difficulty)
            if result.get('error'):
                raise ValueError(result.get('error'))

            if user_id and user_id != 'anonymous' and self.db:
                state_result = self._get_or_create_state(user_id=user_id, difficulty=difficulty, part=1, topic=topic)
                if state_result.get('success'):
                    self._update_state_section(state_result['state'], 1, result)
                    result['test_id'] = state_result['test_id']

            return result
        except Exception as e:
            logger.exception(f"Generate Part 1 failed: {e}")
            return {'error': str(e), 'success': False, 'message': f"Failed to generate Part 1: {str(e)}"}

    @rate_limit(max_requests=30, window_seconds=60)
    def generate_part2(self, category: str = None, difficulty: str = "medium", user_id: str = None) -> Dict:
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            if user_id and user_id != 'anonymous' and self.sub_manager:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    allowed, error, requires_sub = self.sub_manager.can_access_test(user_id_int, 'speaking')
                    if not allowed:
                        return {'success': False, 'error': error,
                                'requires_subscription': requires_sub,
                                'redirect_to': '/subscription?module=ielts'}
                except (ValueError, TypeError):
                    pass

            if user_id and user_id != 'anonymous' and self.db:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    state = GenerationState.query.filter_by(
                        user_id=user_id_int, module='ielts_speaking', status='in_progress'
                    ).first()
                    if state and state.section_data and '2' in state.section_data:
                        return {**state.section_data['2'], 'from_cache': True, 'test_id': state.test_id}
                except Exception as e:
                    logger.warning(f"Cache lookup failed: {e}")

            result = self.test_generator.generate_part2(category, difficulty)
            if result.get('error'):
                raise ValueError(result.get('error'))

            if user_id and user_id != 'anonymous' and self.db:
                state_result = self._get_or_create_state(user_id=user_id, difficulty=difficulty, part=2, topic=category)
                if state_result.get('success'):
                    self._update_state_section(state_result['state'], 2, result)
                    result['test_id'] = state_result['test_id']

            return result
        except Exception as e:
            logger.exception(f"Generate Part 2 failed: {e}")
            return {'error': str(e), 'success': False, 'message': f"Failed to generate Part 2: {str(e)}"}

    @rate_limit(max_requests=30, window_seconds=60)
    def generate_part3(self, part2_topic: str = None, difficulty: str = "medium", user_id: str = None) -> Dict:
        try:
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            if user_id and user_id != 'anonymous' and self.sub_manager:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    allowed, error, requires_sub = self.sub_manager.can_access_test(user_id_int, 'speaking')
                    if not allowed:
                        return {'success': False, 'error': error,
                                'requires_subscription': requires_sub,
                                'redirect_to': '/subscription?module=ielts'}
                except (ValueError, TypeError):
                    pass

            if user_id and user_id != 'anonymous' and self.db:
                try:
                    user_id_int = self._normalize_user_id(user_id)
                    state = GenerationState.query.filter_by(
                        user_id=user_id_int, module='ielts_speaking', status='in_progress'
                    ).first()
                    if state and state.section_data and '3' in state.section_data:
                        return {**state.section_data['3'], 'from_cache': True, 'test_id': state.test_id}
                except Exception as e:
                    logger.warning(f"Cache lookup failed: {e}")

            result = self.test_generator.generate_part3(part2_topic, difficulty)
            if result.get('error'):
                raise ValueError(result.get('error'))

            if user_id and user_id != 'anonymous' and self.db:
                state_result = self._get_or_create_state(user_id=user_id, difficulty=difficulty, part=3, topic=part2_topic)
                if state_result.get('success'):
                    self._update_state_section(state_result['state'], 3, result)
                    result['test_id'] = state_result['test_id']

            return result
        except Exception as e:
            logger.exception(f"Generate Part 3 failed: {e}")
            return {'error': str(e), 'success': False, 'message': f"Failed to generate Part 3: {str(e)}"}

    # ============================================
    # EVALUATE A SINGLE RESPONSE
    # ============================================

    @rate_limit(max_requests=50, window_seconds=60)
    def evaluate_response(self, question: str, response: str, part: int = 1, user_id: str = None) -> Dict:
        response = (response or '').strip()
        word_count = len(response.split()) if response else 0

        if not response or word_count < MIN_WORDS_PER_RESPONSE:
            return {
                "overall": 0.0, "fluency": 0.0, "pronunciation": 0.0,
                "grammar": 0.0, "vocabulary": 0.0,
                "feedback": f"BAND 0 — Response too short ({word_count} words). "
                            f"At least {MIN_WORDS_PER_RESPONSE} words required.",
                "strengths": [], "weaknesses": ["Insufficient response"],
                "success": True, "empty_response": True,
            }

        prompt = f"""You are an IELTS Speaking examiner. Evaluate this response.

Question (Part {part}): {question}
Response: {response}

Score each criterion on a 0-9 scale (0 = no ability, 9 = expert).
Return ONLY valid JSON:
{{
    "overall": <number>,
    "fluency": <number>,
    "pronunciation": <number>,
    "grammar": <number>,
    "vocabulary": <number>,
    "feedback": "Brief feedback on the response",
    "strengths": ["Strength 1", "Strength 2"],
    "weaknesses": ["Weakness 1", "Weakness 2"]
}}"""

        try:
            resp = self.ai_engine.generate(prompt, max_tokens=500, temperature=0.3)
            match = re.search(r'\{.*\}', resp or '', re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                for key in ('overall', 'fluency', 'pronunciation', 'grammar', 'vocabulary'):
                    try:
                        parsed[key] = max(0.0, min(9.0, float(parsed.get(key, 0))))
                    except (ValueError, TypeError):
                        parsed[key] = 0.0
                parsed['success'] = True
                parsed['word_count'] = word_count
                return parsed
        except Exception as e:
            logger.error(f"Evaluation failed: {e}")

        return {
            "overall": 0.0, "fluency": 0.0, "pronunciation": 0.0,
            "grammar": 0.0, "vocabulary": 0.0,
            "feedback": "Evaluation failed. Please try again.",
            "strengths": [], "weaknesses": [], "success": False,
        }

    # ============================================
    # PROGRESS / STATUS
    # ============================================

    @rate_limit(max_requests=30, window_seconds=60)
    def get_progress(self, user_id: str) -> Dict:
        self._ensure_repository()
        if not self.repository:
            return {'error': 'Repository not available', 'stats': {}, 'profile': {}, 'sessions': []}
        try:
            stats = self.repository.get_user_stats(str(user_id))
            profile = self.repository.get_or_create_profile(str(user_id))
            sessions = self.repository.get_user_sessions(str(user_id), limit=10)
            return {'stats': stats, 'profile': profile, 'sessions': sessions, 'success': True}
        except Exception as e:
            logger.error(f"Failed to get progress: {e}")
            return {'error': str(e), 'success': False}

    @rate_limit(max_requests=20, window_seconds=60)
    def get_generation_status(self, test_id: str) -> Dict:
        if not self.db:
            return {'success': False, 'error': 'Database not available'}
        try:
            state = GenerationState.query.filter_by(test_id=test_id, module='ielts_speaking').first()
            if not state:
                return {'success': False, 'error': f'Generation state not found for test_id: {test_id}'}
            generated = list(state.generated_sections or [])
            total = state.total_sections or 3
            return {
                'success': True,
                'test_id': state.test_id,
                'status': state.status,
                'progress': state.progress_percent() if hasattr(state, 'progress_percent')
                            else int((len(generated) / total) * 100),
                'generated_sections': generated,
                'total_sections': total,
                'created_at': state.created_at.isoformat() if state.created_at else None,
                'updated_at': state.updated_at.isoformat() if state.updated_at else None,
                'is_complete': len(generated) >= total,
                'has_error': state.status == 'failed',
                'error_message': state.error_message
            }
        except Exception as e:
            logger.exception(f"Get generation status failed: {e}")
            return {'success': False, 'error': str(e)}

    @rate_limit(max_requests=10, window_seconds=60)
    def cleanup_old_states(self, hours: int = 48) -> Dict:
        if not self.db:
            return {'success': False, 'error': 'Database not available'}
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            deleted = GenerationState.query.filter(
                GenerationState.updated_at < cutoff,
                GenerationState.status.in_(['completed', 'failed', 'abandoned'])
            ).delete()
            self.db.session.commit()
            logger.info(f"Cleaned up {deleted} old speaking states (older than {hours} hours)")
            return {'success': True, 'deleted_count': deleted}
        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")
            self.db.session.rollback()
            return {'success': False, 'error': str(e)}

    # ============================================
    # BLUEPRINT
    # ============================================

    def get_blueprint(self):
        try:
            from flask import Blueprint, request, jsonify
        except ImportError:
            logger.error("Flask is not installed — cannot build speaking blueprint.")
            return None

        bp = Blueprint('ielts_speaking_api', __name__)
        api = self

        def _jsonify_with_402(result):
            if result.get('success') is None and 'error' not in result:
                result['success'] = True
            if result.get('requires_subscription'):
                return jsonify(result), 402
            return jsonify(result)

        # ---------------- /speaking/audio/generate ----------------
        @bp.route('/speaking/audio/generate', methods=['POST'])
        def audio_generate_route():
            data = request.json or {}
            text = (data.get('question') or '').strip()
            if not text:
                return jsonify({'success': False, 'error': 'No question text provided'}), 400

            filename = data.get('custom_filename') or f"speak_{uuid.uuid4().hex[:10]}.mp3"
            style = data.get('style', 'default')

            try:
                from modules.audio.unified_service import audio_service as _svc
                if _svc is not None and hasattr(_svc, 'generate_speaking_audio'):
                    url = _svc.generate_speaking_audio(
                        text=text,
                        part=1,
                        question_num=0,
                        custom_filename=filename,
                    )
                    if url:
                        return jsonify({'success': True, 'audio_url': url})
            except Exception as e:
                logger.warning(f"UnifiedAudioService TTS unavailable: {e}")

            try:
                import asyncio
                import edge_tts

                outdir = os.path.join('static', 'audio', 'speaking')
                os.makedirs(outdir, exist_ok=True)
                outpath = os.path.join(outdir, filename)

                voice = 'en-GB-RyanNeural'
                if style == 'empathetic':
                    voice = 'en-GB-SoniaNeural'
                elif style == 'thoughtful':
                    voice = 'en-GB-RyanNeural'

                async def _synth():
                    comm = edge_tts.Communicate(text, voice)
                    await comm.save(outpath)

                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                if loop.is_running():
                    import threading
                    err = {}
                    def _run():
                        try:
                            asyncio.run(_synth())
                        except Exception as ex:
                            err['e'] = ex
                    t = threading.Thread(target=_run)
                    t.start(); t.join()
                    if 'e' in err:
                        raise err['e']
                else:
                    loop.run_until_complete(_synth())

                url = f"/{outpath.replace(os.sep, '/')}"
                return jsonify({'success': True, 'audio_url': url})
            except Exception as e:
                logger.exception("edge-tts generation failed")
                return jsonify({'success': False, 'error': str(e)}), 500

        # ---------------- /speaking/generate_test ----------------
        @bp.route('/speaking/generate_test', methods=['POST'])
        def generate_test_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            from_full_test = bool(data.get('from_full_test'))
            retake = bool(data.get('retake'))

            if from_full_test:
                snap, parent = _ft_load_speaking_snapshot()
                if snap:
                    logger.info(
                        f" [speaking/generate_test] Serving from full-test "
                        f"snapshot (parent={parent.id if parent else '?'})"
                    )
                    return jsonify({
                        'success': True,
                        'from_full_test': True,
                        'from_snapshot': True,
                        'session_id': parent.id if parent else None,
                        'part1': snap.get('part1'),
                        'part2': snap.get('part2'),
                        'part3': snap.get('part3'),
                    })
                else:
                    logger.warning(
                        " from_full_test=true but no speaking snapshot found "
                        "in parent session — falling through to normal flow."
                    )

            result = api.generate_test(
                difficulty=data.get('difficulty', 'medium'),
                topic=data.get('topic'),
                user_id=user_id,
                resume=not data.get('force_new', False),
                force_new=data.get('force_new', False),
                retake=retake,
                skip_subscription_check=from_full_test,
            )
            return _jsonify_with_402(result)

        # ---------------- /speaking/part1|2|3 ----------------
        @bp.route('/speaking/part1', methods=['POST'])
        def part1_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part1(
                topic=data.get('topic'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        @bp.route('/speaking/part2', methods=['POST'])
        def part2_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part2(
                category=data.get('category'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        @bp.route('/speaking/part3', methods=['POST'])
        def part3_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part3(
                part2_topic=data.get('part2_topic'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        # ---------------- /speaking/submit ----------------
        @bp.route('/speaking/submit', methods=['POST'])
        def submit_route():
            try:
                data = request.json or {}
                responses = data.get('responses') or []
                user_id = api._get_user_id_from_request(data)
                is_full_test = bool(data.get('is_full_test'))
                session_id = data.get('session_id')

                # -------- EMPTY SUBMISSION --------
                if not responses:
                    if is_full_test:
                        state = _ft_get_state()
                        if state:
                            next_phase = _ft_save_speaking_phase(
                                state, band=0.0,
                                answers={'responses': []},
                            )
                            logger.info(
                                f" Empty speaking submit — full-test phase recorded, "
                                f"band=0.0, next={next_phase}"
                            )
                            return jsonify({
                                'success': True,
                                'is_full_test': True,
                                'band_score': 0.0,
                                'feedback': 'BAND 0 — No responses provided.',
                                'next_phase': next_phase,
                                'redirect': '/ielts-full-test?completed=speaking',
                            })

                    usage_result = _increment_speaking_usage(
                        api, user_id, session_id=session_id
                    )

                    return jsonify({
                        'success': True,
                        'user_overall_band': 0.0,
                        'band_score': 0.0,
                        'feedback': 'BAND 0 — No responses provided.',
                        'usage': usage_result,
                    })

                # -------- EVALUATE EACH RESPONSE --------
                per_part_scores = {1: [], 2: [], 3: []}
                feedbacks = []
                for r in responses:
                    part = int(r.get('part') or 1)
                    ev = api.evaluate_response(
                        question=r.get('question', ''),
                        response=r.get('response', ''),
                        part=part,
                        user_id=user_id
                    )
                    overall = float(ev.get('overall') or 0.0)
                    per_part_scores.setdefault(part, []).append(overall)
                    if ev.get('feedback'):
                        feedbacks.append(f"Part {part}: {ev['feedback']}")

                all_scores = [s for lst in per_part_scores.values() for s in lst]
                band = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0

                part_avgs = {}
                for p, lst in per_part_scores.items():
                    part_avgs[p] = round(sum(lst) / len(lst), 1) if lst else 0.0

                summary = (
                    f"Part 1 avg: {part_avgs.get(1, 0.0)} | "
                    f"Part 2 avg: {part_avgs.get(2, 0.0)} | "
                    f"Part 3 avg: {part_avgs.get(3, 0.0)}"
                )
                combined_feedback = summary + " | " + " || ".join(feedbacks[:6])

                # -------- Save to repository --------
                try:
                    api._ensure_repository()
                    if api.repository:
                        api.repository.save_session({
                            'session_id': session_id or api.sid,
                            'user_id': user_id,
                            'topic': None,
                            'overall_band': band,
                            'fluency_coherence': part_avgs.get(1, 0.0),
                            'lexical_resource': part_avgs.get(2, 0.0),
                            'grammar_accuracy': part_avgs.get(3, 0.0),
                            'pronunciation': band,
                            'response_text': ' | '.join(r.get('response', '') for r in responses)[:2000],
                            'word_count': sum(len((r.get('response') or '').split()) for r in responses),
                            'duration_seconds': 0,
                            'feedback': combined_feedback,
                            'improvement_tips': [],
                        })
                except Exception as e:
                    logger.warning(f"Could not persist session: {e}")

                # ══════════════════════════════════════════════════════
                # FULL-TEST BRANCH
                # ══════════════════════════════════════════════════════
                if is_full_test:
                    state = _ft_get_state()
                    if state:
                        next_phase = _ft_save_speaking_phase(
                            state, band=band,
                            answers={'responses': responses},
                        )
                        logger.info(
                            f" Full-test speaking phase recorded for user "
                            f"{user_id}, band={band}, next={next_phase}"
                        )
                        return jsonify({
                            'success': True,
                            'is_full_test': True,
                            'band_score': band,
                            'part_averages': part_avgs,
                            'feedback': combined_feedback,
                            'next_phase': next_phase,
                            'redirect': '/ielts-full-test?completed=speaking',
                        })
                    else:
                        logger.warning(
                            " is_full_test=true but session['full_ielts_test'] "
                            "is missing — falling through to standalone path."
                        )

                # ══════════════════════════════════════════════════════
                # STANDALONE PATH
                # ══════════════════════════════════════════════════════
                usage_result = _increment_speaking_usage(
                    api, user_id, session_id=session_id
                )

                # Mark the TestSession itself as completed
                try:
                    TestSession = get_test_session_model(_MODULE)
                    user_id_int = int(user_id) if str(user_id).isdigit() else 0
                    session_obj = TestSession.query.filter_by(
                        id=session_id,
                        user_id=user_id_int,
                    ).first()
                    if session_obj:
                        session_obj.status = 'completed'
                        session_obj.answers_so_far = {'responses': responses}
                        session_obj.last_updated = datetime.now(timezone.utc)
                        _global_db.session.commit()
                except Exception as e:
                    logger.warning(f"Standalone session marking skipped: {e}")

                return jsonify({
                    'success': True,
                    'user_overall_band': band,
                    'band_score': band,
                    'part_averages': part_avgs,
                    'feedback': combined_feedback,
                    'usage': usage_result,
                })

            except Exception as e:
                logger.exception("submit_route failed")
                return jsonify({
                    'success': False,
                    'error': str(e),
                    'user_overall_band': 0.0,
                    'band_score': 0.0,
                    'feedback': 'Submission error.'
                }), 500

        # ---------------- status / progress ----------------
        @bp.route('/speaking/status/<test_id>', methods=['GET'])
        def status_route(test_id):
            return jsonify(api.get_generation_status(test_id))

        @bp.route('/speaking/progress/<user_id>', methods=['GET'])
        def progress_route(user_id):
            return jsonify(api.get_progress(user_id))

        # ---------------- /api/speaking/* aliases ----------------
        @bp.route('/api/speaking/analyze', methods=['POST'])
        def api_analyze_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            return jsonify(api.analyze(
                text=data.get('text', ''),
                topic=data.get('topic'),
                user_id=user_id,
                test_id=data.get('test_id')
            ))

        @bp.route('/api/speaking/generate', methods=['POST'])
        def api_generate_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            from_full_test = bool(data.get('from_full_test'))
            retake = bool(data.get('retake'))

            if from_full_test:
                snap, parent = _ft_load_speaking_snapshot()
                if snap:
                    return jsonify({
                        'success': True,
                        'from_full_test': True,
                        'from_snapshot': True,
                        'session_id': parent.id if parent else None,
                        'part1': snap.get('part1'),
                        'part2': snap.get('part2'),
                        'part3': snap.get('part3'),
                    })

            result = api.generate_test(
                difficulty=data.get('difficulty', 'medium'),
                topic=data.get('topic'),
                user_id=user_id,
                resume=data.get('resume', True),
                force_new=data.get('force_new', False),
                retake=retake,
                skip_subscription_check=from_full_test,
            )
            return _jsonify_with_402(result)

        @bp.route('/api/speaking/evaluate', methods=['POST'])
        def api_evaluate_route():
            data = request.json or {}
            return jsonify(api.evaluate_response(
                question=data.get('question', ''),
                response=data.get('response', ''),
                part=data.get('part', 1),
                user_id=data.get('user_id')
            ))

        @bp.route('/api/speaking/part1', methods=['POST'])
        def api_part1_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part1(
                topic=data.get('topic'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        @bp.route('/api/speaking/part2', methods=['POST'])
        def api_part2_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part2(
                category=data.get('category'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        @bp.route('/api/speaking/part3', methods=['POST'])
        def api_part3_route():
            data = request.json or {}
            user_id = api._get_user_id_from_request(data)
            result = api.generate_part3(
                part2_topic=data.get('part2_topic'),
                difficulty=data.get('difficulty', 'medium'),
                user_id=user_id
            )
            return _jsonify_with_402(result)

        @bp.route('/api/speaking/status/<test_id>', methods=['GET'])
        def api_status_route(test_id):
            return jsonify(api.get_generation_status(test_id))

        @bp.route('/api/speaking/progress/<user_id>', methods=['GET'])
        def api_progress_route(user_id):
            return jsonify(api.get_progress(user_id))

        # ═══════════════════════════════════════════════════════════
        # FREE CONVERSATION MODE
        # ═══════════════════════════════════════════════════════════
        try:
            from .free_conversation import FreeConversationEngine
            _free_engine = FreeConversationEngine(api.ai_engine)

            @bp.route('/speaking/free/start', methods=['POST'])
            def free_start_route():
                data = request.json or {}
                user_id = api._get_user_id_from_request(data)
                result = _free_engine.start_session(
                    user_id=user_id,
                    difficulty=data.get('difficulty', 'medium'),
                )
                return jsonify(result)

            @bp.route('/speaking/free/respond', methods=['POST'])
            def free_respond_route():
                data = request.json or {}
                session_id = data.get('session_id')
                transcript = data.get('transcript', '')
                if not session_id:
                    return jsonify({'success': False, 'error': 'session_id required'}), 400
                result = _free_engine.process_response(session_id, transcript)
                return jsonify(result)

            @bp.route('/speaking/free/state/<session_id>', methods=['GET'])
            def free_state_route(session_id):
                return jsonify(_free_engine.get_session_state(session_id))

            @bp.route('/speaking/free/end', methods=['POST'])
            def free_end_route():
                data = request.json or {}
                session_id = data.get('session_id')
                if not session_id:
                    return jsonify({'success': False, 'error': 'session_id required'}), 400
                result = _free_engine.end_session(session_id)
                return jsonify(result)

            logger.info(
                " Free Conversation Mode registered "
                "(/speaking/free/start|respond|state|end)"
            )
        except Exception as _free_err:
            logger.warning(
                f" Free Conversation Mode not registered: {_free_err}"
            )

        logger.info(
            " IELTS Speaking blueprint built "
            "(/speaking/* + /api/speaking/*; 402 for blocked; "
            "full-test phase + snapshot serve integrated; "
            "pool-native v6; free-conversation v4; retake v5)"
        )
        return bp


# ============================================
# FACTORY FUNCTIONS
# ============================================

def create_speaking_api(ai_engine, db=None, repository=None):
    if not ai_engine:
        raise ValueError(" AI Engine required to create IELTSSpeakingAPI")
    return IELTSSpeakingAPI(ai_engine, db, repository)


def create_speaking_blueprint(ai_engine, db=None, repository=None):
    api = create_speaking_api(ai_engine, db=db, repository=repository)
    return api.get_blueprint()


def analyze_speaking(text: Optional[str] = None, filepath: Optional[str] = None,
                     topic: Optional[str] = None, user_id: str = "anonymous",
                     ai_engine=None, db=None) -> Dict:
    if not ai_engine:
        raise ValueError(" AI Engine is required for speaking analysis")

    api = IELTSSpeakingAPI(ai_engine, db)
    if filepath:
        return api.analyze_file(filepath, topic, user_id)
    elif text:
        return api.analyze(text=text, topic=topic, user_id=user_id)
    else:
        return {'error': 'Provide text or filepath', 'success': False}


__all__ = [
    'IELTSSpeakingAPI',
    'create_speaking_api',
    'create_speaking_blueprint',
    'analyze_speaking',
    'rate_limit',
    'MIN_WORDS_PER_RESPONSE',
]