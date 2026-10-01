# modules/pte/api.py
"""PTE API – Shared test pool with per-user sessions and real PTE scoring.

v5.0 — POOL-PRIMARY:
  • Removed all dependencies on pte_test_bank_manager
  • Removed /bank/get/<section> route (unused by frontend)
  • Pool is the single source of truth for user-facing tests
"""

import os
import logging
import copy
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from .service import create_pte_service
from .models import db, PTETestSession, PTESetting, PTETestResult, PTESubscription
from .utils.image_generator import pte_image_generator
from .utils.scoring import PTEScoring
from .managers.subscription_manager import pte_subscription_manager
from .managers.test_pool_manager import pte_test_pool_manager

logger = logging.getLogger(__name__)
_MODULE = 'pte'

pte_blueprint = Blueprint('pte_api', __name__, url_prefix='/api/pte')
_service = None

# ─── CONSTANTS ──────────────────────────────────────────────────────────
SECTION_LABELS = {
    'speaking_writing': 'Speaking & Writing',
    'reading': 'Reading',
    'listening': 'Listening'
}
SECTION_ORDER = ['speaking_writing', 'reading', 'listening']
SECTION_KEYS = SECTION_ORDER

# Audio generation config (used in parallel asset generation)
AUDIO_REQUIRED_TYPES = ('repeat_sentence', 're_tell_lecture', 'answer_short_question')
ASSET_GEN_MAX_WORKERS = 4 # parallel workers for audio
ASSET_GEN_TIMEOUT_SEC = 240 # overall timeout for parallel asset gen

# Deepgram payload safety limit (bytes)
DEEPGRAM_MAX_PAYLOAD_BYTES = 10 * 1024 * 1024 # 10 MB
DEEPGRAM_MAX_QUESTIONS = 100 # sanity cap


def init_pte_api(ai_engine):
    global _service
    _service = create_pte_service(ai_engine)
    logger.info("PTE API initialized with AI engine")


def get_service():
    global _service
    if _service is None:
        from ai_engine import ai_engine
        init_pte_api(ai_engine)
    return _service


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════
def deduplicate_questions_by_script(questions):
    seen_scripts = set()
    unique = []
    for q in questions:
        prompt = q.get('prompt', {})
        script = (prompt.get('reading_script') or
                  prompt.get('passage') or
                  prompt.get('text') or '')
        if not script:
            unique.append(q)
            continue
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
        unique.append(q)
    return unique


def _get_valid_resume_session(user_id: int, test_type: str, resume_id: Optional[str] = None):
    TestSession = PTETestSession
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

        sections = test_data.get('sections')
        if sections and isinstance(sections, dict):
            has_any = any(
                sec and isinstance(sec, dict) and sec.get('questions')
                for sec in sections.values()
            )
            if not has_any:
                raise ValueError("No section questions")
        else:
            questions = test_data.get('questions', [])
            if not questions or len(questions) == 0:
                raise ValueError("No questions")
            for q in questions:
                if not q.get('id'):
                    raise ValueError("Missing question id")
        return session_obj, 'valid'
    except Exception as e:
        logger.warning(f"Session {session_obj.id} invalid: {e}, deleting...")
        db.session.delete(session_obj)
        db.session.commit()
        return None, 'corrupted_deleted'


def _build_resume_response(session_obj):
    test_data = session_obj.test_data or {}
    answers = session_obj.answers_so_far or {}
    return {
        'success': True,
        'resumed': True,
        'test_data': test_data,
        'answers_so_far': answers,
        'session_id': session_obj.id,
        'current_question_index': session_obj.current_question_index or 0,
        'status': session_obj.status,
        'module': 'pte',
        'question_time_remaining': test_data.get('question_time_remaining', {}),
        'section_time_remaining': test_data.get('section_time_remaining', {}),
        'listening_audio_play_counts': test_data.get('listening_audio_play_counts', {}),
        'user_pace_history': test_data.get('user_pace_history', []),
        'user_touched_questions': test_data.get('user_touched_questions', []),
        'message': 'Resuming your PTE test.'
    }


def _ensure_assets_exist(test_data: Dict, user_id: int, service) -> Dict:
    if not test_data or not isinstance(test_data, dict):
        return test_data
    sections = test_data.get('sections')
    if sections and isinstance(sections, dict):
        for sec_key, sec in sections.items():
            if not isinstance(sec, dict) or not sec.get('questions'):
                continue
            sec['questions'] = _ensure_assets_in_questions(sec['questions'])
        return test_data
    questions = test_data.get('questions', [])
    if questions:
        test_data['questions'] = _ensure_assets_in_questions(questions)
    return test_data


# ═══════════════════════════════════════════════════════════════════════
# UPDATED: _ensure_assets_in_questions()
# Parallel audio + image generation (3-5x faster than sequential)
# ═══════════════════════════════════════════════════════════════════════
def _ensure_assets_in_questions(questions: List[Dict]) -> List[Dict]:
    """
    Ensure every question has its required assets.

    Parallel strategy:
      • Identify which questions are missing audio / image
      • Generate all missing assets concurrently (max 4 workers)
      • Attach results back to original question dicts
    """
    if not questions:
        return questions

    # Lazy import — only when needed
    from .generators.speaking_writing import PTESpeakingWriting
    generator = PTESpeakingWriting()

    # ─── Step 1: Collect missing-asset tasks ──────────────────────
    audio_tasks: List[tuple] = [] # (question, text, qtype)
    image_tasks: List[tuple] = [] # (question, chart_type, description, difficulty)

    for q in questions:
        q_type = q.get('type', '')
        prompt = q.get('prompt') or {}

        # ── Audio needed? ──
        if q_type in AUDIO_REQUIRED_TYPES:
            if not prompt.get('audio_url'):
                if q_type == 'repeat_sentence':
                    text = prompt.get('sentence', '')
                elif q_type == 're_tell_lecture':
                    text = prompt.get('lecture', '')
                elif q_type == 'answer_short_question':
                    text = prompt.get('question', '')
                else:
                    text = ''
                if text and text.strip():
                    audio_tasks.append((q, text, q_type))

        # ── Image needed? ──
        elif q_type == 'describe_image':
            if not prompt.get('image_url') and not prompt.get('base64'):
                chart_type = prompt.get('chart_type', 'bar_chart')
                description = prompt.get('image_description', 'A chart showing data.')
                difficulty = q.get('difficulty', 'medium')
                image_tasks.append((q, chart_type, description, difficulty))

    total_tasks = len(audio_tasks) + len(image_tasks)
    if total_tasks == 0:
        logger.info(" All assets present — no generation needed")
        return questions

    logger.info(
        f" Generating {len(audio_tasks)} audio + "
        f"{len(image_tasks)} image assets in parallel "
        f"(workers={ASSET_GEN_MAX_WORKERS})..."
    )

    # ─── Step 2: Parallel execution ───────────────────────────────
    audio_success = 0
    image_success = 0

    with ThreadPoolExecutor(max_workers=ASSET_GEN_MAX_WORKERS) as executor:
        future_map = {}

        # Submit audio tasks
        for q, text, q_type in audio_tasks:
            future = executor.submit(
                generator._generate_audio_for_question,
                text, q_type, q.get('id'), 45, # 45s per-item timeout
            )
            future_map[future] = ('audio', q, q_type)

        # Submit image tasks
        for q, chart_type, description, difficulty in image_tasks:
            future = executor.submit(
                _generate_image_safe,
                chart_type, description, difficulty,
            )
            future_map[future] = ('image', q, None)

        # Collect results
        try:
            for future in as_completed(future_map, timeout=ASSET_GEN_TIMEOUT_SEC):
                kind, q, q_type = future_map[future]
                try:
                    result = future.result()
                    if kind == 'audio' and result:
                        q['prompt']['audio_url'] = result
                        audio_success += 1
                        logger.info(f" Audio OK for {q_type} (Q:{q.get('id')})")
                    elif kind == 'image' and result and result.get('success'):
                        q['prompt']['image_url'] = result.get('image_url')
                        q['prompt']['base64'] = result.get('base64')
                        image_success += 1
                        logger.info(f" Image OK for Q:{q.get('id')}")
                except Exception as e:
                    logger.warning(f"Asset future failed ({kind}, Q:{q.get('id')}): {e}")
        except Exception as e:
            logger.warning(f"Some asset futures timed out: {e}")

    logger.info(
        f" Asset generation complete: "
        f"{audio_success}/{len(audio_tasks)} audio, "
        f"{image_success}/{len(image_tasks)} images"
    )
    return questions


