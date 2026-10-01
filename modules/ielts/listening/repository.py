"""Database operations for IELTS Listening module – now module‑aware (reusable for PTE/UKVI)

v2.0 — PostgreSQL Compatible:
  • Auto-detects SQLite vs PostgreSQL
  • SQLite-specific PRAGMAs only run on SQLite
  • Type mapping handles PostgreSQL JSONB, TIMESTAMP WITH TIME ZONE
  • Schema migration works on both databases
"""

import json
import random
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, DateTime, JSON,
    Index, func, inspect, text
)
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

Base = declarative_base()


class ListeningTest(Base):
    __tablename__ = 'listening_tests'

    id = Column(Integer, primary_key=True, autoincrement=True)
    serial_number = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String(300))
    topic = Column(String(200))
    topic_category = Column(String(100))
    difficulty = Column(String(50))
    exam_type = Column(String(50), default='ielts')
    accent = Column(String(50), default='british')
    module = Column(String(20), default='ielts', index=True) # ielts, pte, ukvi
    audio_script = Column(Text)
    audio_paths = Column(JSON)
    questions = Column(JSON)
    correct_answers = Column(JSON)
    sections = Column(JSON)
    usage_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('idx_exam_diff', 'exam_type', 'difficulty'),
        Index('idx_serial', 'serial_number'),
        Index('idx_topic', 'topic'),
        Index('idx_accent', 'accent'),
        Index('idx_usage', 'usage_count'),
        Index('idx_module', 'module'),
        Index('idx_module_exam', 'module', 'exam_type'),
    )


