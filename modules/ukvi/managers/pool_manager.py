# modules/ukvi/managers/pool_manager.py
"""
UKVI Pool Manager — Same-university sharing + async queue.

Design (NOT like IELTS/PTE):
  • 1 pool set per (university, course, difficulty)
  • Same university → same pool set (with rephrase for freshness)
  • Different university → separate pool set
  • Per-user progress → NOT tracked
  • Dynamic cap → NOT used
  • Async queue → max 5 concurrent AI calls

Flow:
  1. cache_key = "ukvi_{university}_{course}_{difficulty}"
  2. Pool HIT  → return questions (rephrased)
  3. Pool MISS → check existing job for same cache_key
     ├─ Job exists → share job_id (multiple users)
     └─ No job    → create job + return job_id
"""

import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from models import db
from ..models import UKVITestBank, UKVIGenerationJob

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
JOB_TIMEOUT_SECONDS = 120          # 2 min max per job
STALE_JOB_MINUTES = 5              # Jobs older than this → failed
MAX_CONCURRENT_WORKERS = 5         # Concurrent AI calls
POOL_MAX_SETS_PER_UNI = 1          # 1 set per university
REPHRASE_PROBABILITY = 0.5         # 50% rephrase for freshness


class UKVIPoolManager:
    """Pool + queue for UKVI interview generation."""

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════
    @staticmethod
    def build_cache_key(university: str, course: str, difficulty: str = 'medium') -> str:
        """Build safe-length cache key."""
        uni = (university or 'unknown').strip().lower()[:80]
        course = (course or 'unknown').strip().lower()[:80]
        difficulty = (difficulty or 'medium').strip().lower()[:20]
        return f"ukvi_{uni}_{course}_{difficulty}"[:255]

    # ═══════════════════════════════════════════════════════════
    # POOL LOOKUP
    # ═══════════════════════════════════════════════════════════
    def get_from_pool(self, cache_key: str) -> Optional[List[Dict]]:
        """Return questions from pool if a set exists."""
        try:
            pooled = (
                UKVITestBank.query
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
            db.session.commit()

            logger.info(
                f" [UKVI POOL HIT] cache_key={cache_key} "
                f"usage={pooled.usage_count}"
            )
            return questions
        except Exception as e:
            db.session.rollback()
            logger.exception(f"get_from_pool failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    # JOB LOOKUP
    # ═══════════════════════════════════════════════════════════
    def find_active_job(self, cache_key: str) -> Optional[UKVIGenerationJob]:
        """Return existing queued/generating job for cache_key."""
        try:
            self._cleanup_stale_jobs()
            return (
                UKVIGenerationJob.query
                .filter(
                    UKVIGenerationJob.cache_key == cache_key,
                    UKVIGenerationJob.status.in_(['queued', 'generating'])
                )
                .order_by(UKVIGenerationJob.created_at.asc())
                .first()
            )
        except Exception as e:
            logger.exception(f"find_active_job failed: {e}")
            return None

    def create_job(
        self,
        user_id: int,
        cache_key: str,
        university: str,
        course: str,
        difficulty: str = 'medium',
    ) -> UKVIGenerationJob:
        """Create a new job."""
        job = UKVIGenerationJob(
            id=secrets.token_hex(8),
            user_id=user_id,
            cache_key=cache_key,
            university=university,
            course=course,
            difficulty=difficulty,
            status='queued',
        )
        db.session.add(job)
        db.session.commit()
        logger.info(
            f" [UKVI JOB CREATED] job_id={job.id} "
            f"user={user_id} key={cache_key}"
        )
        return job

    # ═══════════════════════════════════════════════════════════
    # JOB LIFECYCLE
    # ═══════════════════════════════════════════════════════════
    def mark_generating(self, job_id: str) -> bool:
        try:
            job = db.session.get(UKVIGenerationJob, job_id)
            if not job:
                return False
            job.status = 'generating'
            job.started_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.exception(f"mark_generating failed: {e}")
            return False

    def mark_complete(self, job_id: str, questions: List[Dict]) -> bool:
        """Mark job complete AND save to pool for reuse."""
        try:
            job = db.session.get(UKVIGenerationJob, job_id)
            if not job:
                return False

            # Save to pool (1 set per cache_key)
            existing = (
                UKVITestBank.query
                .filter_by(test_type='ukvi_interview', topic=job.cache_key)
                .first()
            )
            if not existing:
                pooled = UKVITestBank(
                    test_type='ukvi_interview',
                    difficulty=job.difficulty or 'medium',
                    topic=job.cache_key,
                    test_data=json.dumps({'questions': questions}),
                    usage_count=0,
                )
                db.session.add(pooled)
                logger.info(f" [UKVI POOL SAVE] cache_key={job.cache_key}")

            # Mark job complete
            job.status = 'complete'
            job.questions = json.dumps({'questions': questions})
            job.completed_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(f" [UKVI JOB COMPLETE] job_id={job_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.exception(f"mark_complete failed: {e}")
            return False

    def mark_failed(self, job_id: str, error: str) -> bool:
        try:
            job = db.session.get(UKVIGenerationJob, job_id)
            if not job:
                return False
            job.status = 'failed'
            job.error = (error or 'Unknown error')[:500]
            job.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.warning(f" [UKVI JOB FAILED] job_id={job_id}: {error}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.exception(f"mark_failed failed: {e}")
            return False

    def get_job(self, job_id: str) -> Optional[UKVIGenerationJob]:
        try:
            return db.session.get(UKVIGenerationJob, job_id)
        except Exception:
            return None

    def get_queue_position(self, job_id: str) -> int:
        """Position in queue (1-based). 0 if not queued."""
        try:
            job = db.session.get(UKVIGenerationJob, job_id)
            if not job or job.status != 'queued':
                return 0

            ahead = (
                UKVIGenerationJob.query
                .filter(
                    UKVIGenerationJob.status == 'queued',
                    UKVIGenerationJob.created_at < job.created_at
                )
                .count()
            )
            return ahead + 1
        except Exception:
            return 0

    # ═══════════════════════════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════════════════════════
    def _cleanup_stale_jobs(self) -> int:
        """Mark stale queued/generating jobs as failed."""
        try:
            cutoff = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(minutes=STALE_JOB_MINUTES)
            )
            stale = (
                UKVIGenerationJob.query
                .filter(
                    UKVIGenerationJob.status.in_(['queued', 'generating']),
                    UKVIGenerationJob.created_at < cutoff
                )
                .all()
            )
            for job in stale:
                job.status = 'failed'
                job.error = 'Timeout (stale job cleaned up)'
                job.completed_at = datetime.now(timezone.utc)
            if stale:
                db.session.commit()
                logger.warning(f" Cleaned up {len(stale)} stale UKVI jobs")
            return len(stale)
        except Exception as e:
            db.session.rollback()
            logger.exception(f"_cleanup_stale_jobs failed: {e}")
            return 0

    # ═══════════════════════════════════════════════════════════
    # ADMIN
    # ═══════════════════════════════════════════════════════════
    def get_stats(self) -> Dict:
        try:
            total_pool = UKVITestBank.query.filter_by(
                test_type='ukvi_interview'
            ).count()

            queued = UKVIGenerationJob.query.filter_by(status='queued').count()
            generating = UKVIGenerationJob.query.filter_by(status='generating').count()
            complete = UKVIGenerationJob.query.filter_by(status='complete').count()
            failed = UKVIGenerationJob.query.filter_by(status='failed').count()

            return {
                'pool_size': total_pool,
                'jobs': {
                    'queued': queued,
                    'generating': generating,
                    'complete': complete,
                    'failed': failed,
                    'total': queued + generating + complete + failed,
                },
                'max_concurrent': MAX_CONCURRENT_WORKERS,
            }
        except Exception as e:
            logger.exception(f"get_stats failed: {e}")
            return {}

    def reset_pool(self) -> int:
        try:
            count = UKVITestBank.query.filter_by(
                test_type='ukvi_interview'
            ).delete(synchronize_session=False)
            db.session.commit()
            logger.info(f" UKVI pool reset: {count} entries deleted")
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"reset_pool failed: {e}")
            return 0

    def reset_jobs(self) -> int:
        try:
            count = UKVIGenerationJob.query.delete(synchronize_session=False)
            db.session.commit()
            logger.info(f" UKVI jobs reset: {count} deleted")
            return count
        except Exception as e:
            db.session.rollback()
            logger.exception(f"reset_jobs failed: {e}")
            return 0


# ═══════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════
ukvi_pool_manager = UKVIPoolManager()

__all__ = ['UKVIPoolManager', 'ukvi_pool_manager']