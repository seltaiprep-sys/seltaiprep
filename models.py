# models.py
"""
Database models for IELTS only.
PTE and UKVI have their own separate model files.

v8 NOTE (Coupons):
    * `Coupon` — admin-created discount codes (percent / fixed).
    * `CouponUsage` — tracks which user used which coupon.

v7 NOTE (Profile fields):
    * User.full_name   — display name (optional)
    * User.target_band — IELTS target band score (0–9, optional)

v6 NOTE (Facebook + Phone login):
    * User.facebook_id — Facebook OAuth ID
    * User.phone — phone number for SMS login

v5 NOTE (Payment + Bills):
    * `PaymentSettings` — singleton row controlled by admin.
    * `Bill` — auto-generated invoices for AUTOMATIC payments only.

v4 NOTE (Full-Test separation):
    `FullTestVariant` is a SEPARATE table from `TestBank`.

v3 NOTE (Test Bank system):
    `TestBank.used_by_users` is the SHARED-resource tracking column.
"""

import json
from datetime import datetime, timedelta, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.ext.mutable import MutableDict  # added for IELTSTestPool

db = SQLAlchemy()


# ============================================================
# IELTS USER MODEL
# ============================================================
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    facebook_id = db.Column(db.String(100), unique=True, nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    full_name = db.Column(db.String(120), nullable=True)
    target_band = db.Column(db.Float, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def get_id(self):
        return str(self.id)


# ============================================================
# IELTS SUBSCRIPTION
# ============================================================
class Subscription(db.Model):
    __tablename__ = 'subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan = db.Column(db.String(50), default='free')
    status = db.Column(db.String(50), default='inactive')
    tests_remaining = db.Column(db.Integer, default=3)
    tests_taken = db.Column(db.Integer, default=0)
    free_tests_used = db.Column(db.Integer, default=0)
    free_tests_limit = db.Column(db.Integer, default=3)
    free_usage = db.Column(db.JSON, default=dict)
    amount_paid_npr = db.Column(db.Integer, default=0)
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('subscription', uselist=False))

    def is_active(self):
        # FIX: Free plan is NOT considered an active subscription
        if self.status != 'active':
            return False
        if self.plan == 'free':
            return False
        if self.subscription_end and self.subscription_end < datetime.now(timezone.utc):
            return False
        return True

    def days_remaining(self):
        if not self.subscription_end:
            return 0
        # FIX: Ensure subscription_end is timezone-aware
        end = self.subscription_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (end - now).days)


# ============================================================
# IELTS TEST BANK (STANDALONE tests — SHARED RESOURCE)
# ============================================================
class TestBank(db.Model):
    """
    Shared STANDALONE test bank row.

     IMPORTANT (v4):
        This table stores ONLY standalone phase tests:
            test_type IN ('listening', 'reading', 'writing', 'speaking')

        Full IELTS mock tests are stored in `FullTestVariant` (separate table).
        DO NOT save rows with test_type='full_ielts' here.

    v3 rolling bank semantics:
        * A test is a SHARED resource — many users can take the same test.
        * `used_by_users` (JSON-encoded list) tracks which user_ids have
          already taken it. `TestBankManager.get_or_create_test()` filters
          per-user, so a test generated by User 1 is immediately available
          to User 2, User 3, etc.
        * `usage_count` and `last_used_at` are used to:
            - prefer least-used unused tests when serving
            - serve oldest repeat when the module cap (100) is reached
        * The 100-per-MODULE cap (all difficulties combined) is enforced
          by TestBankManager, not by this model.
    """
    __tablename__ = 'test_bank'

    id = db.Column(db.Integer, primary_key=True)
    test_type = db.Column(db.String(50), nullable=False)
    difficulty = db.Column(db.String(50), default='medium')
    topic = db.Column(db.String(200), default='general')
    test_data = db.Column(db.JSON, nullable=False)
    usage_count = db.Column(db.Integer, default=0)
    used_by_users = db.Column(db.Text, default='[]')  # v3 shared-resource tracking
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = db.Column(db.DateTime, nullable=True)

    # ─── Helpers for safe JSON handling of used_by_users ───
    def get_used_by_users(self):
        try:
            return json.loads(self.used_by_users) if self.used_by_users else []
        except Exception:
            return []

    def mark_used_by_user(self, user_id):
        users = self.get_used_by_users()
        uid = str(user_id)
        if uid not in users:
            users.append(uid)
            self.used_by_users = json.dumps(users)
            self.usage_count = (self.usage_count or 0) + 1
            self.last_used_at = datetime.now(timezone.utc)
        return self

    def has_user_used(self, user_id):
        return str(user_id) in self.get_used_by_users()


