"""Student progress tracking across multiple test sessions - Thread-safe, production-ready

v3.0 — Multi-Database Compatible (SQLite + PostgreSQL):
  (1) Uses SQLAlchemy engine (works with both SQLite and PostgreSQL)
  (2) PRAGMAs only applied on SQLite — skipped on PostgreSQL (no syntax errors)
  (3) Auto-detects DB type from engine URL
  (4) Foreign keys enforced (SQLite PRAGMA / PostgreSQL native FK)
  (5) All DB access via context-managed transactions
  (6) Safe handling of recordings / scores when data is missing or None
  (7) Skills with a real 0 score are preserved
  (8) weakest_skill only considers skills that actually have data
  (9) improvement_rate requires >= 5 samples
  (10) register_student() reads the profile inside the same lock (no race)
  (11) get_sessions() decodes JSON columns to Python lists
  (12) export_report() handles a bare filename
  (13) overall band averaged over skills that have data
  (14) All numeric coercions guarded with float()/int() try blocks

Backward compatible:
  • create_progress_tracker(db_path="data/progress.db") → uses SQLite
  • create_progress_tracker(db_url="postgresql+psycopg2://...") → uses PostgreSQL
  • create_progress_tracker(engine=<existing Engine>) → reuses an existing engine
"""

import os
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================

MIN_IMPROVEMENT_SAMPLES = 5 # need at least this many bands to compute improvement
IELTS_TOPIC_POOL = [
    "hometown", "home", "family", "work", "study", "travel",
    "food", "technology", "hobbies", "music", "sports",
    "books", "movies", "shopping", "weather", "health",
    "animals", "festivals", "transport", "environment", "education",
]
ALL_SKILLS = ("Grammar", "Coherence", "Pronunciation")


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class TestSession:
    """Single test session data"""
    session_id: str
    timestamp: str
    difficulty: str
    topic: str
    overall_band: float = 0.0
    grammar_score: float = 0.0
    coherence_score: float = 0.0
    pronunciation_score: float = 0.0
    fluency_score: float = 0.0
    vocabulary_score: float = 0.0
    total_questions: int = 0
    total_duration: float = 0.0
    word_count: int = 0
    confidence_level: str = "medium"
    topics_covered: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class StudentProfile:
    """Student profile with aggregated stats"""
    student_id: str
    name: str = ""
    email: str = ""
    total_tests: int = 0
    average_band: float = 0.0
    best_band: float = 0.0
    recent_band: float = 0.0
    grammar_avg: float = 0.0
    coherence_avg: float = 0.0
    pronunciation_avg: float = 0.0
    total_practice_time: float = 0.0
    total_words_spoken: int = 0
    strongest_skill: str = ""
    weakest_skill: str = ""
    improvement_rate: float = 0.0
    common_topics: List[str] = field(default_factory=list)
    recommended_topics: List[str] = field(default_factory=list)
    level: str = "intermediate"
    created_at: str = ""
    last_active: str = ""


# ============================================================
# HELPERS
# ============================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, returning default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_json_loads(value: Any, default: Any) -> Any:
    """Parse a JSON string; return default on failure."""
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _safe_recordings_list(recordings: Any) -> List[Dict]:
    """Return a list of dicts, filtering out anything that isn't a dict."""
    if not isinstance(recordings, list):
        return []
    return [r for r in recordings if isinstance(r, dict)]


# ============================================================
# PROGRESS TRACKER
# ============================================================

