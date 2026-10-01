"""Database operations for IELTS Speaking module - Production-ready with SQLAlchemy
Now module‑aware – stores data for different modules (ielts, pte, ukvi) separately.

FIX:
  (1) save_* methods now call session.flush() before returning IDs — was returning None
  (2) Removed column-level `unique=True` from UserSpeakingProfile.user_id
      (only the composite (user_id, module) should be unique)
  (3) _sanitize() is now recursive and handles bool, nested dicts/lists, numpy types
  (4) update_profile() guards against None band
  (5) get_user_stats() returns has_data flag
  (6) get_or_create_profile() flushes so created_at is populated
  (7) save_pronunciation_batch() counts only successful inserts
  (8) update_template_stats() flushes before returning
  (9) _session_scope() logs the rollback reason
  (10) __init__ disposes engine on create_all failure
"""

import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, DateTime, JSON,
    Index, ForeignKey, func,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

Base = declarative_base()


# ============================================================
# Optional numpy import (guarded)
# ============================================================

try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    _np = None


# ============================================================
# DATABASE MODELS
# ============================================================

class SpeakingSession(Base):
    __tablename__ = 'speaking_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), unique=True, index=True, nullable=False)
    user_id = Column(String(100), default="anonymous", index=True)
    module = Column(String(20), default='ielts', index=True)

    topic = Column(String(200))
    part = Column(Integer, default=1)
    difficulty = Column(String(50))

    overall_band = Column(Float)
    fluency_coherence = Column(Float)
    lexical_resource = Column(Float)
    grammar_accuracy = Column(Float)
    pronunciation = Column(Float)

    pronunciation_details = Column(JSON)
    coherence_details = Column(JSON)
    grammar_details = Column(JSON)
    emotion_data = Column(JSON)

    response_text = Column(Text)
    reference_text = Column(Text)
    word_count = Column(Integer)
    duration_seconds = Column(Float)

    audio_path = Column(String(500))
    audio_duration = Column(Float)

    feedback = Column(Text)
    sentence_feedback = Column(JSON)
    improvement_tips = Column(JSON)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Integer, default=0)

    pronunciation_records = relationship(
        "PronunciationRecord",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index('idx_user_created', 'user_id', 'created_at'),
        Index('idx_session_user', 'session_id', 'user_id'),
        Index('idx_user_deleted', 'user_id', 'is_deleted'),
        Index('idx_module', 'module'),
        Index('idx_user_module', 'user_id', 'module'),
        Index('idx_user_module_created', 'user_id', 'module', 'created_at'),
    )


class SpeakingTestTemplate(Base):
    __tablename__ = 'speaking_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    serial_number = Column(Integer, unique=True, index=True)
    module = Column(String(20), default='ielts', index=True)

    topic = Column(String(200))
    difficulty = Column(String(50))

    part1_intro = Column(Text)
    part1_questions = Column(JSON)

    part2_intro = Column(Text)
    part2_topic = Column(String(500))
    part2_prompts = Column(JSON)

    part3_intro = Column(Text)
    part3_questions = Column(JSON)

    usage_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_band_score = Column(Float)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index('idx_template_module', 'module'),
        Index('idx_template_module_difficulty', 'module', 'difficulty'),
    )


class PronunciationRecord(Base):
    __tablename__ = 'pronunciation_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(50),
        ForeignKey('speaking_sessions.session_id', ondelete='CASCADE'),
        index=True,
    )

    word = Column(String(200))
    phoneme = Column(String(50))
    ipa = Column(String(50))

    confidence = Column(Float)
    is_correct = Column(Integer, default=1)
    stress_correct = Column(Integer, default=1)

    expected = Column(String(200))
    actual = Column(String(200))

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("SpeakingSession", back_populates="pronunciation_records")


class UserSpeakingProfile(Base):
    __tablename__ = 'speaking_profiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # FIX: user_id is NOT globally unique — one profile per (user_id, module)
    user_id = Column(String(100), index=True, nullable=False)
    module = Column(String(20), default='ielts', index=True, nullable=False)

    total_sessions = Column(Integer, default=0)
    average_band = Column(Float)
    highest_band = Column(Float)
    lowest_band = Column(Float)

    strong_topics = Column(JSON)
    weak_topics = Column(JSON)
    strong_skills = Column(JSON)
    weak_skills = Column(JSON)

    common_phoneme_errors = Column(JSON)
    accent_detected = Column(String(100))
    accent_confidence = Column(Float)

    band_history = Column(JSON)
    improvement_rate = Column(Float)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # FIX: only composite unique — allows same user across modules
        Index('idx_profile_user_module', 'user_id', 'module', unique=True),
    )