def _generate_image_safe(chart_type: str, description: str, difficulty: str) -> Dict:
    """Thread-safe image regeneration wrapper."""
    try:
        result = pte_image_generator.regenerate_image(chart_type, description, difficulty)
        if result and result.get('success'):
            return result
    except Exception as e:
        logger.warning(f"Image regeneration error: {e}")
    return {'success': False}


def _create_test_session(user_id: int, test_type: str, test_data: dict, difficulty: str = 'medium') -> int:
    session = PTETestSession(
        user_id=user_id,
        test_type=test_type,
        difficulty=difficulty,
        test_data=test_data,
        status='in_progress',
        answers_so_far={},
        current_question_index=0,
        start_time=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc)
    )
    db.session.add(session)
    db.session.commit()
    return session.id


def _clone_completed_test(user_id: int, test_type: str, difficulty: str = 'medium') -> Optional[Dict]:
    last_completed = PTETestSession.query.filter(
        PTETestSession.user_id == user_id,
        PTETestSession.test_type == test_type,
        PTETestSession.status == 'completed'
    ).order_by(PTETestSession.ended_at.desc()).first()

    if not last_completed:
        return None

    test_data = last_completed.test_data or {}
    questions = test_data.get('questions', [])
    if not questions:
        return None

    cloned_questions = copy.deepcopy(questions)
    cloned_data = {
        'questions': cloned_questions,
        'duration': test_data.get('duration', '30-40 min'),
        'retake_of': last_completed.id,
        'retake_at': datetime.now(timezone.utc).isoformat()
    }

    new_test_id = _create_test_session(user_id, test_type, cloned_data, difficulty)

    return {
        'success': True,
        'questions': cloned_questions,
        'test_id': new_test_id,
        'duration': cloned_data['duration'],
        'retake': True,
        'message': 'Retaking your previous test.',
        'original_test_id': last_completed.id
    }


# ═══════════════════════════════════════════════════════════════════════
# SCORING HELPERS
# ═══════════════════════════════════════════════════════════════════════
def _is_empty_answer(ans) -> bool:
    """Return True if an answer is effectively blank."""
    if ans is None:
        return True
    if isinstance(ans, str):
        return ans.strip() == ''
    if isinstance(ans, (list, tuple)):
        return all(_is_empty_answer(x) for x in ans)
    if isinstance(ans, dict):
        return all(_is_empty_answer(v) for v in ans.values())
    return False


def _flatten_answers(answers: Dict) -> Dict:
    """Flatten nested fill-in-the-blanks dicts into ordered lists. Skips empty answers."""
    flat = {}
    for qid, ans in answers.items():
        if _is_empty_answer(ans):
            continue
        if isinstance(ans, dict):
            try:
                keys = sorted(
                    ans.keys(),
                    key=lambda k: int(k.split('_')[-1])
                    if '_' in k and k.split('_')[-1].isdigit() else 999
                )
                flat[qid] = [ans[k] for k in keys]
            except (ValueError, AttributeError):
                flat[qid] = list(ans.values())
        else:
            flat[qid] = ans
    return flat


def _build_detailed_results(scoring_result: Dict) -> List[Dict]:
    detailed = []
    for pq in scoring_result.get('per_question', []):
        detailed.append({
            'question_id': pq.get('question_id'),
            'type': pq.get('type'),
            'is_correct': pq.get('is_correct', False),
            'user_answer': pq.get('user_answer'),
            'correct_answer': pq.get('correct_answer'),
            'score': pq.get('score', 0.0),
            'details': pq.get('details', ''),
        })
    return detailed


def _build_section_breakdown(scoring_result: Dict) -> Dict:
    breakdown = {}
    per_question = scoring_result.get('per_question', [])
    for qt, avg in (scoring_result.get('type_breakdown') or {}).items():
        type_items = [pq for pq in per_question if pq.get('type') == qt]
        correct_in_type = sum(1 for pq in per_question
                              if pq.get('type') == qt and pq.get('is_correct'))
        breakdown[qt] = {
            'percentage': int(round(avg * 100)),
            'correct': correct_in_type,
            'total': len(type_items),
        }
    return breakdown


def _attach_ai_scores(questions: List[Dict], deepgram_data: Optional[Dict]) -> None:
    if not deepgram_data or not isinstance(deepgram_data, dict):
        return
    speaking_types = {'read_aloud', 'repeat_sentence', 'describe_image',
                      're_tell_lecture', 'answer_short_question'}
    for q in questions:
        qid = q.get('id')
        if not qid or qid not in deepgram_data:
            continue
        if q.get('type') not in speaking_types:
            continue
        dg = deepgram_data[qid] or {}
        conf = float(dg.get('confidence', 0.5) or 0.5)
        conf = max(0.0, min(1.0, conf))
        q['ai_score'] = {
            'content': conf, 'fluency': conf, 'pronunciation': conf,
            'content_relevance': conf, 'oral_fluency': conf,
        }