class ProgressTracker:
    """
    Thread-safe progress tracker using SQLAlchemy (SQLite or PostgreSQL).

    All public methods acquire a single re-entrant lock so read-modify-write
    sequences are atomic. Connections are per-call and always closed via
    a context manager.

    Usage:
        # SQLite (default — same as before)
        tracker = ProgressTracker(db_path="data/progress.db")

        # PostgreSQL
        tracker = ProgressTracker(db_url="postgresql+psycopg2://user:pass@localhost:5433/db")

        # Reuse an existing SQLAlchemy engine
        tracker = ProgressTracker(engine=some_engine)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        db_url: Optional[str] = None,
        engine: Optional[Engine] = None,
    ):
        """
        Initialize the tracker.

        Priority:
          1. If `engine` is given, use it directly.
          2. Else if `db_url` is given, create an engine from the URL.
          3. Else use `db_path` (defaults to 'data/progress.db') as SQLite.
        """
        self._lock = threading.RLock()
        self.is_sqlite = False

        if engine is not None:
            self.engine = engine
            self.db_url = str(engine.url)
        elif db_url is not None:
            self.db_url = db_url
            self.engine = self._build_engine(db_url)
        else:
            db_path = db_path or "data/progress.db"
            # Ensure parent directory exists (SQLite only)
            dirpath = os.path.dirname(db_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            self.db_url = f"sqlite:///{db_path}"
            self.engine = self._build_engine(self.db_url)

        self.is_sqlite = self.db_url.startswith('sqlite')

        # Apply SQLite-only PRAGMAs (safe to call on PostgreSQL — it will skip)
        self._apply_pragmas()

        # Create schema
        self._init_db()

        db_kind = self.db_url.split(':')[0]
        logger.info(f" ProgressTracker initialized with {db_kind}")

    # ============================================================
    # ENGINE / PRAGMA / SCHEMA
    # ============================================================

    def _build_engine(self, url: str) -> Engine:
        """Build an engine with sensible defaults for the given URL."""
        if url.startswith('sqlite'):
            return create_engine(
                url,
                connect_args={"check_same_thread": False},
                echo=False,
                future=True,
            )
        # PostgreSQL / other
        return create_engine(
            url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
            future=True,
        )

    def _apply_pragmas(self) -> None:
        """
        Apply SQLite-specific PRAGMAs only when actually using SQLite.
        On PostgreSQL this silently does nothing.
        """
        if not self.is_sqlite:
            logger.info(
                f" Progress tracker: PRAGMA skipped "
                f"(using {self.db_url.split(':')[0]})"
            )
            return
        try:
            with self.engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys = ON"))
                conn.execute(text("PRAGMA journal_mode = WAL"))
                conn.execute(text("PRAGMA busy_timeout = 30000"))
                conn.commit()
            logger.info(" Progress tracker: SQLite WAL mode + FK enabled")
        except Exception as e:
            logger.warning(f"Could not apply SQLite PRAGMAs: {e}")

    @contextmanager
    def _connect(self):
        """
        Context-managed SQLAlchemy connection.

        Commits on success, rolls back on error, always closes.
        """
        conn = self.engine.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _init_db(self) -> None:
        """Create tables and indexes (works on both SQLite and PostgreSQL)."""
        with self._lock, self._connect() as conn:
            # -------- students --------
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS students (
                    student_id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(200) DEFAULT '',
                    email VARCHAR(200) DEFAULT '',
                    level VARCHAR(50) DEFAULT 'intermediate',
                    created_at VARCHAR(50) DEFAULT '',
                    last_active VARCHAR(50) DEFAULT ''
                )
            '''))

            # -------- sessions --------
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id VARCHAR(64) PRIMARY KEY,
                    student_id VARCHAR(64),
                    timestamp VARCHAR(50),
                    difficulty VARCHAR(50),
                    topic VARCHAR(200),
                    overall_band FLOAT DEFAULT 0,
                    grammar_score FLOAT DEFAULT 0,
                    coherence_score FLOAT DEFAULT 0,
                    pronunciation_score FLOAT DEFAULT 0,
                    fluency_score FLOAT DEFAULT 0,
                    vocabulary_score FLOAT DEFAULT 0,
                    total_questions INTEGER DEFAULT 0,
                    total_duration FLOAT DEFAULT 0,
                    word_count INTEGER DEFAULT 0,
                    confidence_level VARCHAR(50) DEFAULT 'medium',
                    topics_covered TEXT DEFAULT '[]',
                    weaknesses TEXT DEFAULT '[]',
                    strengths TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    FOREIGN KEY (student_id)
                        REFERENCES students(student_id)
                        ON DELETE CASCADE
                )
            '''))

            # -------- skill_progress --------
            # id: SQLite uses AUTOINCREMENT; PostgreSQL uses SERIAL
            if self.is_sqlite:
                id_col = "INTEGER PRIMARY KEY AUTOINCREMENT"
            else:
                id_col = "SERIAL PRIMARY KEY"

            conn.execute(text(f'''
                CREATE TABLE IF NOT EXISTS skill_progress (
                    id {id_col},
                    student_id VARCHAR(64),
                    session_id VARCHAR(64),
                    skill_name VARCHAR(50),
                    score FLOAT,
                    timestamp VARCHAR(50),
                    FOREIGN KEY (student_id)
                        REFERENCES students(student_id)
                        ON DELETE CASCADE
                )
            '''))

            # -------- indexes --------
            try:
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS idx_sessions_student_id '
                    'ON sessions(student_id)'
                ))
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS idx_sessions_timestamp '
                    'ON sessions(timestamp)'
                ))
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS idx_skill_progress_student_id '
                    'ON skill_progress(student_id)'
                ))
            except Exception as e:
                logger.debug(f"Index creation warning (safe to ignore): {e}")

    # ============================================================
    # STUDENT REGISTRATION
    # ============================================================

    def register_student(
        self,
        student_id: str,
        name: str = "",
        email: str = "",
    ) -> StudentProfile:
        """
        Register a new student or update an existing one.
        Reads the profile inside the same lock (no race).
        """
        if not student_id:
            raise ValueError("student_id is required")

        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            with self._connect() as conn:
                # Check existence
                row = conn.execute(
                    text('SELECT student_id FROM students WHERE student_id = :sid'),
                    {'sid': student_id}
                ).first()

                if row is None:
                    conn.execute(
                        text('INSERT INTO students '
                             '(student_id, name, email, created_at, last_active) '
                             'VALUES (:sid, :name, :email, :created, :active)'),
                        {
                            'sid': student_id,
                            'name': name,
                            'email': email,
                            'created': now,
                            'active': now,
                        }
                    )
                else:
                    conn.execute(
                        text('UPDATE students SET name = :name, email = :email, '
                             'last_active = :active WHERE student_id = :sid'),
                        {
                            'sid': student_id,
                            'name': name,
                            'email': email,
                            'active': now,
                        }
                    )

            return self._get_profile_locked(student_id)

    # ============================================================
    # SESSIONS
    # ============================================================

    def save_session(self, student_id: str, session_data: Dict) -> str:
        """
        Save a test session and return the generated session_id.
        Safe handling of missing/None fields.
        """
        if not student_id:
            raise ValueError("student_id is required")
        if not isinstance(session_data, dict):
            session_data = {}

        session_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        # ---- Extract inputs safely ----
        scores = session_data.get('scores') or {}
        if not isinstance(scores, dict):
            scores = {}
        recordings = _safe_recordings_list(session_data.get('recordings'))
        test = session_data.get('test') or {}
        if not isinstance(test, dict):
            test = {}

        # ---- Aggregate words / duration safely ----
        total_words = 0
        total_duration = 0.0
        for r in recordings:
            transcript = r.get('transcript') or ""
            if isinstance(transcript, str):
                total_words += len(transcript.split())
            total_duration += _safe_float(r.get('duration'), 0.0)

        topic = test.get('topic') or session_data.get('topic') or 'unknown'
        difficulty = (
            session_data.get('difficulty')
            or test.get('difficulty')
            or 'medium'
        )

        # ---- Skill scores: keep genuine 0s, skip missing ----
        raw_skills = {
            'Grammar': scores.get('grammar'),
            'Coherence': scores.get('coherence'),
            'Pronunciation': scores.get('pronunciation'),
        }
        skills: Dict[str, float] = {}
        for k, v in raw_skills.items():
            if v is None:
                continue
            try:
                skills[k] = float(v)
            except (TypeError, ValueError):
                continue

        if skills:
            strongest = max(skills, key=skills.get)
            weakest = min(skills, key=skills.get)
            overall = round(sum(skills.values()) / len(skills), 1)
        else:
            strongest = weakest = "unknown"
            overall = 0.0

        topics_covered = [topic] if topic and topic != 'unknown' else []

        with self._lock, self._connect() as conn:
            # Ensure student row exists (protects against FK failure)
            row = conn.execute(
                text('SELECT student_id FROM students WHERE student_id = :sid'),
                {'sid': student_id}
            ).first()

            if row is None:
                conn.execute(
                    text('INSERT INTO students '
                         '(student_id, name, email, created_at, last_active) '
                         'VALUES (:sid, :name, :email, :created, :active)'),
                    {
                        'sid': student_id,
                        'name': '',
                        'email': '',
                        'created': now,
                        'active': now,
                    }
                )

            # Insert session
            conn.execute(text('''
                INSERT INTO sessions (
                    session_id, student_id, timestamp, difficulty, topic,
                    overall_band, grammar_score, coherence_score, pronunciation_score,
                    total_questions, total_duration, word_count,
                    topics_covered, strengths, weaknesses
                ) VALUES (
                    :sid, :student_id, :ts, :diff, :topic,
                    :overall, :grammar, :coherence, :pron,
                    :tq, :tdur, :wc,
                    :topics, :strengths, :weaknesses
                )
            '''), {
                'sid': session_id,
                'student_id': student_id,
                'ts': now,
                'diff': difficulty,
                'topic': topic,
                'overall': overall,
                'grammar': skills.get('Grammar', 0.0),
                'coherence': skills.get('Coherence', 0.0),
                'pron': skills.get('Pronunciation', 0.0),
                'tq': _safe_int(test.get('total_questions'), 0),
                'tdur': total_duration,
                'wc': total_words,
                'topics': json.dumps(topics_covered),
                'strengths': json.dumps([strongest] if strongest != "unknown" else []),
                'weaknesses': json.dumps([weakest] if weakest != "unknown" else []),
            })

            # Insert per-skill progress
            for skill_name, score in skills.items():
                conn.execute(text('''
                    INSERT INTO skill_progress
                        (student_id, session_id, skill_name, score, timestamp)
                    VALUES (:sid, :session, :skill, :score, :ts)
                '''), {
                    'sid': student_id,
                    'session': session_id,
                    'skill': skill_name,
                    'score': score,
                    'ts': now,
                })

            # Update last_active
            conn.execute(
                text('UPDATE students SET last_active = :active WHERE student_id = :sid'),
                {'active': now, 'sid': student_id}
            )

        return session_id

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Fetch a single session by ID."""
        if not session_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                text('SELECT * FROM sessions WHERE session_id = :sid'),
                {'sid': session_id}
            ).first()
            if not row:
                return None
            return self._row_to_session_dict(row)

    def delete_session(self, session_id: str) -> bool:
        """Delete a single session."""
        if not session_id:
            return False
        with self._lock, self._connect() as conn:
            result = conn.execute(
                text('DELETE FROM sessions WHERE session_id = :sid'),
                {'sid': session_id}
            )
            return (result.rowcount or 0) > 0

    def delete_student(self, student_id: str) -> bool:
        """Delete a student and all associated sessions/skills (via CASCADE)."""
        if not student_id:
            return False
        with self._lock, self._connect() as conn:
            result = conn.execute(
                text('DELETE FROM students WHERE student_id = :sid'),
                {'sid': student_id}
            )
            return (result.rowcount or 0) > 0

    # ============================================================
    # PROFILE
    # ============================================================

    def get_profile(self, student_id: str) -> StudentProfile:
        with self._lock:
            return self._get_profile_locked(student_id)

    def _get_profile_locked(self, student_id: str) -> StudentProfile:
        """Internal: caller MUST hold self._lock."""
        if not student_id:
            return StudentProfile(student_id="")

        with self._connect() as conn:
            student_row = conn.execute(
                text('SELECT * FROM students WHERE student_id = :sid'),
                {'sid': student_id}
            ).first()

            if not student_row:
                return StudentProfile(student_id=student_id)

            # Fetch all sessions for this student
            session_rows = conn.execute(
                text('SELECT * FROM sessions WHERE student_id = :sid '
                     'ORDER BY timestamp ASC'),
                {'sid': student_id}
            ).fetchall()

        # ---------- No sessions yet ----------
        if not session_rows:
            return StudentProfile(
                student_id=student_id,
                name=student_row._mapping.get('name', '') or "",
                email=student_row._mapping.get('email', '') or "",
                created_at=student_row._mapping.get('created_at', '') or "",
                last_active=student_row._mapping.get('last_active', '') or "",
            )

        # ---------- Aggregate ----------
        bands: List[float] = []
        grammar_scores: List[float] = []
        coherence_scores: List[float] = []
        pron_scores: List[float] = []
        total_time = 0.0
        total_words = 0

        for s in session_rows:
            m = s._mapping
            b = _safe_float(m.get('overall_band'), 0.0)
            if b > 0:
                bands.append(b)
            g = _safe_float(m.get('grammar_score'), 0.0)
            if g > 0:
                grammar_scores.append(g)
            c = _safe_float(m.get('coherence_score'), 0.0)
            if c > 0:
                coherence_scores.append(c)
            p = _safe_float(m.get('pronunciation_score'), 0.0)
            if p > 0:
                pron_scores.append(p)
            total_time += _safe_float(m.get('total_duration'), 0.0)
            total_words += _safe_int(m.get('word_count'), 0)

        # ---------- Improvement rate ----------
        improvement = 0.0
        if len(bands) >= MIN_IMPROVEMENT_SAMPLES:
            first_half = bands[: len(bands) // 2]
            second_half = bands[len(bands) // 2:]
            if first_half and second_half:
                delta = (
                    sum(second_half) / len(second_half)
                    - sum(first_half) / len(first_half)
                )
                improvement = round(delta * 10, 1)

        # ---------- Level ----------
        avg_band = round(sum(bands) / len(bands), 1) if bands else 0.0
        if avg_band >= 7.5:
            level = "expert"
        elif avg_band >= 6.5:
            level = "advanced"
        elif avg_band >= 5.0:
            level = "intermediate"
        else:
            level = "beginner"

        # ---------- Per-skill averages ----------
        def _mean(xs: List[float]) -> float:
            return round(sum(xs) / len(xs), 1) if xs else 0.0

        avg_skills = {
            'Grammar': _mean(grammar_scores),
            'Coherence': _mean(coherence_scores),
            'Pronunciation': _mean(pron_scores),
        }

        scored_skills = {k: v for k, v in avg_skills.items() if v > 0.0}
        if scored_skills:
            strongest_skill = max(scored_skills, key=scored_skills.get)
            weakest_skill = min(scored_skills, key=scored_skills.get)
        else:
            strongest_skill = weakest_skill = ""

        # ---------- Topics ----------
        all_topics: List[str] = []
        for s in session_rows:
            topics_list = _safe_json_loads(s._mapping.get('topics_covered'), [])
            if isinstance(topics_list, list):
                all_topics.extend(t for t in topics_list if t)

        topic_counts: Dict[str, int] = {}
        for t in all_topics:
            topic_counts[t] = topic_counts.get(t, 0) + 1
        common_topics = sorted(
            topic_counts, key=topic_counts.get, reverse=True
        )[:5]

        recommended = [
            t for t in IELTS_TOPIC_POOL if t not in common_topics
        ][:5]

        return StudentProfile(
            student_id=student_id,
            name=student_row._mapping.get('name', '') or "",
            email=student_row._mapping.get('email', '') or "",
            total_tests=len(session_rows),
            average_band=avg_band,
            best_band=max(bands) if bands else 0.0,
            recent_band=bands[-1] if bands else 0.0,
            grammar_avg=avg_skills['Grammar'],
            coherence_avg=avg_skills['Coherence'],
            pronunciation_avg=avg_skills['Pronunciation'],
            total_practice_time=round(total_time, 1),
            total_words_spoken=total_words,
            strongest_skill=strongest_skill,
            weakest_skill=weakest_skill,
            improvement_rate=improvement,
            common_topics=common_topics,
            recommended_topics=recommended,
            level=level,
            created_at=student_row._mapping.get('created_at', '') or "",
            last_active=student_row._mapping.get('last_active', '') or "",
        )

    # ============================================================
    # SESSIONS LIST
    # ============================================================

    def _row_to_session_dict(self, row) -> Dict:
        """Decode JSON columns back to lists."""
        m = row._mapping
        return {
            'session_id': m.get('session_id'),
            'student_id': m.get('student_id'),
            'timestamp': m.get('timestamp'),
            'difficulty': m.get('difficulty'),
            'topic': m.get('topic'),
            'overall_band': _safe_float(m.get('overall_band'), 0.0),
            'grammar_score': _safe_float(m.get('grammar_score'), 0.0),
            'coherence_score': _safe_float(m.get('coherence_score'), 0.0),
            'pronunciation_score': _safe_float(m.get('pronunciation_score'), 0.0),
            'fluency_score': _safe_float(m.get('fluency_score'), 0.0),
            'vocabulary_score': _safe_float(m.get('vocabulary_score'), 0.0),
            'total_questions': _safe_int(m.get('total_questions'), 0),
            'total_duration': _safe_float(m.get('total_duration'), 0.0),
            'word_count': _safe_int(m.get('word_count'), 0),
            'confidence_level': m.get('confidence_level') or 'medium',
            'topics_covered': _safe_json_loads(m.get('topics_covered'), []),
            'weaknesses': _safe_json_loads(m.get('weaknesses'), []),
            'strengths': _safe_json_loads(m.get('strengths'), []),
            'notes': m.get('notes') or '',
        }

    def get_sessions(
        self,
        student_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict]:
        """Get paginated sessions for a student (JSON columns decoded)."""
        if not student_id:
            return []
        limit = max(1, min(500, _safe_int(limit, 20)))
        offset = max(0, _safe_int(offset, 0))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                text('SELECT * FROM sessions WHERE student_id = :sid '
                     'ORDER BY timestamp DESC LIMIT :lim OFFSET :off'),
                {'sid': student_id, 'lim': limit, 'off': offset}
            ).fetchall()

        return [self._row_to_session_dict(r) for r in rows]

    # ============================================================
    # SKILL PROGRESS
    # ============================================================

    def get_skill_progress(self, student_id: str) -> Dict[str, List[Dict]]:
        """Return skill scores over time for charts."""
        if not student_id:
            return {skill: [] for skill in ALL_SKILLS}

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                text('SELECT skill_name, score, timestamp FROM skill_progress '
                     'WHERE student_id = :sid ORDER BY timestamp ASC'),
                {'sid': student_id}
            ).fetchall()

        progress: Dict[str, List[Dict]] = {skill: [] for skill in ALL_SKILLS}
        for row in rows:
            m = row._mapping
            skill = m.get('skill_name')
            if skill in progress:
                progress[skill].append({
                    'score': _safe_float(m.get('score'), 0.0),
                    'timestamp': m.get('timestamp'),
                })
        return progress

    # ============================================================
    # IMPROVEMENT SUMMARY
    # ============================================================

    def get_improvement_summary(self, student_id: str) -> Dict:
        """Return improvement summary + recommendations."""
        with self._lock:
            profile = self._get_profile_locked(student_id)
            skill_progress = self.get_skill_progress(student_id)
            recent_sessions = self.get_sessions(student_id, limit=5)

        recommendations: List[str] = []

        if profile.average_band < 5.0:
            recommendations.append(
                "Focus on basic sentence structures and simple vocabulary"
            )
            recommendations.append(
                "Practice speaking about familiar topics daily for 5-10 minutes"
            )
        elif profile.average_band < 6.5:
            recommendations.append(
                "Work on using more complex sentence structures"
            )
            recommendations.append(
                "Practice linking ideas with connectors "
                "(however, furthermore, therefore)"
            )
        elif profile.average_band < 7.5:
            recommendations.append(
                "Focus on idiomatic expressions and precise vocabulary"
            )
            recommendations.append(
                "Practice abstract discussions on unfamiliar topics"
            )
        else:
            recommendations.append(
                "Refine pronunciation and intonation patterns"
            )
            recommendations.append(
                "Practice high-level debates on current affairs"
            )

        if profile.weakest_skill:
            skill_map = {
                'Grammar': 'tenses and conditional sentences',
                'Coherence': 'discourse markers and logical organization',
                'Pronunciation': 'shadow native speakers',
            }
            recommendations.append(
                f"Focus on {profile.weakest_skill}: "
                f"{skill_map.get(profile.weakest_skill, 'practice regularly')}"
            )

        if profile.recommended_topics:
            recommendations.append(
                f"Try these new topics: "
                f"{', '.join(profile.recommended_topics[:3])}"
            )

        return {
            'profile': asdict(profile),
            'skill_progress': skill_progress,
            'recommendations': recommendations,
            'recent_sessions': recent_sessions,
        }

    # ============================================================
    # EXPORT
    # ============================================================

    def export_report(
        self,
        student_id: str,
        filepath: Optional[str] = None,
    ) -> str:
        """Export the progress report as a JSON file."""
        summary = self.get_improvement_summary(student_id)

        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"data/reports/{student_id}_{timestamp}.json"

        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)

        return filepath

    # ============================================================
    # CLEANUP
    # ============================================================

    def close(self) -> None:
        """Dispose the engine and release all connections."""
        try:
            self.engine.dispose()
            logger.info("Progress tracker engine disposed")
        except Exception as e:
            logger.warning(f"Error disposing progress tracker engine: {e}")


# ============================================================
# FACTORY
# ============================================================

def create_progress_tracker(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
    engine: Optional[Engine] = None,
) -> ProgressTracker:
    """
    Factory function for ProgressTracker.

    Usage:
        # SQLite (default)
        create_progress_tracker(db_path="data/progress.db")

        # PostgreSQL
        create_progress_tracker(db_url="postgresql+psycopg2://user:pass@localhost:5433/db")

        # Reuse existing engine
        create_progress_tracker(engine=some_engine)
    """
    return ProgressTracker(db_path=db_path, db_url=db_url, engine=engine)


__all__ = [
    'TestSession',
    'StudentProfile',
    'ProgressTracker',
    'create_progress_tracker',
]