# ============================================================
# IELTS FULL-TEST VARIANT BANK (SEPARATE from TestBank)
# ============================================================
class FullTestVariant(db.Model):
    """
    Full IELTS mock test variant — SEPARATE table from `TestBank`.

    Each row contains ONE complete, pre-generated IELTS mock test:

        snapshot = {
            'listening': {...},
            'reading': {...},
            'writing': {...},
            'speaking': {...},
        }
    """
    __tablename__ = 'full_test_variants'

    id = db.Column(db.Integer, primary_key=True)
    difficulty = db.Column(db.String(20), default='medium', nullable=False)
    accent = db.Column(db.String(30), default='british', nullable=False)
    variant_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # Complete snapshot (JSON)
    snapshot = db.Column(db.JSON, nullable=True)

    # Fast-access listening audio (also duplicated inside snapshot.listening)
    listening_audio_urls = db.Column(db.JSON, default=dict)
    listening_audio_timings = db.Column(db.JSON, default=dict)

    # Status: 'generating' | 'ready' | 'failed'
    status = db.Column(db.String(20), default='generating', nullable=False, index=True)
    error_message = db.Column(db.Text, nullable=True)

    # Shared-resource tracking (same pattern as TestBank)
    usage_count = db.Column(db.Integer, default=0)
    used_by_users = db.Column(db.Text, default='[]')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ready_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_ft_difficulty_status', 'difficulty', 'status'),
    )

    # ────────────────────────────────────────────────────────
    # Shared-resource helpers
    # ────────────────────────────────────────────────────────
    def get_used_by_users(self):
        try:
            return json.loads(self.used_by_users) if self.used_by_users else []
        except Exception:
            return []

    def mark_used_by(self, user_id):
        users = self.get_used_by_users()
        uid = str(user_id)
        if uid not in users:
            users.append(uid)
            self.used_by_users = json.dumps(users)
            self.usage_count = (self.usage_count or 0) + 1
            self.last_used_at = datetime.now(timezone.utc)
        return self

    def has_user_used(self, user_id):
        return str(user_id) in self.get_used_by_users()

    # ────────────────────────────────────────────────────────
    # Snapshot accessors — always return defensive copies
    # ────────────────────────────────────────────────────────
    def get_listening_snapshot(self):
        if not self.snapshot:
            return None
        snap = self.snapshot.get('listening')
        if not snap:
            return None

        # Defensive copy so callers can mutate freely
        snap = dict(snap)

        # Merge fast-access audio columns
        if self.listening_audio_urls:
            merged = dict(snap.get('audio_urls') or {})
            merged.update(self.listening_audio_urls or {})
            snap['audio_urls'] = merged
        if self.listening_audio_timings:
            merged = dict(snap.get('audio_timings') or {})
            merged.update(self.listening_audio_timings or {})
            snap['audio_timings'] = merged

        # Guarantee metadata fields
        sections = snap.get('sections') or []
        if not snap.get('generated_sections'):
            snap['generated_sections'] = list(range(1, len(sections) + 1))
        snap.setdefault('total_sections', len(sections) or 4)
        snap.setdefault('partial', False)
        snap.setdefault('difficulty', self.difficulty)
        snap.setdefault('accent', self.accent)

        return snap

    def get_reading_snapshot(self):
        return (self.snapshot or {}).get('reading')

    def get_writing_snapshot(self):
        return (self.snapshot or {}).get('writing')

    def get_speaking_snapshot(self):
        return (self.snapshot or {}).get('speaking')

    def is_ready(self):
        return self.status == 'ready' and bool(self.snapshot)

    def to_summary_dict(self):
        """Lightweight representation for admin lists."""
        snap = self.snapshot or {}
        return {
            'id': self.id,
            'difficulty': self.difficulty,
            'accent': self.accent,
            'status': self.status,
            'usage_count': self.usage_count or 0,
            'used_by_count': len(self.get_used_by_users()),
            'has_listening': bool(snap.get('listening')),
            'has_reading': bool(snap.get('reading')),
            'has_writing': bool(snap.get('writing')),
            'has_speaking': bool(snap.get('speaking')),
            'audio_sections': sorted(list((self.listening_audio_urls or {}).keys())),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'ready_at': self.ready_at.isoformat() if self.ready_at else None,
        }


