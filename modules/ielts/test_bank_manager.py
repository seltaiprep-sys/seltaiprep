"""Smart Test Bank Manager
   - Standalone tests: on-demand + rolling bank (TestBank table)
   - Full IELTS mock tests: pre-generated variants (FullTestVariant table)
   - Pool manager: PTE-style shared pool (IELTSTestPool + locks)

═══════════════════════════════════════════════════════════════════════
ARCHITECTURE (v4.2)
═══════════════════════════════════════════════════════════════════════

Three managers, separate responsibilities:

  ┌────────────────────────────────────────────────────────────┐
  │ TestBankManager (STANDALONE — legacy rolling bank) │
  │ ─ Table: `test_bank` │
  │ ─ test_type ∈ {listening, reading, writing, speaking} │
  │ ─ Rolling: serve unused → generate 1 → repeat oldest │
  │ ─ Cap: 100 per module (all difficulties combined) │
  └────────────────────────────────────────────────────────────┘

  ┌────────────────────────────────────────────────────────────┐
  │ FullTestBankManager (FULL IELTS MOCK) │
  │ ─ Table: `full_test_variants` │
  │ ─ Each row = complete snapshot (4 phases) + audio ready │
  │ ─ Cap: 100 variants (all difficulties combined) │
  └────────────────────────────────────────────────────────────┘

  ┌────────────────────────────────────────────────────────────┐
  │ IELTSTestPoolManager (POOL — PTE-style, v4.2 NEW) │
  │ ─ Tables: `ielts_test_pool`, `ielts_generation_lock`, │
  │ `ielts_user_pool_progress` │
  │ ─ Shared pool, 100 per module │
  │ ─ 202 "waiting" on concurrent generation (thundering-herd)│
  │ ─ Per-user progress: serve unseen until exhausted │
  └────────────────────────────────────────────────────────────┘

WHY TWO BANK SYSTEMS?
  The legacy `TestBankManager` provides the admin dashboard and
  pre-generation workflow (rolling bank). The new pool manager
  provides PTE-style behavior for user-facing routes:
      • 202 "another user is generating" responses
      • Shared pool progress tracking per user
      • Lock-based single-generator election

  User routes now call `ielts_test_pool_manager.get_or_generate()`.
  Admin routes continue to use `TestBankManager.generate_and_save_tests()`.
  `FullTestBankManager` is unchanged.

═══════════════════════════════════════════════════════════════════════
INSTANTIATION
═══════════════════════════════════════════════════════════════════════

    from modules.ielts.test_bank_manager import (
        TestBankManager,
        FullTestBankManager,
        IELTSTestPoolManager,
        ielts_test_pool_manager,
    )
"""

import os
import json
import shutil
import random
import threading
import logging
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set, Tuple, Callable

from sqlalchemy.exc import IntegrityError

from models import (
    get_test_bank_model,
    get_test_session_model,
    get_full_test_variant_model,
)

logger = logging.getLogger(__name__)

BANK_ROOT = os.path.join('static', 'full_test_bank')

# Rolling bank cap — per MODULE (all difficulties combined)
MAX_BANK_SIZE_PER_MODULE = 100

# Pool cap (PTE-style)
POOL_MAX_SIZE = 100
POOL_LOCK_TIMEOUT_SECONDS = 300
POOL_RETRY_AFTER_SECONDS = 3


# ══════════════════════════════════════════════════════════════════════
# STANDALONE TEST BANK (listening / reading / writing / speaking)
# ══════════════════════════════════════════════════════════════════════

