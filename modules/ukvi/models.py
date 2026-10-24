# modules/ukvi/models.py
"""
UKVI SQLAlchemy models – using the global db instance from root models.py

v4.0 — B2B MODELS ADDED:
  • UKVIB2BPartner          — Institute accounts with credits
  • UKVIB2BCreditPackage    — Available credit packages
  • UKVIB2BCreditPurchase   — Purchase records
  • UKVIB2BStudent          — Students with scheduled tests
  • UKVIB2BTransaction      — Credit audit trail

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
import secrets
from datetime import datetime, timezone, timedelta, date, time
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
    phone = db.Column(db.String(20), nullable=True, index=True)
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

    # ═══ Soft delete (single source of truth for is_active) ═══
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

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

    @is_active.setter
    def is_active(self, value):
        """Set is_active → maps to deleted_at (no DB column write)"""
        if value:
            self.deleted_at = None
        else:
            self.deleted_at = datetime.now(timezone.utc)
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

    def is_active(self):
        """Check if subscription is active (timezone-aware)."""
        if self.status != 'active':
            return False
        if not self.subscription_end:
            return True
        end = self.subscription_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return end > now

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


# ═══════════════════════════════════════════════════════════════════════
# B2B MODELS — Institute + Credits + Students (UKVI only)
# ═══════════════════════════════════════════════════════════════════════

class UKVIB2BPartner(db.Model):
    """B2B Institute account with credit-based billing"""
    __tablename__ = 'ukvi_b2b_partners'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)

    # Credit system (1 credit = 1 test = Rs 100)
    credits_balance = db.Column(db.Integer, default=0, nullable=False)
    total_credits_purchased = db.Column(db.Integer, default=0, nullable=False)
    total_credits_used = db.Column(db.Integer, default=0, nullable=False)

    # Status
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_admin_approved = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_login = db.Column(db.DateTime, nullable=True)

    # Relationships
    students = db.relationship(
        'UKVIB2BStudent',
        backref='partner',
        lazy='dynamic',
        cascade='all, delete-orphan',
    )
    purchases = db.relationship(
        'UKVIB2BCreditPurchase',
        backref='partner',
        lazy='dynamic',
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(
            password, method='pbkdf2:sha256'
        )

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_credit(self):
        return self.credits_balance > 0

    def __repr__(self):
        return f'<UKVIB2BPartner {self.email} credits={self.credits_balance}>'


class UKVIB2BCreditPackage(db.Model):
    """Available credit packages (admin-configured)"""
    __tablename__ = 'ukvi_b2b_credit_packages'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    price_npr = db.Column(db.Float, nullable=False)
    discount_percent = db.Column(db.Float, default=0)
    description = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, default=True)
    is_featured = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)

    @property
    def price_per_credit(self):
        return round(self.price_npr / self.credits, 2) if self.credits else 0

    def __repr__(self):
        return f'<CreditPackage {self.name} {self.credits}cr>'


class UKVIB2BCreditPurchase(db.Model):
    """Credit purchase records (eSewa/Khalti/Manual)"""
    __tablename__ = 'ukvi_b2b_purchases'

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_b2b_partners.id'),
        nullable=False,
        index=True,
    )
    package_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_b2b_credit_packages.id'),
        nullable=True,
    )

    credits = db.Column(db.Integer, nullable=False)
    amount_npr = db.Column(db.Float, nullable=False)

    # Payment
    payment_method = db.Column(db.String(20), nullable=False)  # esewa/khalti/manual
    payment_status = db.Column(db.String(20), default='pending')  # pending/completed/failed/rejected
    payment_ref = db.Column(db.String(100))  # Transaction ID
    payment_screenshot = db.Column(db.String(300))  # Manual upload path
    admin_notes = db.Column(db.Text)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at = db.Column(db.DateTime, nullable=True)

    package = db.relationship('UKVIB2BCreditPackage')

    def __repr__(self):
        return f'<Purchase {self.id} {self.credits}cr {self.payment_status}>'


class UKVIB2BStudent(db.Model):
    """B2B student with scheduled test — links to UKVIUser"""
    __tablename__ = 'ukvi_b2b_students'

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_b2b_partners.id'),
        nullable=False,
        index=True,
    )
    ukvi_user_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_users.id'),
        nullable=True,
        index=True,
    )

    # Student info (institute fills)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), index=True)
    phone = db.Column(db.String(20))
    passport_number = db.Column(db.String(50))
    notes = db.Column(db.Text)

    # Scheduled test (institute selects)
    scheduled_date = db.Column(db.Date, nullable=False)
    scheduled_time = db.Column(db.Time, nullable=False)

    # Token (link)
    test_token = db.Column(
        db.String(64), unique=True, nullable=False, index=True
    )
    token_created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    token_used = db.Column(db.Boolean, default=False, nullable=False)
    token_revoked = db.Column(db.Boolean, default=False, nullable=False)

    # Registration (student fills)
    registered_at = db.Column(db.DateTime, nullable=True)

    # Test status
    test_started_at = db.Column(db.DateTime, nullable=True)
    test_completed_at = db.Column(db.DateTime, nullable=True)

    # Result summary
    result_score = db.Column(db.Float, nullable=True)
    result_grade = db.Column(db.String(20), nullable=True)

    # Credit tracking
    credit_deducted = db.Column(db.Boolean, default=False, nullable=False)
    credit_refunded = db.Column(db.Boolean, default=False, nullable=False)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.test_token:
            self.test_token = secrets.token_urlsafe(32)

    # ═══════════════════════════════════════════════════════════════
    # WINDOW LOGIC
    # ═══════════════════════════════════════════════════════════════

    @property
    def scheduled_datetime(self):
        """Combined scheduled date + time (UTC)"""
        return datetime.combine(
            self.scheduled_date, self.scheduled_time
        ).replace(tzinfo=timezone.utc)

    @property
    def window_start(self):
        """Test window begins at scheduled time"""
        return self.scheduled_datetime

    @property
    def window_end(self):
        """Test window ends 24 hours after start"""
        return self.scheduled_datetime + timedelta(hours=24)

    @property
    def is_in_window(self):
        """Current time is within test window"""
        now = datetime.now(timezone.utc)
        return self.window_start <= now <= self.window_end

    @property
    def is_too_early(self):
        """Before scheduled time"""
        return datetime.now(timezone.utc) < self.window_start

    @property
    def is_expired(self):
        """Past 24-hour window"""
        return datetime.now(timezone.utc) > self.window_end

    @property
    def window_status(self):
        """Human-readable status"""
        if self.token_used:
            return 'used'
        if self.token_revoked:
            return 'revoked'
        if self.is_expired:
            return 'expired'
        if self.is_too_early:
            return 'scheduled'
        if self.is_in_window:
            return 'active'
        return 'unknown'

    @property
    def test_url(self):
        return f'/ukvi/b2b/test/{self.test_token}'

    def __repr__(self):
        return f'<UKVIB2BStudent {self.full_name} [{self.window_status}]>'

    # ── Interview Profile (institute fills upfront) ──
    father_name = db.Column(db.String(200))
    mother_name = db.Column(db.String(200))
    university = db.Column(db.String(300))
    university_city = db.Column(db.String(100))
    course = db.Column(db.String(300))
    course_duration = db.Column(db.String(50))
    ielts_score = db.Column(db.String(20))
    tuition_fee = db.Column(db.String(50))
    living_cost = db.Column(db.String(50))
    sponsor_name = db.Column(db.String(200))
    sponsor_income = db.Column(db.String(50))
    sponsor_savings = db.Column(db.String(50))
    home_country = db.Column(db.String(100), default="Nepal")
    profile_completed = db.Column(db.Boolean, default=False, nullable=False)

    # ── Attempt tracking (max 5 attempts per credit) ──
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    max_attempts = db.Column(db.Integer, default=5, nullable=False)


class UKVIB2BTransaction(db.Model):
    """Credit transaction log (audit trail)"""
    __tablename__ = 'ukvi_b2b_transactions'

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(
        db.Integer,
        db.ForeignKey('ukvi_b2b_partners.id'),
        nullable=False,
        index=True,
    )

    # Types: purchase / student_added / refund / admin_adjust
    type = db.Column(db.String(30), nullable=False)
    credits_change = db.Column(db.Integer, nullable=False)  # +/- 
    balance_after = db.Column(db.Integer, nullable=False)

    description = db.Column(db.String(300))
    reference_id = db.Column(db.Integer)
    reference_type = db.Column(db.String(20))

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f'<Txn {self.type} {self.credits_change:+d} → {self.balance_after}>'