# ============================================================
# IELTS TEST SESSION
# ============================================================
class TestSession(db.Model):
    __tablename__ = 'test_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    test_type = db.Column(db.String(50))
    difficulty = db.Column(db.String(20))
    bank_id = db.Column(db.Integer, db.ForeignKey('test_bank.id'), nullable=True)
    start_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_updated = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    current_question_index = db.Column(db.Integer, default=0)
    answers_so_far = db.Column(db.JSON, default={})
    test_data = db.Column(db.JSON)
    status = db.Column(db.String(20), default='in_progress')
    ended_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('test_sessions', lazy='dynamic'))
    bank_test = db.relationship('TestBank', backref=db.backref('sessions', lazy='dynamic'))

    def mark_completed(self, answers=None):
        self.status = 'completed'
        self.ended_at = datetime.now(timezone.utc)
        self.last_updated = datetime.now(timezone.utc)
        if answers is not None:
            self.answers_so_far = answers
        return self


# ============================================================
# IELTS USER TEST BANK USAGE
# ============================================================
class UserTestBankUsage(db.Model):
    __tablename__ = 'user_test_bank_usage'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bank_id = db.Column(db.Integer, db.ForeignKey('test_bank.id'), nullable=False)
    taken_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey('test_sessions.id'), nullable=True)

    # Legacy combined score (kept for backward compat)
    score = db.Column(db.Float, nullable=True)
    band_score = db.Column(db.Float, nullable=True)

    # Distinguish the user's real band from a model-essay band
    user_band_score = db.Column(db.Float, nullable=True)
    model_band_score = db.Column(db.Float, nullable=True)

    user = db.relationship('User', backref=db.backref('bank_usage', lazy='dynamic'))
    bank_test = db.relationship('TestBank', backref=db.backref('usage_records', lazy='dynamic'))
    session = db.relationship('TestSession', backref=db.backref('usage_record', uselist=False))

    def mark_completed(self, score=None, band_score=None, user_band_score=None, model_band_score=None):
        self.completed = True
        self.completed_at = datetime.now(timezone.utc)
        if score is not None:
            self.score = score
        if band_score is not None:
            self.band_score = band_score
        if user_band_score is not None:
            self.user_band_score = user_band_score
        if model_band_score is not None:
            self.model_band_score = model_band_score
        return self


# ============================================================
# IELTS TEST RESULT
# ============================================================
class TestResult(db.Model):
    __tablename__ = 'test_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    test_type = db.Column(db.String(50), nullable=False)
    score = db.Column(db.Float, nullable=False)
    band_score = db.Column(db.Float, nullable=False)
    answers = db.Column(db.Text)
    feedback = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Distinguish the user's real band from a model-essay band
    user_band_score = db.Column(db.Float, nullable=True)
    model_band_score = db.Column(db.Float, nullable=True)
    task1_band = db.Column(db.Float, nullable=True)
    task2_band = db.Column(db.Float, nullable=True)

    user = db.relationship('User', backref=db.backref('test_results', lazy='dynamic'))

    def get_answers(self):
        return json.loads(self.answers) if self.answers else {}

    def set_answers(self, answers_dict):
        self.answers = json.dumps(answers_dict)