class TestBankManager:
    """Smart test bank for STANDALONE tests — generates on demand."""

    def __init__(self, db, ai_engine, module: str = 'ielts', app=None):
        self.db = db
        self.ai = ai_engine
        self.module = module
        self.app = app
        self.TestBank = get_test_bank_model(module)
        self.TestSession = get_test_session_model(module)
        self.generation_lock = threading.Lock()
        logger.info(
            f" TestBankManager initialized for module {module} "
            f"with AI engine (app={'yes' if app else 'no'})"
        )

    # ================================================================
    # ROLLING SINGLE-TEST BANK (main entry point for admin)
    # ================================================================
    def get_or_create_test(self, test_type: str, difficulty: str = 'medium',
                           topic: str = None, user_id=None,
                           module: str = 'ielts', **kwargs) -> Dict:
        """Serve a STANDALONE test to the user with rolling generation."""
        if test_type in ('full_ielts', 'full'):
            return {
                'success': False,
                'error': (
                    'TestBankManager handles standalone tests only. '
                    'Use FullTestBankManager for full IELTS mock tests.'
                ),
            }

        if user_id is None:
            return {'success': False, 'error': 'user_id is required'}

        TestBank = self.TestBank
        uid = str(user_id)

        tests_for_diff = self.db.session.query(TestBank).filter(
            TestBank.test_type == test_type,
            TestBank.difficulty == difficulty,
        ).all()

        unused = [
            t for t in tests_for_diff
            if uid not in self._parse_used(t.used_by_users)
        ]

        # RULE 1: Unused found → serve
        if unused:
            unused.sort(key=lambda t: t.usage_count or 0)
            bank = unused[0]
            self._record_usage(bank, uid)
            logger.info(
                f" [bank] Served {test_type}/{difficulty} "
                f"(id={bank.id}, unused_left={len(unused) - 1}) to user {uid}"
            )
            return {
                'success': True,
                'test': bank.test_data,
                'test_id': bank.id,
                'source': 'bank',
                'remaining_unused': len(unused) - 1,
            }

        module_total = self.db.session.query(TestBank).filter(
            TestBank.test_type == test_type,
        ).count()

        # RULE 2: Module FULL → serve oldest repeat
        if module_total >= MAX_BANK_SIZE_PER_MODULE:
            if not tests_for_diff:
                return {
                    'success': False,
                    'error': (
                        f'No {test_type}/{difficulty} tests available '
                        f'and module is at cap ({module_total}/'
                        f'{MAX_BANK_SIZE_PER_MODULE})'
                    )
                }
            tests_for_diff.sort(
                key=lambda t: t.last_used_at or
                datetime.min.replace(tzinfo=timezone.utc)
            )
            bank = tests_for_diff[0]
            self._record_usage(bank, uid)
            logger.warning(
                f" [bank] {test_type} module FULL "
                f"({module_total}/{MAX_BANK_SIZE_PER_MODULE}); "
                f"user {uid} exhausted {difficulty} — serving repeat"
            )
            return {
                'success': True,
                'test': bank.test_data,
                'test_id': bank.id,
                'source': 'bank_repeat',
                'remaining_unused': 0,
                'module_total': module_total,
                'warning': 'Module full; serving repeat.',
            }

        # RULE 3: Module < 100 → generate EXACTLY ONE
        logger.info(
            f"🆕 [bank] User {uid} exhausted {test_type}/{difficulty} — "
            f"generating 1 new (module: {module_total}/"
            f"{MAX_BANK_SIZE_PER_MODULE})"
        )

        test_data = self._generate_single_test(
            test_type, difficulty, topic, **kwargs
        )
        if not test_data:
            return {
                'success': False,
                'error': f'Failed to generate {test_type} test'
            }

        new_test = TestBank(
            test_type=test_type,
            difficulty=difficulty,
            topic=topic or 'general',
            test_data=test_data,
            usage_count=0,
            used_by_users=json.dumps([]),
            created_at=datetime.now(timezone.utc),
        )
        self.db.session.add(new_test)
        self.db.session.commit()

        self._record_usage(new_test, uid)
        logger.info(
            f" [bank] Generated 1 new {test_type}/{difficulty} "
            f"(id={new_test.id}, module_total={module_total + 1}/"
            f"{MAX_BANK_SIZE_PER_MODULE})"
        )
        return {
            'success': True,
            'test': test_data,
            'test_id': new_test.id,
            'source': 'generated_single',
            'remaining_unused': 0,
            'module_total': module_total + 1,
        }

    def _record_usage(self, bank, uid: str):
        used = self._parse_used(bank.used_by_users)
        if uid not in used:
            used.append(uid)
        bank.used_by_users = json.dumps(used)
        bank.usage_count = (bank.usage_count or 0) + 1
        bank.last_used_at = datetime.now(timezone.utc)
        self.db.session.commit()

    def get_bank_status(self):
        TestBank = self.TestBank
        status = {}

        for test_type in ['reading', 'listening', 'writing', 'speaking']:
            module_total = self.db.session.query(TestBank).filter(
                TestBank.test_type == test_type,
            ).count()

            entry = {
                'total': module_total,
                'is_full': module_total >= MAX_BANK_SIZE_PER_MODULE,
                'slots_left': max(0, MAX_BANK_SIZE_PER_MODULE - module_total),
                'max': MAX_BANK_SIZE_PER_MODULE,
                'by_difficulty': {},
            }

            for diff in ['easy', 'medium', 'hard']:
                diff_count = self.db.session.query(TestBank).filter(
                    TestBank.test_type == test_type,
                    TestBank.difficulty == diff,
                ).count()
                entry['by_difficulty'][diff] = diff_count

            status[test_type] = entry

        return status

    # ================================================================
    # ADMIN-SIDE: generate + save any number (1..100 per click)
    # ================================================================
    def generate_and_save_tests(self, test_type: str, difficulty: str,
                                count: int, topic: str = None,
                                module: str = 'ielts') -> Dict:
        if test_type in ('full_ielts', 'full'):
            return {
                'success': False,
                'error': 'Use FullTestBankManager for full-test variants.',
            }

        TestBank = self.TestBank

        module_total = self.db.session.query(TestBank).filter(
            TestBank.test_type == test_type,
        ).count()

        slots_left = MAX_BANK_SIZE_PER_MODULE - module_total
        if slots_left <= 0:
            return {
                'success': False,
                'error': (
                    f'Module cap reached: {test_type} '
                    f'has {module_total}/{MAX_BANK_SIZE_PER_MODULE} tests'
                ),
                'generated': 0,
                'module_total': module_total,
            }

        to_generate = min(int(count), slots_left)
        generated = 0
        failed = 0

        for i in range(to_generate):
            try:
                test_data = self._generate_single_test(
                    test_type, difficulty, topic
                )
                if not test_data:
                    failed += 1
                    logger.warning(
                        f"[admin] Generation {i+1}/{to_generate} returned empty"
                    )
                    continue

                new_test = TestBank(
                    test_type=test_type,
                    difficulty=difficulty,
                    topic=topic or 'general',
                    test_data=test_data,
                    usage_count=0,
                    used_by_users=json.dumps([]),
                    created_at=datetime.now(timezone.utc),
                )
                self.db.session.add(new_test)
                self.db.session.commit()
                generated += 1
                logger.info(
                    f"[admin] Generated {generated}/{to_generate} "
                    f"{test_type}/{difficulty} (id={new_test.id}, "
                    f"module: {module_total + generated}/"
                    f"{MAX_BANK_SIZE_PER_MODULE})"
                )
            except Exception as e:
                failed += 1
                logger.error(
                    f"[admin] Generation {i+1}/{to_generate} failed: {e}",
                    exc_info=True
                )

        return {
            'success': True,
            'generated': generated,
            'failed': failed,
            'module_total_before': module_total,
            'module_total_after': module_total + generated,
            'max': MAX_BANK_SIZE_PER_MODULE,
            'capped': to_generate < int(count),
        }

    # ================================================================
    # LEGACY: single-type get_test
    # ================================================================
    def get_test(self, test_type: str, difficulty: str = "medium",
                 topic: str = None) -> Dict:
        TestBank = self.TestBank

        query = self.db.session.query(TestBank).filter(
            TestBank.test_type == test_type,
            TestBank.difficulty == difficulty
        )
        if topic:
            query = query.filter(TestBank.topic == topic)

        existing_test = query.order_by(TestBank.usage_count.asc()).first()

        if existing_test:
            existing_test.usage_count += 1
            existing_test.last_used_at = datetime.now(timezone.utc)
            self.db.session.commit()
            logger.info(f" Served from DB: {test_type}/{difficulty} "
                        f"(used {existing_test.usage_count} times)")
            return {
                'success': True,
                'test': existing_test.test_data,
                'source': 'database',
                'test_id': existing_test.id,
            }

        logger.info(f" Generating new {test_type}/{difficulty} test")

        with self.generation_lock:
            existing_test = query.order_by(TestBank.usage_count.asc()).first()
            if existing_test:
                existing_test.usage_count += 1
                self.db.session.commit()
                return {
                    'success': True,
                    'test': existing_test.test_data,
                    'source': 'database',
                    'test_id': existing_test.id,
                }

            test_data = self._generate_single_test(test_type, difficulty, topic)
            if not test_data:
                return {'success': False, 'error': 'Failed to generate test'}

            new_test = TestBank(
                test_type=test_type,
                difficulty=difficulty,
                topic=topic or 'general',
                test_data=test_data,
                usage_count=1,
                created_at=datetime.now(timezone.utc),
            )
            self.db.session.add(new_test)
            self.db.session.commit()

            logger.info(f" Saved new {test_type}/{difficulty} test (ID: {new_test.id})")

            self._trigger_background_generation(test_type, difficulty)

            return {
                'success': True,
                'test': test_data,
                'source': 'generated',
                'test_id': new_test.id,
            }

    # ================================================================
    # GENERATE SINGLE STANDALONE TEST
    # ================================================================
    def _generate_single_test(self, test_type: str, difficulty: str,
                              topic: str = None, **kwargs) -> Optional[Dict]:
        try:
            if test_type == 'reading':
                from modules.ielts.reading.test_generator import IELTSReadingGenerator
                generator = IELTSReadingGenerator(ai_engine=self.ai)
                test = generator.generate_complete_test(
                    difficulty, [topic] if topic else None
                )
                return test.to_dict() if hasattr(test, 'to_dict') else test

            elif test_type == 'listening':
                from modules.ielts.listening.test_generator import ListeningTestGenerator
                generator = ListeningTestGenerator(self.ai)
                return generator.generate(
                    difficulty=difficulty,
                    topic=topic,
                    exam='ielts',
                    accent=kwargs.get('accent', 'british'),
                    fast=kwargs.get('fast', True),
                )

            elif test_type == 'writing':
                from modules.ielts.writing import create_writing_test_generator
                generator = create_writing_test_generator(self.ai)
                return generator.generate_complete_advanced(difficulty, topic)

            elif test_type == 'speaking':
                from modules.ielts.speaking import create_speaking_test
                generator = create_speaking_test(self.ai)
                return generator.generate_complete_test(
                    difficulty=difficulty, topic=topic
                )

            return None
        except Exception as e:
            logger.error(f"Failed to generate {test_type} test: {e}", exc_info=True)
            return None

    # ================================================================
    # BACKGROUND PRE-GENERATION (legacy)
    # ================================================================
    def _trigger_background_generation(self, test_type: str, difficulty: str):
        def generate_more():
            ctx = self.app.app_context() if self.app is not None else nullcontext()
            with ctx:
                try:
                    TestBank = self.TestBank
                    import time
                    count = self.db.session.query(TestBank).filter(
                        TestBank.test_type == test_type,
                        TestBank.difficulty == difficulty
                    ).count()

                    if count < 50:
                        needed = 50 - count
                        logger.info(f" Background: Generating {needed} more "
                                    f"{test_type}/{difficulty} tests")
                        for i in range(min(needed, 10)):
                            test_data = self._generate_single_test(test_type, difficulty)
                            if test_data:
                                new_test = TestBank(
                                    test_type=test_type,
                                    difficulty=difficulty,
                                    topic='general',
                                    test_data=test_data,
                                    usage_count=0,
                                    created_at=datetime.now(timezone.utc),
                                )
                                self.db.session.add(new_test)
                                self.db.session.commit()
                                logger.info(f" Generated {i+1}/{needed}")
                            time.sleep(1)
                        logger.info(f" Background complete: {test_type}/{difficulty}")
                except Exception as e:
                    logger.error(f"Background generation failed: {e}", exc_info=True)
                finally:
                    try:
                        self.db.session.remove()
                    except Exception:
                        pass

        thread = threading.Thread(target=generate_more, daemon=True)
        thread.start()

    # ================================================================
    # BANK STATS (legacy)
    # ================================================================
    def get_bank_stats(self) -> Dict:
        TestBank = self.TestBank
        stats = {}
        test_types = ['reading', 'listening', 'writing', 'speaking']
        difficulties = ['easy', 'medium', 'hard']
        for test_type in test_types:
            for difficulty in difficulties:
                count = self.db.session.query(TestBank).filter(
                    TestBank.test_type == test_type,
                    TestBank.difficulty == difficulty
                ).count()
                stats[f"{test_type}_{difficulty}"] = count
        stats['total'] = sum(stats.values())
        return stats

    # ================================================================
    # WARM-UP (legacy)
    # ================================================================
    def warm_up_bank(self):
        import time

        def warm_up():
            ctx = self.app.app_context() if self.app is not None else nullcontext()
            with ctx:
                try:
                    test_types = ['reading', 'listening', 'writing', 'speaking']
                    difficulties = ['easy', 'medium', 'hard']
                    logger.info(" Starting test bank warm-up...")
                    for test_type in test_types:
                        for difficulty in difficulties:
                            self._trigger_background_generation(test_type, difficulty)
                            time.sleep(2)
                    logger.info(" Test bank warm-up completed")
                except Exception as e:
                    logger.error(f"Warm-up failed: {e}", exc_info=True)
                finally:
                    try:
                        self.db.session.remove()
                    except Exception:
                        pass

        thread = threading.Thread(target=warm_up, daemon=True)
        thread.start()
        logger.info(" Test bank warm-up started in background")

    # ─── Helpers ──────────────────────────────────────────────
    @staticmethod
    def _parse_used(raw):
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return []
            try:
                return [str(x) for x in json.loads(raw)]
            except Exception:
                return [x.strip() for x in raw.split(',') if x.strip()]
        return []