def _score_session(session_obj: PTETestSession,
                   flattened_answers: Dict,
                   deepgram_data: Optional[Dict] = None) -> Dict[str, Any]:
    test_data = session_obj.test_data or {}
    test_type = session_obj.test_type

    sections = test_data.get('sections')
    if sections and isinstance(sections, dict):
        section_results = {}
        section_scores_0_90 = {}

        for sec_key in SECTION_KEYS:
            sec = sections.get(sec_key)
            if not sec or not sec.get('questions'):
                continue
            questions = sec['questions']
            if sec_key == 'reading':
                r = PTEScoring.score_reading_test(questions, flattened_answers)
            elif sec_key == 'listening':
                r = PTEScoring.score_listening_test(questions, flattened_answers)
            else:
                _attach_ai_scores(questions, deepgram_data)
                r = PTEScoring.score_speaking_writing_test(questions, flattened_answers)
            section_results[sec_key] = r
            section_scores_0_90[sec_key] = r.get('score_0_90', 10)

        if section_scores_0_90:
            overall = int(round(sum(section_scores_0_90.values()) / len(section_scores_0_90)))
        else:
            overall = 10
        overall = max(10, min(90, overall))
        band = PTEScoring.pte_to_ielts(overall)

        detailed = []
        total_correct = 0
        total_questions = 0
        for sec_key, r in section_results.items():
            detailed.extend(_build_detailed_results(r))
            total_correct += r.get('raw_correct', 0)
            total_questions += r.get('total', 0)

        return {
            'pte_score': overall, 'score': overall,
            'band_score': band, 'ielts_equivalent': band, 'ielts_band': band,
            'correct': total_correct, 'total': total_questions,
            'section_scores': section_scores_0_90,
            'detailed_results': detailed,
            'feedback': PTEScoring.get_score_descriptor(overall),
        }

    questions = test_data.get('questions', [])
    if not questions:
        return {
            'pte_score': 10, 'score': 10, 'band_score': 2.0,
            'ielts_equivalent': 2.0, 'ielts_band': 2.0,
            'correct': 0, 'total': 0,
            'section_scores': {}, 'detailed_results': [], 'feedback': '',
        }

    if test_type == 'pte_reading':
        r = PTEScoring.score_reading_test(questions, flattened_answers)
    elif test_type == 'pte_listening':
        r = PTEScoring.score_listening_test(questions, flattened_answers)
    elif test_type == 'pte_speaking_writing':
        _attach_ai_scores(questions, deepgram_data)
        r = PTEScoring.score_speaking_writing_test(questions, flattened_answers)
    else:
        r = PTEScoring.score_reading_test(questions, flattened_answers)

    pte_score = r.get('score_0_90', 10)
    band = PTEScoring.pte_to_ielts(pte_score)

    result = {
        'pte_score': pte_score, 'score': pte_score,
        'band_score': band, 'ielts_equivalent': band, 'ielts_band': band,
        'correct': r.get('raw_correct', 0), 'total': r.get('total', 0),
        'section_scores': _build_section_breakdown(r),
        'detailed_results': _build_detailed_results(r),
        'feedback': PTEScoring.get_score_descriptor(pte_score),
        'score_0_1': r.get('score_0_1', 0.0),
    }

    if test_type == 'pte_speaking_writing':
        result['speaking_score'] = pte_score
        result['writing_score'] = pte_score

    return result


# ═══════════════════════════════════════════════════════════════════════
# SLOT CONSUMPTION
# ═══════════════════════════════════════════════════════════════════════
def _consume_slot_for_completed_test(user_id: int, session_obj: PTETestSession) -> Dict:
    test_type = session_obj.test_type
    session_id = session_obj.id
    consume_result = {'success': True, 'tests_remaining': 0, 'mode': 'none'}

    try:
        status = pte_subscription_manager.get_subscription_status(user_id)
    except Exception as e:
        logger.warning(f"Could not fetch subscription status for user {user_id}: {e}")
        status = {'has_subscription': False}

    try:
        if status.get('has_subscription'):
            consume_result = pte_subscription_manager.consume_subscription_test(
                user_id=user_id, session_id=session_id,
            )
            consume_result['mode'] = 'subscription'
        else:
            pte_subscription_manager.increment_free_usage(
                user_id, test_type, session_id=session_id,
            )
            consume_result = {'success': True, 'tests_remaining': 0, 'mode': 'free'}
    except Exception as e:
        logger.exception(f"Slot consumption failed for user {user_id}, session {session_id}: {e}")
        consume_result = {'success': False, 'error': str(e), 'mode': 'error'}

    return consume_result