# ============================================================
# SHARED MODELS (Not module-specific)
# ============================================================
class PendingPayment(db.Model):
    __tablename__ = 'pending_payments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    module = db.Column(db.String(20), nullable=False)  # 'ielts', 'pte', 'ukvi'
    amount = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100), unique=True, nullable=False)
    status = db.Column(db.String(20), default='pending')
    transaction_id = db.Column(db.String(100), nullable=True)
    screenshot = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=True)
    plan = db.Column(db.String(20), default='1month')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    verified_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_pending_status', 'status'),
        db.Index('idx_pending_module', 'module', 'status'),
    )

    @property
    def user(self):
        try:
            if self.user_id is None:
                return None
            User = get_user_model(self.module or 'ielts')
            if User is None:
                return None
            return db.session.get(User, int(self.user_id))
        except Exception:
            return None

    @property
    def username(self):
        u = self.user
        return u.username if u else f"User #{self.user_id}"

    @property
    def email(self):
        u = self.user
        return u.email if u else ""


class GenerationState(db.Model):
    __tablename__ = 'generation_states'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    module = db.Column(db.String(50), nullable=False)
    test_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    difficulty = db.Column(db.String(20), default='medium')
    accent = db.Column(db.String(20), default='british')
    total_sections = db.Column(db.Integer, default=4)
    generated_sections = db.Column(db.JSON, default=[])
    current_section = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='in_progress')
    test_data = db.Column(db.JSON)
    audio_urls = db.Column(db.JSON, default={})
    section_data = db.Column(db.JSON)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_generation_user_module', 'user_id', 'module', 'status'),
        db.Index('idx_generation_test_id', 'test_id'),
    )

    def is_complete(self):
        return self.status == 'completed' or len(self.generated_sections) >= self.total_sections

    def progress_percent(self):
        return int((len(self.generated_sections) / self.total_sections) * 100) if self.total_sections else 0

    def get_next_section(self):
        for i in range(1, self.total_sections + 1):
            if i not in self.generated_sections:
                return i
        return None

    def mark_section_complete(self, section_num, audio_url=None, section_info=None):
        if section_num not in self.generated_sections:
            self.generated_sections.append(section_num)
            self.generated_sections = sorted(self.generated_sections)
        if audio_url:
            self.audio_urls[str(section_num)] = audio_url
        if section_info:
            self.section_data = self.section_data or {}
            self.section_data[str(section_num)] = section_info
        self.current_section = section_num + 1
        self.updated_at = datetime.now(timezone.utc)
        if len(self.generated_sections) >= self.total_sections:
            self.status = 'completed'
            self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error_message):
        self.status = 'failed'
        self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'module': self.module,
            'test_id': self.test_id,
            'difficulty': self.difficulty,
            'accent': self.accent,
            'total_sections': self.total_sections,
            'generated_sections': self.generated_sections or [],
            'current_section': self.current_section,
            'status': self.status,
            'progress': self.progress_percent(),
            'audio_urls': self.audio_urls or {},
            'test_data': self.test_data,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


# ═══════════════════════════════════════════════════════════
# PAYMENT SETTINGS (Admin controlled — singleton)
# ═══════════════════════════════════════════════════════════
class PaymentSettings(db.Model):
    """
    Global payment settings — admin controls which gateways are active.

    Singleton row (only one exists). Use `PaymentSettings.get()` to fetch
    or auto-create it.

    Behavior:
        * manual_enabled  → shows "Bank Transfer" button + /manual-payment allowed
        * esewa_enabled   → shows "Pay via eSewa" button + auto-bill on success
        * khalti_enabled  → reserved (not implemented yet)
    """
    __tablename__ = 'payment_settings'

    id = db.Column(db.Integer, primary_key=True)
    manual_enabled = db.Column(db.Boolean, default=True, nullable=False)
    esewa_enabled = db.Column(db.Boolean, default=True, nullable=False)
    khalti_enabled = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_by = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'manual_enabled': self.manual_enabled,
            'esewa_enabled': self.esewa_enabled,
            'khalti_enabled': self.khalti_enabled,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'updated_by': self.updated_by,
        }

    @classmethod
    def get(cls):
        """
        Return the singleton settings row.
        Auto-creates one with sensible defaults on first call.
        """
        s = cls.query.first()
        if not s:
            s = cls(
                manual_enabled=True,
                esewa_enabled=True,
                khalti_enabled=False,
            )
            db.session.add(s)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                # In case of race condition, re-fetch
                s = cls.query.first()
        return s

    def __repr__(self):
        return (
            f"<PaymentSettings manual={self.manual_enabled} "
            f"esewa={self.esewa_enabled} khalti={self.khalti_enabled}>"
        )


