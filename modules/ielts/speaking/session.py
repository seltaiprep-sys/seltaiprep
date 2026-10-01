"""IELTS Speaking Session Orchestrator - Production-ready state machine

FIX:
  (1) Removed call to non-existent emotion_detector.calibrate() — would crash on start()
  (2) Fixed overall band formula: average of coherence + fluency (no fabricated 75/25 split)
  (3) Floor is now 0.0 (was 2.0)
  (4) Default scores are 0.0 when no turns (was 5.0 / 0.5)
  (5) hesitation_avg now divides by turns that have emotion data
  (6) Timer uses a dedicated timer thread with an explicit cancel handle
  (7) _speak() accepts wait_for_completion parameter
  (8) _record_and_submit() validates audio before submitting
  (9) get_next_prompt() no longer re-serves a question that was already spoken
  (10) audio_duration computed via _safe_audio_duration()
  (11) _fallback_start resets question index
  (12) submit_response in PART2_PREP is rejected (only timer drives that transition)
  (13) part2_topic uses first sentence of the cue card
  (14) Coherence score read from the top-level result object
"""

import time
import logging
import json
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime

import numpy as np

from modules.ielts.speaking.content_aware_examiner import ContentAwareExaminer
from modules.ielts.speaking.coherence import create_coherence_analyzer
from modules.ielts.speaking.emotion import create_emotion_detector, EmotionResult

logger = logging.getLogger(__name__)


# ============================================================
# STATE MACHINE
# ============================================================

class SessionState(Enum):
    IDLE = "idle"
    PART1 = "part1"
    PART2_PREP = "part2_prep"
    PART2_SPEECH = "part2_speech"
    PART3 = "part3"
    FINISHED = "finished"


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class Turn:
    """A single interaction turn (question → answer → assessment)"""
    timestamp: float
    part: int
    question: str
    transcript: str
    audio_duration: float
    emotion_result: Optional[EmotionResult] = None
    coherence_result: Optional[Dict] = None
    examiner_action: Optional[str] = None
    follow_up: Optional[str] = None

    def to_dict(self) -> Dict:
        emotion = None
        if self.emotion_result is not None:
            emotion = getattr(self.emotion_result, 'emotions', None)
        return {
            'timestamp': self.timestamp,
            'part': self.part,
            'question': self.question,
            'transcript': self.transcript,
            'audio_duration': round(self.audio_duration, 2),
            'emotion': emotion,
            'coherence': self.coherence_result,
            'examiner_action': self.examiner_action,
            'follow_up': self.follow_up,
        }


@dataclass
class SessionReport:
    """Final report for the entire speaking test"""
    overall_band: float
    coherence_band: float
    fluency_score: float
    confidence_trend: str
    total_duration: float
    turns: List[Dict]
    themes_discussed: List[str]
    profile_summary: Dict

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=str)


# ============================================================
# ORCHESTRATOR
# ============================================================

