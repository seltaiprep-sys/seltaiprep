# modules/pte/models.py
"""PTE Database Models – Shared db instance from main models

 ARCHITECTURE NOTE (v5.0):
  PTE is now POOL-PRIMARY. The following are DEPRECATED but kept for
  backward compatibility with pre-v5 sessions:
    • PTETestBank → replaced by PTETestPool
    • PTEUserTestBankUsage → replaced by PTEUserPoolProgress
    • PTETestSession.bank_id → pool tests use test_data.pool_id instead
  Do NOT use these in new code.

v5.1 NOTE (Profile fields):
    * PTEUser.full_name   — display name (optional)
    * PTEUser.target_band — target PTE/IELTS equivalent band (optional)
"""
import json
from datetime import datetime, timedelta, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.ext.mutable import MutableDict

from models import db


class PTEUser(UserMixin, db.Model):
    __tablename__ = 'pte_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    facebook_id = db.Column(db.String(100), unique=True, nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    full_name = db.Column(db.String(120), nullable=True)      # 🆕
    target_band = db.Column(db.Float, nullable=True)          # 🆕
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class PTESubscription(db.Model):
    __tablename__ = 'pte_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('pte_users.id'), nullable=False, index=True)
    plan = db.Column(db.String(50), default='free')
    status = db.Column(db.String(50), default='inactive', index=True)
    tests_remaining = db.Column(db.Integer, default=3)
    tests_taken = db.Column(db.Integer, default=0)
    free_tests_used = db.Column(db.Integer, default=0)
    free_tests_limit = db.Column(db.Integer, default=3)
    free_usage = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    amount_paid_npr = db.Column(db.Integer, default=0)
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)

    user = db.relationship('PTEUser', backref=db.backref('subscription', uselist=False))

    def is_active(self):
        if self.status != 'active':
            return False
        if self.subscription_end and self.subscription_end < datetime.now(timezone.utc):
            return False
        return True

    def days_remaining(self):
        if not self.subscription_end:
            return 0
        end = self.subscription_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (end - now).days)

    def increment_free_usage(self, test_type: str):
        usage = dict(self.free_usage or {})
        usage[test_type] = int(usage.get(test_type, 0) or 0) + 1
        self.free_usage = usage
        return usage[test_type]


# ═══════════════════════════════════════════════════════════════════════
# DEPRECATED — kept for backward compatibility
# Do NOT use in new code. Use PTETestPool instead.
# ═══════════════════════════════════════════════════════════════════════
class PTETestBank(db.Model):
    """
    DEPRECATED (v5.0): Replaced by PTETestPool.

    Kept so that:
      • PTETestSession.bank_id FK doesn't break
      • PTEUserTestBankUsage FK doesn't break
      • Existing pre-v5 sessions can still be read

    New admin generation → uses PTETestPool.
    """
    __tablename__ = 'pte_test_bank'

    id = db.Column(db.Integer, primary_key=True)
    test_type = db.Column(db.String(50), nullable=False, index=True)
    difficulty = db.Column(db.String(50), default='medium', index=True)
    topic = db.Column(db.String(200), default='general', index=True)
    test_data = db.Column(MutableDict.as_mutable(db.JSON), nullable=False)
    usage_count = db.Column(db.Integer, default=0, index=True)
    used_by_users = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    last_used_at = db.Column(db.DateTime, nullable=True, index=True)

    def get_used_by_users(self):
        raw = self.used_by_users
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []

    def mark_used_by_user(self, user_id):
        users = self.get_used_by_users()
        if user_id not in users:
            users.append(user_id)
            self.used_by_users = users
            self.usage_count = (self.usage_count or 0) + 1
            self.last_used_at = datetime.now(timezone.utc)
        return self

    def has_user_used(self, user_id):
        return user_id in self.get_used_by_users()