# ═══════════════════════════════════════════════════════════════════════
# POOL PROGRESS RECORDING
# ═══════════════════════════════════════════════════════════════════════
def _record_pool_progress_if_applicable(user_id: int, session_obj: PTETestSession) -> Optional[int]:
    """
    If this session came from the shared pool (has a pool_id),
    record that this user has completed that pool test.
    """
    try:
        test_data = session_obj.test_data or {}
        pool_id = test_data.get('pool_id')
        if not pool_id:
            return None

        pte_test_pool_manager.record_user_progress(
            user_id=user_id,
            module=session_obj.test_type,
            pool_id=int(pool_id),
        )
        logger.info(
            f" Pool progress recorded: user={user_id}, "
            f"module={session_obj.test_type}, pool_id={pool_id}"
        )
        return int(pool_id)
    except Exception as e:
        logger.warning(f"Could not record pool progress: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# NEW: Deepgram payload validator
# ═══════════════════════════════════════════════════════════════════════
def _validate_deepgram_payload(deepgram_data) -> tuple:
    """
    Returns (is_valid, error_message, sanitized_data).
    Prevents huge/malformed deepgram payloads from bloating DB.
    """
    if deepgram_data is None:
        return True, None, None

    if not isinstance(deepgram_data, dict):
        return False, 'deepgram_data must be a dict', None

    if len(deepgram_data) > DEEPGRAM_MAX_QUESTIONS:
        return False, f'deepgram_data has too many entries ({len(deepgram_data)})', None

    try:
        import json as _json
        raw = _json.dumps(deepgram_data)
        if len(raw.encode('utf-8')) > DEEPGRAM_MAX_PAYLOAD_BYTES:
            return False, 'deepgram_data payload too large', None
    except (TypeError, ValueError) as e:
        return False, f'deepgram_data not JSON-serializable: {e}', None

    return True, None, deepgram_data


# ═══════════════════════════════════════════════════════════════════════
# ROUTES — POOL-BASED GENERATION
# ═══════════════════════════════════════════════════════════════════════

@pte_blueprint.route('/reading/generate', methods=['POST'])
@login_required
def generate_reading():
    try:
        data = request.get_json() or {}
        difficulty = data.get('difficulty', 'medium')
        user_id = current_user.id
        retake = data.get('retake', False)
        force_new = data.get('force_new', False)

        # 1. RETAKE
        if retake:
            cloned = _clone_completed_test(user_id, 'pte_reading', difficulty)
            if cloned:
                return jsonify(cloned)

        # 2. RESUME
        if not force_new and data.get('resume', True):
            session_obj, status = _get_valid_resume_session(
                user_id, 'pte_reading', data.get('resume_id')
            )
            if status == 'valid' and session_obj:
                return jsonify(_build_resume_response(session_obj))
            elif status == 'corrupted_deleted':
                logger.info("Deleted corrupted reading session for user %s", user_id)

        # 3. SUBSCRIPTION
        allowed, error, requires_sub = pte_subscription_manager.can_access_test(user_id, 'pte_reading')
        if not allowed:
            return jsonify({
                'success': False, 'error': error,
                'requires_subscription': requires_sub,
                'redirect_to': '/pte/subscription?module=pte'
            }), 402

        # 4. POOL
        service = get_service()

        def _gen():
            return service.generate_reading_test_raw(user_id, difficulty)

        test_data, source, pool_id = pte_test_pool_manager.get_or_generate(
            module='pte_reading',
            difficulty=difficulty,
            user_id=user_id,
            generate_fn=_gen,
        )

        if source == 'waiting':
            return jsonify({
                'success': False, 'status': 'generating',
                'message': 'Another user is generating this test. Please retry.',
                'retry_after': 3
            }), 202

        if source == 'exhausted':
            return jsonify({
                'success': False,
                'error': 'Test pool is currently full. Please try again shortly.'
            }), 503

        if source == 'failed' or not test_data:
            return jsonify({
                'success': False,
                'error': 'Test generation failed. Please try again.'
            }), 500

        questions = test_data.get('questions', [])
        if not questions:
            return jsonify({'success': False, 'error': 'Empty pool test'}), 500

        session_test_data = {
            'questions': questions,
            'duration': test_data.get('duration', '30-35 min'),
            'pool_id': pool_id,
        }
        test_id = _create_test_session(user_id, 'pte_reading', session_test_data, difficulty)

        logger.info(
            f" PTE Reading session created: user={user_id}, "
            f"test_id={test_id}, source={source}, pool_id={pool_id}"
        )

        return jsonify({
            'success': True,
            'test_id': test_id,
            'questions': questions,
            'duration': session_test_data['duration'],
            'total_questions': len(questions),
            'source': source,
            'pool_id': pool_id,
        })

    except Exception as e:
        logger.exception("Reading generation error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/reading/active-session', methods=['GET'])
@login_required
def reading_active_session():
    try:
        session_obj, status = _get_valid_resume_session(current_user.id, 'pte_reading')
        if status == 'valid' and session_obj:
            return jsonify({
                'success': True, 'active': True,
                'session_id': session_obj.id,
                'current_question_index': session_obj.current_question_index or 0,
            })
        return jsonify({'success': True, 'active': False})
    except Exception as e:
        logger.exception("Active-session error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/listening/generate', methods=['POST'])
@login_required
def generate_listening():
    try:
        data = request.get_json() or {}
        difficulty = data.get('difficulty', 'medium')
        user_id = current_user.id
        retake = data.get('retake', False)
        force_new = data.get('force_new', False)

        if retake:
            cloned = _clone_completed_test(user_id, 'pte_listening', difficulty)
            if cloned:
                return jsonify(cloned)

        if not force_new and data.get('resume', True):
            session_obj, status = _get_valid_resume_session(
                user_id, 'pte_listening', data.get('resume_id')
            )
            if status == 'valid' and session_obj:
                return jsonify(_build_resume_response(session_obj))
            elif status == 'corrupted_deleted':
                logger.info("Deleted corrupted listening session for user %s", user_id)

        allowed, error, requires_sub = pte_subscription_manager.can_access_test(user_id, 'pte_listening')
        if not allowed:
            return jsonify({
                'success': False, 'error': error,
                'requires_subscription': requires_sub,
                'redirect_to': '/pte/subscription?module=pte'
            }), 402

        service = get_service()
        generate_audio = data.get('generate_audio', True)

        def _gen():
            return service.generate_listening_test_raw(
                user_id, difficulty, generate_audio=generate_audio
            )

        test_data, source, pool_id = pte_test_pool_manager.get_or_generate(
            module='pte_listening',
            difficulty=difficulty,
            user_id=user_id,
            generate_fn=_gen,
        )

        if source == 'waiting':
            return jsonify({
                'success': False, 'status': 'generating',
                'message': 'Another user is generating this test. Please retry.',
                'retry_after': 3
            }), 202

        if source == 'exhausted':
            return jsonify({'success': False, 'error': 'Test pool is currently full.'}), 503

        if source == 'failed' or not test_data:
            return jsonify({'success': False, 'error': 'Test generation failed.'}), 500

        questions = test_data.get('questions', [])
        if not questions:
            return jsonify({'success': False, 'error': 'Empty pool test'}), 500

        session_test_data = {
            'questions': questions,
            'duration': test_data.get('duration', '20-25 min'),
            'pool_id': pool_id,
        }
        test_id = _create_test_session(user_id, 'pte_listening', session_test_data, difficulty)

        logger.info(
            f" PTE Listening session created: user={user_id}, "
            f"test_id={test_id}, source={source}, pool_id={pool_id}"
        )

        return jsonify({
            'success': True,
            'test_id': test_id,
            'questions': questions,
            'duration': session_test_data['duration'],
            'total_questions': len(questions),
            'source': source,
            'pool_id': pool_id,
        })

    except Exception as e:
        logger.exception("Listening generation error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/listening/active-session', methods=['GET'])
@login_required
def listening_active_session():
    try:
        session_obj, status = _get_valid_resume_session(current_user.id, 'pte_listening')
        if status == 'valid' and session_obj:
            return jsonify({
                'success': True, 'active': True,
                'session_id': session_obj.id,
                'current_question_index': session_obj.current_question_index or 0,
            })
        return jsonify({'success': True, 'active': False})
    except Exception as e:
        logger.exception("Active-session error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/listening/regenerate_audio', methods=['POST'])
@login_required
def regenerate_listening_audio():
    try:
        data = request.get_json() or {}
        question_id = data.get('question_id')
        question_data = data.get('question_data')
        if not question_id or not question_data:
            return jsonify({'success': False, 'error': 'Missing question_id or question_data'}), 400

        from .generators.listening import pte_listening
        new_audio_url = pte_listening.regenerate_audio_for_question(question_id, question_data)
        if new_audio_url:
            return jsonify({'success': True, 'audio_url': new_audio_url})
        else:
            return jsonify({'success': False, 'error': 'Failed to regenerate audio'}), 500
    except Exception as e:
        logger.exception("Regenerate audio error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/speaking/generate', methods=['POST'])
@login_required
def generate_speaking_writing():
    try:
        data = request.get_json() or {}
        difficulty = data.get('difficulty', 'medium')
        user_id = current_user.id
        retake = data.get('retake', False)
        force_new = data.get('force_new', False)

        if retake:
            cloned = _clone_completed_test(user_id, 'pte_speaking_writing', difficulty)
            if cloned:
                return jsonify(cloned)

        if not force_new:
            session_obj, status = _get_valid_resume_session(
                user_id, 'pte_speaking_writing', data.get('resume_id')
            )
            if status == 'valid' and session_obj:
                service = get_service()
                test_data = session_obj.test_data or {}
                test_data = _ensure_assets_exist(test_data, user_id, service)
                if test_data.get('_assets_updated'):
                    test_data.pop('_assets_updated', None)
                    session_obj.test_data = test_data
                    session_obj.last_updated = datetime.now(timezone.utc)
                    db.session.commit()
                return jsonify(_build_resume_response(session_obj))
            elif status == 'corrupted_deleted':
                logger.info("Deleted corrupted S&W session for user %s", user_id)

        allowed, error, requires_sub = pte_subscription_manager.can_access_test(user_id, 'pte_speaking_writing')
        if not allowed:
            return jsonify({
                'success': False, 'error': error,
                'requires_subscription': requires_sub,
                'redirect_to': '/pte/subscription?module=pte'
            }), 402

        service = get_service()
        generate_images = data.get('generate_images', True)
        generate_audio = data.get('generate_audio', True)

        def _gen():
            return service.generate_speaking_writing_test_raw(
                user_id, difficulty,
                generate_images=generate_images,
                generate_audio=generate_audio,
            )

        test_data, source, pool_id = pte_test_pool_manager.get_or_generate(
            module='pte_speaking_writing',
            difficulty=difficulty,
            user_id=user_id,
            generate_fn=_gen,
        )

        if source == 'waiting':
            return jsonify({
                'success': False, 'status': 'generating',
                'message': 'Another user is generating this test. Please retry.',
                'retry_after': 3
            }), 202

        if source == 'exhausted':
            return jsonify({'success': False, 'error': 'Test pool is currently full.'}), 503

        if source == 'failed' or not test_data:
            return jsonify({'success': False, 'error': 'Test generation failed.'}), 500

        questions = test_data.get('questions', [])
        if not questions:
            return jsonify({'success': False, 'error': 'Empty pool test'}), 500

        # Parallel asset generation (audio + images in one pass)
        questions = _ensure_assets_in_questions(questions)

        session_test_data = {
            'questions': questions,
            'duration': test_data.get('duration', '30-35 min'),
            'pool_id': pool_id,
        }
        test_id = _create_test_session(user_id, 'pte_speaking_writing', session_test_data, difficulty)

        logger.info(
            f" PTE Speaking/Writing session created: user={user_id}, "
            f"test_id={test_id}, source={source}, pool_id={pool_id}"
        )

        return jsonify({
            'success': True,
            'test_id': test_id,
            'questions': questions,
            'duration': session_test_data['duration'],
            'total_questions': len(questions),
            'source': source,
            'pool_id': pool_id,
        })

    except Exception as e:
        logger.exception("Speaking/Writing generation error")
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/speaking/active-session', methods=['GET'])
@login_required
def speaking_active_session():
    try:
        session_obj, status = _get_valid_resume_session(current_user.id, 'pte_speaking_writing')
        if status == 'valid' and session_obj:
            return jsonify({
                'success': True, 'active': True,
                'session_id': session_obj.id,
                'current_question_index': session_obj.current_question_index or 0,
            })
        return jsonify({'success': True, 'active': False})
    except Exception as e:
        logger.exception("Active-session error")
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# FULL TEST — POOL-BASED
# ═══════════════════════════════════════════════════════════════════════
@pte_blueprint.route('/full/generate', methods=['POST'])
@login_required
def generate_full_test():
    try:
        data = request.get_json() or {}
        if not data.get('full_test', False):
            return jsonify({'success': False, 'error': 'full_test must be True'}), 400

        difficulty = data.get('difficulty', 'medium')
        user_id = current_user.id

        status = pte_subscription_manager.get_subscription_status(user_id)
        if not status.get('has_subscription'):
            return jsonify({
                'success': False,
                'error': 'Full tests require an active subscription.',
                'requires_subscription': True,
                'redirect_to': '/pte/subscription?module=pte'
            }), 402

        service = get_service()

        def _gen_full():
            return service.generate_full_test_raw(user_id, difficulty)

        test_data, source, pool_id = pte_test_pool_manager.get_or_generate(
            module='pte_full',
            difficulty=difficulty,
            user_id=user_id,
            generate_fn=_gen_full,
        )

        if source == 'waiting':
            return jsonify({
                'success': False, 'status': 'generating',
                'message': 'Another user is generating a full test. Please retry.',
                'retry_after': 5
            }), 202

        if source == 'exhausted':
            return jsonify({'success': False, 'error': 'Full test pool is full.'}), 503

        if source == 'failed' or not test_data:
            return jsonify({'success': False, 'error': 'Full test generation failed.'}), 500

        sections_data = test_data.get('sections', {})
        if not sections_data:
            return jsonify({'success': False, 'error': 'No sections in full test'}), 500

        # Ensure all section questions have their assets (parallel per section)
        for sec_key, sec in sections_data.items():
            if sec and isinstance(sec, dict) and sec.get('questions'):
                sec['questions'] = _ensure_assets_in_questions(sec['questions'])

        # Create per-user section sessions
        section_sessions = {}
        for sec_key, sec in sections_data.items():
            if not sec or not sec.get('questions'):
                continue
            sec_test_type = 'pte_' + sec_key
            sec_test_data = {
                'questions': sec['questions'],
                'duration': sec.get('duration', '30-40 min'),
                'pool_id': pool_id,
            }
            sec_id = _create_test_session(user_id, sec_test_type, sec_test_data, difficulty)
            section_sessions[sec_key] = sec_id

        # Create full session
        full_test_data = {
            'sections': sections_data,
            'duration': test_data.get('duration', '~2.5 hours'),
            'generation_status': 'complete',
            'pool_id': pool_id,
        }

        full_session = PTETestSession(
            user_id=user_id,
            test_type='pte_full',
            difficulty=difficulty,
            test_data=full_test_data,
            status='in_progress',
            answers_so_far={},
            current_question_index=0,
            start_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        db.session.add(full_session)
        db.session.commit()

        response_sections = {}
        for sec_key, sec in sections_data.items():
            if not sec or not sec.get('questions'):
                continue
            response_sections[sec_key] = {
                'questions': sec['questions'],
                'test_id': section_sessions.get(sec_key),
                'label': sec.get('label', SECTION_LABELS.get(sec_key, sec_key)),
                'duration': sec.get('duration', '30-40 min'),
            }

        logger.info(
            f" PTE Full test session created: user={user_id}, "
            f"session_id={full_session.id}, source={source}, pool_id={pool_id}, "
            f"sections={list(response_sections.keys())}"
        )

        return jsonify({
            'success': True,
            'session_id': full_session.id,
            'sections': response_sections,
            'duration': full_test_data['duration'],
            'generation_status': 'complete',
            'source': source,
            'pool_id': pool_id,
        })

    except Exception as e:
        logger.exception("Full test generation error")
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# SUBMIT — Idempotent, empty answers skipped, pool progress recorded
# ═══════════════════════════════════════════════════════════════════════
@pte_blueprint.route('/submit', methods=['POST'])
@login_required
def submit_test():
    try:
        data = request.get_json() or {}
        test_id = data.get('test_id')
        answers = data.get('answers', {})
        user_id = current_user.id
        deepgram_data = data.get('deepgram_data', None)
        auto_submitted = bool(data.get('auto_submitted', False))
        time_taken = data.get('time_taken', 0)
        session_id_from_client = data.get('session_id')

        # STEP 1: Full payload logging (helps diagnose 403s)
        logger.info(
            f" SUBMIT payload — "
            f"test_id={test_id!r} (type={type(test_id).__name__}), "
            f"session_id={session_id_from_client!r}, "
            f"user_id={user_id}, "
            f"answers_count={len(answers) if isinstance(answers, dict) else 'N/A'}, "
            f"auto={auto_submitted}, "
            f"time_taken={time_taken}, "
            f"has_deepgram={deepgram_data is not None}"
        )

        # STEP 2: Strict test_id validation
        if test_id is None:
            logger.warning(f" 400 — test_id missing. Payload keys: {list(data.keys())}")
            return jsonify({'success': False, 'error': 'test_id required'}), 400

        try:
            test_id_int = int(test_id)
        except (ValueError, TypeError):
            logger.warning(f" 400 — test_id invalid type: {test_id!r}")
            return jsonify({
                'success': False,
                'error': f'test_id must be an integer, got {type(test_id).__name__}',
                'received': str(test_id),
            }), 400

        # STEP 3: Validate deepgram payload (size + type)
        dg_valid, dg_err, dg_clean = _validate_deepgram_payload(deepgram_data)
        if not dg_valid:
            logger.warning(f" 400 — deepgram_data invalid: {dg_err}")
            return jsonify({'success': False, 'error': dg_err}), 400
        deepgram_data = dg_clean

        # STEP 4: Fetch session (SQLAlchemy 2.0 style)
        session_obj = db.session.get(PTETestSession, test_id_int)
        if not session_obj:
            logger.warning(
                f" 404 — test session {test_id_int} not found in DB "
                f"(user={user_id})"
            )
            return jsonify({
                'success': False,
                'error': 'Test session not found',
                'test_id': test_id_int,
            }), 404

        # STEP 5: Ownership check with DETAILED diagnostic
        if session_obj.user_id != user_id:
            logger.error(
                f" 403 SUBMIT OWNERSHIP MISMATCH — "
                f"test_id={test_id_int}, "
                f"session.user_id={session_obj.user_id} (type={type(session_obj.user_id).__name__}), "
                f"current_user.id={user_id} (type={type(user_id).__name__}), "
                f"session.type={session_obj.test_type}, "
                f"session.status={session_obj.status}, "
                f"session.created={session_obj.start_time}, "
                f"payload.user_id={data.get('user_id')}, "
                f"payload.session_id={session_id_from_client}"
            )
            return jsonify({
                'success': False,
                'error': 'Unauthorized',
                'debug': {
                    'test_id': test_id_int,
                    'session_owner_id': session_obj.user_id,
                    'your_id': user_id,
                    'session_type': session_obj.test_type,
                    'session_status': session_obj.status,
                    'hint': 'This session belongs to a different user. '
                            'Your client may be sending a stale or incorrect test_id.'
                }
            }), 403

        # STEP 6: IDEMPOTENCY GUARD
        if session_obj.status == 'completed':
            existing = PTETestResult.query.filter_by(
                session_id=session_obj.id, user_id=user_id,
            ).order_by(PTETestResult.id.desc()).first()

            if existing:
                stored = existing.get_full_result() or {}
                logger.info(
                    f"↩ Duplicate submit ignored for session {session_obj.id}"
                )
                return jsonify({
                    'success': True,
                    'already_submitted': True,
                    'result_id': existing.id,
                    'auto_submitted': bool(existing.auto_submitted),
                    'time_taken': existing.time_taken or 0,
                    'test_type': session_obj.test_type,
                    'tests_remaining': 0,
                    **stored,
                })

            return jsonify({
                'success': False,
                'error': 'This test has already been completed.'
            }), 409

        # STEP 7: Flatten + filter empties
        flattened_answers = _flatten_answers(answers)

        # STEP 8: Score
        try:
            scoring_result = _score_session(session_obj, flattened_answers, deepgram_data)
        except Exception as score_err:
            logger.exception(f"Scoring failed: {score_err}")
            return jsonify({
                'success': False,
                'error': 'Scoring failed. Please try submitting again.'
            }), 500

        # STEP 9: Mark completed
        session_obj.status = 'completed'
        session_obj.ended_at = datetime.now(timezone.utc)
        session_obj.answers_so_far = flattened_answers
        session_obj.last_updated = datetime.now(timezone.utc)

        # Abandon any other in-progress sessions for same user+type
        try:
            stale = PTETestSession.query.filter(
                PTETestSession.user_id == user_id,
                PTETestSession.test_type == session_obj.test_type,
                PTETestSession.id != session_obj.id,
                PTETestSession.status.in_(['in_progress', 'paused'])
            ).all()
            for s in stale:
                s.status = 'abandoned'
                s.ended_at = datetime.now(timezone.utc)
                logger.info(f" Abandoned stale session {s.id} on submit")
        except Exception as ce:
            logger.warning(f"Could not abandon stale sessions: {ce}")

        # STEP 10: Persist result
        pte_score = scoring_result.get('pte_score', 0)
        band_score = scoring_result.get('band_score', 0)

        test_result = PTETestResult(
            user_id=user_id,
            test_type=session_obj.test_type,
            score=pte_score,
            band_score=band_score,
            session_id=session_obj.id,
            difficulty=session_obj.difficulty,
            auto_submitted=auto_submitted,
            time_taken=time_taken if time_taken else None,
            created_at=datetime.now(timezone.utc),
        )
        test_result.set_answers(flattened_answers)
        test_result.set_full_result(scoring_result)
        db.session.add(test_result)

        # STEP 11: Consume slot
        consume_result = _consume_slot_for_completed_test(user_id, session_obj)
        if not consume_result.get('success'):
            logger.warning(f"Slot consumption failed: {consume_result}")

        # STEP 12: Record pool progress
        recorded_pool_id = _record_pool_progress_if_applicable(user_id, session_obj)

        db.session.commit()

        logger.info(
            f" Submit — user={user_id}, test={session_obj.test_type}, "
            f"score={pte_score}, band={band_score}, auto={auto_submitted}, "
            f"mode={consume_result.get('mode')}, pool_id={recorded_pool_id}"
        )

        return jsonify({
            'success': True,
            'result_id': test_result.id,
            'auto_submitted': auto_submitted,
            'time_taken': time_taken,
            'test_type': session_obj.test_type,
            'tests_remaining': consume_result.get('tests_remaining', 0),
            'consumption_mode': consume_result.get('mode', 'none'),
            'pool_id': recorded_pool_id,
            **scoring_result,
        })

    except Exception as e:
        logger.exception("Submit error")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/status/<int:test_id>', methods=['GET'])
@login_required
def get_status(test_id):
    try:
        session_obj = db.session.get(PTETestSession, test_id)
        if not session_obj or session_obj.user_id != current_user.id:
            return jsonify({'success': False, 'error': 'Not found'}), 404

        test_data = session_obj.test_data or {}

        return jsonify({
            'success': True,
            'test_id': session_obj.id,
            'status': session_obj.status,
            'test_data': test_data,
            'answers_so_far': session_obj.answers_so_far or {},
            'current_question_index': session_obj.current_question_index or 0,
            'question_time_remaining': test_data.get('question_time_remaining', {}),
            'section_time_remaining': test_data.get('section_time_remaining', {}),
            'listening_audio_play_counts': test_data.get('listening_audio_play_counts', {}),
            'user_pace_history': test_data.get('user_pace_history', []),
            'user_touched_questions': test_data.get('user_touched_questions', []),
            'time_remaining_seconds': test_data.get('time_remaining_seconds'),
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/subscription/status', methods=['GET'])
@login_required
def subscription_status():
    try:
        status = pte_subscription_manager.get_subscription_status(current_user.id)

        result = {
            'success': True,
            'has_subscription': status.get('has_subscription', False),
            'free_tests_used': status.get('free_tests_used', 0),
            'free_limit': status.get('free_limit', 3),
            'has_any_subscription': status.get('has_subscription', False)
        }

        usage = status.get('free_usage', {})
        limits = status.get('free_limits', {})
        for mod in ['pte_reading', 'pte_listening', 'pte_speaking_writing']:
            limit = limits.get(mod, 2)
            used = usage.get(mod, 0)
            result[mod] = {
                'free_used': used,
                'free_limit': limit,
                'requires_subscription': used >= limit,
                'has_subscription': status.get('has_subscription', False)
            }

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pte_blueprint.route('/topics', methods=['GET'])
@login_required
def get_topics():
    topics = [
        'technology', 'environment', 'education', 'health', 'economy',
        'society', 'culture', 'science', 'business', 'politics',
        'artificial intelligence', 'climate change', 'globalization'
    ]
    return jsonify({'success': True, 'topics': topics})


# ─── ADMIN ──────────────────────────────────────────────────────────
@pte_blueprint.route('/admin/set-limit', methods=['POST'])
@login_required
def admin_set_free_limit():
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized – admin only'}), 403

    data = request.get_json() or {}
    new_limit = data.get('limit')
    test_type = data.get('test_type')

    if new_limit is None:
        return jsonify({'success': False, 'error': 'Missing "limit" parameter'}), 400

    try:
        new_limit = int(new_limit)
        if new_limit < 0:
            return jsonify({'success': False, 'error': 'Limit must be >= 0'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Limit must be an integer'}), 400

    if test_type:
        if test_type not in ['pte_reading', 'pte_listening', 'pte_speaking_writing']:
            return jsonify({'success': False, 'error': 'Invalid test_type'}), 400
        key = f'pte_free_limit_{test_type}'
        PTESetting.set_value(key, str(new_limit))
        pte_subscription_manager.DEFAULT_FREE_LIMITS[test_type] = new_limit
        message = f'Free test limit for {test_type} updated to {new_limit}'
    else:
        PTESetting.set_value('pte_free_tests_limit', str(new_limit))
        message = f'Global free test limit updated to {new_limit}'

    return jsonify({
        'success': True,
        'message': message,
        'new_limit': new_limit,
        'test_type': test_type if test_type else 'global'
    })


@pte_blueprint.route('/admin/get-limit', methods=['GET'])
@login_required
def admin_get_free_limit():
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    test_type = request.args.get('test_type')
    if test_type:
        stored = PTESetting.get_value(f'pte_free_limit_{test_type}', None)
        limit = int(stored) if stored is not None else pte_subscription_manager.DEFAULT_FREE_LIMITS.get(test_type, 2)
    else:
        limit = PTESetting.get_int('pte_free_tests_limit', 3)

    return jsonify({
        'success': True,
        'free_tests_limit': limit,
        'test_type': test_type if test_type else 'global'
    })


# ─── POOL ADMIN ─────────────────────────────────────────────────────
@pte_blueprint.route('/admin/pool/stats', methods=['GET'])
@login_required
def admin_pool_stats():
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    return jsonify({
        'success': True,
        'stats': pte_test_pool_manager.get_pool_stats()
    })


@pte_blueprint.route('/admin/pool/list', methods=['GET'])
@login_required
def admin_pool_list():
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    module = request.args.get('module')
    limit = int(request.args.get('limit', 50))
    if module:
        items = pte_test_pool_manager.list_pool_items(module, limit)
    else:
        items = []
        for mod in ['pte_reading', 'pte_listening', 'pte_speaking_writing', 'pte_full']:
            items.extend(pte_test_pool_manager.list_pool_items(mod, limit))
    return jsonify({'success': True, 'items': items})


@pte_blueprint.route('/admin/pool/reset', methods=['POST'])
@login_required
def admin_pool_reset():
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    module = data.get('module')
    result = pte_test_pool_manager.reset_all()
    return jsonify({'success': True, **result, 'module': module or 'all'})


@pte_blueprint.route('/admin/user-progress/reset', methods=['POST'])
@login_required
def admin_user_progress_reset():
    """Reset user pool progress (all or filtered by user/module)."""
    if not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    user_id = data.get('user_id')
    module = data.get('module')
    count = pte_test_pool_manager.reset_user_progress(
        user_id=int(user_id) if user_id else None,
        module=module,
    )
    return jsonify({'success': True, 'deleted': count,
                    'user_id': user_id, 'module': module or 'all'})


# ─── FULL TEST RESUME / SAVE / UPDATE / COMPLETE ──────────────────
@pte_blueprint.route('/full/status', methods=['GET'])
@login_required
def full_test_status():
    user_id = current_user.id
    session = PTETestSession.query.filter(
        PTETestSession.user_id == user_id,
        PTETestSession.test_type == 'pte_full',
        PTETestSession.status.in_(['in_progress', 'paused'])
    ).order_by(PTETestSession.start_time.desc()).first()

    if session:
        test_data = session.test_data or {}
        sections = test_data.get('sections', {})

        all_ready = all(
            sec.get('questions') for sec in sections.values()
            if sec.get('label') in SECTION_LABELS.values()
        )

        if all_ready and test_data.get('generation_status') != 'complete':
            test_data['generation_status'] = 'complete'
            session.test_data = test_data
            db.session.commit()

        return jsonify({
            'success': True,
            'session_id': session.id,
            'test_data': test_data,
            'answers_so_far': session.answers_so_far or {},
            'current_question_index': session.current_question_index or 0,
            'status': session.status,
            'generation_status': test_data.get('generation_status', 'complete'),
            'question_time_remaining': test_data.get('question_time_remaining', {}),
            'section_time_remaining': test_data.get('section_time_remaining', {}),
            'listening_audio_play_counts': test_data.get('listening_audio_play_counts', {}),
            'user_pace_history': test_data.get('user_pace_history', []),
            'user_touched_questions': test_data.get('user_touched_questions', []),
        })

    return jsonify({'success': False, 'message': 'No active full test session'}), 404


@pte_blueprint.route('/full/save', methods=['POST'])
@login_required
def save_full_test():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data'}), 400

    test_data = data.get('test_data')
    if not test_data:
        return jsonify({'success': False, 'error': 'Missing test_data'}), 400

    existing = PTETestSession.query.filter(
        PTETestSession.user_id == current_user.id,
        PTETestSession.test_type == 'pte_full',
        PTETestSession.status.in_(['in_progress', 'paused'])
    ).all()
    for sess in existing:
        sess.status = 'abandoned'

    session = PTETestSession(
        user_id=current_user.id,
        test_type='pte_full',
        difficulty=data.get('difficulty', 'medium'),
        test_data=test_data,
        status='in_progress',
        answers_so_far={},
        current_question_index=0,
        start_time=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc)
    )
    db.session.add(session)
    db.session.commit()

    return jsonify({
        'success': True,
        'session_id': session.id,
        'message': 'Full test saved'
    })


@pte_blueprint.route('/full/update', methods=['POST'])
@login_required
def update_full_test():
    data = request.get_json()
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Missing session_id'}), 400

    session = db.session.get(PTETestSession, session_id)
    if not session or session.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Session not found'}), 404

    if 'answers' in data:
        session.answers_so_far = data['answers']
    if 'current_question_index' in data:
        session.current_question_index = data['current_question_index']

    test_data = dict(session.test_data or {})
    updated = False
    for key in (
        'section_time_remaining',
        'question_time_remaining',
        'listening_audio_play_counts',
        'user_pace_history',
        'user_touched_questions',
        'time_remaining_seconds',
    ):
        if key in data:
            test_data[key] = data[key]
            updated = True

    if updated:
        session.test_data = test_data

    session.last_updated = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'success': True})


@pte_blueprint.route('/full/complete', methods=['POST'])
@login_required
def complete_full_test():
    data = request.get_json()
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Missing session_id'}), 400

    session = db.session.get(PTETestSession, session_id)
    if not session or session.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Session not found'}), 404

    session.status = 'completed'
    session.ended_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'success': True})


# ─── DEEPGRAM ─────────────────────────────────────────────────────
@pte_blueprint.route('/deepgram/token', methods=['GET'])
@login_required
def get_deepgram_token():
    api_key = os.environ.get('DEEPGRAM_API_KEY')
    if not api_key:
        return jsonify({
            'success': False,
            'error': 'Deepgram API key not configured on server'
        }), 500
    return jsonify({
        'success': True,
        'key': api_key
    })


@pte_blueprint.route('/transcribe', methods=['POST'])
@login_required
def transcribe_audio():
    """Transcribe audio using Deepgram API with word-level timings."""
    try:
        if 'audio' not in request.files:
            return jsonify({'success': False, 'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']
        if audio_file.filename == '':
            return jsonify({'success': False, 'error': 'No audio file selected'}), 400

        api_key = os.environ.get('DEEPGRAM_API_KEY')
        if not api_key:
            logger.warning("Deepgram API key not configured")
            return jsonify({
                'success': False,
                'error': 'Transcription service not configured.'
            }), 500

        try:
            import requests
        except ImportError:
            return jsonify({'success': False, 'error': 'Requests library not installed'}), 500

        audio_data = audio_file.read()

        params = {
            "model": "nova-2",
            "language": "en-US",
            "punctuate": "true",
            "diarize": "false",
            "word_timings": "true",
            "confidence": "true",
            "utterances": "true"
        }

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/webm"
        }

        response = requests.post(
            "https://api.deepgram.com/v1/listen",
            headers=headers,
            params=params,
            data=audio_data,
            timeout=30
        )

        if response.status_code != 200:
            logger.error(f"Deepgram API error: {response.status_code} - {response.text}")
            return jsonify({
                'success': False,
                'error': f'Deepgram API error: {response.status_code}'
            }), response.status_code

        result = response.json()
        channels = result.get('results', {}).get('channels', [])
        if not channels:
            return jsonify({'success': False, 'error': 'No speech detected'}), 400

        alt = channels[0].get('alternatives', [{}])[0]
        transcript = alt.get('transcript', '').strip()
        words = alt.get('words', [])
        confidence = alt.get('confidence', 0.0)
        utterances = result.get('results', {}).get('utterances', [])

        if words and not confidence:
            confs = [w.get('confidence', 0.0) for w in words if w.get('confidence') is not None]
            confidence = sum(confs) / len(confs) if confs else 0.0

        normalized_words = [{
            'word': w.get('word', ''),
            'start': w.get('start', 0.0),
            'end': w.get('end', 0.0),
            'confidence': w.get('confidence', 0.0)
        } for w in words]

        normalized_utterances = [{
            'transcript': u.get('transcript', ''),
            'start': u.get('start', 0.0),
            'end': u.get('end', 0.0),
            'confidence': u.get('confidence', 0.0)
        } for u in utterances]

        logger.info(
            f" Deepgram transcription: {len(transcript)} chars, "
            f"{len(words)} words, {len(utterances)} utterances"
        )

        return jsonify({
            'success': True,
            'transcript': transcript,
            'confidence': round(confidence, 4),
            'words': normalized_words,
            'utterances': normalized_utterances,
            'source': 'deepgram_enhanced'
        })

    except requests.exceptions.Timeout:
        logger.error("Deepgram API timeout")
        return jsonify({'success': False, 'error': 'Transcription service timeout'}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Deepgram request error: {e}")
        return jsonify({'success': False, 'error': f'Transcription service error: {str(e)}'}), 500
    except Exception as e:
        logger.exception("Transcription error")
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── FACTORY ────────────────────────────────────────────────────────
def create_pte_api(ai_engine=None, db=None, service=None):
    if ai_engine:
        init_pte_api(ai_engine)
    return pte_blueprint