class ListeningRepository:
    """Database operations for Listening tests – module‑aware + PostgreSQL compatible."""

    def __init__(self, db_url: Optional[str] = None, module: str = 'ielts'):
        """
        Initialize repository with database URL and default module.
        """
        self.default_module = module or 'ielts'
        if db_url is None:
            db_url = os.environ.get('DATABASE_URL', 'sqlite:///ielts.db')

        self.db_url = db_url
        self.is_sqlite = 'sqlite' in db_url.lower()
        self.is_postgres = 'postgresql' in db_url.lower() or db_url.startswith('postgres://')

        # Build engine with database-specific options
        if self.is_sqlite:
            self.engine = create_engine(
                db_url,
                connect_args={"check_same_thread": False},
                echo=False
            )
        elif self.is_postgres:
            self.engine = create_engine(
                db_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_timeout=30,
                echo=False
            )
        else:
            # Generic fallback
            self.engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False
            )

        # Apply SQLite-only optimizations (WAL, pragmas)
        self._apply_sqlite_pragmas()

        # Ensure schema is up-to-date (add missing columns)
        self._ensure_schema()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        db_kind = 'PostgreSQL' if self.is_postgres else ('SQLite' if self.is_sqlite else 'DB')
        logger.info(f"ListeningRepository initialized with {db_kind} (module={self.default_module})")

    # ═══════════════════════════════════════════════════════════════
    # SQLite-only optimizations — skipped on PostgreSQL
    # ═══════════════════════════════════════════════════════════════
    def _apply_sqlite_pragmas(self):
        """Apply SQLite-specific PRAGMAs only when actually using SQLite."""
        if not self.is_sqlite:
            # PostgreSQL / other DBs don't need (and can't run) PRAGMA
            return
        try:
            with self.engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA synchronous=NORMAL"))
                conn.execute(text("PRAGMA busy_timeout=30000"))
                conn.execute(text("PRAGMA cache_size=10000"))
                conn.execute(text("PRAGMA temp_store=MEMORY"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.commit()
            logger.info(" SQLite WAL mode enabled (ListeningRepository)")
        except Exception as e:
            logger.warning(f"Could not enable SQLite WAL mode: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Schema migration — database-agnostic
    # ═══════════════════════════════════════════════════════════════
    def _ensure_schema(self):
        """
        Add missing columns to the listening_tests table if needed.
        Works on both SQLite and PostgreSQL.
        """
        inspector = inspect(self.engine)
        if not inspector.has_table('listening_tests'):
            logger.info("Table listening_tests does not exist, will be created by create_all")
            return

        existing_columns = [col['name'] for col in inspector.get_columns('listening_tests')]
        model_columns = {c.name: c for c in ListeningTest.__table__.columns}

        added = False
        for col_name, col in model_columns.items():
            if col_name not in existing_columns:
                sql_type = self._column_to_sql_type(col)
                try:
                    with self.engine.connect() as conn:
                        # Both SQLite and PostgreSQL support ALTER TABLE ADD COLUMN
                        conn.execute(text(
                            f"ALTER TABLE listening_tests ADD COLUMN {col_name} {sql_type}"
                        ))
                        conn.commit()
                        logger.info(f"Added column {col_name} ({sql_type}) to listening_tests")
                        added = True
                except Exception as e:
                    logger.warning(f"Could not add column {col_name}: {e}")

        if added:
            logger.info("Schema updated successfully")

    def _column_to_sql_type(self, col) -> str:
        """
        Convert SQLAlchemy column type to database-specific SQL type.
        Uses self.is_postgres / self.is_sqlite to pick the right type.
        """
        col_type_str = str(col.type).upper()

        if 'VARCHAR' in col_type_str:
            match = re.search(r'VARCHAR\((\d+)\)', col_type_str)
            return f"VARCHAR({match.group(1)})" if match else "VARCHAR(255)"
        if 'INTEGER' in col_type_str or 'INT' in col_type_str:
            return "INTEGER"
        if 'FLOAT' in col_type_str or 'REAL' in col_type_str or 'NUMERIC' in col_type_str:
            return "DOUBLE PRECISION" if self.is_postgres else "FLOAT"
        if 'JSON' in col_type_str:
            # PostgreSQL supports native JSON; JSONB would be better but let's keep JSON for compatibility
            return "JSON" if self.is_postgres else "TEXT"
        if 'DATETIME' in col_type_str or 'TIMESTAMP' in col_type_str:
            return "TIMESTAMP WITH TIME ZONE" if self.is_postgres else "TIMESTAMP"
        if 'TEXT' in col_type_str or 'CLOB' in col_type_str:
            return "TEXT"
        # Default fallback
        return "TEXT"

    # ============================================================
    # CREATE
    # ============================================================

    def save(self, data: Dict[str, Any], module: str = None) -> Optional[int]:
        """Save a test to the database – module is added if not present."""
        module = module or self.default_module
        session = self.Session()
        try:
            clean_data = self._sanitize(data)
            clean_data['module'] = module
            record = ListeningTest(**clean_data)
            session.add(record)
            session.commit()
            logger.info(f"Test saved | serial={data.get('serial_number')} | id={record.id} | module={module}")
            return record.id
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error saving test: {e}")
            return None
        except Exception as e:
            session.rollback()
            logger.exception(f"Unexpected error saving test: {e}")
            return None
        finally:
            session.close()

    def save_batch(self, tests: List[Dict[str, Any]], module: str = None) -> List[int]:
        """Save multiple tests in a single transaction – module added."""
        module = module or self.default_module
        session = self.Session()
        saved_ids = []
        try:
            records = []
            for data in tests:
                clean_data = self._sanitize(data)
                clean_data['module'] = module
                record = ListeningTest(**clean_data)
                session.add(record)
                records.append(record)
            session.flush()
            saved_ids = [r.id for r in records]
            session.commit()
            logger.info(f"Batch saved: {len(saved_ids)} tests (module={module})")
            return saved_ids
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Batch save failed: {e}")
            return []
        finally:
            session.close()

    # ============================================================
    # READ (Single)
    # ============================================================

    def get(self, test_id: int, module: str = None) -> Optional[Dict]:
        """Get a test by database ID, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(id=test_id, module=module).first()
            return self._to_dict(record)
        finally:
            session.close()

    def get_by_serial(self, serial_number: int, module: str = None) -> Optional[Dict]:
        """Get a test by serial number, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(serial_number=serial_number, module=module).first()
            return self._to_dict(record)
        finally:
            session.close()

    def get_by_title(self, title: str, module: str = None) -> Optional[Dict]:
        """Get a test by exact title match, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(title=title, module=module).first()
            return self._to_dict(record)
        finally:
            session.close()

    # ============================================================
    # READ (Multiple / Filtered)
    # ============================================================

    def random(
        self,
        difficulty: Optional[str] = None,
        exam_type: str = 'ielts',
        accent: Optional[str] = None,
        topic: Optional[str] = None,
        module: str = None
    ) -> Optional[Dict]:
        """Get a random test with optional filters, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            query = session.query(ListeningTest).filter_by(exam_type=exam_type, module=module)
            if difficulty:
                query = query.filter_by(difficulty=difficulty)
            if accent:
                query = query.filter_by(accent=accent)
            if topic:
                query = query.filter(ListeningTest.topic.ilike(f"%{topic}%"))

            count = query.count()
            if count == 0:
                return None
            offset = random.randint(0, count - 1)
            record = query.offset(offset).first()
            return self._to_dict(record)
        finally:
            session.close()

    def all(
        self,
        exam_type: Optional[str] = None,
        difficulty: Optional[str] = None,
        accent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        module: str = None
    ) -> List[Dict]:
        """Get all tests with pagination and filtering, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            query = session.query(ListeningTest).filter_by(module=module)
            if exam_type:
                query = query.filter_by(exam_type=exam_type)
            if difficulty:
                query = query.filter_by(difficulty=difficulty)
            if accent:
                query = query.filter_by(accent=accent)
            records = query.order_by(
                ListeningTest.serial_number.desc()
            ).offset(offset).limit(limit).all()
            return [self._to_dict(r) for r in records]
        finally:
            session.close()

    def get_by_topic(self, topic: str, limit: int = 10, module: str = None) -> List[Dict]:
        """Get tests by topic (partial match), filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            records = session.query(ListeningTest).filter(
                ListeningTest.topic.ilike(f"%{topic}%"),
                ListeningTest.module == module
            ).limit(limit).all()
            return [self._to_dict(r) for r in records]
        finally:
            session.close()

    def get_by_category(self, category: str, limit: int = 10, module: str = None) -> List[Dict]:
        """Get tests by topic category, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            records = session.query(ListeningTest).filter_by(
                topic_category=category, module=module
            ).limit(limit).all()
            return [self._to_dict(r) for r in records]
        finally:
            session.close()

    def get_popular(self, limit: int = 10, module: str = None) -> List[Dict]:
        """Get most used tests, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            records = session.query(ListeningTest).filter_by(module=module).order_by(
                ListeningTest.usage_count.desc()
            ).limit(limit).all()
            return [self._to_dict(r) for r in records]
        finally:
            session.close()

    def get_high_scoring(self, min_success_rate: float = 0.7, limit: int = 10, module: str = None) -> List[Dict]:
        """Get tests with high success rates, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            records = session.query(ListeningTest).filter(
                ListeningTest.success_rate >= min_success_rate,
                ListeningTest.module == module
            ).order_by(
                ListeningTest.success_rate.desc()
            ).limit(limit).all()
            return [self._to_dict(r) for r in records]
        finally:
            session.close()

    # ============================================================
    # UPDATE
    # ============================================================

    def update_stats(self, test_id: int, score: float, module: str = None) -> bool:
        """Update usage count and success rate for a test, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(id=test_id, module=module).first()
            if not record:
                return False

            old_count = record.usage_count or 0
            new_count = old_count + 1
            record.usage_count = new_count

            if record.success_rate is None:
                record.success_rate = score
            else:
                record.success_rate = ((record.success_rate * old_count) + score) / new_count

            session.commit()
            logger.debug(f"Stats updated | test_id={test_id} | usage={new_count} | avg_score={record.success_rate:.1f}")
            return True
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to update stats for test {test_id}: {e}")
            return False
        finally:
            session.close()

    def increment_usage(self, test_id: int, module: str = None) -> bool:
        """Increment usage count without affecting success rate, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(id=test_id, module=module).first()
            if not record:
                return False
            record.usage_count = (record.usage_count or 0) + 1
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to increment usage for test {test_id}: {e}")
            return False
        finally:
            session.close()

    # ============================================================
    # DELETE
    # ============================================================

    def delete(self, test_id: int, module: str = None) -> bool:
        """Delete a test by ID, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            record = session.query(ListeningTest).filter_by(id=test_id, module=module).first()
            if not record:
                return False
            session.delete(record)
            session.commit()
            logger.info(f"Test deleted | id={test_id}")
            return True
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to delete test {test_id}: {e}")
            return False
        finally:
            session.close()

    def delete_old(self, days: int = 30, module: str = None) -> int:
        """Delete tests older than specified days (with low usage), filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            records = session.query(ListeningTest).filter(
                ListeningTest.created_at < cutoff,
                ListeningTest.usage_count < 2,
                ListeningTest.module == module
            ).all()
            count = len(records)
            for r in records:
                session.delete(r)
            session.commit()
            if count > 0:
                logger.info(f"Deleted {count} old, low-usage tests (module={module})")
            return count
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to delete old tests: {e}")
            return 0
        finally:
            session.close()

    # ============================================================
    # SERIAL NUMBER HELPERS
    # ============================================================

    def get_next_serial(self, module: str = None) -> int:
        """Get the next available serial number for the given module."""
        module = module or self.default_module
        session = self.Session()
        try:
            last = session.query(ListeningTest).filter_by(module=module).order_by(
                ListeningTest.serial_number.desc()
            ).first()
            return (last.serial_number + 1) if last and last.serial_number else 1
        finally:
            session.close()

    def get_max_serial(self, module: str = None) -> int:
        """Get the highest serial number for the given module."""
        module = module or self.default_module
        session = self.Session()
        try:
            last = session.query(ListeningTest).filter_by(module=module).order_by(
                ListeningTest.serial_number.desc()
            ).first()
            return last.serial_number if last else 0
        finally:
            session.close()

    def count(self, exam_type: Optional[str] = None, module: str = None) -> int:
        """Get total test count, filtered by module and optionally exam_type."""
        module = module or self.default_module
        session = self.Session()
        try:
            query = session.query(func.count(ListeningTest.id)).filter_by(module=module)
            if exam_type:
                query = query.filter_by(exam_type=exam_type)
            return query.scalar() or 0
        finally:
            session.close()

    def count_by_difficulty(self, exam_type: str = 'ielts', module: str = None) -> Dict[str, int]:
        """Get test count by difficulty, filtered by module."""
        module = module or self.default_module
        session = self.Session()
        try:
            results = session.query(
                ListeningTest.difficulty,
                func.count(ListeningTest.id)
            ).filter_by(exam_type=exam_type, module=module).group_by(
                ListeningTest.difficulty
            ).all()
            return {row[0]: row[1] for row in results}
        finally:
            session.close()

    # ============================================================
    # HELPERS
    # ============================================================

    def _to_dict(self, record: Optional[ListeningTest]) -> Optional[Dict]:
        if not record:
            return None
        result = {}
        for column in record.__table__.columns:
            value = getattr(record, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[column.name] = value
        return result

    def _sanitize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        clean = {}
        datetime_fields = {"generated_at", "created_at"}
        column_names = {c.name for c in ListeningTest.__table__.columns}

        for key, value in data.items():
            if key not in column_names:
                continue

            if key in datetime_fields and value is not None:
                if isinstance(value, str):
                    try:
                        clean[key] = datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        )
                    except ValueError:
                        logger.warning(f"Invalid datetime format for {key}: {value}, using now")
                        clean[key] = datetime.now(timezone.utc)
                elif isinstance(value, datetime):
                    clean[key] = value
                else:
                    clean[key] = datetime.now(timezone.utc)
            elif key == 'questions' and isinstance(value, list):
                clean[key] = value
            elif key == 'correct_answers' and isinstance(value, dict):
                clean[key] = value
            elif key == 'sections' and isinstance(value, list):
                clean[key] = value
            elif key == 'audio_paths' and isinstance(value, dict):
                clean[key] = value
            else:
                clean[key] = value

        return clean

    def close(self) -> None:
        try:
            self.engine.dispose()
            logger.info("Database connection closed")
        except Exception as e:
            logger.warning(f"Error closing database: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============================================================
# FACTORY FUNCTION (RECOMMENDED)
# ============================================================

def create_listening_repository(db_url: Optional[str] = None, module: str = 'ielts') -> ListeningRepository:
    """Factory function to create ListeningRepository with module awareness."""
    return ListeningRepository(db_url, module)


# ============================================================
# SINGLETON INSTANCE (backward compatibility)
# ============================================================

listening_repo = ListeningRepository()