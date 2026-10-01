# modules/ielts/writing/repository.py
"""Database operations for IELTS Writing module - PURE DATABASE, NO FALLBACKS
Now supports multiple modules (ielts, pte, ukvi) via a `module` column.

FIXES APPLIED (v2):
  (1) `get_user_stats` now returns a DETERMINISTIC `recent_band`.
        Previously the query had no ORDER BY, so `bands[-1]` depended on
        whatever order the DB engine happened to return rows in — under
        some engines "recent" became "oldest". Now the query orders by
        `created_at ASC` and `recent_band` is truly the most recent.

  (2) `_normalize_json` no longer returns a raw string for non-JSON
        input. It now guarantees the JSON column always contains a dict
        or list, wrapping plain strings in `{"text": value}` and other
        scalars in `{"value": value}`. This makes the write→read round
        trip predictable regardless of what the caller passes in.

  (3) `save_test` now normalizes `serial_number`:
        · ints pass through unchanged
        · digit-strings are coerced to int
        · non-numeric strings (e.g. uuid hex like "abc12345") are
          replaced with a random int fallback
        This is required because the generator was switched to emit
        `uuid.uuid4().hex[:8]` for collision resistance, but the
        `serial_number` column is still an Integer.

  (4) Added an indexed `content_hash` column to `WritingTestRecord`
        so TestBank dedup can be done with an indexed equality lookup
        instead of an in-Python scan of every candidate row. The API
        layer also falls back to in-Python comparison when the hash is
        missing (e.g. rows created before this migration).

         Migration note: SQLite/Postgres `create_all` will NOT add a
        new column to an existing table. Existing deployments must run
        one of:
            ALTER TABLE writing_tests ADD COLUMN content_hash VARCHAR(64);
            CREATE INDEX IF NOT EXISTS idx_writing_content_hash
                ON writing_tests(content_hash);
        or drop & recreate `writing_tests`.

  (5) `save_essay` now defensively coerces `test_id`:
        · None / "" → NULL
        · int → passed through
        · digit-string → int
        · anything else (e.g. "w_abc123") → NULL
        The FK column already allows NULL, so this never breaks the
        insert when the caller has no valid integer id.

  (6) `save_essay` now uses a single `_try_int()` helper and no longer
        relies on the caller having pre-normalized the value.

  (7) Added `find_test_by_content_hash()` for the dedup path.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, DateTime, JSON,
    ForeignKey, Index,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

logger = logging.getLogger(__name__)
Base = declarative_base()


# ============================================================
# HELPERS
# ============================================================
def _normalize_json(value: Any) -> Any:
    """
    Coerce a value destined for a JSON column into a dict or list.

    Ensures the column always contains structured data, so read-back
    is predictable regardless of the caller's input type.

    · None → None
    · dict / list → as-is
    · str (valid JSON) → parsed, if it yields dict/list; else wrapped
    · str (plain text) → {"text": <original>}
    · int / float / bool → {"value": <original>}
    · other → {"value": str(original)}
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass
        return {"text": value}
    if isinstance(value, (int, float, bool)):
        return {"value": value}
    try:
        return {"value": str(value)}
    except Exception:
        return {"value": ""}