# ══════════════════════════════════════════════════════════════════════
# FULL-TEST VARIANT BANK (full IELTS mock test — 4 phases in 1 row)
# ══════════════════════════════════════════════════════════════════════

class FullTestBankManager:
    """
    Manages FULL IELTS mock test variants in the `full_test_variants` table.
    """

    MAX_VARIANTS = 100

    def __init__(self, db, ai_engine, app=None):
        self.db = db
        self.ai = ai_engine
        self.app = app
        self.FullTestVariant = get_full_test_variant_model('ielts')
        self.TestSession = get_test_session_model('ielts')
        self._gen_lock = threading.Lock()
        logger.info(
            f" FullTestBankManager initialized "
            f"(app={'yes' if app else 'no'})"
        )

    # ================================================================
    # STATUS / ADMIN
    # ================================================================
    def get_bank_status(self):
        FTV = self.FullTestVariant
        total = self.db.session.query(FTV).count()
        ready = self.db.session.query(FTV).filter(FTV.status == 'ready').count()
        generating = self.db.session.query(FTV).filter(FTV.status == 'generating').count()
        failed = self.db.session.query(FTV).filter(FTV.status == 'failed').count()

        by_diff = {}
        for diff in ['easy', 'medium', 'hard']:
            by_diff[diff] = self.db.session.query(FTV).filter(
                FTV.difficulty == diff,
                FTV.status == 'ready',
            ).count()

        return {
            'total': total,
            'ready': ready,
            'generating': generating,
            'failed': failed,
            'max': self.MAX_VARIANTS,
            'is_full': total >= self.MAX_VARIANTS,
            'slots_left': max(0, self.MAX_VARIANTS - total),
            'by_difficulty': by_diff,
        }

    def list_variants(self):
        FTV = self.FullTestVariant
        return self.db.session.query(FTV).order_by(FTV.id.desc()).all()

    # ================================================================
    # PICK A VARIANT FOR A USER
    # ================================================================
    def get_full_test_variant(self, user_id, difficulty: str = 'medium') -> Dict:
        FTV = self.FullTestVariant
        try:
            variants = self.db.session.query(FTV).filter(
                FTV.difficulty == difficulty,
                FTV.status == 'ready',
            ).all()

            if not variants:
                return {
                    'success': False,
                    'error': f'No full-test variants available for {difficulty}',
                }

            uid = str(user_id)
            unused = [v for v in variants if not v.has_user_used(uid)]
            pool = unused or variants
            pool.sort(key=lambda v: v.usage_count or 0)
            variant = pool[0]

            variant.mark_used_by(uid)
            self.db.session.commit()

            snapshot = dict(variant.snapshot or {})
            listening_snap = variant.get_listening_snapshot()
            if listening_snap:
                snapshot['listening'] = listening_snap

            logger.info(
                f" [full-test] Served variant #{variant.id} "
                f"({difficulty}) to user {uid} "
                f"(usage={variant.usage_count})"
            )

            return {
                'success': True,
                'variant_id': variant.id,
                'bank_id': variant.id,
                'snapshot': snapshot,
                'audio_urls': listening_snap.get('audio_urls', {}) if listening_snap else {},
            }
        except Exception as e:
            logger.error(f"get_full_test_variant failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    get_variant_for_user = get_full_test_variant

    # ================================================================
    # CREATE A USER ATTEMPT (parent TestSession)
    # ================================================================
    def create_user_attempt(self, user_id, bank_id, snapshot,
                            difficulty: str = 'medium') -> Dict:
        try:
            s = self.TestSession(
                user_id=user_id,
                test_type='full_ielts',
                difficulty=difficulty,
                bank_id=None,
                test_data={
                    '_full_test': True,
                    '_from_variant': True,
                    'variant_id': bank_id,
                    'bank_id': bank_id,
                    'snapshot': snapshot,
                },
                status='ready',
                start_time=datetime.now(timezone.utc),
            )
            self.db.session.add(s)
            self.db.session.commit()
            logger.info(
                f" Attempt #{s.id} created for user {user_id} "
                f"(variant #{bank_id})"
            )
            return {'success': True, 'session_id': s.id}
        except Exception as e:
            self.db.session.rollback()
            logger.error(f"create_user_attempt failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    # ================================================================
    # READ A PHASE FROM PARENT SESSION
    # ================================================================
    def get_variant_snapshot(self, parent_session, phase: str):
        if not parent_session:
            return None
        td = parent_session.test_data or {}
        snap = td.get('snapshot') or {}
        phase_data = snap.get(phase)
        if not phase_data:
            return None

        if phase == 'listening':
            variant_id = td.get('variant_id') or td.get('bank_id')
            if variant_id:
                FTV = self.FullTestVariant
                variant = self.db.session.get(FTV, int(variant_id))
                if variant:
                    merged = variant.get_listening_snapshot()
                    if merged:
                        for k, v in phase_data.items():
                            if k not in merged:
                                merged[k] = v
                        return merged

            phase_data = dict(phase_data)
            sections = phase_data.get('sections') or []
            if not phase_data.get('generated_sections'):
                phase_data['generated_sections'] = list(
                    range(1, len(sections) + 1)
                )
            phase_data.setdefault('total_sections', len(sections) or 4)
            phase_data.setdefault('partial', False)

        return phase_data

    # ================================================================
    # GENERATE + SAVE (SYNCHRONOUS) — full variant with audio
    # ================================================================
    def generate_full_test_variant_bg(self, difficulty: str = 'medium',
                                      accent: str = 'british',
                                      on_done=None):
        FTV = self.FullTestVariant

        with self._gen_lock:
            total = self.db.session.query(FTV).count()
            if total >= self.MAX_VARIANTS:
                logger.warning(
                    f" [full-test bank] FULL ({total}/{self.MAX_VARIANTS}) "
                    f"— refusing generate"
                )
                return {'success': False,
                        'error': f'Full-test bank is full ({total}/{self.MAX_VARIANTS})'}

            placeholder = FTV(
                difficulty=difficulty,
                accent=accent,
                variant_hash=self._make_hash(),
                snapshot=None,
                status='generating',
                created_at=datetime.now(timezone.utc),
            )
            self.db.session.add(placeholder)
            self.db.session.commit()
            variant_id = placeholder.id

        logger.info(
            f" [full-test] Variant #{variant_id} generation started "
            f"(difficulty={difficulty}, accent={accent})"
        )

        try:
            from modules.ielts.listening.test_generator import ListeningTestGenerator
            lgen = ListeningTestGenerator(self.ai)
            lfull = lgen.generate(
                difficulty=difficulty, topic=None,
                exam='ielts', accent=accent, fast=True,
            )
            if not lfull or lfull.get('error'):
                raise RuntimeError(f"Listening: {lfull}")

            lsections = lfull.get('sections', [])
            if len(lsections) < 4:
                raise RuntimeError(
                    f"Listening returned only {len(lsections)} sections"
                )

            audio_urls, audio_timings = self._generate_listening_audio(
                lsections, accent=accent, variant_id=variant_id,
            )
            logger.info(
                f" [full-test] Audio for variant #{variant_id}: "
                f"sections={sorted(audio_urls.keys())}"
            )

            listening_snap = {
                'sections': lsections,
                'total_sections': 4,
                'generated_sections': [1, 2, 3, 4],
                'partial': False,
                'accent': accent,
                'difficulty': difficulty,
                'audio_urls': audio_urls,
                'audio_timings': audio_timings,
            }

            from modules.ielts.reading.test_generator import IELTSReadingGenerator
            rgen = IELTSReadingGenerator(ai_engine=self.ai)
            rdata = rgen.generate_complete_test(difficulty, None)
            if hasattr(rdata, 'to_dict'):
                rdata = rdata.to_dict()

            from modules.ielts.writing import create_writing_api
            wapi = create_writing_api(ai_engine=self.ai, db=self.db)
            wdata = wapi.start_test(
                difficulty=difficulty, topic=None,
                user_id='bank_generator', resume=False,
                force_new=True, auto_generate=False,
                preserve_user_essay=True,
            )
            if wdata.get('error'):
                raise RuntimeError(f"Writing: {wdata['error']}")

            from modules.ielts.speaking import create_speaking_test
            spgen = create_speaking_test(self.ai)
            spdata = spgen.generate_complete_test(
                difficulty=difficulty, topic=None
            )
            if not spdata or spdata.get('error'):
                raise RuntimeError(f"Speaking: {spdata}")

            snapshot = {
                'listening': listening_snap,
                'reading': rdata,
                'writing': wdata,
                'speaking': spdata,
                '_published_at': datetime.now(timezone.utc).isoformat(),
            }

            placeholder.snapshot = snapshot
            placeholder.listening_audio_urls = audio_urls
            placeholder.listening_audio_timings = audio_timings
            placeholder.status = 'ready'
            placeholder.ready_at = datetime.now(timezone.utc)
            self.db.session.commit()

            logger.info(
                f" [full-test] Variant #{variant_id} ready "
                f"(difficulty={difficulty}, sections=4, "
                f"audio={len(audio_urls)})"
            )

            if on_done:
                try:
                    on_done(variant_id)
                except Exception as cb_err:
                    logger.warning(
                        f"on_done callback failed: {cb_err}", exc_info=True
                    )

            return {'success': True, 'variant_id': variant_id}

        except Exception as e:
            logger.error(
                f" [full-test] Variant #{variant_id} generation failed: {e}",
                exc_info=True,
            )
            try:
                placeholder.status = 'failed'
                placeholder.error_message = str(e)[:500]
                self.db.session.commit()
            except Exception:
                self.db.session.rollback()
            return {'success': False, 'error': str(e)}

    # ================================================================
    # AUDIO BACKFILL
    # ================================================================
    def ensure_variant_audio(self, variant_id: int) -> Dict:
        FTV = self.FullTestVariant
        variant = self.db.session.get(FTV, variant_id)
        if not variant:
            return {'success': False, 'error': 'Variant not found'}

        snap = variant.snapshot or {}
        listening = snap.get('listening') or {}
        sections = listening.get('sections') or []
        if not sections:
            return {
                'success': False,
                'error': 'Variant has no listening sections to synthesize',
            }

        existing_audio = dict(variant.listening_audio_urls or {})
        missing = [
            i for i in range(1, len(sections) + 1)
            if str(i) not in existing_audio
        ]
        if not missing:
            return {
                'success': True,
                'variant_id': variant_id,
                'sections': sorted(existing_audio.keys()),
                'message': 'Audio already complete',
            }

        logger.info(
            f" [full-test] Backfilling audio for variant #{variant_id} — "
            f"missing sections: {missing}"
        )

        accent = variant.accent or 'british'
        new_audio, new_timings = self._generate_listening_audio(
            sections, accent=accent, variant_id=variant_id,
        )

        merged_audio = dict(existing_audio)
        merged_audio.update(new_audio)
        merged_timings = dict(variant.listening_audio_timings or {})
        merged_timings.update(new_timings)

        variant.listening_audio_urls = merged_audio
        variant.listening_audio_timings = merged_timings

        listening['audio_urls'] = merged_audio
        listening['audio_timings'] = merged_timings
        snap['listening'] = listening
        variant.snapshot = snap

        self.db.session.commit()

        logger.info(
            f" [full-test] Audio backfill complete — variant #{variant_id} "
            f"now has sections {sorted(merged_audio.keys())}"
        )

        return {
            'success': True,
            'variant_id': variant_id,
            'sections': sorted(merged_audio.keys()),
        }

    # ================================================================
    # AUDIO GENERATION
    # ================================================================
    def _generate_listening_audio(self, sections, accent: str = 'british',
                                  variant_id: Optional[int] = None):
        audio_urls = {}
        audio_timings = {}

        audio_gen = None
        audio_service = None

        try:
            from modules.ielts.listening.audio_generator import audio_generator as audio_gen
        except Exception as e:
            logger.warning(f"[full-test] audio_generator unavailable: {e}")

        try:
            from modules.audio.unified_service import audio_service as audio_service
        except Exception as e:
            logger.warning(f"[full-test] audio_service unavailable: {e}")

        if not audio_gen and not audio_service:
            logger.error(
                "[full-test] No audio engine available — skipping audio generation"
            )
            return audio_urls, audio_timings

        accents_map = {str(i): accent for i in range(1, 5)}

        for sec_num, section_data in enumerate(sections, start=1):
            try:
                script = section_data.get('script', '')
                if not script:
                    text_parts = [
                        q.get('text', '')
                        for q in section_data.get('questions', [])
                        if q.get('text')
                    ]
                    script = '. '.join(text_parts)
                if not script:
                    logger.warning(
                        f"[full-test] Section {sec_num} has no script; skipping audio"
                    )
                    continue

                test_title = (
                    f"ft_variant_{variant_id}_section_{sec_num}"
                    if variant_id else f"ft_section_{sec_num}"
                )
                sec_accent = accents_map.get(str(sec_num), 'british')

                urls = error = timings = None

                if audio_gen is not None:
                    speaker_engine_map = None
                    if hasattr(audio_gen, '_parse_script'):
                        try:
                            turns = audio_gen._parse_script(script, sec_num)
                            if turns:
                                engine = 'edge' if sec_num in (2, 4) else 'deepgram'
                                speaker_engine_map = {spk: engine for spk, _ in turns}
                        except Exception:
                            pass

                    speaker_genders = section_data.get('speaker_genders') or {}

                    urls, error, timings = audio_gen.generate(
                        script=script,
                        section_number=sec_num,
                        test_title=test_title,
                        accent=sec_accent,
                        total_questions=10,
                        include_instructions=False,
                        speaker_engine_map=speaker_engine_map,
                        speaker_genders=speaker_genders,
                        fast=True,
                    )

                if (not urls or not urls.get('main')) and audio_service is not None:
                    logger.warning(
                        f" [full-test] Primary audio failed for "
                        f"variant #{variant_id} section {sec_num} "
                        f"({error}) — trying audio_service fallback"
                    )
                    try:
                        result = audio_service.generate_listening_section_audio(
                            section_data=section_data,
                            section_num=sec_num,
                            include_instructions=False,
                        )
                        if result.get('success'):
                            main_url = result.get('merged_audio')
                            duration = result.get('duration', 30)
                            urls = {'main': main_url}
                            timings = {'start': 0, 'end': duration, 'duration': duration}
                            error = None
                    except Exception as fb_err:
                        logger.error(
                            f"[full-test] Fallback audio failed for "
                            f"section {sec_num}: {fb_err}",
                            exc_info=True,
                        )

                if urls and urls.get('main'):
                    audio_urls[str(sec_num)] = urls
                    audio_timings[str(sec_num)] = timings or {}
                    logger.info(
                        f" [full-test] Audio generated — variant #{variant_id} "
                        f"section {sec_num}"
                    )
                else:
                    logger.warning(
                        f" [full-test] Audio failed — variant #{variant_id} "
                        f"section {sec_num}: {error}"
                    )

            except Exception as sec_err:
                logger.error(
                    f"[full-test] Audio error on section {sec_num}: {sec_err}",
                    exc_info=True,
                )

        return audio_urls, audio_timings

    # ================================================================
    # HELPERS
    # ================================================================
    @staticmethod
    def _make_hash() -> str:
        import secrets
        return secrets.token_hex(32)

    @staticmethod
    def _variant_dir(variant_id: int) -> str:
        p = os.path.join(BANK_ROOT, f"variant_{variant_id:03d}")
        os.makedirs(p, exist_ok=True)
        return p

    @staticmethod
    def _copy_file(src_url: str, target_dir: str, target_name: str) -> str:
        if not src_url:
            return src_url
        clean = src_url.split('?', 1)[0]
        if clean.startswith('/static/'):
            fs = os.path.join('static', clean[len('/static/'):])
        elif clean.startswith('/'):
            fs = clean.lstrip('/')
        else:
            fs = clean
        if not os.path.exists(fs):
            logger.warning(f"Source file missing: {fs}")
            return src_url
        os.makedirs(target_dir, exist_ok=True)
        dst = os.path.join(target_dir, target_name)
        shutil.copy2(fs, dst)
        rel = os.path.relpath(dst, 'static').replace(os.sep, '/')
        return f"/static/{rel}"

    # ================================================================
    # DELETE VARIANT (admin)
    # ================================================================
    def delete_variant(self, variant_id: int) -> Dict:
        FTV = self.FullTestVariant
        v = self.db.session.get(FTV, variant_id)
        if not v:
            return {'success': False, 'error': 'Variant not found'}
        try:
            vdir = os.path.join(BANK_ROOT, f"variant_{variant_id:03d}")
            if os.path.exists(vdir):
                shutil.rmtree(vdir, ignore_errors=True)
            self.db.session.delete(v)
            self.db.session.commit()
            logger.info(f" Full-test variant #{variant_id} deleted")
            return {'success': True}
        except Exception as e:
            self.db.session.rollback()
            return {'success': False, 'error': str(e)}


# ══════════════════════════════════════════════════════════════════════
# IELTS TEST POOL MANAGER — PTE-style shared pool + 202 waiting
# Mirrors modules/pte/managers/test_pool_manager.py
# ══════════════════════════════════════════════════════════════════════

from models import (
    db as _pool_db,
    IELTSTestPool,
    IELTSGenerationLock,
    IELTSUserPoolProgress,
)


class IELTSTestPoolManager:
    """
    IELTS shared pool manager.

    Rules (identical to PTE):
      1. User completed some pool tests → serve unseen
      2. User completed ALL pool tests → generate fresh
      3. Pool empty → generate new
      4. Other users (haven't exhausted) → also serve unseen

    Thundering-herd safe: SQL unique constraint on lock table.
    Round-robin: least-used unseen test served first.
    Auto-cleanup: stale locks (>5 min) force-released.
    """

    MAX_POOL_SIZE = POOL_MAX_SIZE
    LOCK_TIMEOUT_SECONDS = POOL_LOCK_TIMEOUT_SECONDS
    RETRY_AFTER_SECONDS = POOL_RETRY_AFTER_SECONDS

    KNOWN_MODULES = [
        'ielts_reading',
        'ielts_listening',
        'ielts_writing',
        'ielts_speaking',
    ]

    # ─── POOL READ ─────────────────────────────────────────
    def get_pool_size(self, module: str) -> int:
        try:
            return IELTSTestPool.query.filter_by(module=module, is_active=True).count()
        except Exception as e:
            logger.exception(f"get_pool_size failed for {module}: {e}")
            return 0

    def is_pool_full(self, module: str) -> bool:
        return self.get_pool_size(module) >= self.MAX_POOL_SIZE

    def get_pool_stats(self) -> Dict[str, Dict[str, int]]:
        stats = {}
        for mod in self.KNOWN_MODULES:
            size = self.get_pool_size(mod)
            stats[mod] = {
                'size': size,
                'max': self.MAX_POOL_SIZE,
                'remaining': max(0, self.MAX_POOL_SIZE - size),
            }
        return stats

    def list_pool_items(self, module: str, limit: int = 20):
        items = (
            IELTSTestPool.query
            .filter_by(module=module)
            .order_by(IELTSTestPool.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                'id': it.id,
                'module': it.module,
                'difficulty': it.difficulty,
                'question_count': it.question_count,
                'usage_count': it.usage_count,
                'is_active': it.is_active,
                'created_by': it.created_by,
                'created_at': it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ]

    # ─── USER PROGRESS ─────────────────────────────────────
    def get_user_completed_pool_ids(self, user_id: int, module: str) -> Set[int]:
        try:
            rows = (
                IELTSUserPoolProgress.query
                .filter_by(user_id=user_id, module=module)
                .all()
            )
            return {r.pool_id for r in rows}
        except Exception as e:
            logger.exception(f"get_user_completed_pool_ids failed: {e}")
            return set()

    def get_active_pool_ids(self, module: str) -> Set[int]:
        try:
            rows = (
                IELTSTestPool.query
                .filter_by(module=module, is_active=True)
                .all()
            )
            return {r.id for r in rows}
        except Exception:
            return set()

    def is_user_exhausted(self, user_id: int, module: str) -> bool:
        active = self.get_active_pool_ids(module)
        if not active:
            return False
        completed = self.get_user_completed_pool_ids(user_id, module)
        return active.issubset(completed)

    def record_user_progress(self, user_id: int, module: str, pool_id: int) -> bool:
        if not pool_id:
            return False
        try:
            exists = (
                IELTSUserPoolProgress.query
                .filter_by(user_id=user_id, module=module, pool_id=pool_id)
                .first()
            )
            if exists:
                return True
            row = IELTSUserPoolProgress(
                user_id=user_id,
                module=module,
                pool_id=pool_id,
            )
            _pool_db.session.add(row)
            _pool_db.session.commit()
            logger.info(
                f" IELTS pool progress: user {user_id} "
                f"completed #{pool_id} ({module})"
            )
            return True
        except IntegrityError:
            _pool_db.session.rollback()
            return True
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"record_user_progress failed: {e}")
            return False

    def _pick_unseen_for_user(self, user_id, module, difficulty):
        completed = self.get_user_completed_pool_ids(user_id, module)

        q = IELTSTestPool.query.filter_by(
            module=module, difficulty=difficulty, is_active=True
        )
        if completed:
            q = q.filter(~IELTSTestPool.id.in_(completed))
        item = q.order_by(
            IELTSTestPool.usage_count.asc(),
            IELTSTestPool.created_at.asc()
        ).first()
        if item:
            return item

        q2 = IELTSTestPool.query.filter_by(module=module, is_active=True)
        if completed:
            q2 = q2.filter(~IELTSTestPool.id.in_(completed))
        return q2.order_by(
            IELTSTestPool.usage_count.asc(),
            IELTSTestPool.created_at.asc()
        ).first()

    # ─── POOL HELPERS ──────────────────────────────────────
    def _get_next_from_pool(self, module, difficulty='medium'):
        item = (
            IELTSTestPool.query
            .filter_by(module=module, difficulty=difficulty, is_active=True)
            .order_by(IELTSTestPool.usage_count.asc(),
                      IELTSTestPool.created_at.asc())
            .first()
        )
        if item:
            return item
        return (
            IELTSTestPool.query
            .filter_by(module=module, is_active=True)
            .order_by(IELTSTestPool.usage_count.asc(),
                      IELTSTestPool.created_at.asc())
            .first()
        )

    def _increment_pool_usage(self, pool_item):
        try:
            pool_item.usage_count = (pool_item.usage_count or 0) + 1
            _pool_db.session.commit()
        except Exception as e:
            _pool_db.session.rollback()
            logger.warning(f"Failed to increment pool usage #{pool_item.id}: {e}")

    def _reset_usage_counts(self, module) -> int:
        try:
            count = (
                IELTSTestPool.query
                .filter_by(module=module)
                .update({'usage_count': 0})
            )
            _pool_db.session.commit()
            logger.info(f" IELTS pool usage reset ({module}, {count} items)")
            return count
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"Reset usage failed for {module}: {e}")
            return 0

    def _count_questions(self, module: str, test_data: Dict) -> int:
        try:
            if module == 'ielts_listening':
                sections = test_data.get('sections') or []
                total = 0
                for sec in sections:
                    if isinstance(sec, dict):
                        total += len(sec.get('questions') or [])
                return total
            if module == 'ielts_reading':
                passages = test_data.get('passages') or []
                if passages:
                    return sum(
                        len(p.get('questions') or [])
                        for p in passages if isinstance(p, dict)
                    )
                return len(test_data.get('questions') or [])
            if module == 'ielts_writing':
                return 2
            if module == 'ielts_speaking':
                return sum(
                    1 for k in ('part1', 'part2', 'part3') if test_data.get(k)
                )
            return 0
        except Exception:
            return 0

    def _save_to_pool(self, module, difficulty, test_data, user_id):
        total = self._count_questions(module, test_data)
        pool_item = IELTSTestPool(
            module=module,
            difficulty=difficulty,
            test_data=test_data,
            created_by=user_id,
            question_count=total,
            usage_count=0,
            is_active=True,
        )
        _pool_db.session.add(pool_item)
        _pool_db.session.commit()
        logger.info(
            f" IELTS pool SAVE: {module} #{pool_item.id} ({total} questions) "
            f"by user {user_id} "
            f"[pool {self.get_pool_size(module)}/{self.MAX_POOL_SIZE}]"
        )
        return pool_item

    # ─── LOCK ──────────────────────────────────────────────
    def _cleanup_expired_locks(self) -> int:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                seconds=self.LOCK_TIMEOUT_SECONDS
            )
            expired = (
                IELTSGenerationLock.query
                .filter(
                    IELTSGenerationLock.status == 'generating',
                    IELTSGenerationLock.locked_at.isnot(None),
                    IELTSGenerationLock.locked_at < cutoff,
                )
                .all()
            )
            for lock in expired:
                logger.warning(
                    f" Force-release expired IELTS lock {lock.module} "
                    f"(user {lock.locked_by})"
                )
                _pool_db.session.delete(lock)
            if expired:
                _pool_db.session.commit()
            return len(expired)
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS lock cleanup error: {e}")
            return 0

    def _try_acquire_lock(self, module, user_id) -> bool:
        self._cleanup_expired_locks()

        try:
            lock = IELTSGenerationLock(
                module=module,
                status='generating',
                locked_by=user_id,
                locked_at=datetime.now(timezone.utc),
                error=None,
            )
            _pool_db.session.add(lock)
            _pool_db.session.commit()
            logger.info(f" IELTS lock ACQUIRED for {module} by user {user_id}")
            return True
        except IntegrityError:
            _pool_db.session.rollback()
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS lock insert error {module}: {e}")
            return False

        try:
            existing = IELTSGenerationLock.query.filter_by(module=module).first()
        except Exception as e:
            logger.exception(f"IELTS lock re-query failed {module}: {e}")
            return False

        if not existing:
            return self._try_acquire_lock(module, user_id)

        status = (existing.status or '').lower()

        if status == 'generating':
            logger.info(
                f" IELTS {module} already generating — user {user_id} waits"
            )
            return False

        if status == 'failed':
            logger.info(
                f" Previous IELTS {module} generation failed — "
                f"user {user_id} retrying"
            )
            try:
                existing.status = 'generating'
                existing.locked_by = user_id
                existing.locked_at = datetime.now(timezone.utc)
                existing.error = None
                _pool_db.session.commit()
                return True
            except Exception as e:
                _pool_db.session.rollback()
                logger.exception(f"IELTS lock takeover failed {module}: {e}")
                return False

        logger.info(f" Cleaning stale '{status}' IELTS lock for {module}")
        try:
            _pool_db.session.delete(existing)
            _pool_db.session.commit()
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS stale lock delete failed {module}: {e}")
            return False

        return self._try_acquire_lock(module, user_id)

    def _release_lock(self, module, success, error=None):
        try:
            lock = IELTSGenerationLock.query.filter_by(module=module).first()
            if not lock:
                return
            if success:
                _pool_db.session.delete(lock)
                _pool_db.session.commit()
                logger.info(f" IELTS lock RELEASED (success) for {module}")
            else:
                lock.status = 'failed'
                lock.error = (error or 'Unknown error')[:500]
                _pool_db.session.commit()
                logger.warning(f" IELTS lock FAILED for {module}: {error}")
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS lock release error {module}: {e}")

    # ─── PUBLIC ENTRY POINT ────────────────────────────────
    def get_or_generate(
        self,
        module: str,
        difficulty: str,
        user_id: int,
        generate_fn: Callable[[], Optional[Dict]],
    ) -> Tuple[Optional[Dict], str, Optional[int]]:
        """
        Serve an IELTS test. Returns (test_data, source, pool_id).
        source ∈ {'pool', 'generated', 'waiting', 'exhausted', 'failed'}
        """
        if module not in self.KNOWN_MODULES:
            logger.error(f"Unknown IELTS module: {module}")
            return None, 'failed', None

        user_exhausted = self.is_user_exhausted(user_id, module)
        pool_size = self.get_pool_size(module)

        if user_exhausted:
            logger.info(
                f" IELTS user {user_id} exhausted pool for {module} "
                f"({pool_size} tests done) — will generate fresh"
            )

        # Serve unseen pool test
        if not user_exhausted:
            pool_item = self._pick_unseen_for_user(user_id, module, difficulty)
            if pool_item:
                self._increment_pool_usage(pool_item)
                logger.info(
                    f" IELTS UNSEEN SERVE: user {user_id} → "
                    f"{module} #{pool_item.id} "
                    f"(usage now {pool_item.usage_count})"
                )
                return pool_item.test_data, 'pool', pool_item.id

        # Generate new
        if self.is_pool_full(module):
            logger.info(f" IELTS pool full for {module} — resetting usage")
            self._reset_usage_counts(module)
            pool_item = self._get_next_from_pool(module, difficulty)
            if pool_item:
                self._increment_pool_usage(pool_item)
                return pool_item.test_data, 'pool', pool_item.id
            return None, 'exhausted', None

        if not self._try_acquire_lock(module, user_id):
            return None, 'waiting', None

        try:
            reason = 'exhausted' if user_exhausted else 'empty-pool'
            logger.info(
                f" IELTS GENERATING {module} (user {user_id}, "
                f"reason={reason}, pool {pool_size}/{self.MAX_POOL_SIZE})"
            )
            result = generate_fn()

            if not result or not isinstance(result, dict):
                raise ValueError("generate_fn returned invalid result")

            success_flag = result.get('success', True)
            has_payload = self._has_payload(module, result)

            if success_flag and has_payload:
                pool_item = self._save_to_pool(module, difficulty, result, user_id)
                self._release_lock(module, success=True)
                return result, 'generated', pool_item.id

            err = result.get('error') or 'Empty payload'
            self._release_lock(module, success=False, error=str(err))
            return None, 'failed', None

        except Exception as e:
            logger.exception(f"IELTS generation exception for {module}: {e}")
            self._release_lock(module, success=False, error=str(e))
            return None, 'failed', None

    def _has_payload(self, module, result) -> bool:
        if module == 'ielts_listening':
            return bool(result.get('sections'))
        if module == 'ielts_reading':
            return bool(result.get('passages') or result.get('questions'))
        if module == 'ielts_writing':
            return bool(result.get('task1') and result.get('task2'))
        if module == 'ielts_speaking':
            return bool(result.get('part1') and result.get('part2'))
        return bool(result)

    # ─── ADMIN RESET ───────────────────────────────────────
    def reset_pool(self, module=None) -> int:
        try:
            q = IELTSTestPool.query
            if module:
                q = q.filter_by(module=module)
            count = q.delete(synchronize_session=False)
            _pool_db.session.commit()
            logger.info(f" IELTS pool reset: {count} items ({module or 'all'})")
            return count
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS pool reset failed: {e}")
            return 0

    def reset_locks(self) -> int:
        try:
            count = IELTSGenerationLock.query.delete(synchronize_session=False)
            _pool_db.session.commit()
            logger.info(f" IELTS locks cleared ({count})")
            return count
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS lock reset failed: {e}")
            return 0

    def reset_user_progress(self, user_id=None, module=None) -> int:
        try:
            q = IELTSUserPoolProgress.query
            if user_id is not None:
                q = q.filter_by(user_id=user_id)
            if module:
                q = q.filter_by(module=module)
            count = q.delete(synchronize_session=False)
            _pool_db.session.commit()
            logger.info(f" IELTS user progress reset ({count})")
            return count
        except Exception as e:
            _pool_db.session.rollback()
            logger.exception(f"IELTS user progress reset failed: {e}")
            return 0

    def reset_all(self) -> Dict[str, int]:
        return {
            'pool_items_deleted': self.reset_pool(None),
            'locks_deleted': self.reset_locks(),
            'user_progress_deleted': self.reset_user_progress(),
        }


# ═══════════════════════════════════════════════════════════════════════
# SINGLETONS
# ═══════════════════════════════════════════════════════════════════════
ielts_test_pool_manager = IELTSTestPoolManager()


__all__ = [
    'TestBankManager',
    'FullTestBankManager',
    'IELTSTestPoolManager',
    'ielts_test_pool_manager',
    'MAX_BANK_SIZE_PER_MODULE',
    'POOL_MAX_SIZE',
]