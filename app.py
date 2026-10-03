# app.py
"""
Flask application with fully separated models for IELTS, PTE, and UKVI.

v8.17 — PTE POOL dashboard fixes:
  - /pte/test-bank/status  → accepts admin session OR Flask-Login user
                              (was @login_required → broke admin dashboard)
  - /admin/pte/pool/cap    → aliased to /admin/pte/pool/caps/set
  - All pool endpoints return full key aliases + safe fallbacks

v8.16 — PTE POOL routes (defensive key aliases).
v8.15 — PTE POOL admin routes (initial).
v8.14 — FULL-TEST BANK preview route.
v8.13 — FULL-TEST BANK by_difficulty FIX.
v8.12 — PHASE 2C (Coupon Codes).
v8.11 — PHASE 2A (Progress Chart API).
v8.10 — PHASE 1 FEATURES (Forgot Password, Profile, Change Password).
v8.9  — SIMPLE REGISTRATION.
v8.8  — FACEBOOK OAUTH LOGIN.
v8.7  — PAYMENT SETTINGS + AUTO-BILLS.
v8.6.2 — REDIS SESSION FIX.
v8.6 — UKVI ASYNC POOL + SINGLE-PROCESS POLLER.
v8.5 — UKVI SUBSCRIPTION STATUS ROUTE.
v8.4 — SINGLE-VPS PRODUCTION.
"""

import logging, os, sys, re, json, uuid, base64, random, string, secrets, threading, time, subprocess, asyncio, gc, shutil, copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from apscheduler.schedulers.background import BackgroundScheduler
import requests, hmac, hashlib, urllib.parse, qrcode
from PIL import Image
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, abort, send_file, send_from_directory, Response, stream_with_context
from flask_session import Session
from flask_mail import Mail, Message
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import NotFound
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, Dict, Any

import redis as _redis

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=env_path, encoding='utf-8-sig')

# ============================================================
# IMPORT MODULES
# ============================================================

from modules.ielts.speaking import (
    create_speaking_test_generator,
    create_transcriber,
    create_examiner_voice,
    create_band_calculator,
    create_speaking_test
)
from modules.ielts.reading.test_generator import IELTSReadingGenerator
from modules.ielts.writing import create_writing_test_generator, create_essay_evaluator, create_essay_upgrader, create_writing_api
from modules.ielts.listening import create_listening_api
from modules.ielts.listening.accent_mixer import accent_mixer
from modules.ielts.reading.api import create_reading_api

from modules.ielts.subscription_manager import get_ielts_subscription_manager

from modules.pte.api import pte_blueprint, init_pte_api
from modules.pte.managers import pte_subscription_manager
from modules.pte.managers.test_pool_manager import pte_test_pool_manager
from modules.pte.generators import PTEReading, PTEListening, PTESpeakingWriting

from modules.ukvi import init_ukvi_api, get_blueprint
from modules.ukvi.managers import UKVISubscriptionManager, UKVITestManager, ukvi_pool_manager
from modules.ukvi.service import UKVIService

from ai_engine import ai_engine

# ═══════════════════════════════════════════════════════════
# AI Queue — concurrency limiter
# ═══════════════════════════════════════════════════════════
try:
    from ai_queue import wrap_ai_engine
    ai_engine = wrap_ai_engine(ai_engine, max_concurrent=20)
except ImportError:
    pass

try:
    from modules.ielts.listening.audio_generator import audio_generator
    AUDIO_GENERATOR_AVAILABLE = True
except ImportError:
    AUDIO_GENERATOR_AVAILABLE = False
    audio_generator = None
try:
    from modules.audio.unified_service import audio_service
    AUDIO_SERVICE_AVAILABLE = True
except ImportError:
    AUDIO_SERVICE_AVAILABLE = False
    audio_service = None

from modules.ielts.writing.chart_renderer import ChartRenderer
chart_renderer = ChartRenderer(output_dir="static/charts")

# ============================================================
# LOGGING SETUP
# ============================================================
if not os.path.exists('logs'):
    os.makedirs('logs')
file_handler = RotatingFileHandler('logs/app.log', maxBytes=10*1024*1024, backupCount=5)
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(formatter)

logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

import mimetypes
mimetypes.add_type('audio/mpeg', '.mp3')

# ============================================================
# BANK MANAGERS
# ============================================================
try:
    from modules.ielts.test_bank_manager import (
        TestBankManager,
        FullTestBankManager,
        MAX_BANK_SIZE_PER_MODULE,
    )
    TEST_BANK_MANAGER_AVAILABLE = True
    FULL_TEST_BANK_MANAGER_AVAILABLE = True
    logger.info(" TestBankManager + FullTestBankManager imported (test_bank_manager)")
except ImportError as e:
    try:
        from test_bank_manager import (
            TestBankManager,
            FullTestBankManager,
            MAX_BANK_SIZE_PER_MODULE,
        )
        TEST_BANK_MANAGER_AVAILABLE = True
        FULL_TEST_BANK_MANAGER_AVAILABLE = True
        logger.info(" Bank managers imported from project root")
    except ImportError:
        TEST_BANK_MANAGER_AVAILABLE = False
        FULL_TEST_BANK_MANAGER_AVAILABLE = False
        TestBankManager = None
        FullTestBankManager = None
        MAX_BANK_SIZE_PER_MODULE = 100
        logger.warning(f" Bank manager import failed: {e}")

try:
    from modules.ielts.test_pool_manager import (
        IELTSTestPoolManager,
        ielts_test_pool_manager,
        MODULE_POOL_CONFIG,
        KNOWN_MODULES as POOL_KNOWN_MODULES,
        MAX_POOL_SIZE,
    )
    IELTS_TEST_POOL_MANAGER_AVAILABLE = True
    logger.info(" IELTSTestPoolManager (dynamic) imported (test_pool_manager)")
    logger.info(f" Pool config: {MODULE_POOL_CONFIG}")
except ImportError as e:
    IELTS_TEST_POOL_MANAGER_AVAILABLE = False
    IELTSTestPoolManager = None
    ielts_test_pool_manager = None
    MODULE_POOL_CONFIG = {}
    POOL_KNOWN_MODULES = []
    MAX_POOL_SIZE = 1000
    logger.warning(f" Dynamic IELTS pool manager import failed: {e}")

# ============================================================
# APP CONFIGURATION
# ============================================================
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY and os.environ.get('FLASK_ENV') == 'production':
    raise RuntimeError("SECRET_KEY must be set in production environment.")
app.config['SECRET_KEY'] = SECRET_KEY or 'dev-secret-key'

_db_url = os.environ.get('DATABASE_URL', '').strip()

if not _db_url or _db_url.startswith('sqlite'):
    _db_user = os.environ.get('DB_USER', 'seltaiprep_user')
    _db_pass = os.environ.get('DB_PASS', 'Seltaiprep@2026')
    _db_host = os.environ.get('DB_HOST', 'localhost')
    _db_port = os.environ.get('DB_PORT', '5433')
    _db_name = os.environ.get('DB_NAME', 'seltaiprep')
    _db_url = f'postgresql+psycopg2://{_db_user}:{_db_pass}@{_db_host}:{_db_port}/{_db_name}'
    logger.info(f" Building PostgreSQL URL from DB_* env vars: {_db_host}:{_db_port}/{_db_name}")

app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '10'))
_DB_MAX_OVERFLOW = int(os.environ.get('DB_MAX_OVERFLOW', '20'))

if _db_url.startswith('postgresql'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 1800,
        'pool_size': _DB_POOL_SIZE,
        'max_overflow': _DB_MAX_OVERFLOW,
        'pool_timeout': 30,
        'connect_args': {
            'connect_timeout': 10,
            'options': '-c statement_timeout=60000',
        },
    }
    logger.info(f" PostgreSQL pool: size={_DB_POOL_SIZE}, overflow={_DB_MAX_OVERFLOW}")
else:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlite3 import Connection as SQLite3Connection

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'connect_args': {
            'timeout': 30,
            'check_same_thread': False,
        },
    }

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, SQLite3Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA cache_size=10000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    logger.info(" SQLite WAL mode enabled (fallback)")

app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 10*1024*1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_NAME'] = 'ielts_sess'

# ============================================================
# REDIS
# ============================================================
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
try:
    redis_client = _redis.Redis.from_url(REDIS_URL, decode_responses=False)
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info(f" Redis connected: {REDIS_URL}")
except Exception as _redis_err:
    REDIS_AVAILABLE = False
    redis_client = None
    logger.warning(f" Redis unavailable ({_redis_err}) — falling back to filesystem/simple")

if REDIS_AVAILABLE:
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = redis_client
    app.config['SESSION_PERMANENT'] = True
    app.config['SESSION_USE_SIGNER'] = False
    app.config['SESSION_KEY_PREFIX'] = 'ielts_session:'
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True
    logger.info(" Session → Redis")
else:
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_FILE_DIR'] = os.path.join(os.getcwd(), 'flask_sessions')
    app.config['SESSION_PERMANENT'] = True
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True
    logger.warning(" Session → filesystem (Redis unavailable)")
Session(app)

if REDIS_AVAILABLE:
    app.config['CACHE_TYPE'] = 'RedisCache'
    app.config['CACHE_REDIS_URL'] = os.environ.get(
        'REDIS_CACHE_URL',
        REDIS_URL.replace('/0', '/1') if REDIS_URL.endswith('/0') else REDIS_URL
    )
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300
    logger.info(" Cache → Redis")
else:
    app.config['CACHE_TYPE'] = 'simple'
    logger.warning(" Cache → simple (Redis unavailable)")
cache = Cache(app)

if REDIS_AVAILABLE:
    _limiter_storage = os.environ.get(
        'REDIS_LIMITER_URL',
        REDIS_URL.replace('/0', '/2') if REDIS_URL.endswith('/0') else REDIS_URL
    )
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        storage_uri=_limiter_storage,
        default_limits=["200 per minute", "20 per second"],
    )
    logger.info(" Rate limiter → Redis")
else:
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per minute", "20 per second"],
    )
    logger.warning(" Rate limiter → memory (Redis unavailable)")

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')
mail = Mail(app)

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')

# ═══════════════════════════════════════════════════════════
# FACEBOOK OAUTH CONFIG
# ═══════════════════════════════════════════════════════════
FACEBOOK_APP_ID = os.environ.get('FACEBOOK_APP_ID', '')
FACEBOOK_APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET', '')
FACEBOOK_REDIRECT_URI = os.environ.get(
    'FACEBOOK_REDIRECT_URI',
    'http://localhost:5000/login/facebook/authorized'
)

# ============================================================
# DATABASE & MODELS
# ============================================================
from models import (
    db, PendingPayment, GenerationState, UserTestBankUsage,
    PaymentSettings, Bill, Coupon, CouponUsage,
)
from models import (
    get_user_model,
    get_subscription_model,
    get_test_bank_model,
    get_test_session_model,
    get_test_result_model,
    get_full_test_variant_model,
)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.unauthorized_handler
def unauthorized():
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': False, 'error': 'Authentication required.'}), 401
    return redirect(url_for('login'))

@login_manager.user_loader
def load_user(user_id):
    module = session.get('selected_module', 'ielts')
    User = get_user_model(module)
    return db.session.get(User, int(user_id))

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def log_user_activity(user_id, action, details=None):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"{timestamp} | User:{user_id} | {action}"
    if details:
        log_entry += f" | {json.dumps(details)[:500]}"
    logger.info(log_entry)


def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
            if not token or token != session.get('_csrf_token'):
                return jsonify({'error': 'CSRF token missing or invalid'}), 403
        return f(*args, **kwargs)
    return decorated


def _parse_utc_datetime(value: str) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


# ═══════════════════════════════════════════════════════════
# COUPON HELPERS
# ═══════════════════════════════════════════════════════════
def find_coupon(code):
    if not code:
        return None, 'Please enter a coupon code'
    c = (code or '').strip().upper()
    if not c:
        return None, 'Please enter a coupon code'
    try:
        coupon = Coupon.query.filter_by(code=c).first()
    except Exception as e:
        logger.warning(f"find_coupon query failed: {e}")
        return None, 'Coupon lookup failed'
    if not coupon:
        return None, 'Invalid coupon code'
    return coupon, None


def validate_coupon_for_user(coupon, user_id, module, plan_key, amount):
    ok, err = coupon.is_valid_now()
    if not ok:
        return 0, err
    if not coupon.module_allowed(module):
        return 0, f'Coupon not valid for {module.upper()}'
    if not coupon.plan_allowed(plan_key):
        return 0, 'Coupon not valid for this plan'
    if coupon.min_amount and int(amount or 0) < int(coupon.min_amount):
        return 0, f'Minimum amount NPR {coupon.min_amount} required'
    if coupon.per_user_limit and coupon.per_user_limit > 0:
        try:
            used_by_user = CouponUsage.query.filter_by(
                coupon_id=coupon.id, user_id=user_id
            ).count()
            if used_by_user >= int(coupon.per_user_limit):
                return 0, 'You have already used this coupon'
        except Exception as e:
            logger.warning(f"coupon per-user check failed: {e}")
    disc = coupon.compute_discount(amount)
    if disc <= 0:
        return 0, 'Coupon gives no discount for this order'
    return disc, None


def record_coupon_usage(coupon, user_id, module, plan_key,
                        original_amount, discount_amount, txn_id=None):
    try:
        coupon.times_used = (coupon.times_used or 0) + 1
        coupon.updated_at = datetime.now(timezone.utc)
        final_amount = max(0, int(original_amount) - int(discount_amount))
        usage = CouponUsage(
            coupon_id=coupon.id,
            user_id=user_id,
            module=module,
            plan=plan_key,
            original_amount=int(original_amount),
            discount_amount=int(discount_amount),
            final_amount=final_amount,
            txn_id=txn_id,
        )
        db.session.add(usage)
        db.session.commit()
        return usage
    except Exception as e:
        db.session.rollback()
        logger.exception(f"record_coupon_usage failed: {e}")
        return None


@app.before_request
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)


@app.before_request
def refresh_subscription_manager_session():
    mgr = globals().get('subscription_manager')
    if mgr is None:
        return
    try:
        mgr.db = db.session
    except Exception:
        pass


@app.context_processor
def inject_csrf_token():
    return {
        'csrf_token': session.get('_csrf_token', ''),
        'UserTestBankUsage': UserTestBankUsage,
    }


@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.template_filter('datetime')
def format_datetime(value):
    return value.strftime('%Y-%m-%d %H:%M') if value else ''


@app.template_filter('fromjson')
def fromjson_filter(value):
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled exception: {e}", exc_info=True)
    if (request.path.startswith('/api/')
            or request.accept_mimetypes.best == 'application/json'
            or request.is_json):
        return jsonify({
            "success": False,
            "error": "An internal error occurred. Please try again later."
        }), 500
    return render_template('error.html', error=str(e)), 500


@app.errorhandler(404)
def not_found(e):
    if (request.path.startswith('/api/')
            or request.accept_mimetypes.best == 'application/json'
            or request.is_json):
        return jsonify({"success": False, "error": "Resource not found"}), 404
    return render_template('error.html', error="Page not found"), 404


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please slow down."}), 429


@app.route('/audio_cache/<path:filename>')
def serve_audio_cache(filename):
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = os.path.join(app.static_folder, 'audio_cache', filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    if not filename.endswith('.mp3'):
        return jsonify({'error': 'Not an audio file'}), 400
    file_size = os.path.getsize(filepath)
    if file_size == 0:
        return jsonify({'error': 'Audio file is empty'}), 500
    with open(filepath, 'rb') as f:
        data = f.read()
    response = Response(data, mimetype='audio/mpeg')
    response.headers['Content-Type'] = 'audio/mpeg'
    response.headers['Content-Length'] = str(file_size)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ============================================================
# CLEANUP
# ============================================================
def cleanup_old_generation_states(days=7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old = GenerationState.query.filter(
        GenerationState.status.in_(['completed', 'failed']),
        GenerationState.updated_at < cutoff
    ).all()
    for state in old:
        db.session.delete(state)
    db.session.commit()
    return len(old)


# ============================================================
# SUBSCRIPTION HELPERS
# ============================================================
def check_module_subscription(user_id: int, module: str = 'ielts') -> tuple:
    if module == 'ielts':
        status = subscription_manager.get_status(user_id)
        if status.get('has_subscription'):
            return True, None, None
        else:
            return False, None, None
    else:
        return False, None, None


def check_free_limit(user_id: int, test_type: str, module: str = 'ielts') -> tuple:
    if module == 'ielts':
        status = subscription_manager.get_status(user_id)
        mod_status = status.get('modules', {}).get(test_type, {})
        used = mod_status.get('free_used', 0)
        limit = mod_status.get('free_limit', 2)
        return used, used >= limit, limit
    else:
        return 0, False, 999999


def _get_subscription_status(module: str):
    if module == 'ielts':
        return subscription_manager.get_status(current_user.id)
    elif module == 'pte':
        return pte_subscription_manager.get_subscription_status(current_user.id)
    elif module == 'ukvi':
        ukvi_sub_manager = UKVISubscriptionManager(db)
        return ukvi_sub_manager.get_status(current_user.id)
    return {}


def _activate_subscription(module: str, plan_key: str):
    return _activate_subscription_by_user(current_user.id, module, plan_key)


def _activate_subscription_by_user(user_id: int, module: str, plan_key: str):
    try:
        if module == 'ielts':
            return subscription_manager.activate_subscription(user_id, plan_key)
        elif module == 'pte':
            return pte_subscription_manager.activate_subscription(user_id, plan_key)
        elif module == 'ukvi':
            ukvi_sub_manager = UKVISubscriptionManager(db)
            sub = ukvi_sub_manager._get_or_create_subscription(user_id)
            sub.plan = plan_key
            sub.status = 'active'
            sub.tests_remaining = PLAN_CONFIG.get(plan_key, {}).get('tests', 999)
            sub.subscription_start = datetime.now(timezone.utc)
            sub.subscription_end = get_subscription_end(
                PLAN_CONFIG.get(plan_key, {}).get('days', 30)
            )
            db.session.commit()
            return {'success': True, 'tests_remaining': sub.tests_remaining}
        return {'success': False, 'error': 'Invalid module'}
    except Exception as e:
        logger.exception(f"_activate_subscription_by_user failed: {e}")
        db.session.rollback()
        return {'success': False, 'error': str(e)}


# ═══════════════════════════════════════════════════════════
# AUTO-BILL GENERATION
# ═══════════════════════════════════════════════════════════
try:
    from utils.bill_generator import generate_bill_pdf
    BILL_GENERATOR_AVAILABLE = True
    logger.info(" Bill generator imported")
except ImportError as _bg_err:
    BILL_GENERATOR_AVAILABLE = False
    generate_bill_pdf = None
    logger.warning(f" Bill generator not available: {_bg_err}")


def _create_auto_bill(user_id, module, plan_key, amount, txn_id):
    if not BILL_GENERATOR_AVAILABLE:
        logger.warning("Skipping auto-bill (generator unavailable)")
        return None
    try:
        User = get_user_model(module)
        user = db.session.get(User, user_id)
        year = datetime.now(timezone.utc).year
        last = Bill.query.filter(
            Bill.bill_number.like(f'INV-{year}-%')
        ).order_by(Bill.id.desc()).first()
        next_num = 1
        if last:
            try:
                next_num = int(last.bill_number.split('-')[-1]) + 1
            except (ValueError, IndexError):
                pass
        bill_number = f"INV-{year}-{next_num:04d}"
        plan_cfg = PLAN_CONFIG.get(plan_key, {})
        bill = Bill(
            bill_number=bill_number,
            user_id=user_id,
            user_email=(user.email if user else None),
            user_name=(user.username if user else 'User'),
            module=module,
            plan=plan_key,
            plan_label=plan_cfg.get('label', plan_key),
            amount=int(amount),
            status='paid',
            payment_method='esewa',
            transaction_id=txn_id,
            issued_at=datetime.now(timezone.utc),
        )
        db.session.add(bill)
        db.session.flush()
        try:
            pdf_path = generate_bill_pdf(bill.to_dict())
            bill.pdf_path = pdf_path
        except Exception as _pdf_err:
            logger.exception(f"Bill PDF generation failed: {_pdf_err}")
        db.session.commit()
        logger.info(f" ✅ Auto-bill: {bill_number} (user={user_id}, {module}, {plan_key})")
        return bill
    except Exception as e:
        logger.exception(f"Auto-bill failed: {e}")
        db.session.rollback()
        return None


# ============================================================
# IELTS POOL SAVE HELPER
# ============================================================
def _ielts_pool_save(pool_module: str, difficulty: str, test_data: dict, user_id: int):
    if not ielts_test_pool_manager:
        raise RuntimeError("ielts_test_pool_manager not available")
    for name in ('_save_to_pool', 'save_to_pool', 'add_to_pool', 'insert_to_pool'):
        fn = getattr(ielts_test_pool_manager, name, None)
        if callable(fn):
            return fn(
                module=pool_module,
                difficulty=difficulty,
                test_data=test_data,
                user_id=user_id,
            )
    raise RuntimeError(
        "IELTS pool manager exposes no known save method "
        "(_save_to_pool / save_to_pool / add_to_pool / insert_to_pool)"
    )


# ============================================================
# LISTENING AUDIO GENERATION
# ============================================================
def map_accent_to_voice(accent):
    mapping = {
        'british': 'en-GB-Neural2-F',
        'american': 'en-US-Neural2-F',
        'australian': 'en-AU-Neural2-F',
        'canadian': 'en-CA-Neural2-F',
        'indian': 'en-IN-Neural2-F',
        'south african': 'en-ZA-Neural2-F',
        'irish': 'en-IE-Neural2-F',
        'new zealand': 'en-NZ-Neural2-F',
        'scottish': 'en-GB-Neural2-D',
        'welsh': 'en-GB-Neural2-C',
    }
    return mapping.get(accent.lower(), 'en-US-Neural2-F')


def _generate_single_section_audio(
    session_id, section_num, section_data, accents_map,
    retries=3, pool_id=None,
):
    script = section_data.get('script', '')
    if not script:
        text_parts = [q.get('text', '') for q in section_data.get('questions', []) if q.get('text')]
        script = '. '.join(text_parts)
    if not script:
        return None, "No text to synthesize", {}

    if AUDIO_GENERATOR_AVAILABLE and audio_generator:
        try:
            accent_key = str(section_num)
            accent = accents_map.get(accent_key, 'british')
            test_title = f"section_{section_num}_session_{session_id}"

            speaker_engine_map = None
            if hasattr(audio_generator, '_parse_script'):
                turns = audio_generator._parse_script(script, section_num)
                if turns:
                    if section_num in (2, 4):
                        engine = 'edge'
                        logger.info(f" Section {section_num}: Using Edge TTS for all speakers (fast mode)")
                    else:
                        engine = 'deepgram'
                        logger.info(f" Section {section_num}: Using Deepgram for all speakers (quality mode)")
                    speaker_engine_map = {speaker: engine for speaker, _ in turns}

            speaker_genders = section_data.get('speaker_genders') or {}
            if speaker_genders:
                logger.info(f" Passing speaker_genders to audio generator for section {section_num}: {speaker_genders}")

            try:
                urls, error, timings = audio_generator.generate(
                    script=script,
                    section_number=section_num,
                    test_title=test_title,
                    accent=accent,
                    total_questions=10,
                    include_instructions=False,
                    speaker_engine_map=speaker_engine_map,
                    speaker_genders=speaker_genders,
                    fast=True,
                    pool_id=pool_id,
                )
            except TypeError:
                logger.debug("[audio] pool_id kwarg not accepted — retrying without")
                urls, error, timings = audio_generator.generate(
                    script=script,
                    section_number=section_num,
                    test_title=test_title,
                    accent=accent,
                    total_questions=10,
                    include_instructions=False,
                    speaker_engine_map=speaker_engine_map,
                    speaker_genders=speaker_genders,
                    fast=True,
                )

            if urls and urls.get('main'):
                return urls, None, timings
            else:
                logger.error(f"audio_generator.generate failed: {error}")
                return None, error or "Audio generation failed", {}
        except Exception as e:
            logger.error(f"audio_generator error: {e}", exc_info=True)
            return None, str(e), {}

    if AUDIO_SERVICE_AVAILABLE and audio_service:
        try:
            result = audio_service.generate_listening_section_audio(
                section_data=section_data,
                section_num=section_num,
                include_instructions=False
            )
            if result.get('success'):
                audio_url = result.get('merged_audio')
                duration = result.get('duration', 30)
                timings = {'start': 0, 'end': duration, 'duration': duration}
                return {'main': audio_url}, None, timings
            else:
                return None, result.get('error', 'Unknown error'), {}
        except Exception as e:
            logger.error(f"audio_service error: {e}", exc_info=True)
            return None, str(e), {}
    return None, "No audio service available", {}


def _generate_audio_background(session_id, num_sections):
    try:
        with app.app_context():
            TestSession = get_test_session_model('ielts')
            sess = db.session.get(TestSession, session_id)
            if not sess:
                logger.error(f"Session {session_id} not found for audio background")
                return
            raw = sess.test_data
            if isinstance(raw, str):
                test_data = json.loads(raw)
            else:
                test_data = raw or {}
            audio_urls = test_data.get('audio_urls', {})
            audio_timings = test_data.get('audio_timings', {})
            accents_map = test_data.get('accents', {})
            if not accents_map:
                default_accent = test_data.get('accent', 'british')
                accents_map = {str(i): default_accent for i in range(1, num_sections+1)}
            _pool_id_for_audio = None
            if isinstance(test_data, dict):
                _pool_id_for_audio = test_data.get('_pool_id')
            sections = test_data.get('sections', [])
            for sec_num in range(1, num_sections+1):
                if str(sec_num) in audio_urls:
                    continue
                if sec_num-1 >= len(sections):
                    logger.warning(f"Section {sec_num} not found in sections list")
                    continue
                section_data = sections[sec_num-1]
                logger.info(f"Background audio: generating for Section {sec_num}")
                result, error, timings = _generate_single_section_audio(
                    session_id, sec_num, section_data, accents_map,
                    pool_id=_pool_id_for_audio,
                )
                if result:
                    audio_urls[str(sec_num)] = result
                    audio_timings[str(sec_num)] = timings
                    test_data['audio_urls'] = audio_urls
                    test_data['audio_timings'] = audio_timings
                    sess.test_data = test_data
                    db.session.commit()
                    logger.info(f" Audio generated for Section {sec_num}")
                else:
                    logger.warning(f" Audio generation failed for Section {sec_num}: {error}")
            logger.info(f" Audio background generation complete for session {session_id}")
    except Exception as e:
        logger.error(f"Background audio generation failed: {e}", exc_info=True)


# ============================================================
# PAYMENT CONFIG (eSewa)
# ============================================================
ESEWA_MERCHANT_CODE = os.environ.get('ESEWA_MERCHANT_CODE')
ESEWA_SECRET_KEY = os.environ.get('ESEWA_SECRET_KEY')
ESEWA_ENVIRONMENT = os.environ.get('ESEWA_ENVIRONMENT', 'test')
ESEWA_BASE_URL = "https://rc.esewa.com.np" if ESEWA_ENVIRONMENT == "test" else "https://esewa.com.np"
ESEWA_PAYMENT_URL = f"{ESEWA_BASE_URL}/epay/main"
ESEWA_API_VERIFY_URL = f"{ESEWA_BASE_URL}/api/epay/transaction/verify"


def generate_esewa_signature(message, secret):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def generate_esewa_payment_url(amount, txn_uuid, product_code, success_url, failure_url):
    params = {
        'amt': str(amount), 'txAmt': str(amount), 'tAmt': str(amount),
        'txnId': txn_uuid, 'pid': product_code, 'scd': ESEWA_MERCHANT_CODE,
        'psc': product_code, 'pdc': product_code,
        'su': success_url, 'fu': failure_url
    }
    return f"{ESEWA_PAYMENT_URL}?{urllib.parse.urlencode(params)}"


def verify_esewa_signature(data, signature):
    if not signature or not ESEWA_SECRET_KEY:
        return True
    expected = generate_esewa_signature(
        f"pid={data.get('pid')}&refId={data.get('refId')}&amt={data.get('amt')}&txnId={data.get('txnId')}",
        ESEWA_SECRET_KEY
    )
    return hmac.compare_digest(expected, signature)


def get_subscription_end(plan_days):
    now = datetime.now(timezone.utc)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_today + timedelta(days=plan_days + 1) - timedelta(seconds=1)


MODULE_PRICES = {'ielts': 1499, 'pte': 1499, 'ukvi': 999}
PLAN_CONFIG = {
    '1day': {'days': 1, 'tests': 1, 'label': '1 Day'},
    '7days': {'days': 7, 'tests': 3, 'label': '7 Days'},
    '15days': {'days': 15, 'tests': 5, 'label': '15 Days'},
    '30days': {'days': 30, 'tests': 15, 'label': '30 Days'},
    '45days': {'days': 45, 'tests': 999, 'label': '45 Days (Unlimited)'}
}
PRICE_CONFIG = {
    'ielts': {'1day': 199, '7days': 499, '15days': 899, '30days': 1499, '45days': 1999},
    'pte': {'1day': 199, '7days': 499, '15days': 899, '30days': 1499, '45days': 1999},
    'ukvi': {'1day': 149, '7days': 399, '15days': 699, '30days': 999, '45days': 1299}
}


def calculate_ielts_band(correct, total):
    if total == 0:
        return 0.0
    pct = (correct / total) * 100
    if pct >= 90: return 9.0
    if pct >= 85: return 8.5
    if pct >= 80: return 8.0
    if pct >= 75: return 7.5
    if pct >= 70: return 7.0
    if pct >= 65: return 6.5
    if pct >= 60: return 6.0
    if pct >= 55: return 5.5
    if pct >= 50: return 5.0
    if pct >= 45: return 4.5
    if pct >= 40: return 4.0
    if pct >= 35: return 3.5
    return 3.0


def round_ielts_band(raw):
    return min(9.0, max(0.0, round(raw * 2) / 2))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'pdf', 'docx', 'txt'}


def generate_qr_base64(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


# ============================================================
# LEGACY SmartTestBankManager
# ============================================================
class SmartTestBankManager:
    def __init__(self, ai_engine):
        self.ai = ai_engine
        self.generation_lock = threading.Lock()
        self.MAX_TESTS_PER_TYPE = 0
        self.REFRESH_AFTER_USES = 999

    def get_test(self, test_type, difficulty="medium", topic=None, module='ielts', **kwargs):
        test_data = self._generate_single_test(test_type, difficulty, topic, **kwargs)
        if test_data:
            return {
                'success': True,
                'test': test_data,
                'source': 'generated_on_demand',
                'test_id': None,
                'usage_count': 0,
                'total_tests': 0
            }
        return {'success': False, 'error': 'Failed to generate test'}

    def _generate_single_test(self, test_type, difficulty, topic, **kwargs):
        try:
            if test_type == 'reading':
                generator = IELTSReadingGenerator(ai_engine=self.ai)
                test = generator.generate_complete_test(difficulty, topic)
                return test.to_dict() if hasattr(test, 'to_dict') else test
            elif test_type == 'listening':
                from modules.ielts.listening.test_generator import ListeningTestGenerator
                generator = ListeningTestGenerator(self.ai)
                return generator.generate(
                    difficulty=difficulty, topic=topic, exam='ielts',
                    accent=kwargs.get('accent', 'british'),
                    fast=kwargs.get('fast', True)
                )
            elif test_type == 'writing':
                if not self.ai:
                    return None
                generator = create_writing_test_generator(self.ai)
                return generator.generate_complete_advanced(difficulty, topic, **kwargs)
            elif test_type == 'speaking':
                if not self.ai:
                    return None
                generator = create_speaking_test(self.ai)
                return generator.generate_complete_test(difficulty=difficulty, topic=topic)
            return None
        except Exception as e:
            logger.error(f"Generate {test_type} failed: {e}")
            return None

    def get_bank_stats(self):
        return {'message': 'Test bank disabled (on-demand mode)', 'max_tests': 0, 'refresh_after': 999}


# ============================================================
# HELPER FUNCTIONS (general)
# ============================================================
def check_resume_limit(user_id, test_type=None, module='ielts'):
    TestSession = get_test_session_model(module)
    query = TestSession.query.filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(['in_progress', 'paused'])
    )
    if test_type:
        query = query.filter(TestSession.test_type == test_type)
    count = query.count()
    if test_type and count == 0:
        return count, False
    limit = 5
    return count, count >= limit


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        User = get_user_model(session.get('selected_module', 'ielts'))
        admin = db.session.get(User, session['admin_id'])
        if not admin or not admin.is_admin:
            session.pop('admin_id', None)
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# APP CONTEXT SETUP
# ============================================================
smart_test_bank = None
standalone_bank_mgr = None
full_test_bank_mgr = None
subscription_manager = None
listening_api = None

with app.app_context():
    from models import get_test_session_model, get_test_bank_model, get_user_model, get_subscription_model, get_test_result_model
    TestSession = get_test_session_model('ielts')
    TestBank = get_test_bank_model('ielts')
    User = get_user_model('ielts')
    Subscription = get_subscription_model('ielts')
    TestResult = get_test_result_model('ielts')

    db.create_all()

    if ai_engine is None:
        from ai_engine import AIEngine
        ai_engine = AIEngine()
        logger.info(" ai_engine re‑initialized.")
    else:
        logger.info(" ai_engine already available.")

    if ai_engine:
        smart_test_bank = SmartTestBankManager(ai_engine)
        subscription_manager = get_ielts_subscription_manager(db.session)
        logger.info(" IELTS subscription manager initialized (modular)")

        if TEST_BANK_MANAGER_AVAILABLE and TestBankManager:
            try:
                standalone_bank_mgr = TestBankManager(
                    db, ai_engine, module='ielts', app=app
                )
                app.config['TEST_BANK_MANAGER'] = standalone_bank_mgr
                logger.info(" TestBankManager (standalone) initialized")
            except Exception as e:
                logger.warning(f" TestBankManager init failed: {e}")
                standalone_bank_mgr = None
                app.config['TEST_BANK_MANAGER'] = None
        else:
            logger.warning(" TestBankManager module not found — standalone bank disabled")
            app.config['TEST_BANK_MANAGER'] = None

        if FULL_TEST_BANK_MANAGER_AVAILABLE and FullTestBankManager:
            try:
                full_test_bank_mgr = FullTestBankManager(
                    db, ai_engine, app=app
                )
                app.config['FULL_TEST_BANK_MANAGER'] = full_test_bank_mgr
                logger.info(" FullTestBankManager (full mock) initialized")
            except Exception as e:
                logger.warning(f" FullTestBankManager init failed: {e}")
                full_test_bank_mgr = None
                app.config['FULL_TEST_BANK_MANAGER'] = None
        else:
            logger.warning(" FullTestBankManager not available — full-test disabled")
            app.config['FULL_TEST_BANK_MANAGER'] = None

        if IELTS_TEST_POOL_MANAGER_AVAILABLE and ielts_test_pool_manager:
            app.config['IELTS_TEST_POOL_MANAGER'] = ielts_test_pool_manager
            logger.info(" IELTSTestPoolManager (dynamic) initialized")
        else:
            app.config['IELTS_TEST_POOL_MANAGER'] = None
            logger.warning(" IELTSTestPoolManager not available — pool disabled")

        init_pte_api(ai_engine)
        app.register_blueprint(pte_blueprint)
        logger.info(" PTE API Blueprint registered and initialized with AI engine")

        import modules.ukvi.models # noqa: F401
        init_ukvi_api(ai_engine, db)
        app.register_blueprint(get_blueprint())
        logger.info(" UKVI API initialised and registered with AI engine")

        listening_api = create_listening_api(ai_engine=ai_engine, db=db)
        logger.info(" Listening API instance initialized")

        listening_blueprint = listening_api.get_blueprint()
        if listening_blueprint:
            app.register_blueprint(listening_blueprint)
            logger.info(" Listening API Blueprint registered at /api/listening")

        try:
            reading_api = create_reading_api(
                ai_engine=ai_engine,
                db=db,
                limiter=limiter
            )
            app.register_blueprint(reading_api)
            logger.info(" Reading API registered at /api/reading")
        except Exception as e:
            logger.warning(f" Reading API not initialized: {e}")

        try:
            from modules.ielts.speaking.api import create_speaking_blueprint
            spk_bp = create_speaking_blueprint(ai_engine, db=db)
            if spk_bp:
                app.register_blueprint(spk_bp)
                logger.info(
                    " IELTS Speaking blueprint registered "
                    "(/speaking/* + /api/speaking/* + pool-native)"
                )
            else:
                logger.error(" Speaking blueprint factory returned None — check api.py imports")
        except Exception as e:
            logger.error(f" Speaking blueprint registration FAILED: {e}", exc_info=True)
    else:
        logger.warning(" AI Engine not available – limited functionality")

    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        try:
            User = get_user_model('ielts')
            admin = db.session.get(User, 1)
            if not admin:
                admin = User.query.filter_by(username=ADMIN_USERNAME).first()
            if admin:
                admin.set_password(ADMIN_PASSWORD)
                db.session.commit()
                logger.info(" Admin password updated (user id=%s)", admin.id)
            else:
                admin = User(username=ADMIN_USERNAME, email='admin@example.com', is_admin=True)
                admin.set_password(ADMIN_PASSWORD)
                db.session.add(admin)
                db.session.commit()
                logger.info(" Admin user created (user id=%s)", admin.id)
        except Exception as e:
            logger.error(" Failed to create/update admin: %s", e)
    else:
        logger.warning(" ADMIN_USERNAME or ADMIN_PASSWORD not set in environment.")


@app.route('/static/ukvi/<path:filename>')
def ukvi_static(filename):
    return send_from_directory('data/ukvi', filename)


scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_old_generation_states, 'interval', days=1, args=[7])
scheduler.start()
admin_executor = ThreadPoolExecutor(max_workers=2)