# ============================================================
# REPOSITORY CLASS
# ============================================================

class SpeakingRepository:
    """Database operations for speaking module – module-aware."""

    def __init__(self, db_url: str = "sqlite:///ielts_speaking.db", module: str = 'ielts'):
        self.default_module = module or 'ielts'

        try:
            if 'sqlite' in db_url:
                self.engine = create_engine(
                    db_url,
                    connect_args={"check_same_thread": False},
                    poolclass=StaticPool,
                    echo=False,
                )
            else:
                self.engine = create_engine(
                    db_url,
                    pool_size=5,
                    max_overflow=10,
                    pool_pre_ping=True,
                    echo=False,
                )
            Base.metadata.create_all(self.engine)
        except Exception as e:
            logger.exception(f"Failed to initialize database: {e}")
            if hasattr(self, 'engine'):
                try:
                    self.engine.dispose()
                except Exception:
                    pass
            raise

        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        logger.info(f"SpeakingRepository initialized (module={self.default_module})")

    # ============================================================
    # SESSION CONTEXT MANAGER
    # ============================================================

    def _session_scope(self):
        """Provide a session. Commits on success, rolls back on error."""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception as e:
            logger.warning(f"Session rollback due to: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    # ============================================================
    # HELPERS
    # ============================================================

    def _sanitize(self, data: Any) -> Any:
        """
        Recursively convert numpy types to native Python types.
         FIX: Handles bool, nested dicts/lists, and numpy scalars.
        """
        if data is None:
            return None

        if _NUMPY_AVAILABLE and _np is not None:
            if isinstance(data, (_np.floating, _np.float32, _np.float64)):
                return float(data)
            if isinstance(data, (_np.integer, _np.int32, _np.int64)):
                return int(data)
            if isinstance(data, _np.bool_):
                return bool(data)
            if isinstance(data, _np.ndarray):
                return [self._sanitize(x) for x in data.tolist()]

        if isinstance(data, bool):
            return data
        if isinstance(data, dict):
            return {k: self._sanitize(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return [self._sanitize(x) for x in data]
        if isinstance(data, datetime):
            return data
        if isinstance(data, (int, float, str)):
            return data

        # Fallback: try to make it JSON-serializable
        try:
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            return str(data)

    def _sanitize_dict(self, data: Dict) -> Dict:
        """Sanitize a dict of fields (each value recursively cleaned)."""
        return {k: self._sanitize(v) for k, v in (data or {}).items()}

    def _to_dict(self, record) -> Dict:
        """Convert a SQLAlchemy record to a plain dict."""
        if record is None:
            return {}
        result = {}
        for column in record.__table__.columns:
            value = getattr(record, column.name, None)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[column.name] = value
        return result

    # ============================================================
    # SESSION OPERATIONS
    # ============================================================

    def save_session(self, data: Dict, module: str = None) -> Optional[int]:
        """
        Save a speaking session. Returns the new row id, or None on error.

         FIX: flush() is called before returning record.id so the ID is populated.
        """
        module = module or self.default_module
        try:
            session = self.Session()
            try:
                clean_data = self._sanitize_dict(data)
                clean_data['module'] = module
                record = SpeakingSession(**clean_data)
                session.add(record)
                session.flush() # assign id + created_at
                new_id = record.id
                session.commit()
                return new_id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.exception(f"Failed to save session: {e}")
            return None

    def get_session(self, session_id: str, module: str = None) -> Optional[Dict]:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(SpeakingSession).filter_by(
                    session_id=session_id, module=module, is_deleted=0
                ).first()
                return self._to_dict(record) if record else None
        except Exception as e:
            logger.exception(f"Failed to get session: {e}")
            return None

    def get_user_sessions(
        self, user_id: str, limit: int = 20, offset: int = 0, module: str = None
    ) -> List[Dict]:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                records = (
                    session.query(SpeakingSession)
                    .filter_by(user_id=user_id, module=module, is_deleted=0)
                    .order_by(SpeakingSession.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                    .all()
                )
                return [self._to_dict(r) for r in records]
        except Exception as e:
            logger.exception(f"Failed to get user sessions: {e}")
            return []

    def update_session(self, session_id: str, data: Dict, module: str = None) -> bool:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(SpeakingSession).filter_by(
                    session_id=session_id, module=module
                ).first()
                if not record:
                    return False
                for key, value in self._sanitize_dict(data).items():
                    if hasattr(record, key):
                        setattr(record, key, value)
                record.updated_at = datetime.now(timezone.utc)
                return True
        except Exception as e:
            logger.exception(f"Failed to update session: {e}")
            return False

    def delete_session(
        self, session_id: str, hard_delete: bool = False, module: str = None
    ) -> bool:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(SpeakingSession).filter_by(
                    session_id=session_id, module=module
                ).first()
                if not record:
                    return False
                if hard_delete:
                    session.delete(record)
                else:
                    record.is_deleted = 1
                    record.updated_at = datetime.now(timezone.utc)
                return True
        except Exception as e:
            logger.exception(f"Failed to delete session: {e}")
            return False

    # ============================================================
    # TEST TEMPLATE OPERATIONS
    # ============================================================

    def save_template(self, data: Dict, module: str = None) -> Optional[int]:
        """Save a speaking test template. flush() before returning id."""
        module = module or self.default_module
        try:
            session = self.Session()
            try:
                clean_data = self._sanitize_dict(data)
                clean_data['module'] = module
                record = SpeakingTestTemplate(**clean_data)
                session.add(record)
                session.flush()
                new_id = record.id
                session.commit()
                return new_id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.exception(f"Failed to save template: {e}")
            return None

    def get_template(self, serial_number: int, module: str = None) -> Optional[Dict]:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(SpeakingTestTemplate).filter_by(
                    serial_number=serial_number, module=module
                ).first()
                return self._to_dict(record) if record else None
        except Exception as e:
            logger.exception(f"Failed to get template: {e}")
            return None

    def get_random_template(self, difficulty: str = None, module: str = None) -> Optional[Dict]:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                query = session.query(SpeakingTestTemplate).filter_by(module=module)
                if difficulty:
                    query = query.filter_by(difficulty=difficulty)
                record = query.order_by(func.random()).first()
                return self._to_dict(record) if record else None
        except Exception as e:
            logger.exception(f"Failed to get random template: {e}")
            return None

    def update_template_stats(
        self, template_id: int, band_score: float, module: str = None
    ) -> bool:
        """Update template usage stats. flush() so changes are visible immediately."""
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(SpeakingTestTemplate).filter_by(
                    id=template_id, module=module
                ).first()
                if not record:
                    return False
                record.usage_count = (record.usage_count or 0) + 1
                if record.avg_band_score is None:
                    record.avg_band_score = float(band_score)
                else:
                    n = record.usage_count
                    record.avg_band_score = (
                        record.avg_band_score * (n - 1) + float(band_score)
                    ) / n
                record.success_rate = (
                    (record.success_rate or 0) * 0.9
                    + (1 if band_score >= 6 else 0) * 0.1
                )
                session.flush()
                return True
        except Exception as e:
            logger.exception(f"Failed to update template stats: {e}")
            return False

    # ============================================================
    # PRONUNCIATION RECORDS
    # ============================================================

    def save_pronunciation(self, data: Dict) -> Optional[int]:
        """Save one pronunciation record. flush() before returning id."""
        try:
            session = self.Session()
            try:
                record = PronunciationRecord(**self._sanitize_dict(data))
                session.add(record)
                session.flush()
                new_id = record.id
                session.commit()
                return new_id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.exception(f"Failed to save pronunciation: {e}")
            return None

    def save_pronunciation_batch(self, records: List[Dict]) -> int:
        """
        Save multiple pronunciation records. Returns count of SUCCESSFUL inserts.

         FIX: Per-item error handling + flush per item.
        """
        if not records:
            return 0

        count = 0
        session = self.Session()
        try:
            for data in records:
                try:
                    rec = PronunciationRecord(**self._sanitize_dict(data))
                    session.add(rec)
                    session.flush()
                    count += 1
                except Exception as e:
                    logger.warning(f"Skipping bad pronunciation record: {e}")
                    session.rollback()
                    # Reopen the transaction for the next item
                    continue
            session.commit()
        except Exception as e:
            logger.exception(f"Failed to save batch: {e}")
            session.rollback()
        finally:
            session.close()
        return count

    def get_session_pronunciation(self, session_id: str) -> List[Dict]:
        try:
            with self._session_scope() as session:
                records = session.query(PronunciationRecord).filter_by(
                    session_id=session_id
                ).all()
                return [self._to_dict(r) for r in records]
        except Exception as e:
            logger.exception(f"Failed to get pronunciation: {e}")
            return []

    # ============================================================
    # USER PROFILE OPERATIONS
    # ============================================================

    def get_or_create_profile(self, user_id: str, module: str = None) -> Dict:
        """
        Get or create user speaking profile.

         FIX: flush() after creating, so created_at is populated.
        """
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(UserSpeakingProfile).filter_by(
                    user_id=user_id, module=module
                ).first()
                if not record:
                    record = UserSpeakingProfile(
                        user_id=user_id,
                        module=module,
                        total_sessions=0,
                        strong_topics=[],
                        weak_topics=[],
                        strong_skills=[],
                        weak_skills=[],
                        common_phoneme_errors={},
                        band_history=[],
                    )
                    session.add(record)
                    session.flush() # populate id and created_at
                return self._to_dict(record)
        except Exception as e:
            logger.exception(f"Failed to get/create profile: {e}")
            return {}

    def update_profile(
        self, user_id: str, session_result: Dict, module: str = None
    ) -> bool:
        """
        Update user profile with new session results.
         FIX: Guards against None band.
        """
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                record = session.query(UserSpeakingProfile).filter_by(
                    user_id=user_id, module=module
                ).first()
                if not record:
                    return False

                # FIX: safe float extraction
                raw_band = session_result.get('overall_band', 0)
                try:
                    band = float(raw_band) if raw_band is not None else 0.0
                except (TypeError, ValueError):
                    band = 0.0

                old_total = record.total_sessions or 0
                new_total = old_total + 1
                record.total_sessions = new_total

                old_avg = record.average_band or 0.0
                record.average_band = (old_avg * old_total + band) / new_total

                record.highest_band = max(record.highest_band or 0.0, band)
                if old_total > 0:
                    record.lowest_band = min(record.lowest_band or 9.0, band)
                else:
                    record.lowest_band = band

                history = list(record.band_history or [])
                history.append({
                    'date': datetime.now(timezone.utc).isoformat(),
                    'band': band,
                })
                record.band_history = history[-50:]

                session.flush()
                return True
        except Exception as e:
            logger.exception(f"Failed to update profile: {e}")
            return False

    # ============================================================
    # STATISTICS
    # ============================================================

    def get_user_stats(self, user_id: str, module: str = None) -> Dict:
        """
        Get user speaking statistics.

         FIX: `has_data` flag distinguishes "no tests" from "all bands 0".
        """
        module = module or self.default_module
        empty = {
            'total_sessions': 0,
            'average_band': 0.0,
            'highest_band': 0.0,
            'lowest_band': 0.0,
            'topics_practiced': [],
            'has_data': False,
        }
        try:
            with self._session_scope() as session:
                total = session.query(SpeakingSession).filter_by(
                    user_id=user_id, module=module, is_deleted=0
                ).count()

                if total == 0:
                    return empty

                band_rows = session.query(SpeakingSession.overall_band).filter_by(
                    user_id=user_id, module=module, is_deleted=0
                ).all()

                bands = [
                    float(b[0]) for b in band_rows
                    if b[0] is not None and isinstance(b[0], (int, float))
                ]

                topics = session.query(SpeakingSession.topic).filter_by(
                    user_id=user_id, module=module, is_deleted=0
                ).distinct().all()

                return {
                    'total_sessions': total,
                    'average_band': round(sum(bands) / len(bands), 1) if bands else 0.0,
                    'highest_band': max(bands) if bands else 0.0,
                    'lowest_band': min(bands) if bands else 0.0,
                    'topics_practiced': [t[0] for t in topics if t[0]],
                    'has_data': bool(bands),
                }
        except Exception as e:
            logger.exception(f"Failed to get user stats: {e}")
            return empty

    def get_topic_performance(self, user_id: str, module: str = None) -> Dict[str, float]:
        module = module or self.default_module
        try:
            with self._session_scope() as session:
                results = session.query(
                    SpeakingSession.topic,
                    SpeakingSession.overall_band,
                ).filter_by(
                    user_id=user_id, module=module, is_deleted=0
                ).all()

                topic_scores: Dict[str, List[float]] = {}
                for topic, band in results:
                    if not topic or band is None:
                        continue
                    try:
                        b = float(band)
                    except (TypeError, ValueError):
                        continue
                    topic_scores.setdefault(topic, []).append(b)

                return {
                    topic: round(sum(scores) / len(scores), 1)
                    for topic, scores in topic_scores.items()
                    if scores
                }
        except Exception as e:
            logger.exception(f"Failed to get topic performance: {e}")
            return {}

    def cleanup(self):
        """Dispose the engine and free connections."""
        try:
            self.engine.dispose()
        except Exception as e:
            logger.warning(f"Engine dispose failed: {e}")


# ============================================================
# FACTORY FUNCTION
# ============================================================

def create_speaking_repository(
    db_url: str = "sqlite:///ielts_speaking.db",
    module: str = 'ielts',
) -> SpeakingRepository:
    """Factory function to create SpeakingRepository with module awareness."""
    return SpeakingRepository(db_url, module)


__all__ = [
    'Base',
    'SpeakingSession',
    'SpeakingTestTemplate',
    'PronunciationRecord',
    'UserSpeakingProfile',
    'SpeakingRepository',
    'create_speaking_repository',
]