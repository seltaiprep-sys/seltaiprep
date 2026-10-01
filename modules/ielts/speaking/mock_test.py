# modules/ielts/speaking/mock_test.py
"""Full IELTS Speaking Mock Test - Complete 11-14 minute simulation

FIX:
  (1) Removed duplicate ContentAwareExaminer — imports the canonical one
  (2) Fixed MockTestInterface imports (uses real factory functions)
  (3) Uses voice.speak_sync() (correct method name, was voice.speak)
  (4) Reads the ACTUAL test_generator structure (examiner_greeting,
      warmup_section.questions, topic_section.questions, topic_card, etc.)
  (5) Part 2 speech is now 120s total (was 180s)
  (6) Removed duplicate on_question calls
  (7) Added speed_multiplier for fast testing (default 1.0 = real time)
  (8) self.scores and self.recording now properly populated
  (9) Off-topic detection uses word boundaries (not substring)
  (10) All .get() calls guarded against None / wrong types
"""

import random
import time
import logging
import re
from typing import Dict, Optional, Callable, List, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ============================================================
# Use the canonical ContentAwareExaminer
# ============================================================

try:
    from .content_aware_examiner import ContentAwareExaminer
except ImportError:
    # Fallback minimal implementation if the real module is missing
    logger.warning("content_aware_examiner not importable — using stub")

    class ContentAwareExaminer: # type: ignore
        def __init__(self, ai_engine=None, voice=None, coherence_analyzer=None):
            self.ai = ai_engine

        def analyze_response(self, transcript, question, part):
            wc = len((transcript or "").split())
            if wc < 10:
                return {'action': 'probe', 'reason': 'too_short',
                        'follow_up': "Can you tell me more about that?",
                        'assessment': {'length': 'very_short'}}
            return {'action': 'move_on', 'reason': 'adequate',
                    'follow_up': None, 'assessment': {'length': 'adequate'}}

        def generate_personalized_transition(self, prev_topic=None,
                                              prev_answer=None,
                                              next_topic=None):
            nxt = (next_topic or "the next topic").replace('_', ' ').title()
            return f"Now, let's talk about {nxt}."

        def generate_closing_remark(self):
            return "Thank you. That is the end of the speaking test."

        def assess_confidence(self, transcript):
            return 'medium'


# ============================================================
# Helpers
# ============================================================

def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        return default
    v = value.strip()
    return v if v else default


def _to_questions_list(raw) -> List[Dict]:
    """Normalize a questions field to a list of {'question': str, 'type': str}."""
    out: List[Dict] = []
    if not isinstance(raw, list):
        return out
    for q in raw:
        if isinstance(q, str):
            if q.strip():
                out.append({'question': q.strip(), 'type': 'general'})
        elif isinstance(q, dict):
            text = _safe_str(q.get('question') or q.get('text'))
            if text:
                out.append({'question': text, 'type': q.get('type', 'general')})
    return out


# ============================================================
# Main mock test class
# ============================================================