class PTETestSession(db.Model):
    __tablename__ = 'pte_test_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('pte_users.id'), nullable=False, index=True)
    test_type = db.Column(db.String(50), index=True)
    difficulty = db.Column(db.String(20))

    # DEPRECATED: bank_id is legacy from v4.x.
    # Pool-primary architecture stores pool_id inside test_data dict.
    # Kept for backward compatibility only.
    bank_id = db.Column(db.Integer, db.ForeignKey('pte_test_bank.id'), nullable=True, index=True)

    start_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    last_updated = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    current_question_index = db.Column(db.Integer, default=0)
    answers_so_far = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    test_data = db.Column(MutableDict.as_mutable(db.JSON))
    status = db.Column(db.String(20), default='in_progress', index=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    is_template = db.Column(db.Boolean, default=False, index=True)

    user = db.relationship('PTEUser', backref=db.backref('test_sessions', lazy='dynamic'))
    bank_test = db.relationship('PTETestBank', backref=db.backref('sessions', lazy='dynamic'))

    def mark_completed(self, answers=None):
        self.status = 'completed'
        self.ended_at = datetime.now(timezone.utc)
        self.last_updated = datetime.now(timezone.utc)
        if answers is not None:
            self.answers_so_far = answers
        return self

    def update_adaptive_state(
        self,
        question_time_remaining=None,
        section_time_remaining=None,
        listening_audio_play_counts=None,
        user_pace_history=None,
        user_touched_questions=None,
        time_remaining_seconds=None,
    ):
        data = dict(self.test_data or {})
        if question_time_remaining is not None:
            data['question_time_remaining'] = question_time_remaining
        if section_time_remaining is not None:
            data['section_time_remaining'] = section_time_remaining
        if listening_audio_play_counts is not None:
            data['listening_audio_play_counts'] = listening_audio_play_counts
        if user_pace_history is not None:
            data['user_pace_history'] = user_pace_history
        if user_touched_questions is not None:
            data['user_touched_questions'] = user_touched_questions
        if time_remaining_seconds is not None:
            data['time_remaining_seconds'] = time_remaining_seconds
        self.test_data = data
        self.last_updated = datetime.now(timezone.utc)
        return self


# ═══════════════════════════════════════════════════════════════════════
# DEPRECATED — kept so existing rows don't break
# Do NOT use in new code. Use PTEUserPoolProgress instead.
# ═══════════════════════════════════════════════════════════════════════
class PTEUserTestBankUsage(db.Model):
    """DEPRECATED (v5.0): Replaced by PTEUserPoolProgress."""
    __tablename__ = 'pte_user_test_bank_usage'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('pte_users.id'), nullable=False, index=True)
    bank_id = db.Column(db.Integer, db.ForeignKey('pte_test_bank.id'), nullable=False, index=True)
    taken_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed = db.Column(db.Boolean, default=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey('pte_test_sessions.id'), nullable=True, index=True)
    score = db.Column(db.Float, nullable=True)
    band_score = db.Column(db.Float, nullable=True)

    user = db.relationship('PTEUser', backref=db.backref('bank_usage', lazy='dynamic'))
    bank_test = db.relationship('PTETestBank', backref=db.backref('usage_records', lazy='dynamic'))
    session = db.relationship('PTETestSession', backref=db.backref('usage_record', uselist=False))

    def mark_completed(self, score=None, band_score=None):
        self.completed = True
        self.completed_at = datetime.now(timezone.utc)
        if score is not None:
            self.score = score
        if band_score is not None:
            self.band_score = band_score
        return self


class PTETestResult(db.Model):
    """
    Stores a completed test result.

     MIGRATION NOTE:
    Fields marked [NEW] require an ALTER TABLE migration.
    Run the SQL below once on your DB:

      ALTER TABLE pte_test_results
        ADD COLUMN session_id INT NULL,
        ADD COLUMN difficulty VARCHAR(20) NULL,
        ADD COLUMN section_scores JSON NULL,
        ADD COLUMN detailed_results JSON NULL,
        ADD COLUMN enabling_skills JSON NULL,
        ADD COLUMN speaking_score FLOAT NULL,
        ADD COLUMN writing_score FLOAT NULL,
        ADD COLUMN auto_submitted BOOLEAN DEFAULT 0,
        ADD COLUMN time_taken INT NULL,
        ADD COLUMN result_payload JSON NULL;
    """
    __tablename__ = 'pte_test_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('pte_users.id'), nullable=False, index=True)
    test_type = db.Column(db.String(50), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)
    band_score = db.Column(db.Float, nullable=False)
    answers = db.Column(db.JSON)
    feedback = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    session_id = db.Column(db.Integer, db.ForeignKey('pte_test_sessions.id'), nullable=True, index=True)
    difficulty = db.Column(db.String(20), nullable=True, index=True)

    section_scores = db.Column(db.JSON, nullable=True)
    detailed_results = db.Column(db.JSON, nullable=True)
    enabling_skills = db.Column(db.JSON, nullable=True)
    speaking_score = db.Column(db.Float, nullable=True)
    writing_score = db.Column(db.Float, nullable=True)
    auto_submitted = db.Column(db.Boolean, default=False)
    time_taken = db.Column(db.Integer, nullable=True)
    result_payload = db.Column(db.JSON, nullable=True)

    user = db.relationship('PTEUser', backref=db.backref('test_results', lazy='dynamic'))
    session = db.relationship('PTETestSession', backref=db.backref('result', uselist=False))

    def get_answers(self):
        raw = self.answers
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    def set_answers(self, answers_dict):
        self.answers = answers_dict if isinstance(answers_dict, dict) else {}

    def set_full_result(self, scoring_output: dict):
        if not isinstance(scoring_output, dict):
            return self
        self.section_scores = scoring_output.get('section_scores') or {}
        self.detailed_results = scoring_output.get('detailed_results') or []
        self.enabling_skills = scoring_output.get('enabling_skills') or {}
        if scoring_output.get('speaking_score') is not None:
            self.speaking_score = float(scoring_output['speaking_score'])
        if scoring_output.get('writing_score') is not None:
            self.writing_score = float(scoring_output['writing_score'])
        if scoring_output.get('feedback'):
            self.feedback = str(scoring_output['feedback'])
        self.result_payload = scoring_output
        return self

    def get_full_result(self):
        if isinstance(self.result_payload, dict) and self.result_payload:
            return self.result_payload
        return {
            'pte_score': self.score,
            'score': self.score,
            'band_score': self.band_score,
            'ielts_equivalent': self.band_score,
            'ielts_band': self.band_score,
            'section_scores': self.section_scores or {},
            'detailed_results': self.detailed_results or [],
            'enabling_skills': self.enabling_skills or {},
            'speaking_score': self.speaking_score,
            'writing_score': self.writing_score,
            'feedback': self.feedback or '',
        }