def _try_int(value: Any) -> Optional[int]:
    """Best-effort coercion of a value to int. Returns None on failure."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None # avoid True → 1
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _compute_content_hash(payload: Any) -> str:
    """
    Deterministic SHA-256 of a JSON-able payload.

    Uses sort_keys=True so key order doesn't affect the hash, and
    separators=(',', ':') so whitespace doesn't either.
    """
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, default=str)
    except Exception:
        canonical = str(payload)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


# ============================================================
# MODELS
# ============================================================
class WritingTestRecord(Base):
    """Database record for writing tests - module-aware."""
    __tablename__ = 'writing_tests'

    id = Column(Integer, primary_key=True, autoincrement=True)
    serial_number = Column(Integer, unique=True, index=True)
    topic = Column(String(200))
    difficulty = Column(String(50))
    exam_type = Column(String(50), default='ielts') # legacy alias
    module = Column(String(20), default='ielts', index=True) # ielts, pte, ukvi
    task1_prompt = Column(Text)
    task1_chart_type = Column(String(50))
    task1_chart_data = Column(JSON)
    task2_prompt = Column(Text)
    usage_count = Column(Integer, default=0)
    avg_score = Column(Float, default=0.0)

    # NEW: indexable, deterministic dedup key for TestBank reuse.
    # Existing rows will have NULL until backfilled.
    content_hash = Column(String(64), nullable=True, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    essays = relationship(
        "WritingEssayRecord",
        back_populates="test",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index('idx_writing_module', 'module'),
        Index('idx_writing_module_difficulty', 'module', 'difficulty'),
        Index('idx_writing_content_hash', 'content_hash'),
    )


class WritingEssayRecord(Base):
    """Database record for writing essays - module-aware."""
    __tablename__ = 'writing_essays'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), index=True)
    module = Column(String(20), default='ielts', index=True)
    # FK is nullable by default — safe when there's no valid integer test_id.
    test_id = Column(Integer, ForeignKey('writing_tests.id'), index=True, nullable=True)
    task_type = Column(String(10)) # '1' or '2'
    essay = Column(Text)
    prompt = Column(Text)
    band_score = Column(Float, default=0.0)
    feedback = Column(JSON)
    criteria = Column(JSON)
    word_count = Column(Integer, default=0)
    penalty_applied = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    test = relationship("WritingTestRecord", back_populates="essays")

    __table_args__ = (
        Index('idx_essay_module_user', 'module', 'user_id'),
        Index('idx_essay_module_created', 'module', 'created_at'),
    )


# ============================================================
# REPOSITORY
# ============================================================
class WritingRepository:
    """Database operations for Writing module - PURE DATABASE, NO FALLBACKS"""

    def __init__(self, db_url: str = None, module: str = 'ielts'):
        """
        Initialize database connection.

        :param db_url: SQLAlchemy database URL (defaults to WRITING_DATABASE_URL
                       env var or sqlite:///ielts_writing.db)
        :param module: default module for this repository instance
                       (ielts, pte, ukvi)
        """
        self.default_module = module or 'ielts'
        db_url = db_url or os.environ.get(
            'WRITING_DATABASE_URL', 'sqlite:///ielts_writing.db'
        )

        try:
            self.eng = create_engine(
                db_url,
                connect_args={"check_same_thread": False} if 'sqlite' in db_url else {},
                echo=False,
            )
            Base.metadata.create_all(self.eng)
            self.Sess = sessionmaker(bind=self.eng, autocommit=False, autoflush=False)
            logger.info(
                f"[WritingRepository] Connected to database: {db_url} "
                f"(module={self.default_module})"
            )
        except Exception as e:
            logger.error(f"[WritingRepository] Database connection failed: {e}")
            raise RuntimeError(f" Failed to connect to database: {e}")

    def _session(self):
        """Create a new database session."""
        return self.Sess()

    def _to_dict(self, record) -> Optional[Dict]:
        """Convert a SQLAlchemy record to a plain dict."""
        if not record:
            return None
        return {c.name: getattr(record, c.name) for c in record.__table__.columns}

    # ============================================================
    # TEST OPERATIONS
    # ============================================================
    def save_test(self, data: Dict, module: str = None) -> Optional[int]:
        """
        Save a writing test to the database.

        Normalizes:
          · `module` → falls back to repo default
          · `serial_number` → int (or random int fallback)
          · `task1_chart_data` → structured dict/list via _normalize_json
          · `content_hash` → computed from data if not supplied
        """
        s = self._session()
        try:
            module = module or self.default_module
            valid = {c.name for c in WritingTestRecord.__table__.columns if c.name != 'id'}

            data = dict(data) # don't mutate caller's dict
            data['module'] = module

            # ---- serial_number ----
            if 'serial_number' in data:
                coerced = _try_int(data['serial_number'])
                if coerced is None:
                    # Generator may have supplied a hex string; use int fallback.
                    coerced = abs(hash(str(data['serial_number']))) % 10**9
                data['serial_number'] = coerced

            # ---- JSON column ----
            if 'task1_chart_data' in data:
                data['task1_chart_data'] = _normalize_json(data['task1_chart_data'])

            # ---- content_hash ----
            if not data.get('content_hash'):
                data['content_hash'] = _compute_content_hash({
                    'task1_prompt': data.get('task1_prompt'),
                    'task1_chart_data': data.get('task1_chart_data'),
                    'task2_prompt': data.get('task2_prompt'),
                    'module': module,
                    'difficulty': data.get('difficulty'),
                })

            record = WritingTestRecord(**{k: v for k, v in data.items() if k in valid})
            s.add(record)
            s.commit()

            logger.info(
                f"[Repository] Test saved: ID={record.id}, "
                f"Serial={record.serial_number}, Module={module}, "
                f"Hash={record.content_hash[:12]}..."
            )
            return record.id
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Save test failed: {e}")
            return None
        finally:
            s.close()

    def get_test(self, test_id: int, module: str = None) -> Optional[Dict]:
        """Get test by ID, optionally filtered by module."""
        s = self._session()
        try:
            query = s.query(WritingTestRecord).filter_by(id=test_id)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            return self._to_dict(record)
        except Exception as e:
            logger.exception(f"[Repository] Get test failed: {e}")
            return None
        finally:
            s.close()

    def get_test_by_serial(self, serial: int, module: str = None) -> Optional[Dict]:
        """Get test by serial number, optionally filtered by module."""
        s = self._session()
        try:
            coerced = _try_int(serial)
            if coerced is None:
                return None
            query = s.query(WritingTestRecord).filter_by(serial_number=coerced)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            return self._to_dict(record)
        except Exception as e:
            logger.exception(f"[Repository] Get test by serial failed: {e}")
            return None
        finally:
            s.close()

    def find_test_by_content_hash(self, payload: Any, module: str = None,
                                   difficulty: str = None) -> Optional[Dict]:
        """
        Return an existing test row whose `content_hash` matches the payload.

        Faster than an in-Python scan of every candidate row. Falls back
        gracefully when the payload has no hash (returns None) so callers
        can still perform an in-Python comparison.
        """
        s = self._session()
        try:
            target_hash = _compute_content_hash({
                'task1_prompt': payload.get('task1_prompt') if isinstance(payload, dict) else None,
                'task1_chart_data': payload.get('task1_chart_data') if isinstance(payload, dict) else None,
                'task2_prompt': payload.get('task2_prompt') if isinstance(payload, dict) else None,
                'module': (module or self.default_module),
                'difficulty': difficulty or (payload.get('difficulty') if isinstance(payload, dict) else None),
            }) if isinstance(payload, dict) else _compute_content_hash(payload)

            query = s.query(WritingTestRecord).filter_by(content_hash=target_hash)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            return self._to_dict(record)
        except Exception as e:
            logger.exception(f"[Repository] Find test by content_hash failed: {e}")
            return None
        finally:
            s.close()

    def get_random_test(self, difficulty: str = None,
                        module: str = None) -> Optional[Dict]:
        """Get a random test, optionally filtered by difficulty and/or module."""
        s = self._session()
        try:
            from sqlalchemy.sql.expression import func
            query = s.query(WritingTestRecord)
            module = module or self.default_module
            query = query.filter_by(module=module)
            if difficulty:
                query = query.filter_by(difficulty=difficulty)
            record = query.order_by(func.random()).first()

            if record:
                record.usage_count = (record.usage_count or 0) + 1
                s.commit()
                return self._to_dict(record)
            return None
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Get random test failed: {e}")
            return None
        finally:
            s.close()

    def update_test_stats(self, test_id: int, score: float, module: str = None):
        """Update a test's running average and usage count."""
        s = self._session()
        try:
            query = s.query(WritingTestRecord).filter_by(id=test_id)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            if record:
                old_avg = record.avg_score or 0
                old_count = record.usage_count or 0
                new_avg = (old_avg * old_count + (score or 0)) / (old_count + 1)
                record.avg_score = round(new_avg, 2)
                record.usage_count = old_count + 1
                s.commit()
                logger.info(
                    f"[Repository] Updated test stats: "
                    f"ID={test_id}, new_avg={new_avg:.2f}"
                )
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Update stats failed: {e}")
        finally:
            s.close()

    def list_tests(self, limit: int = 100, offset: int = 0,
                   module: str = None) -> List[Dict]:
        """List all tests, paginated, optionally filtered by module."""
        s = self._session()
        try:
            query = s.query(WritingTestRecord)
            module = module or self.default_module
            query = query.filter_by(module=module)
            records = query.order_by(WritingTestRecord.created_at.desc()) \
                .offset(offset).limit(limit).all()
            return [self._to_dict(r) for r in records]
        except Exception as e:
            logger.exception(f"[Repository] List tests failed: {e}")
            return []
        finally:
            s.close()

    # ============================================================
    # ESSAY OPERATIONS
    # ============================================================
    def save_essay(self, data: Dict, module: str = None) -> Optional[int]:
        """
        Save a writing essay to the database.

        Normalizes:
          · `module` → falls back to repo default
          · `test_id` → int or NULL (never "" or "w_xxx")
          · `penalty` → mapped to `penalty_applied`
          · `feedback` → JSON column (always dict/list)
          · `criteria` → JSON column (always dict/list)
          · `word_count` → derived from essay if missing
        """
        s = self._session()
        try:
            module = module or self.default_module
            valid = {c.name for c in WritingEssayRecord.__table__.columns if c.name != 'id'}

            data = dict(data) # don't mutate caller's dict
            data['module'] = module

            # ---- Alias `penalty` → `penalty_applied` ----
            if 'penalty' in data and 'penalty_applied' not in data:
                data['penalty_applied'] = data.pop('penalty')

            # ---- Coerce test_id defensively ----
            if 'test_id' in data:
                data['test_id'] = _try_int(data['test_id'])

            # ---- JSON columns ----
            if 'feedback' in data:
                data['feedback'] = _normalize_json(data['feedback'])
            if 'criteria' in data:
                data['criteria'] = _normalize_json(data['criteria'])

            # ---- word_count sanity ----
            if (not data.get('word_count')) and data.get('essay'):
                data['word_count'] = len((data.get('essay') or '').split())

            # ---- band_score default ----
            if 'band_score' not in data or data.get('band_score') is None:
                data['band_score'] = 0.0

            record = WritingEssayRecord(**{k: v for k, v in data.items() if k in valid})
            s.add(record)
            s.commit()

            logger.info(
                f"[Repository] Essay saved: ID={record.id}, User={record.user_id}, "
                f"test_id={record.test_id}, task={record.task_type}, "
                f"band={record.band_score}, words={record.word_count}, "
                f"penalty={record.penalty_applied}, Module={module}"
            )
            return record.id
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Save essay failed: {e}")
            return None
        finally:
            s.close()

    def get_essay(self, essay_id: int, module: str = None) -> Optional[Dict]:
        """Get essay by ID, optionally filtered by module."""
        s = self._session()
        try:
            query = s.query(WritingEssayRecord).filter_by(id=essay_id)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            return self._to_dict(record)
        except Exception as e:
            logger.exception(f"[Repository] Get essay failed: {e}")
            return None
        finally:
            s.close()

    def get_user_essays(self, user_id: str, limit: int = 20,
                        module: str = None) -> List[Dict]:
        """Get the most recent essays for a user, optionally filtered by module."""
        s = self._session()
        try:
            module = module or self.default_module
            records = s.query(WritingEssayRecord) \
                .filter_by(user_id=user_id, module=module) \
                .order_by(WritingEssayRecord.created_at.desc()) \
                .limit(limit).all()
            return [self._to_dict(r) for r in records]
        except Exception as e:
            logger.exception(f"[Repository] Get user essays failed: {e}")
            return []
        finally:
            s.close()

    def get_user_stats(self, user_id: str, module: str = None) -> Dict:
        """
        Get statistics for a user, optionally filtered by module.

        Fixes:
          · Query now ORDERs BY created_at ASC so `recent_band` is
            deterministic and truly the most recent band.
          · Only counts essays with band_score > 0 toward averages, so
            empty (Band 0) essays don't drag the average down.
        """
        empty_stats = {
            'total_essays': 0,
            'average_band': 0,
            'task1_count': 0,
            'task2_count': 0,
            'best_band': 0,
            'recent_band': 0,
            'total_words': 0,
            'average_penalty': 0,
        }

        s = self._session()
        try:
            module = module or self.default_module
            records = s.query(WritingEssayRecord) \
                .filter_by(user_id=user_id, module=module) \
                .order_by(WritingEssayRecord.created_at.asc()) \
                .all()

            if not records:
                return empty_stats

            bands = [r.band_score for r in records
                     if r.band_score and r.band_score > 0]
            penalties = [r.penalty_applied for r in records
                         if r.penalty_applied is not None]

            return {
                'total_essays': len(records),
                'average_band': round(sum(bands) / len(bands), 1) if bands else 0,
                'best_band': max(bands) if bands else 0,
                # `bands[-1]` is now truly the most recent, thanks to ASC ordering
                'recent_band': bands[-1] if bands else 0,
                'average_penalty': round(sum(penalties) / len(penalties), 1)
                                   if penalties else 0,
                'task1_count': sum(1 for r in records
                                   if r.task_type in ('1', 'task1')),
                'task2_count': sum(1 for r in records
                                   if r.task_type in ('2', 'task2')),
                'total_words': sum(r.word_count or 0 for r in records),
            }
        except Exception as e:
            logger.exception(f"[Repository] Get user stats failed: {e}")
            return empty_stats
        finally:
            s.close()

    def get_essays_by_band(self, user_id: str, min_band: float = 0,
                           max_band: float = 9, module: str = None) -> List[Dict]:
        """Get essays within a band range, optionally filtered by module."""
        s = self._session()
        try:
            module = module or self.default_module
            records = s.query(WritingEssayRecord) \
                .filter_by(user_id=user_id, module=module) \
                .filter(WritingEssayRecord.band_score >= min_band) \
                .filter(WritingEssayRecord.band_score <= max_band) \
                .order_by(WritingEssayRecord.created_at.desc()) \
                .all()
            return [self._to_dict(r) for r in records]
        except Exception as e:
            logger.exception(f"[Repository] Get essays by band failed: {e}")
            return []
        finally:
            s.close()

    def delete_essay(self, essay_id: int, module: str = None) -> bool:
        """Delete an essay by ID, optionally filtered by module."""
        s = self._session()
        try:
            query = s.query(WritingEssayRecord).filter_by(id=essay_id)
            if module:
                query = query.filter_by(module=module)
            record = query.first()
            if record:
                s.delete(record)
                s.commit()
                logger.info(f"[Repository] Essay deleted: ID={essay_id}")
                return True
            return False
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Delete essay failed: {e}")
            return False
        finally:
            s.close()

    def delete_user_essays(self, user_id: str, module: str = None) -> int:
        """Delete all essays for a user, optionally filtered by module."""
        s = self._session()
        try:
            module = module or self.default_module
            count = s.query(WritingEssayRecord) \
                .filter_by(user_id=user_id, module=module).delete()
            s.commit()
            logger.info(
                f"[Repository] Deleted {count} essays for user: {user_id} "
                f"(module={module})"
            )
            return count
        except Exception as e:
            s.rollback()
            logger.exception(f"[Repository] Delete user essays failed: {e}")
            return 0
        finally:
            s.close()

    def get_essay_count(self, user_id: str = None, module: str = None) -> int:
        """Get total essay count, optionally filtered by user and/or module."""
        s = self._session()
        try:
            module = module or self.default_module
            query = s.query(WritingEssayRecord).filter_by(module=module)
            if user_id:
                query = query.filter_by(user_id=user_id)
            return query.count()
        except Exception as e:
            logger.exception(f"[Repository] Get essay count failed: {e}")
            return 0
        finally:
            s.close()

    def get_average_band_by_difficulty(self, difficulty: str,
                                        module: str = None) -> float:
        """Get average band score for tests of a specific difficulty and module."""
        s = self._session()
        try:
            module = module or self.default_module
            results = s.query(WritingEssayRecord).join(
                WritingTestRecord,
                WritingEssayRecord.test_id == WritingTestRecord.id,
            ).filter(
                WritingTestRecord.module == module,
                WritingTestRecord.difficulty == difficulty,
            ).all()

            bands = [r.band_score for r in results
                     if r.band_score and r.band_score > 0]
            return round(sum(bands) / len(bands), 1) if bands else 0
        except Exception as e:
            logger.exception(f"[Repository] Get average band by difficulty failed: {e}")
            return 0
        finally:
            s.close()


# ============================================================
# FACTORY
# ============================================================
def create_writing_repository(db_url: str = None, module: str = 'ielts'):
    """Factory function to create WritingRepository with optional module default."""
    return WritingRepository(db_url, module)


# No singleton instance – create via factory in app context.