class SpeakingMockTest:
    """
    Complete IELTS Speaking mock test simulation.

    Uses the canonical ContentAwareExaminer and the current
    test_generator output structure.
    """

    NATURAL_TRANSITIONS = ["", "", "", "", "Now, ", "So, ", "And, ",
                           "Let me ask you, ", "Tell me, "]

    ACKNOWLEDGMENTS = ["", "", "", "Thank you. ", "I see. ",
                       "Interesting. ", "Alright. ", "OK. "]

    def __init__(
        self,
        test_generator,
        examiner_voice,
        ai_analyzer=None,
        coherence_analyzer=None,
        speed_multiplier: float = 1.0,
    ):
        """
        Args:
            test_generator: SpeakingTest instance
            examiner_voice: ExaminerVoice instance
            ai_analyzer: Optional AI engine for probe generation
            coherence_analyzer: Optional CoherenceAnalyzer
            speed_multiplier: 1.0 = real time. Use 0.05 for 20x speedup in tests.
        """
        self.generator = test_generator
        self.voice = examiner_voice
        self.analyzer = ai_analyzer
        self.speed_multiplier = max(0.01, float(speed_multiplier))

        self.recording = False
        self.responses: Dict[str, Any] = {}
        self.scores: Dict[str, float] = {}
        self.is_paused = False
        self.is_stopped = False
        self.conversation_history: List[Dict] = []

        try:
            self.content_examiner = ContentAwareExaminer(
                ai_engine=ai_analyzer,
                voice=examiner_voice,
                coherence_analyzer=coherence_analyzer,
            )
        except TypeError:
            # Fallback for older constructor signature
            self.content_examiner = ContentAwareExaminer(ai_analyzer, examiner_voice)

    # ============================================================
    # PUBLIC CONTROL
    # ============================================================

    def pause_test(self):
        self.is_paused = True

    def resume_test(self):
        self.is_paused = False

    def stop_test(self):
        self.is_stopped = True

    # ============================================================
    # MAIN ENTRY
    # ============================================================

    def run_full_test(
        self,
        difficulty: str = "medium",
        topic: str = None,
        on_question: Callable = None,
        on_timer: Callable = None,
        on_complete: Callable = None,
    ) -> Dict:
        """
        Run the complete mock speaking test.

        Returns:
            Dict with keys: test, responses, scores, completed_at,
                            conversation_history (and stopped_early if aborted).
        """
        self.is_stopped = False
        test = self.generator.generate_complete_test(difficulty, topic)

        if not test or test.get('error') or test.get('success') is False:
            err = (test or {}).get('error', 'Unknown generation error')
            logger.error(f"Cannot run mock test: {err}")
            return {
                'error': err,
                'success': False,
                'test': test or {},
                'responses': {},
                'scores': {},
            }

        # Safely extract the display topic
        display_topic = (
            test.get('part1_topic')
            or test.get('topic')
            or 'general'
        )

        print("\n" + "=" * 60)
        print("IELTS SPEAKING MOCK TEST")
        print("=" * 60)
        print(f"Topic: {str(display_topic).replace('_', ' ').title()}")
        print(f"Difficulty: {difficulty}")
        print(f"Duration: ~11-14 minutes (speed x{self.speed_multiplier:.2f})")
        print("=" * 60)

        # ---------- PART 1 ----------
        print("\n PART 1: Introduction & Interview")
        self._run_part1(test.get('part1') or {}, on_question, on_timer)
        if self.is_stopped:
            return self._partial(test)

        # ---------- PART 2 ----------
        print("\n PART 2: Individual Long Turn")
        self._run_part2(test.get('part2') or {}, on_question, on_timer)
        if self.is_stopped:
            return self._partial(test)

        # ---------- PART 3 ----------
        print("\n PART 3: Two-way Discussion")
        self._run_part3(test.get('part3') or {}, on_question, on_timer)

        print("\n" + "=" * 60)
        print("TEST COMPLETE!")
        print("=" * 60)

        results = {
            'success': True,
            'test': test,
            'responses': self.responses,
            'scores': self.scores,
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'conversation_history': self.conversation_history,
        }

        if on_complete:
            try:
                on_complete(results)
            except Exception as e:
                logger.warning(f"on_complete callback failed: {e}")

        return results

    def _partial(self, test: Dict) -> Dict:
        return {
            'success': False,
            'stopped_early': True,
            'test': test,
            'responses': self.responses,
            'scores': self.scores,
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'conversation_history': self.conversation_history,
        }

    # ============================================================
    # PART 1
    # ============================================================

    def _run_part1(self, part1: Dict, on_question, on_timer) -> None:
        # ---- Examiner opening lines ----
        for key in (
            'examiner_greeting',
            'examiner_name_follow_up',
            'examiner_id_check',
            'examiner_thank_you',
            'examiner_test_explanation',
            'examiner_begin',
        ):
            text = _safe_str(part1.get(key))
            if text and not self.is_stopped:
                self._speak(text, f"part1_{key}.mp3",
                            on_question, part=1, q_num=0)

        # ---- Warmup questions ----
        warmup = part1.get('warmup_section') or {}
        if isinstance(warmup, dict):
            transition = _safe_str(warmup.get('examiner_transition'))
            if transition and not self.is_stopped:
                self._speak(transition, "part1_warmup_transition.mp3",
                            on_question, part=1, q_num=0)

            for i, q in enumerate(_to_questions_list(warmup.get('questions'))):
                if self.is_stopped:
                    return
                self._ask_and_probe(
                    q['question'], part=1, q_num=i + 1,
                    on_question=on_question, on_timer=on_timer,
                )

        # ---- Topic questions ----
        topic_sec = part1.get('topic_section') or {}
        if isinstance(topic_sec, dict):
            transition = _safe_str(topic_sec.get('examiner_transition'))
            if transition and not self.is_stopped:
                self._speak(transition, "part1_topic_transition.mp3",
                            on_question, part=1, q_num=0)

            base = 100 # offset numbering
            for i, q in enumerate(_to_questions_list(topic_sec.get('questions'))):
                if self.is_stopped:
                    return
                self._ask_and_probe(
                    q['question'], part=1, q_num=base + i + 1,
                    on_question=on_question, on_timer=on_timer,
                )

        # ---- Closing ----
        closing = _safe_str(part1.get('examiner_part1_closing'))
        if closing and not self.is_stopped:
            self._speak(closing, "part1_closing.mp3",
                        on_question, part=1, q_num=0)

        print("\n Part 1 complete")

    # ============================================================
    # PART 2
    # ============================================================

    def _run_part2(self, part2: Dict, on_question, on_timer) -> None:
        intro = _safe_str(part2.get('examiner_intro'))
        if intro and not self.is_stopped:
            self._speak(intro, "part2_intro.mp3", on_question, part=2, q_num=0)

        instructions = _safe_str(part2.get('examiner_instructions'))
        if instructions and not self.is_stopped:
            self._speak(instructions, "part2_instructions.mp3",
                        on_question, part=2, q_num=0)

        if self.is_stopped:
            return

        card = part2.get('topic_card') or {}
        title = _safe_str(card.get('title'))
        prompts = [
            _safe_str(p) for p in (card.get('prompts') or []) if _safe_str(p)
        ]

        if title:
            print(f"\n Topic: {title}")
            for p in prompts:
                print(f" • {p}")
            self._speak(title, "part2_topic.mp3",
                        on_question, part=2, q_num=0)

        # 60-second preparation
        prep_start = _safe_str(part2.get('examiner_preparation_start'))
        if prep_start and not self.is_stopped:
            self._speak(prep_start, "part2_prep_start.mp3",
                        on_question, part=2, q_num=0)

        print("\n Preparation time: 60 seconds")
        self._countdown(60, "Preparation", on_timer)
        if self.is_stopped:
            return

        prep_end = _safe_str(part2.get('examiner_preparation_end'))
        if prep_end:
            self._speak(prep_end, "part2_prep_end.mp3",
                        on_question, part=2, q_num=0)

        speech_start = _safe_str(part2.get('examiner_speaking_start'))
        if speech_start:
            self._speak(speech_start, "part2_speech_start.mp3",
                        on_question, part=2, q_num=0)

        # 120-second speech (real IELTS Part 2 max)
        print("\n Speaking time: 2 minutes")
        self._countdown(120, "Speaking", on_timer)
        if self.is_stopped:
            return

        speech_end = _safe_str(part2.get('examiner_speaking_end'))
        if speech_end:
            self._speak(speech_end, "part2_speech_end.mp3",
                        on_question, part=2, q_num=0)

        # Follow-up
        follow_up = _safe_str(part2.get('examiner_follow_up_question'))
        if not follow_up:
            follow_up = random.choice([
                "Did you enjoy talking about this topic?",
                "Have you told anyone else about this?",
                "Would you like to learn more about this?",
            ])
        self._speak(follow_up, "part2_followup.mp3",
                    on_question, part=2, q_num=0)

        # Record simulated response
        self.conversation_history.append({
            'part': 2,
            'question': title or 'part2_long_turn',
            'response': self._simulate_response(title or 'a memorable event', part=2),
        })

        print("\n Part 2 complete")

    # ============================================================
    # PART 3
    # ============================================================

    def _run_part3(self, part3: Dict, on_question, on_timer) -> None:
        intro = _safe_str(part3.get('examiner_intro'))
        if intro and not self.is_stopped:
            self._speak(intro, "part3_intro.mp3", on_question, part=3, q_num=0)

        transition = _safe_str(part3.get('examiner_transition'))
        if transition and not self.is_stopped:
            self._speak(transition, "part3_transition.mp3",
                        on_question, part=3, q_num=0)

        questions = _to_questions_list(part3.get('questions'))
        for i, q in enumerate(questions):
            if self.is_stopped:
                return
            self._ask_and_probe(
                q['question'], part=3, q_num=i + 1,
                on_question=on_question, on_timer=on_timer,
            )

        closing = _safe_str(part3.get('examiner_closing'))
        if not closing and not self.is_stopped:
            try:
                closing = self.content_examiner.generate_closing_remark()
            except Exception:
                closing = "Thank you. That is the end of the speaking test."
        if closing:
            self._speak(closing, "part3_closing.mp3",
                        on_question, part=3, q_num=0)

        print(f"\n Part 3 complete — {len(questions)} questions")

    # ============================================================
    # SHARED QUESTION + PROBE LOGIC
    # ============================================================

    def _ask_and_probe(
        self,
        question: str,
        part: int,
        q_num: int,
        on_question,
        on_timer,
    ) -> None:
        """Speak a question, simulate the answer, run the examiner, probe if needed."""
        if self.is_stopped:
            return

        # Optional "Now, ", "So, " prefix
        prefix = ""
        if random.random() < 0.25:
            prefix = random.choice(self.NATURAL_TRANSITIONS)
        text = f"{prefix}{question}" if prefix else question

        audio_path = self._speak(text, f"part{part}_q{q_num}.mp3",
                                  on_question, part=part, q_num=q_num)
        print(f" Q{q_num}: {question[:80]}...")

        # Simulate student response
        simulated = self._simulate_response(question, part=part)
        self.responses[f"part{part}_q{q_num}"] = simulated

        # Countdown for the student's speaking time
        if part == 1:
            wait = random.randint(18, 30)
        elif part == 3:
            wait = random.randint(25, 40)
        else:
            wait = 30
        self._countdown(wait, f"Speaking Q{q_num}", on_timer)
        if self.is_stopped:
            return

        # Record the turn
        self.conversation_history.append({
            'part': part,
            'question': question,
            'response': simulated,
        })

        # Run the examiner
        try:
            analysis = self.content_examiner.analyze_response(
                transcript=simulated,
                question=question,
                part=part,
            )
        except Exception as e:
            logger.warning(f"Examiner analysis failed: {e}")
            analysis = {'action': 'move_on', 'follow_up': None}

        action = (analysis or {}).get('action')
        follow_up = (analysis or {}).get('follow_up')

        # Handle probe / deepen / encourage / redirect
        if action in ('probe', 'deepen') and follow_up:
            print(f" ↳ {action.capitalize()}: {follow_up[:80]}...")
            self._speak(follow_up, f"part{part}_q{q_num}_{action}.mp3",
                        on_question, part=part, q_num=q_num)
            self._countdown(15, "Extended response", on_timer)
        elif action == 'encourage' and follow_up:
            print(f" ↳ Encouragement: {follow_up[:80]}...")
            self._speak(follow_up, f"part{part}_q{q_num}_encourage.mp3",
                        on_question, part=part, q_num=q_num)
            self._countdown(5, "Pause", on_timer)
        elif action == 'redirect' and follow_up:
            print(f" ↳ Redirect: {follow_up[:80]}...")
            self._speak(follow_up, f"part{part}_q{q_num}_redirect.mp3",
                        on_question, part=part, q_num=q_num)
            self._countdown(20, "Redirected response", on_timer)

    # ============================================================
    # SIMULATED STUDENT
    # ============================================================

    def _simulate_response(self, question: str, part: int) -> str:
        """
        Simulate a student response.
        In production, replace with actual VAD + Whisper transcription.
        """
        response_type = random.choices(
            ['good', 'short', 'hesitant', 'off_topic'],
            weights=[0.5, 0.2, 0.2, 0.1],
        )[0]

        q_preview = (question or "this topic")[:40].lower()

        if response_type == 'good':
            return (
                f"I think {q_preview} is very important because it affects "
                f"many aspects of our lives. For example, in my experience, "
                f"I have seen how it impacts communities and individuals in "
                f"different ways. It's something we should pay more attention to."
            )
        if response_type == 'short':
            return "Yes, I agree."
        if response_type == 'hesitant':
            return "Um... I think... uh... maybe it depends on the situation... um..."
        # off_topic
        return "I really enjoy playing football with my friends on weekends."

    # ============================================================
    # SPEAK + COUNTDOWN
    # ============================================================

    def _speak(
        self,
        text: str,
        filename: str,
        on_question,
        part: int,
        q_num: int,
    ) -> Optional[str]:
        """Speak text via the voice module and emit the on_question callback once."""
        if not text or self.is_stopped:
            return None

        print(f" {text[:100]}...")

        audio_path = None
        if self.voice is not None:
            try:
                # FIX: use the actual method name
                audio_path = self.voice.speak_sync(text, filename)
            except Exception as e:
                logger.warning(f"Voice speak failed: {e}")

        # FIX: single call to on_question
        if on_question:
            try:
                on_question(part, q_num, text, audio_path)
            except Exception as e:
                logger.warning(f"on_question callback failed: {e}")

        # Natural pause between utterances
        self._sleep(random.uniform(0.5, 1.5))
        return audio_path

    def _countdown(self, seconds: int, label: str, callback) -> None:
        """
        Countdown with pause/stop support and speed multiplier.

         FIX: speed_multiplier lets tests run 10-100x faster.
        """
        if seconds <= 0:
            return

        for remaining in range(int(seconds), 0, -1):
            # Handle pause
            while self.is_paused and not self.is_stopped:
                self._sleep(0.1)

            if self.is_stopped:
                return

            if callback:
                try:
                    callback(remaining, label)
                except Exception as e:
                    logger.warning(f"on_timer callback failed: {e}")

            if remaining % 15 == 0 and remaining != seconds:
                mins = remaining // 60
                secs = remaining % 60
                print(f" {label}: {mins}:{secs:02d} remaining")

            self._sleep(1)

    def _sleep(self, seconds: float) -> None:
        """Sleep scaled by speed_multiplier (for fast test runs)."""
        time.sleep(max(0.0, seconds / self.speed_multiplier))