# ═══════════════════════════════════════════════════════════
# BILLS (Auto-generated invoices — only for AUTOMATIC payments)
# ═══════════════════════════════════════════════════════════
class Bill(db.Model):
    """
    Auto-generated invoice/receipt for AUTOMATIC (eSewa) payments only.

    ⚠️ IMPORTANT:
        * Manual payments do NOT create Bill rows.
        * Only `_create_auto_bill()` in app.py creates these rows.
        * PDF is stored under protected_uploads/bills/.

    Bill number format: INV-YYYY-NNNN
        Example: INV-2026-0001, INV-2026-0002, ...
    """
    __tablename__ = 'bills'

    id = db.Column(db.Integer, primary_key=True)
    bill_number = db.Column(db.String(30), unique=True, nullable=False, index=True)

    # User info (denormalized for PDF — user may be deleted later)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    user_email = db.Column(db.String(255), nullable=True)
    user_name = db.Column(db.String(255), nullable=True)

    # Plan info
    module = db.Column(db.String(20), nullable=False, index=True)  # ielts/pte/ukvi
    plan = db.Column(db.String(20), nullable=False)                # 30days, etc.
    plan_label = db.Column(db.String(50), nullable=True)           # "30 Days"
    amount = db.Column(db.Integer, nullable=False)                 # NPR
    status = db.Column(db.String(20), default='paid', index=True)  # paid/pending/refunded

    # Payment info
    payment_method = db.Column(db.String(20), nullable=False)      # esewa
    transaction_id = db.Column(db.String(100), nullable=True, index=True)

    # Timestamps + PDF path
    issued_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    pdf_path = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.Index('idx_bill_user_issued', 'user_id', 'issued_at'),
        db.Index('idx_bill_module_status', 'module', 'status'),
    )

    def to_dict(self):
        """Serializable dict — passed to PDF generator."""
        return {
            'id': self.id,
            'bill_number': self.bill_number,
            'user_id': self.user_id,
            'user_email': self.user_email,
            'user_name': self.user_name,
            'module': self.module,
            'plan': self.plan,
            'plan_label': self.plan_label,
            'amount': self.amount,
            'status': self.status,
            'payment_method': self.payment_method,
            'transaction_id': self.transaction_id,
            'issued_at': self.issued_at,
            'pdf_path': self.pdf_path,
        }

    def __repr__(self):
        return f"<Bill {self.bill_number} user={self.user_id} NPR={self.amount} {self.status}>"


