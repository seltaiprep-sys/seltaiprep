# modules/ukvi/api.py
"""
UKVI Flask Blueprint — routes for interview practice.

v2.0 — POOL + ASYNC QUEUE:
  • /start-async         — start interview (pool-aware)
  • /job-status/<job_id> — poll job status
  • /start (legacy)      — kept for backward compatibility
  • Same university → pool share → 1 AI call for all users

v1.0 — Initial blueprint.
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from .service import UKVIService
from .managers import UKVISubscriptionManager, UKVITestManager, ukvi_pool_manager
import logging
import uuid

logger = logging.getLogger(__name__)

# Global instances (set by init_ukvi_api)
ukvi_service = None
subscription_manager = None
test_manager = None


def init_ukvi_api(ai_engine, db):
    """Initialize UKVI service and managers with AI engine and db."""
    global ukvi_service, subscription_manager, test_manager
    ukvi_service = UKVIService(ai_engine=ai_engine, db=db)
    subscription_manager = UKVISubscriptionManager(db)
    test_manager = UKVITestManager(db, ukvi_service)
    logger.info(" UKVI API initialised with AI engine and DB")


def get_blueprint():
    bp = Blueprint('ukvi_api', __name__, url_prefix='/api/ukvi')

    # ═══════════════════════════════════════════════════════════
    # PROFILE
    # ═══════════════════════════════════════════════════════════
    @bp.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        """GET or POST user profile — auth required, own profile only."""
        if request.method == 'GET':
            user_id = request.args.get('user_id')
            if not user_id:
                return jsonify({'error': 'user_id required'}), 400
            try:
                uid_int = int(user_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'invalid user_id'}), 400
            if uid_int != current_user.id:
                return jsonify({'error': 'Forbidden'}), 403
            data = ukvi_service.get_profile(uid_int)
            return jsonify({'success': True, 'profile': data or {}})

        # POST
        data = request.json or {}
        user_id = data.get('user_id')
        profile_data = data.get('profile', {})
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400
        try:
            uid_int = int(user_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'invalid user_id'}), 400
        if uid_int != current_user.id:
            return jsonify({'error': 'Forbidden'}), 403
        result = ukvi_service.save_profile(uid_int, profile_data)
        return jsonify(result)

    @bp.route('/register', methods=['POST'])
    def register():
        """Legacy endpoint — save user profile."""
        data = request.json or {}
        user_id = data.get('user_id')
        profile = data.get('profile', {})
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400
        result = ukvi_service.save_profile(int(user_id), profile)
        return jsonify(result)

    # ═══════════════════════════════════════════════════════════
    # ASYNC START (POOL-AWARE)
    # ═══════════════════════════════════════════════════════════
    @bp.route('/start-async', methods=['POST'])
    def start_async():
        """
        Start interview (pool-aware).

        Returns:
          - Pool HIT:  {source: 'cache', session_id, questions}
          - New job:   {source: 'new',    job_id}
          - Shared:    {source: 'shared', job_id}
        """
        data = request.json or {}
        user_id = data.get('user_id')
        difficulty = data.get('difficulty', 'medium')
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400

        # Subscription check
        can_proceed = subscription_manager.can_start_test(int(user_id))
        if not can_proceed.get('allowed'):
            return jsonify({
                'error': can_proceed.get('error', 'Free limit reached'),
                'requires_subscription': True,
                'redirect_to': '/subscription?module=ukvi',
            }), 402

        # Pool-aware generation
        result = ukvi_service.generate_interview(int(user_id), difficulty, True)

        if not result.get('success'):
            return jsonify(result), 400

        source = result.get('source')

        # Cache HIT → create session immediately
        if source == 'cache':
            questions = result['questions']
            session_result = _create_ukvi_session(
                int(user_id), difficulty, questions
            )
            if session_result.get('error'):
                return jsonify(session_result), 400

            subscription_manager.increment_usage(int(user_id), 'ukvi_interview')

            return jsonify({
                'success': True,
                'source': 'cache',
                'session_id': session_result['session_id'],
                'questions': questions,
                'total_questions': len(questions),
            })

        # Job created/shared → return job_id for polling
        return jsonify({
            'success': True,
            'source': source,
            'job_id': result['job_id'],
            'poll_url': f"/api/ukvi/job-status/{result['job_id']}",
        })

    # ═══════════════════════════════════════════════════════════
    # JOB STATUS POLLING
    # ═══════════════════════════════════════════════════════════
    @bp.route('/job-status/<job_id>', methods=['GET'])
    def job_status(job_id):
        """Poll job status."""
        job = ukvi_pool_manager.get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        if job.status == 'complete':
            questions = job.get_questions()
            session_result = _create_ukvi_session(
                job.user_id, job.difficulty, questions
            )
            if session_result.get('error'):
                return jsonify(session_result), 400

            try:
                subscription_manager.increment_usage(job.user_id, 'ukvi_interview')
            except Exception as e:
                logger.warning(f"Usage increment failed: {e}")

            return jsonify({
                'success': True,
                'status': 'complete',
                'session_id': session_result['session_id'],
                'questions': questions,
                'total_questions': len(questions),
            })

        if job.status == 'failed':
            return jsonify({
                'success': False,
                'status': 'failed',
                'error': job.error or 'Generation failed',
            })

        position = ukvi_pool_manager.get_queue_position(job_id)
        return jsonify({
            'success': True,
            'status': job.status,           # 'queued' or 'generating'
            'position': position,
        })

    # ═══════════════════════════════════════════════════════════
    # LEGACY START (backward compat)
    # ═══════════════════════════════════════════════════════════
    @bp.route('/start', methods=['POST'])
    def start():
        """Legacy sync start — kept for backward compatibility."""
        data = request.json or {}
        user_id = data.get('user_id')
        difficulty = data.get('difficulty', 'medium')
        personalized = data.get('personalized', True)
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400

        can_proceed = subscription_manager.can_start_test(int(user_id))
        if not can_proceed.get('allowed'):
            return jsonify({
                'error': can_proceed.get('error', 'Free limit reached'),
                'requires_subscription': True,
                'redirect_to': '/subscription?module=ukvi',
            }), 402

        result = test_manager.start_interview(
            int(user_id), difficulty, personalized
        )
        if result.get('error'):
            return jsonify(result), 400

        subscription_manager.increment_usage(int(user_id), 'ukvi_interview')
        return jsonify(result)

    # ═══════════════════════════════════════════════════════════
    # ANSWER SUBMISSION
    # ═══════════════════════════════════════════════════════════
    @bp.route('/answer', methods=['POST'])
    def answer():
        """Submit a text answer for a question."""
        data = request.json or {}
        user_id = data.get('user_id')
        session_id = data.get('session_id')
        question_index = data.get('question_index')
        question_text = data.get('question_text', '')
        answer_text = data.get('answer', '')
        if not all([
            user_id,
            session_id,
            question_index is not None,
            question_text,
        ]):
            return jsonify({'error': 'Missing required fields'}), 400
        result = test_manager.submit_answer_adaptive(
            int(user_id), _safe_session_id(session_id, int(user_id))[0], int(question_index),
            question_text, answer_text,
        )
        return jsonify(result)

    @bp.route('/submit-audio', methods=['POST'])
    def submit_audio():
        """Submit an audio answer."""
        audio_file = request.files.get('audio')
        if not audio_file:
            return jsonify({'error': 'No audio file'}), 400
        audio_data = audio_file.read()
        user_id = request.form.get('user_id')
        session_id = request.form.get('session_id')
        question_index = request.form.get('question_index')
        question_text = request.form.get('question_text', '')
        if not all([
            user_id,
            session_id,
            question_index is not None,
            question_text,
        ]):
            return jsonify({'error': 'Missing fields'}), 400
        result = test_manager.submit_audio_answer(
            int(user_id), _safe_session_id(session_id, int(user_id))[0], int(question_index),
            question_text, audio_data, audio_file.filename,
        )
        return jsonify(result)

    # ═══════════════════════════════════════════════════════════
    # TTS
    # ═══════════════════════════════════════════════════════════
    @bp.route('/question-audio', methods=['POST'])
    def question_audio():
        """Generate TTS audio for a question."""
        data = request.json or {}
        user_id = data.get('user_id')
        session_id = data.get('session_id')
        question_index = data.get('question_index')
        if not all([user_id, session_id, question_index is not None]):
            return jsonify({'error': 'Missing fields'}), 400
        result = test_manager.get_question_audio(
            int(user_id), _safe_session_id(session_id, int(user_id))[0], int(question_index)
        )
        return jsonify(result)

    @bp.route('/tts', methods=['POST'])
    def tts():
        """Generate TTS audio — IELTS service primary, listening fallback."""
        data = request.json or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'error': 'No text provided'}), 400

        filename = f"ukvi_tts_{uuid.uuid4().hex[:8]}.mp3"
        audio_url = None

        # 1. Try IELTS UnifiedAudioService (has speaking method)
        try:
            from modules.ielts.audio.unified_service import (
                UnifiedAudioService as _IA,
            )
            _ia = None
            for _kw in ({}, {'ai_engine': None}, {'tts_provider': None}):
                try:
                    _ia = _IA(**_kw)
                    break
                except TypeError:
                    continue
                except Exception:
                    continue
            if _ia and hasattr(_ia, 'generate_speaking_audio'):
                audio_url = _ia.generate_speaking_audio(
                    text=text, part=0, question_num=0,
                    custom_filename=filename,
                )
        except Exception as _e:
            logger.warning('IELTS TTS failed: %s', _e)

        # 2. Fallback: app audio_service listening method
        if not audio_url:
            try:
                from app import audio_service as _app_audio
                if _app_audio and hasattr(_app_audio, 'generate_listening_section_audio'):
                    audio_url = _app_audio.generate_listening_section_audio(
                        text=text, custom_filename=filename,
                    )
            except Exception as _e:
                logger.warning('Listening TTS fallback failed: %s', _e)

        if audio_url:
            return jsonify({'audio_url': audio_url})
        logger.error('All TTS methods failed for text: %s', text[:60])
        return jsonify({'error': 'TTS generation failed'}), 500
    @bp.route('/finish', methods=['POST'])
    def finish():
        """Complete the interview and get evaluation."""
        data = request.json or {}
        user_id = data.get('user_id')
        session_id = data.get('session_id')
        if not all([user_id, session_id]):
            return jsonify({'error': 'Missing fields'}), 400
        result = test_manager.complete_interview(
            int(user_id), _safe_session_id(session_id, int(user_id))[0]
        )
        return jsonify(result)

    @bp.route('/history/<int:user_id>', methods=['GET'])
    @login_required
    def history(user_id):
        """List interview sessions — own history only."""
        if user_id != current_user.id:
            return jsonify({'error': 'Forbidden'}), 403
        result = test_manager.get_history(user_id)
        return jsonify(result)

    @bp.route('/progress/<int:user_id>', methods=['GET'])
    @login_required
    def progress(user_id):
        """Get summary progress — own progress only."""
        if user_id != current_user.id:
            return jsonify({'error': 'Forbidden'}), 403
        result = test_manager.get_progress(user_id)
        return jsonify(result)

    @bp.route('/subscription/status', methods=['GET'])
    def subscription_status():
        """Get subscription status."""
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400
        status = subscription_manager.get_status(int(user_id))
        return jsonify(status)

    return bp


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════
def _create_ukvi_session(user_id: int, difficulty: str, questions: list) -> dict:
    """Create UKVITestSession with generated questions."""
    from .models import UKVITestSession
    from models import db
    import json

    try:
        session_obj = UKVITestSession(
            user_id=user_id,
            test_type='interview',
            difficulty=difficulty,
            status='in_progress',
            start_time=datetime.now(timezone.utc),
            test_data=json.dumps({
                'questions': questions,
                'personalized': True,
                'difficulty': difficulty,
                'visa_type': 'student',
            }),
            current_question_index=0,
            answers_so_far='{}',
        )
        db.session.add(session_obj)
        db.session.commit()
        return {'session_id': session_obj.id}
    except Exception as e:
        db.session.rollback()
        logger.exception(f"_create_ukvi_session failed: {e}")
        return {'error': str(e)}

# ═══════════════════════════════════════════════════════════════════
# UKVI TTS HELPER — robust fallback
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# SESSION ID SAFETY HELPER
# ═══════════════════════════════════════════════════════════════════

def _safe_session_id(raw_session_id, user_id):
    """
    Convert session_id safely.
    If invalid (e.g. 'phase_xxx' from frontend), find latest active session.
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)

    # Try to parse as int first
    if raw_session_id is not None:
        try:
            return int(raw_session_id), None
        except (ValueError, TypeError):
            _log.warning(
                'Invalid session_id received: %r — falling back to latest session',
                raw_session_id
            )

    # Fallback: find latest active session for this user
    try:
        from .models import UKVITestSession
        from app import db
        sess = UKVITestSession.query.filter_by(
            user_id=user_id,
            status='in_progress'
        ).order_by(UKVITestSession.id.desc()).first()
        if not sess:
            # Try any recent session
            sess = UKVITestSession.query.filter_by(
                user_id=user_id
            ).order_by(UKVITestSession.id.desc()).first()
        if sess:
            _log.info('Using fallback session #%s for user %s', sess.id, user_id)
            return sess.id, None
    except Exception as e:
        _log.exception('Fallback session lookup failed: %s', e)

    return None, 'Session not found'

