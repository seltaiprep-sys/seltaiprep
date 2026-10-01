"""Public API entry point for IELTS Listening module with Resumable Generation and TestBank reuse
Updated for module‑separated models – uses dynamic model getters for IELTS.
Now supports partial generation: Section 1 generated synchronously, others in background.
Fixed background generation with proper Flask app context passing.
Fixed get_section_data to return 202 (not 404) when section is being generated but not yet stored in sections list.
Fixed background_task to properly persist JSON field changes by reassigning test_data.
Added 'test_id' field in start_test response (same as session_id) for frontend compatibility.
Added /session/<session_id> endpoint to retrieve full session data.
Fixed SQLite transaction isolation: enables WAL mode (SQLite only), rolls back stale transactions, uses expire_all() and fresh queries.
NEW: Passes speaker_genders to audio_generator so voices match character genders.

v3.2 — PostgreSQL Compatible:
  • WAL mode (PRAGMA) only runs when the underlying DB is SQLite
  • PostgreSQL connections skip PRAGMA entirely (no more syntax errors)
  • Uses db.engine.url to auto-detect the database type

BANK INTEGRATION (v3 NEW):
  * start_test() now tries the shared rolling TEST_BANK_MANAGER first
    (via current_app.config['TEST_BANK_MANAGER'].get_or_create_test()).
      - Serves unused tests from the shared bank (0 AI call, all 4 sections ready).
      - Generates exactly ONE new test when the user has exhausted all bank
        tests AND the module is below the 100-per-module cap.
      - Serves the oldest repeat when the module is FULL.
      - Falls back to partial on-demand generation only when the bank manager
        is unavailable (or explicitly disabled).
  * The legacy per-difficulty TestBank reuse block was REMOVED — it never
    grew the bank because it required an exact topic match.
  * Response now includes a `source` field:
        'bank' | 'generated_single' | 'bank_repeat' | 'on_demand' | 'partial'

FULL-TEST SNAPSHOT COMPATIBILITY (v3.1):
  * get_section_data() now infers `generated_sections` from the actual
    `sections` list length when the field is missing (e.g. old full-test
    bank variants that were saved before this field was added).
    Without this, the frontend's lazy section-polling endpoint would
    return HTTP 202 forever even though the sections were already baked
    into the snapshot.
"""

import logging
import uuid
import json
import threading
from typing import Dict, Any, Optional, List
from functools import wraps
from datetime import datetime, timedelta, timezone

from flask import current_app

# ─── SQLAlchemy text() for raw SQL (fixes WAL PRAGMA on SQLAlchemy 2.0) ──
from sqlalchemy import text

# ============================================
# DYNAMIC MODEL IMPORTS
# ============================================
from models import (
    db,
    GenerationState, # shared – not module‑specific
    get_test_bank_model,
    get_test_session_model,
)
# For IELTS, we use the 'ielts' module
_MODULE = 'ielts'
TestBank = get_test_bank_model(_MODULE)
TestSession = get_test_session_model(_MODULE)

# ─── Import subscription manager for free usage increment ──────
from modules.ielts.subscription_manager import get_ielts_subscription_manager

logger = logging.getLogger(__name__)

# ============================================
# RATE LIMITING (unchanged)
# ============================================

_rate_limit_store = {}

def rate_limit(max_requests: int = 30, window_seconds: int = 60):
    """Rate limiting decorator (unchanged)."""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            client_id = kwargs.get('user_id')
            if client_id is None:
                if len(args) > 0 and hasattr(args[0], 'get'):
                    client_id = args[0].get('user_id')
            if client_id is None:
                client_id = 'anonymous'
            now = datetime.now(timezone.utc)
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
# AUDIO HELPER
# ============================================
try:
    from .audio_generator import audio_generator
    AUDIO_GENERATOR_AVAILABLE = bool(audio_generator)
except ImportError:
    audio_generator = None
    AUDIO_GENERATOR_AVAILABLE = False
    logger.warning("audio_generator not available")


# ============================================
# HELPER: normalize a bank-provided full test into the
# shape the frontend expects (section-1 first).
# ============================================
def _normalize_bank_test_data(test_data: Dict) -> Dict:
    """
    Ensure a bank-provided listening test has the expected keys:
        sections, total_sections, generated_sections,
        audio_urls, audio_timings, difficulty, accent, partial.
    """
    if not isinstance(test_data, dict):
        return {}
    sections = test_data.get('sections') or []
    audio_urls = test_data.get('audio_urls') or {}
    audio_timings = test_data.get('audio_timings') or {}
    generated = test_data.get('generated_sections') or list(range(1, len(sections) + 1))
    total = test_data.get('total_sections') or max(4, len(sections))
    partial = test_data.get('partial', len(sections) < total)
    return {
        'sections': sections,
        'total_sections': total,
        'generated_sections': generated,
        'audio_urls': audio_urls,
        'audio_timings': audio_timings,
        'difficulty': test_data.get('difficulty', 'medium'),
        'accent': test_data.get('accent', 'british'),
        'topic': test_data.get('topic'),
        'partial': partial,
    }


