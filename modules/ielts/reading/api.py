# modules/ielts/reading/api.py
"""
Reading API blueprint – handles generating, resuming, and submitting reading tests.

BANK-FIRST (v2):
- /start tries the shared rolling TEST_BANK_MANAGER first.
- If bank serves a test → saves bank_id in TestSession.
- If bank fails → falls back to on-demand generation (no 503).
- force_new=True always bypasses bank.

FULL-TEST INTEGRATION:
- Serves from parent snapshot when from_full_test=true.
- /submit with is_full_test=true stashes score without quota consumption.

v2.1 — DRAFT-SAVE SAFE + IDEMPOTENT SUBMIT:
- /submit with draft_only=True now saves answers WITHOUT charging usage.
- /submit on already-completed session returns cached result, no extra charge.
- Prevents free-tier over-counting from auto-save or double-clicks.
"""

import json
import copy
import logging
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, session
from flask_login import login_required, current_user

from models import (
    get_test_session_model,
    get_test_result_model,
    get_subscription_model,
    get_test_bank_usage_model,
)
from models import db
from .test_generator import IELTSReadingGenerator

from modules.ielts.subscription_manager import get_ielts_subscription_manager

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Full-test helpers
# ────────────────────────────────────────────────────────────
FULL_TEST_PHASE_ORDER = ['listening', 'reading', 'writing', 'speaking']


def _ft_state():
    return session.get('full_ielts_test')


def _ft_next_phase(state):
    for p in FULL_TEST_PHASE_ORDER:
        if not state.get('completed_sections', {}).get(p):
            return p
    return None


def _ft_save(state):
    session['full_ielts_test'] = state
    session.modified = True


def _validate_reading_test_data(test_data):
    """Return True if given test_data looks like a usable reading test."""
    if not test_data or not isinstance(test_data, dict):
        return False
    passages = test_data.get('passages')
    if isinstance(passages, list) and len(passages) > 0:
        for p in passages:
            if isinstance(p, dict) and isinstance(p.get('questions'), list) and len(p['questions']) > 0:
                return True
    questions = test_data.get('questions')
    if isinstance(questions, list) and len(questions) > 0:
        return True
    return False


def _normalize_reading_bank_data(test_data):
    """
    Ensure a bank-provided reading test has the expected keys:
    passages, total_questions, difficulty, etc.
    """
    if not isinstance(test_data, dict):
        return {}
    passages = test_data.get('passages') or []
    questions = test_data.get('questions') or []

    # Some bank payloads store questions at top level
    if not passages and questions:
        passages = [{'questions': questions}]

    total = 0
    for p in passages:
        if isinstance(p, dict):
            total += len(p.get('questions') or [])

    return {
        'passages': passages,
        'questions': questions,
        'total_questions': test_data.get('total_questions') or total,
        'difficulty': test_data.get('difficulty', 'medium'),
        'topic': test_data.get('topic'),
    }


def _clone_completed_reading_test(user_id: int, difficulty: str = 'medium'):
    """Clone last completed reading test into fresh session."""
    try:
        TestSession = get_test_session_model('ielts')
    except Exception as e:
        logger.error(f"Retake: cannot get TestSession model for ielts: {e}")
        return None

    try:
        last_completed = TestSession.query.filter(
            TestSession.user_id == user_id,
            TestSession.test_type == 'reading',
            TestSession.status == 'completed'
        ).order_by(TestSession.last_updated.desc()).first()
    except Exception as e:
        logger.error(f"Retake: query failed for user={user_id} (reading): {e}")
        return None

    if not last_completed:
        logger.info(f"Retake: no prior completed reading test for user {user_id}")
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

    if not _validate_reading_test_data(src):
        logger.warning(f"Retake: session {last_completed.id} has unusable reading data")
        return None

    cloned_data = copy.deepcopy(src)
    cloned_data['retake_of'] = last_completed.id
    cloned_data['retake_at'] = datetime.now(timezone.utc).isoformat()

    try:
        new_session = TestSession(
            user_id=user_id,
            test_type='reading',
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
        f" [reading] retake — new session {new_session.id} "
        f"cloned from completed session {last_completed.id}"
    )

    return {
        'success': True,
        'retake': True,
        'session_id': new_session.id,
        'test_id': new_session.id,
        'test_data': cloned_data,
        'original_test_id': last_completed.id,
        'message': 'Retaking your previous reading test.',
    }