# ═══════════════════════════════════════════════════════════
# 🆕 PHASE 2C — COUPONS
# ═══════════════════════════════════════════════════════════
class Coupon(db.Model):
    """
    Discount coupon — admin-created.

    Supports:
      • Percent discounts  (e.g., 10% off)
      • Fixed NPR discounts (e.g., NPR 200 off)

    Safety features:
      • max_uses        — total usage limit (e.g., first 100 users)
      • per_user_limit  — per-user limit (e.g., 1 time per user)
      • min_amount      — minimum order amount required
      • max_discount    — cap on percent discount
      • expires_at      — expiry timestamp
      • applicable_modules / applicable_plans — scope limiting
    """
    __tablename__ = 'coupons'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)

    discount_type = db.Column(db.String(20), default='percent', nullable=False)
    discount_value = db.Column(db.Float, default=10.0, nullable=False)
    min_amount = db.Column(db.Integer, default=0)
    max_discount = db.Column(db.Integer, nullable=True)

    max_uses = db.Column(db.Integer, default=0)
    times_used = db.Column(db.Integer, default=0)
    per_user_limit = db.Column(db.Integer, default=1)

    applicable_modules = db.Column(db.String(100), default='all')
    applicable_plans = db.Column(db.String(255), default='all')

    starts_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def compute_discount(self, amount):
        """Return discount (NPR) for the given original amount."""
        try:
            amt = float(amount or 0)
        except (ValueError, TypeError):
            return 0
        if amt <= 0:
            return 0
        if self.min_amount and amt < int(self.min_amount):
            return 0

        if (self.discount_type or '').lower() == 'percent':
            disc = amt * (float(self.discount_value or 0) / 100.0)
        else:
            disc = float(self.discount_value or 0)

        if self.max_discount:
            disc = min(disc, float(self.max_discount))
        disc = min(disc, amt)
        return int(round(disc))

    def is_valid_now(self):
        now = datetime.now(timezone.utc)
        if not self.is_active:
            return False, 'Coupon is inactive'
        if self.starts_at and self.starts_at > now:
            return False, 'Coupon is not yet active'
        if self.expires_at and self.expires_at < now:
            return False, 'Coupon has expired'
        if self.max_uses and self.times_used >= self.max_uses:
            return False, 'Coupon usage limit reached'
        return True, None

    def module_allowed(self, module):
        s = (self.applicable_modules or 'all').lower().strip()
        if s == 'all' or not s:
            return True
        return module.lower() in [x.strip() for x in s.split(',')]

    def plan_allowed(self, plan_key):
        s = (self.applicable_plans or 'all').lower().strip()
        if s == 'all' or not s:
            return True
        return plan_key.lower() in [x.strip() for x in s.split(',')]

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'description': self.description,
            'discount_type': self.discount_type,
            'discount_value': self.discount_value,
            'min_amount': self.min_amount,
            'max_discount': self.max_discount,
            'max_uses': self.max_uses,
            'times_used': self.times_used,
            'per_user_limit': self.per_user_limit,
            'applicable_modules': self.applicable_modules,
            'applicable_plans': self.applicable_plans,
            'starts_at': self.starts_at.isoformat() if self.starts_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Coupon {self.code} {self.discount_type}={self.discount_value}>"


class CouponUsage(db.Model):
    """Tracks which user used which coupon (for per-user limit + history)."""
    __tablename__ = 'coupon_usages'

    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    module = db.Column(db.String(20), nullable=True)
    plan = db.Column(db.String(50), nullable=True)
    original_amount = db.Column(db.Integer, nullable=False)
    discount_amount = db.Column(db.Integer, nullable=False, default=0)
    final_amount = db.Column(db.Integer, nullable=False)
    txn_id = db.Column(db.String(100), nullable=True)
    used_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def to_dict(self):
        return {
            'id': self.id,
            'coupon_id': self.coupon_id,
            'user_id': self.user_id,
            'module': self.module,
            'plan': self.plan,
            'original_amount': self.original_amount,
            'discount_amount': self.discount_amount,
            'final_amount': self.final_amount,
            'txn_id': self.txn_id,
            'used_at': self.used_at.isoformat() if self.used_at else None,
        }

    def __repr__(self):
        return f"<CouponUsage coupon={self.coupon_id} user={self.user_id}>"


# ============================================================
# HELPER FUNCTION (IELTS only)
# ============================================================
def create_default_subscription_for_user(user_id, module='ielts'):
    """Create default subscription for a user (IELTS only)."""
    if module != 'ielts':
        return None
    sub = Subscription(
        user_id=user_id,
        plan='free',
        status='active',
        tests_remaining=3,
        free_tests_limit=3,
        free_tests_used=0,
        free_usage={},
        subscription_start=datetime.now(timezone.utc),
        subscription_end=datetime.now(timezone.utc) + timedelta(days=30)
    )
    db.session.add(sub)
    db.session.commit()
    return sub


# ============================================================
# MODEL GETTER FUNCTIONS (IELTS + PTE + UKVI)
# ============================================================