class PTESetting(db.Model):
    __tablename__ = 'pte_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.String(500), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    @classmethod
    def get_value(cls, key: str, default: str = None) -> str:
        setting = cls.query.filter_by(key=key).first()
        if setting:
            return setting.value
        if default is not None:
            new_setting = cls(key=key, value=default)
            db.session.add(new_setting)
            db.session.commit()
            return default
        return None

    @classmethod
    def get_int(cls, key: str, default: int = 3) -> int:
        try:
            value = cls.get_value(key, str(default))
            return int(value) if value else default
        except (ValueError, TypeError):
            return default

    @classmethod
    def set_value(cls, key: str, value: str) -> bool:
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
            setting.updated_at = datetime.now(timezone.utc)
        else:
            setting = cls(key=key, value=value)
            db.session.add(setting)
        db.session.commit()
        return True


# ═══════════════════════════════════════════════════════════════════════
# TEST POOL — Primary architecture (v5.0+)
# ═══════════════════════════════════════════════════════════════════════
class PTETestPool(db.Model):
    """
    Shared pool of pre-generated tests.

    Design:
      • Every user gets served from this pool.
      • Max MAX_POOL_SIZE (default 100) items per module.
      • Once full, no more generation — only serving (round-robin).
      • usage_count tracks how many users have been served this item.

    Module keys: 'pte_reading' | 'pte_listening' | 'pte_speaking_writing' | 'pte_full'
    """
    __tablename__ = 'pte_test_pool'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(50), nullable=False, index=True)
    difficulty = db.Column(db.String(20), default='medium', index=True)
    test_data = db.Column(MutableDict.as_mutable(db.JSON), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_by = db.Column(db.Integer, nullable=True)
    usage_count = db.Column(db.Integer, default=0, index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    question_count = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.Index('idx_pte_pool_module_usage', 'module', 'usage_count'),
        db.Index('idx_pte_pool_module_active', 'module', 'is_active'),
    )

    def __repr__(self):
        return f"<PTETestPool {self.module} #{self.id} usage={self.usage_count}>"


class PTEGenerationLock(db.Model):
    """
    Per-module generation lock.

    Prevents thundering herd: when N users click Generate at once,
    only ONE (the first to acquire the lock) performs generation.
    Others get a 202 "waiting" and retry after a short delay.

    The `module` column is UNIQUE, so at most one lock row exists per module.
    """
    __tablename__ = 'pte_generation_lock'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(50), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), default='idle')
    locked_by = db.Column(db.Integer, nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
    error = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<PTEGenerationLock {self.module} status={self.status} by={self.locked_by}>"


class PTEUserPoolProgress(db.Model):
    """
    Tracks which pool tests each user has COMPLETED.

    Used by test_pool_manager to detect whether a user has exhausted
    the pool for a module.
    """
    __tablename__ = 'pte_user_pool_progress'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    module = db.Column(db.String(50), nullable=False, index=True)
    pool_id = db.Column(db.Integer, nullable=False, index=True)
    completed_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'module', 'pool_id',
            name='uq_pte_user_module_pool',
        ),
        db.Index('idx_pte_user_module', 'user_id', 'module'),
    )

    def __repr__(self):
        return f"<PTEUserPoolProgress user={self.user_id} {self.module} pool={self.pool_id}>"


def create_default_pte_subscription(user_id):
    sub = PTESubscription(
        user_id=user_id,
        plan='free',
        status='inactive',
        tests_remaining=0,
        free_tests_limit=3,
        free_tests_used=0,
        free_usage={},
        amount_paid_npr=0,
        subscription_start=datetime.now(timezone.utc),
        subscription_end=datetime.now(timezone.utc)
    )
    db.session.add(sub)
    db.session.commit()
    return sub