def create_reading_api(ai_engine, db, limiter=None):
    """Factory to create the reading blueprint with dependencies."""
    bp = Blueprint('reading_api', __name__, url_prefix='/api/reading')

    def get_module():
        return session.get('selected_module', 'ielts')

    # ────────────────────────────────────────────────────────────
    # START TEST
    # ────────────────────────────────────────────────────────────
    @bp.route('/start', methods=['POST'])
    @login_required
    def start_test():
        data = request.get_json() or {}
        difficulty = data.get('difficulty', 'medium')
        topic = data.get('topic')
        resume_id = data.get('resume_id')
        force_new = bool(data.get('force_new'))
        auto_resume = bool(data.get('auto_resume'))
        retake = bool(data.get('retake'))
        from_full_test = bool(data.get('from_full_test'))
        user_id = current_user.id
        module = get_module()

        TestSession = get_test_session_model(module)

        logger.info(
            f" [reading/start] user={user_id} difficulty={difficulty} "
            f"resume_id={resume_id} force_new={force_new} "
            f"auto_resume={auto_resume} retake={retake} "
            f"from_full_test={from_full_test}"
        )

        # ═══════════════════════════════════════════════════════
        # FULL-TEST MODE
        # ═══════════════════════════════════════════════════════
        ft = session.get('full_ielts_test') or {}
        parent_id = ft.get('parent_session_id')
        if parent_id:
            try:
                parent = db.session.get(TestSession, int(parent_id))
            except Exception:
                parent = None

            if parent:
                snap = (parent.test_data or {}).get('snapshot', {}).get('reading')
                if snap:
                    phase_session = TestSession(
                        user_id=user_id,
                        test_type='reading',
                        difficulty=difficulty,
                        test_data=snap,
                        status='in_progress',
                        start_time=datetime.now(timezone.utc),
                    )
                    db.session.add(phase_session)
                    db.session.commit()

                    logger.info(
                        f" [reading/start] Serving from full-test snapshot "
                        f"(parent={parent_id}, phase_session={phase_session.id})"
                    )
                    return jsonify({
                        'success': True,
                        'from_full_test': True,
                        'from_snapshot': True,
                        'session_id': phase_session.id,
                        'test_data': snap,
                        'current_question_index': 0,
                        'answers_so_far': {},
                    })

        # ─── 1a. EXPLICIT RESUME ──────────────────────────
        if resume_id and not retake:
            try:
                resume_id_int = int(resume_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid resume ID'}), 400

            session_obj = TestSession.query.filter_by(
                id=resume_id_int,
                user_id=user_id,
                test_type='reading',
                status='in_progress'
            ).first()

            if session_obj:
                test_data = session_obj.test_data
                if isinstance(test_data, str):
                    try:
                        test_data = json.loads(test_data)
                    except Exception:
                        test_data = {}

                logger.info(f" [reading/start] Explicit resume — session {session_obj.id}")
                return jsonify({
                    'success': True,
                    'resumed': True,
                    'explicit_resume': True,
                    'from_full_test': from_full_test,
                    'session_id': session_obj.id,
                    'test_data': test_data,
                    'answers_so_far': session_obj.answers_so_far or {},
                    'current_question_index': session_obj.current_question_index or 0
                })

        # ─── 1b. AUTO-RESUME ────────────────────────────────
        if (not from_full_test) and (not force_new) and (not resume_id) and (not retake):
            auto = TestSession.query.filter_by(
                user_id=user_id,
                test_type='reading',
                status='in_progress'
            ).order_by(TestSession.start_time.desc()).first()

            if auto:
                test_data = auto.test_data
                if isinstance(test_data, str):
                    try:
                        test_data = json.loads(test_data)
                    except Exception:
                        test_data = {}

                if _validate_reading_test_data(test_data):
                    logger.info(
                        f" [reading/start] Auto-resuming session {auto.id} "
                        f"for user {user_id}"
                    )
                    return jsonify({
                        'success': True,
                        'resumed': True,
                        'auto_resumed': True,
                        'from_full_test': from_full_test,
                        'session_id': auto.id,
                        'test_data': test_data,
                        'answers_so_far': auto.answers_so_far or {},
                        'current_question_index': auto.current_question_index or 0
                    })
                else:
                    logger.warning(f" Corrupted in-progress session {auto.id} — deleting")
                    try:
                        db.session.delete(auto)
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"Failed to delete corrupted session {auto.id}: {e}")

        # ─── 2. FREE LIMIT CHECK ──────────────────────────
        sub_manager = get_ielts_subscription_manager()
        allowed, error, requires_sub = sub_manager.can_access_test(user_id, 'reading')

        if not allowed:
            logger.info(
                f"User {user_id} blocked from reading: free limit reached "
                f"and no paid subscription tests"
            )
            return jsonify({
                'success': False,
                'error': error,
                'requires_subscription': requires_sub,
                'redirect_to': '/subscription?module=ielts'
            }), 402

        # ─── 2b. RETAKE ────────────────────────────────────
        if retake and not from_full_test:
            cloned = _clone_completed_reading_test(user_id, difficulty)
            if cloned:
                return jsonify({
                    'success': True,
                    'retake': True,
                    'session_id': cloned['session_id'],
                    'test_id': cloned['session_id'],
                    'test_data': cloned['test_data'],
                    'answers_so_far': {},
                    'current_question_index': 0,
                    'source': 'retake',
                    'message': cloned.get('message', 'Retaking your previous reading test.'),
                })
            logger.info(f"Retake requested but no usable prior test — generating fresh")

        # ═══════════════════════════════════════════════════════
        # 3. BANK-FIRST (improved with bank_id + fallback)
        # ═══════════════════════════════════════════════════════
        bank_mgr = current_app.config.get('TEST_BANK_MANAGER')
        test_data = None
        source = 'on_demand'
        bank_id = None

        if bank_mgr is not None and not force_new:
            try:
                bank_result = bank_mgr.get_or_create_test(
                    test_type='reading',
                    difficulty=difficulty,
                    topic=topic,
                    user_id=user_id,
                    module=module,
                )
            except Exception as e:
                logger.exception(f" [reading/start] bank manager exception: {e}")
                bank_result = {'success': False, 'error': str(e)}

            if bank_result.get('success'):
                raw = bank_result.get('test') or bank_result.get('test_data') or {}
                source = bank_result.get('source', 'bank')
                bank_id = bank_result.get('bank_id') or bank_result.get('id')

                normalized = _normalize_reading_bank_data(raw)
                if _validate_reading_test_data(normalized):
                    test_data = normalized
                    logger.info(
                        f" [reading/start] user={user_id} source={source} "
                        f"bank_id={bank_id} "
                        f"(remaining_unused={bank_result.get('remaining_unused')})"
                    )
                else:
                    logger.warning(
                        f" [reading/start] bank returned invalid test — "
                        f"falling back to on-demand"
                    )
                    test_data = None
                    source = 'on_demand'
                    bank_id = None
            else:
                logger.warning(
                    f" [reading/start] bank manager error: "
                    f"{bank_result.get('error')} — falling back to on-demand"
                )

        # ── On-demand fallback ────────────────────────────
        if test_data is None:
            generator = IELTSReadingGenerator(ai_engine=ai_engine)
            test_data = generator.generate_complete_test(difficulty, topic)
            if not test_data:
                return jsonify({'error': 'Failed to generate reading test'}), 500

            if hasattr(test_data, 'to_dict'):
                test_data = test_data.to_dict()

            source = 'on_demand'
            bank_id = None
            logger.info(f"🆕 [reading/start] Generated fresh (on-demand) for user {user_id}")

        # ─── 4. SAVE SESSION (with bank_id) ───────────────
        session_obj = TestSession(
            user_id=user_id,
            test_type='reading',
            difficulty=difficulty,
            test_data=test_data,
            bank_id=bank_id, # ← save bank_id
            start_time=datetime.now(timezone.utc),
            status='in_progress'
        )
        db.session.add(session_obj)
        db.session.commit()

        logger.info(f" [reading/start] session={session_obj.id} source={source} bank_id={bank_id}")

        return jsonify({
            'success': True,
            'from_full_test': from_full_test,
            'session_id': session_obj.id,
            'test_data': test_data,
            'source': source,
            'bank_id': bank_id,
            'message': f'Reading test started ({source}).'
        })

    # ────────────────────────────────────────────────────────────
    # SUBMIT TEST
    # ────────────────────────────────────────────────────────────
    @bp.route('/submit', methods=['POST'])
    @login_required
    def submit_test():
        data = request.get_json() or {}
        session_id = data.get('session_id')
        raw_answers = data.get('answers', {})
        is_full_test = bool(data.get('is_full_test'))
        user_id = current_user.id
        module = get_module()

        # ═══════════════════════════════════════════════════════════
        # AUTO-SAVE PATH — draft_only=True ले free_usage बढाउँदैन
        # Frontend ले every 30-60 sec मा draft save गर्छ।
        # यो path ले server मा answers save गर्छ तर credit charge गर्दैन।
        # ═══════════════════════════════════════════════════════════
        if data.get('draft_only') is True:
            try:
                TestSessionDS = get_test_session_model(module)
                sess_ds = TestSessionDS.query.filter_by(
                    id=session_id,
                    user_id=user_id,
                    test_type='reading'
                ).first()
                if sess_ds and sess_ds.status == 'in_progress':
                    sess_ds.answers_so_far = raw_answers or {}
                    sess_ds.last_updated = datetime.now(timezone.utc)
                    db.session.commit()
                    logger.info(
                        f" [reading/draft] Saved draft for session={session_id} "
                        f"({len(raw_answers or {})} answers) — no usage charged"
                    )
                return jsonify({
                    'success': True,
                    'draft': True,
                    'message': 'Draft saved — no usage charged'
                }), 200
            except Exception as e:
                logger.warning(f"[reading/draft] Draft save failed: {e}")
                return jsonify({'success': True, 'draft': True}), 200

        TestSession = get_test_session_model(module)
        TestResult = get_test_result_model(module)

        session_obj = TestSession.query.filter_by(
            id=session_id,
            user_id=user_id,
            test_type='reading'
        ).first()
        if not session_obj:
            return jsonify({'error': 'Session not found'}), 404

        # ═══════════════════════════════════════════════════════════
        # IDEMPOTENCY — Session पहिले नै completed भए फेरि credit नलिनु
        # User ले Submit दुई पटक click गरे वा page refresh गरे पनि
        # free_usage दोहोरो बढ्नबाट रोक्छ।
        # ═══════════════════════════════════════════════════════════
        if session_obj.status == 'completed':
            logger.info(
                f" [reading/submit] session={session_id} already completed — "
                f"returning cached result, no usage charged"
            )
            # Try to fetch the previous TestResult for this session
            prev_result = TestResult.query.filter_by(
                user_id=user_id,
                test_type='reading',
            ).order_by(TestResult.created_at.desc()).first()

            if prev_result:
                return jsonify({
                    'success': True,
                    'already_submitted': True,
                    'message': 'This test was already submitted.',
                    'score': prev_result.score or 0,
                    'band_score': prev_result.band_score or 0,
                    'correct_count': 0,
                    'incorrect_count': 0,
                    'unanswered_count': 0,
                    'total_questions': 0,
                    'detailed_results': [],
                }), 200

            return jsonify({
                'success': True,
                'already_submitted': True,
                'message': 'This test was already submitted.',
                'score': 0,
                'band_score': 0,
                'correct_count': 0,
                'incorrect_count': 0,
                'unanswered_count': 0,
                'total_questions': 0,
                'detailed_results': [],
            }), 200

        # ─── Normalize answers ───
        def _normalize_answers(raw):
            if raw is None:
                return {}
            if isinstance(raw, list):
                return {str(i + 1): v for i, v in enumerate(raw)}
            if not isinstance(raw, dict):
                return {}
            out = {}
            for k, v in raw.items():
                key = str(k).strip()
                lower = key.lower()
                for prefix in ('question_', 'question', 'q', '#'):
                    if lower.startswith(prefix):
                        stripped = key[len(prefix):].strip()
                        if stripped and stripped[0].isdigit():
                            key = stripped
                            break
                out[key] = v
            return out

        user_answers = _normalize_answers(raw_answers)

        logger.info(
            f" [reading/submit] session={session_id} "
            f"full_test={is_full_test} "
            f"answers_received={len(user_answers)}"
        )

        test_data = session_obj.test_data
        if isinstance(test_data, str):
            test_data = json.loads(test_data)

        # ─── Load subscription ───
        Subscription = get_subscription_model(module)
        sub = Subscription.query.filter_by(user_id=int(user_id)).first()
        if not sub:
            from models import create_default_subscription_for_user
            sub = create_default_subscription_for_user(int(user_id), module)

        sub_end = sub.subscription_end
        if sub_end and sub_end.tzinfo is None:
            sub_end = sub_end.replace(tzinfo=timezone.utc)

        has_active_subscription = bool(
            sub.status == 'active'
            and sub_end
            and sub_end > datetime.now(timezone.utc)
        )

        # ─── Build correct-answer map ───
        correct_answers = {}
        question_meta = {}
        lookup_map = {}
        ordered_keys = []

        passages = test_data.get('passages', []) or []
        if not passages and test_data.get('questions'):
            passages = [{'questions': test_data['questions']}]

        display_num = 0
        for p_idx, passage in enumerate(passages):
            for q_idx, question in enumerate(passage.get('questions', []) or []):
                display_num += 1

                correct_ans = (
                    question.get('correct_answer')
                    or question.get('answer')
                    or question.get('correct')
                    or ''
                )
                if correct_ans == '':
                    continue

                q_number = (
                    question.get('number')
                    or question.get('question_number')
                    or question.get('id')
                    or display_num
                )
                try:
                    q_number = int(q_number)
                except (ValueError, TypeError):
                    q_number = display_num

                raw_id = question.get('id')
                if raw_id and str(raw_id) not in correct_answers:
                    primary_key = str(raw_id)
                else:
                    primary_key = f"p{p_idx}_q{q_idx}"
                    if primary_key in correct_answers:
                        primary_key = f"{primary_key}_n{display_num}"

                correct_answers[primary_key] = correct_ans
                ordered_keys.append(primary_key)
                question_meta[primary_key] = {
                    'text': question.get('text') or question.get('question') or '',
                    'options': question.get('options') or [],
                    'display_num': q_number,
                }

                for candidate in {
                    str(q_number),
                    str(raw_id) if raw_id else None,
                    f"q{q_number}",
                    f"question{q_number}",
                    f"question_{q_number}",
                    f"#{q_number}",
                    str(q_idx + 1),
                    str(display_num),
                }:
                    if candidate and candidate not in lookup_map:
                        lookup_map[candidate] = primary_key

        total = len(correct_answers)
        if total == 0:
            logger.error(" [reading/submit] No correct answers found in test_data")
            return jsonify({'error': 'Test data has no correct answers'}), 400

        # ─── Grade ───
        correct = 0
        unanswered = 0
        detailed_results = []
        used_user_keys = set()

        for q_idx, (primary_key, correct_ans) in enumerate(correct_answers.items()):
            raw_user = None
            matched_key = None

            if primary_key in user_answers:
                raw_user = user_answers[primary_key]
                matched_key = primary_key
            else:
                for candidate, mapped_primary in lookup_map.items():
                    if mapped_primary == primary_key and candidate in user_answers:
                        raw_user = user_answers[candidate]
                        matched_key = candidate
                        break

            if raw_user is None:
                pos_key = str(q_idx + 1)
                if pos_key in user_answers and pos_key not in used_user_keys:
                    raw_user = user_answers[pos_key]
                    matched_key = pos_key

            if matched_key:
                used_user_keys.add(matched_key)

            user_ans = str(raw_user or '').strip()

            if not user_ans:
                unanswered += 1
                is_correct = False
                display_user = '(not answered)'
            else:
                is_correct = (
                    user_ans.lower() == str(correct_ans).lower().strip()
                )
                display_user = user_ans
                if is_correct:
                    correct += 1

            meta = question_meta.get(primary_key, {})
            detailed_results.append({
                'question_id': primary_key,
                'display_number': meta.get('display_num', q_idx + 1),
                'user_answer': display_user,
                'correct_answer': correct_ans,
                'is_correct': is_correct,
            })

        incorrect = total - correct - unanswered
        score_pct = (correct / total * 100) if total > 0 else 0

        logger.info(
            f" [reading/submit] session={session_id} total={total} "
            f"correct={correct} incorrect={incorrect} unanswered={unanswered}"
        )

        def calc_band(correct_count, total_count):
            if total_count == 0:
                return 0.0
            pct = (correct_count / total_count) * 100
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

        def round_band(raw):
            return min(9.0, max(0.0, round(raw * 2) / 2))

        band = round_band(calc_band(correct, total))

        # ═══════════════════════════════════════════════════════
        # Bank usage tracking — mark this bank test as taken by user
        # ═══════════════════════════════════════════════════════
        if getattr(session_obj, 'bank_id', None):
            try:
                TestBank = get_test_bank_usage_model(module)
                from models import get_test_bank_model
                BankModel = get_test_bank_model(module)
                if BankModel:
                    bank_row = BankModel.query.get(session_obj.bank_id)
                    if bank_row and hasattr(bank_row, 'mark_used_by_user'):
                        bank_row.mark_used_by_user(user_id)
                        db.session.commit()
                        logger.info(
                            f" [reading/submit] Bank #{session_obj.bank_id} "
                            f"marked used by user {user_id}"
                        )

                if TestBank:
                    usage_row = TestBank.query.filter_by(
                        user_id=user_id,
                        bank_id=session_obj.bank_id,
                    ).first()
                    if usage_row and hasattr(usage_row, 'mark_completed'):
                        usage_row.mark_completed(
                            score=score_pct,
                            band_score=band,
                        )
                        db.session.commit()
                        logger.info(
                            f" [reading/submit] UserTestBankUsage marked complete"
                        )
            except Exception as e:
                logger.warning(f" [reading/submit] bank usage tracking failed: {e}")

        # ═══════════════════════════════════════════════════════
        # FULL-TEST BRANCH
        # ═══════════════════════════════════════════════════════
        if is_full_test:
            ft_state = _ft_state()
            if not ft_state:
                logger.warning(
                    " [reading/submit] is_full_test=true but no "
                    "session['full_ielts_test'] — falling through to standalone."
                )
            else:
                ft_state['answers']['reading'] = user_answers
                ft_state['scores']['reading'] = {
                    'band_score': band,
                    'score_pct': round(score_pct, 1),
                    'correct': correct,
                    'total': total,
                }
                ft_state['completed_sections']['reading'] = True
                ft_state['phase'] = _ft_next_phase(ft_state) or 'done'
                _ft_save(ft_state)

                session_obj.status = 'completed'
                session_obj.answers_so_far = user_answers
                session_obj.current_question_index = total
                session_obj.last_updated = datetime.now(timezone.utc)
                db.session.commit()

                logger.info(
                    f" [reading/submit] Full-test phase recorded for user "
                    f"{user_id}, band={band}, next={ft_state['phase']}"
                )

                return jsonify({
                    'success': True,
                    'is_full_test': True,
                    'score': score_pct,
                    'band_score': band,
                    'correct_count': correct,
                    'incorrect_count': incorrect,
                    'unanswered_count': unanswered,
                    'total_questions': total,
                    'detailed_results': detailed_results,
                    'next_phase': ft_state['phase'],
                    'redirect': '/ielts-full-test?completed=reading',
                    'correct': correct,
                    'total': total,
                })

        # ═══════════════════════════════════════════════════════
        # STANDALONE PATH
        # ═══════════════════════════════════════════════════════
        result = TestResult(
            user_id=user_id,
            test_type='reading',
            score=score_pct,
            band_score=band
        )
        result.set_answers(user_answers)
        result.feedback = f"Correct: {correct}/{total} | Band: {band}"
        db.session.add(result)

        session_obj.status = 'completed'
        session_obj.answers_so_far = user_answers
        session_obj.current_question_index = total
        session_obj.last_updated = datetime.now(timezone.utc)

        if has_active_subscription and (sub.tests_remaining or 0) > 0:
            sub.tests_remaining = (sub.tests_remaining or 0) - 1
            sub.tests_taken = (sub.tests_taken or 0) + 1
            db.session.commit()
            logger.info(
                f" Subscription test used for user {user_id} (reading), "
                f"{sub.tests_remaining} left"
            )
        else:
            db.session.commit()
            try:
                sub_manager = get_ielts_subscription_manager()
                sub_manager.increment_free_usage(user_id, 'reading')
                logger.info(
                    f" Free usage incremented for user {user_id} (reading) "
                    f"after submission"
                )
            except Exception as e:
                logger.error(
                    f" Failed to increment reading free usage for user {user_id}: {e}"
                )

        return jsonify({
            'success': True,
            'score': score_pct,
            'band_score': band,
            'correct_count': correct,
            'incorrect_count': incorrect,
            'unanswered_count': unanswered,
            'total_questions': total,
            'detailed_results': detailed_results,
            'result_id': result.id,
            'correct': correct,
            'total': total,
        })

    # ────────────────────────────────────────────────────────────
    # STATUS
    # ────────────────────────────────────────────────────────────
    @bp.route('/status/<int:session_id>', methods=['GET'])
    @login_required
    def status(session_id):
        module = get_module()
        TestSession = get_test_session_model(module)
        session_obj = TestSession.query.get(session_id)
        if not session_obj or session_obj.user_id != current_user.id:
            return jsonify({'error': 'Session not found'}), 404
        return jsonify({
            'status': session_obj.status,
            'current_question_index': session_obj.current_question_index,
            'answers_so_far': session_obj.answers_so_far or {},
            'created_at': session_obj.start_time.isoformat(),
            'last_updated': session_obj.last_updated.isoformat() if session_obj.last_updated else None
        })

    return bp