# ============================================
# MAIN API CLASS
# ============================================

class ListeningAPI:
    """
    Public API for IELTS Listening operations with Resumable Generation and TestBank reuse.
    (Module = 'ielts')
    """

    def __init__(self, ai_engine=None, db=None, service=None):
        if not ai_engine:
            raise ValueError(" AI Engine is required. No fallback templates available.")
        self.ai_engine = ai_engine
        self.db = db
        self.module = _MODULE # 'ielts'
        # Cache model classes for this module
        self.TestBank = get_test_bank_model(_MODULE)
        self.TestSession = get_test_session_model(_MODULE)
        # GenerationState is shared; keep direct import
        if service:
            self.service = service
        else:
            from .service import ListeningService
            self.service = ListeningService(ai_engine)
        logger.info("ListeningAPI initialized with pure AI mode and resumable generation")

        # ENABLE WAL MODE — only for SQLite (PostgreSQL doesn't support PRAGMA)
        if self.db:
            try:
                # Detect database type from the SQLAlchemy engine URL
                db_url = str(self.db.engine.url)
                is_sqlite = db_url.startswith('sqlite')

                if is_sqlite:
                    with self.db.engine.connect() as conn:
                        conn.execute(text("PRAGMA journal_mode=WAL"))
                        conn.execute(text("PRAGMA synchronous=NORMAL"))
                        conn.execute(text("PRAGMA busy_timeout=30000"))
                        conn.commit()
                    logger.info(" SQLite WAL mode enabled successfully")
                else:
                    # PostgreSQL / MySQL / etc. — skip PRAGMA silently
                    db_kind = db_url.split(':')[0]
                    logger.info(f" WAL mode skipped (using {db_kind})")
            except Exception as e:
                logger.warning(f"Could not enable WAL mode: {e}")

    # ============================================
    # GENERATION STATE MANAGEMENT (RESUMABLE)
    # ============================================

    def _get_or_create_state(self, user_id: str, test_id: str = None,
                             difficulty: str = "medium",
                             topic: str = None,
                             accent: str = "british") -> Dict:
        """Get existing generation state or create a new one."""
        from models import GenerationState
        if not self.db:
            return {'success': False, 'error': 'Database not available'}
        try:
            if test_id:
                state = GenerationState.query.filter_by(
                    user_id=user_id,
                    test_id=test_id,
                    module='ielts_listening'
                ).first()
                if state:
                    return {'success': True, 'state': state, 'exists': True, 'test_id': test_id}
            if not test_id:
                test_id = f"l_{uuid.uuid4().hex[:12]}"
            new_state = GenerationState(
                user_id=user_id,
                module='ielts_listening',
                test_id=test_id,
                difficulty=difficulty,
                total_sections=4,
                generated_sections=[],
                current_section=1,
                status='in_progress'
            )
            if topic:
                new_state.topic = topic
            if accent:
                new_state.accent = accent
            self.db.session.add(new_state)
            self.db.session.commit()
            logger.info(f"New listening generation state created: {test_id} for user {user_id}")
            return {'success': True, 'state': new_state, 'exists': False, 'test_id': test_id}
        except Exception as e:
            logger.exception(f"Failed to get/create state: {e}")
            if self.db:
                self.db.session.rollback()
            return {'success': False, 'error': str(e)}

    def _update_state_section(self, state, section_num: int,
                              section_data: Dict = None,
                              status: str = None) -> bool:
        """Update generation state for a section."""
        if not self.db:
            return False
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
                if state.section_data:
                    state.test_data = {
                        'sections': state.section_data,
                        'difficulty': state.difficulty,
                        'accent': state.accent,
                        'topic': state.topic
                    }
            self.db.session.commit()
            return True
        except Exception as e:
            logger.exception(f"Failed to update state: {e}")
            self.db.session.rollback()
            return False

    def _resume_from_state(self, state) -> Dict:
        """Resume generation from existing state."""
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
                    'test_id': state.test_id,
                    'test_data': state.test_data or state.section_data,
                    'sections': state.section_data,
                    'message': 'All sections already generated'
                }
            missing_sections = []
            for i in range(1, total + 1):
                if i not in generated:
                    missing_sections.append(i)
            logger.info(f"Resuming listening state: generated={generated}, missing={missing_sections}")
            full_test = None
            for section_num in missing_sections:
                try:
                    if hasattr(self.service, 'generate_section'):
                        result = self.service.generate_section(
                            section_num=section_num,
                            difficulty=state.difficulty or 'medium',
                            topic=state.topic,
                            accent=getattr(state, 'accent', 'british')
                        )
                    else:
                        if not full_test:
                            full_test = self.service.start_test(
                                difficulty=state.difficulty or 'medium',
                                topic=state.topic,
                                exam='ielts',
                                accent=getattr(state, 'accent', 'british')
                            )
                            if full_test.get('error'):
                                raise ValueError(full_test.get('error'))
                        sections = full_test.get('sections', [])
                        result = None
                        for s in sections:
                            if s.get('section_num') == section_num:
                                result = s
                                break
                        if not result:
                            raise ValueError(f"Section {section_num} not found")
                    if result and not result.get('error'):
                        self._update_state_section(state, section_num, result)
                        logger.info(f"Section {section_num} generated via resume")
                    else:
                        state.status = 'failed'
                        state.error_message = f"Failed section {section_num}: {result.get('error') if result else 'unknown'}"
                        self.db.session.commit()
                        return {'success': False, 'error': state.error_message, 'resumed': True}
                except Exception as e:
                    state.status = 'failed'
                    state.error_message = str(e)
                    self.db.session.commit()
                    return {'success': False, 'error': str(e), 'resumed': True}
            state.status = 'completed'
            state.completed_at = datetime.now(timezone.utc)
            state.test_data = {
                'sections': state.section_data,
                'difficulty': state.difficulty,
                'accent': state.accent,
                'topic': state.topic
            }
            self.db.session.commit()
            return {
                'success': True,
                'resumed': True,
                'completed': True,
                'test_id': state.test_id,
                'test_data': state.test_data,
                'sections': state.section_data,
                'message': 'Generation resumed and completed successfully'
            }
        except Exception as e:
            logger.exception(f"Resume failed: {e}")
            state.status = 'failed'
            state.error_message = str(e)
            self.db.session.commit()
            return {'success': False, 'error': str(e), 'resumed': True}

    # ============================================
    # AUDIO GENERATION (section-level)
    # ============================================

    def _generate_audio_for_section(self, section_data: Dict, section_num: int, accent: str = 'british') -> tuple:
        """
        Generate audio for a single section using the audio_generator.
        Extracts speaker_genders from section_data and passes it to
        audio_generator so voices match character genders (from the AI prompt).
        Returns: (audio_urls_dict, error_message, timings_dict)
        """
        script = section_data.get('script', '')
        if not script:
            # Fallback: build script from questions
            questions = section_data.get('questions', [])
            text_parts = [q.get('text', '') for q in questions if q.get('text')]
            script = '. '.join(text_parts)
        if not script:
            return {}, "No text to synthesize", {}

        if not AUDIO_GENERATOR_AVAILABLE or not audio_generator:
            return {}, "Audio generator not available", {}

        # Extract speaker_genders map (may be empty for Sections 2/4 if not stored)
        speaker_genders = section_data.get('speaker_genders') or {}
        if speaker_genders:
            logger.info(f" Passing speaker_genders to audio generator for section {section_num}: {speaker_genders}")

        try:
            test_title = f"section_{section_num}_partial"
            urls, error, timings = audio_generator.generate(
                script=script,
                section_number=section_num,
                test_title=test_title,
                accent=accent,
                total_questions=10,
                include_instructions=False,
                speaker_genders=speaker_genders,
                fast=True
            )
            if urls and urls.get('main'):
                return urls, None, timings
            else:
                logger.error(f"audio_generator.generate failed for section {section_num}: {error}")
                return {}, error or "Audio generation failed", {}
        except Exception as e:
            logger.error(f"Audio generation error for section {section_num}: {e}", exc_info=True)
            return {}, str(e), {}

    # =====================================================
    # PARTIAL TEST GENERATION (Section 1 first) – FIXED APP CONTEXT
    # =====================================================

    @rate_limit(max_requests=30, window_seconds=60)
    def start_test(
        self,
        difficulty: str = "medium",
        topic: Optional[str] = None,
        exam: str = "ielts",
        accent: str = "british",
        user_id: Optional[str] = None,
        resume: bool = True,
        force_new: bool = False,
        auto_generate: bool = True,
        preserve_user_essay: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate / resume / serve a listening test.

        Resolution order (v3):
          1. Resume check (GenerationState) if resume=True
          2. BANK-FIRST via TEST_BANK_MANAGER.get_or_create_test()
             → serve complete test (all 4 sections ready, no AI call if bank has one)
             → or generate exactly ONE complete test if user exhausted bank
             → or serve oldest repeat if module is FULL
          3. FALLBACK (bank manager unavailable): partial on-demand generation
             (Section 1 sync + Sections 2-4 in background).
        """
        logger.info(f" [1] start_test() called with user_id={user_id}, difficulty={difficulty}, accent={accent}")
        TestBank = self.TestBank
        TestSession = self.TestSession
        from models import GenerationState

        try:
            logger.info(" [2] Inside try block")
            if not self.ai_engine:
                raise RuntimeError(" AI Engine not available")

            logger.info(" [3] AI Engine OK")

            # ---------- RESUME LOGIC ----------
            if user_id and resume and self.db:
                logger.info(f" [4] Checking for existing state for user {user_id}")
                existing_state = GenerationState.query.filter_by(
                    user_id=user_id,
                    module='ielts_listening',
                    status='in_progress'
                ).first()
                if existing_state:
                    logger.info(f" [5] Found existing state, resuming...")
                    result = self._resume_from_state(existing_state)
                    if result.get('success'):
                        logger.info(" [6] Resume successful, returning early")
                        return result
                else:
                    logger.info(" [5] No existing state found")
            else:
                logger.info(f" [4] Skipping resume: user_id={user_id}, resume={resume}, db={self.db is not None}")

            # ═══════════════════════════════════════════════════════
            # BANK-FIRST (v3): shared rolling manager
            # ═══════════════════════════════════════════════════════
            bank_mgr = None
            try:
                bank_mgr = current_app.config.get('TEST_BANK_MANAGER')
            except Exception:
                bank_mgr = None

            if (bank_mgr is not None
                    and not force_new
                    and self.db
                    and user_id and user_id != 'anonymous'):

                logger.info(" [7] Trying bank-first via TEST_BANK_MANAGER")
                bank_result = bank_mgr.get_or_create_test(
                    test_type='listening',
                    difficulty=difficulty,
                    topic=topic,
                    user_id=user_id,
                    module=_MODULE,
                    accent=accent,
                    fast=True,
                )

                if bank_result.get('success'):
                    raw_test = bank_result['test'] or {}
                    source = bank_result.get('source', 'bank')
                    test_data = _normalize_bank_test_data(raw_test)

                    # Validate the bank test has at least section 1
                    if test_data and test_data.get('sections'):
                        # Create a user TestSession referencing this test
                        session_obj = TestSession(
                            user_id=int(user_id),
                            test_type='listening',
                            difficulty=difficulty,
                            test_data=test_data,
                            status='in_progress',
                            start_time=datetime.now(timezone.utc)
                        )
                        self.db.session.add(session_obj)
                        self.db.session.commit()
                        session_id = session_obj.id

                        # Populate the resume-state fields so future resumes work
                        try:
                            state_result = self._get_or_create_state(
                                user_id=user_id, difficulty=difficulty,
                                topic=topic, accent=accent
                            )
                            if state_result.get('success'):
                                st = state_result['state']
                                for i, sec in enumerate(test_data.get('sections', []), 1):
                                    self._update_state_section(st, i, sec)
                        except Exception as e:
                            logger.warning(f"Could not create generation state for bank serve: {e}")

                        sections = test_data.get('sections', [])
                        section1 = sections[0] if sections else {}
                        audio_urls_all = test_data.get('audio_urls', {})
                        audio_timings_all = test_data.get('audio_timings', {})
                        sec1_audio = audio_urls_all.get('1', {})
                        sec1_timings = audio_timings_all.get('1', {})

                        logger.info(
                            f" [listening] Served from bank — user={user_id} "
                            f"session={session_id} source={source} "
                            f"(unused_left={bank_result.get('remaining_unused')}, "
                            f"module_total={bank_result.get('module_total')})"
                        )

                        return {
                            'success': True,
                            'session_id': session_id,
                            'test_id': session_id,
                            'section': 1,
                            'test_data': section1,
                            'audio_urls': sec1_audio,
                            'audio_timings': sec1_timings,
                            'sections': sections,
                            'audio_urls_all': audio_urls_all,
                            'audio_timings_all': audio_timings_all,
                            'total_sections': test_data.get('total_sections', 4),
                            'generated_sections': test_data.get('generated_sections', [1, 2, 3, 4]),
                            'partial': test_data.get('partial', False),
                            'source': source,
                            'message': f'Listening test loaded ({source}).',
                        }
                    else:
                        logger.warning(
                            " Bank returned empty/malformed listening test — "
                            "falling back to on-demand partial generation."
                        )
                else:
                    logger.warning(
                        f" Bank manager returned error: "
                        f"{bank_result.get('error')} — falling back to on-demand."
                    )
            else:
                logger.info(
                    f" [7] Skipping bank: "
                    f"mgr={bank_mgr is not None}, force_new={force_new}, "
                    f"db={self.db is not None}, user_id={user_id}"
                )

            # ═══════════════════════════════════════════════════════
            # FALLBACK: on-demand PARTIAL generation
            # (Section 1 sync, Sections 2-4 in background)
            # ═══════════════════════════════════════════════════════
            logger.info(" [9] Starting partial on-demand generation (Section 1 only)")
            from .test_generator import ListeningTestGenerator
            gen = ListeningTestGenerator(self.ai_engine)
            logger.info(" [10] ListeningTestGenerator created")

            # 1. Generate Section 1 (questions + script)
            logger.info(" [11] Generating Section 1")
            section1_data = gen.generate_single_section(1, difficulty, topic, accent)
            if not section1_data:
                raise ValueError("Failed to generate Section 1")
            logger.info(" [12] Section 1 generated")

            # 2. Generate audio for Section 1 (synchronous)
            logger.info(" [13] Generating audio for Section 1")
            audio_urls, audio_error, timings = self._generate_audio_for_section(section1_data, 1, accent)
            if audio_error:
                logger.warning(f"Section 1 audio generation failed: {audio_error}")
            logger.info(" [14] Audio generation done")

            # 3. Create TestSession with only Section 1
            logger.info(" [15] Creating TestSession")
            test_data = {
                'sections': [section1_data],
                'total_sections': 4,
                'generated_sections': [1],
                'accent': accent,
                'difficulty': difficulty,
                'topic': topic,
                'audio_urls': {'1': audio_urls},
                'audio_timings': {'1': timings},
                'partial': True,
            }
            session_obj = TestSession(
                user_id=int(user_id) if user_id and user_id != 'anonymous' else 0,
                test_type='listening',
                difficulty=difficulty,
                test_data=test_data,
                status='in_progress',
                start_time=datetime.now(timezone.utc)
            )
            self.db.session.add(session_obj)
            self.db.session.commit()
            session_id = session_obj.id
            logger.info(f" [16] TestSession created with id={session_id}")

            # 4. Get actual Flask app instance for background thread
            logger.info(" [17] Getting app instance")
            app = current_app._get_current_object()
            logger.info(f" [18] Got app object: {app is not None}")

            # 5. Start background thread for Sections 2–4
            def background_task(app, session_id, difficulty, topic, accent):
                logger.info(f" BACKGROUND TASK STARTED for session {session_id}")
                try:
                    with app.app_context():
                        sess = self.db.session.get(TestSession, session_id)
                        if not sess:
                            logger.error(f"Session {session_id} not found in background thread")
                            return
                        logger.info(f" Session found, starting generation for sections 2-4")
                        for sec in [2, 3, 4]:
                            sec_data = gen.generate_single_section(sec, difficulty, topic, accent)
                            if not sec_data:
                                logger.error(f"Failed to generate Section {sec}")
                                continue
                            audio_urls_sec, err_sec, timings_sec = self._generate_audio_for_section(sec_data, sec, accent)
                            if err_sec:
                                logger.warning(f"Section {sec} audio error: {err_sec}")

                            # Re-fetch session each time to avoid stale
                            sess = self.db.session.get(TestSession, session_id)
                            if not sess:
                                break

                            # FIX: reassign test_data to persist JSON changes
                            test_data = sess.test_data
                            # Guard: sections list must exist and match section order
                            if 'sections' not in test_data or not isinstance(test_data['sections'], list):
                                test_data['sections'] = []
                            # Pad missing section slots so index-by-position works
                            while len(test_data['sections']) < sec - 1:
                                test_data['sections'].append(None)
                            if len(test_data['sections']) >= sec:
                                test_data['sections'][sec - 1] = sec_data
                            else:
                                test_data['sections'].append(sec_data)

                            test_data.setdefault('audio_urls', {})[str(sec)] = audio_urls_sec
                            test_data.setdefault('audio_timings', {})[str(sec)] = timings_sec
                            gen_list = test_data.get('generated_sections', [])
                            if sec not in gen_list:
                                gen_list.append(sec)
                            test_data['generated_sections'] = sorted(gen_list)
                            sess.test_data = test_data # force SQLAlchemy to detect change
                            sess.last_updated = datetime.now(timezone.utc)
                            self.db.session.commit()
                            logger.info(f" Section {sec} generated and saved (session {session_id})")

                        # Mark completion
                        sess = self.db.session.get(TestSession, session_id)
                        if sess and len(sess.test_data.get('generated_sections', [])) >= 4:
                            test_data = sess.test_data
                            test_data['partial'] = False
                            sess.test_data = test_data
                            self.db.session.commit()
                            logger.info(f" All sections completed for session {session_id}")
                except Exception as e:
                    logger.error(f"Background generation failed: {e}", exc_info=True)

            logger.info(" [19] Creating and starting background thread...")
            thread = threading.Thread(
                target=background_task,
                args=(app, session_id, difficulty, topic, accent),
                daemon=True
            )
            thread.start()
            logger.info(f" [20] Background thread started (daemon={thread.daemon})")

            # 6. Return immediate response with Section 1
            logger.info(" [21] Returning response")
            return {
                'success': True,
                'session_id': session_id,
                'test_id': session_id,
                'section': 1,
                'test_data': section1_data,
                'audio_urls': audio_urls,
                'audio_timings': timings,
                'sections': [section1_data],
                'total_sections': 4,
                'generated_sections': [1],
                'partial': True,
                'source': 'partial',
                'message': 'Section 1 ready. Remaining sections generating in background.',
            }

        except Exception as e:
            logger.exception(f" Start test failed: {e}")
            return {
                'error': str(e),
                'success': False,
                'message': f"Failed to generate test: {str(e)}"
            }

    # =====================================================
    # GET SECTION DATA – FIXED SQLITE ISOLATION
    # v3.1: infer generated_sections when missing
    # =====================================================

    def get_section_data(self, session_id: int, section: int) -> Dict:
        """
        Retrieve a specific section's data and audio from a TestSession.

        SQLite isolation fix: rollback stale transaction, expire_all, fresh query.

         v3.1 FIX: If the TestSession's test_data has `sections` but is missing
        `generated_sections` (as happens with full-test bank variants saved
        before that field existed), infer that all loaded sections are already
        generated. This prevents the frontend's section-polling loop from
        returning HTTP 202 forever.
        """
        try:
            self.db.session.rollback()
        except Exception:
            pass

        self.db.session.expire_all()

        session_obj = self.db.session.query(TestSession).filter_by(id=session_id).first()
        if not session_obj:
            return {'error': 'Session not found', 'status': 'error'}, 404

        self.db.session.refresh(session_obj)

        test_data = session_obj.test_data or {}
        total = test_data.get('total_sections', 4)
        if section < 1 or section > total:
            return {'error': 'Invalid section number'}, 400

        generated = test_data.get('generated_sections', [])
        sections_list = test_data.get('sections', [])

        # v3.1 DEFENSIVE FIX: infer generated_sections when the field is
        # missing but sections are actually loaded (e.g. old full-test
        # bank variants). Persist the inferred value so subsequent calls
        # are also fast.
        if not generated and sections_list:
            generated = list(range(1, len(sections_list) + 1))
            try:
                test_data['generated_sections'] = generated
                session_obj.test_data = test_data
                self.db.session.commit()
                logger.info(
                    f" Inferred generated_sections={generated} for session "
                    f"{session_id} (snapshot missing the field)"
                )
            except Exception as e:
                logger.warning(
                    f"Could not persist inferred generated_sections for "
                    f"session {session_id}: {e}"
                )

        logger.info(
            f"Section data request: session={session_id}, section={section}, "
            f"generated={generated}, sections_len={len(sections_list)}"
        )

        if section not in generated:
            progress = int((len(generated) / total) * 100) if total else 0
            return {
                'ready': False,
                'status': 'generating',
                'section': section,
                'progress': progress,
                'message': f'Section {section} is still being generated. Progress: {progress}%'
            }, 202

        if section > len(sections_list):
            logger.warning(
                f"Section {section} in generated_sections but sections list length is "
                f"{len(sections_list)}. Returning 202."
            )
            progress = int((len(generated) / total) * 100) if total else 0
            return {
                'ready': False,
                'status': 'generating',
                'section': section,
                'progress': progress,
                'message': 'Section data is being saved, please wait.'
            }, 202

        section_data = sections_list[section - 1]
        audio_urls = test_data.get('audio_urls', {}).get(str(section), {})
        timings = test_data.get('audio_timings', {}).get(str(section), {})

        return {
            'ready': True,
            'section': section,
            'test_data': section_data,
            'audio_urls': audio_urls,
            'audio_timings': timings,
            'status': 'ready'
        }, 200

    # =====================================================
    # TEST RETRIEVAL METHODS
    # =====================================================

    @rate_limit(max_requests=50, window_seconds=60)
    def get_test(self, difficulty: str = "medium", exam: str = "ielts",
                 user_id: Optional[str] = None) -> Dict[str, Any]:
        return self.service.get_saved_test(difficulty, exam)

    @rate_limit(max_requests=100, window_seconds=60)
    def get_test_by_serial(self, serial_number: int) -> Optional[Dict[str, Any]]:
        if serial_number < 1:
            logger.warning(f"Invalid serial number: {serial_number}")
            return None
        return self.service.get_test_by_serial(serial_number)

    @rate_limit(max_requests=100, window_seconds=60)
    def get_test_by_id(self, test_id: int) -> Optional[Dict[str, Any]]:
        if test_id < 1:
            logger.warning(f"Invalid test ID: {test_id}")
            return None
        result = self.service.get_test_by_id(test_id)
        if result:
            return result
        session_obj = self.db.session.query(TestSession).filter_by(id=test_id).first() if self.db else None
        if session_obj:
            return session_obj.test_data
        return None

    @rate_limit(max_requests=50, window_seconds=60)
    def get_all(self, exam: Optional[str] = None, limit: int = 50,
                offset: int = 0) -> List[Dict[str, Any]]:
        return self.service.get_all_tests(exam, limit=limit, offset=offset)

    @rate_limit(max_requests=100, window_seconds=60)
    def get_topics(self) -> Dict[str, List[str]]:
        return self.service.get_available_topics()

    @rate_limit(max_requests=30, window_seconds=60)
    def get_stats(self) -> Dict[str, Any]:
        return self.service.get_service_stats()

    @rate_limit(max_requests=100, window_seconds=60)
    def get_sections(self, test_id: int) -> List[Dict[str, Any]]:
        test_data = self.get_test_by_id(test_id)
        if not test_data:
            return []
        return test_data.get('sections', [])

    @rate_limit(max_requests=100, window_seconds=60)
    def get_questions_by_section(self, test_id: int,
                                 section_num: int) -> List[Dict[str, Any]]:
        sections = self.get_sections(test_id)
        for section in sections:
            if section and section.get('section_num') == section_num:
                return section.get('questions', [])
        return []

    # =====================================================
    # RESUME SESSION
    # =====================================================

    @rate_limit(max_requests=30, window_seconds=60)
    def resume_test(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        test_id = session_data.get('test_id')
        if not test_id:
            raise ValueError("Missing test_id in session data")
        test_data = self.get_test_by_id(test_id)
        if not test_data and isinstance(test_id, str):
            logger.warning(f"Test {test_id} not found by ID, attempting alternate lookup...")
            try:
                return self.service.resume_test(session_data)
            except Exception as e:
                logger.error(f"Failed to resume test: {e}")
                raise ValueError(f"Test {test_id} not found")
        if not test_data:
            raise ValueError(f"Test {test_id} not found")
        answers_so_far = session_data.get('answers_so_far', {})
        for question in test_data.get('questions', []):
            qid = str(question.get('id', ''))
            if qid in answers_so_far:
                question['saved_answer'] = answers_so_far[qid]
        test_data['current_question_index'] = session_data.get('current_question_index', 0)
        test_data['_resumed'] = True
        return test_data

    # =====================================================
    # SUBMIT METHODS – UPDATED with free usage increment
    # =====================================================

    @rate_limit(max_requests=50, window_seconds=60)
    def submit(self, test_id: int, answers: Dict[str, str],
               user_id: Optional[str] = None) -> Dict[str, Any]:
        from models import GenerationState
        try:
            result = self.service.submit_test(test_id, answers)

            if user_id and self.db:
                try:
                    state = GenerationState.query.filter_by(
                        user_id=user_id,
                        module='ielts_listening',
                        status='completed'
                    ).first()
                    if state:
                        if not state.test_data:
                            state.test_data = {}
                        state.test_data['submission'] = result
                        self.db.session.commit()
                except Exception as e:
                    logger.warning(f"Could not update generation state: {e}")

                try:
                    sub_mgr = get_ielts_subscription_manager(self.db.session)
                    sub_mgr.increment_free_usage(int(user_id), 'listening')
                    logger.info(f" Free usage incremented for user {user_id} (listening) via API")
                except Exception as e:
                    logger.error(f"Failed to increment free usage for user {user_id}: {e}")

            return result

        except Exception as e:
            logger.exception(f"Submit failed: {e}")
            return {'error': str(e), 'success': False}

    @rate_limit(max_requests=30, window_seconds=60)
    def submit_session(self, session_id: int,
                       answers: Dict[str, str]) -> Dict[str, Any]:
        session_obj = self.TestSession.query.get(session_id)
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")
        test_data = session_obj.test_data
        test_id = test_data.get('id')
        if not test_id:
            raise ValueError("No test_id in session data")
        return self.submit(test_id, answers)

    # =====================================================
    # SCORING HELPERS
    # =====================================================

    @rate_limit(max_requests=100, window_seconds=60)
    def band_score(self, correct: int) -> float:
        return self.service.get_band_score(correct)

    @rate_limit(max_requests=100, window_seconds=60)
    def band_description(self, band: float) -> str:
        return self.service.get_band_description(band)

    @rate_limit(max_requests=100, window_seconds=60)
    def cefr_level(self, band: float) -> str:
        return self.service.get_cefr_level(band)

    @rate_limit(max_requests=100, window_seconds=60)
    def score_breakdown(self, correct: int) -> Dict[str, Any]:
        return self.service.get_score_breakdown(correct)

    @rate_limit(max_requests=100, window_seconds=60)
    def validate_answers(self, answers: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.validate_answers(answers)

    # =====================================================
    # GENERATION STATUS
    # =====================================================

    @rate_limit(max_requests=20, window_seconds=60)
    def get_generation_status(self, test_id: str) -> Dict:
        from models import GenerationState
        if not self.db:
            return {'success': False, 'error': 'Database not available'}
        try:
            state = GenerationState.query.filter_by(
                test_id=test_id,
                module='ielts_listening'
            ).first()
            if not state:
                return {'success': False, 'error': f'Generation state not found for test_id: {test_id}'}
            return {
                'success': True,
                'test_id': state.test_id,
                'status': state.status,
                'progress': state.progress_percent() if hasattr(state, 'progress_percent') else
                            int((len(state.generated_sections or []) / (state.total_sections or 1)) * 100),
                'generated_sections': state.generated_sections or [],
                'total_sections': state.total_sections,
                'created_at': state.created_at.isoformat() if state.created_at else None,
                'updated_at': state.updated_at.isoformat() if state.updated_at else None,
                'is_complete': len(state.generated_sections or []) >= (state.total_sections or 1),
                'has_error': state.status == 'failed',
                'error_message': state.error_message
            }
        except Exception as e:
            logger.exception(f"Get generation status failed: {e}")
            return {'success': False, 'error': str(e)}

    # =====================================================
    # ADMIN UTILITY
    # =====================================================

    @rate_limit(max_requests=10, window_seconds=60)
    def cleanup_old_states(self, hours: int = 48) -> Dict:
        from models import GenerationState
        if not self.db:
            return {'success': False, 'error': 'Database not available'}
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            deleted = GenerationState.query.filter(
                GenerationState.updated_at < cutoff,
                GenerationState.status.in_(['completed', 'failed', 'abandoned'])
            ).delete()
            self.db.session.commit()
            logger.info(f"Cleaned up {deleted} old listening states (older than {hours} hours)")
            return {'success': True, 'deleted_count': deleted}
        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")
            self.db.session.rollback()
            return {'success': False, 'error': str(e)}

    # =====================================================
    # MODULE INTEGRATION (Flask Blueprint)
    # =====================================================

    def get_blueprint(self):
        try:
            from flask import Blueprint, request, jsonify
            bp = Blueprint('listening_api', __name__, url_prefix='/api/listening')

            @bp.route('/start', methods=['POST'])
            def start_test_route():
                data = request.json or {}
                result = self.start_test(
                    difficulty=data.get('difficulty', 'medium'),
                    topic=data.get('topic'),
                    exam=data.get('exam', 'ielts'),
                    accent=data.get('accent', 'british'),
                    user_id=data.get('user_id'),
                    resume=data.get('resume', True),
                    force_new=data.get('force_new', False),
                    auto_generate=data.get('auto_generate', True),
                    preserve_user_essay=data.get('preserve_user_essay', True)
                )
                return jsonify(result)

            @bp.route('/submit', methods=['POST'])
            def submit_route():
                data = request.json or {}
                result = self.submit(
                    test_id=data.get('test_id', 0),
                    answers=data.get('answers', {}),
                    user_id=data.get('user_id')
                )
                return jsonify(result)

            @bp.route('/test/<int:test_id>', methods=['GET'])
            def get_test_by_id_route(test_id):
                result = self.get_test_by_id(test_id)
                if result:
                    return jsonify({'success': True, 'data': result})
                return jsonify({'success': False, 'error': 'Test not found'}), 404

            @bp.route('/serial/<int:serial>', methods=['GET'])
            def get_test_by_serial_route(serial):
                result = self.get_test_by_serial(serial)
                if result:
                    return jsonify({'success': True, 'data': result})
                return jsonify({'success': False, 'error': 'Test not found'}), 404

            @bp.route('/all', methods=['GET'])
            def get_all_route():
                exam = request.args.get('exam')
                limit = request.args.get('limit', 50, type=int)
                offset = request.args.get('offset', 0, type=int)
                result = self.get_all(exam, limit, offset)
                return jsonify({'success': True, 'data': result})

            @bp.route('/topics', methods=['GET'])
            def topics_route():
                result = self.get_topics()
                return jsonify({'success': True, 'data': result})

            @bp.route('/score/band/<int:correct>', methods=['GET'])
            def band_route(correct):
                band = self.band_score(correct)
                return jsonify({'band': band})

            @bp.route('/status/<test_id>', methods=['GET'])
            def status_route(test_id):
                result = self.get_generation_status(test_id)
                return jsonify(result)

            @bp.route('/section/<int:session_id>/<int:section>', methods=['GET'])
            def get_section_route(session_id, section):
                result, status_code = self.get_section_data(session_id, section)
                return jsonify(result), status_code

            @bp.route('/session/<int:session_id>', methods=['GET'])
            def get_session_route(session_id):
                session_obj = self.db.session.query(TestSession).filter_by(id=session_id).first() if self.db else None
                if not session_obj:
                    return jsonify({'error': 'Session not found'}), 404
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'test_data': session_obj.test_data
                })

            return bp
        except ImportError:
            logger.warning("Flask not available for blueprint")
            return None


# ============================================
# FACTORY FUNCTION
# ============================================

def create_listening_api(ai_engine, db=None, service=None):
    """Factory function to create ListeningAPI instance."""
    if not ai_engine:
        raise ValueError(" AI Engine required to create ListeningAPI")
    return ListeningAPI(ai_engine, db, service)


# ============================================
# EXPORTS
# ============================================

__all__ = [
    'ListeningAPI',
    'create_listening_api',
    'rate_limit'
]