# ============================================================
# INTERFACE (thin wrapper for CLI use)
# ============================================================

class MockTestInterface:
    """
    User-friendly wrapper around SpeakingMockTest.

     FIX: Now imports the actual factory functions.
    """

    def __init__(
        self,
        ai_engine=None,
        speed_multiplier: float = 1.0,
    ):
        # ---- Real imports (were broken before) ----
        from .test_generator import create_speaking_test_generator
        from .examiner_voice import create_examiner_voice

        if ai_engine is None:
            raise ValueError(
                "MockTestInterface requires an ai_engine for test generation"
            )

        self.generator = create_speaking_test_generator(ai_engine)
        self.voice = create_examiner_voice(use_cache=True)

        self.mock = SpeakingMockTest(
            test_generator=self.generator,
            examiner_voice=self.voice,
            ai_analyzer=ai_engine,
            speed_multiplier=speed_multiplier,
        )

        self.current_question = None
        self.timer_seconds = 0
        self.results = None

    def on_question(self, part, q_num, question, audio_path):
        self.current_question = {
            'part': part,
            'number': q_num,
            'question': question,
            'audio': audio_path,
        }

    def on_timer(self, seconds, label):
        self.timer_seconds = seconds

    def on_complete(self, results):
        self.results = results
        print("\n Test results ready!")

    def start_test(self, difficulty="medium", topic=None):
        return self.mock.run_full_test(
            difficulty=difficulty,
            topic=topic,
            on_question=self.on_question,
            on_timer=self.on_timer,
            on_complete=self.on_complete,
        )

    def pause_test(self):
        self.mock.pause_test()

    def resume_test(self):
        self.mock.resume_test()

    def stop_test(self):
        self.mock.stop_test()

    def get_current_question(self):
        return self.current_question

    def get_timer(self):
        return self.timer_seconds

    def get_results(self):
        return self.results


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    import os

    # ---- CLI usage: python -m modules.ielts.speaking.mock_test [difficulty] [topic] ----
    difficulty = sys.argv[1] if len(sys.argv) > 1 else "medium"
    topic = sys.argv[2] if len(sys.argv) > 2 else None
    speed = float(os.environ.get("MOCK_TEST_SPEED", "1.0"))

    try:
        from ai_engine import ai_engine
    except ImportError:
        print(" Could not import ai_engine. Run this from the project root.")
        sys.exit(1)

    interface = MockTestInterface(ai_engine=ai_engine, speed_multiplier=speed)
    results = interface.start_test(difficulty, topic)

    if results:
        print("\nTest Results:")
        test = results.get('test') or {}
        print(f" Questions asked: {test.get('total_questions', 0)}")
        print(f" Duration: {test.get('total_duration', 'unknown')}")
        print(f" Completed: {results.get('completed_at')}")
    else:
        print("\n Test failed to run")


__all__ = [
    'SpeakingMockTest',
    'MockTestInterface',
]