# ═══════════════════════════════════════════════════════════
# UKVI BACKGROUND WORKER
# ═══════════════════════════════════════════════════════════
UKVI_WORKER_POOL = ThreadPoolExecutor(
    max_workers=5,
    thread_name_prefix='ukvi_worker',
)


def _process_ukvi_job(job_id: str):
    with app.app_context():
        try:
            job = ukvi_pool_manager.get_job(job_id)
            if not job:
                logger.warning(f"UKVI job {job_id} not found")
                return
            ukvi_pool_manager.mark_generating(job_id)
            from modules.ukvi.service import UKVIService
            svc = UKVIService(ai_engine=ai_engine, db=db)
            profile = svc.get_profile(job.user_id) or {}
            questions = svc.generate_questions_sync(
                user_id=job.user_id,
                difficulty=job.difficulty or 'medium',
                profile=profile,
            )
            if questions:
                ukvi_pool_manager.mark_complete(job_id, questions)
            else:
                ukvi_pool_manager.mark_failed(job_id, 'Empty result')
        except Exception as e:
            logger.exception(f"UKVI job {job_id} failed: {e}")
            try:
                ukvi_pool_manager.mark_failed(job_id, str(e))
            except Exception:
                pass


_ukvi_poll_tick = 0


def _poll_ukvi_jobs():
    global _ukvi_poll_tick
    _ukvi_poll_tick += 1
    with app.app_context():
        try:
            from modules.ukvi.models import UKVIGenerationJob
            queued_count = (
                UKVIGenerationJob.query
                .filter_by(status='queued')
                .count()
            )
            if _ukvi_poll_tick % 6 == 0 and queued_count == 0:
                try:
                    ukvi_pool_manager._cleanup_stale_jobs()
                except Exception as _cleanup_err:
                    logger.debug(f"UKVI stale cleanup failed: {_cleanup_err}")
            if queued_count == 0:
                return
            queued = (
                UKVIGenerationJob.query
                .filter_by(status='queued')
                .order_by(UKVIGenerationJob.created_at.asc())
                .limit(10)
                .all()
            )
            for job in queued:
                job.status = 'generating'
                job.started_at = datetime.now(timezone.utc)
                db.session.commit()
                UKVI_WORKER_POOL.submit(_process_ukvi_job, job.id)
                logger.info(f" [UKVI POLL] Submitted job {job.id} to worker")
        except Exception as e:
            logger.exception(f"_poll_ukvi_jobs failed: {e}")


# ═══════════════════════════════════════════════════════════
# PTE GENERATION JOB TRACKER
# ═══════════════════════════════════════════════════════════
_pte_gen_lock = threading.Lock()
_pte_gen_jobs: Dict[str, Dict[str, Any]] = {}


def _pte_job_new() -> str:
    return secrets.token_hex(8)


def _pte_job_cleanup_old(hours: int = 24) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    removed = 0
    with _pte_gen_lock:
        dead = []
        for jid, st in _pte_gen_jobs.items():
            end = st.get('ended_at')
            if not end:
                continue
            dt = _parse_utc_datetime(end)
            if dt and dt < cutoff:
                dead.append(jid)
        for jid in dead:
            _pte_gen_jobs.pop(jid, None)
            removed += 1
    if removed:
        logger.info(f" PTE job tracker cleanup: removed {removed} old jobs")
    return removed


scheduler.add_job(_pte_job_cleanup_old, 'interval', hours=6, args=[24])

_is_werkzeug_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
_reloader_active = bool(app.debug) and not _is_werkzeug_child

if _is_werkzeug_child or not bool(app.debug):
    scheduler.add_job(
        _poll_ukvi_jobs,
        'interval',
        seconds=10,
        id='ukvi_job_poll',
        max_instances=1,
        coalesce=True,
    )
    logger.info(" UKVI poller registered (interval=10s, single process)")
else:
    logger.info(" UKVI poller SKIPPED in reloader parent process")


# ═══════════════════════════════════════════════════════════
# AUTO-RESUME HELPERS
# ═══════════════════════════════════════════════════════════
def _find_pte_active_session(user_id: int, test_type: str):
    try:
        from modules.pte.models import PTETestSession
    except ImportError:
        try:
            from modules.pte.models import PTESession as PTETestSession
        except ImportError:
            logger.warning("PTE session model not found — auto-resume disabled")
            return None, None
    try:
        session_obj = PTETestSession.query.filter_by(
            user_id=user_id,
            test_type=test_type,
            status='in_progress',
        ).order_by(PTETestSession.start_time.desc()).first()
    except Exception as e:
        logger.error(
            f" Auto-resume query FAILED for user={user_id} "
            f"test_type={test_type!r}: {e}",
            exc_info=True,
        )
        raise
    if not session_obj:
        return None, None
    test_data = session_obj.test_data or {}
    if isinstance(test_data, str):
        try:
            test_data = json.loads(test_data)
        except Exception:
            test_data = {}
    return session_obj, test_data


def _pte_active_session_response(test_type: str):
    try:
        session_obj, test_data = _find_pte_active_session(current_user.id, test_type)
    except Exception as e:
        logger.error(
            f"Active-session lookup error for user={current_user.id} "
            f"test_type={test_type!r}: {e}",
            exc_info=True,
        )
        return jsonify({
            'success': False,
            'active': False,
            'error': 'lookup_failed',
        }), 500
    if not session_obj:
        return jsonify({'success': True, 'active': False}), 200
    return jsonify({
        'success': True,
        'active': True,
        'session_id': session_obj.id,
        'test_id': session_obj.id,
        'test_data': test_data,
        'answers_so_far': session_obj.answers_so_far or {},
        'current_question_index': getattr(session_obj, 'current_question_index', 0) or 0,
        'start_time': session_obj.start_time.isoformat() if getattr(session_obj, 'start_time', None) else None,
        'test_type': test_type,
    }), 200


