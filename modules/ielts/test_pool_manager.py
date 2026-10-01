# modules/ielts/test_pool_manager.py
"""
IELTS Test Pool Manager — Shared pool + dynamic growth + per-user progress.

v2.3 — MUTABLEDICT FIX (SQLAlchemy):
  • `get_or_generate()` अब `pool_item.test_data` लाई `dict()` मा convert
    गरेर return गर्छ। यसले app.py मा `result['_pool_id'] = pool_id` गर्दा
    आउने `Can't flag attribute 'test_data' modified` error हटाउँछ।
  • ६ return statements fix: 4× pool_item.test_data, 2× result dict.

v2.2 — THREAD-SAFE CAP PERSISTENCE (race-condition fix):
  • Added module-level `_CAPS_LOCK` (RLock) so concurrent Flask threads
    can't clobber each other's cap increments.
  • `_bump_cap()`, `set_pool_cap()`, `reset_caps()` now perform an
    atomic read-modify-write under the lock.
  • `_save_caps()` also acquires the lock for standalone use.
  • Multi-process note: threading.RLock only protects within one process.

v2.1 — JSON-FILE CAP PERSISTENCE (no models.py dependency):
  • Removed `IELTSSetting` import (not in models.py).
  • Cap persisted to `data/ielts_pool_caps.json` (survives restarts).

v2.0 — DYNAMIC POOL SIZING (PTE parity):
  • Each module starts at `initial` size.
  • When user exhausts ALL pool tests → cap grows by `step` (up to `max`).
  • One fresh test generated immediately for that exhausted user.

Design:
  • Pool holds up to dynamic cap tests per module (initial=100, max=1000).
  • Shared across all users.
  • Per-user progress tracked in IELTSUserPoolProgress.
  • Thundering-herd safe: SQL unique constraint on lock table.
  • Round-robin: least-used unseen test served first.
  • Auto-cleanup: stale locks (>5 min) force-released.

Modules:
  • ielts_listening
  • ielts_reading
  • ielts_writing
  • ielts_speaking
  • full_ielts
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, Callable, List, Set

from sqlalchemy.exc import IntegrityError

# IELTS pool classes live in the top-level models.py
from models import (
    db,
    IELTSTestPool,
    IELTSGenerationLock,
    IELTSUserPoolProgress,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# JSON-FILE CAP PERSISTENCE (replaces IELTSSetting)
# ═══════════════════════════════════════════════════════════════════════
_CAPS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data',
    'ielts_pool_caps.json',
)

# v2.2 — process-wide lock so concurrent _bump_cap() calls from
# multiple Flask threads don't lose increments.
# RLock allows nested acquire (e.g. _save_caps inside _bump_cap).
_CAPS_LOCK = threading.RLock()


def _load_caps() -> Dict[str, int]:
    """
    Load cap values from JSON file. Returns {} on any failure.

    NOTE: callers that need atomic read-modify-write MUST hold _CAPS_LOCK.
    This function itself does not take the lock (so it can be called
    safely from inside a locked section).
    """
    try:
        if os.path.exists(_CAPS_FILE_PATH):
            with open(_CAPS_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {k: int(v) for k, v in data.items()}
    except Exception as e:
        logger.warning(f"Could not load pool caps file: {e}")
    return {}


def _write_caps_unlocked(caps: Dict[str, int]) -> None:
    """
    v2.2 — internal writer. Assumes caller holds _CAPS_LOCK.
    Use this inside locked sections to avoid redundant lock churn.
    """
    try:
        os.makedirs(os.path.dirname(_CAPS_FILE_PATH), exist_ok=True)
        with open(_CAPS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(caps, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save pool caps file: {e}")


def _save_caps(caps: Dict[str, int]) -> None:
    """
    Persist cap values to JSON file. Silent on failure.

    v2.2 — acquires _CAPS_LOCK so standalone calls are thread-safe.
    Nested calls (from inside _bump_cap / set_pool_cap / reset_caps) are
    safe because _CAPS_LOCK is an RLock.
    """
    with _CAPS_LOCK:
        _write_caps_unlocked(caps)


# ═══════════════════════════════════════════════════════════════════════
# DYNAMIC POOL CONFIG (PTE parity)
# ═══════════════════════════════════════════════════════════════════════
MODULE_POOL_CONFIG: Dict[str, Dict[str, int]] = {
    'ielts_listening': {'initial': 100, 'step': 100, 'max': 1000},
    'ielts_reading': {'initial': 100, 'step': 100, 'max': 1000},
    'ielts_writing': {'initial': 100, 'step': 100, 'max': 1000},
    'ielts_speaking': {'initial': 100, 'step': 100, 'max': 1000},
    'full_ielts': {'initial': 50, 'step': 50, 'max': 500},
}

# ═══════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT: MAX_POOL_SIZE
# ═══════════════════════════════════════════════════════════════════════
MAX_POOL_SIZE = max(cfg['max'] for cfg in MODULE_POOL_CONFIG.values())  # 1000

LOCK_TIMEOUT_SECONDS = 300  # 5 min — force-release stale locks
RETRY_AFTER_SECONDS = 3     # client retry interval after 202

KNOWN_MODULES = list(MODULE_POOL_CONFIG.keys())


class IELTSTestPoolManager:
    """Shared pool + dynamic growth + user progress + single-generator election."""

    # ═════════════════════════════════════════════════════════════════
    # HELPERS — MutableDict → plain dict
    # ═════════════════════════════════════════════════════════════════
    @staticmethod
    def _to_plain_dict(data: Any) -> Optional[Dict]:
        """
        v2.3 — Convert SQLAlchemy MutableDict (or None) to plain dict.

        SQLAlchemy 2.0 stores JSON columns as MutableDict, which raises
        `InvalidRequestError: Can't flag attribute modified` when the
        calling code (e.g. app.py) tries to mutate it after the object
        is detached.  We return a plain `dict` to avoid that.

        Safe for None / non-dict values.
        """
        if data is None:
            return None
        if isinstance(data, dict):
            # Copy to a plain built-in dict (detaches from SQLAlchemy)
            return dict(data)
        try:
            return dict(data)
        except (TypeError, ValueError):
            return data

    # ═════════════════════════════════════════════════════════════════
    # DYNAMIC CAP — READ / BUMP / SET / RESET
    # ═════════════════════════════════════════════════════════════════
    def _get_cap(self, module: str) -> int:
        """
        Return current cap for a module.
        Cap starts at `initial` and grows by `step` when users exhaust it.
        Persisted to `data/ielts_pool_caps.json` (survives restarts).

        v2.2 — the initialize-on-first-read path is atomic under
        _CAPS_LOCK to prevent duplicate writes from concurrent threads.
        """
        cfg = MODULE_POOL_CONFIG.get(module)
        if not cfg:
            return 0

        with _CAPS_LOCK:
            caps = _load_caps()
            if module in caps:
                try:
                    cap = int(caps[module])
                    return max(cfg['initial'], min(cap, cfg['max']))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid stored cap for {module}: {e}")

            # First time — initialize to `initial` and persist
            caps[module] = cfg['initial']
            _write_caps_unlocked(caps)
            return cfg['initial']

    def _bump_cap(self, module: str) -> Tuple[int, int]:
        """
        Increase the module's cap by `step` (up to `max`).
        Returns (old_cap, new_cap).

        v2.2 — ENTIRE read-modify-write is atomic under _CAPS_LOCK.
        """
        cfg = MODULE_POOL_CONFIG.get(module)
        if not cfg:
            return (0, 0)

        with _CAPS_LOCK:
            caps = _load_caps()

            # Resolve current cap (with init-on-first-read)
            if module in caps:
                try:
                    old_cap = int(caps[module])
                except (ValueError, TypeError):
                    old_cap = cfg['initial']
            else:
                old_cap = cfg['initial']

            # Clamp within [initial, max]
            old_cap = max(cfg['initial'], min(old_cap, cfg['max']))

            if old_cap >= cfg['max']:
                # Persist the (possibly initialized) value and exit
                caps[module] = old_cap
                _write_caps_unlocked(caps)
                return (old_cap, old_cap)  # already maxed

            new_cap = min(old_cap + cfg['step'], cfg['max'])
            caps[module] = new_cap
            _write_caps_unlocked(caps)

        logger.info(
            f" IELTS cap bumped for {module}: {old_cap} → {new_cap} "
            f"(max={cfg['max']})"
        )
        return (old_cap, new_cap)

    def get_pool_cap(self, module: str) -> int:
        """Public helper for admin UI."""
        return self._get_cap(module)

    def set_pool_cap(self, module: str, cap: int) -> bool:
        """
        Admin override — set explicit cap (bounded by config).

        v2.2 — atomic under _CAPS_LOCK.
        """
        cfg = MODULE_POOL_CONFIG.get(module)
        if not cfg:
            return False
        cap = max(cfg['initial'], min(int(cap), cfg['max']))
        with _CAPS_LOCK:
            caps = _load_caps()
            caps[module] = cap
            _write_caps_unlocked(caps)
        logger.info(f" IELTS pool cap SET for {module}: {cap}")
        return True

    def reset_caps(self) -> Dict[str, int]:
        """
        Reset all module caps back to their `initial` values.

        v2.2 — atomic under _CAPS_LOCK.
        """
        result = {}
        with _CAPS_LOCK:
            caps = _load_caps()
            for mod, cfg in MODULE_POOL_CONFIG.items():
                caps[mod] = cfg['initial']
                result[mod] = cfg['initial']
                logger.info(f" IELTS cap reset for {mod}: → {cfg['initial']}")
            _write_caps_unlocked(caps)
        return result

    # ═════════════════════════════════════════════════════════════════
    # POOL — READ / STATS
    # ═════════════════════════════════════════════════════════════════
    def get_pool_size(self, module: str) -> int:
        """Count active pool items for a module."""
        try:
            return IELTSTestPool.query.filter_by(module=module, is_active=True).count()
        except Exception as e:
            logger.exception(f"get_pool_size failed for {module}: {e}")
            return 0

    def is_pool_full(self, module: str) -> bool:
        """True if pool has reached its current dynamic cap."""
        return self.get_pool_size(module) >= self._get_cap(module)

    def is_pool_at_hard_max(self, module: str) -> bool:
        """True if pool has hit the absolute safety cap."""
        cfg = MODULE_POOL_CONFIG.get(module)
        if not cfg:
            return True
        return self.get_pool_size(module) >= cfg['max']

    def get_pool_stats(self) -> Dict[str, Dict[str, Any]]:
        """Full stats for all known modules."""
        stats: Dict[str, Dict[str, Any]] = {}
        for mod in KNOWN_MODULES:
            cfg = MODULE_POOL_CONFIG[mod]
            size = self.get_pool_size(mod)
            cap = self._get_cap(mod)
            stats[mod] = {
                'size': size,
                'cap': cap,
                'initial': cfg['initial'],
                'step': cfg['step'],
                'max': cfg['max'],
                'remaining_to_cap': max(0, cap - size),
                'at_cap': size >= cap,
                'at_max': size >= cfg['max'],
            }
        return stats

    def list_pool_items(self, module: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Debug helper — return basic info about pool items."""
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

    # ═════════════════════════════════════════════════════════════════
    # USER PROGRESS
    # ═════════════════════════════════════════════════════════════════
    def get_user_completed_pool_ids(self, user_id: int, module: str) -> Set[int]:
        """Return set of pool_id this user has completed in this module."""
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
        """Return set of active pool_id for this module."""
        try:
            rows = (
                IELTSTestPool.query
                .filter_by(module=module, is_active=True)
                .all()
            )
            return {r.id for r in rows}
        except Exception as e:
            logger.exception(f"get_active_pool_ids failed for {module}: {e}")
            return set()

    def is_user_exhausted(self, user_id: int, module: str) -> bool:
        """
        True if user has completed ALL active pool tests for this module.
        This is the trigger condition for generating a fresh test.
        """
        active = self.get_active_pool_ids(module)
        if not active:
            return False  # pool empty → normal flow handles
        completed = self.get_user_completed_pool_ids(user_id, module)
        return active.issubset(completed)

    def record_user_progress(self, user_id: int, module: str, pool_id: int) -> bool:
        """
        Called when user submits a test that came from pool_id.
        Idempotent per (user_id, module, pool_id).
        """
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
            db.session.add(row)
            db.session.commit()
            logger.info(
                f" User progress: user {user_id} completed pool #{pool_id} "
                f"({module})"
            )
            return True
        except IntegrityError:
            db.session.rollback()
            return True  # already recorded (race-safe)
        except Exception as e:
            db.session.rollback()
            logger.exception(f"record_user_progress failed: {e}")
            return False

    def _pick_unseen_for_user(
        self, user_id: int, module: str, difficulty: str
    ) -> Optional[IELTSTestPool]:
        """
        Pick a pool test the user has NOT yet completed.
        Round-robin (least-used first) among unseen tests.
        """
        completed = self.get_user_completed_pool_ids(user_id, module)

        # Exact difficulty match first
        q = IELTSTestPool.query.filter_by(
            module=module, difficulty=difficulty, is_active=True
        )
        if completed:
            q = q.filter(~IELTSTestPool.id.in_(completed))
        item = (
            q.order_by(IELTSTestPool.usage_count.asc(), IELTSTestPool.created_at.asc())
            .first()
        )
        if item:
            return item

        # Fallback: any difficulty
        q2 = IELTSTestPool.query.filter_by(module=module, is_active=True)
        if completed:
            q2 = q2.filter(~IELTSTestPool.id.in_(completed))
        return (
            q2.order_by(IELTSTestPool.usage_count.asc(), IELTSTestPool.created_at.asc())
            .first()
        )

    # ═════════════════════════════════════════════════════════════════
    # POOL — FALLBACK PICK / INCREMENT / RESET
    # ═════════════════════════════════════════════════════════════════
    def _get_next_from_pool(
        self, module: str, difficulty: str = 'medium'
    ) -> Optional[IELTSTestPool]:
        """
        Round-robin pick (ignores user progress).
        Used only when user is exhausted or as a fallback.
        """
        item = (
            IELTSTestPool.query
            .filter_by(module=module, difficulty=difficulty, is_active=True)
            .order_by(IELTSTestPool.usage_count.asc(), IELTSTestPool.created_at.asc())
            .first()
        )
        if item:
            return item
        return (
            IELTSTestPool.query
            .filter_by(module=module, is_active=True)
            .order_by(IELTSTestPool.usage_count.asc(), IELTSTestPool.created_at.asc())
            .first()
        )

    def _increment_pool_usage(self, pool_item: IELTSTestPool) -> None:
        """Atomically increment usage_count."""
        try:
            pool_item.usage_count = (pool_item.usage_count or 0) + 1
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Failed to increment pool usage for #{pool_item.id}: {e}")

    def _reset_usage_counts(self, module: str) -> int:
        """Reset usage_count = 0 for all items in a module. Returns rows updated."""
        try:
            count = (
                IELTSTestPool.query
                .filter_by(module=module)
                .update({'usage_count': 0})
            )
            db.session.commit()
            logger.info(f" Pool usage reset for module {module} ({count} items)")
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Failed to reset usage for {module}: {e}")
            return 0

    def _count_questions(self, module: str, test_data: Dict) -> int:
        """Count total questions in a test_data payload."""
        try:
            if module == 'full_ielts':
                total = 0
                for phase in ('listening', 'reading', 'writing', 'speaking'):
                    phase_data = test_data.get(phase) or {}
                    if isinstance(phase_data, dict):
                        for key in ('sections', 'passages', 'questions'):
                            items = phase_data.get(key) or []
                            if isinstance(items, list):
                                total += len(items)
                return total

            if module == 'ielts_listening':
                sections = test_data.get('sections') or []
                total = 0
                for sec in sections:
                    if isinstance(sec, dict):
                        total += len(sec.get('questions') or [])
                return total

            if module == 'ielts_reading':
                passages = test_data.get('passages') or []
                total = 0
                for p in passages:
                    if isinstance(p, dict):
                        total += len(p.get('questions') or [])
                if total == 0:
                    total = len(test_data.get('questions') or [])
                return total

            if module == 'ielts_writing':
                count = 0
                if test_data.get('task1'):
                    count += 1
                if test_data.get('task2'):
                    count += 1
                return count

            if module == 'ielts_speaking':
                count = 0
                for part in ('part1', 'part2', 'part3'):
                    if test_data.get(part):
                        count += 1
                return count

            return len(test_data.get('questions') or [])
        except Exception:
            return 0

    def _has_payload(self, module: str, result: Dict) -> bool:
        """Return True if the generated payload contains usable content."""
        if not isinstance(result, dict):
            return False
        if module == 'ielts_listening':
            return bool(result.get('sections'))
        if module == 'ielts_reading':
            return bool(result.get('passages') or result.get('questions'))
        if module == 'ielts_writing':
            return bool(result.get('task1') or result.get('task2'))
        if module == 'ielts_speaking':
            return bool(result.get('part1') or result.get('part2') or result.get('part3'))
        if module == 'full_ielts':
            return bool(result.get('listening') or result.get('reading')
                        or result.get('writing') or result.get('speaking'))
        return bool(result.get('questions') or result.get('sections'))

    def _save_to_pool(
        self,
        module: str,
        difficulty: str,
        test_data: Dict,
        user_id: int,
    ) -> Optional[IELTSTestPool]:
        """Persist a freshly generated test into the shared pool."""
        total = self._count_questions(module, test_data)
        try:
            pool_item = IELTSTestPool(
                module=module,
                difficulty=difficulty,
                test_data=test_data,
                created_by=user_id,
                question_count=total,
                usage_count=0,
                is_active=True,
            )
            db.session.add(pool_item)
            db.session.commit()

            logger.info(
                f" IELTS Pool SAVE: {module} #{pool_item.id} "
                f"({total} questions) by user {user_id} "
                f"[pool now {self.get_pool_size(module)}/{self._get_cap(module)}]"
            )
            return pool_item
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Failed to save to pool for {module}: {e}")
            return None

    # ═════════════════════════════════════════════════════════════════
    # LOCK — CLEANUP / ACQUIRE / RELEASE
    # ═════════════════════════════════════════════════════════════════
    def _cleanup_expired_locks(self) -> int:
        """Force-release locks held > LOCK_TIMEOUT_SECONDS."""
        try:
            cutoff = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(seconds=LOCK_TIMEOUT_SECONDS)
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
                    f" Force-releasing expired IELTS lock for {lock.module} "
                    f"(held by user {lock.locked_by} since {lock.locked_at})"
                )
                db.session.delete(lock)
            if expired:
                db.session.commit()
            return len(expired)
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Lock cleanup error: {e}")
            return 0

    def _try_acquire_lock(self, module: str, user_id: int) -> bool:
        """Atomically try to become the generator for this module."""
        self._cleanup_expired_locks()

        try:
            lock = IELTSGenerationLock(
                module=module,
                status='generating',
                locked_by=user_id,
                locked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                error=None,
            )
            db.session.add(lock)
            db.session.commit()
            logger.info(f" IELTS Lock ACQUIRED for {module} by user {user_id}")
            return True
        except IntegrityError:
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Unexpected lock insert error for {module}: {e}")
            return False

        try:
            existing = IELTSGenerationLock.query.filter_by(module=module).first()
        except Exception as e:
            logger.exception(f"Lock re-query failed for {module}: {e}")
            return False

        if not existing:
            return self._try_acquire_lock(module, user_id)

        status = (existing.status or '').lower()

        if status == 'generating':
            logger.info(
                f" {module} already generating "
                f"(by user {existing.locked_by}) — user {user_id} will wait"
            )
            return False

        if status == 'failed':
            logger.info(
                f" Previous {module} generation failed "
                f"({existing.error or 'no error'}) — user {user_id} retrying"
            )
            try:
                existing.status = 'generating'
                existing.locked_by = user_id
                existing.locked_at = datetime.now(timezone.utc).replace(tzinfo=None)
                existing.error = None
                db.session.commit()
                return True
            except Exception as e:
                db.session.rollback()
                logger.exception(f"Failed to take over failed lock for {module}: {e}")
                return False

        logger.info(f" Cleaning stale '{status}' lock for {module}")
        try:
            db.session.delete(existing)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Failed to delete stale lock for {module}: {e}")
            return False

        return self._try_acquire_lock(module, user_id)

    def _release_lock(
        self, module: str, success: bool, error: Optional[str] = None
    ) -> None:
        """Release the lock after generation."""
        try:
            lock = IELTSGenerationLock.query.filter_by(module=module).first()
            if not lock:
                return

            if success:
                db.session.delete(lock)
                db.session.commit()
                logger.info(f" IELTS Lock RELEASED (success) for {module}")
            else:
                lock.status = 'failed'
                lock.error = (error or 'Unknown error')[:500]
                db.session.commit()
                logger.warning(f" IELTS Lock marked FAILED for {module}: {error}")
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Lock release error for {module}: {e}")

    # ═════════════════════════════════════════════════════════════════
    # PUBLIC — MAIN ENTRY POINT
    # ═════════════════════════════════════════════════════════════════
    def get_or_generate(
        self,
        module: str,
        difficulty: str,
        user_id: int,
        generate_fn: Callable[[], Optional[Dict]],
    ) -> Tuple[Optional[Dict], str, Optional[int]]:
        """
        Serve test to user.

        Flow (v2.3 — MutableDict fix):
          1. Serve unseen pool test (fast path).       → dict(test_data)
          2. If user exhausted:
             a. If cap < max → bump cap by `step`.
             b. Generate 1 fresh test NOW (synchronous).
             c. If sync generation fails → reset user progress, serve oldest.
          3. If pool empty (first ever) → generate fresh.

        Returns:
            (test_data, source, pool_id)
              source ∈ {'pool', 'generated', 'waiting', 'exhausted', 'failed'}
        """
        if module not in KNOWN_MODULES:
            logger.error(f"Unknown IELTS module requested: {module}")
            return None, 'failed', None

        user_exhausted = self.is_user_exhausted(user_id, module)
        pool_size = self.get_pool_size(module)
        current_cap = self._get_cap(module)

        # ─── 1. Serve unseen pool test (fast path) ─────────────────
        if not user_exhausted:
            pool_item = self._pick_unseen_for_user(user_id, module, difficulty)
            if pool_item:
                self._increment_pool_usage(pool_item)
                logger.info(
                    f" IELTS UNSEEN SERVE: user {user_id} → {module} #{pool_item.id} "
                    f"(usage now {pool_item.usage_count}, pool {pool_size}/{current_cap})"
                )
                # v2.3 — return plain dict (avoid MutableDict mutation error)
                return self._to_plain_dict(pool_item.test_data), 'pool', pool_item.id

            logger.info(
                f" No unseen IELTS pool test for user {user_id} in {module} — "
                f"will generate (pool {pool_size}/{current_cap})"
            )

        # ─── 2. User exhausted OR pool empty ───────────────────────
        if user_exhausted:
            old_cap, new_cap = self._bump_cap(module)
            if new_cap > old_cap:
                logger.info(
                    f" IELTS cap bumped for {module}: {old_cap} → {new_cap} "
                    f"(user {user_id} triggered growth)"
                )
            else:
                logger.info(
                    f" IELTS cap already at max for {module} ({new_cap}) — "
                    f"will reset user {user_id} progress and recycle"
                )

        # ─── 3. GENERATE 1 FRESH TEST ──────────────────────────────
        if self.is_pool_at_hard_max(module) and user_exhausted:
            logger.info(
                f" IELTS pool at hard max for {module} ({pool_size}) — "
                f"resetting user {user_id} progress and re-serving"
            )
            self.reset_user_progress(user_id=user_id, module=module)
            self._reset_usage_counts(module)
            pool_item = self._get_next_from_pool(module, difficulty)
            if pool_item:
                self._increment_pool_usage(pool_item)
                # v2.3 — plain dict
                return self._to_plain_dict(pool_item.test_data), 'pool', pool_item.id
            return None, 'exhausted', None

        if not self._try_acquire_lock(module, user_id):
            return None, 'waiting', None

        try:
            reason = 'exhausted' if user_exhausted else 'empty-pool'
            logger.info(
                f" IELTS GENERATING {module} (user {user_id}, reason={reason}, "
                f"pool {pool_size}/{current_cap})"
            )
            result = generate_fn()

            if not result:
                raise ValueError("generate_fn returned None")
            if not isinstance(result, dict):
                raise ValueError(f"generate_fn returned non-dict: {type(result)}")

            success_flag = result.get('success', True)
            has_payload = self._has_payload(module, result)

            if success_flag and has_payload:
                pool_item = self._save_to_pool(module, difficulty, result, user_id)
                self._release_lock(module, success=True)

                if pool_item:
                    # v2.3 — plain dict
                    return self._to_plain_dict(result), 'generated', pool_item.id

                logger.warning(
                    f" IELTS pool save failed for {module} — serving without pool_id"
                )
                # v2.3 — plain dict
                return self._to_plain_dict(result), 'generated', None

            error_msg = result.get('error') or 'Generation returned empty payload'
            logger.error(f" IELTS generation failed for {module}: {error_msg}")
            self._release_lock(module, success=False, error=str(error_msg))

            if user_exhausted:
                logger.info(
                    f"↩ IELTS fallback: resetting user {user_id} and serving from pool"
                )
                self.reset_user_progress(user_id=user_id, module=module)
                pool_item = self._get_next_from_pool(module, difficulty)
                if pool_item:
                    self._increment_pool_usage(pool_item)
                    # v2.3 — plain dict
                    return self._to_plain_dict(pool_item.test_data), 'pool', pool_item.id

            return None, 'failed', None

        except Exception as e:
            logger.exception(f"IELTS generation exception for {module}: {e}")
            self._release_lock(module, success=False, error=str(e))

            if user_exhausted:
                self.reset_user_progress(user_id=user_id, module=module)
                pool_item = self._get_next_from_pool(module, difficulty)
                if pool_item:
                    self._increment_pool_usage(pool_item)
                    # v2.3 — plain dict
                    return self._to_plain_dict(pool_item.test_data), 'pool', pool_item.id

            return None, 'failed', None

    # ═════════════════════════════════════════════════════════════════
    # ADMIN — RESET / STATS
    # ═════════════════════════════════════════════════════════════════
    def reset_pool(self, module: Optional[str] = None) -> int:
        """Delete all pool items (or one module's). Returns count deleted."""
        try:
            q = IELTSTestPool.query
            if module:
                q = q.filter_by(module=module)
            count = q.delete(synchronize_session=False)
            db.session.commit()
            logger.info(f" IELTS pool reset: deleted {count} items ({module or 'all'})")
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Pool reset failed: {e}")
            return 0

    def reset_locks(self) -> int:
        """Delete all rows from the lock table. Returns count deleted."""
        try:
            count = IELTSGenerationLock.query.delete(synchronize_session=False)
            db.session.commit()
            logger.info(f" IELTS lock table cleared ({count} rows)")
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Lock reset failed: {e}")
            return 0

    def reset_user_progress(
        self, user_id: Optional[int] = None, module: Optional[str] = None
    ) -> int:
        """Delete user-progress rows (all or filtered). Returns count deleted."""
        try:
            q = IELTSUserPoolProgress.query
            if user_id is not None:
                q = q.filter_by(user_id=user_id)
            if module:
                q = q.filter_by(module=module)
            count = q.delete(synchronize_session=False)
            db.session.commit()
            logger.info(
                f" IELTS user progress reset ({count} rows, user={user_id or 'all'}, "
                f"module={module or 'all'})"
            )
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"User progress reset failed: {e}")
            return 0

    def reset_all(self) -> Dict[str, int]:
        """Full reset — pool + locks + user progress. Caps NOT reset."""
        pools_deleted = self.reset_pool(None)
        locks_deleted = self.reset_locks()
        progress_deleted = self.reset_user_progress()
        return {
            'pool_items_deleted': pools_deleted,
            'locks_deleted': locks_deleted,
            'user_progress_deleted': progress_deleted,
        }


# ═══════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════
ielts_test_pool_manager = IELTSTestPoolManager()


__all__ = [
    'IELTSTestPoolManager',
    'ielts_test_pool_manager',
    'MODULE_POOL_CONFIG',
    'KNOWN_MODULES',
    'RETRY_AFTER_SECONDS',
    'MAX_POOL_SIZE',
]