def get_user_model(module=None):
    if module == 'ielts':
        from models import User
        return User
    elif module == 'pte':
        from modules.pte.models import PTEUser
        return PTEUser
    elif module == 'ukvi':
        from modules.ukvi.models import UKVIUser
        return UKVIUser
    else:
        from models import User
        return User


def get_subscription_model(module=None):
    if module == 'ielts':
        from models import Subscription
        return Subscription
    elif module == 'pte':
        from modules.pte.models import PTESubscription
        return PTESubscription
    elif module == 'ukvi':
        from modules.ukvi.models import UKVISubscription
        return UKVISubscription
    else:
        from models import Subscription
        return Subscription


def get_test_bank_model(module=None):
    if module == 'ielts':
        from models import TestBank
        return TestBank
    elif module == 'pte':
        from modules.pte.models import PTETestBank
        return PTETestBank
    elif module == 'ukvi':
        from modules.ukvi.models import UKVITestBank
        return UKVITestBank
    else:
        from models import TestBank
        return TestBank


def get_test_session_model(module=None):
    if module == 'ielts':
        from models import TestSession
        return TestSession
    elif module == 'pte':
        from modules.pte.models import PTETestSession
        return PTETestSession
    elif module == 'ukvi':
        from modules.ukvi.models import UKVITestSession
        return UKVITestSession
    else:
        from models import TestSession
        return TestSession


def get_test_result_model(module=None):
    if module == 'ielts':
        from models import TestResult
        return TestResult
    elif module == 'pte':
        from modules.pte.models import PTETestResult
        return PTETestResult
    elif module == 'ukvi':
        from modules.ukvi.models import UKVITestResult
        return UKVITestResult
    else:
        from models import TestResult
        return TestResult


def get_test_bank_usage_model(module=None):
    if module == 'ielts':
        from models import UserTestBankUsage
        return UserTestBankUsage
    else:
        from models import UserTestBankUsage
        return UserTestBankUsage


def get_full_test_variant_model(module=None):
    """
    Return the FullTestVariant model class.

    Currently only IELTS has full-test variants. For PTE/UKVI we return
    None so callers can handle gracefully.
    """
    if module in (None, 'ielts'):
        from models import FullTestVariant
        return FullTestVariant
    return None


# ═══════════════════════════════════════════════════════════════════════
# IELTS TEST POOL — PTE-style shared pool
# ═══════════════════════════════════════════════════════════════════════
class IELTSTestPool(db.Model):
    """Shared pool of pre-generated IELTS tests (100 per module)."""
    __tablename__ = 'ielts_test_pool'

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
        db.Index('idx_ielts_pool_module_usage', 'module', 'usage_count'),
        db.Index('idx_ielts_pool_module_active', 'module', 'is_active'),
    )

    def __repr__(self):
        return f"<IELTSTestPool {self.module} #{self.id} usage={self.usage_count}>"


class IELTSGenerationLock(db.Model):
    """Per-module generation lock (thundering-herd safe)."""
    __tablename__ = 'ielts_generation_lock'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(50), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), default='idle')
    locked_by = db.Column(db.Integer, nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
    error = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<IELTSGenerationLock {self.module} status={self.status} by={self.locked_by}>"


class IELTSUserPoolProgress(db.Model):
    """Which pool tests has each user completed?"""
    __tablename__ = 'ielts_user_pool_progress'

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
            name='uq_ielts_user_module_pool',
        ),
        db.Index('idx_ielts_user_module', 'user_id', 'module'),
    )

    def __repr__(self):
        return f"<IELTSUserPoolProgress user={self.user_id} {self.module} pool={self.pool_id}>"

# ═══════════════════════════════════════════════════════════
# PUSH NOTIFICATION SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════
class PushSubscription(db.Model):
    """Store browser push subscription endpoints."""
    __tablename__ = 'push_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    module = db.Column(db.String(20), default='ielts', index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: __import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    last_used_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'module': self.module,
            'endpoint': self.endpoint[:80] + '...' if self.endpoint else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