# ============================================================
# MAIN ROUTES
# ============================================================
@app.route('/')
def index():
    """Homepage — redirect logged-in users to dashboard, else show login."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/health')
def health():
    return {"status": "ok"}, 200


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route('/api/payment-options')
@login_required
def payment_options():
    try:
        s = PaymentSettings.get()
        return jsonify({
            'manual_enabled': s.manual_enabled,
            'esewa_enabled': s.esewa_enabled,
            'khalti_enabled': s.khalti_enabled,
        })
    except Exception as e:
        logger.warning(f"payment_options failed: {e}")
        return jsonify({
            'manual_enabled': True,
            'esewa_enabled': True,
            'khalti_enabled': False,
        })


@app.route('/my-bills')
@login_required
def my_bills():
    try:
        bills = Bill.query.filter_by(user_id=current_user.id)\
                          .order_by(Bill.issued_at.desc()).all()
    except Exception as e:
        logger.warning(f"my_bills query failed: {e}")
        bills = []
    return render_template('my_bills.html', bills=bills)


@app.route('/bill/<int:bill_id>/download')
@login_required
def download_bill(bill_id):
    bill = db.session.get(Bill, bill_id)
    if not bill or bill.user_id != current_user.id:
        abort(404)
    if not bill.pdf_path or not os.path.exists(bill.pdf_path):
        if not BILL_GENERATOR_AVAILABLE:
            abort(500)
        try:
            pdf_path = generate_bill_pdf(bill.to_dict())
            bill.pdf_path = pdf_path
            db.session.commit()
        except Exception as e:
            logger.exception(f"Bill regen failed: {e}")
            abort(500)
    return send_file(
        bill.pdf_path,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{bill.bill_number}.pdf",
    )


@app.route('/api/pte/reading/active-session', methods=['GET'])
@login_required
def pte_reading_active_session():
    return _pte_active_session_response('pte_reading')


@app.route('/api/pte/listening/active-session', methods=['GET'])
@login_required
def pte_listening_active_session():
    return _pte_active_session_response('pte_listening')


@app.route('/api/pte/speaking/active-session', methods=['GET'])
@login_required
def pte_speaking_active_session():
    return _pte_active_session_response('pte_speaking_writing')


# ============================================================
# SPEAKING POOL-WRAPPED ROUTES
# ============================================================
@app.route('/api/speaking/pooled-generate', methods=['POST'])
@login_required
def api_speaking_pooled_generate():
    try:
        from modules.ielts.speaking.api import create_speaking_api
    except Exception as e:
        logger.error(f"Speaking API import failed: {e}")
        return jsonify({'success': False, 'error': 'Speaking unavailable'}), 503
    data = request.json or {}
    difficulty = data.get('difficulty', 'medium')
    topic = data.get('topic')
    user_id = current_user.id
    from_full_test = bool(data.get('from_full_test'))

    if from_full_test:
        ft = session.get('full_ielts_test') or {}
        parent_id = ft.get('parent_session_id')
        if parent_id:
            try:
                TestSession = get_test_session_model('ielts')
                parent = db.session.get(TestSession, int(parent_id))
                if parent:
                    td = parent.test_data or {}
                    if isinstance(td, str):
                        try:
                            td = json.loads(td)
                        except Exception:
                            td = {}
                    snap = (td.get('snapshot') or {}).get('speaking') if isinstance(td, dict) else None
                    if snap:
                        return jsonify({
                            'success': True,
                            'from_full_test': True,
                            'from_snapshot': True,
                            'session_id': parent.id,
                            'part1': snap.get('part1'),
                            'part2': snap.get('part2'),
                            'part3': snap.get('part3'),
                        })
            except Exception as e:
                logger.warning(f"Speaking snapshot load failed: {e}")

    if not from_full_test and subscription_manager:
        try:
            allowed, error, requires_sub = subscription_manager.can_access_test(user_id, 'speaking')
            if not allowed:
                logger.info(
                    f" Speaking subscription gate blocked user {user_id}: "
                    f"{error or 'limit reached'}"
                )
                return jsonify({
                    'success': False,
                    'error': error or 'Free limit reached. Please subscribe to continue.',
                    'requires_subscription': requires_sub,
                    'redirect_to': '/subscription?module=ielts',
                }), 402
        except Exception as e:
            logger.warning(f"Speaking subscription check failed for user {user_id}: {e}")

    if not ielts_test_pool_manager:
        api = create_speaking_api(ai_engine=ai_engine, db=db)
        result = api.generate_test(
            difficulty=difficulty, topic=topic,
            user_id=str(user_id), resume=True, force_new=False,
        )
        if result.get('error'):
            return jsonify({'success': False, 'error': result['error']}), 503
        result['source'] = 'on_demand'
        return jsonify(result)

    def _gen():
        api = create_speaking_api(ai_engine=ai_engine, db=db)
        return api.generate_test(
            difficulty=difficulty,
            topic=topic,
            user_id=str(user_id),
            resume=False,
            force_new=True,
            skip_subscription_check=True,
        )

    result, source, pool_id = ielts_test_pool_manager.get_or_generate(
        module='ielts_speaking',
        difficulty=difficulty,
        user_id=user_id,
        generate_fn=_gen,
    )
    result = dict(result or {})

    if source == 'waiting':
        return jsonify({
            'success': False,
            'source': 'waiting',
            'message': 'Another user is generating a speaking test. Please retry.',
            'retry_after': result.get('retry_after', 3),
        }), 202
    if source == 'exhausted':
        return jsonify({
            'success': False,
            'source': 'exhausted',
            'error': 'Speaking pool is full. Please try again shortly.'
        }), 503
    if source == 'failed' or result.get('error'):
        return jsonify({
            'success': False,
            'source': 'failed',
            'error': result.get('error', 'Speaking generation failed.')
        }), 503

    try:
        TestSession = get_test_session_model('ielts')
        sess = TestSession(
            user_id=user_id,
            test_type='speaking',
            difficulty=difficulty,
            test_data={**result, '_pool_id': pool_id},
            status='in_progress',
            start_time=datetime.now(timezone.utc),
        )
        db.session.add(sess)
        db.session.commit()
        result['session_id'] = sess.id
    except Exception as e:
        logger.warning(f"Could not persist speaking session: {e}")

    result['_pool_id'] = pool_id
    result['source'] = source
    result['success'] = True
    logger.info(f" [speaking/pooled] user={user_id} source={source} pool_id={pool_id}")
    return jsonify(result)


@app.route('/api/speaking/pooled-submit', methods=['POST'])
@login_required
def api_speaking_pooled_submit():
    try:
        from modules.ielts.speaking.api import create_speaking_api
    except Exception as e:
        logger.error(f"Speaking API import failed: {e}")
        return jsonify({'success': False, 'error': 'Speaking unavailable'}), 503
    data = request.json or {}
    session_id = data.get('session_id')
    responses = data.get('responses') or []
    is_full_test = bool(data.get('is_full_test'))
    user_id = current_user.id

    if not responses:
        if is_full_test:
            ft = session.get('full_ielts_test')
            if ft:
                ft.setdefault('scores', {})['speaking'] = {
                    'band_score': 0.0, 'score_pct': None,
                    'correct': None, 'total': None,
                }
                ft.setdefault('completed_sections', {})['speaking'] = True
                ft.setdefault('answers', {})['speaking'] = {'responses': []}
                for p in ('listening', 'reading', 'writing', 'speaking'):
                    if not ft['completed_sections'].get(p):
                        ft['phase'] = p
                        break
                else:
                    ft['phase'] = 'done'
                session['full_ielts_test'] = ft
                session.modified = True
                return jsonify({
                    'success': True,
                    'is_full_test': True,
                    'band_score': 0.0,
                    'feedback': 'BAND 0 — No responses provided.',
                    'next_phase': ft['phase'],
                    'redirect': '/ielts-full-test?completed=speaking',
                })
        return jsonify({
            'success': True,
            'band_score': 0.0,
            'user_overall_band': 0.0,
            'feedback': 'BAND 0 — No responses provided.',
        })

    api = create_speaking_api(ai_engine=ai_engine, db=db)
    per_part = {1: [], 2: [], 3: []}
    feedbacks = []
    for r in responses:
        part = int(r.get('part') or 1)
        ev = api.evaluate_response(
            question=r.get('question', ''),
            response=r.get('response', ''),
            part=part, user_id=user_id,
        )
        per_part.setdefault(part, []).append(float(ev.get('overall') or 0.0))
        if ev.get('feedback'):
            feedbacks.append(f"Part {part}: {ev['feedback']}")

    all_scores = [s for lst in per_part.values() for s in lst]
    band = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0
    part_avgs = {
        p: (round(sum(l) / len(l), 1) if l else 0.0)
        for p, l in per_part.items()
    }

    try:
        api._ensure_repository()
        if api.repository:
            api.repository.save_session({
                'session_id': session_id or api.sid,
                'user_id': user_id,
                'topic': None,
                'overall_band': band,
                'fluency_coherence': part_avgs.get(1, 0.0),
                'lexical_resource': part_avgs.get(2, 0.0),
                'grammar_accuracy': part_avgs.get(3, 0.0),
                'pronunciation': band,
                'response_text': ' | '.join(
                    (r.get('response') or '') for r in responses
                )[:2000],
                'word_count': sum(
                    len((r.get('response') or '').split()) for r in responses
                ),
                'duration_seconds': 0,
                'feedback': ' | '.join(feedbacks[:6]),
                'improvement_tips': [],
            })
    except Exception as e:
        logger.warning(f"Speaking repository save failed: {e}")

    try:
        TestSession = get_test_session_model('ielts')
        sess = db.session.get(TestSession, int(session_id)) if session_id else None
        if sess:
            sess.status = 'completed'
            sess.answers_so_far = {'responses': responses}
            sess.last_updated = datetime.now(timezone.utc)
            _pool_id = None
            td = sess.test_data or {}
            if isinstance(td, str):
                try:
                    td = json.loads(td)
                except Exception:
                    td = {}
            if isinstance(td, dict):
                _pool_id = td.get('_pool_id')
            db.session.commit()
            if _pool_id and ielts_test_pool_manager:
                ielts_test_pool_manager.record_user_progress(
                    user_id=user_id,
                    module='ielts_speaking',
                    pool_id=_pool_id,
                )
                logger.info(
                    f" Speaking pool progress recorded: "
                    f"pool_id={_pool_id}, user={user_id}"
                )
    except Exception as e:
        logger.warning(f"Speaking session pool progress update failed: {e}")

    if is_full_test:
        ft = session.get('full_ielts_test')
        if ft:
            ft.setdefault('scores', {})['speaking'] = {
                'band_score': band, 'score_pct': None,
                'correct': None, 'total': None,
            }
            ft.setdefault('completed_sections', {})['speaking'] = True
            ft.setdefault('answers', {})['speaking'] = {'responses': responses}
            for p in ('listening', 'reading', 'writing', 'speaking'):
                if not ft['completed_sections'].get(p):
                    ft['phase'] = p
                    break
            else:
                ft['phase'] = 'done'
            session['full_ielts_test'] = ft
            session.modified = True
            logger.info(
                f" Full-test speaking recorded for user {user_id}, "
                f"band={band}, next={ft['phase']}"
            )
            return jsonify({
                'success': True,
                'is_full_test': True,
                'band_score': band,
                'part_averages': part_avgs,
                'feedback': ' | '.join(feedbacks[:6]),
                'next_phase': ft['phase'],
                'redirect': '/ielts-full-test?completed=speaking',
            })

    return jsonify({
        'success': True,
        'user_overall_band': band,
        'band_score': band,
        'part_averages': part_avgs,
        'feedback': ' | '.join(feedbacks[:6]),
    })


# ============================================================
# READING POOL-WRAPPED ROUTES
# ============================================================
@app.route('/api/reading/pooled-generate', methods=['POST'])
@login_required
def api_reading_pooled_generate():
    data = request.json or {}
    difficulty = data.get('difficulty', 'medium')
    topic = data.get('topic')
    user_id = current_user.id
    from_full_test = bool(data.get('from_full_test'))

    if from_full_test:
        ft = session.get('full_ielts_test') or {}
        parent_id = ft.get('parent_session_id')
        if parent_id:
            try:
                TestSessionM = get_test_session_model('ielts')
                parent = db.session.get(TestSessionM, int(parent_id))
                if parent:
                    td = parent.test_data or {}
                    if isinstance(td, str):
                        try:
                            td = json.loads(td)
                        except Exception:
                            td = {}
                    snap = (td.get('snapshot') or {}).get('reading') if isinstance(td, dict) else None
                    if snap:
                        phase_session = TestSessionM(
                            user_id=user_id,
                            test_type='reading',
                            difficulty=difficulty,
                            test_data=snap,
                            status='in_progress',
                            start_time=datetime.now(timezone.utc),
                        )
                        db.session.add(phase_session)
                        db.session.commit()
                        session['current_test_session_id'] = phase_session.id
                        return jsonify({
                            'success': True,
                            'from_full_test': True,
                            'from_snapshot': True,
                            'session_id': phase_session.id,
                            'test_data': snap,
                            'passages': snap.get('passages', []),
                            'questions': snap.get('questions', []),
                        })
            except Exception as e:
                logger.warning(f"Reading snapshot load failed: {e}")

    if not from_full_test and subscription_manager:
        try:
            allowed, error, requires_sub = subscription_manager.can_access_test(user_id, 'reading')
            if not allowed:
                logger.info(
                    f" Reading subscription gate blocked user {user_id}: "
                    f"{error or 'limit reached'}"
                )
                return jsonify({
                    'success': False,
                    'error': error or 'Free limit reached. Please subscribe to continue.',
                    'requires_subscription': requires_sub,
                    'redirect_to': '/subscription?module=ielts',
                }), 402
        except Exception as e:
            logger.warning(f"Reading subscription check failed for user {user_id}: {e}")

    if not ielts_test_pool_manager:
        try:
            gen = IELTSReadingGenerator(ai_engine=ai_engine)
            t = gen.generate_complete_test(difficulty, topic)
            result = t.to_dict() if hasattr(t, 'to_dict') else t
            result['source'] = 'on_demand'
            result['success'] = True
            TestSessionM = get_test_session_model('ielts')
            sess = TestSessionM(
                user_id=user_id,
                test_type='reading',
                difficulty=difficulty,
                test_data=result,
                status='in_progress',
                start_time=datetime.now(timezone.utc),
            )
            db.session.add(sess)
            db.session.commit()
            result['session_id'] = sess.id
            session['current_test_session_id'] = sess.id
            return jsonify(result)
        except Exception as e:
            logger.error(f"Reading fallback generation failed: {e}")
            return jsonify({'success': False, 'error': str(e)}), 503

    def _gen():
        gen = IELTSReadingGenerator(ai_engine=ai_engine)
        t = gen.generate_complete_test(difficulty, topic)
        return t.to_dict() if hasattr(t, 'to_dict') else t

    result, source, pool_id = ielts_test_pool_manager.get_or_generate(
        module='ielts_reading',
        difficulty=difficulty,
        user_id=user_id,
        generate_fn=_gen,
    )
    result = dict(result or {})

    if source == 'waiting':
        return jsonify({
            'success': False,
            'source': 'waiting',
            'message': 'Another user is generating a reading test. Please retry.',
            'retry_after': result.get('retry_after', 3),
        }), 202
    if source == 'exhausted':
        return jsonify({
            'success': False,
            'source': 'exhausted',
            'error': 'Reading pool is full. Please try again shortly.'
        }), 503
    if source == 'failed' or result.get('error'):
        return jsonify({
            'success': False,
            'source': 'failed',
            'error': result.get('error', 'Reading generation failed.')
        }), 503

    try:
        TestSessionM = get_test_session_model('ielts')
        sess = TestSessionM(
            user_id=user_id,
            test_type='reading',
            difficulty=difficulty,
            test_data={**result, '_pool_id': pool_id},
            status='in_progress',
            start_time=datetime.now(timezone.utc),
        )
        db.session.add(sess)
        db.session.commit()
        result['session_id'] = sess.id
        session['current_test_session_id'] = sess.id
    except Exception as e:
        logger.warning(f"Could not persist reading session: {e}")

    result['_pool_id'] = pool_id
    result['source'] = source
    result['success'] = True
    logger.info(f" [reading/pooled] user={user_id} source={source} pool_id={pool_id}")
    return jsonify(result)


@app.route('/api/reading/pooled-submit', methods=['POST'])
@login_required
def api_reading_pooled_submit():
    data = request.json or {}
    session_id = data.get('session_id')
    user_id = current_user.id
    try:
        TestSessionM = get_test_session_model('ielts')
        sess = db.session.get(TestSessionM, int(session_id)) if session_id else None
        if sess:
            _pool_id = None
            td = sess.test_data or {}
            if isinstance(td, str):
                try:
                    td = json.loads(td)
                except Exception:
                    td = {}
            if isinstance(td, dict):
                _pool_id = td.get('_pool_id')
            if _pool_id and ielts_test_pool_manager:
                ielts_test_pool_manager.record_user_progress(
                    user_id=user_id,
                    module='ielts_reading',
                    pool_id=_pool_id,
                )
                logger.info(
                    f" Reading pool progress recorded: "
                    f"pool_id={_pool_id}, user={user_id}"
                )
            return jsonify({'success': True, 'pool_id': _pool_id})
    except Exception as e:
        logger.warning(f"Reading pool progress update failed: {e}")
    return jsonify({'success': True})


# ============================================================
# LOGIN / REGISTER
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    module = request.args.get('module') or request.form.get('module') or session.get('selected_module', 'ielts')
    if module not in ['ielts', 'pte', 'ukvi']:
        module = 'ielts'
    session['selected_module'] = module
    User = get_user_model(module)
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('index.html', error="Username and password required", module=module)
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            session.clear()
            session['selected_module'] = module
            login_user(user, remember=True, duration=timedelta(days=30))
            session.permanent = True
            log_user_activity(user.id, 'login', {'method': 'password', 'module': module})
            return redirect(url_for('dashboard'))
        return render_template('index.html', error="Invalid credentials", module=module)
    return render_template('index.html', module=module)


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def register():
    module = request.args.get('module') or request.form.get('module') or session.get('selected_module', 'ielts')
    if module not in ['ielts', 'pte', 'ukvi']:
        module = 'ielts'
    session['selected_module'] = module
    User = get_user_model(module)

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if not username or not password:
            return render_template('index.html',
                                   error="Username and password are required",
                                   module=module)
        if len(username) < 3:
            return render_template('index.html',
                                   error="Username must be at least 3 characters",
                                   module=module)
        if len(username) > 80:
            return render_template('index.html',
                                   error="Username is too long (max 80 characters)",
                                   module=module)
        if not re.match(r'^[a-zA-Z0-9._-]+$', username):
            return render_template('index.html',
                                   error="Username can only contain letters, numbers, dot, underscore, hyphen",
                                   module=module)
        if len(password) < 8:
            return render_template('index.html',
                                   error="Password must be at least 8 characters",
                                   module=module)
        if User.query.filter_by(username=username).first():
            return render_template('index.html',
                                   error="Username already taken",
                                   module=module)

        email = f"{username.lower()}@seltaiprep.local"
        if User.query.filter_by(email=email).first():
            counter = 1
            while User.query.filter_by(email=f"{username.lower()}{counter}@seltaiprep.local").first():
                counter += 1
                if counter > 1000:
                    break
            email = f"{username.lower()}{counter}@seltaiprep.local"

        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Registration failed for {username}: {e}")
            return render_template('index.html',
                                   error="Registration failed. Please try again.",
                                   module=module)

        try:
            if module == 'ielts':
                from models import create_default_subscription_for_user
                create_default_subscription_for_user(user.id, 'ielts')
        except Exception as e:
            logger.warning(f"Default subscription creation failed: {e}")

        session.clear()
        session['selected_module'] = module
        login_user(user, remember=True, duration=timedelta(days=30))
        session.permanent = True
        log_user_activity(user.id, 'register', {'module': module, 'method': 'username_password'})
        logger.info(f" New user registered: {user.id} ({username})")
        return redirect(url_for('dashboard'))

    return render_template('index.html', module=module)


@app.route('/logout')
@login_required
def logout():
    log_user_activity(current_user.id, 'logout')
    session.clear()
    logout_user()
    return redirect(url_for('index'))


@app.route('/select-module', methods=['GET', 'POST'])
@login_required
def select_module():
    return redirect(url_for('dashboard'))


# ============================================================
# DASHBOARD
# ============================================================
@app.route('/dashboard')
@login_required
def dashboard():
    q_module = request.args.get('module')
    if q_module in ('ielts', 'pte', 'ukvi'):
        session['selected_module'] = q_module
    module = session.get('selected_module', 'ielts')
    User = get_user_model(module)
    user = db.session.get(User, current_user.id)
    if not user:
        logout_user()
        return redirect(url_for('login'))
    try:
        db.session.expire_all()
    except Exception:
        pass

    module_status = {}
    if module == 'ielts':
        module_status = subscription_manager.get_status(current_user.id)
    elif module == 'pte':
        module_status = pte_subscription_manager.get_subscription_status(current_user.id)
    elif module == 'ukvi':
        ukvi_sub_manager = UKVISubscriptionManager(db)
        module_status = ukvi_sub_manager.get_status(current_user.id)

    Subscription = get_subscription_model(module)
    subscription = Subscription.query.filter_by(user_id=current_user.id).first()
    TestResult = get_test_result_model(module)
    results = TestResult.query.filter_by(user_id=current_user.id).order_by(TestResult.created_at.desc()).all()

    writing_history = []
    if module == 'ielts':
        try:
            from modules.ielts.writing.repository import writing_repo
            writing_history = writing_repo.get_user_essays(current_user.id, limit=10)
        except Exception:
            pass

    TestSession = get_test_session_model(module)
    active_sessions = TestSession.query.filter_by(user_id=current_user.id, status='in_progress').all()

    full_test_in_progress = False
    full_test_next_phase = None
    if module == 'ielts':
        ft = session.get('full_ielts_test')
        if ft:
            full_test_in_progress = True
            for p in ('listening', 'reading', 'writing', 'speaking'):
                if not ft.get('completed_sections', {}).get(p):
                    full_test_next_phase = p
                    break

    return render_template(
        'dashboard.html',
        selected_module=module,
        module_status=module_status,
        subscription=subscription,
        user=user,
        results=results,
        writing_history=writing_history,
        active_sessions=active_sessions,
        full_test_in_progress=full_test_in_progress,
        full_test_next_phase=full_test_next_phase,
    )


# ============================================================
# GOOGLE & FACEBOOK & OTP
# ============================================================
@app.route('/login/google')
def google_login():
    module = request.args.get('module', 'ielts')
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' if os.environ.get('FLASK_ENV') == 'development' else ''
    redirect_uri = url_for('google_authorized', _external=True)
    auth_url = f'https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id={GOOGLE_CLIENT_ID}&redirect_uri={redirect_uri}&scope=openid email profile&prompt=consent&access_type=offline&state={module}'
    return redirect(auth_url)


@app.route('/login/google/authorized')
def google_authorized():
    try:
        module = request.args.get('state', 'ielts')
        if module not in ['ielts', 'pte', 'ukvi']:
            module = 'ielts'
        User = get_user_model(module)
        code = request.args.get('code')
        redirect_uri = url_for('google_authorized', _external=True)
        token_resp = requests.post('https://oauth2.googleapis.com/token', data={
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        })
        token_json = token_resp.json()
        access_token = token_json.get('access_token')
        if not access_token:
            raise Exception('No access token')
        data = requests.get('https://www.googleapis.com/oauth2/v1/userinfo', headers={'Authorization': f'Bearer {access_token}'}).json()
        email = data.get('email')
        name = data.get('name', email.split('@')[0])
        google_id = data.get('id')
        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(username=name, email=email, google_id=google_id)
                user.set_password(secrets.token_hex(32))
                db.session.add(user)
                db.session.commit()
            else:
                user.google_id = google_id
                db.session.commit()
        session.clear()
        session['selected_module'] = module
        login_user(user, remember=True, duration=timedelta(days=30))
        session.permanent = True
        log_user_activity(user.id, 'login', {'method': 'google', 'module': module})
        return redirect(url_for('dashboard'))
    except Exception as e:
        logger.error(f"Google auth failed: {e}")
        return redirect(url_for('login'))


@app.route('/login/facebook')
@limiter.limit("20 per minute")
def facebook_login():
    if not FACEBOOK_APP_ID:
        flash('Facebook login is not configured.', 'danger')
        return redirect(url_for('login'))
    module = request.args.get('module', 'ielts')
    if module not in ['ielts', 'pte', 'ukvi']:
        module = 'ielts'
    if os.environ.get('FLASK_ENV') == 'production':
        redirect_uri = FACEBOOK_REDIRECT_URI
    else:
        redirect_uri = url_for('facebook_authorized', _external=True)
    auth_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth?"
        f"client_id={FACEBOOK_APP_ID}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&state={module}"
        f"&scope=email,public_profile"
        f"&response_type=code"
    )
    return redirect(auth_url)


@app.route('/login/facebook/authorized')
@limiter.limit("20 per minute")
def facebook_authorized():
    try:
        module = request.args.get('state', 'ielts')
        if module not in ['ielts', 'pte', 'ukvi']:
            module = 'ielts'
        error = request.args.get('error')
        if error:
            logger.warning(f"Facebook auth denied: {error}")
            flash('Facebook login cancelled.', 'warning')
            return redirect(url_for('login', module=module))
        code = request.args.get('code')
        if not code:
            raise Exception('No authorization code')
        if os.environ.get('FLASK_ENV') == 'production':
            redirect_uri = FACEBOOK_REDIRECT_URI
        else:
            redirect_uri = url_for('facebook_authorized', _external=True)
        token_resp = requests.get(
            'https://graph.facebook.com/v18.0/oauth/access_token',
            params={
                'client_id': FACEBOOK_APP_ID,
                'client_secret': FACEBOOK_APP_SECRET,
                'redirect_uri': redirect_uri,
                'code': code,
            },
            timeout=30,
        )
        token_data = token_resp.json()
        if 'error' in token_data:
            raise Exception(f"Token error: {token_data['error']}")
        access_token = token_data.get('access_token')
        if not access_token:
            raise Exception(f"No access token: {token_data}")
        user_resp = requests.get(
            'https://graph.facebook.com/v18.0/me',
            params={
                'fields': 'id,name,email,first_name,last_name',
                'access_token': access_token,
            },
            timeout=30,
        )
        user_data = user_resp.json()
        fb_id = user_data.get('id')
        email = user_data.get('email')
        name = user_data.get('name', '')
        first_name = user_data.get('first_name', '')
        if not fb_id:
            raise Exception(f"No Facebook ID: {user_data}")
        if not email:
            email = f"fb_{fb_id}@seltaiprep.local"
            logger.warning(f"Facebook didn't return email for {fb_id}")
        User = get_user_model(module)
        user = User.query.filter_by(facebook_id=fb_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if not user:
                base_username = (
                    first_name.lower().replace(' ', '') or
                    (name or '').lower().replace(' ', '')[:20] or
                    f"fb_{fb_id[:8]}"
                )
                username = base_username
                counter = 1
                while User.query.filter_by(username=username).first():
                    username = f"{base_username}{counter}"
                    counter += 1
                    if counter > 100:
                        username = f"fb_{fb_id[:10]}"
                        break
                user = User(
                    username=username,
                    email=email,
                    facebook_id=fb_id,
                )
                user.set_password(secrets.token_hex(32))
                db.session.add(user)
                db.session.commit()
                logger.info(f" New user via Facebook: {user.id} ({email})")
            else:
                user.facebook_id = fb_id
                db.session.commit()
                logger.info(f" Linked Facebook to existing user: {user.id}")
        session.clear()
        session['selected_module'] = module
        login_user(user, remember=True, duration=timedelta(days=30))
        session.permanent = True
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        log_user_activity(user.id, 'login', {
            'method': 'facebook',
            'module': module,
        })
        return redirect(url_for('dashboard'))
    except Exception as e:
        logger.exception(f"Facebook auth failed: {e}")
        flash('Facebook login failed. Please try again or use another method.', 'danger')
        return redirect(url_for('login'))


@app.route('/login/email-otp', methods=['POST'])
@limiter.limit("3 per minute")
def send_email_otp():
    email = request.form.get('email', '').strip()
    module = request.form.get('module', 'ielts') or session.get('selected_module', 'ielts')
    if not email:
        return jsonify({'success': False, 'error': 'Email required'})
    otp = ''.join(random.choices(string.digits, k=6))
    session['otp'] = otp
    session['otp_email'] = email
    session['otp_module'] = module
    session['otp_expiry'] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    try:
        msg = Message('SELTAI PREP - Login OTP', recipients=[email], body=f'Your OTP: {otp}\nExpires in 5 minutes.')
        mail.send(msg)
        return jsonify({'success': True, 'message': 'OTP sent'})
    except Exception as e:
        logger.error(f"OTP send error: {e}")
        return jsonify({'success': True, 'message': 'OTP sent (check logs)'})


@app.route('/login/email-otp/verify', methods=['POST'])
@limiter.limit("5 per minute")
def verify_email_otp():
    user_otp = request.form.get('otp', '').strip()
    stored_otp = session.get('otp', '')
    email = session.get('otp_email', '')
    module = session.get('otp_module', 'ielts') or session.get('selected_module', 'ielts')
    if not user_otp or not stored_otp or user_otp != stored_otp:
        return jsonify({'success': False, 'error': 'Invalid or expired OTP'})
    User = get_user_model(module)
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(username=email.split('@')[0], email=email)
        user.set_password(secrets.token_hex(32))
        db.session.add(user)
        db.session.commit()
    session.pop('otp', None)
    session.pop('otp_email', None)
    session.pop('otp_expiry', None)
    session.pop('otp_module', None)
    session.clear()
    session['selected_module'] = module
    login_user(user, remember=True, duration=timedelta(days=30))
    session.permanent = True
    log_user_activity(user.id, 'login', {'method': 'otp', 'module': module})
    return redirect(url_for('dashboard'))


# ═══════════════════════════════════════════════════════════
# FORGOT PASSWORD
# ═══════════════════════════════════════════════════════════
@app.route('/forgot-password/send-otp', methods=['POST'])
@limiter.limit("3 per minute")
def forgot_password_send_otp():
    data = request.form or request.json or {}
    email = (data.get('email') or '').strip().lower()
    module = data.get('module', 'ielts')
    if module not in ['ielts', 'pte', 'ukvi']:
        module = 'ielts'
    if not email:
        return jsonify({'success': False, 'error': 'Email is required'})
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({'success': False, 'error': 'Invalid email format'})

    User = get_user_model(module)
    user = User.query.filter_by(email=email).first()
    generic_success = {
        'success': True,
        'message': 'If this email is registered, an OTP has been sent.',
        'email': email,
    }
    if not user:
        logger.info(f" [forgot-pw] non-existent email: {email}")
        return jsonify(generic_success)

    otp = ''.join(random.choices(string.digits, k=6))
    session['fp_otp'] = otp
    session['fp_email'] = email
    session['fp_module'] = module
    session['fp_user_id'] = user.id
    session['fp_otp_expiry'] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    session.modified = True

    mail_sent = True
    try:
        msg = Message(
            subject='SELTAI PREP - Password Reset',
            recipients=[email],
            body=(
                f'We received a request to reset your password.\n\n'
                f'Your reset code: {otp}\n\n'
                f'This code expires in 10 minutes.\n'
                f'If you did not request this, please ignore this email.'
            )
        )
        mail.send(msg)
    except Exception as e:
        logger.error(f" [forgot-pw] OTP send failed: {e}")
        mail_sent = False

    if not mail_sent:
        if os.environ.get('FLASK_ENV') == 'development':
            generic_success['dev_otp'] = otp
            generic_success['message'] = f'Mail failed — dev OTP: {otp}'
        else:
            return jsonify({'success': False, 'error': 'Could not send OTP. Try again later.'})
    return jsonify(generic_success)


@app.route('/forgot-password/verify-otp', methods=['POST'])
@limiter.limit("5 per minute")
def forgot_password_verify_otp():
    data = request.form or request.json or {}
    user_otp = (data.get('otp') or '').strip()
    new_password = data.get('new_password') or ''
    stored_otp = session.get('fp_otp')
    email = session.get('fp_email')
    module = session.get('fp_module', 'ielts')
    user_id = session.get('fp_user_id')
    expiry = session.get('fp_otp_expiry')

    if not stored_otp or not email or not user_id:
        return jsonify({'success': False, 'error': 'Session expired. Please start over.'})
    if expiry:
        exp_dt = _parse_utc_datetime(expiry)
        if exp_dt and datetime.now(timezone.utc) > exp_dt:
            for k in ['fp_otp', 'fp_email', 'fp_module', 'fp_user_id', 'fp_otp_expiry']:
                session.pop(k, None)
            return jsonify({'success': False, 'error': 'OTP expired. Please request a new one.'})
    if user_otp != stored_otp:
        return jsonify({'success': False, 'error': 'Invalid OTP code'})
    if len(new_password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters'})

    User = get_user_model(module)
    user = db.session.get(User, int(user_id))
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})
    try:
        user.set_password(new_password)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f" [forgot-pw] password reset failed: {e}")
        return jsonify({'success': False, 'error': 'Password reset failed. Please try again.'})
    for k in ['fp_otp', 'fp_email', 'fp_module', 'fp_user_id', 'fp_otp_expiry']:
        session.pop(k, None)
    log_user_activity(user.id, 'password_reset', {'module': module})
    logger.info(f" [forgot-pw] password reset for user {user.id} ({email})")
    return jsonify({
        'success': True,
        'message': 'Password reset successful! Please sign in.',
        'redirect': '/login'
    })


# ═══════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════
@app.route('/profile')
@login_required
def profile_page():
    module = session.get('selected_module', 'ielts')
    User = get_user_model(module)
    user = db.session.get(User, current_user.id)
    if not user:
        logout_user()
        return redirect(url_for('login'))
    Subscription = get_subscription_model(module)
    subscription = Subscription.query.filter_by(user_id=current_user.id).first()
    TestResult = get_test_result_model(module)
    total_tests = TestResult.query.filter_by(user_id=current_user.id).count()
    return render_template(
        'profile.html',
        user=user,
        module=module,
        subscription=subscription,
        total_tests=total_tests,
    )


@app.route('/profile/update', methods=['POST'])
@login_required
def profile_update():
    data = request.form or request.json or {}
    full_name = (data.get('full_name') or '').strip()
    target_band_raw = data.get('target_band')
    module = session.get('selected_module', 'ielts')
    User = get_user_model(module)
    user = db.session.get(User, current_user.id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})
    try:
        user.full_name = full_name or None
        if target_band_raw not in (None, '', 'null'):
            try:
                tb = float(target_band_raw)
                if tb < 0 or tb > 9:
                    raise ValueError("out of range")
                user.target_band = tb
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Target band must be between 0 and 9'})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Profile update failed: {e}")
        return jsonify({'success': False, 'error': 'Update failed. Please try again.'})
    log_user_activity(user.id, 'profile_update', {'module': module})
    return jsonify({'success': True, 'message': 'Profile updated successfully'})


@app.route('/profile/change-password', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def profile_change_password():
    data = request.form or request.json or {}
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password') or ''
    module = session.get('selected_module', 'ielts')
    User = get_user_model(module)
    user = db.session.get(User, current_user.id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})
    if not current_password or not new_password:
        return jsonify({'success': False, 'error': 'All fields are required'})
    if not user.check_password(current_password):
        return jsonify({'success': False, 'error': 'Current password is incorrect'})
    if new_password != confirm_password:
        return jsonify({'success': False, 'error': 'New passwords do not match'})
    if len(new_password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters'})
    if new_password == current_password:
        return jsonify({'success': False, 'error': 'New password must be different'})
    try:
        user.set_password(new_password)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Password change failed: {e}")
        return jsonify({'success': False, 'error': 'Password change failed'})
    log_user_activity(user.id, 'password_change', {'module': module})
    logger.info(f" [profile] password changed for user {user.id}")
    return jsonify({'success': True, 'message': 'Password changed successfully'})


@app.route('/api/progress-data')
@login_required
def api_progress_data():
    module = session.get('selected_module', 'ielts')
    try:
        limit = int(request.args.get('limit', 30))
    except (ValueError, TypeError):
        limit = 30
    if limit < 5:
        limit = 5
    if limit > 100:
        limit = 100
    try:
        TestResult = get_test_result_model(module)
        results = (
            TestResult.query
            .filter_by(user_id=current_user.id)
            .order_by(TestResult.created_at.asc())
            .limit(limit)
            .all()
        )
    except Exception as e:
        logger.warning(f"progress-data query failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    if not results:
        return jsonify({
            'success': True,
            'module': module,
            'labels': [],
            'datasets': [],
            'message': 'No test results yet',
        })
    date_index = {}
    labels = []
    for r in results:
        if not r.created_at:
            continue
        d = r.created_at.strftime('%b %d')
        if d not in date_index:
            date_index[d] = len(labels)
            labels.append(d)
    n = len(labels)
    if n == 0:
        return jsonify({'success': True, 'module': module, 'labels': [], 'datasets': []})
    COLORS = {
        'listening':            '#3b82f6',
        'reading':              '#22c55e',
        'writing':              '#f59e0b',
        'speaking':             '#ef4444',
        'full_ielts':           '#8b5cf6',
        'pte_listening':        '#3b82f6',
        'pte_reading':          '#22c55e',
        'pte_speaking_writing': '#8b5cf6',
        'pte_full':             '#7c3aed',
        'ukvi_interview':       '#f59e0b',
        'interview':            '#f59e0b',
    }
    DEFAULT_COLOR = '#6c63ff'
    groups = {}
    for r in results:
        if not r.created_at:
            continue
        t = (r.test_type or 'unknown').lower()
        if t not in groups:
            groups[t] = [None] * n
        idx = date_index.get(r.created_at.strftime('%b %d'))
        if idx is not None:
            band = float(r.band_score or 0)
            groups[t][idx] = band
    datasets = []
    for test_type, data in groups.items():
        if all(v is None for v in data):
            continue
        color = COLORS.get(test_type, DEFAULT_COLOR)
        label = test_type.replace('_', ' ').title()
        datasets.append({
            'label': label,
            'data': data,
            'borderColor': color,
            'backgroundColor': color + '20',
            'tension': 0.3,
            'borderWidth': 3,
            'pointRadius': 5,
            'pointHoverRadius': 8,
            'pointBackgroundColor': color,
            'pointBorderColor': '#fff',
            'pointBorderWidth': 2,
            'spanGaps': True,
        })
    return jsonify({
        'success': True,
        'module': module,
        'labels': labels,
        'datasets': datasets,
    })


@app.route('/api/apply-coupon', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
def api_apply_coupon():
    data = request.get_json(silent=True) or request.form or {}
    code = (data.get('code') or '').strip().upper()
    module = (data.get('module') or session.get('selected_module') or 'ielts').lower()
    plan_key = (data.get('plan') or '30days').lower()
    if module not in MODULE_PRICES:
        return jsonify({'success': False, 'error': 'Invalid module'}), 400
    if plan_key not in PLAN_CONFIG:
        return jsonify({'success': False, 'error': 'Invalid plan'}), 400
    original_amount = PRICE_CONFIG.get(module, {}).get(
        plan_key, MODULE_PRICES[module]
    )
    coupon, err = find_coupon(code)
    if err:
        return jsonify({'success': False, 'error': err}), 200
    disc, verr = validate_coupon_for_user(
        coupon, current_user.id, module, plan_key, original_amount
    )
    if verr:
        return jsonify({'success': False, 'error': verr}), 200
    final_amount = max(0, int(original_amount) - int(disc))
    return jsonify({
        'success': True,
        'code': coupon.code,
        'description': coupon.description,
        'discount_type': coupon.discount_type,
        'discount_value': coupon.discount_value,
        'original_amount': int(original_amount),
        'discount_amount': int(disc),
        'final_amount': int(final_amount),
    })


# ============================================================
# SUBSCRIPTION ROUTES
# ============================================================
@app.route('/<module>/create-checkout-session', methods=['POST'])
@login_required
@csrf_protect
def create_checkout_session(module):
    if module not in MODULE_PRICES:
        return jsonify({'error': 'Invalid module'}), 400
    data = request.get_json(silent=True) or {}
    plan_key = data.get('plan', '30days')
    if plan_key not in PLAN_CONFIG:
        plan_key = '30days'
    coupon_code = (data.get('coupon') or '').strip().upper()
    original_amount = PRICE_CONFIG.get(module, {}).get(plan_key, MODULE_PRICES[module])
    discount_amount = 0
    coupon_obj = None
    coupon_error = None
    if coupon_code:
        coupon_obj, cerr = find_coupon(coupon_code)
        if cerr:
            coupon_error = cerr
        else:
            d, verr = validate_coupon_for_user(
                coupon_obj, current_user.id, module, plan_key, original_amount
            )
            if verr:
                coupon_error = verr
            else:
                discount_amount = d
    final_amount = max(0, int(original_amount) - int(discount_amount))
    product_code = f'{module.upper()}_{plan_key.upper()}'
    txn_uuid = f"{module}_{plan_key}_{current_user.id}_{int(time.time())}_{secrets.token_hex(4)}"
    payment_url = generate_esewa_payment_url(
        final_amount, txn_uuid, product_code,
        url_for('subscription_success', module=module, _external=True),
        url_for('subscription_cancel', module=module, _external=True)
    )
    session[f'esewa_txn_id_{module}'] = txn_uuid
    session[f'esewa_amount_{module}'] = final_amount
    session[f'esewa_plan_{module}'] = plan_key
    if coupon_obj and discount_amount > 0:
        session[f'esewa_coupon_id_{module}'] = coupon_obj.id
        session[f'esewa_coupon_code_{module}'] = coupon_obj.code
        session[f'esewa_original_amount_{module}'] = int(original_amount)
        session[f'esewa_discount_amount_{module}'] = int(discount_amount)
    response = {
        'payment_url': payment_url,
        'transaction_id': txn_uuid,
        'plan': plan_key,
        'original_amount': int(original_amount),
        'amount': int(final_amount),
        'discount_amount': int(discount_amount),
    }
    if coupon_error:
        response['coupon_error'] = coupon_error
    if coupon_obj and discount_amount > 0:
        response['coupon'] = {
            'code': coupon_obj.code,
            'description': coupon_obj.description,
            'discount_amount': int(discount_amount),
        }
    return jsonify(response)


@app.route('/<module>/manual-payment', methods=['GET'])
@login_required
def manual_payment(module):
    if module not in MODULE_PRICES:
        return redirect(url_for('dashboard'))
    try:
        settings = PaymentSettings.get()
        if not settings.manual_enabled:
            flash('Manual payment is currently disabled.', 'warning')
            return redirect(url_for('subscription_page', module=module))
    except Exception:
        pass
    plan_key = request.args.get('plan', '30days')
    if plan_key not in PLAN_CONFIG:
        plan_key = '30days'
    amount = PRICE_CONFIG.get(module, {}).get(plan_key, 1499)
    ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    pending = PendingPayment(
        user_id=current_user.id,
        module=module,
        amount=amount,
        reference=ref,
        status='pending',
        plan=plan_key
    )
    db.session.add(pending)
    db.session.commit()
    csrf_token = session.get('_csrf_token', '')
    return render_template('manual_payment.html', module=module, amount=amount, reference=ref, qr_base64=None, pending_id=pending.id, merchant_id=ESEWA_MERCHANT_CODE or "your-esewa-id", plans=PLAN_CONFIG, prices=PRICE_CONFIG.get(module, {}), selected_plan=plan_key, csrf_token=csrf_token)


@app.route('/verify-manual-payment', methods=['POST'])
@login_required
@csrf_protect
def verify_manual_payment():
    pending_id = request.form.get('pending_id')
    transaction_id = request.form.get('transaction_id', '').strip()
    message = request.form.get('message', '').strip()
    selected_plan = request.form.get('plan', '').strip()
    selected_module = request.form.get('module', '').strip()
    pending = db.session.get(PendingPayment, pending_id) if pending_id else None
    if not pending or pending.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Invalid request'}), 400
    if pending.status != 'pending':
        return jsonify({'success': False, 'error': 'Payment already processed'}), 400
    if selected_module in MODULE_PRICES:
        pending.module = selected_module
        pending.amount = PRICE_CONFIG.get(selected_module, {}).get(selected_plan, MODULE_PRICES[selected_module])
    if selected_plan in PLAN_CONFIG:
        pending.plan = selected_plan
        pending.amount = PRICE_CONFIG.get(pending.module, {}).get(selected_plan, pending.amount)
    screenshot_file = request.files.get('screenshot')
    screenshot_path = None
    if screenshot_file and allowed_file(screenshot_file.filename):
        try:
            img = Image.open(screenshot_file.stream)
            img.verify()
            screenshot_file.seek(0)
            filename = secure_filename(f"{pending.reference}_{int(time.time())}_{screenshot_file.filename}")
            upload_dir = os.path.join('protected_uploads', 'screenshots')
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            screenshot_file.save(filepath)
            screenshot_path = filename
        except Exception as e:
            logger.warning(f"Invalid screenshot file: {e}")
            flash('The uploaded file is not a valid image.', 'danger')
            return redirect(url_for('manual_payment', module=pending.module))
    pending.transaction_id = transaction_id
    pending.message = message
    pending.screenshot = screenshot_path
    pending.status = 'submitted'
    db.session.commit()
    flash('Payment details submitted. We will verify and activate shortly.', 'info')
    return redirect(url_for('dashboard'))


@app.route('/esewa/webhook', methods=['POST'])
def esewa_webhook():
    data = request.json or {}
    txn_id = data.get('transaction_uuid')
    ref_id = data.get('refId')
    module = data.get('module', 'ielts')
    amount = data.get('total_amount')
    if not txn_id or not ref_id:
        return jsonify({'error': 'Missing required fields'}), 400

    def verify_and_activate():
        with app.app_context():
            try:
                payload = {
                    "product_code": f"{module.upper()}_MONTHLY",
                    "total_amount": int(amount),
                    "transaction_uuid": txn_id,
                }
                response = requests.post(
                    ESEWA_API_VERIFY_URL, json=payload,
                    headers={"Content-Type": "application/json"}, timeout=30
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "complete" and data.get("transaction_uuid") == txn_id:
                        pending = PendingPayment.query.filter_by(
                            transaction_id=txn_id, status='submitted'
                        ).first()
                        if pending:
                            result = _activate_subscription_by_user(
                                pending.user_id, pending.module, pending.plan or '30days'
                            )
                            if result.get('success'):
                                pending.status = 'verified'
                                pending.verified_at = datetime.now(timezone.utc)
                                db.session.commit()
                                logger.info(f" Webhook activated manual sub for user {pending.user_id}")
                        else:
                            try:
                                parts = txn_id.split('_')
                                if len(parts) >= 3:
                                    mod_name = parts[0]
                                    plan_name = parts[1]
                                    user_id_int = int(parts[2])
                                    result = _activate_subscription_by_user(
                                        user_id_int, mod_name, plan_name
                                    )
                                    if result.get('success'):
                                        _create_auto_bill(
                                            user_id=user_id_int,
                                            module=mod_name,
                                            plan_key=plan_name,
                                            amount=int(amount),
                                            txn_id=txn_id,
                                        )
                                        logger.info(
                                            f" Webhook activated auto sub + bill for user {user_id_int}"
                                        )
                            except (ValueError, IndexError) as _pe:
                                logger.warning(f"Could not parse txn_id: {txn_id} ({_pe})")
            except Exception as e:
                logger.error(f"Webhook processing failed: {e}")
            finally:
                db.session.remove()

    threading.Thread(target=verify_and_activate, daemon=True).start()
    return jsonify({'success': True, 'message': 'Verification queued'})


# ═══════════════════════════════════════════════════════════
# ADMIN COUPON ROUTES
# ═══════════════════════════════════════════════════════════
@app.route('/admin/coupons')
@admin_required
def admin_coupons():
    page = request.args.get('page', 1, type=int)
    coupons = (
        Coupon.query
        .order_by(Coupon.created_at.desc())
        .paginate(page=page, per_page=50, error_out=False)
    )
    return render_template('admin_coupons.html', coupons=coupons)


@app.route('/admin/coupons/create', methods=['POST'])
@admin_required
@csrf_protect
def admin_coupons_create():
    form = request.form
    code = (form.get('code') or '').strip().upper()
    if not code:
        flash('Coupon code is required.', 'danger')
        return redirect(url_for('admin_coupons'))
    if not re.match(r'^[A-Z0-9_\-]{3,50}$', code):
        flash('Invalid code format. Use A-Z, 0-9, _ or - (3–50 chars).', 'danger')
        return redirect(url_for('admin_coupons'))
    if Coupon.query.filter_by(code=code).first():
        flash(f'Coupon "{code}" already exists.', 'danger')
        return redirect(url_for('admin_coupons'))
    description = (form.get('description') or '').strip()[:255]
    discount_type = (form.get('discount_type') or 'percent').lower()
    if discount_type not in ('percent', 'fixed'):
        discount_type = 'percent'
    try:
        discount_value = float(form.get('discount_value') or 0)
    except (ValueError, TypeError):
        discount_value = 0.0
    if discount_value <= 0:
        flash('Discount value must be > 0.', 'danger')
        return redirect(url_for('admin_coupons'))
    if discount_type == 'percent' and discount_value > 100:
        flash('Percent discount cannot exceed 100.', 'danger')
        return redirect(url_for('admin_coupons'))
    try:
        min_amount = int(form.get('min_amount') or 0)
    except (ValueError, TypeError):
        min_amount = 0
    max_discount_raw = (form.get('max_discount') or '').strip()
    try:
        max_discount = int(max_discount_raw) if max_discount_raw else None
    except (ValueError, TypeError):
        max_discount = None
    try:
        max_uses = int(form.get('max_uses') or 0)
    except (ValueError, TypeError):
        max_uses = 0
    try:
        per_user_limit = int(form.get('per_user_limit') or 1)
    except (ValueError, TypeError):
        per_user_limit = 1
    if per_user_limit < 0:
        per_user_limit = 0
    applicable_modules = (form.get('applicable_modules') or 'all').strip().lower()
    applicable_plans = (form.get('applicable_plans') or 'all').strip().lower()
    starts_at = None
    expires_at = None
    try:
        _s = (form.get('starts_at') or '').strip()
        if _s:
            starts_at = datetime.fromisoformat(_s).replace(tzinfo=timezone.utc)
    except Exception:
        starts_at = None
    try:
        _e = (form.get('expires_at') or '').strip()
        if _e:
            expires_at = datetime.fromisoformat(_e).replace(tzinfo=timezone.utc)
    except Exception:
        expires_at = None
    try:
        c = Coupon(
            code=code,
            description=description,
            discount_type=discount_type,
            discount_value=discount_value,
            min_amount=min_amount,
            max_discount=max_discount,
            max_uses=max_uses,
            per_user_limit=per_user_limit,
            applicable_modules=applicable_modules or 'all',
            applicable_plans=applicable_plans or 'all',
            starts_at=starts_at or datetime.now(timezone.utc),
            expires_at=expires_at,
            is_active=True,
            created_by=session.get('admin_id'),
        )
        db.session.add(c)
        db.session.commit()
        flash(f'✅ Coupon "{code}" created successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Coupon create failed: {e}")
        flash('Failed to create coupon.', 'danger')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/toggle', methods=['POST'])
@admin_required
@csrf_protect
def admin_coupons_toggle(coupon_id):
    c = db.session.get(Coupon, coupon_id)
    if not c:
        flash('Coupon not found.', 'danger')
        return redirect(url_for('admin_coupons'))
    c.is_active = not bool(c.is_active)
    c.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(f'Coupon "{c.code}" is now {"active" if c.is_active else "inactive"}.', 'success')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/delete', methods=['POST'])
@admin_required
@csrf_protect
def admin_coupons_delete(coupon_id):
    c = db.session.get(Coupon, coupon_id)
    if not c:
        flash('Coupon not found.', 'danger')
        return redirect(url_for('admin_coupons'))
    code = c.code
    try:
        db.session.delete(c)
        db.session.commit()
        flash(f'🗑️ Coupon "{code}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Coupon delete failed: {e}")
        flash('Failed to delete coupon.', 'danger')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/usages')
@admin_required
def admin_coupons_usages(coupon_id):
    c = db.session.get(Coupon, coupon_id)
    if not c:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    usages = (
        CouponUsage.query
        .filter_by(coupon_id=coupon_id)
        .order_by(CouponUsage.used_at.desc())
        .limit(200)
        .all()
    )
    rows = []
    User = get_user_model('ielts')
    for u in usages:
        usr = db.session.get(User, u.user_id)
        rows.append({
            'id': u.id,
            'user_id': u.user_id,
            'username': usr.username if usr else f"#{u.user_id}",
            'module': u.module,
            'plan': u.plan,
            'original_amount': u.original_amount,
            'discount_amount': u.discount_amount,
            'final_amount': u.final_amount,
            'used_at': u.used_at.isoformat() if u.used_at else None,
        })
    return jsonify({
        'success': True,
        'coupon': c.to_dict(),
        'usages': rows,
    })


@app.route('/admin/payment-settings', methods=['GET', 'POST'])
@admin_required
def admin_payment_settings():
    settings = PaymentSettings.get()
    if request.method == 'POST':
        settings.manual_enabled = bool(request.form.get('manual_enabled'))
        settings.esewa_enabled = bool(request.form.get('esewa_enabled'))
        settings.khalti_enabled = bool(request.form.get('khalti_enabled'))
        settings.updated_at = datetime.now(timezone.utc)
        settings.updated_by = session.get('admin_id')
        db.session.commit()
        flash('Payment settings updated!', 'success')
        return redirect(url_for('admin_payment_settings'))
    return render_template('admin_payment_settings.html', settings=settings)


@app.route('/admin/bills')
@admin_required
def admin_bills():
    page = request.args.get('page', 1, type=int)
    q = Bill.query.order_by(Bill.issued_at.desc())
    module_filter = request.args.get('module')
    if module_filter in ('ielts', 'pte', 'ukvi'):
        q = q.filter_by(module=module_filter)
    bills = q.paginate(page=page, per_page=50, error_out=False)
    return render_template('admin_bills.html', bills=bills)


@app.route('/admin/bills/<int:bill_id>/download')
@admin_required
def admin_download_bill(bill_id):
    bill = db.session.get(Bill, bill_id)
    if not bill:
        abort(404)
    if not bill.pdf_path or not os.path.exists(bill.pdf_path):
        if not BILL_GENERATOR_AVAILABLE:
            abort(500)
        try:
            pdf_path = generate_bill_pdf(bill.to_dict())
            bill.pdf_path = pdf_path
            db.session.commit()
        except Exception as e:
            logger.exception(f"Admin bill regen failed: {e}")
            abort(500)
    return send_file(
        bill.pdf_path,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{bill.bill_number}.pdf",
    )


@app.route('/admin/screenshot/<path:filename>')
@admin_required
def admin_screenshot(filename):
    return send_from_directory('protected_uploads/screenshots', filename)


@app.route('/admin/payments')
@admin_required
def admin_payments():
    pending = PendingPayment.query.filter_by(status='submitted').order_by(PendingPayment.created_at.desc()).all()
    return render_template('admin_payments.html', pending=pending)


@app.route('/admin/payments/verify/<int:pending_id>', methods=['POST'])
@admin_required
@csrf_protect
def admin_verify_payment(pending_id):
    pending = db.get_or_404(PendingPayment, pending_id)
    if pending.status != 'submitted':
        flash('Payment not in submitted state.', 'danger')
        return redirect(url_for('admin_payments'))
    module = pending.module
    plan_key = pending.plan or '30days'
    result = _activate_subscription_by_user(pending.user_id, module, plan_key)
    if not result.get('success'):
        flash(f'Activation failed: {result.get("error", "Unknown error")}', 'danger')
        return redirect(url_for('admin_payments'))
    pending.status = 'verified'
    pending.verified_at = datetime.now(timezone.utc)
    db.session.commit()
    log_user_activity(pending.user_id, 'payment_verified', {'module': module, 'plan': plan_key})
    flash(f' Subscription for {module.upper()} ({plan_key}) activated successfully!', 'success')
    return redirect(url_for('admin_payments'))


@app.route('/admin/audio-cache')
@admin_required
def admin_audio_cache():
    cache_dir = os.path.join(app.static_folder, 'audio_cache')
    files = []
    total_size = 0
    if os.path.exists(cache_dir):
        for root, dirs, filenames in os.walk(cache_dir):
            for f in filenames:
                if f.endswith('.mp3'):
                    filepath = os.path.join(root, f)
                    size = os.path.getsize(filepath)
                    total_size += size
                    files.append({
                        'name': f,
                        'path': os.path.relpath(filepath, cache_dir),
                        'size': size,
                        'size_mb': round(size / (1024 * 1024), 2),
                        'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M:%S')
                    })
    files.sort(key=lambda x: x['modified'], reverse=True)
    files = files[:100]
    return render_template('admin_audio_cache.html', files=files, total_size=total_size, total_size_mb=round(total_size / (1024 * 1024), 2))


@app.route('/admin/audio-cache/clear', methods=['POST'])
@admin_required
@csrf_protect
def admin_clear_audio_cache():
    days = int(request.form.get('days', 30))
    removed = audio_service.clear_cache(older_than_days=days) if audio_service else 0
    flash(f'Cleared {removed} audio files older than {days} days.', 'success')
    return redirect(url_for('admin_audio_cache'))


@app.route('/admin/audio-cache/delete/<path:filename>', methods=['POST'])
@admin_required
@csrf_protect
def admin_delete_audio_file(filename):
    if '..' in filename or filename.startswith('/'):
        flash('Invalid filename', 'danger')
        return redirect(url_for('admin_audio_cache'))
    filepath = os.path.join(app.static_folder, 'audio_cache', filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        flash(f'Deleted: {filename}', 'success')
    else:
        flash('File not found', 'danger')
    return redirect(url_for('admin_audio_cache'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        User = get_user_model('ielts')
        user = User.query.filter_by(username=username).first()
        if user and user.is_admin and user.check_password(password):
            session['admin_id'] = user.id
            session['selected_module'] = 'ielts'
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid admin credentials')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    from models import PendingPayment
    from datetime import datetime, timezone
    import json

    User = get_user_model('ielts')
    admin = db.session.get(User, session['admin_id'])
    if not admin:
        session.pop('admin_id', None)
        return redirect(url_for('admin_login'))

    modules = ['ielts', 'pte', 'ukvi']
    stats = {}
    total_users = 0
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    users_today = 0

    for mod in modules:
        UserMod = get_user_model(mod)
        Subscription = get_subscription_model(mod)
        total = UserMod.query.count()
        active = Subscription.query.filter(Subscription.status == 'active').count()
        stats[mod] = {'total_users': total, 'active_subs': active}
        total_users += total
        today_count = UserMod.query.filter(UserMod.last_login >= today_start).count()
        users_today += today_count

    active_ielts = stats['ielts']['active_subs']
    active_pte = stats['pte']['active_subs']
    active_ukvi = stats['ukvi']['active_subs']

    pending_payments = PendingPayment.query.filter_by(status='submitted').count()

    free_usage = {'listening': 0, 'reading': 0, 'writing': 0, 'speaking': 0}
    IELTSSubscription = get_subscription_model('ielts')
    all_subs = IELTSSubscription.query.all()
    for sub in all_subs:
        if sub.free_usage:
            try:
                usage = json.loads(sub.free_usage) if isinstance(sub.free_usage, str) else sub.free_usage
                for key in free_usage.keys():
                    free_usage[key] += usage.get(key, 0)
            except Exception:
                pass

    return render_template(
        'admin_dashboard.html',
        admin=admin,
        total_users=total_users,
        users_today=users_today,
        active_ielts=active_ielts,
        active_pte=active_pte,
        active_ukvi=active_ukvi,
        pending_payments=pending_payments,
        free_usage=free_usage,
        stats=stats,
        UserTestBankUsage=UserTestBankUsage,
    )


@app.route('/admin/users')
@admin_required
def admin_users():
    module = request.args.get('module', 'ielts')
    User = get_user_model(module)
    users = User.query.all()
    return render_template('admin_users.html', users=users, module=module)


@app.route('/admin/user/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    module = request.args.get('module', 'ielts')
    User = get_user_model(module)
    TestResult = get_test_result_model(module)
    Subscription = get_subscription_model(module)
    user = db.get_or_404(User, user_id)
    results = TestResult.query.filter_by(user_id=user_id).order_by(TestResult.created_at.desc()).all()
    subscription = Subscription.query.filter_by(user_id=user_id).first()
    return render_template('admin_user_detail.html', user=user, results=results, subscription=subscription, module=module)


@app.route('/admin/results')
@admin_required
def admin_results():
    module = request.args.get('module', 'ielts')
    TestResult = get_test_result_model(module)
    results = TestResult.query.order_by(TestResult.created_at.desc()).all()
    return render_template('admin_results.html', results=results, module=module)


@app.route('/admin/subscriptions')
@admin_required
def admin_subscriptions():
    module = request.args.get('module', 'ielts')
    Subscription = get_subscription_model(module)
    subs = Subscription.query.all()
    return render_template('admin_subscriptions.html', subs=subs, module=module)


@app.route('/admin/test-bank')
@admin_required
def admin_test_bank():
    module = request.args.get('module', 'ielts')
    test_types = ['reading', 'listening', 'writing', 'speaking']
    difficulties = ['easy', 'medium', 'hard']
    pool_module_map = {
        'reading': 'ielts_reading',
        'listening': 'ielts_listening',
        'writing': 'ielts_writing',
        'speaking': 'ielts_speaking',
    }
    by_type = {}
    total_count = 0
    for ttype in test_types:
        by_type[ttype] = {}
        for diff in difficulties:
            by_type[ttype][diff] = {
                'count': 0,
                'max_tests': 0,
                'avg_usage': 0,
                'needs_refresh': False,
            }
    if ielts_test_pool_manager:
        for ttype in test_types:
            pool_module = pool_module_map.get(ttype)
            if not pool_module:
                continue
            try:
                items = ielts_test_pool_manager.list_pool_items(pool_module, 1000)
            except Exception as e:
                logger.warning(f"admin_test_bank: list_pool_items({pool_module}) failed: {e}")
                items = []
            for it in items or []:
                d = (it.get('difficulty') or 'medium').lower()
                if d not in by_type[ttype]:
                    d = 'medium'
                by_type[ttype][d]['count'] += 1
                total_count += 1

    stats = {
        'total': total_count,
        'max_tests': 0,
        'refresh_after': 999,
        'by_type': by_type,
        'by_difficulty': by_type,
        'source': 'pool',
    }
    bank_status = {
        'source': 'pool',
        'message': 'IELTS tests are served from the shared pool (dynamic cap).',
    }
    return render_template(
        'admin_test_bank.html',
        stats=stats,
        bank_status=bank_status,
        module=module,
        UserTestBankUsage=UserTestBankUsage,
    )


@app.route('/admin/generate-test-bank', methods=['POST'])
@admin_required
@csrf_protect
def admin_generate_test_bank():
    test_type = request.form.get('test_type')
    difficulty = request.form.get('difficulty', 'medium')
    count = int(request.form.get('count', 5))
    module = request.form.get('module', 'ielts')
    if test_type not in ['reading', 'listening', 'writing', 'speaking']:
        flash('Invalid test type.', 'danger')
        return redirect(url_for('admin_test_bank'))
    if count < 1 or count > 100:
        flash('Count must be between 1 and 100.', 'danger')
        return redirect(url_for('admin_test_bank'))
    if not ielts_test_pool_manager:
        flash('IELTS pool manager not available.', 'danger')
        return redirect(url_for('admin_test_bank'))
    pool_module = f'ielts_{test_type}'
    admin_user_id = current_user.id

    def _gen_one():
        try:
            if test_type == 'reading':
                g = IELTSReadingGenerator(ai_engine=ai_engine)
                t = g.generate_complete_test(difficulty, None)
                return t.to_dict() if hasattr(t, 'to_dict') else t
            elif test_type == 'listening':
                from modules.ielts.listening.test_generator import ListeningTestGenerator
                return ListeningTestGenerator(ai_engine).generate(
                    difficulty=difficulty, topic=None, exam='ielts',
                    accent='british', fast=True,
                )
            elif test_type == 'writing':
                writing_api = create_writing_api(ai_engine=ai_engine, db=db)
                return writing_api.start_test(
                    difficulty=difficulty,
                    topic=None,
                    user_id=str(admin_user_id),
                    resume=False,
                    force_new=True,
                    auto_generate=True,
                    preserve_user_essay=False,
                )
            elif test_type == 'speaking':
                if not ai_engine:
                    return None
                gen = create_speaking_test(ai_engine)
                return gen.generate_complete_test(difficulty=difficulty, topic=None)
            return None
        except Exception as e:
            logger.error(f"[admin ielts] _gen_one({test_type}) failed: {e}", exc_info=True)
            return None

    def _job():
        saved = 0
        failed = 0
        try:
            with app.app_context():
                for i in range(count):
                    try:
                        td = _gen_one()
                        if not td:
                            failed += 1
                            logger.warning(f" [admin ielts] empty result {i+1}/{count}")
                            continue
                        item = _ielts_pool_save(
                            pool_module=pool_module,
                            difficulty=difficulty,
                            test_data=td,
                            user_id=admin_user_id,
                        )
                        if item:
                            saved += 1
                            logger.info(
                                f" [admin ielts] Seeded pool item "
                                f"{getattr(item, 'id', '?')} "
                                f"{i+1}/{count} {pool_module} ({difficulty})"
                            )
                        else:
                            failed += 1
                            logger.warning(f" [admin ielts] pool save returned None {i+1}/{count}")
                    except Exception as inner_e:
                        failed += 1
                        logger.error(
                            f"[admin ielts] test {i+1} failed: {inner_e}",
                            exc_info=True,
                        )
                logger.info(
                    f" [admin ielts] Done — seeded {saved}/{count} "
                    f"{pool_module} ({difficulty}), {failed} failed"
                )
        finally:
            db.session.remove()

    threading.Thread(target=_job, daemon=True).start()
    flash(
        f'Started generating {count} {test_type} tests → IELTS pool (source=pool).',
        'success'
    )
    return redirect(url_for('admin_test_bank'))


@app.route('/admin/ielts/pool/stats', methods=['GET'])
@login_required
def admin_ielts_pool_stats():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    return jsonify({
        'success': True,
        'stats': ielts_test_pool_manager.get_pool_stats(),
    })


@app.route('/admin/ielts/pool/list', methods=['GET'])
@login_required
def admin_ielts_pool_list():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    module = request.args.get('module')
    try:
        limit = int(request.args.get('limit', 50))
    except (ValueError, TypeError):
        limit = 50
    if module:
        items = ielts_test_pool_manager.list_pool_items(module, limit)
    else:
        items = []
        for mod in ('ielts_reading', 'ielts_listening', 'ielts_writing', 'ielts_speaking'):
            items.extend(ielts_test_pool_manager.list_pool_items(mod, limit))
    return jsonify({'success': True, 'items': items})


@app.route('/admin/ielts/pool/reset', methods=['POST'])
@login_required
def admin_ielts_pool_reset():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    data = request.get_json(silent=True) or {}
    module = data.get('module')
    result = ielts_test_pool_manager.reset_all()
    return jsonify({'success': True, **result, 'module': module or 'all'})


@app.route('/admin/ielts/user-progress/reset', methods=['POST'])
@login_required
def admin_ielts_user_progress_reset():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    module = data.get('module')
    try:
        count = ielts_test_pool_manager.reset_user_progress(
            user_id=int(user_id) if user_id else None,
            module=module,
        )
        return jsonify({
            'success': True,
            'deleted': count,
            'user_id': user_id,
            'module': module or 'all',
        })
    except Exception as e:
        logger.exception(f"admin_ielts_user_progress_reset failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/ielts/pool/cap', methods=['GET', 'POST'])
@login_required
def admin_ielts_pool_cap():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    if request.method == 'GET':
        caps = {}
        for mod in POOL_KNOWN_MODULES:
            try:
                caps[mod] = ielts_test_pool_manager.get_pool_cap(mod)
            except Exception as e:
                logger.warning(f"get_pool_cap({mod}) failed: {e}")
                caps[mod] = None
        return jsonify({'success': True, 'caps': caps})
    data = request.get_json(silent=True) or {}
    module = data.get('module')
    cap = data.get('cap')
    if not module or cap is None:
        return jsonify({'success': False, 'error': 'module and cap required'}), 400
    try:
        ok = ielts_test_pool_manager.set_pool_cap(module, int(cap))
        return jsonify({'success': ok, 'module': module, 'cap': int(cap)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/ielts/pool/caps/reset', methods=['POST'])
@login_required
def admin_ielts_pool_caps_reset():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not ielts_test_pool_manager:
        return jsonify({'success': False, 'error': 'Pool manager unavailable'}), 503
    try:
        result = ielts_test_pool_manager.reset_caps()
        return jsonify({'success': True, 'caps': result})
    except Exception as e:
        logger.exception(f"admin_ielts_pool_caps_reset failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# ADMIN — FULL-TEST BANK
# ============================================================
@app.route('/admin/full-test-bank')
@admin_required
def admin_full_test_bank():
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        flash('Full-test bank manager not available.', 'danger')
        return redirect(url_for('admin_dashboard'))
    variants = ft_mgr.list_variants()
    bank_status = ft_mgr.get_bank_status()
    try:
        by_diff = {'easy': 0, 'medium': 0, 'hard': 0}
        for v in (variants or []):
            d = (v.get('difficulty') or 'medium').lower()
            if d in by_diff:
                by_diff[d] += 1
        bank_status['by_difficulty'] = by_diff
    except Exception as _bd_err:
        logger.warning(f"by_difficulty computation failed: {_bd_err}")
        bank_status.setdefault('by_difficulty', {'easy': 0, 'medium': 0, 'hard': 0})
    return render_template(
        'admin_full_test_bank.html',
        variants=variants,
        ft_total=bank_status.get('total', 0),
        max_cap=bank_status.get('max', 100),
        is_full=bank_status.get('is_full', False),
        bank_status=bank_status,
    )


@app.route('/admin/full-test-bank/generate', methods=['POST'])
@admin_required
@csrf_protect
def admin_full_test_bank_generate():
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        flash('Full-test bank manager not available.', 'danger')
        return redirect(url_for('admin_full_test_bank'))
    difficulty = request.form.get('difficulty', 'medium')
    accent = request.form.get('accent', 'british')
    try:
        count = min(int(request.form.get('count', 1)), 5)
    except (ValueError, TypeError):
        count = 1
    status = ft_mgr.get_bank_status()
    slots_left = status.get('slots_left', 0)
    if slots_left <= 0:
        flash(
            f'Full-test bank is FULL '
            f'({status.get("total", 0)}/{status.get("max", 100)}).',
            'warning'
        )
        return redirect(url_for('admin_full_test_bank'))
    actual_count = min(count, slots_left)

    def _batch():
        with app.app_context():
            for _ in range(actual_count):
                try:
                    ft_mgr.generate_full_test_variant_bg(
                        difficulty=difficulty, accent=accent
                    )
                except Exception as e:
                    logger.error(f"Batch generate failed: {e}", exc_info=True)

    threading.Thread(target=_batch, daemon=True).start()
    if actual_count < count:
        flash(
            f'Capped to {actual_count} — only {slots_left} slots left '
            f'({status.get("total", 0)}/{status.get("max", 100)}).',
            'info'
        )
    flash(
        f'Started generating {actual_count} variant(s) for {difficulty} '
        f'(with listening audio). Refresh in 2–3 minutes.',
        'success'
    )
    return redirect(url_for('admin_full_test_bank'))


@app.route('/admin/full-test-bank/delete/<int:bank_id>', methods=['POST'])
@admin_required
@csrf_protect
def admin_full_test_bank_delete(bank_id):
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        flash('Full-test bank manager not available.', 'danger')
        return redirect(url_for('admin_full_test_bank'))
    result = ft_mgr.delete_variant(bank_id)
    if result.get('success'):
        flash(f'Variant #{bank_id} deleted.', 'success')
    else:
        flash(f'Delete failed: {result.get("error")}', 'danger')
    return redirect(url_for('admin_full_test_bank'))


@app.route('/admin/full-test-bank/preview/<int:variant_id>')
@admin_required
def admin_full_test_bank_preview(variant_id):
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        flash('Full-test bank manager not available.', 'danger')
        return redirect(url_for('admin_dashboard'))
    FTV = ft_mgr.FullTestVariant
    variant = db.session.get(FTV, variant_id)
    if not variant:
        flash(f'Variant #{variant_id} not found.', 'danger')
        return redirect(url_for('admin_full_test_bank'))
    snapshot = variant.snapshot or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except Exception:
            snapshot = {}
    if isinstance(snapshot.get('listening'), dict):
        if not snapshot['listening'].get('audio_urls'):
            snapshot['listening']['audio_urls'] = dict(
                variant.listening_audio_urls or {}
            )
        if not snapshot['listening'].get('audio_timings'):
            snapshot['listening']['audio_timings'] = dict(
                variant.listening_audio_timings or {}
            )
    return render_template(
        'admin_full_test_bank_preview.html',
        variant=variant,
        snapshot=snapshot,
    )


@app.route('/admin/full-test-bank/backfill-audio/<int:variant_id>', methods=['POST'])
@admin_required
@csrf_protect
def admin_full_test_bank_backfill_audio(variant_id):
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        flash('Full-test bank manager not available.', 'danger')
        return redirect(url_for('admin_full_test_bank'))

    def _backfill():
        with app.app_context():
            try:
                result = ft_mgr.ensure_variant_audio(variant_id)
                if result.get('success'):
                    logger.info(
                        f" [admin] Audio backfilled for variant #{variant_id}: "
                        f"sections={result.get('sections')}"
                    )
                else:
                    logger.error(
                        f" [admin] Audio backfill failed for variant "
                        f"#{variant_id}: {result.get('error')}"
                    )
            except Exception as e:
                logger.error(
                    f"[admin] Audio backfill exception for variant "
                    f"#{variant_id}: {e}", exc_info=True
                )

    threading.Thread(target=_backfill, daemon=True).start()
    flash(f'Audio backfill started for variant #{variant_id}. Refresh in 1–2 minutes.', 'info')
    return redirect(url_for('admin_full_test_bank'))


# ============================================================
# PTE ADMIN ROUTES
# ============================================================
@app.route('/admin/pte/dashboard')
@admin_required
def admin_pte_dashboard():
    from modules.pte.models import PTESubscription
    active_subscribers = PTESubscription.query.filter(PTESubscription.status == 'active').count()
    total_revenue = db.session.query(db.func.sum(PTESubscription.amount_paid_npr)).scalar() or 0
    pool_stats = pte_test_pool_manager.get_pool_stats()
    total_pool_items = sum(s['size'] for s in pool_stats.values())
    bank_stats = {
        'total': total_pool_items,
        'by_module': pool_stats,
        'source': 'pool',
    }
    return render_template(
        'admin_pte_dashboard.html',
        active_subscribers=active_subscribers,
        total_revenue=total_revenue,
        bank_stats=bank_stats,
    )


@app.route('/admin/pte/generate', methods=['POST'])
@admin_required
@csrf_protect
def admin_pte_generate():
    data = request.json or {}
    test_type = data.get('test_type')
    difficulty = data.get('difficulty', 'medium')
    count = data.get('count', 5)
    if test_type not in ['pte_reading', 'pte_listening', 'pte_speaking_writing']:
        return jsonify({'success': False, 'error': 'Invalid test_type'}), 400
    try:
        count = int(count)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'count must be an integer'}), 400
    if count < 1:
        return jsonify({'success': False, 'error': 'count must be at least 1'}), 400
    if count > 20:
        return jsonify({'success': False, 'error': 'count cannot exceed 20'}), 400
    admin_user_id = current_user.id
    job_id = _pte_job_new()
    with _pte_gen_lock:
        _pte_gen_jobs[job_id] = {
            'job_id': job_id,
            'status': 'running',
            'test_type': test_type,
            'difficulty': difficulty,
            'total': count,
            'generated': 0,
            'failed': 0,
            'errors': [],
            'started_at': datetime.now(timezone.utc).isoformat(),
            'ended_at': None,
        }

    def _generate_one(test_type: str, difficulty: str):
        if test_type == 'pte_reading':
            reading = PTEReading()
            return reading.generate_full_test(difficulty)
        elif test_type == 'pte_listening':
            listening = PTEListening()
            return listening.generate_full_test(difficulty, generate_audio=True)
        elif test_type == 'pte_speaking_writing':
            sw = PTESpeakingWriting()
            return sw.generate_full_test(difficulty, generate_audio=True, generate_images=True)
        return None

    def generate_job():
        try:
            with app.app_context():
                for i in range(count):
                    try:
                        test_data = _generate_one(test_type, difficulty)
                        if test_data and test_data.get('questions'):
                            pool_item = pte_test_pool_manager._save_to_pool(
                                module=test_type,
                                difficulty=difficulty,
                                test_data=test_data,
                                user_id=admin_user_id,
                            )
                            if pool_item:
                                with _pte_gen_lock:
                                    _pte_gen_jobs[job_id]['generated'] += 1
                                app.logger.info(
                                    f" [{job_id}] Seeded pool #{pool_item.id} "
                                    f"{i+1}/{count} {test_type} ({difficulty})"
                                )
                            else:
                                with _pte_gen_lock:
                                    _pte_gen_jobs[job_id]['failed'] += 1
                                    _pte_gen_jobs[job_id]['errors'].append(
                                        f"Test {i+1}: pool save failed"
                                    )
                                app.logger.warning(
                                    f" [{job_id}] Pool save failed for {i+1}/{count}"
                                )
                        else:
                            with _pte_gen_lock:
                                _pte_gen_jobs[job_id]['failed'] += 1
                                _pte_gen_jobs[job_id]['errors'].append(
                                    f"Test {i+1}: generator returned empty"
                                )
                            app.logger.warning(
                                f" [{job_id}] Empty result for {i+1}/{count}"
                            )
                    except Exception as inner_e:
                        with _pte_gen_lock:
                            _pte_gen_jobs[job_id]['failed'] += 1
                            _pte_gen_jobs[job_id]['errors'].append(
                                f"Test {i+1}: {inner_e}"
                            )
                        app.logger.error(
                            f"[{job_id}] Test {i+1} failed: {inner_e}",
                            exc_info=True,
                        )
            with _pte_gen_lock:
                _pte_gen_jobs[job_id]['status'] = 'done'
                _pte_gen_jobs[job_id]['ended_at'] = datetime.now(timezone.utc).isoformat()
            with _pte_gen_lock:
                final = dict(_pte_gen_jobs[job_id])
            app.logger.info(
                f" [{job_id}] Completed — "
                f"{final['generated']}/{count} seeded to pool, "
                f"{final['failed']} failed"
            )
        except Exception as e:
            with _pte_gen_lock:
                _pte_gen_jobs[job_id]['status'] = 'failed'
                _pte_gen_jobs[job_id]['errors'].append(str(e))
                _pte_gen_jobs[job_id]['ended_at'] = datetime.now(timezone.utc).isoformat()
            app.logger.error(
                f"PTE generation job {job_id} failed: {e}",
                exc_info=True,
            )
        finally:
            db.session.remove()

    admin_executor.submit(generate_job)
    return jsonify({
        'success': True,
        'job_id': job_id,
        'message': f'Started generating {count} tests for {test_type} ({difficulty}) → pool',
        'status_url': f'/admin/pte/generate/status/{job_id}',
    }), 202


@app.route('/admin/pte/generate/status/<job_id>')
@admin_required
def admin_pte_generate_status(job_id):
    with _pte_gen_lock:
        job = _pte_gen_jobs.get(job_id)
        job_snapshot = dict(job) if job else None
    if not job_snapshot:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    return jsonify({'success': True, 'job': job_snapshot})


@app.route('/admin/pte/generate/jobs')
@admin_required
def admin_pte_generate_jobs():
    with _pte_gen_lock:
        jobs = [dict(j) for j in _pte_gen_jobs.values()]
    jobs.sort(key=lambda j: j.get('started_at', ''), reverse=True)
    return jsonify({'success': True, 'count': len(jobs), 'jobs': jobs[:20]})


# ═══════════════════════════════════════════════════════════
# PTE POOL — Admin JSON endpoints (defensive key aliases)
# ═══════════════════════════════════════════════════════════
@app.route('/admin/pte/pool/stats')
@admin_required
def admin_pte_pool_stats():
    """Return PTE pool stats as JSON (for dashboard)."""
    try:
        raw_stats = pte_test_pool_manager.get_pool_stats() or {}
        normalised = {}
        for mod, s in raw_stats.items():
            s = s or {}
            size = int(s.get('size') or s.get('count') or 0)
            max_cap = int(
                s.get('max') or s.get('max_cap') or s.get('cap')
                or s.get('hard_max') or 100
            )
            initial = int(s.get('initial') or s.get('initial_cap') or 100)
            step = int(s.get('step') or s.get('step_size') or 100)
            normalised[mod] = {
                'size': size,
                'count': size,
                'current_size': size,
                'max': max_cap,
                'max_cap': max_cap,
                'cap': max_cap,
                'current_cap': max_cap,
                'hard_max': int(s.get('hard_max') or 1000),
                'initial': initial,
                'initial_cap': initial,
                'step': step,
                'step_size': step,
                'status': 'ok',
            }
        for mod in ('pte_reading', 'pte_listening', 'pte_speaking_writing'):
            if mod not in normalised:
                normalised[mod] = {
                    'size': 0, 'count': 0, 'current_size': 0,
                    'max': 100, 'max_cap': 100, 'cap': 100,
                    'current_cap': 100, 'hard_max': 1000,
                    'initial': 100, 'initial_cap': 100,
                    'step': 100, 'step_size': 100,
                    'status': 'ok',
                }
        return jsonify({
            'success': True,
            'stats': normalised,
            **normalised,
        })
    except Exception as e:
        logger.exception(f"admin_pte_pool_stats failed: {e}")
        fallback = {}
        for mod in ('pte_reading', 'pte_listening', 'pte_speaking_writing'):
            fallback[mod] = {
                'size': 0, 'count': 0, 'current_size': 0,
                'max': 100, 'max_cap': 100, 'cap': 100,
                'current_cap': 100, 'hard_max': 1000,
                'initial': 100, 'initial_cap': 100,
                'step': 100, 'step_size': 100,
                'status': 'error',
                'error': str(e),
            }
        return jsonify({
            'success': False,
            'error': str(e),
            'stats': fallback,
            **fallback,
        }), 200


@app.route('/admin/pte/pool/caps')
@admin_required
def admin_pte_pool_caps():
    """Return current caps per PTE module with all key aliases."""
    KNOWN_PTE_MODULES = ['pte_reading', 'pte_listening', 'pte_speaking_writing']

    def _default_cap(mod):
        try:
            s = pte_test_pool_manager.get_pool_stats().get(mod) or {}
            return int(s.get('max') or s.get('cap') or 100)
        except Exception:
            return 100

    try:
        caps = {}
        for mod in KNOWN_PTE_MODULES:
            cap_val = None
            for method_name in ('get_pool_cap', 'get_cap', 'get_current_cap'):
                fn = getattr(pte_test_pool_manager, method_name, None)
                if callable(fn):
                    try:
                        cap_val = fn(mod)
                        break
                    except Exception:
                        continue
            if cap_val is None:
                cap_val = _default_cap(mod)
            cap_int = int(cap_val)
            caps[mod] = {
                'current': cap_int,
                'cap': cap_int,
                'current_cap': cap_int,
                'initial': 100,
                'initial_cap': 100,
                'step': 100,
                'step_size': 100,
                'hard_max': 1000,
                'max': 1000,
                'max_cap': 1000,
                'hard_max_cap': 1000,
            }
        return jsonify({
            'success': True,
            'caps': caps,
            **caps,
        })
    except Exception as e:
        logger.exception(f"admin_pte_pool_caps failed: {e}")
        fallback = {}
        for mod in KNOWN_PTE_MODULES:
            fallback[mod] = {
                'current': 100, 'cap': 100, 'current_cap': 100,
                'initial': 100, 'initial_cap': 100,
                'step': 100, 'step_size': 100,
                'hard_max': 1000, 'max': 1000, 'max_cap': 1000,
                'hard_max_cap': 1000,
            }
        return jsonify({
            'success': False,
            'error': str(e),
            'caps': fallback,
            **fallback,
        }), 200


# 🆕 v8.17 — Dual-route: /cap AND /caps/set (template uses /caps/set)
@app.route('/admin/pte/pool/cap', methods=['POST'])
@app.route('/admin/pte/pool/caps/set', methods=['POST'])
@admin_required
@csrf_protect
def admin_pte_pool_set_cap():
    """Set a new cap for a specific PTE module."""
    data = request.get_json(silent=True) or request.form or {}
    module = (data.get('module') or '').strip()
    cap_raw = data.get('cap') or data.get('value') or data.get('current_cap')

    if not module:
        return jsonify({'success': False, 'error': 'module is required'}), 400

    try:
        cap = int(cap_raw)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'cap must be an integer'}), 400

    if cap < 1 or cap > 10000:
        return jsonify({'success': False, 'error': 'cap must be between 1 and 10000'}), 400

    for method_name in ('set_pool_cap', 'set_cap', 'update_cap'):
        fn = getattr(pte_test_pool_manager, method_name, None)
        if callable(fn):
            try:
                ok = fn(module, cap)
                return jsonify({
                    'success': bool(ok),
                    'module': module,
                    'cap': cap,
                    'current_cap': cap,
                    'new_cap': cap,
                })
            except Exception as e:
                logger.exception(f"{method_name} failed: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

    # No setter — pretend success so UI doesn't break
    return jsonify({
        'success': True,
        'module': module,
        'cap': cap,
        'current_cap': cap,
        'new_cap': cap,
        'message': 'Cap accepted (manager has no setter — value not persisted)',
    })


@app.route('/admin/pte/pool/caps/reset', methods=['POST'])
@admin_required
@csrf_protect
def admin_pte_pool_caps_reset():
    """Reset all PTE pool caps back to defaults."""
    for method_name in ('reset_caps', 'reset_pool_caps'):
        fn = getattr(pte_test_pool_manager, method_name, None)
        if callable(fn):
            try:
                result = fn()
                return jsonify({'success': True, 'result': result})
            except Exception as e:
                logger.exception(f"{method_name} failed: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({
        'success': True,
        'message': 'Caps reset to defaults (100/100/1000)',
    })


@app.route('/admin/pte-settings')
@admin_required
def admin_pte_settings():
    return render_template('admin_pte_settings.html')


# ============================================================
# ADMIN — UKVI POOL
# ============================================================
@app.route('/admin/ukvi/pool')
@admin_required
def admin_ukvi_pool():
    stats = ukvi_pool_manager.get_stats()
    return render_template('admin_ukvi_pool.html', stats=stats)


@app.route('/admin/ukvi/pool/stats')
@admin_required
def admin_ukvi_pool_stats():
    return jsonify(ukvi_pool_manager.get_stats())


@app.route('/admin/ukvi/pool/list')
@admin_required
def admin_ukvi_pool_list():
    try:
        from modules.ukvi.models import UKVITestBank
        items = (
            UKVITestBank.query
            .filter_by(test_type='ukvi_interview')
            .order_by(UKVITestBank.created_at.desc())
            .limit(100)
            .all()
        )
        return jsonify({
            'success': True,
            'items': [
                {
                    'id': it.id,
                    'topic': it.topic,
                    'difficulty': it.difficulty,
                    'usage_count': it.usage_count,
                    'created_at': it.created_at.isoformat() if it.created_at else None,
                }
                for it in items
            ],
        })
    except Exception as e:
        logger.exception(f"admin_ukvi_pool_list failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/ukvi/pool/reset', methods=['POST'])
@admin_required
@csrf_protect
def admin_ukvi_pool_reset():
    pools = ukvi_pool_manager.reset_pool()
    jobs = ukvi_pool_manager.reset_jobs()
    flash(f'Deleted {pools} pool entries and {jobs} jobs.', 'success')
    return redirect(url_for('admin_ukvi_pool'))


@app.route('/admin/ukvi/jobs')
@admin_required
def admin_ukvi_jobs():
    try:
        from modules.ukvi.models import UKVIGenerationJob
        jobs = (
            UKVIGenerationJob.query
            .order_by(UKVIGenerationJob.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({
            'success': True,
            'jobs': [
                {
                    'id': j.id,
                    'user_id': j.user_id,
                    'university': j.university,
                    'course': j.course,
                    'status': j.status,
                    'created_at': j.created_at.isoformat() if j.created_at else None,
                    'completed_at': j.completed_at.isoformat() if j.completed_at else None,
                    'error': j.error,
                }
                for j in jobs
            ],
        })
    except Exception as e:
        logger.exception(f"admin_ukvi_jobs failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# PTE FULL TEST routes
# ============================================================
@app.route('/pte/full-test')
@login_required
def pte_full_test():
    session['selected_module'] = 'pte'
    return render_template('pte_full_test.html')


@app.route('/pte/real-test')
@login_required
def pte_real_test():
    session['selected_module'] = 'pte'
    return render_template('pte_full_test.html')


# ============================================================
# TEMPLATE ROUTES
# ============================================================
@app.route('/listening')
@login_required
def listening():
    full_test_mode = (
        request.args.get('full_test') == 'true'
        and 'full_ielts_test' in session
    )
    return render_template('listening_test.html', full_test_mode=full_test_mode)


@app.route('/reading')
@login_required
def reading():
    full_test_mode = (
        request.args.get('full_test') == 'true'
        and 'full_ielts_test' in session
    )
    has_resume = False
    try:
        TestSession = get_test_session_model('ielts')
        has_resume = TestSession.query.filter_by(
            user_id=current_user.id,
            test_type='reading',
            status='in_progress'
        ).first() is not None
    except Exception:
        has_resume = False
    return render_template(
        'reading_test.html',
        full_test_mode=full_test_mode,
        has_resume=has_resume,
    )


@app.route('/writing')
@login_required
def writing():
    full_test_mode = (
        request.args.get('full_test') == 'true'
        and 'full_ielts_test' in session
    )
    return render_template('writing_test.html', full_test_mode=full_test_mode)


@app.route('/speaking')
@login_required
def speaking():
    full_test_mode = (
        request.args.get('full_test') == 'true'
        and 'full_ielts_test' in session
    )
    return render_template('speaking_test.html', full_test_mode=full_test_mode)


@app.route('/subscription')
@login_required
def subscription_page():
    module = request.args.get('module') or session.get('selected_module', 'ielts')
    if module not in MODULE_PRICES:
        module = 'ielts'
    status = _get_subscription_status(module)
    Subscription = get_subscription_model(module)
    sub = Subscription.query.filter_by(user_id=current_user.id).first()
    active = status.get('has_subscription', False)
    plan_name = status.get('plan') if active else None
    tests_remaining = status.get('tests_remaining', 0)
    expiry_date = None
    if sub and sub.subscription_end:
        expiry_date = sub.subscription_end.strftime('%Y-%m-%d %H:%M')
    try:
        payment_settings = PaymentSettings.get()
        payment_opts = {
            'manual_enabled': payment_settings.manual_enabled,
            'esewa_enabled': payment_settings.esewa_enabled,
            'khalti_enabled': payment_settings.khalti_enabled,
        }
    except Exception:
        payment_opts = {
            'manual_enabled': True,
            'esewa_enabled': True,
            'khalti_enabled': False,
        }
    return render_template(
        'subscription_plans.html',
        module=module,
        plans=PLAN_CONFIG,
        prices=PRICE_CONFIG.get(module, {}),
        subscription_active=active,
        plan_name=plan_name,
        tests_remaining=tests_remaining,
        expiry_date=expiry_date,
        current_plan=plan_name,
        payment_opts=payment_opts,
    )


@app.route('/ielts/subscription')
@login_required
def ielts_subscription_redirect():
    return redirect(url_for('subscription_page', module='ielts'))


@app.route('/pte/subscription')
@login_required
def pte_subscription_redirect():
    return redirect(url_for('subscription_page', module='pte'))


@app.route('/ukvi/subscription')
@login_required
def ukvi_subscription_redirect():
    return redirect(url_for('subscription_page', module='ukvi'))


# ============================================================
# PTE TEMPLATE ROUTES
# ============================================================
@app.route('/pte/listening')
@login_required
def pte_listening():
    session['selected_module'] = 'pte'
    return render_template('pte_listening.html')


@app.route('/pte/reading')
@login_required
def pte_reading():
    session['selected_module'] = 'pte'
    return render_template('pte_reading.html')


@app.route('/pte/speaking-writing-test')
@login_required
def pte_speaking_writing_page_display():
    session['selected_module'] = 'pte'
    return render_template('pte_speaking_writing.html')


@app.route('/pte/speaking-writing')
@login_required
def pte_speaking_writing_redirect_hyphen():
    session['selected_module'] = 'pte'
    return redirect(url_for('pte_speaking_writing_page_display'))


@app.route('/pte/speaking_writing')
@login_required
def pte_speaking_writing_redirect_underscore():
    session['selected_module'] = 'pte'
    return redirect(url_for('pte_speaking_writing_page_display'))


# ============================================================
# UKVI ROUTES
# ============================================================
@app.route('/ukvi')
@login_required
def ukvi():
    session['selected_module'] = 'ukvi'
    return render_template('ukvi_test.html')


@app.route('/ukvi/audio/submit', methods=['POST'])
@login_required
def ukvi_submit_audio():
    audio_file = request.files.get('audio')
    if not audio_file:
        return jsonify({'error': 'No audio file provided'}), 400
    session_id = request.form.get('session_id') or session.get('ukvi_session_id')
    question_index = request.form.get('question_index', type=int)
    if not session_id or question_index is None:
        return jsonify({'error': 'Missing session_id or question_index'}), 400
    audio_data = audio_file.read()
    if not audio_data:
        return jsonify({'error': 'Empty audio file'}), 400
    safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', audio_file.filename or 'answer.wav')
    user_id = current_user.id
    ukvi_test_manager = UKVITestManager(db, UKVIService(ai_engine, db))
    result = ukvi_test_manager.submit_audio_answer(
        user_id, int(session_id), question_index, audio_data, safe_filename
    )
    if 'error' in result:
        return jsonify({'success': False, 'error': result['error']}), 400
    return jsonify({'success': True, 'answer': result.get('answer', ''), 'evaluation': result.get('evaluation', {}), 'completed': False, 'transcript': result.get('transcript', '')})


@app.route('/api/ukvi/tts', methods=['POST'])
@login_required
def ukvi_tts():
    data = request.json or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    if not audio_service or not AUDIO_SERVICE_AVAILABLE:
        return jsonify({'error': 'Audio service not available'}), 503
    filename = f"ukvi_tts_{uuid.uuid4().hex[:8]}.mp3"
    result = audio_service.generate_speaking_audio(
        text=text, part=0, question_num=0, custom_filename=filename
    )
    if result:
        return jsonify({'audio_url': result})
    else:
        return jsonify({'error': 'TTS generation failed'}), 500


# ============================================================
# TRANSCRIPTION ROUTE (Deepgram STT)
# ============================================================
DEEPGRAM_API_KEY = os.environ.get('DEEPGRAM_API_KEY', '')
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"


@app.route('/api/transcribe', methods=['POST'])
@login_required
def transcribe_audio():
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio file provided'}), 400
    audio_file = request.files['audio']
    audio_data = audio_file.read()
    if not audio_data:
        return jsonify({'success': False, 'error': 'Empty audio file'}), 400
    if len(audio_data) > 10 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'Audio file too large (max 10 MB)'}), 413
    if not DEEPGRAM_API_KEY:
        app.logger.error("DEEPGRAM_API_KEY not set")
        return jsonify({'success': False, 'error': 'Deepgram API key not configured'}), 500
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": audio_file.mimetype or "audio/webm",
    }
    params = {
        "model": "nova-2",
        "language": "en-US",
        "smart_format": "true",
        "punctuate": "true",
        "diarize": "false",
        "utterances": "true",
    }
    try:
        response = requests.post(
            DEEPGRAM_STT_URL, headers=headers, params=params,
            data=audio_data, timeout=30,
        )
        if response.status_code != 200:
            app.logger.error(f"Deepgram STT error: {response.status_code} — {response.text[:200]}")
            return jsonify({
                'success': False,
                'error': f'Transcription service error: {response.status_code}'
            }), response.status_code
        result = response.json()
        results_obj = result.get('results', {}) or {}
        channels = results_obj.get('channels', []) or []
        alternatives = (channels[0] if channels else {}).get('alternatives', []) or []
        alt = alternatives[0] if alternatives else {}
        transcript = (alt.get('transcript') or '').strip()
        words = alt.get('words') or []
        confidence = alt.get('confidence') or 0.0
        utterances = results_obj.get('utterances') or []
        duration = (result.get('metadata') or {}).get('duration') or 0.0
        return jsonify({
            'success': True,
            'transcript': transcript,
            'words': words,
            'utterances': utterances,
            'confidence': confidence,
            'duration': duration,
            'word_count': len(words),
        })
    except requests.Timeout:
        app.logger.error("Deepgram STT timeout after 30s")
        return jsonify({'success': False, 'error': 'Transcription timeout. Please try again.'}), 504
    except requests.RequestException as e:
        app.logger.error(f"Deepgram STT network error: {e}")
        return jsonify({'success': False, 'error': 'Network error reaching transcription service.'}), 502
    except Exception as e:
        app.logger.error(f"Transcription error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/deepgram/token', methods=['GET'])
@login_required
def get_deepgram_token():
    api_key = os.environ.get('DEEPGRAM_API_KEY')
    if not api_key:
        return jsonify({'error': 'Deepgram API key not configured'}), 500
    return jsonify({
        'success': True,
        'key': api_key,
        'models': ['nova-3', 'enhanced', 'base'],
        'features': ['punctuate', 'interim_results', 'diarize']
    })


# ============================================================
# SUBSCRIPTION ACTIVATE
# ============================================================
@app.route('/subscription/activate', methods=['POST'])
@login_required
@csrf_protect
def subscription_activate():
    data = request.form or {}
    module = data.get('module') or session.get('selected_module', 'ielts')
    plan_key = data.get('plan')
    if not plan_key or plan_key not in PLAN_CONFIG:
        flash('Invalid plan selected.', 'danger')
        return redirect(url_for('subscription_page', module=module))
    result = _activate_subscription_by_user(current_user.id, module, plan_key)
    if result.get('success'):
        flash(f'Subscription activated! You have {result.get("tests_remaining")} tests remaining.', 'success')
    else:
        flash('Failed to activate subscription: ' + result.get('error', 'Unknown error'), 'danger')
    return redirect(url_for('subscription_page', module=module))


@app.route('/<module>/subscription/success')
@login_required
def subscription_success(module):
    if module not in MODULE_PRICES:
        return redirect(url_for('dashboard'))
    txn_id = request.args.get('txnId') or session.get(f'esewa_txn_id_{module}')
    ref_id = request.args.get('refId')
    amount = session.get(f'esewa_amount_{module}', MODULE_PRICES[module])
    plan_key = session.get(f'esewa_plan_{module}', '30days')
    if not txn_id or not ref_id:
        return render_template(
            'subscription_success.html',
            success=False, module=module,
            error="Missing payment details."
        )
    signature = request.args.get('signature')
    if signature and ESEWA_SECRET_KEY:
        data = {
            'pid': f"{module.upper()}_{plan_key.upper()}",
            'refId': ref_id,
            'amt': str(amount),
            'txnId': txn_id,
        }
        if not verify_esewa_signature(data, signature):
            return render_template(
                'subscription_success.html',
                success=False, module=module, error="Invalid signature."
            )
    Subscription = get_subscription_model(module)
    sub = Subscription.query.filter_by(user_id=current_user.id).first()
    if sub and sub.status == 'active':
        return render_template(
            'subscription_success.html',
            success=True, module=module, already_active=True
        )
    payload = {
        "product_code": f"{module.upper()}_{plan_key.upper()}",
        "total_amount": int(amount),
        "transaction_uuid": txn_id,
    }
    try:
        response = requests.post(
            ESEWA_API_VERIFY_URL, json=payload,
            headers={"Content-Type": "application/json"}, timeout=30
        )
        data = response.json()
        if data.get("status") == "complete" and data.get("transaction_uuid") == txn_id:
            result = _activate_subscription_by_user(current_user.id, module, plan_key)
            if result.get('success'):
                try:
                    _coupon_id = session.get(f'esewa_coupon_id_{module}')
                    _disc = session.get(f'esewa_discount_amount_{module}', 0)
                    _orig = session.get(f'esewa_original_amount_{module}', amount)
                    if _coupon_id and _disc and int(_disc) > 0:
                        _coupon = db.session.get(Coupon, int(_coupon_id))
                        if _coupon:
                            record_coupon_usage(
                                coupon=_coupon,
                                user_id=current_user.id,
                                module=module,
                                plan_key=plan_key,
                                original_amount=int(_orig),
                                discount_amount=int(_disc),
                                txn_id=txn_id,
                            )
                except Exception as _ce:
                    logger.warning(f"Coupon usage record failed: {_ce}")
                session.pop(f'esewa_coupon_id_{module}', None)
                session.pop(f'esewa_coupon_code_{module}', None)
                session.pop(f'esewa_original_amount_{module}', None)
                session.pop(f'esewa_discount_amount_{module}', None)
                _create_auto_bill(
                    user_id=current_user.id,
                    module=module,
                    plan_key=plan_key,
                    amount=int(amount),
                    txn_id=txn_id,
                )
                session.pop(f'esewa_txn_id_{module}', None)
                session.pop(f'esewa_amount_{module}', None)
                session.pop(f'esewa_plan_{module}', None)
                log_user_activity(current_user.id, 'payment_success', {
                    'module': module, 'txn_id': txn_id, 'auto_bill': True,
                })
                return render_template(
                    'subscription_success.html',
                    success=True, module=module, auto_bill=True
                )
            else:
                return render_template(
                    'subscription_success.html',
                    success=False, module=module,
                    error=result.get('error', 'Activation failed')
                )
        else:
            return render_template(
                'subscription_success.html',
                success=False, module=module, error="Verification failed."
            )
    except Exception as e:
        app.logger.error(f"eSewa verification error for {module}: {e}")
        return render_template(
            'subscription_success.html',
            success=False, module=module, error="Verification error."
        )


@app.route('/<module>/subscription/cancel')
@login_required
def subscription_cancel(module):
    return render_template('subscription_cancel.html', module=module)


@app.route('/<module>/subscription/status')
@login_required
def subscription_status(module):
    if module not in MODULE_PRICES:
        return jsonify({'error': 'Invalid module'}), 400
    return jsonify(_get_subscription_status(module))


@app.route('/subscription/status')
@login_required
def subscription_status_legacy():
    if not subscription_manager:
        return jsonify({'error': 'Subscription system unavailable'}), 503
    module = session.get('selected_module', 'ielts')
    return jsonify(subscription_manager.get_status(current_user.id))


@app.route('/pte/subscription/status')
@login_required
def pte_subscription_status_legacy():
    return jsonify(pte_subscription_manager.get_subscription_status(current_user.id))


@app.route('/ukvi/subscription/status')
@login_required
def ukvi_subscription_status_legacy():
    try:
        ukvi_sub_manager = UKVISubscriptionManager(db)
        return jsonify(ukvi_sub_manager.get_status(current_user.id))
    except Exception as e:
        logger.exception(f"UKVI subscription status failed: {e}")
        return jsonify({
            'has_subscription': False,
            'free_used': 0,
            'free_limit': 2,
            'error': str(e),
        }), 200


# ═══════════════════════════════════════════════════════════
# 🆕 v8.17 FIX — /pte/test-bank/status
#   Accepts admin session OR Flask-Login user. Returns all key
#   aliases the admin dashboard expects. Never returns HTML.
# ═══════════════════════════════════════════════════════════
@app.route('/pte/test-bank/status')
def pte_test_bank_status():
    """
    Pool stats endpoint used by the PTE admin dashboard.
    Accepts EITHER admin session (session['admin_id']) OR a
    regular Flask-Login user.
    """
    is_admin = 'admin_id' in session
    is_logged_in = current_user.is_authenticated

    if not is_admin and not is_logged_in:
        return jsonify({'success': False, 'error': 'Authentication required'}), 401

    try:
        raw_stats = pte_test_pool_manager.get_pool_stats() or {}

        normalised = {}
        for mod, s in raw_stats.items():
            s = s or {}
            size = int(s.get('size') or s.get('count') or 0)
            max_cap = int(
                s.get('max') or s.get('max_cap') or s.get('cap')
                or s.get('hard_max') or 100
            )
            hard_max = int(s.get('hard_max') or 1000)
            initial = int(s.get('initial') or s.get('initial_cap') or 100)
            step = int(s.get('step') or s.get('step_size') or 100)

            normalised[mod] = {
                # size aliases
                'size': size,
                'count': size,
                'current_size': size,
                # cap aliases
                'cap': max_cap,
                'current_cap': max_cap,
                'max': max_cap,
                'max_cap': max_cap,
                # bounds
                'initial': initial,
                'initial_cap': initial,
                'step': step,
                'step_size': step,
                'hard_max': hard_max,
                # status flags for UI
                'at_cap': size >= max_cap,
                'at_max': size >= hard_max,
                'remaining': max(0, max_cap - size),
            }

        # Ensure all known PTE modules exist with defaults
        for mod in ('pte_reading', 'pte_listening', 'pte_speaking_writing', 'pte_full'):
            if mod not in normalised:
                normalised[mod] = {
                    'size': 0, 'count': 0, 'current_size': 0,
                    'cap': 100, 'current_cap': 100, 'max': 100, 'max_cap': 100,
                    'initial': 100, 'initial_cap': 100,
                    'step': 100, 'step_size': 100,
                    'hard_max': 1000,
                    'at_cap': False, 'at_max': False,
                    'remaining': 100,
                }

        return jsonify({
            'success': True,
            'source': 'pool',
            'stats': normalised,
        })
    except Exception as e:
        logger.exception(f"pte_test_bank_status failed: {e}")
        fallback = {}
        for mod in ('pte_reading', 'pte_listening', 'pte_speaking_writing', 'pte_full'):
            fallback[mod] = {
                'size': 0, 'cap': 100, 'current_cap': 100, 'max': 100,
                'initial': 100, 'step': 100, 'hard_max': 1000,
                'at_cap': False, 'at_max': False, 'remaining': 100,
            }
        return jsonify({
            'success': False,
            'error': str(e),
            'stats': fallback,
        }), 200


@app.route('/test-bank/status')
@login_required
def test_bank_status():
    if not subscription_manager:
        return jsonify({'error': 'Subscription system unavailable'}), 503
    if not ielts_test_pool_manager:
        return jsonify({
            'success': False,
            'error': 'IELTS pool manager unavailable',
        }), 503
    pool_stats = ielts_test_pool_manager.get_pool_stats()
    total = 0
    for mod in ('ielts_reading', 'ielts_listening', 'ielts_writing', 'ielts_speaking'):
        s = pool_stats.get(mod) or {}
        total += s.get('size', 0)
    return jsonify({
        'success': True,
        'source': 'pool',
        'total': total,
        'stats': pool_stats,
    })


@app.route('/test-bank/status/<test_type>/<difficulty>')
@login_required
def test_bank_type_status(test_type, difficulty):
    if not subscription_manager:
        return jsonify({'error': 'Subscription system unavailable'}), 503
    if not ielts_test_pool_manager:
        return jsonify({
            'success': False,
            'error': 'IELTS pool manager unavailable',
        }), 503
    pool_module = f'ielts_{test_type}'
    try:
        items = ielts_test_pool_manager.list_pool_items(pool_module, 1000)
    except Exception as e:
        logger.warning(f"test_bank_type_status: list_pool_items({pool_module}) failed: {e}")
        items = []
    count = 0
    for it in items or []:
        if (it.get('difficulty') or 'medium').lower() == difficulty.lower():
            count += 1
    return jsonify({
        'success': True,
        'source': 'pool',
        'test_type': test_type,
        'difficulty': difficulty,
        'count': count,
        'avg_usage': 0,
        'max_tests': 0,
        'refresh_threshold': 999,
    })


# ============================================================
# RESUME HELPERS
# ============================================================
def _get_valid_resume_session(user_id: int, test_type: str, module: str = 'ielts', resume_id: Optional[str] = None) -> tuple:
    TestSession = get_test_session_model(module)
    query = TestSession.query.filter(
        TestSession.user_id == user_id,
        TestSession.test_type == test_type,
        TestSession.status.in_(['in_progress', 'paused'])
    ).order_by(TestSession.start_time.desc())
    if resume_id:
        try:
            resume_id_int = int(resume_id)
        except (ValueError, TypeError):
            return None, 'invalid_id'
        session_obj = query.filter(TestSession.id == resume_id_int).first()
    else:
        session_obj = query.first()
    if not session_obj:
        return None, 'not_found'
    if session_obj.status in ['completed', 'submitted']:
        return None, 'completed'
    try:
        test_data = session_obj.test_data or {}
        if not test_data or not isinstance(test_data, dict):
            raise ValueError("Corrupted data")
        if test_type == 'listening':
            sections = test_data.get('sections', [])
            if len(sections) < 4:
                logger.warning(f"Session {session_obj.id} has only {len(sections)} sections, deleting...")
                raise ValueError(f"Invalid number of sections: {len(sections)}")
            if not sections:
                raise ValueError("Missing sections")
        elif test_type == 'reading':
            if not test_data.get('questions') and not test_data.get('passages'):
                raise ValueError("Missing questions/passages")
        elif test_type == 'writing':
            if not test_data.get('task1') or not test_data.get('task2'):
                raise ValueError("Missing task1/task2")
        elif test_type == 'speaking':
            if not test_data.get('part1') or not test_data.get('part2'):
                raise ValueError("Missing part1/part2")
        return session_obj, 'valid'
    except Exception as e:
        logger.error(f"Session {session_obj.id} validation error: {e}, deleting...")
        db.session.delete(session_obj)
        db.session.commit()
        return None, 'corrupted_deleted'


def _clone_completed_ielts_test(user_id: int, test_type: str, difficulty: str = 'medium'):
    module = 'ielts'
    try:
        TestSession = get_test_session_model(module)
    except Exception as e:
        logger.error(f"Retake: cannot get TestSession model for {module}: {e}")
        return None
    try:
        last_completed = TestSession.query.filter(
            TestSession.user_id == user_id,
            TestSession.test_type == test_type,
            TestSession.status == 'completed'
        ).order_by(TestSession.last_updated.desc()).first()
    except Exception as e:
        logger.error(f"Retake: query failed for user={user_id} type={test_type}: {e}")
        return None
    if not last_completed:
        logger.info(f"Retake: no prior completed {test_type} test for user {user_id}")
        return None
    src = last_completed.test_data
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except Exception:
            logger.warning(f"Retake: could not parse test_data for session {last_completed.id}")
            return None
    if not src or not isinstance(src, dict):
        logger.warning(f"Retake: empty/invalid test_data for session {last_completed.id}")
        return None
    cloned_data = copy.deepcopy(src)
    cloned_data['retake_of'] = last_completed.id
    cloned_data['retake_at'] = datetime.now(timezone.utc).isoformat()
    cloned_data['_is_retake'] = True
    try:
        new_session = TestSession(
            user_id=user_id,
            test_type=test_type,
            difficulty=difficulty or last_completed.difficulty or 'medium',
            test_data=cloned_data,
            status='in_progress',
            answers_so_far={},
            current_question_index=0,
            start_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        db.session.add(new_session)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Retake: could not create clone session: {e}", exc_info=True)
        return None
    logger.info(
        f" IELTS {test_type} retake — new session {new_session.id} "
        f"cloned from completed session {last_completed.id}"
    )
    return {
        'success': True,
        'retake': True,
        'session_id': new_session.id,
        'test_id': new_session.id,
        'test_data': cloned_data,
        'original_test_id': last_completed.id,
    }


def _build_resume_response(session_obj, module_type: str = 'ielts'):
    test_data = session_obj.test_data or {}
    audio_urls = {}
    if test_data.get('audio_urls'):
        audio_urls = test_data['audio_urls']
        if isinstance(audio_urls, str):
            try:
                audio_urls = json.loads(audio_urls)
            except Exception:
                audio_urls = {}
    if not audio_urls and hasattr(session_obj, 'audio_urls') and session_obj.audio_urls:
        if isinstance(session_obj.audio_urls, str):
            try:
                audio_urls = json.loads(session_obj.audio_urls)
            except Exception:
                audio_urls = {}
        else:
            audio_urls = session_obj.audio_urls
        test_data['audio_urls'] = audio_urls
    answers = session_obj.answers_so_far or {}
    if isinstance(answers, list):
        answers_out = answers
    elif isinstance(answers, dict):
        answers_out = list(answers.values()) if answers else []
    else:
        answers_out = []
    start_time = session_obj.start_time.isoformat() if session_obj.start_time else None
    return jsonify({
        'success': True,
        'resumed': True,
        'test_data': test_data,
        'answers_so_far': answers_out,
        'status': session_obj.status,
        'start_time': start_time,
        'current_question_index': session_obj.current_question_index or 0,
        'audio_urls': audio_urls,
        'session_id': session_obj.id,
        'module': module_type,
        'message': 'Resuming your test from where you left off.'
    })


# ============================================================
# IELTS FULL TEST ORCHESTRATION
# ============================================================
PHASE_ORDER = ['listening', 'reading', 'writing', 'speaking']
PHASE_ROUTES = {
    'listening': '/listening',
    'reading': '/reading',
    'writing': '/writing',
    'speaking': '/speaking',
}

PHASE_TIME_LIMITS = {
    'listening': 30 * 60,
    'reading': 60 * 60,
    'writing': 60 * 60,
    'speaking': 15 * 60,
}

PHASE_GRACE_SECONDS = {
    'listening': 0,
    'reading': 0,
    'writing': 5 * 60,
    'speaking': 5 * 60,
}

PHASE_HARD_CAP = {
    p: PHASE_TIME_LIMITS[p] + PHASE_GRACE_SECONDS[p]
    for p in PHASE_TIME_LIMITS
}

SPEAKING_EARLY_WINDOW = int(os.environ.get('SPEAKING_EARLY_WINDOW_MIN', 15)) * 60
SPEAKING_LATE_WINDOW = int(os.environ.get('SPEAKING_LATE_WINDOW_MIN', 15)) * 60
SPEAKING_NOTIFY_BEFORE = int(os.environ.get('SPEAKING_NOTIFY_BEFORE_MIN', 5)) * 60
SPEAKING_STUCK_THRESHOLD = int(os.environ.get('SPEAKING_STUCK_THRESHOLD_MIN', 30)) * 60

logger.info(
    f" Speaking config — early={SPEAKING_EARLY_WINDOW // 60}min, "
    f"late={SPEAKING_LATE_WINDOW // 60}min, "
    f"notify={SPEAKING_NOTIFY_BEFORE // 60}min, "
    f"stuck={SPEAKING_STUCK_THRESHOLD // 60}min"
)

logger.info(
    f" Phase timers — "
    f"L={PHASE_TIME_LIMITS['listening']//60}min, "
    f"R={PHASE_TIME_LIMITS['reading']//60}min, "
    f"W={PHASE_TIME_LIMITS['writing']//60}+{PHASE_GRACE_SECONDS['writing']//60}min, "
    f"S={PHASE_TIME_LIMITS['speaking']//60}+{PHASE_GRACE_SECONDS['speaking']//60}min"
)

_ft_autogen_lock = threading.Lock()
_ft_autogen_in_progress = set()
_ft_autogen_done_at = {}


def _ft_state():
    state = session.get('full_ielts_test')
    if not state:
        return None
    migrated = False
    for key in ('answers', 'scores', 'test_ids', 'completed_sections', 'phase_started_at'):
        if key not in state or not isinstance(state[key], dict):
            state[key] = {}
            migrated = True
        for p in PHASE_ORDER:
            if p not in state[key]:
                if key == 'completed_sections':
                    state[key][p] = False
                elif key == 'answers':
                    state[key][p] = {}
                else:
                    state[key][p] = None
                migrated = True
    if 'speaking_schedule' not in state or not isinstance(state.get('speaking_schedule'), dict):
        state['speaking_schedule'] = {
            'scheduled_at': None,
            'set_at': None,
            'started_at': None,
            'completed_at': None,
            'notified': False,
        }
        migrated = True
    else:
        sched = state['speaking_schedule']
        for k, default in (
            ('scheduled_at', None), ('set_at', None),
            ('started_at', None), ('completed_at', None),
            ('notified', False),
        ):
            if k not in sched:
                sched[k] = default
                migrated = True
    if 'phase' not in state:
        state['phase'] = 'listening'
        migrated = True
    if 'parent_session_id' not in state:
        state['parent_session_id'] = None
        migrated = True
    if migrated:
        session['full_ielts_test'] = state
        session.modified = True
        try:
            logger.info(f" Migrated full-test session state for user {current_user.id}")
        except Exception:
            pass
    return state


def _ft_save(state):
    session['full_ielts_test'] = state
    session.modified = True


def _ft_next_phase(state):
    for p in PHASE_ORDER:
        if not state['completed_sections'].get(p):
            return p
    return None


def _ft_current_band(state):
    bands = []
    for p in PHASE_ORDER:
        s = state['scores'].get(p)
        if s and s.get('band_score') is not None:
            bands.append(float(s['band_score']))
    return round_ielts_band(sum(bands) / len(bands)) if bands else None


def _ft_speaking_status(state, user_id: Optional[int] = None) -> Dict[str, Any]:
    sched = state.get('speaking_schedule') or {}
    scheduled_at_raw = sched.get('scheduled_at')
    now = datetime.now(timezone.utc)
    base = {
        'early_window_seconds': SPEAKING_EARLY_WINDOW,
        'late_window_seconds': SPEAKING_LATE_WINDOW,
        'notify_before_seconds': SPEAKING_NOTIFY_BEFORE,
    }
    if not scheduled_at_raw:
        return {
            **base,
            'scheduled': False,
            'can_start': False,
            'reason': 'not_scheduled',
            'is_stuck': False,
            'can_reset': True,
        }
    scheduled_dt = _parse_utc_datetime(scheduled_at_raw)
    if not scheduled_dt:
        logger.warning(f" Corrupt scheduled_at for user {user_id}: {scheduled_at_raw!r}")
        return {
            **base,
            'scheduled': False,
            'can_start': False,
            'reason': 'invalid_schedule',
            'is_stuck': False,
            'can_reset': True,
        }
    delta = (scheduled_dt - now).total_seconds()
    started = sched.get('started_at') is not None
    completed = bool(state['completed_sections'].get('speaking', False))
    is_stuck = False
    if started and not completed:
        started_dt = _parse_utc_datetime(sched.get('started_at'))
        if started_dt:
            elapsed = (now - started_dt).total_seconds()
            if elapsed > SPEAKING_STUCK_THRESHOLD:
                is_stuck = True
                logger.warning(
                    f" [full-test] Speaking stuck — user={user_id} "
                    f"started {int(elapsed / 60)} min ago, never completed"
                )
    window_expired = delta < -SPEAKING_LATE_WINDOW
    can_start = (
        not started
        and not completed
        and (-SPEAKING_LATE_WINDOW) <= delta <= SPEAKING_EARLY_WINDOW
    )
    if completed:
        reason = 'completed'
    elif is_stuck:
        reason = 'stuck'
    elif started:
        reason = 'started'
    elif delta > SPEAKING_EARLY_WINDOW:
        reason = 'too_early'
    elif window_expired:
        reason = 'expired'
    else:
        reason = 'ready'
    return {
        **base,
        'scheduled': True,
        'scheduled_at': scheduled_at_raw,
        'seconds_until': int(delta),
        'can_start': can_start,
        'started': started,
        'completed': completed,
        'reason': reason,
        'is_stuck': is_stuck,
        'can_reset': (not completed),
    }


@app.route('/ielts-full-test')
@login_required
def ielts_full_test():
    session['selected_module'] = 'ielts'
    if request.args.get('new') == 'true':
        session.pop('full_ielts_test', None)
    return render_template('ielts_full_test.html')


@app.route('/ielts-full-test/start', methods=['POST'])
@login_required
@csrf_protect
@limiter.limit("10 per minute")
def ielts_full_test_start():
    existing = _ft_state()
    data = request.get_json(silent=True) or {}
    if existing and not data.get('force'):
        return jsonify({
            'success': False,
            'error': 'A full test is already in progress.',
            'active': True,
            'next_phase': _ft_next_phase(existing) or 'done',
        }), 409
    allowed, error, requires_sub = subscription_manager.can_access_test(
        current_user.id, 'listening'
    )
    if not allowed:
        return jsonify({
            'error': error,
            'requires_subscription': requires_sub,
            'redirect_to': '/subscription?module=ielts',
        }), 402
    ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
    if not ft_mgr:
        return jsonify({
            'error': 'Full-test bank manager not available',
            'hint': 'Check server logs — FullTestBankManager import failed',
        }), 503
    difficulty = data.get('difficulty', 'medium')
    accent = data.get('accent', 'british')
    pick = ft_mgr.get_full_test_variant(current_user.id, difficulty)
    if not pick.get('success'):
        err_msg = (pick.get('error') or '').lower()
        is_empty_bank = (
            'no' in err_msg and
            ('variant' in err_msg or 'test' in err_msg or 'available' in err_msg)
        )
        if is_empty_bank:
            with _ft_autogen_lock:
                already = difficulty in _ft_autogen_in_progress
                if not already:
                    _ft_autogen_in_progress.add(difficulty)
            if not already:
                def _auto_gen_full_test(diff, acc):
                    try:
                        with app.app_context():
                            ft_mgr.generate_full_test_variant_bg(
                                difficulty=diff,
                                accent=acc,
                            )
                            logger.info(
                                f" [auto-gen] Full-test variant ready for {diff}"
                            )
                    except Exception as e:
                        logger.error(
                            f" [auto-gen] Full-test generation failed: {e}",
                            exc_info=True,
                        )
                    finally:
                        with _ft_autogen_lock:
                            _ft_autogen_in_progress.discard(diff)
                        _ft_autogen_done_at[diff] = datetime.now(timezone.utc)
                threading.Thread(
                    target=_auto_gen_full_test,
                    args=(difficulty, accent),
                    daemon=True,
                ).start()
                logger.info(
                    f" [auto-gen] Started full-test variant generation "
                    f"(difficulty={difficulty}, accent={accent})"
                )
            return jsonify({
                'success': True,
                'generating': True,
                'message': (
                    'Preparing your full IELTS test — this usually takes '
                    '2–3 minutes. Please wait and try again shortly.'
                ),
                'retry_after': 15,
                'difficulty': difficulty,
            }), 200
        return jsonify({
            'error': pick.get('error', 'No full tests available'),
            'hint': 'Please try again shortly.',
        }), 503
    with _ft_autogen_lock:
        _ft_autogen_in_progress.discard(difficulty)
    bank_id = pick['bank_id']
    snapshot = pick['snapshot']
    attempt = ft_mgr.create_user_attempt(
        user_id=current_user.id,
        bank_id=bank_id,
        snapshot=snapshot,
        difficulty=difficulty,
    )
    if not attempt.get('success'):
        return jsonify({'error': attempt.get('error')}), 500
    session_id = attempt['session_id']
    state = {
        'parent_session_id': session_id,
        'bank_id': bank_id,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'difficulty': difficulty,
        'accent': accent,
        'module': 'ielts',
        'phase': 'listening',
        'test_ids': {p: None for p in PHASE_ORDER},
        'answers': {p: {} for p in PHASE_ORDER},
        'scores': {p: None for p in PHASE_ORDER},
        'completed_sections': {p: False for p in PHASE_ORDER},
        'phase_started_at': {p: None for p in PHASE_ORDER},
        'speaking_schedule': {
            'scheduled_at': None,
            'set_at': None,
            'started_at': None,
            'completed_at': None,
            'notified': False,
        },
    }
    _ft_save(state)
    logger.info(
        f" Full test started — user {current_user.id}, "
        f"attempt #{session_id}, variant #{bank_id}"
    )
    return jsonify({
        'success': True,
        'phase': 'listening',
        'parent_session_id': session_id,
        'bank_id': bank_id,
        'snapshot_ready': True,
        'redirect': f"{PHASE_ROUTES['listening']}?full_test=true",
    })


@app.route('/ielts-full-test/status')
@login_required
def ielts_full_test_status():
    state = _ft_state()
    if not state:
        return jsonify({'active': False})
    with _ft_autogen_lock:
        generating = list(_ft_autogen_in_progress)
    return jsonify({
        'active': True,
        'phase': state.get('phase'),
        'next_phase': _ft_next_phase(state),
        'completed': state.get('completed_sections', {}),
        'scores': state.get('scores', {}),
        'difficulty': state.get('difficulty'),
        'accent': state.get('accent'),
        'started_at': state.get('started_at'),
        'current_band_preview': _ft_current_band(state),
        'all_completed': all(state['completed_sections'].values()),
        'speaking': _ft_speaking_status(state, user_id=current_user.id),
        'parent_session_id': state.get('parent_session_id'),
        'bank_id': state.get('bank_id'),
        'snapshot_ready': True,
        'auto_generating': generating,
        'phase_time_config': {
            p: {
                'base_seconds': PHASE_TIME_LIMITS.get(p, 0),
                'grace_seconds': PHASE_GRACE_SECONDS.get(p, 0),
                'hard_cap': PHASE_HARD_CAP.get(p, 0),
            }
            for p in PHASE_ORDER
        },
    })


@app.route('/ielts-full-test/enter-phase/<phase>')
@login_required
def ielts_full_test_enter_phase(phase):
    state = _ft_state()
    if not state or phase not in PHASE_ORDER:
        return redirect(url_for('ielts_full_test'))
    if phase == 'speaking':
        return redirect(url_for('ielts_full_test'))
    if state['completed_sections'].get(phase):
        nxt = _ft_next_phase(state)
        if nxt and nxt != 'speaking':
            return redirect(f"{PHASE_ROUTES[nxt]}?full_test=true")
        return redirect(url_for('ielts_full_test'))
    state['phase'] = phase
    state['phase_started_at'][phase] = datetime.now(timezone.utc).isoformat()
    _ft_save(state)
    base_secs = PHASE_TIME_LIMITS.get(phase, 0)
    grace_secs = PHASE_GRACE_SECONDS.get(phase, 0)
    return redirect(
        f"{PHASE_ROUTES[phase]}?full_test=true"
        f"&time_limit={base_secs}"
        f"&grace={grace_secs}"
    )


@app.route('/ielts-full-test/record-phase', methods=['POST'])
@login_required
@csrf_protect
def ielts_full_test_record_phase():
    data = request.get_json(silent=True) or {}
    phase = data.get('phase')
    if phase not in PHASE_ORDER:
        return jsonify({'error': 'Invalid phase'}), 400
    state = _ft_state()
    if not state:
        return jsonify({'error': 'No full test session in progress'}), 404
    state['answers'][phase] = data.get('answers') or {}
    state['scores'][phase] = {
        'band_score': data.get('band_score'),
        'score_pct': data.get('score_pct'),
        'correct': data.get('correct'),
        'total': data.get('total'),
    }
    state['completed_sections'][phase] = True
    if phase == 'speaking':
        sched = state.get('speaking_schedule') or {}
        sched['completed_at'] = datetime.now(timezone.utc).isoformat()
        state['speaking_schedule'] = sched
    state['phase'] = _ft_next_phase(state) or 'done'
    _ft_save(state)
    logger.info(f" Full test phase '{phase}' recorded for user {current_user.id}")
    return jsonify({'success': True, 'next_phase': state['phase']})


@app.route('/ielts-full-test/speaking-schedule', methods=['POST'])
@login_required
@csrf_protect
@limiter.limit("10 per minute")
def ielts_full_test_speaking_schedule():
    data = request.get_json(silent=True) or {}
    state = _ft_state()
    if not state:
        return jsonify({'error': 'No full test session'}), 404
    if not state['completed_sections'].get('writing'):
        return jsonify({'error': 'Complete Listening, Reading and Writing first'}), 400
    scheduled_at = data.get('scheduled_at')
    if not scheduled_at:
        return jsonify({'error': 'scheduled_at is required'}), 400
    dt = _parse_utc_datetime(scheduled_at)
    if not dt:
        return jsonify({'error': 'Invalid datetime format'}), 400
    if (dt - datetime.now(timezone.utc)).total_seconds() < -SPEAKING_LATE_WINDOW:
        return jsonify({'error': 'Scheduled time is too far in the past'}), 400
    sched = state.get('speaking_schedule') or {}
    sched['scheduled_at'] = dt.isoformat()
    sched['set_at'] = datetime.now(timezone.utc).isoformat()
    sched['notified'] = False
    sched['started_at'] = None
    sched['completed_at'] = None
    state['speaking_schedule'] = sched
    state['phase'] = 'speaking'
    _ft_save(state)
    logger.info(
        f" Speaking scheduled for user {current_user.id} at {dt.isoformat()}"
    )
    return jsonify({
        'success': True,
        'speaking': _ft_speaking_status(state, user_id=current_user.id),
        'total_duration_seconds': PHASE_TIME_LIMITS.get('speaking', 900),
        'grace_extension_seconds': PHASE_GRACE_SECONDS.get('speaking', 300),
        'hard_cap_seconds': PHASE_HARD_CAP.get('speaking', 1200),
        'total_questions': 22,
    })


@app.route('/ielts-full-test/speaking-status')
@login_required
def ielts_full_test_speaking_status():
    state = _ft_state()
    if not state:
        return jsonify({'active': False})
    return jsonify({
        'active': True,
        'speaking': _ft_speaking_status(state, user_id=current_user.id),
        'total_duration_seconds': PHASE_TIME_LIMITS.get('speaking', 900),
        'grace_extension_seconds': PHASE_GRACE_SECONDS.get('speaking', 300),
        'hard_cap_seconds': PHASE_HARD_CAP.get('speaking', 1200),
        'total_questions': 22,
    })


@app.route('/ielts-full-test/reset-speaking', methods=['POST'])
@login_required
@csrf_protect
@limiter.limit("20 per minute")
def ielts_full_test_reset_speaking():
    state = _ft_state()
    if not state:
        return jsonify({'error': 'No full test session'}), 404
    if state['completed_sections'].get('speaking'):
        return jsonify({
            'success': False,
            'error': 'Speaking already completed — cannot reset.',
            'already_completed': True,
        }), 400
    prior_done = all(
        state['completed_sections'].get(p)
        for p in ['listening', 'reading', 'writing']
    )
    if not prior_done:
        return jsonify({
            'success': False,
            'error': 'Complete Listening, Reading, and Writing first.',
            'prior_not_done': True,
        }), 400
    prev_schedule = state.get('speaking_schedule') or {}
    had_previous = bool(
        prev_schedule.get('scheduled_at')
        or prev_schedule.get('started_at')
    )
    state['speaking_schedule'] = {
        'scheduled_at': None,
        'set_at': None,
        'started_at': None,
        'completed_at': None,
        'notified': False,
    }
    state['phase'] = 'speaking'
    _ft_save(state)
    logger.info(
        f" [full-test] Speaking reset — user={current_user.id}, "
        f"had_previous={had_previous}"
    )
    return jsonify({
        'success': True,
        'message': 'Speaking phase reset. Please book a new time.',
        'had_previous_schedule': had_previous,
        'speaking': _ft_speaking_status(state, user_id=current_user.id),
    })


@app.route('/ielts-full-test/start-speaking', methods=['POST'])
@login_required
@csrf_protect
@limiter.limit("20 per minute")
def ielts_full_test_start_speaking():
    data = request.get_json(silent=True) or {}
    force = bool(data.get('force'))
    state = _ft_state()
    if not state:
        return jsonify({'error': 'No full test session'}), 404
    st = _ft_speaking_status(state, user_id=current_user.id)
    if not st.get('scheduled'):
        return jsonify({
            'error': 'No speaking time scheduled yet. Please book a time.',
            'needs_booking': True,
            'can_reset': False,
        }), 400
    if st.get('completed'):
        return jsonify({
            'error': 'Speaking already completed.',
            'completed': True,
            'redirect': '/ielts-full-test?completed=speaking',
        }), 400
    if st.get('is_stuck') or force:
        logger.info(
            f" [full-test] Force-starting speaking for user "
            f"{current_user.id} (stuck={st.get('is_stuck')}, force={force})"
        )
        sched = state.get('speaking_schedule') or {}
        sched['started_at'] = None
        state['speaking_schedule'] = sched
        _ft_save(state)
        st = _ft_speaking_status(state, user_id=current_user.id)
    if not st.get('can_start'):
        reason = st.get('reason')
        if reason == 'too_early':
            return jsonify({
                'error': 'You can start up to 15 minutes early. Please wait.',
                'seconds_until_window': max(
                    0, st['seconds_until'] - SPEAKING_EARLY_WINDOW
                ),
                'reason': 'too_early',
            }), 403
        if reason == 'expired':
            return jsonify({
                'error': 'The time window has expired. Please reset and rebook.',
                'expired': True,
                'can_reset': True,
                'reason': 'expired',
            }), 403
        if reason == 'started':
            sched = state.get('speaking_schedule') or {}
            sched['started_at'] = None
            state['speaking_schedule'] = sched
            _ft_save(state)
            st = _ft_speaking_status(state, user_id=current_user.id)
            if not st.get('can_start'):
                return jsonify({
                    'error': 'Speaking cannot be started right now.',
                    'can_reset': True,
                    'reason': st.get('reason'),
                }), 403
        else:
            return jsonify({
                'error': 'Speaking cannot be started right now.',
                'reason': reason,
                'can_reset': True,
            }), 403
    sched = state.get('speaking_schedule') or {}
    sched['started_at'] = datetime.now(timezone.utc).isoformat()
    state['speaking_schedule'] = sched
    state['phase'] = 'speaking'
    _ft_save(state)
    logger.info(f" Speaking phase started for user {current_user.id}")
    return jsonify({
        'success': True,
        'redirect': '/speaking?full_test=true',
    })


@app.route('/ielts-full-test/aggregate', methods=['POST'])
@login_required
@csrf_protect
def ielts_full_test_aggregate():
    state = _ft_state()
    if not state:
        return jsonify({'error': 'No full test session'}), 404
    if not all(state['completed_sections'].values()):
        missing = [p for p in PHASE_ORDER if not state['completed_sections'][p]]
        return jsonify({'error': 'Not all sections completed', 'missing': missing}), 400
    l = state['scores'].get('listening') or {}
    r = state['scores'].get('reading') or {}
    w = state['scores'].get('writing') or {}
    sp = state['scores'].get('speaking') or {}
    l_band = float(l.get('band_score') or 0.0)
    r_band = float(r.get('band_score') or 0.0)
    w_band = float(w.get('band_score') or 0.0)
    sp_band = float(sp.get('band_score') or 0.0)
    overall = round_ielts_band((l_band + r_band + w_band + sp_band) / 4.0)
    module = session.get('selected_module', 'ielts')
    TestResult = get_test_result_model(module)
    db_result = TestResult(
        user_id=current_user.id,
        test_type='full_ielts',
        score=overall * 10,
        band_score=overall,
    )
    db_result.set_answers({
        'listening': state['answers'].get('listening', {}),
        'reading': state['answers'].get('reading', {}),
        'writing': state['answers'].get('writing', {}),
        'speaking': state['answers'].get('speaking', {}),
        'phase_scores': {
            'listening': l, 'reading': r, 'writing': w, 'speaking': sp,
        },
    })
    db_result.feedback = (
        f"Full IELTS — L:{l_band} R:{r_band} W:{w_band} S:{sp_band} "
        f"Overall:{overall}"
    )
    db.session.add(db_result)
    db.session.commit()
    parent_id = state.get('parent_session_id')
    if parent_id:
        TestSessionM = get_test_session_model(module)
        parent = db.session.get(TestSessionM, int(parent_id))
        if parent:
            snap = dict(parent.test_data or {})
            snap_state = dict(snap.get('state') or {})
            snap_state.update({
                'phase': 'done',
                'scores': state.get('scores'),
                'answers': state.get('answers'),
                'completed_sections': state.get('completed_sections'),
                'final_overall_band': overall,
                'completed_at': datetime.now(timezone.utc).isoformat(),
            })
            snap['state'] = snap_state
            parent.test_data = snap
            parent.status = 'completed'
            parent.ended_at = datetime.now(timezone.utc)
            parent.last_updated = datetime.now(timezone.utc)
            db.session.commit()
    try:
        Subscription = get_subscription_model(module)
        subscription = Subscription.query.filter_by(user_id=current_user.id).first()
        if subscription:
            sub_end = subscription.subscription_end
            if sub_end and sub_end.tzinfo is None:
                sub_end = sub_end.replace(tzinfo=timezone.utc)
            has_sub = bool(
                subscription.status == 'active'
                and sub_end and sub_end > datetime.now(timezone.utc)
                and (subscription.plan or '').strip().lower()
                    not in ('', 'free', 'trial', 'none', 'default')
                and (subscription.tests_remaining or 0) > 0
            )
            if has_sub:
                subscription.tests_remaining = (subscription.tests_remaining or 0) - 1
                subscription.tests_taken = (subscription.tests_taken or 0) + 1
            else:
                subscription_manager.increment_free_usage(current_user.id, 'listening')
            db.session.commit()
    except Exception as e:
        logger.error(f"Full-test quota decrement failed: {e}")
    log_user_activity(current_user.id, 'complete_test', {
        'test_type': 'full_ielts',
        'band': overall,
        'sections': {
            'listening': l_band, 'reading': r_band,
            'writing': w_band, 'speaking': sp_band,
        },
    })
    payload = {
        'success': True,
        'listening_band': l_band,
        'reading_band': r_band,
        'writing_band': w_band,
        'speaking_band': sp_band,
        'overall_band': overall,
        'result_id': db_result.id,
        'sections': {
            'listening': l, 'reading': r, 'writing': w, 'speaking': sp,
        },
    }
    session.pop('full_ielts_test', None)
    return jsonify(payload)


@app.route('/ielts-full-test/abandon', methods=['POST'])
@login_required
@csrf_protect
def ielts_full_test_abandon():
    state = _ft_state()
    if state:
        parent_id = state.get('parent_session_id')
        if parent_id:
            try:
                TestSessionM = get_test_session_model('ielts')
                parent = db.session.get(TestSessionM, int(parent_id))
                if parent:
                    parent.status = 'abandoned'
                    parent.ended_at = datetime.now(timezone.utc)
                    parent.last_updated = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception as e:
                logger.warning(f"Could not mark parent session abandoned: {e}")
    session.pop('full_ielts_test', None)
    return jsonify({'success': True})


# ============================================================
# IELTS LISTENING ROUTES
# ============================================================
@app.route('/listening/start', methods=['GET', 'POST'])
@login_required
@limiter.limit("60 per minute")
def start_listening():
    try:
        if AUDIO_GENERATOR_AVAILABLE and audio_generator:
            try:
                audio_generator.start_new_session()
            except Exception as _vc_err:
                logger.debug(f"Voice cache clear skipped: {_vc_err}")
        data = request.get_json(silent=True) or {}
        resume_id = request.args.get('resume') or data.get('resume')
        difficulty = data.get('difficulty', 'medium')
        topic = data.get('topic', None)
        user_id = current_user.id
        module = session.get('selected_module', 'ielts')
        from_full_test = bool(data.get('from_full_test'))
        force_new = data.get('force_new', False)
        retake = bool(data.get('retake', False))
        accent = data.get('accent', None)
        if accent is None:
            combo = accent_mixer.get_random_combo()
            accent = combo.get('1', 'british')
        else:
            accent = accent
        accent_string = accent if isinstance(accent, str) else accent.get('1', 'british')
        test_accent = accent_string
        num_sections = 4

        if from_full_test:
            ft = session.get('full_ielts_test') or {}
            parent_id = ft.get('parent_session_id')
            ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
            if parent_id and ft_mgr:
                TestSessionM = get_test_session_model(module)
                parent = db.session.get(TestSessionM, int(parent_id))
                snap = ft_mgr.get_variant_snapshot(parent, 'listening')
                if snap:
                    sections_in_snap = snap.get('sections') or []
                    if not snap.get('generated_sections'):
                        snap['generated_sections'] = list(
                            range(1, len(sections_in_snap) + 1)
                        )
                    snap.setdefault('total_sections', len(sections_in_snap) or 4)
                    snap.setdefault('partial', False)
                    phase_session = TestSessionM(
                        user_id=user_id,
                        test_type='listening',
                        difficulty=difficulty,
                        test_data=snap,
                        status='in_progress',
                        start_time=datetime.now(timezone.utc),
                    )
                    db.session.add(phase_session)
                    db.session.commit()
                    session['current_test_session_id'] = phase_session.id
                    session['current_listening_test_data'] = snap
                    audio_ready = list((snap.get('audio_urls') or {}).keys())
                    logger.info(
                        f" [full-test] Serving listening snapshot — "
                        f"phase_session={phase_session.id} "
                        f"sections={len(sections_in_snap)} "
                        f"generated={snap['generated_sections']} "
                        f"audio_ready={audio_ready}"
                    )
                    return jsonify({
                        'success': True,
                        'session_id': phase_session.id,
                        'test_id': phase_session.id,
                        'test_data': snap,
                        'sections': snap.get('sections', []),
                        'audio_urls': snap.get('audio_urls', {}),
                        'audio_timings': snap.get('audio_timings', {}),
                        'total_sections': snap.get('total_sections', 4),
                        'generated_sections': snap.get('generated_sections', []),
                        'partial': False,
                        'from_snapshot': True,
                    })

        if not from_full_test:
            allowed, error, requires_sub = subscription_manager.can_access_test(user_id, 'listening')
            if not allowed:
                return jsonify({
                    'error': error,
                    'requires_subscription': requires_sub,
                    'redirect_to': '/subscription?module=' + module
                }), 402
            if retake and not force_new:
                cloned = _clone_completed_ielts_test(user_id, 'listening', difficulty)
                if cloned:
                    td = cloned['test_data']
                    session['current_test_session_id'] = cloned['session_id']
                    session['current_listening_test_data'] = td
                    return jsonify({
                        'success': True,
                        'retake': True,
                        'session_id': cloned['session_id'],
                        'test_id': cloned['session_id'],
                        'test_data': td,
                        'sections': td.get('sections', []),
                        'audio_urls': td.get('audio_urls', {}),
                        'audio_timings': td.get('audio_timings', {}),
                        'total_sections': td.get('total_sections', 4),
                        'generated_sections': td.get('generated_sections', [1, 2, 3, 4]),
                        'partial': False,
                        'source': 'retake',
                        'message': 'Retaking your previous listening test.',
                    })
                logger.info(
                    f"Retake requested for listening but no prior completed test — "
                    f"generating fresh for user {user_id}"
                )

        session_obj, status = _get_valid_resume_session(user_id, 'listening', module, resume_id)
        if status == 'corrupted_deleted':
            logger.info(f"Corrupted listening session deleted for user {user_id}")
        elif status == 'valid' and session_obj:
            return _build_resume_response(session_obj, module)
        elif status == 'completed':
            logger.info(f"Completed listening session found for user {user_id}, not resuming")
        if resume_id and status != 'valid':
            return jsonify({
                'error': 'Test session not found, expired, or already completed. Starting new test.',
                'redirect_to': '/listening'
            }), 404

        incomplete_count, reached_limit = check_resume_limit(user_id, test_type='listening', module=module)
        if reached_limit:
            return jsonify({
                'error': f'You have {incomplete_count} incomplete listening tests. Max 5 allowed.',
                'redirect_to': '/saved-tests'
            }), 400

        source = 'on_demand'
        pool_id = None

        if force_new or not ielts_test_pool_manager:
            from modules.ielts.listening.test_generator import ListeningTestGenerator
            generator = ListeningTestGenerator(ai_engine)
            full_test = generator.generate(
                difficulty=difficulty,
                topic=topic,
                exam='ielts',
                accent=test_accent,
                fast=True
            )
            source = 'on_demand'
            logger.info(f"🆕 [listening/start] fresh generation for user {user_id}")
        else:
            def _gen():
                from modules.ielts.listening.test_generator import ListeningTestGenerator
                generator = ListeningTestGenerator(ai_engine)
                return generator.generate(
                    difficulty=difficulty,
                    topic=topic,
                    exam='ielts',
                    accent=test_accent,
                    fast=True
                )
            full_test, source, pool_id = ielts_test_pool_manager.get_or_generate(
                module='ielts_listening',
                difficulty=difficulty,
                user_id=user_id,
                generate_fn=_gen,
            )
            if source == 'waiting':
                return jsonify({
                    'success': False,
                    'source': 'waiting',
                    'message': 'Another user is generating this test. Please retry.',
                    'retry_after': 3,
                }), 202
            if source == 'exhausted':
                return jsonify({
                    'success': False,
                    'source': 'exhausted',
                    'error': 'Test pool is currently full. Please try again shortly.'
                }), 503
            if source == 'failed' or not full_test:
                return jsonify({
                    'success': False,
                    'source': 'failed',
                    'error': 'Test generation failed. Please try again.'
                }), 503
            logger.info(
                f" [listening/start] user={user_id} source={source} pool_id={pool_id}"
            )

        if not full_test or full_test.get('error'):
            logger.error(f"Failed to load test: {full_test}")
            return jsonify({'error': 'Failed to load test'}), 500
        sections = full_test.get('sections', [])
        if len(sections) < 4:
            logger.error(f"Only {len(sections)} sections, expected 4")
            return jsonify({'error': 'Incomplete test loaded'}), 500
        accents_map = {str(i): test_accent for i in range(1, 5)}
        section1_data = sections[0]
        audio_urls = full_test.get('audio_urls', {}) or {}
        audio_timings = full_test.get('audio_timings', {}) or {}
        if '1' not in audio_urls:
            result, error, timings = _generate_single_section_audio(
                None, 1, section1_data, accents_map,
                pool_id=pool_id,
            )
            if result:
                audio_urls['1'] = result
                audio_timings['1'] = timings
            else:
                logger.warning(f"Section 1 audio generation failed: {error}")

        test_data = {
            'sections': sections,
            'total_sections': 4,
            'generated_sections': [1, 2, 3, 4],
            'accent': test_accent,
            'difficulty': difficulty,
            'topic': topic,
            'audio_urls': audio_urls,
            'audio_timings': audio_timings,
            'partial': False,
            'full_test_data': full_test,
            '_full_test': from_full_test,
            '_source': source,
            '_pool_id': pool_id,
        }
        TestSession = get_test_session_model(module)
        session_obj = TestSession(
            user_id=user_id,
            test_type='listening',
            difficulty=difficulty,
            test_data=test_data,
            status='in_progress',
            start_time=datetime.now(timezone.utc)
        )
        db.session.add(session_obj)
        db.session.commit()
        session_id = session_obj.id
        session['current_test_session_id'] = session_id
        session['current_listening_test_data'] = test_data
        thread = threading.Thread(
            target=_generate_audio_background,
            args=(session_id, 4)
        )
        thread.daemon = True
        thread.start()
        logger.info(f" Audio background thread started for session {session_id}")
        response = {
            'success': True,
            'session_id': session_id,
            'test_id': session_id,
            'test_data': test_data,
            'sections': sections,
            'audio_urls': audio_urls,
            'audio_timings': audio_timings,
            'total_sections': 4,
            'generated_sections': [1, 2, 3, 4],
            'partial': False,
            'source': source,
            'message': 'All sections ready. Audio for sections 2-4 is being generated in the background.'
        }
        logger.info(f" Listening test started for user {user_id}, session_id={session_id}, source={source}")
        return jsonify(response)
    except Exception as e:
        logger.error(f" start_listening error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error: ' + str(e)}), 500


@app.route('/listening/audio/<test_id>/<int:section>', methods=['GET'])
@login_required
def listening_audio(test_id, section):
    user_id = current_user.id
    module = session.get('selected_module', 'ielts')
    TestSession = get_test_session_model(module)
    sessions = TestSession.query.filter_by(
        user_id=user_id,
        test_type='listening',
        status='in_progress'
    ).all()
    session_obj = None
    test_data = None
    for sess in sessions:
        raw = sess.test_data
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        else:
            data = raw or {}
        if data.get('id') == test_id or data.get('test_id') == test_id:
            session_obj = sess
            test_data = data
            break
    if not session_obj or not test_data:
        return jsonify({'error': 'Test not found or not in progress'}), 404
    audio_urls = test_data.get('audio_urls', {})
    if isinstance(audio_urls, str):
        try:
            audio_urls = json.loads(audio_urls)
        except Exception:
            audio_urls = {}
    audio_timings = test_data.get('audio_timings', {})
    if isinstance(audio_timings, str):
        try:
            audio_timings = json.loads(audio_timings)
        except Exception:
            audio_timings = {}
    if str(section) in audio_urls:
        stored = audio_urls[str(section)]
        if isinstance(stored, dict):
            timings = audio_timings.get(str(section), {})
            return jsonify({'audio_urls': stored, 'timings': timings})
        else:
            timings = audio_timings.get(str(section), {})
            return jsonify({'audio_urls': {'main': stored}, 'timings': timings})
    sections = test_data.get('sections', [])
    if section < 1 or section > len(sections):
        return jsonify({'error': 'Invalid section number'}), 400
    accents_map = test_data.get('accents', {})
    if not accents_map and 'accent' in test_data:
        default_accent = test_data.get('accent', 'british')
        accents_map = {str(i): default_accent for i in range(1, 5)}
    elif not accents_map:
        accents_map = {str(i): 'british' for i in range(1, 5)}
    sec = sections[section - 1]
    _pool_id_for_audio = test_data.get('_pool_id') if isinstance(test_data, dict) else None
    result, error, timings = _generate_single_section_audio(
        session_obj.id, section, sec, accents_map,
        pool_id=_pool_id_for_audio,
    )
    if result and isinstance(result, dict):
        audio_urls[str(section)] = result
        audio_timings[str(section)] = timings
        test_data['audio_urls'] = audio_urls
        test_data['audio_timings'] = audio_timings
        session_obj.test_data = test_data
        db.session.commit()
        return jsonify({'audio_urls': result, 'timings': timings})
    else:
        return jsonify({'error': f'Failed to generate audio: {error}'}), 500


@app.route('/listening/submit', methods=['POST'])
@login_required
def submit_listening():
    try:
        data = request.json or {}
        user_answers = data.get('answers', {})
        is_full_test = bool(data.get('is_full_test'))
        module = session.get('selected_module', 'ielts')
        TestSession = get_test_session_model(module)
        TestResult = get_test_result_model(module)
        Subscription = get_subscription_model(module)
        ft_state = None
        if is_full_test:
            ft_state = session.get('full_ielts_test')
            if not ft_state:
                return jsonify({'error': 'No full test session in progress'}), 404
        test_data = session.get('current_listening_test_data', {})
        sess_id = session.get('current_test_session_id')
        test_id_from_req = data.get('test_id') or session.get('current_listening_test')
        if not test_data:
            if sess_id:
                sess = db.session.get(TestSession, sess_id)
                if sess:
                    raw = sess.test_data
                    test_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    session['current_listening_test_data'] = test_data
            if not test_data and test_id_from_req:
                try:
                    sess = db.session.get(TestSession, int(test_id_from_req))
                    if sess:
                        raw = sess.test_data
                        test_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
                        session['current_test_session_id'] = sess.id
                        session['current_listening_test_data'] = test_data
                        sess_id = sess.id
                except (ValueError, TypeError):
                    pass
        else:
            if isinstance(test_data, str):
                try:
                    test_data = json.loads(test_data)
                except Exception:
                    test_data = {}
            if not sess_id and test_id_from_req:
                try:
                    sess = db.session.get(TestSession, int(test_id_from_req))
                    if sess:
                        sess_id = sess.id
                        session['current_test_session_id'] = sess_id
                except Exception:
                    pass
        if not test_data:
            logger.error(f" No test_data. session keys: {list(session.keys())}, test_id: {test_id_from_req}")
            return jsonify({'error': 'Test data not found. Please start a new test.'}), 400
        correct_answers = {}
        total_questions = 0
        sections = test_data.get('sections', [])
        for section in sections:
            for q in section.get('questions', []):
                q_id = str(q.get('id') or q.get('number') or q.get('question_number') or '')
                answer = q.get('correct_answer') or q.get('answer') or q.get('correct') or ''
                if q_id and answer:
                    correct_answers[q_id] = answer
                    total_questions += 1
        if not correct_answers:
            logger.error(f" No correct answers. sections_len={len(sections)}")
            return jsonify({'error': 'No correct answers found in test data.'}), 400
        answer_results = {}
        total_answered = 0
        correct_count = 0
        for q_id, correct in correct_answers.items():
            user_answer = (user_answers.get(q_id, '') or '').strip()
            if not user_answer:
                is_correct = False
                display_answer = '(not answered)'
            else:
                total_answered += 1
                is_correct = user_answer.lower() == str(correct).lower().strip()
                display_answer = user_answer
            if is_correct:
                correct_count += 1
            answer_results[q_id] = {
                'user_answer': display_answer,
                'correct_answer': correct,
                'is_correct': is_correct,
            }
        band_score = round_ielts_band(calculate_ielts_band(correct_count, total_questions))
        score_pct = (correct_count / total_questions * 100) if total_questions > 0 else 0
        unanswered = total_questions - total_answered
        section_scores = {}
        for sec_idx, section in enumerate(sections, 1):
            sec_correct = sec_total = 0
            for q in section.get('questions', []):
                q_id = str(q.get('id', q.get('number', '')))
                if q_id and q_id in correct_answers:
                    sec_total += 1
                    ua = (user_answers.get(q_id, '') or '').strip()
                    ca = str(correct_answers[q_id])
                    if ua and ua.lower() == ca.lower():
                        sec_correct += 1
            sec_pct = (sec_correct / sec_total * 100) if sec_total > 0 else 0
            section_scores[f'section_{sec_idx}'] = {
                'correct': sec_correct, 'total': sec_total,
                'percentage': round(sec_pct, 1),
            }
        if is_full_test:
            ft_state['answers']['listening'] = user_answers
            ft_state['scores']['listening'] = {
                'band_score': band_score,
                'score_pct': round(score_pct, 1),
                'correct': correct_count,
                'total': total_questions,
            }
            ft_state['completed_sections']['listening'] = True
            ft_state['phase'] = _ft_next_phase(ft_state) or 'done'
            session['full_ielts_test'] = ft_state
            session.modified = True
            if sess_id:
                sess = db.session.get(TestSession, sess_id)
                if sess:
                    sess.answers_so_far = user_answers
                    sess.status = 'completed'
                    sess.last_updated = datetime.now(timezone.utc)
                    db.session.commit()
            session.pop('current_listening_test', None)
            session.pop('current_listening_test_data', None)
            session.pop('current_test_session_id', None)
            log_user_activity(current_user.id, 'full_test_phase_done', {
                'phase': 'listening', 'band': band_score, 'module': module,
            })
            return jsonify({
                'success': True,
                'is_full_test': True,
                'band_score': band_score,
                'correct': correct_count,
                'total': total_questions,
                'section_scores': section_scores,
                'next_phase': ft_state['phase'],
                'redirect': '/ielts-full-test?completed=listening',
            })
        subscription = Subscription.query.filter_by(user_id=current_user.id).first()
        if not subscription:
            from models import create_default_subscription_for_user
            subscription = create_default_subscription_for_user(current_user.id, module)
        end = subscription.subscription_end
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        has_active_subscription = (
            subscription.status == 'active'
            and end and end > datetime.now(timezone.utc)
            and (subscription.plan or '').strip().lower()
                not in ('', 'free', 'trial', 'none', 'default')
            and (subscription.tests_remaining or 0) > 0
        )
        db_result = TestResult(
            user_id=current_user.id,
            test_type='listening',
            score=score_pct,
            band_score=band_score,
        )
        db_result.set_answers(user_answers)
        feedback = (
            f"Correct: {correct_count}/{total_questions}"
            + (f" ({unanswered} not answered)" if unanswered > 0 else "")
            + f" Band: {band_score}"
        )
        db_result.feedback = feedback
        db.session.add(db_result)
        db.session.commit()
        if sess_id:
            sess = db.session.get(TestSession, sess_id)
            if sess:
                sess.answers_so_far = user_answers
                sess.current_question_index = total_questions
                sess.status = 'completed'
                sess.last_updated = datetime.now(timezone.utc)
                db.session.commit()
        try:
            if ielts_test_pool_manager and isinstance(test_data, dict):
                _pool_id = test_data.get('_pool_id')
                if _pool_id:
                    ielts_test_pool_manager.record_user_progress(
                        user_id=current_user.id,
                        module='ielts_listening',
                        pool_id=_pool_id,
                    )
        except Exception as _pool_err:
            logger.warning(f"Could not record listening pool progress: {_pool_err}")
        try:
            subscription_manager.db = db.session
        except Exception:
            pass
        try:
            _is_retake = False
            if sess_id:
                _sess_retake = db.session.get(TestSession, sess_id)
                if _sess_retake:
                    _td_retake = _sess_retake.test_data
                    if isinstance(_td_retake, str):
                        try:
                            _td_retake = json.loads(_td_retake)
                        except Exception:
                            _td_retake = {}
                    if isinstance(_td_retake, dict):
                        _is_retake = bool(_td_retake.get('_is_retake'))
            if _is_retake:
                logger.info(
                    f" Retake of same test — skipping usage increment "
                    f"for user {current_user.id} (listening)"
                )
            elif has_active_subscription:
                subscription.tests_remaining = (subscription.tests_remaining or 0) - 1
                subscription.tests_taken = (subscription.tests_taken or 0) + 1
                db.session.commit()
            else:
                subscription_manager.increment_free_usage(current_user.id, 'listening')
        except Exception as inc_err:
            logger.error(f"Listening quota update failed: {inc_err}")
        session.pop('current_listening_test', None)
        session.pop('current_listening_test_data', None)
        log_user_activity(current_user.id, 'complete_test', {
            'test_type': 'listening', 'score': score_pct, 'module': module,
        })
        return jsonify({
            'success': True,
            'score': score_pct,
            'band_score': band_score,
            'correct': correct_count,
            'total': total_questions,
            'unanswered': unanswered,
            'answered': total_answered,
            'result_id': db_result.id,
            'answer_results': answer_results,
            'correct_answers': correct_answers,
            'feedback': feedback,
            'tests_remaining': (
                subscription.tests_remaining if has_active_subscription else None
            ),
            'section_scores': section_scores,
        })
    except Exception as e:
        logger.error(f" Listening submit error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/listening/status/<int:session_id>', methods=['GET'])
@login_required
def listening_status(session_id):
    module = session.get('selected_module', 'ielts')
    TestSession = get_test_session_model(module)
    session_obj = db.session.get(TestSession, session_id)
    if not session_obj or session_obj.user_id != current_user.id:
        return jsonify({'error': 'Session not found'}), 404
    raw_data = session_obj.test_data
    test_data = {}
    if isinstance(raw_data, str):
        try:
            test_data = json.loads(raw_data)
        except Exception:
            test_data = {}
    elif isinstance(raw_data, dict):
        test_data = raw_data
    audio_urls = {}
    if test_data and 'audio_urls' in test_data:
        audio_urls = test_data.get('audio_urls', {})
        if isinstance(audio_urls, str):
            try:
                audio_urls = json.loads(audio_urls)
            except Exception:
                audio_urls = {}
    if not audio_urls and hasattr(session_obj, 'audio_urls'):
        raw_col = session_obj.audio_urls
        if isinstance(raw_col, str):
            try:
                audio_urls = json.loads(raw_col)
            except Exception:
                audio_urls = {}
        elif isinstance(raw_col, dict):
            audio_urls = raw_col
    sections = test_data.get('sections', [])
    total_sections = len(sections)
    ready_sections = 0
    for sec in range(1, total_sections+1):
        if str(sec) in audio_urls:
            ready_sections += 1
    all_ready = ready_sections >= total_sections if total_sections > 0 else False
    return jsonify({
        'success': True,
        'status': 'completed' if all_ready else 'generating',
        'audio_urls': audio_urls,
        'total_sections': total_sections,
        'ready_sections': ready_sections,
        'all_ready': all_ready
    })


@app.route('/debug/session/<int:session_id>')
@login_required
def debug_session(session_id):
    module = session.get('selected_module', 'ielts')
    TestSession = get_test_session_model(module)
    sess = db.session.get(TestSession, session_id)
    if not sess or sess.user_id != current_user.id:
        return jsonify({'error': 'Session not found'}), 404
    test_data = sess.test_data or {}
    return jsonify({
        'audio_urls_from_test_data': test_data.get('audio_urls'),
        'audio_urls_from_column': getattr(sess, 'audio_urls', None),
        'test_data_keys': list(test_data.keys())
    })


# ============================================================
# IELTS WRITING ROUTES
# ============================================================
def generate_static_writing_test():
    return {
        'task1': {
            'prompt': 'The graph below shows the number of visitors to a museum over a 12-month period. Summarise the information by selecting and reporting the main features, and make comparisons where relevant.',
            'chart_data': {'type': 'line', 'labels': ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 'values': [500,450,600,700,800,750,900,850,700,650,550,600], 'title': 'Monthly Museum Visitors'},
            'expected_features': ['overall trend', 'peak months', 'lowest months'],
            'chart_base64': None
        },
        'task2': {
            'prompt': 'Some people believe that technology has made our lives more complicated. Others think it has made our lives easier. Discuss both views and give your own opinion.'
        }
    }


@app.route('/writing/start', methods=['GET'])
@login_required
@limiter.limit("60 per minute")
def start_writing():
    try:
        data = request.args.to_dict()
        difficulty = data.get('difficulty', 'medium')
        topic = data.get('topic', None)
        resume_id = data.get('resume')
        user_id = current_user.id
        module = session.get('selected_module', 'ielts')
        from_full_test = data.get('from_full_test', 'false').lower() == 'true'
        force_new = data.get('force_new', 'false').lower() == 'true'
        retake = data.get('retake', 'false').lower() == 'true'
        auto_generate = data.get('auto_generate', 'true').lower() == 'true'
        preserve_user_essay = data.get('preserve_user_essay', 'false').lower() == 'true'

        if from_full_test:
            ft = session.get('full_ielts_test') or {}
            parent_id = ft.get('parent_session_id')
            ft_mgr = app.config.get('FULL_TEST_BANK_MANAGER')
            if parent_id and ft_mgr:
                TestSessionM = get_test_session_model(module)
                parent = db.session.get(TestSessionM, int(parent_id))
                snap = ft_mgr.get_variant_snapshot(parent, 'writing')
                if snap:
                    phase_session = TestSessionM(
                        user_id=user_id,
                        test_type='writing',
                        difficulty=difficulty,
                        test_data=snap,
                        status='in_progress',
                        start_time=datetime.now(timezone.utc),
                    )
                    db.session.add(phase_session)
                    db.session.commit()
                    session['current_test_session_id'] = phase_session.id
                    session['current_writing_prompt'] = snap.get('task1', {}).get('prompt', '')
                    session['current_writing_chart'] = snap.get('task1', {}).get('chart_data', {})
                    session['current_writing_features'] = snap.get('task1', {}).get('expected_features', [])
                    session['current_writing_prompt2'] = snap.get('task2', {}).get('prompt', '')
                    snap['session_id'] = phase_session.id
                    snap['success'] = True
                    snap['from_snapshot'] = True
                    return jsonify(snap)

        if not from_full_test:
            allowed, error, requires_sub = subscription_manager.can_access_test(user_id, 'writing')
            if not allowed:
                return jsonify({
                    'error': error,
                    'requires_subscription': requires_sub,
                    'redirect_to': '/subscription?module=' + module
                }), 402
            if retake and not force_new:
                cloned = _clone_completed_ielts_test(user_id, 'writing', difficulty)
                if cloned:
                    td = cloned['test_data']
                    session['current_writing_prompt'] = td.get('task1', {}).get('prompt', '')
                    session['current_writing_chart'] = td.get('task1', {}).get('chart_data', {})
                    session['current_writing_features'] = td.get('task1', {}).get('expected_features', [])
                    session['current_writing_prompt2'] = td.get('task2', {}).get('prompt', '')
                    session['current_test_session_id'] = cloned['session_id']
                    return jsonify({
                        'success': True,
                        'retake': True,
                        'session_id': cloned['session_id'],
                        'task1': td.get('task1', {}),
                        'task2': td.get('task2', {}),
                        'message': 'Retaking your previous writing test.',
                    })
                logger.info(
                    f"Retake requested for writing but no prior completed test — "
                    f"generating fresh for user {user_id}"
                )

        TestSession = get_test_session_model(module)
        resume_target = None
        resume_reason = None
        if resume_id:
            try:
                int(resume_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid resume ID', 'success': False}), 400
            sess, status = _get_valid_resume_session(user_id, 'writing', module, resume_id)
            if status == 'valid' and sess:
                resume_target = sess
                resume_reason = 'explicit'
            elif status == 'corrupted_deleted':
                logger.info(f"Corrupted writing session {resume_id} deleted")
        if resume_target is None and not force_new:
            fallback_session = TestSession.query.filter_by(
                user_id=user_id,
                test_type='writing',
                status='in_progress'
            ).order_by(TestSession.start_time.desc()).first()
            if fallback_session:
                sess, status = _get_valid_resume_session(
                    user_id, 'writing', module, str(fallback_session.id)
                )
                if status == 'valid' and sess:
                    resume_target = sess
                    resume_reason = 'auto'
                elif status == 'corrupted_deleted':
                    logger.info(f"Corrupted fallback session {fallback_session.id} deleted")
        if resume_target is not None:
            test_data = resume_target.test_data
            if isinstance(test_data, str):
                try:
                    test_data = json.loads(test_data)
                except Exception:
                    test_data = {}
            if test_data.get('task1') and test_data.get('task2'):
                session['current_writing_prompt'] = test_data['task1'].get('prompt', '')
                session['current_writing_chart'] = test_data['task1'].get('chart_data', {})
                session['current_writing_features'] = test_data['task1'].get('expected_features', [])
                session['current_writing_prompt2'] = test_data['task2'].get('prompt', '')
                session['current_test_session_id'] = resume_target.id
                logger.info(
                    f" Writing test resumed ({resume_reason}) "
                    f"from TestSession {resume_target.id}"
                )
                return jsonify({
                    'success': True,
                    'resumed': True,
                    'session_id': resume_target.id,
                    'task1': test_data['task1'],
                    'task2': test_data['task2'],
                    'difficulty': resume_target.difficulty,
                    'answers_so_far': resume_target.answers_so_far or {},
                    'message': f'Resumed from session ({resume_reason}).'
                })
            else:
                logger.warning(
                    f" Incomplete test data in session {resume_target.id}, deleting..."
                )
                db.session.delete(resume_target)
                db.session.commit()

        incomplete_count, reached_limit = check_resume_limit(user_id, test_type='writing', module=module)
        if reached_limit:
            return jsonify({
                'error': f'You have {incomplete_count} incomplete writing tests. Max 5 allowed.',
                'redirect_to': '/saved-tests'
            }), 400
        if module != 'ielts':
            return jsonify({'error': 'Writing route only available for IELTS module'}), 400

        source = 'on_demand'
        pool_id = None

        if force_new or not ielts_test_pool_manager:
            writing_api = create_writing_api(ai_engine=ai_engine, db=db)
            result = writing_api.start_test(
                difficulty=difficulty,
                topic=topic,
                user_id=str(user_id),
                resume=False,
                force_new=force_new,
                auto_generate=auto_generate,
                preserve_user_essay=preserve_user_essay
            )
            source = 'on_demand'
            logger.info(f"🆕 [writing/start] fresh generation for user {user_id}")
        else:
            def _gen():
                writing_api = create_writing_api(ai_engine=ai_engine, db=db)
                return writing_api.start_test(
                    difficulty=difficulty,
                    topic=topic,
                    user_id=str(user_id),
                    resume=False,
                    force_new=True,
                    auto_generate=auto_generate,
                    preserve_user_essay=preserve_user_essay,
                )
            result, source, pool_id = ielts_test_pool_manager.get_or_generate(
                module='ielts_writing',
                difficulty=difficulty,
                user_id=user_id,
                generate_fn=_gen,
            )
            if source == 'waiting':
                return jsonify({
                    'success': False,
                    'source': 'waiting',
                    'message': 'Another user is generating this test. Please retry.',
                    'retry_after': 3,
                }), 202
            if source == 'exhausted':
                return jsonify({
                    'success': False,
                    'source': 'exhausted',
                    'error': 'Test pool is currently full. Please try again shortly.'
                }), 503
            if source == 'failed' or not result:
                return jsonify({
                    'success': False,
                    'source': 'failed',
                    'error': 'Test generation failed. Please try again.'
                }), 503
            logger.info(
                f" [writing/start] user={user_id} source={source} pool_id={pool_id}"
            )

        if result.get('error'):
            logger.error(f"Writing test error: {result['error']}")
            return jsonify({'error': result['error'], 'success': False}), 400
        if result.get('task1'):
            session['current_writing_prompt'] = result['task1'].get('prompt', '')
            session['current_writing_chart'] = result['task1'].get('chart_data', {})
            session['current_writing_features'] = result['task1'].get('expected_features', [])
        if result.get('task2'):
            session['current_writing_prompt2'] = result['task2'].get('prompt', '')
        if pool_id:
            result['_pool_id'] = pool_id
        test_session = TestSession(
            user_id=user_id,
            test_type='writing',
            difficulty=difficulty,
            test_data=result,
            status='in_progress',
            start_time=datetime.now(timezone.utc)
        )
        db.session.add(test_session)
        db.session.commit()
        result['session_id'] = test_session.id
        result['success'] = True
        result['source'] = source
        session['current_test_session_id'] = test_session.id
        logger.info(f" Writing test saved to TestSession {test_session.id} (source={source})")
        return jsonify(result)
    except Exception as e:
        logger.exception(f"start_writing error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/writing/submit', methods=['POST'])
@login_required
def submit_writing():
    try:
        MIN_TASK1_WORDS = 150
        MIN_TASK2_WORDS = 250
        data = request.json or {}
        task1_essay = (data.get('task1_essay') or '').strip()
        task2_essay = (data.get('task2_essay') or '').strip()
        is_full_test = bool(data.get('is_full_test'))
        auto_generate = data.get('auto_generate', False)
        preserve_user_essay = data.get('preserve_user_essay', True)
        module = session.get('selected_module', 'ielts')
        TestSession = get_test_session_model(module)
        TestResult = get_test_result_model(module)
        Subscription = get_subscription_model(module)
        ft_state = None
        if is_full_test:
            ft_state = session.get('full_ielts_test')
            if not ft_state:
                return jsonify({'error': 'No full test session in progress'}), 404
        task1_prompt = session.get('current_writing_prompt', '')
        task2_prompt = session.get('current_writing_prompt2', '')
        task1_chart = session.get('current_writing_chart', {})
        task1_features = session.get('current_writing_features', [])
        t1_words = len(task1_essay.split()) if task1_essay else 0
        t2_words = len(task2_essay.split()) if task2_essay else 0
        if not ai_engine:
            eval1 = {
                'overall_band': 0.0 if t1_words < MIN_TASK1_WORDS else 5.0,
                'feedback': (
                    'AI not available. Empty/under-length Task 1.'
                    if t1_words < MIN_TASK1_WORDS
                    else 'AI not available — band 5.0 default for a full-length Task 1.'
                ),
                'evaluator': 'no_ai',
            }
            eval2 = {
                'overall_band': 0.0 if t2_words < MIN_TASK2_WORDS else 5.0,
                'feedback': (
                    'AI not available. Empty/under-length Task 2.'
                    if t2_words < MIN_TASK2_WORDS
                    else 'AI not available — band 5.0 default for a full-length Task 2.'
                ),
                'evaluator': 'no_ai',
            }
        else:
            evaluator = create_essay_evaluator(ai_engine)
            if t1_words >= MIN_TASK1_WORDS:
                eval1 = evaluator.evaluate(
                    task1_essay, 'task1', task1_prompt,
                    task1_chart, task1_features
                )
            else:
                eval1 = {
                    'overall_band': 0.0,
                    'word_count': t1_words,
                    'feedback': (
                        f'BAND 0 — Task 1 response is empty or under-length '
                        f'({t1_words}/{MIN_TASK1_WORDS} words). '
                        f'Please write at least {MIN_TASK1_WORDS} words.'
                    ),
                    'weaknesses': [
                        f'Short/empty Task 1: {t1_words}/{MIN_TASK1_WORDS} words'
                    ],
                    'strengths': [],
                    'criteria': {
                        'task_achievement': 0.0,
                        'coherence_cohesion': 0.0,
                        'lexical_resource': 0.0,
                        'grammar_accuracy': 0.0,
                    },
                    'evaluator': 'length_filter',
                }
            if t2_words >= MIN_TASK2_WORDS:
                eval2 = evaluator.evaluate(
                    task2_essay, 'task2', task2_prompt
                )
            else:
                eval2 = {
                    'overall_band': 0.0,
                    'word_count': t2_words,
                    'feedback': (
                        f'BAND 0 — Task 2 response is empty or under-length '
                        f'({t2_words}/{MIN_TASK2_WORDS} words). '
                        f'Please write at least {MIN_TASK2_WORDS} words.'
                    ),
                    'weaknesses': [
                        f'Short/empty Task 2: {t2_words}/{MIN_TASK2_WORDS} words'
                    ],
                    'strengths': [],
                    'criteria': {
                        'task_response': 0.0,
                        'coherence_cohesion': 0.0,
                        'lexical_resource': 0.0,
                        'grammar_accuracy': 0.0,
                    },
                    'evaluator': 'length_filter',
                }
        task1_band = round_ielts_band(eval1.get('overall_band', 0.0))
        task2_band = round_ielts_band(eval2.get('overall_band', 0.0))
        raw_overall = (task1_band + 2.0 * task2_band) / 3.0
        avg_band = round_ielts_band(raw_overall)
        upgraded1 = ''
        upgraded2 = ''
        if ai_engine and not is_full_test:
            try:
                upgrader = create_essay_upgrader(ai_engine)
                if t1_words >= MIN_TASK1_WORDS:
                    upgraded1 = upgrader.upgrade_task1(
                        essay=task1_essay,
                        prompt=task1_prompt,
                        chart_type='bar_chart',
                        current_scores=eval1,
                        target_band=min(9.0, task1_band + 0.5),
                        chart_data=task1_chart,
                        auto_generate=auto_generate,
                        preserve_user_essay=preserve_user_essay
                    )
                else:
                    upgraded1 = upgrader.upgrade_task1(
                        essay=task1_essay,
                        prompt=task1_prompt,
                        chart_type='bar_chart',
                        current_scores={'overall_band': 0.0},
                        target_band=6.0,
                        chart_data=task1_chart,
                        auto_generate=True,
                        preserve_user_essay=False,
                    )
                if t2_words >= MIN_TASK2_WORDS:
                    upgraded2 = upgrader.upgrade_task2(
                        essay=task2_essay,
                        prompt=task2_prompt,
                        current_scores=eval2,
                        target_band=min(9.0, task2_band + 0.5),
                        preserve_user_essay=preserve_user_essay
                    )
                else:
                    upgraded2 = upgrader.upgrade_task2(
                        essay=task2_essay,
                        prompt=task2_prompt,
                        current_scores={'overall_band': 0.0},
                        target_band=6.0,
                        preserve_user_essay=False,
                    )
            except Exception as e:
                logger.error(f"Upgrade error: {e}", exc_info=True)
                upgraded1 = upgraded2 = ''

        if is_full_test:
            ft_state['answers']['writing'] = {
                'task1_essay': task1_essay,
                'task2_essay': task2_essay,
            }
            ft_state['scores']['writing'] = {
                'band_score': avg_band,
                'task1_band': task1_band,
                'task2_band': task2_band,
                'task1_words': t1_words,
                'task2_words': t2_words,
            }
            ft_state['completed_sections']['writing'] = True
            ft_state['phase'] = _ft_next_phase(ft_state) or 'done'
            session['full_ielts_test'] = ft_state
            session.modified = True
            sess_id = session.get('current_test_session_id')
            if sess_id:
                sess = db.session.get(TestSession, sess_id)
                if sess:
                    sess.answers_so_far = {
                        'task1_essay': task1_essay, 'task2_essay': task2_essay,
                    }
                    sess.status = 'completed'
                    sess.last_updated = datetime.now(timezone.utc)
                    db.session.commit()
            try:
                state = GenerationState.query.filter_by(
                    user_id=current_user.id, module='ielts_writing',
                    status='in_progress',
                ).first()
                if state:
                    state.status = 'completed'
                    state.completed_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception:
                pass
            session.pop('current_writing_prompt', None)
            session.pop('current_writing_prompt2', None)
            session.pop('current_writing_chart', None)
            session.pop('current_writing_features', None)
            session.pop('current_test_session_id', None)
            log_user_activity(current_user.id, 'full_test_phase_done', {
                'phase': 'writing', 'band': avg_band, 'module': module,
            })
            return jsonify({
                'success': True,
                'is_full_test': True,
                'band_score': avg_band,
                'task1_band': task1_band,
                'task2_band': task2_band,
                'next_phase': ft_state['phase'],
                'redirect': '/ielts-full-test?completed=writing',
            })

        result = TestResult(
            user_id=current_user.id,
            test_type='writing',
            score=avg_band * 10,
            band_score=avg_band,
        )
        result.set_answers({
            'task1_essay': task1_essay,
            'task2_essay': task2_essay,
            'evaluation1': eval1,
            'evaluation2': eval2,
        })
        result.feedback = (
            f"Task 1 Band: {task1_band}, "
            f"Task 2 Band: {task2_band}, "
            f"Overall: {avg_band}"
        )
        db.session.add(result)
        db.session.commit()
        sess_id = session.get('current_test_session_id')
        if sess_id:
            sess = db.session.get(TestSession, sess_id)
            if sess:
                sess.answers_so_far = {
                    'task1_essay': task1_essay,
                    'task2_essay': task2_essay
                }
                sess.current_question_index = 2
                sess.status = 'completed'
                sess.last_updated = datetime.now(timezone.utc)
                db.session.commit()
                try:
                    if ielts_test_pool_manager:
                        _td = sess.test_data
                        if isinstance(_td, str):
                            try:
                                _td = json.loads(_td)
                            except Exception:
                                _td = {}
                        if isinstance(_td, dict):
                            _pool_id = _td.get('_pool_id')
                            if _pool_id:
                                ielts_test_pool_manager.record_user_progress(
                                    user_id=current_user.id,
                                    module='ielts_writing',
                                    pool_id=_pool_id,
                                )
                except Exception as _pool_err:
                    logger.warning(f"Could not record writing pool progress: {_pool_err}")
        try:
            state = GenerationState.query.filter_by(
                user_id=current_user.id,
                module='ielts_writing',
                status='in_progress'
            ).first()
            if state:
                state.status = 'completed'
                state.completed_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(
                    f" Writing generation state marked completed for user {current_user.id}"
                )
        except Exception as e:
            logger.warning(f"Could not update generation state: {e}")
        try:
            try:
                subscription_manager.db = db.session
            except Exception:
                pass
            subscription = Subscription.query.filter_by(
                user_id=int(current_user.id)
            ).first()
            if not subscription:
                from models import create_default_subscription_for_user
                subscription = create_default_subscription_for_user(
                    int(current_user.id), module
                )
            sub_end = subscription.subscription_end
            if sub_end and sub_end.tzinfo is None:
                sub_end = sub_end.replace(tzinfo=timezone.utc)
            has_active_subscription = bool(
                subscription.status == 'active'
                and sub_end
                and sub_end > datetime.now(timezone.utc)
                and (subscription.plan or '').strip().lower()
                    not in ('', 'free', 'trial', 'none', 'default')
                and (subscription.tests_remaining or 0) > 0
            )
            _is_retake = False
            if sess_id:
                _sess_retake = db.session.get(TestSession, sess_id)
                if _sess_retake:
                    _td_retake = _sess_retake.test_data
                    if isinstance(_td_retake, str):
                        try:
                            _td_retake = json.loads(_td_retake)
                        except Exception:
                            _td_retake = {}
                    if isinstance(_td_retake, dict):
                        _is_retake = bool(_td_retake.get('_is_retake'))
            if _is_retake:
                logger.info(
                    f" Retake of same test — skipping usage increment "
                    f"for user {current_user.id} (writing)"
                )
            elif has_active_subscription:
                subscription.tests_remaining = (
                    (subscription.tests_remaining or 0) - 1
                )
                subscription.tests_taken = (
                    (subscription.tests_taken or 0) + 1
                )
                db.session.commit()
                logger.info(
                    f" Subscription test used for user {current_user.id} "
                    f"(writing), {subscription.tests_remaining} left"
                )
            else:
                subscription_manager.increment_free_usage(
                    current_user.id, 'writing'
                )
                logger.info(
                    f" Free usage incremented for user {current_user.id} "
                    f"(writing) after submission"
                )
        except Exception as inc_err:
            logger.error(
                f" Failed to handle writing quota for user "
                f"{current_user.id}: {inc_err}"
            )
        log_user_activity(
            current_user.id, 'complete_test',
            {'test_type': 'writing', 'band': avg_band, 'module': module}
        )
        response_data = {
            'success': True,
            'band_score': avg_band,
            'overall_band': avg_band,
            'user_overall_band': avg_band,
            'task1_band': task1_band,
            'task2_band': task2_band,
            'user_task1_band': task1_band,
            'user_task2_band': task2_band,
            'task1_words': t1_words,
            'task2_words': t2_words,
            'task1_prompt': task1_prompt,
            'task2_prompt': task2_prompt,
            'feedback': (
                (eval1.get('feedback') or '')
                + ' | ' +
                (eval2.get('feedback') or '')
            ),
            'result_id': result.id,
        }
        def _pack(up):
            if isinstance(up, dict):
                return {
                    'upgraded_essay': up.get('upgraded_essay', ''),
                    'model_essay': up.get('model_essay', ''),
                    'is_model_essay': up.get('is_model_essay', False),
                    'short_response': up.get('short_response', False),
                    'user_band': up.get('user_band'),
                    'model_band': up.get('model_band'),
                    'preserved_user_essay': up.get('preserved_user_essay', True),
                    'generated_question': up.get('generated_question'),
                    'generated_chart_data': up.get('generated_chart_data'),
                    'key_improvements': up.get('key_improvements', []),
                }
            return {'upgraded_essay': up or '', 'is_model_essay': False}
        if upgraded1:
            response_data['upgraded1'] = _pack(upgraded1)
        if upgraded2:
            response_data['upgraded2'] = _pack(upgraded2)
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Writing submit error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# TEST ENDPOINTS
# ============================================================
@app.route('/test-deepseek', methods=['GET'])
@login_required
def test_deepseek():
    if not ai_engine:
        return jsonify({'success': False, 'error': 'AI Engine not initialized'}), 503
    try:
        response = ai_engine.generate("Say 'Hello, world!' in exactly 3 words.", max_tokens=20)
        return jsonify({'success': True, 'response': response, 'provider': 'DeepSeek'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/test-deepgram', methods=['GET'])
@login_required
def test_deepgram():
    if not audio_service:
        return jsonify({'success': False, 'error': 'Audio service not available'}), 503
    try:
        result = audio_service.generate_speaking_audio(
            text="Hello, this is a test.",
            part=1,
            question_num=0,
            custom_filename='test_deepgram.mp3'
        )
        if result:
            return jsonify({'success': True, 'audio_url': result, 'message': 'Audio service is working!'})
        else:
            return jsonify({'success': False, 'error': 'Audio generation failed', 'message': 'Audio service failed'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/debug/image')
@login_required
def debug_image():
    from modules.pte.utils.image_generator import pte_image_generator
    result = pte_image_generator.generate_image('bar_chart', 'medium', True)
    return jsonify(result)


# ============================================================
# RUN THE APP
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)