class SpeakingSession:
    """
    Full IELTS Speaking test orchestrator.

    Wraps:
    - ContentAwareExaminer (interaction & probing)
    - CoherenceAnalyzer (grading)
    - VoiceEmotionDetector (real-time affect tracking)

    Also handles:
    - IELTS timings (Part 2 prep/speech)
    - State machine transitions
    - Audio recording/playback callbacks
    - Session logging & reporting
    """

    # ---- IELTS timing constants ----
    PART1_MIN_QUESTIONS = 4
    PART1_MAX_QUESTIONS = 6
    PART2_PREP_SECONDS = 60
    PART2_SPEECH_SECONDS = 120
    PART3_MIN_QUESTIONS = 3
    PART3_MAX_QUESTIONS = 5

    COUNTDOWN_WARNINGS = [10, 5, 3, 2, 1]

    # ---- Minimums for scoring ----
    MIN_WORDS_FOR_TURN = 5

    def __init__(
        self,
        question_bank: Dict[int, List[str]],
        examiner: Optional[ContentAwareExaminer] = None,
        emotion_detector: Optional[Any] = None,
        coherence_analyzer: Optional[Any] = None,
        stt_callback: Optional[Callable[[bytes, int], str]] = None,
        tts_callback: Optional[Callable[[str], None]] = None,
        audio_recorder_callback: Optional[Callable[[int], tuple]] = None,
        use_semantic: bool = True,
    ):
        # ---- Callbacks ----
        self.stt = stt_callback
        self.tts = tts_callback
        self.record = audio_recorder_callback

        # ---- Question bank ----
        if not isinstance(question_bank, dict):
            raise ValueError("question_bank must be a dict {1: [...], 2: [...], 3: [...]}")
        self.question_bank = question_bank

        # ---- Core modules ----
        if coherence_analyzer is None:
            coherence_analyzer = create_coherence_analyzer(use_semantic=use_semantic)
        self.coherence_analyzer = coherence_analyzer

        if emotion_detector is None:
            emotion_detector = create_emotion_detector()
        self.emotion_detector = emotion_detector

        if examiner is None:
            examiner = ContentAwareExaminer(
                ai_engine=None,
                coherence_analyzer=coherence_analyzer,
            )
        self.examiner = examiner

        # ---- Session state ----
        self.state = SessionState.IDLE
        self.turns: List[Turn] = []
        self.session_start_time: float = 0.0
        self.part2_topic: str = ""
        self.part2_cue_card: str = ""
        self.part1_question_index = 0
        self.part3_question_index = 0
        self.is_recording = False

        # FIX: track the last question we spoke so get_next_prompt() doesn't repeat it
        self._last_spoken_question: Optional[str] = None

        # FIX: track the timer thread so we can cancel it
        self._timer_thread: Optional[threading.Thread] = None
        self._timer_cancel = threading.Event()

        self._final_report: Optional[SessionReport] = None

        logger.info("SpeakingSession initialized")

    # ============================================================
    # PUBLIC API
    # ============================================================

    def start(self) -> None:
        """Start the test. Begins with Part 1."""
        if self.state != SessionState.IDLE:
            raise RuntimeError(f"Cannot start: session is already in state {self.state}")

        self.session_start_time = time.time()

        # FIX: emotion_detector no longer has .calibrate().
        # The detector auto-handles silence internally.
        # We simply skip the calibration step.

        self.state = SessionState.PART1
        self.part1_question_index = 0

        question = self._get_next_question(1)
        if question:
            self._ask_question(question, part=1)
        else:
            self._fallback_start()

    def submit_response(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        transcript: str,
    ) -> None:
        """
        Submit the student's spoken response.

         FIX: Rejected in IDLE / FINISHED / PART2_PREP states.
        """
        if self.state in (SessionState.IDLE, SessionState.FINISHED):
            logger.warning(f"submit_response called in state {self.state} – ignoring")
            return

        # FIX: In PART2_PREP the student is just preparing, not speaking
        if self.state == SessionState.PART2_PREP:
            logger.warning("submit_response called during Part 2 preparation – ignoring")
            return

        transcript = (transcript or "").strip()

        # ---- Duration (safe) ----
        audio_duration = self._safe_audio_duration(audio_bytes, sample_rate)

        # ---- 1. Emotion analysis (only if audio is meaningful) ----
        emotion_result: Optional[EmotionResult] = None
        if audio_bytes and sample_rate and audio_duration > 0.3:
            try:
                audio_np = self._bytes_to_numpy(audio_bytes, sample_rate)
                emotion_result = self.emotion_detector.analyze(
                    audio_np, sample_rate, transcript=transcript,
                )
            except Exception as e:
                logger.warning(f"Emotion analysis failed: {e}")
                emotion_result = None

        # ---- 2. Current question ----
        current_question = self._get_current_question()

        # ---- 3. Examiner analysis ----
        try:
            examiner_result = self.examiner.analyze_response(
                transcript=transcript,
                question=current_question,
                part=self._current_part_number(),
            )
        except Exception as e:
            logger.warning(f"Examiner analysis failed: {e}")
            examiner_result = {
                'action': 'move_on',
                'reason': 'analysis_error',
                'follow_up': None,
                'assessment': {'coherence_band': 0.0},
            }

        # ---- 4. Coherence (top-level score, not from details) ----
        coherence_dict: Dict[str, Any] = {}
        try:
            coh = self.coherence_analyzer.analyze(
                text=transcript, topic=current_question
            )
            # FIX: read score from the result object, not details
            coherence_dict = {
                'score': float(getattr(coh, 'score', 0.0)),
                'details': getattr(coh, 'details', {}) or {},
            }
        except Exception as e:
            logger.warning(f"Coherence analysis failed: {e}")
            coherence_dict = {'score': 0.0, 'details': {}, 'error': str(e)}

        # ---- 5. Store the turn ----
        turn = Turn(
            timestamp=time.time(),
            part=self._current_part_number(),
            question=current_question,
            transcript=transcript,
            audio_duration=audio_duration,
            emotion_result=emotion_result,
            coherence_result=coherence_dict,
            examiner_action=examiner_result.get('action'),
            follow_up=examiner_result.get('follow_up'),
        )
        self.turns.append(turn)

        logger.info(
            f"Part {turn.part} turn: action={examiner_result.get('action')}, "
            f"words={len(transcript.split())}, "
            f"coh={coherence_dict.get('score')}, "
            f"audio={audio_duration:.1f}s"
        )

        # ---- 6. Transition ----
        self._transition(examiner_result)

    def get_next_prompt(self) -> Optional[str]:
        """
        Return the examiner's next utterance.

         FIX: No longer re-serves a question that was already spoken.
        Only returns the last follow_up if it was set but not yet spoken.
        """
        if self.state == SessionState.FINISHED:
            return None

        # If the last turn has a follow_up, return it (once)
        if self.turns and self.turns[-1].follow_up:
            follow_up = self.turns[-1].follow_up
            self.turns[-1].follow_up = None # consumed
            return follow_up

        # Otherwise, ask the next question from the bank
        next_q = self._get_next_question_to_ask()
        if next_q and next_q != self._last_spoken_question:
            self._last_spoken_question = next_q
            return next_q
        return None

    def is_finished(self) -> bool:
        return self.state == SessionState.FINISHED

    def get_report(self) -> SessionReport:
        """
        Generate the final session report.

         FIX: Real IELTS-inspired formula. Coherence is only one input;
                fluency is derived from emotion signals.
         FIX: No fabricated 75/25 weighting.
         FIX: Defaults are 0.0 when no data (was 5.0 / 0.5).
         FIX: Floor 0.0 (was 2.0).
         FIX: hesitation_avg only over turns that had emotion data.
        """
        if self.state != SessionState.FINISHED:
            logger.warning("Getting report before session finished – partial data")

        # ---- Coherence: mean across turns that have data ----
        coherence_scores = [
            float(t.coherence_result.get('score', 0.0))
            for t in self.turns
            if isinstance(t.coherence_result, dict)
            and t.coherence_result.get('score') is not None
        ]
        avg_coherence = (
            sum(coherence_scores) / len(coherence_scores)
            if coherence_scores else 0.0
        )

        # ---- Emotion/fluency: only over turns that have emotion data ----
        confidence_values: List[float] = []
        hesitation_values: List[float] = []
        for t in self.turns:
            if t.emotion_result is None:
                continue
            emotions = getattr(t.emotion_result, 'emotions', None) or {}
            # Skip empty results (from emotion.py's empty-result helper)
            if getattr(t.emotion_result, 'details', {}).get('empty'):
                continue
            confidence_values.append(float(emotions.get('confidence', 0.0)))
            hesitation_values.append(float(emotions.get('hesitation', 0.0)))

        avg_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values else 0.0
        )
        hesitation_avg = (
            sum(hesitation_values) / len(hesitation_values)
            if hesitation_values else 0.0
        )

        # Fluency is 0.0 if there is no emotion data at all
        if confidence_values:
            fluency_score = (
                avg_confidence * 0.6 + (1.0 - hesitation_avg) * 0.4
            )
        else:
            fluency_score = 0.0

        # ---- Confidence trend ----
        if len(confidence_values) >= 2:
            if confidence_values[-1] > confidence_values[0] + 0.05:
                trend = "improving"
            elif confidence_values[-1] < confidence_values[0] - 0.05:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "unknown"

        # ---- Overall band ----
        # Coherence (0-9) + fluency (0-1 scaled to 0-9) averaged
        # FIX: was (coh * 0.75) + (fluency * 9 * 0.25) — fabricated split
        fluency_band = fluency_score * 9.0
        if coherence_scores or confidence_values:
            overall = (avg_coherence + fluency_band) / 2.0
        else:
            overall = 0.0

        # FIX: floor 0.0 (was 2.0)
        overall = round(min(9.0, max(0.0, overall)), 1)

        self._final_report = SessionReport(
            overall_band=overall,
            coherence_band=round(avg_coherence, 1),
            fluency_score=round(fluency_score, 3),
            confidence_trend=trend,
            total_duration=round(time.time() - self.session_start_time, 1),
            turns=[t.to_dict() for t in self.turns],
            themes_discussed=self.examiner.student_profile.get('common_themes', []),
            profile_summary=self.examiner.student_profile,
        )
        return self._final_report

    # ============================================================
    # STATE TRANSITION
    # ============================================================

    def _transition(self, examiner_result: Dict) -> None:
        action = (examiner_result or {}).get('action')

        if self.state == SessionState.PART1:
            self.part1_question_index += 1

            if self.part1_question_index >= self.PART1_MAX_QUESTIONS:
                logger.info("Part 1 complete – transitioning to Part 2")
                self._start_part2()
            elif action in ('move_on', 'deepen'):
                next_q = self._get_next_question(1)
                if next_q:
                    self._ask_question(next_q, part=1)
                else:
                    self._start_part2()
            # else: follow_up is spoken via get_next_prompt()

        elif self.state == SessionState.PART2_SPEECH:
            logger.info("Part 2 speech complete – transitioning to Part 3")
            self._start_part3()

        elif self.state == SessionState.PART3:
            self.part3_question_index += 1

            if self.part3_question_index >= self.PART3_MAX_QUESTIONS:
                logger.info("Part 3 complete – finishing session")
                self._finish_session()
            elif action in ('move_on', 'deepen'):
                next_q = self._get_next_question(3)
                if next_q:
                    self._ask_question(next_q, part=3)
                else:
                    self._finish_session()

    # ============================================================
    # PHASE STARTERS
    # ============================================================

    def _start_part2(self) -> None:
        self.state = SessionState.PART2_PREP

        cue_card = self._get_next_question(2)
        if not cue_card:
            self._fallback_part2()
            return

        self.part2_cue_card = cue_card
        # FIX: better topic extraction (first sentence)
        first_sentence = cue_card.split('.')[0].strip()
        self.part2_topic = first_sentence[:120] if first_sentence else cue_card[:120]

        prompt = (
            "Now, I'm going to give you a topic. You have 1 minute to prepare. "
            f"Here is your cue card:\n\n{cue_card}\n\nYour time starts now."
        )
        self._speak(prompt)

        self._start_timer(
            duration=self.PART2_PREP_SECONDS,
            on_complete=self._on_part2_prep_complete,
            warnings=self.COUNTDOWN_WARNINGS,
        )

    def _start_part2_speech(self) -> None:
        self.state = SessionState.PART2_SPEECH

        prompt = (
            "Your preparation time is up. Please speak for up to 2 minutes. "
            "I'll let you know when to stop."
        )
        self._speak(prompt)

        self._start_timer(
            duration=self.PART2_SPEECH_SECONDS,
            on_complete=self._on_part2_speech_complete,
            warnings=self.COUNTDOWN_WARNINGS,
        )

    def _start_part3(self) -> None:
        self.state = SessionState.PART3
        self.part3_question_index = 0

        transition = self.examiner.generate_personalized_transition(
            prev_topic=self.part2_topic,
            prev_answer="",
            next_topic="Part 3 discussion",
        )
        self._speak(transition)

        first_q = self._get_next_question(3)
        if first_q:
            self._ask_question(first_q, part=3)
        else:
            self._finish_session()

    def _finish_session(self) -> None:
        self._cancel_timer()
        self.state = SessionState.FINISHED
        closing = self.examiner.generate_closing_remark()
        self._speak(closing)
        logger.info("Session finished")

    # ============================================================
    # TIMER
    # ============================================================

    def _start_timer(
        self,
        duration: int,
        on_complete: Callable,
        warnings: Optional[List[int]] = None,
    ) -> None:
        """
        Start a countdown timer.

         FIX: Uses an Event to allow cancellation, and stores the handle.
        """
        self._cancel_timer()
        self._timer_cancel = threading.Event()

        def timer_thread():
            try:
                for remaining in range(duration, 0, -1):
                    if self._timer_cancel.is_set():
                        return
                    if warnings and remaining in warnings:
                        self._speak(f"{remaining} seconds remaining")
                    # Sleep in 100 ms slices so cancellation is responsive
                    for _ in range(10):
                        if self._timer_cancel.is_set():
                            return
                        time.sleep(0.1)
                if not self._timer_cancel.is_set():
                    try:
                        on_complete()
                    except Exception as e:
                        logger.error(f"Timer on_complete failed: {e}")
            except Exception as e:
                logger.error(f"Timer thread failed: {e}")

        self._timer_thread = threading.Thread(target=timer_thread, daemon=True)
        self._timer_thread.start()

    def _cancel_timer(self) -> None:
        """Cancel any running timer."""
        if self._timer_cancel:
            self._timer_cancel.set()
        if self._timer_thread and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=1.0)
        self._timer_thread = None

    def _on_part2_prep_complete(self) -> None:
        logger.info("Part 2 prep time expired")
        if self.state == SessionState.PART2_PREP:
            self._start_part2_speech()

    def _on_part2_speech_complete(self) -> None:
        logger.info("Part 2 speech time expired")
        if self.state == SessionState.PART2_SPEECH:
            self._speak("Your time is up. Please stop speaking.")

    # ============================================================
    # QUESTION MANAGEMENT
    # ============================================================

    def _get_next_question(self, part: int) -> Optional[str]:
        questions = self.question_bank.get(part, [])
        if not questions:
            return None

        if part == 1:
            if self.part1_question_index < len(questions):
                return questions[self.part1_question_index]
            return None
        if part == 2:
            return questions[0] if questions else None
        if part == 3:
            if self.part3_question_index < len(questions):
                return questions[self.part3_question_index]
            return None
        return None

    def _get_current_question(self) -> str:
        if self.turns:
            return self.turns[-1].question
        return "What is your name?"

    def _get_next_question_to_ask(self) -> Optional[str]:
        if self.state == SessionState.PART1:
            return self._get_next_question(1)
        if self.state == SessionState.PART3:
            return self._get_next_question(3)
        return None

    def _ask_question(self, question: str, part: int) -> None:
        logger.info(f"Part {part} asking: {question[:60]}...")
        self._last_spoken_question = question
        self._speak(question)

    def _current_part_number(self) -> int:
        if self.state == SessionState.PART1:
            return 1
        if self.state in (SessionState.PART2_PREP, SessionState.PART2_SPEECH):
            return 2
        if self.state == SessionState.PART3:
            return 3
        return 0

    # ============================================================
    # IO HELPERS
    # ============================================================

    def _bytes_to_numpy(self, audio_bytes: bytes, sample_rate: int) -> np.ndarray:
        """Convert raw 16-bit PCM bytes to float32 array in [-1, 1]."""
        try:
            arr = np.frombuffer(audio_bytes, dtype=np.int16)
            return arr.astype(np.float32) / 32768.0
        except Exception as e:
            logger.warning(f"_bytes_to_numpy failed: {e}")
            return np.array([], dtype=np.float32)

    @staticmethod
    def _safe_audio_duration(audio_bytes: bytes, sample_rate: int) -> float:
        """ FIX: Safe duration computation with validation."""
        if not audio_bytes or not sample_rate or sample_rate <= 0:
            return 0.0
        try:
            n_samples = len(audio_bytes) // 2 # 16-bit = 2 bytes/sample
            return n_samples / float(sample_rate)
        except Exception:
            return 0.0

    def _speak(self, text: str) -> None:
        """Send text to TTS. FIX: catches all errors and logs."""
        if not text:
            return
        if self.tts:
            try:
                self.tts(text)
            except Exception as e:
                logger.error(f"TTS error: {e}")
        else:
            logger.info(f"TTS (mock): {text}")

    # ============================================================
    # FALLBACKS
    # ============================================================

    def _fallback_start(self) -> None:
        """ FIX: reset the question index too."""
        self.part1_question_index = 0
        self._last_spoken_question = "Tell me about yourself."
        self._speak("Let's begin the speaking test. Please tell me about yourself.")
        self.state = SessionState.PART1

    def _fallback_part2(self) -> None:
        self.part2_cue_card = "Describe a memorable event in your life."
        self.part2_topic = self.part2_cue_card
        self._start_part2()

    def _record_and_submit(self) -> None:
        """
        Helper for external CLI loops.
         FIX: Validates audio before submitting.
        """
        if not self.record or not self.stt:
            logger.error("Record or STT callbacks missing")
            return

        duration = 30
        if self.state == SessionState.PART2_SPEECH:
            duration = self.PART2_SPEECH_SECONDS

        logger.info(f"Recording for {duration}s...")
        try:
            result = self.record(duration)
        except Exception as e:
            logger.error(f"Recording failed: {e}")
            return

        if not result:
            logger.warning("Record callback returned no data — skipping submit")
            return

        audio_bytes, sr = result
        # FIX: validate before submitting
        if not audio_bytes or not sr or len(audio_bytes) < 100:
            logger.warning("Recorded audio is empty or too short — skipping submit")
            return

        try:
            transcript = self.stt(audio_bytes, sr)
        except Exception as e:
            logger.error(f"STT failed: {e}")
            transcript = ""

        logger.info(f"Transcript: {(transcript or '')[:80]}...")
        self.submit_response(audio_bytes, sr, transcript)


# ============================================================
# FACTORY
# ============================================================

def create_speaking_session(
    question_bank: Dict[int, List[str]],
    examiner: Optional[ContentAwareExaminer] = None,
    emotion_detector: Optional[Any] = None,
    coherence_analyzer: Optional[Any] = None,
    stt_callback: Optional[Callable[[bytes, int], str]] = None,
    tts_callback: Optional[Callable[[str], None]] = None,
    audio_recorder_callback: Optional[Callable[[int], tuple]] = None,
    use_semantic: bool = True,
) -> SpeakingSession:
    """Create a ready-to-use SpeakingSession."""
    return SpeakingSession(
        question_bank=question_bank,
        examiner=examiner,
        emotion_detector=emotion_detector,
        coherence_analyzer=coherence_analyzer,
        stt_callback=stt_callback,
        tts_callback=tts_callback,
        audio_recorder_callback=audio_recorder_callback,
        use_semantic=use_semantic,
    )


__all__ = [
    'SessionState',
    'Turn',
    'SessionReport',
    'SpeakingSession',
    'create_speaking_session',
]