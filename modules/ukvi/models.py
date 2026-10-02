# modules/ukvi/models.py
"""
UKVI SQLAlchemy models – using the global db instance from root models.py

v3.1 — Profile fields:
  • UKVIUser.full_name   — display name (optional)
  • UKVIUser.target_band — target band score (optional)

v3.0 — UKVI POOL + ASYNC QUEUE:
  • Added UKVIGenerationJob (background job tracking for async generation)
  • UKVITestBank.topic: String(255) — pool cache key
  • Same university → same pool set (shared by many users)
  • Different university → separate pool set

v2.0 — REAL UKVI REALISM:
  • UKVITestBank.topic: String(100) → String(255)
  • UKVIUser.profile_data: extended comment

v1.0 — Initial models.
"""

import json
from datetime import datetime, timezone
from models import db  # Import the global db from root
from werkzeug.security import generate_password_hash, check_password_hash


# ═══════════════════════════════════════════════════════════════════════
# USER
# ═══════════════════════════════════════════════════════════════════════
class UKVIUser(db.Model):
    __tablename__ = 'ukvi_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200))
    google_id = db.Column(db.String(80), unique=True, nullable=True)
    facebook_id = db.Column(db.String(100), unique=True, nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    full_name = db.Column(db.String(120), nullable=True)      # 🆕
    target_band = db.Column(db.Float, nullable=True)          # 🆕
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_login = db.Column(db.DateTime, nullable=True)

    # JSON profile — all fields used by real UKVI interview generator:
    # {
    #   "full_name":      "Ram Bahadur",
    #   "university":     "University of Central Lancashire",
    #   "university_city":"Preston",
    #   "course":         "MSc Computer Science",
    #   "course_duration":"1 year",
    #   "tuition_fee":    "£15,000",
    #   "living_cost":    "£12,000/year",
    #   "sponsor_name":   "Father",
    #   "sponsor_income": "NPR 15,00,000/year",
    #   "sponsor_savings":"NPR 50,00,000",
    #   "home_country":   "Nepal",
    #   "father_name":    "Hari Bahadur",
    #   "mother_name":    "Sita Devi",
    #   "ielts_score":    "6.5"
    # }
    profile_data = db.Column(db.Text, default='{}')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_profile(self):
        return json.loads(self.profile_data) if self.profile_data else {}

    def set_profile(self, profile: dict):
        self.profile_data = json.dumps(profile)

    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False


# ═══════════════════════════════════════════════════════════════════════
# SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════════════
class UKVISubscription(db.Model):
    __tablename__ = 'ukvi_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_users.id'),
        unique=True,
        nullable=False,
    )
    plan = db.Column(db.String(20), default='free')
    status = db.Column(db.String(20), default='active')
    free_usage = db.Column(db.Text, default='{}')
    tests_remaining = db.Column(db.Integer, default=0)
    tests_taken = db.Column(db.Integer, default=0)
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)
    amount_paid_npr = db.Column(db.Float, default=0.0)

    def get_free_usage(self):
        return json.loads(self.free_usage) if self.free_usage else {}

    def set_free_usage(self, usage: dict):
        self.free_usage = json.dumps(usage)

    user = db.relationship(
        'UKVIUser',
        backref=db.backref('subscription', uselist=False),
    )


# ═══════════════════════════════════════════════════════════════════════
# TEST SESSION
# ═══════════════════════════════════════════════════════════════════════
class UKVITestSession(db.Model):
    __tablename__ = 'ukvi_test_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_users.id'),
        nullable=False,
    )
    test_type = db.Column(db.String(20), default='interview')
    difficulty = db.Column(db.String(20), default='medium')
    status = db.Column(db.String(20), default='in_progress')
    test_data = db.Column(db.Text)
    answers_so_far = db.Column(db.Text, default='{}')
    current_question_index = db.Column(db.Integer, default=0)
    start_time = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_updated = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship(
        'UKVIUser',
        backref=db.backref('sessions', lazy='dynamic'),
    )

    def get_test_data(self):
        return json.loads(self.test_data) if self.test_data else {}

    def set_test_data(self, data: dict):
        self.test_data = json.dumps(data)

    def get_answers(self):
        return json.loads(self.answers_so_far) if self.answers_so_far else {}

    def set_answers(self, answers: dict):
        self.answers_so_far = json.dumps(answers)


# ═══════════════════════════════════════════════════════════════════════
# TEST RESULT
# ═══════════════════════════════════════════════════════════════════════
class UKVITestResult(db.Model):
    __tablename__ = 'ukvi_test_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_users.id'),
        nullable=False,
    )
    test_type = db.Column(db.String(20), default='interview')
    score = db.Column(db.Float)
    band_score = db.Column(db.Float)
    answers = db.Column(db.Text, default='{}')
    feedback = db.Column(db.Text, default='')
    detailed_evaluation = db.Column(db.Text, default='{}')
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user = db.relationship(
        'UKVIUser',
        backref=db.backref('results', lazy='dynamic'),
    )

    def get_answers(self):
        return json.loads(self.answers) if self.answers else {}

    def set_answers(self, ans: dict):
        self.answers = json.dumps(ans)

    def get_evaluation(self):
        return json.loads(self.detailed_evaluation) if self.detailed_evaluation else {}

    def set_evaluation(self, ev: dict):
        self.detailed_evaluation = json.dumps(ev)


# ═══════════════════════════════════════════════════════════════════════
# TEST BANK (POOL — 1 set per university/course/difficulty)
# ═══════════════════════════════════════════════════════════════════════
class UKVITestBank(db.Model):
    __tablename__ = 'ukvi_test_bank'

    id = db.Column(db.Integer, primary_key=True)
    test_type = db.Column(db.String(20), default='interview')
    difficulty = db.Column(db.String(20), default='medium')

    # v2.0 — FIX: 100 → 255
    # cache_key = f"ukvi_{university}_{course}_{difficulty}"
    topic = db.Column(db.String(255), nullable=True, index=True)

    test_data = db.Column(db.Text)
    usage_count = db.Column(db.Integer, default=0)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def get_test_data(self):
        return json.loads(self.test_data) if self.test_data else {}

    def set_test_data(self, data: dict):
        self.test_data = json.dumps(data)


# ═══════════════════════════════════════════════════════════════════════
# GENERATION JOB (v3.0 — ASYNC QUEUE)
# ═══════════════════════════════════════════════════════════════════════
class UKVIGenerationJob(db.Model):
    """
    Background job for UKVI interview generation.

    Flow:
      1. User requests → check pool (UKVITestBank)
      2. Pool HIT → serve immediately (with rephrase)
      3. Pool MISS → check existing job for same cache_key
      4. Same job exists → return job_id (multiple users share)
      5. New job → create + queue → worker generates → save to pool

    Same university → same cache_key → shared job → 1 AI call for all users
    Different university → different cache_key → separate jobs
    """
    __tablename__ = 'ukvi_generation_jobs'

    id = db.Column(db.String(32), primary_key=True)   # job_id (hex)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    cache_key = db.Column(db.String(255), nullable=False, index=True)
    university = db.Column(db.String(200), nullable=True)
    course = db.Column(db.String(200), nullable=True)
    difficulty = db.Column(db.String(20), default='medium')

    # Status: queued | generating | complete | failed
    status = db.Column(db.String(20), default='queued', index=True)
    questions = db.Column(db.Text)                # JSON: {"questions": [...]}
    error = db.Column(db.Text)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    def get_questions(self):
        """Return list of question dicts."""
        if not self.questions:
            return []
        try:
            data = json.loads(self.questions)
            return data.get('questions', [])
        except Exception:
            return []