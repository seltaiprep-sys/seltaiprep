"""Business logic for IELTS Writing module - PURE AI, NO FALLBACKS

FIXES APPLIED (v3):
  (1) Accuracy telemetry — evaluate_essay() now logs whether the
        score came from the AI path or the rule-based fallback, so
        accuracy regressions can be diagnosed from logs.
  (2) Trimmed save_essay() feedback payload. The full evaluation
        dict was being stored in a JSON column, which bloats the DB.
        Now only the key fields needed for history + UI are persisted.
  (3) Removed three unused imports — cosmetic cleanup, no behavior
        change. Also removed a dead `evaluator_type` local variable in
        evaluate_essay().

FIXES APPLIED (v2):
  (4) CRITICAL — Removed the "under-length → Band 0" shortcut in
        evaluate_essay(). Real IELTS does NOT give Band 0 for a 149-word
        Task 1; it applies a modest Task Response deduction.
      Only truly empty / noise responses (< ABSOLUTE_MIN_WORDS) still
      short-circuit to Band 0.

  (5) Removed the "current_band <= 1.0" and "current_band < 5.0" blocks
        in upgrade_essay(). The EssayUpgrader already handles short essays
        by returning a clearly-flagged model essay (is_model_essay=True).

  (6) save_essay() now:
        · accepts test_id as Optional[int] (was str)
        · forwards `feedback` and `criteria` as native dicts
        · drops the manual `created_at` so the DB default (UTC) applies
        · normalizes None test_id to NULL before the FK insert

  (7) All timestamps use datetime.now(timezone.utc).

  (8) evaluate_essay() returns the evaluator's band verbatim.
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

from .test_generator import WritingTestGenerator
from .evaluator import EssayEvaluator
from .essay_upgrader import EssayUpgrader
from .scoring import WritingScoring

logger = logging.getLogger(__name__)

# IELTS minimum word counts
MIN_TASK1_WORDS = 150
MIN_TASK2_WORDS = 250

# Absolute floor below which the response is treated as "no meaningful content"
ABSOLUTE_MIN_WORDS = 20


class WritingService:
    """Service layer for IELTS Writing tests - PURE AI mode"""

    def __init__(self, ai_engine=None, repository=None):
        if not ai_engine:
            raise ValueError(" AI Engine is required. No fallback templates available.")

        self.ai_engine = ai_engine
        self.generator = WritingTestGenerator(ai_engine)
        self.evaluator = EssayEvaluator(ai_engine)
        self.upgrader = EssayUpgrader(ai_engine)
        self.scoring = WritingScoring()
        self.repo = repository
        self._repo_initialized = False

        # Session tracking for penalty history
        self.sessions = {}

        logger.info("WritingService initialized with pure AI mode")

    def _ensure_repository(self):
        """Lazy initialize repository if needed"""
        if not self._repo_initialized and self.repo is None:
            try:
                from .repository import create_writing_repository
                self.repo = create_writing_repository()
                self._repo_initialized = True
            except Exception as e:
                logger.warning(f"Repository initialization failed: {e}")
                self.repo = None
                self._repo_initialized = True

    # ==================== START TEST ====================
    def start_test(self, difficulty: str = "medium", topic: str = None,
                   exam_type: str = "ielts", **kwargs) -> Dict:
        """
        Start a new writing test - PURE AI generation

        Args:
            difficulty (str): 'easy', 'medium', 'hard'
            topic (str): optional topic
            exam_type (str): 'ielts' or other
            **kwargs: additional parameters (force_new, auto_generate,
                      preserve_user_essay) – logged but ignored at this layer.
                      Cache / TestBank reuse is handled in api.py.
        """
        if not self.ai_engine:
            raise RuntimeError(" Cannot start test: AI Engine not available")

        if kwargs:
            logger.debug(f"start_test received extra kwargs: {kwargs}")

        test_data = self.generator.generate_complete(difficulty, topic, exam_type)

        session_id = test_data.get('serial', hash(str(datetime.now(timezone.utc))))
        self.sessions[session_id] = {
            'start_time': datetime.now(timezone.utc),
            'difficulty': difficulty,
            'exam_type': exam_type,
            'test_data': test_data,
            'attempts': {'task1': 0, 'task2': 0},
            'last_penalties': None
        }

        test_data['session_id'] = session_id
        logger.info(f"Test started: session_id={session_id}, difficulty={difficulty}")

        return test_data

    def get_task1(self, difficulty: str = "medium", topic: str = None) -> Dict:
        """Get Task 1 only - PURE AI generation"""
        if not self.ai_engine:
            raise RuntimeError(" Cannot generate Task 1: AI Engine not available")
        return self.generator.generate_task1_with_chart(difficulty, topic)

    def get_task2(self, difficulty: str = "medium", topic: str = None) -> Dict:
        """Get Task 2 only - PURE AI generation"""
        if not self.ai_engine:
            raise RuntimeError(" Cannot generate Task 2: AI Engine not available")
        return self.generator.generate_task2(difficulty, topic)

    # ==================== EVALUATE ESSAY ====================
    def evaluate_essay(self, essay: str, task_type: str, prompt: str = "",
                       chart_data: dict = None, expected_features: list = None,
                       session_id: str = None) -> Dict:
        """
        Evaluate essay with penalty tracking and session history.

        FIX (v2): We no longer return Band 0 for under-length responses.
        The evaluator already applies a graduated word-count cap, so a
        149-word Task 1 gets a modest deduction (e.g. Band 5.5), not Band 0.
        Only truly empty / noise responses (< ABSOLUTE_MIN_WORDS) short-circuit.
        """
        # Determine minimum words for this task type (still used for logging)
        min_words = MIN_TASK1_WORDS if task_type == 'task1' else MIN_TASK2_WORDS
        word_count = len(essay.split()) if essay else 0

        # ── Truly empty / noise response → Band 0 ─────────────────────
        # Anything with fewer than ABSOLUTE_MIN_WORDS words is treated as
        # "no meaningful content" and short-circuits without calling the AI.
        if not essay or word_count < ABSOLUTE_MIN_WORDS:
            logger.info(
                f"[Evaluation] Empty/noise {task_type} essay "
                f"({word_count} words) -> Band 0"
            )
            return {
                'error': 'No meaningful content',
                'overall_band': 0.0,
                'user_overall_band': 0.0,
                'feedback': (
                    'BAND 0 - No meaningful response provided. '
                    'Please write a proper essay (at least '
                    f'{min_words} words for {task_type.upper()}).'
                ),
                'word_count': word_count,
                'session_id': session_id,
                'evaluator': 'no_meaningful_content',
                'criteria': {},
                'weaknesses': ['insufficient_content'],
                'strengths': [],
                'penalty_applied': 0,
                'success': False,
            }

        # ── Under-length but non-empty → let the evaluator handle it ──
        # The evaluator's AdvancedLengthAnalyzer + word_count_cap will apply
        # a graduated penalty (e.g. -0.25 for 100–150 words, -0.5 below 75%).
        if word_count < min_words:
            logger.info(
                f"[Evaluation] Under-length {task_type} essay "
                f"({word_count}/{min_words} words) — delegating to evaluator "
                f"for graduated penalty"
            )

        session = self.sessions.get(session_id, {})

        # ── Delegate to evaluator ─────────────────────────────────────
        result = self.evaluator.evaluate(
            essay=essay,
            task_type=task_type,
            prompt=prompt,
            chart_data=chart_data,
            expected_features=expected_features,
        )

        if 'overall_band' not in result or result.get('overall_band') is None:
            result['overall_band'] = 0.0

        band = result.get('overall_band', 0)

        # ── Only sanity-check truly broken cases ──────────────────────
        # The evaluator's own early-exit paths already produce the correct
        # band + feedback. We only ensure the feedback string is not empty.
        if band == 0.0 and not result.get('feedback'):
            logger.info(f"[Evaluation] Band 0.0 with empty feedback for {task_type}")
            result['feedback'] = (
                'BAND 0 - No meaningful response provided. '
                'Please write a proper essay addressing the question.'
            )
        elif not result.get('feedback'):
            result['feedback'] = (
                f'Your essay is {word_count} words. '
                f'Focus on developing your ideas with specific examples and '
                f'accurate grammar to improve your band score.'
            )

        result['session_id'] = session_id
        # Mirror overall_band to user_overall_band for frontend convenience
        result.setdefault('user_overall_band', band)

        # ── Track attempts ────────────────────────────────────────────
        if session_id in self.sessions:
            self.sessions[session_id]['attempts'][task_type] = \
                self.sessions[session_id]['attempts'].get(task_type, 0) + 1

            self.sessions[session_id]['last_penalties'] = {
                'total': result.get('penalty_applied', 0),
                'weaknesses': result.get('weaknesses', []),
            }

            result['attempt_number'] = self.sessions[session_id]['attempts'][task_type]

            if result['attempt_number'] > 1:
                result['improvement_suggestions'] = self._get_improvement_suggestions(
                    self.sessions[session_id].get('last_penalties'),
                    result,
                )

        # ── Time-limit check ──────────────────────────────────────────
        if session_id in self.sessions:
            time_check = self._check_time_limit(session, task_type)
            if time_check['penalty'] > 0:
                result['time_penalty'] = time_check['penalty']
                # Only apply time penalty to non-trivial bands
                if band > 1.0:
                    result['overall_band'] = max(0.0, band - time_check['penalty'])
                    result['user_overall_band'] = result['overall_band']
                result['feedback'] = f"{time_check['message']}\n{result.get('feedback', '')}"

        # v3: Accuracy telemetry — log which path was used
        try:
            evaluator_used = result.get('evaluator', 'unknown')
            is_ai = 'ai' in str(evaluator_used).lower()
            if is_ai:
                logger.info(
                    f"[Service] AI evaluation: band={result.get('overall_band')}"
                )
            else:
                logger.warning(
                    f"[Service] Non-AI evaluation (source={evaluator_used}): "
                    f"band={result.get('overall_band')} — accuracy may be lower"
                )
        except Exception:
            pass

        logger.info(
            f"Evaluation complete: task_type={task_type}, "
            f"band={result.get('overall_band')}, "
            f"penalty={result.get('penalty_applied', 0)}"
        )

        return result

    def _check_time_limit(self, session: Dict, task_type: str) -> Dict:
        """Check if essay submitted within time limit"""
        if not session or 'start_time' not in session:
            return {'penalty': 0, 'message': ''}

        start_time = session['start_time']
        # Ensure start_time is tz-aware for consistent subtraction
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        time_limit_minutes = 20 if task_type == 'task1' else 40
        elapsed_minutes = (now - start_time).total_seconds() / 60

        if elapsed_minutes > time_limit_minutes:
            minutes_over = elapsed_minutes - time_limit_minutes
            penalty = min(minutes_over * 0.25, 1.5)
            return {
                'penalty': penalty,
                'message': (
                    f" Time penalty: You were {minutes_over:.0f} minutes over "
                    f"the {time_limit_minutes}-minute limit. "
                    f"Penalty: -{penalty:.1f} bands."
                ),
            }

        return {'penalty': 0, 'message': ''}

    def _get_improvement_suggestions(self, last_penalties: Dict,
                                      current_result: Dict) -> List[str]:
        """Generate improvement suggestions based on penalty history"""
        suggestions = []

        if not last_penalties:
            return suggestions

        last_penalty = last_penalties.get('total', 0)
        current_penalty = current_result.get('penalty_applied', 0)

        if current_penalty >= last_penalty:
            suggestions.append(
                "Your score hasn't improved. Review the specific weaknesses below."
            )

        weaknesses = current_result.get('weaknesses', [])
        band = current_result.get('overall_band', 0)

        for weakness in weaknesses[:3]:
            wl = weakness.lower()
            if band == 0.0:
                suggestions.append(
                    "Your essay does not address the question. Read the prompt "
                    "carefully and write a relevant response."
                )
            elif 'meaningful' in wl or 'insufficient' in wl:
                suggestions.append(
                    "Write a proper essay with meaningful content (at least "
                    "150 words for Task 1, 250 words for Task 2)."
                )
            elif 'off-topic' in wl or 'off_topic' in wl:
                suggestions.append(
                    "Read the prompt carefully and make sure your essay directly "
                    "answers the question."
                )
            elif 'chart' in wl or 'data' in wl:
                suggestions.append(
                    "Make sure to mention exact numbers and labels from the "
                    "chart/graph."
                )
            elif 'cohesion' in wl or 'linking' in wl:
                suggestions.append(
                    "Use more linking words: however, therefore, furthermore, "
                    "moreover, consequently."
                )
            elif 'vocabulary' in wl:
                suggestions.append(
                    "Use academic vocabulary: significant, considerable, "
                    "demonstrates, illustrates."
                )
            elif 'tense' in wl:
                suggestions.append(
                    "Maintain consistent tense throughout your essay."
                )
            elif 'short' in wl or 'length' in wl or 'under' in wl:
                suggestions.append(
                    "Write more — aim for at least 150 words (Task 1) or "
                    "250 words (Task 2)."
                )

        return suggestions[:3]

    # ==================== UPGRADE METHODS ====================

    def upgrade_essay(self, essay: str, current_band: float, task_type: str,
                      prompt: str = "", chart_data: dict = None,
                      auto_generate: bool = False,
                      preserve_user_essay: bool = True) -> Dict:
        """
        Upgrade an essay using the full EssayUpgrader.

        FIX (v2): We no longer block short essays here. The EssayUpgrader
        has its own short-response guard that returns a clearly-flagged
        model essay (is_model_essay=True) with the user's band preserved
        separately.

        Args:
            essay: User's original essay
            current_band: Current estimated band
            task_type: 'task1' or 'task2'
            prompt: Task question (optional if auto_generate=True)
            chart_data: Chart data for Task 1 (optional if auto_generate=True)
            auto_generate: If True, AI generates question + chart_data automatically
            preserve_user_essay: If True, always upgrade user's essay;
                                 if False, regenerate on mismatch / low band
        """
        if not self.ai_engine:
            raise RuntimeError(" Cannot upgrade essay: AI Engine not available")

        # ── Only reject truly empty essays ───────────────────────────
        word_count = len((essay or "").strip().split())
        if not essay or word_count < 5:
            return {
                'error': 'Cannot upgrade - essay is empty',
                'upgraded_essay': essay,
                'original_band': current_band,
                'user_band': current_band,
                'suggestion': 'Write at least a few words before requesting an upgrade.',
                'success': False,
                'is_model_essay': False,
            }

        # ── Delegate to EssayUpgrader (handles short essays internally) ──
        if task_type == 'task1':
            result = self.upgrader.upgrade_task1(
                essay=essay,
                prompt=prompt,
                chart_data=chart_data,
                current_scores={'overall_band': current_band},
                target_band=self._get_upgrade_target(current_band),
                auto_generate=auto_generate,
                preserve_user_essay=preserve_user_essay,
            )
        else: # task2
            result = self.upgrader.upgrade_task2(
                essay=essay,
                prompt=prompt,
                current_scores={'overall_band': current_band},
                target_band=self._get_upgrade_target(current_band),
                preserve_user_essay=preserve_user_essay,
            )

        # Ensure consistent contract on the returned dict
        if not isinstance(result, dict):
            return {
                'error': 'Upgrade returned unexpected type',
                'upgraded_essay': essay,
                'original_band': current_band,
                'user_band': current_band,
                'success': False,
                'is_model_essay': False,
            }

        result.setdefault('success', True)
        result.setdefault('is_model_essay', False)
        result.setdefault('user_band', current_band)
        return result

    def _get_upgrade_target(self, current_band: float) -> float:
        """Map current band to desired upgrade target."""
        try:
            current = round(float(current_band or 0) * 2) / 2 # nearest 0.5
        except (TypeError, ValueError):
            current = 5.5

        if current < 5.0:
            return 5.5
        if current == 5.0:
            return 6.0
        if current == 5.5:
            return 6.5
        if current in (6.0, 6.5):
            return 7.0
        return min(current + 0.5, 9.0)

    # ==================== ASYNC UPGRADE ====================

    def upgrade_essay_async(self, essay: str, current_band: float, task_type: str,
                            prompt: str = "", chart_data: dict = None,
                            auto_generate: bool = False,
                            preserve_user_essay: bool = True) -> Dict:
        """Async upgrade — currently delegates to the sync path."""
        if not self.ai_engine:
            raise RuntimeError(" Cannot upgrade essay: AI Engine not available")

        return self.upgrade_essay(
            essay=essay,
            current_band=current_band,
            task_type=task_type,
            prompt=prompt,
            chart_data=chart_data,
            auto_generate=auto_generate,
            preserve_user_essay=preserve_user_essay,
        )

    # ==================== BAND CALCULATION ====================

    def calculate_overall(self, task1_band: float, task2_band: float) -> float:
        """
        Calculate overall band using the official IELTS Writing formula:
            Overall = (Task1 + 2 * Task2) / 3
        Rounded to the nearest 0.5.

        Examples:
            Task1=0, Task2=0 -> 0.0
            Task1=0, Task2=6 -> 4.0
            Task1=6, Task2=6 -> 6.0
            Task1=5, Task2=7 -> 6.5
        """
        try:
            t1 = float(task1_band or 0)
            t2 = float(task2_band or 0)
        except (TypeError, ValueError):
            t1 = t2 = 0.0

        raw = (t1 + 2 * t2) / 3
        return round(raw * 2) / 2

    # ==================== REPOSITORY ====================

    def save_essay(self, user_id: str, test_id, task_type: str,
                   essay: str, prompt: str, result: Dict) -> Optional[int]:
        """
        Save essay to repository.

        FIX (v2):
          · test_id is Optional[int] — we pass through whatever the API
            layer normalized (int or None). The repository handles NULL.
          · feedback / criteria are forwarded as native dicts, not
            json.dumps() strings — the repository's _normalize_json
            handles both.
          · We do NOT pass created_at — the DB default (UTC) applies.

        FIX (v3):
          · The `feedback` JSON column no longer stores the full
            evaluation dict (which included advanced_analysis,
            memorization_check, etc.). Only the key fields needed for
            history + UI are persisted — prevents DB bloat.
        """
        self._ensure_repository()
        if not self.repo:
            logger.warning("Repository not available - essay not saved")
            return None

        # Normalize the FK: allow int, digit-string, or None
        if test_id is None or test_id == '':
            fk_test_id = None
        elif isinstance(test_id, int):
            fk_test_id = test_id
        else:
            try:
                fk_test_id = int(str(test_id).strip())
            except (ValueError, TypeError):
                logger.warning(f"save_essay received non-integer test_id={test_id!r}; storing NULL")
                fk_test_id = None

        # v3: Trim feedback payload to essential fields only
        trimmed_feedback = {
            'overall_band': result.get('overall_band'),
            'user_overall_band': result.get('user_overall_band'),
            'evaluator': result.get('evaluator', ''),
            'detailed_feedback': (result.get('detailed_feedback') or [])[:10],
            'weaknesses': (result.get('weaknesses') or [])[:5],
            'strengths': (result.get('strengths') or [])[:5],
            'penalty_applied': result.get('penalty_applied', 0),
            'descriptor': result.get('descriptor', ''),
        }

        try:
            return self.repo.save_essay({
                'user_id': user_id,
                'test_id': fk_test_id,
                'task_type': task_type,
                'essay': essay,
                'prompt': prompt,
                'band_score': result.get('overall_band', 0) or 0,
                'feedback': trimmed_feedback,
                'criteria': result.get('criteria', {}) or {},
                'word_count': result.get('word_count', 0) or 0,
                'penalty': result.get('penalty_applied', 0) or 0,
            })
        except Exception as e:
            logger.error(f"Save failed: {e}")
            return None

    def get_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """Get user's essay history"""
        self._ensure_repository()
        if not self.repo:
            return []

        try:
            return self.repo.get_user_essays(user_id, limit)
        except Exception as e:
            logger.error(f"History fetch failed: {e}")
            return []

    def get_essay(self, essay_id: int) -> Optional[Dict]:
        """Get single essay by ID"""
        self._ensure_repository()
        if not self.repo:
            return None

        try:
            return self.repo.get_essay(essay_id)
        except Exception as e:
            logger.error(f"Essay fetch failed: {e}")
            return None

    def get_progress(self, user_id: str) -> Dict:
        """Get user's progress statistics"""
        self._ensure_repository()
        if not self.repo:
            return {
                'total_essays': 0,
                'average_band': 0,
                'task1_count': 0,
                'task2_count': 0,
                'average_penalty': 0,
            }

        try:
            return self.repo.get_user_stats(user_id)
        except Exception as e:
            logger.error(f"Progress fetch failed: {e}")
            return {
                'total_essays': 0,
                'average_band': 0,
                'task1_count': 0,
                'task2_count': 0,
                'average_penalty': 0,
            }

    # ==================== SESSION ====================

    def get_session_info(self, session_id: str) -> Dict:
        """Get current session information"""
        session = self.sessions.get(session_id, {})
        if not session:
            return {'error': 'Session not found', 'session_id': session_id}

        start_time = session.get('start_time')
        if isinstance(start_time, datetime):
            start_time_str = start_time.isoformat()
        else:
            start_time_str = str(start_time) if start_time else ''

        return {
            'session_id': session_id,
            'start_time': start_time_str,
            'difficulty': session.get('difficulty'),
            'exam_type': session.get('exam_type'),
            'attempts': session.get('attempts', {'task1': 0, 'task2': 0}),
            'last_penalties': session.get('last_penalties'),
        }

    def clear_session(self, session_id: str) -> bool:
        """Clear a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Session cleared: {session_id}")
            return True
        return False


def create_writing_service(ai_engine, repository=None):
    """Factory function to create WritingService with AI engine"""
    if not ai_engine:
        raise ValueError(" AI Engine required to create WritingService")

    return WritingService(ai_engine, repository)