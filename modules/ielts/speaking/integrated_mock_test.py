"""IELTS Mock Test with actual recording, VAD, transcription, progress tracking, and detailed feedback

Production‑hardened version with:
- Dynamic silence threshold calibration
- Dependency injection for analyzers
- Background model loading
- Part 2 countdown callbacks
- Retry logic for empty transcripts
- Proper resource cleanup

v2.1 — PostgreSQL Progress Tracker:
- Progress tracker now uses the main PostgreSQL DB (port 5433)
- Falls back to SQLite if PostgreSQL isn't configured
- Auto-detects DB from DATABASE_URL or DB_* environment variables
"""

import os
import time
import json
import logging
import threading
import random
from typing import Dict, Optional, Callable, List, Any
from datetime import datetime, timezone

import numpy as np

# ---- Refactored imports ----
from .recorder import create_recorder
from .transcriber import create_transcriber
from .examiner_voice import create_examiner_voice, ExaminerVoice
from .content_aware_examiner import ContentAwareExaminer
from .coherence import create_coherence_analyzer
from .grammar import create_grammar_analyzer
from .emotion import create_emotion_detector
from .progress_tracker import create_progress_tracker
from .scoring import create_band_calculator

logger = logging.getLogger(__name__)


def _build_progress_tracker_url() -> str:
    """
    Build the database URL for the progress tracker.

    Priority:
      1. DATABASE_URL env var (if it's PostgreSQL)
      2. Individual DB_* env vars
      3. SQLite fallback (data/progress.db)

    Returns the URL string suitable for create_progress_tracker(db_url=...)
    """
    db_url = os.environ.get('DATABASE_URL', '').strip()

    # If DATABASE_URL points to PostgreSQL, use it directly
    if db_url and not db_url.startswith('sqlite'):
        return db_url

    # Otherwise, build from individual DB_* env vars
    db_user = os.environ.get('DB_USER', '').strip()
    db_pass = os.environ.get('DB_PASS', '').strip()
    db_host = os.environ.get('DB_HOST', 'localhost').strip()
    db_port = os.environ.get('DB_PORT', '5433').strip()
    db_name = os.environ.get('DB_NAME', '').strip()

    if db_user and db_name:
        # URL-encode the password to handle special characters like '@'
        from urllib.parse import quote_plus
        safe_pass = quote_plus(db_pass) if db_pass else ''
        return (
            f"postgresql+psycopg2://{db_user}:{safe_pass}@"
            f"{db_host}:{db_port}/{db_name}"
        )

    # Final fallback — SQLite
    logger.info("Progress tracker: using SQLite fallback (data/progress.db)")
    return "sqlite:///data/progress.db"


class IntegratedSpeakingMockTest:
    """
    Full IELTS Speaking mock test with recording, transcription, analysis,
    progress tracking, and detailed feedback.

    Now uses dependency injection and all refactored modules.
    """

    def __init__(
        self,
        test_generator: Any,
        examiner_voice: Optional[ExaminerVoice] = None,
        coherence_analyzer: Optional[Any] = None,
        grammar_analyzer: Optional[Any] = None,
        emotion_detector: Optional[Any] = None,
        transcriber: Optional[Any] = None,
        recorder: Optional[Any] = None,
        band_calculator: Optional[Any] = None,
        progress_tracker: Optional[Any] = None,
        output_dir: str = "data/recordings",
        student_id: str = "default",
        use_semantic: bool = True,
        input_device_index: Optional[int] = None,
        preload_models: bool = True
    ):
        """
        Args:
            test_generator: Object with `generate_complete_test()` method
            examiner_voice: ExaminerVoice instance (auto‑created if None)
            coherence_analyzer: CoherenceAnalyzer instance (auto‑created)
            grammar_analyzer: SpokenGrammarAnalyzer instance (auto‑created)
            emotion_detector: VoiceEmotionDetector instance (auto‑created)
            transcriber: WhisperTranscriber instance (auto‑created)
            recorder: AudioRecorder instance (auto‑created)
            band_calculator: IELTSBandCalculator instance (auto‑created)
            progress_tracker: ProgressTracker instance (auto‑created)
            output_dir: Directory to save recordings and results
            student_id: Unique identifier for the student
            use_semantic: Passed to CoherenceAnalyzer if auto‑created
            input_device_index: PyAudio device index (None = default)
            preload_models: If True, preload Whisper and Wav2Vec2 in background
        """
        self.test_generator = test_generator
        self.input_device_index = input_device_index
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.student_id = student_id or "default"

        # ---- Core dependencies (inject or auto‑create) ----
        self.voice = examiner_voice or create_examiner_voice(
            voice="female_british",
            output_dir=os.path.join(output_dir, "audio_cache"),
            use_cache=True
        )

        self.coherence_analyzer = coherence_analyzer or create_coherence_analyzer(use_semantic=use_semantic)
        self.grammar_analyzer = grammar_analyzer or create_grammar_analyzer(detect_advanced_errors=True)
        self.emotion_detector = emotion_detector or create_emotion_detector()
        self.transcriber = transcriber or create_transcriber(model_size="base", prefer_faster=True)
        self.recorder = recorder or create_recorder(
            sample_rate=16000,
            channels=1,
            use_webrtc=True,
            silence_duration=2.0,
            min_recording=1.0,
            max_recording=130.0,
            chunk_duration=0.5
        )
        self.band_calculator = band_calculator or create_band_calculator()

        # PostgreSQL progress tracker (falls back to SQLite if not configured)
        if progress_tracker is not None:
            self.tracker = progress_tracker
        else:
            tracker_url = _build_progress_tracker_url()
            try:
                self.tracker = create_progress_tracker(db_url=tracker_url)
                db_kind = tracker_url.split(':')[0]
                logger.info(f" Speaking progress tracker using {db_kind}")
            except TypeError:
                # Backward compat: older tracker without db_url parameter
                logger.warning(
                    "progress_tracker.create_progress_tracker() doesn't accept "
                    "db_url — falling back to db_path (SQLite). "
                    "Update progress_tracker.py to enable PostgreSQL."
                )
                self.tracker = create_progress_tracker(db_path="data/progress.db")

        # ---- State ----
        self.recording_history: List[Dict] = []
        self.responses: Dict = {}
        self.conversation_history: List[Dict] = []
        self.is_stopped = False

        # Register student in tracker
        self.tracker.register_student(self.student_id)

        # ---- Preload models in background (optional) ----
        if preload_models:
            try:
                from .pronunciation import _get_wav2vec_model
                threading.Thread(target=_get_wav2vec_model, daemon=True).start()
                logger.info("Wav2Vec2 preload started in background")
            except ImportError:
                pass

    # ============================================================
    # TRANSCRIPTION
    # ============================================================

    def _transcribe_audio(self, audio: np.ndarray, sr: int) -> str:
        """Transcribe audio using the injected transcriber."""
        if len(audio) == 0:
            return ""
        return self.transcriber.transcribe(audio, sr)

    # ============================================================
    # RESPONSE RECORDING (with timer callbacks)
    # ============================================================

    def record_response(
        self,
        question_num,
        part: int,
        max_duration: int = 45,
        timeout_callbacks: Dict[float, Callable] = None
    ) -> Dict:
        """
        Record the student's response with optional callbacks.

        Args:
            question_num: Question identifier (int or string)
            part: 1, 2, or 3
            max_duration: Maximum recording time in seconds
            timeout_callbacks: Dict {seconds: callback} for countdown warnings

        Returns:
            Dict with audio_path, duration, transcript, analysis
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        q_str = str(question_num).replace(" ", "_")
        filename = os.path.join(self.output_dir, f"part{part}_q{q_str}_{timestamp}.wav")

        self.recorder.max_recording = max_duration
        self.recorder.silence_duration = 2.0

        print(f" Recording... (speak now)")
        success = self.recorder.start_recording(
            filename=filename,
            timeout_callbacks=timeout_callbacks,
            input_device_index=self.input_device_index
        )

        if not success:
            return {'error': 'Recording failed'}

        # Wait for recording to finish
        while self.recorder.recording and not self.is_stopped:
            time.sleep(0.1)

        duration = self.recorder.get_duration()
        print(f" Recorded: {duration:.1f}s")

        audio_array = self.recorder.get_audio_array()
        transcript = self._transcribe_audio(audio_array, self.recorder.sample_rate)

        # ---- Retry logic for empty transcript ----
        if not transcript and duration > 2.0:
            print(" No speech detected – prompting to repeat...")
            prompt = "Sorry, I didn't catch that. Could you please repeat your answer?"
            self.voice.speak_sync(prompt)
            time.sleep(0.5)

            retry_filename = filename.replace(".wav", "_retry.wav")
            self.recorder.start_recording(
                filename=retry_filename,
                timeout_callbacks=timeout_callbacks,
                input_device_index=self.input_device_index
            )
            while self.recorder.recording and not self.is_stopped:
                time.sleep(0.1)
            audio_array = self.recorder.get_audio_array()
            transcript = self._transcribe_audio(audio_array, self.recorder.sample_rate)
            if transcript:
                print(f" (retry): {transcript[:120]}...")
                filename = retry_filename
            else:
                print(" (retry): still no speech detected")

        if transcript:
            print(f" : {transcript[:120]}...")
        else:
            print(f" : (no speech detected)")

        analysis = self._analyze_response(transcript, audio_array, part)

        result = {
            'audio_path': filename,
            'duration': duration,
            'transcript': transcript,
            'analysis': analysis,
            'part': part,
            'question_num': question_num,
            'timestamp': timestamp
        }

        self.recording_history.append(result)
        self.responses[f"part{part}_q{question_num}"] = result
        self.conversation_history.append({
            'part': part,
            'question': f"Q{question_num}",
            'response': transcript
        })

        return result

    # ============================================================
    # ANALYSIS
    # ============================================================

    def _analyze_response(self, transcript: str, audio: np.ndarray, part: int) -> Dict:
        """Analyze the response using all available analyzers."""
        analysis = {
            'word_count': len(transcript.split()) if transcript else 0,
            'grammar': None,
            'coherence': None,
            'emotion': None
        }

        if transcript and len(transcript.split()) > 3:
            # Grammar
            try:
                gr = self.grammar_analyzer.analyze(transcript)
                if hasattr(gr, 'score'):
                    score = gr.score
                elif isinstance(gr, dict):
                    score = gr.get('score', 6.0)
                else:
                    score = 6.0
                analysis['grammar'] = {
                    'score': score,
                    'errors': getattr(gr, 'errors', []),
                    'error_density': getattr(gr, 'error_density', 0)
                }
            except Exception as e:
                logger.warning(f"Grammar analysis failed: {e}")

            # Coherence
            try:
                cr = self.coherence_analyzer.analyze(transcript)
                if hasattr(cr, 'score'):
                    score = cr.score
                elif isinstance(cr, dict):
                    score = cr.get('score', 6.0)
                else:
                    score = 6.0
                analysis['coherence'] = {
                    'score': score,
                    'details': getattr(cr, 'details', {})
                }
            except Exception as e:
                logger.warning(f"Coherence analysis failed: {e}")

        # Emotion
        if len(audio) > 0:
            try:
                er = self.emotion_detector.analyze(audio, self.recorder.sample_rate)
                analysis['emotion'] = {
                    'confidence': er.emotions.get('confidence', 0.5),
                    'hesitation': er.emotions.get('hesitation', 0),
                    'nervousness': er.emotions.get('nervousness', 0),
                    'enthusiasm': er.emotions.get('enthusiasm', 0.5),
                    'primary': er.primary_emotion
                }
            except Exception as e:
                logger.warning(f"Emotion analysis failed: {e}")

        return analysis

    # ============================================================
    # TEST RUNNERS
    # ============================================================

    def _speak_and_wait(
        self,
        text: str,
        filename_prefix: str,
        on_question: Callable,
        q_num: int,
        add_acknowledgment: bool = False
    ):
        """Speak text, generate audio, and call on_question callback."""
        if not text:
            return
        filename = f"{filename_prefix}.mp3"
        audio_path = self.voice.speak_sync(text, filename)
        if on_question:
            on_question(self._current_part(), q_num, text, audio_path)
        logger.debug(f"Examiner: {text[:80]}...")

    def _current_part(self) -> int:
        # Placeholder – actual part is passed in run methods.
        return 1

    def _run_part1_with_recording(self, part1_data, on_question, on_timer):
        """Run Part 1 with recording."""
        intro_text = part1_data.get('introduction', '')
        self._speak_and_wait(intro_text, "part1_intro", on_question, 0)

        topics = part1_data.get('topics', [])
        question_offset = 0
        for topic_section in topics:
            if self.is_stopped:
                return
            topic_name = topic_section.get('title', '')
            print(f"\n Topic: {topic_name}")
            questions = topic_section.get('questions', [])
            for i, q in enumerate(questions):
                if self.is_stopped:
                    return
                q_num = question_offset + i + 1
                self._speak_and_wait(q['question'], f"part1_q{q_num}", on_question, q_num)

                result = self.record_response(q_num, part=1, max_duration=30)
                transcript = result.get('transcript', '')

                # Probe if answer is too short
                if len(transcript.split()) < 8 and random.random() < 0.5:
                    probe = random.choice([
                        "Can you tell me more about that?",
                        "Why do you think that is?",
                        "Can you give me an example?"
                    ])
                    self._speak_and_wait(probe, f"part1_q{q_num}_probe", on_question, f"{q_num}b")
                    self.record_response(f"{q_num}b", part=1, max_duration=20)

            question_offset += len(questions)

        print(f"\n Part 1 complete - {len(self.recording_history)} recordings")

    def _run_part2_with_recording(self, part2_data, on_question, on_timer):
        """Run Part 2 with the 2‑minute long turn and countdown warnings."""
        # Introduction
        intro = part2_data.get('introduction', '')
        self._speak_and_wait(intro, "part2_intro", on_question, 0)
        if self.is_stopped:
            return

        # Instructions
        instructions = part2_data.get('instructions', '')
        self._speak_and_wait(instructions, "part2_instructions", on_question, 0)
        if self.is_stopped:
            return

        # Topic and prompts
        topic = part2_data.get('topic', '')
        prompts = part2_data.get('prompts', [])
        print(f"\n Topic: {topic}")
        for p in prompts:
            print(f" • {p}")

        self.voice.speak_sync(topic, "part2_topic.mp3")
        if on_question:
            on_question(2, 0, topic, None)

        # ---- 1-minute preparation ----
        print(f"\n Preparation time: 60 seconds")
        for remaining in range(60, 0, -1):
            if self.is_stopped:
                return
            if remaining % 15 == 0 and remaining != 60:
                print(f" Preparation: {remaining}s remaining")
                self.voice.speak_sync(f"{remaining} seconds remaining")
            time.sleep(1)

        # Start cue
        start_cue = "Alright, you can start speaking now."
        self._speak_and_wait(start_cue, "part2_start", on_question, 0)

        # ---- 2-minute speech with countdown callbacks ----
        print(f"\n Recording your 2-minute talk...")

        def warning_callback(seconds: int):
            msg = f"{seconds} seconds remaining"
            print(f" {msg}")
            self.voice.speak_sync(msg)

        callbacks = {}
        for sec in [10, 5, 3, 2, 1]:
            callbacks[120 - sec] = lambda s=sec: warning_callback(s)

        result = self.record_response(
            0,
            part=2,
            max_duration=130,
            timeout_callbacks=callbacks
        )
        print(f" Long turn recorded: {result['duration']:.1f}s")

        # Stop cue
        stop_cue = "Thank you. Can I have the topic card back, please?"
        self._speak_and_wait(stop_cue, "part2_stop", on_question, 0)

        # Follow-up question
        follow_ups = [
            "Did you enjoy talking about this topic?",
            "Have you told anyone else about this?"
        ]
        follow_up = random.choice(follow_ups)
        self._speak_and_wait(follow_up, "part2_followup", on_question, 0)
        self.record_response("followup", part=2, max_duration=20)

        print(f"\n Part 2 complete")

    def _run_part3_with_recording(self, part3_data, on_question, on_timer):
        """Run Part 3 with recording."""
        intro = part3_data.get('introduction', '')
        self._speak_and_wait(intro, "part3_intro", on_question, 0)
        if self.is_stopped:
            return

        questions = part3_data.get('questions', [])
        for i, q in enumerate(questions):
            if self.is_stopped:
                return
            q_num = i + 1
            self._speak_and_wait(q['question'], f"part3_q{q_num}", on_question, q_num)

            result = self.record_response(q_num, part=3, max_duration=60)
            transcript = result.get('transcript', '')

            # Deep probe if answer is shallow
            if len(transcript.split()) < 15 and random.random() < 0.5:
                probe = random.choice([
                    "Why do you think that is?",
                    "Can you explain that further?",
                    "How does that affect society as a whole?"
                ])
                self._speak_and_wait(probe, f"part3_q{q_num}_probe", on_question, f"{q_num}b")
                self.record_response(f"{q_num}b", part=3, max_duration=30)
            elif i < len(questions) - 1 and random.random() < 0.3:
                follow = self._generate_followup(q['question'])
                self._speak_and_wait(follow, f"part3_followup_{q_num}", on_question, 0)

        if self.is_stopped:
            return

        closing = "Thank you. That is the end of the speaking test."
        self._speak_and_wait(closing, "part3_closing", on_question, 0)
        print(f"\n Part 3 complete")

    def _generate_followup(self, question: str) -> str:
        followups = [
            "Can you elaborate on that a bit more?",
            "What about the opposite perspective?",
            "How do you think this might change in the future?",
            "Do you think everyone would agree with that?"
        ]
        return random.choice(followups)

    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================

    def run_full_test_with_recording(
        self,
        difficulty: str = "medium",
        topic: str = None,
        on_question: Callable = None,
        on_timer: Callable = None,
        on_complete: Callable = None
    ) -> Dict:
        """Run the complete IELTS Speaking test with recording."""
        test = self.test_generator.generate_complete_test(difficulty, topic)
        self.is_stopped = False

        print("\n" + "=" * 60)
        print("IELTS SPEAKING MOCK TEST (WITH RECORDING)")
        print("=" * 60)
        print(f"Topic: {test['topic'].replace('_', ' ').title()}")
        print(f"Difficulty: {difficulty}")

        # Show progress tracker data
        if self.student_id != "default":
            profile = self.tracker.get_profile(self.student_id)
            if profile and profile.total_tests > 0:
                print(f" Returning | Tests: {profile.total_tests} | Best: {profile.best_band} | Level: {profile.level}")
                if profile.recommended_topics:
                    print(f" Try: {', '.join(profile.recommended_topics[:3])}")
        print("=" * 60)

        # ---- PART 1 ----
        print("\n PART 1: Introduction & Interview (4-5 min)")
        print(" Speak after each question")
        self._run_part1_with_recording(test['part1'], on_question, on_timer)
        if self.is_stopped:
            return self._get_full_results(test)

        # ---- PART 2 ----
        print("\n PART 2: Individual Long Turn (3-4 min)")
        print(" 1 min preparation + 2 min speaking")
        self._run_part2_with_recording(test['part2'], on_question, on_timer)
        if self.is_stopped:
            return self._get_full_results(test)

        # ---- PART 3 ----
        print("\n PART 3: Two-way Discussion (4-5 min)")
        print(" Speak after each question")
        self._run_part3_with_recording(test['part3'], on_question, on_timer)

        print("\n" + "=" * 60)
        print("TEST COMPLETE!")
        print("=" * 60)

        results = self._get_full_results(test)

        # Save to progress tracker
        session_id = self.tracker.save_session(self.student_id, results)
        print(f" Progress saved | Session: {session_id}")

        # Print detailed feedback report
        self.print_feedback_report(results)

        if on_complete:
            on_complete(results)

        return results

    def _get_full_results(self, test) -> Dict:
        scores = self._calculate_scores()
        return {
            'test': test,
            'responses': self.responses,
            'recordings': self.recording_history,
            'scores': scores,
            'difficulty': test.get('difficulty', 'medium'),
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'stopped_early': self.is_stopped
        }

    def _calculate_scores(self) -> Dict:
        if not self.recording_history:
            return {}

        g_scores, c_scores, p_scores = [], [], []
        for rec in self.recording_history:
            a = rec.get('analysis', {})
            if a.get('grammar') and a['grammar'].get('score'):
                g_scores.append(a['grammar']['score'])
            if a.get('coherence') and a['coherence'].get('score'):
                c_scores.append(a['coherence']['score'])
            if a.get('pronunciation') and a['pronunciation'].get('score'):
                p_scores.append(a['pronunciation']['score'])

        return {
            'grammar': round(sum(g_scores) / len(g_scores), 1) if g_scores else 0,
            'coherence': round(sum(c_scores) / len(c_scores), 1) if c_scores else 0,
            'pronunciation': round(sum(p_scores) / len(p_scores), 1) if p_scores else 0,
        }

    # ============================================================
    # FEEDBACK REPORT
    # ============================================================

    def generate_feedback_report(self, results: Dict) -> Dict:
        scores = results.get('scores', {})
        recordings = results.get('recordings', [])
        g, c, p = scores.get('grammar', 0), scores.get('coherence', 0), scores.get('pronunciation', 0)
        overall = round((g + c + p) / 3, 1) if all([g, c, p]) else 0

        return {
            'overview': {
                'overall_band': overall,
                'ielts_level': self._ielts_level(overall),
                'topic': results.get('test', {}).get('topic', 'unknown'),
                'difficulty': results.get('difficulty', 'medium'),
                'date': results.get('completed_at', '')
            },
            'scores': {
                'grammar': {
                    'band': g,
                    'descriptor': self._band_desc('grammar', g),
                    'feedback': self._skill_fb('grammar', g)
                },
                'coherence': {
                    'band': c,
                    'descriptor': self._band_desc('coherence', c),
                    'feedback': self._skill_fb('coherence', c)
                },
                'pronunciation': {
                    'band': p,
                    'descriptor': self._band_desc('pronunciation', p),
                    'feedback': self._skill_fb('pronunciation', p)
                },
            },
            'strengths': self._strengths(scores, recordings),
            'weaknesses': self._weaknesses(scores, recordings),
            'improvement_plan': self._plan(scores, overall),
            'question_feedback': self._q_feedback(recordings),
            'next_steps': self._next(overall),
        }

    def _ielts_level(self, band: float) -> str:
        levels = [
            (8.5, "Expert User"), (7.5, "Very Good User"),
            (6.5, "Competent User"), (5.5, "Modest User"), (4.5, "Limited User")
        ]
        for threshold, label in levels:
            if band >= threshold:
                return label
        return "Elementary User"

    def _band_desc(self, skill: str, band: float) -> str:
        descriptors = {
            'grammar': {
                9: "Full range of structures naturally. Consistently accurate.",
                8: "Wide range flexibly. Majority error-free.",
                7: "Range of complex structures. Frequent error-free sentences.",
                6: "Mix of simple/complex. Some mistakes with complex forms.",
                5: "Basic forms, some complex attempts. Frequent errors."
            },
            'coherence': {
                9: "Fluent, rare repetition. Fully coherent.",
                8: "Fluent, occasional repetition. Develops topics coherently.",
                7: "Speaks at length without effort. Range of connectives.",
                6: "Willing to speak at length. Limited discourse markers.",
                5: "Maintains flow with repetition. Overuses connectives."
            },
            'pronunciation': {
                9: "Full range with precision. Effortless to understand.",
                8: "Wide range. Easy to understand. L1 minimal effect.",
                7: "Range of features with mixed control.",
                6: "Range with mixed control. Generally understood.",
                5: "Limited range. Mispronunciations cause difficulty."
            }
        }
        sd = descriptors.get(skill, {})
        bands = sorted(sd.keys())
        closest = min(bands, key=lambda x: abs(x - band))
        return sd.get(closest, "")

    def _skill_fb(self, skill: str, band: float) -> str:
        feedback = {
            'grammar': [
                (7, 9, "Strong! Add inversion, cleft sentences, mixed conditionals."),
                (5.5, 7, "Adequate. Reduce article/preposition/tense errors. Use 'although', 'despite'."),
                (0, 5.5, "Focus on basic structures: subject-verb agreement, past vs present perfect.")
            ],
            'coherence': [
                (7, 9, "Excellent flow. Add 'having said that', 'be that as it may'."),
                (5.5, 7, "Good. Use more linkers: 'furthermore', 'consequently'. Record 2-min talks."),
                (0, 5.5, "Practice basic linkers: 'first', 'also', 'however'. Plan before speaking.")
            ],
            'pronunciation': [
                (7, 9, "Very clear. Fine-tune intonation, connected speech ('want to'→'wanna')."),
                (5.5, 7, "Clear. Focus on word stress, 'th' sounds. Shadow native speakers."),
                (0, 5.5, "Practice individual sounds, minimal pairs (ship/sheep). Use ELSA Speak app.")
            ]
        }
        for lo, hi, tip in feedback.get(skill, []):
            if lo <= band < hi:
                return tip
        return "Keep practicing regularly."

    def _strengths(self, scores: Dict, recordings: List) -> List[str]:
        s = []
        if scores.get('grammar', 0) >= 7:
            s.append("Strong grammatical control")
        if scores.get('coherence', 0) >= 7:
            s.append("Fluent speech with good discourse markers")
        if scores.get('pronunciation', 0) >= 7:
            s.append("Clear pronunciation with good intonation")

        words = [w for r in recordings for w in r.get('transcript', '').lower().split()]
        if len(words) > 100 and len(set(words)) / max(len(words), 1) > 0.55:
            s.append("Good lexical range with varied vocabulary")
        if len(words) > 200:
            s.append("Willing to speak at length and develop responses")

        return s or ["Shows willingness to communicate and engage with questions"]

    def _weaknesses(self, scores: Dict, recordings: List) -> List[str]:
        w = []
        if scores.get('grammar', 0) < 5.5:
            w.append("Grammatical accuracy needs significant improvement")
        elif scores.get('grammar', 0) < 6.5:
            w.append("Work on reducing errors in complex sentences")

        if scores.get('coherence', 0) < 5.5:
            w.append("Speech lacks smooth flow between ideas")
        elif scores.get('coherence', 0) < 6.5:
            w.append("Could use more varied discourse markers and linking phrases")

        if scores.get('pronunciation', 0) < 5.5:
            w.append("Pronunciation clarity needs focused practice")
        elif scores.get('pronunciation', 0) < 6.5:
            w.append("Work on word stress and intonation patterns")

        short = sum(1 for r in recordings if len(r.get('transcript', '').split()) < 10)
        if short > len(recordings) * 0.3:
            w.append("Tendency to give short answers - practice developing responses")

        return w or ["Continue polishing all skills for consistency"]

    def _plan(self, scores: Dict, overall: float) -> List[Dict]:
        skills = {
            'Grammar': scores.get('grammar', 0),
            'Coherence': scores.get('coherence', 0),
            'Pronunciation': scores.get('pronunciation', 0)
        }
        weakest = min(skills, key=skills.get)
        routines = {
            'Grammar': "Practice 10 complex sentences daily. Record yourself and check for errors.",
            'Coherence': "Record 2-minute talks on random topics. Plan: opinion→reason→example.",
            'Pronunciation': "Shadow BBC speakers 5 min daily. Practice problematic sounds with minimal pairs."
        }
        return [
            {
                'priority': 'HIGH',
                'focus': f'Improve {weakest}',
                'action': routines.get(weakest, "Practice regularly."),
                'target': f'Raise {weakest} from {skills[weakest]} to {min(9, skills[weakest]+0.5)}',
                'timeframe': '2-4 weeks of daily practice'
            },
            {
                'priority': 'MEDIUM',
                'focus': 'Vocabulary expansion',
                'action': 'Learn 5 new words/phrases daily related to common IELTS topics. Use them in sentences.',
                'target': 'Use new vocabulary naturally in responses',
                'timeframe': 'Continuous'
            },
        ]

    def _q_feedback(self, recordings: List) -> List[Dict]:
        fb = []
        for i, r in enumerate(recordings):
            wc = len(r.get('transcript', '').split())
            p = r.get('part', 1)
            dur = round(r.get('duration', 0), 1)

            if wc == 0:
                qual = "No speech detected"
            elif p == 1 and wc < 10:
                qual = "Too short for Part 1"
            elif p == 1 and wc < 25:
                qual = "Good length"
            elif p == 2 and wc < 50:
                qual = "Too short for Part 2"
            elif p == 2 and wc < 150:
                qual = "Adequate"
            elif p == 2:
                qual = "Excellent"
            elif p == 3 and wc < 15:
                qual = "Too short for Part 3"
            elif p == 3 and wc < 40:
                qual = "Good depth"
            else:
                qual = "Excellent"

            fb.append({
                'q': r.get('question_num', i + 1),
                'part': p,
                'words': wc,
                'duration': dur,
                'quality': qual,
                'tip': 'Add reasons/examples' if wc < 15 else 'Good response'
            })
        return fb

    def _next(self, overall: float) -> List[str]:
        if overall < 5:
            return [
                " Focus on basic English conversation before timed practice",
                " Practice 10 minutes of English speaking daily",
                " Build vocabulary with common IELTS topic word lists"
            ]
        elif overall < 6:
            return [
                " Practice timed speaking on familiar topics",
                " Record yourself and review for grammar errors",
                " Work on speaking for 1-2 minutes continuously"
            ]
        elif overall < 7:
            return [
                " Take 2-3 mock tests per week with different topics",
                " Focus on your weakest skill (see improvement plan)",
                " Practice with a speaking partner or language exchange"
            ]
        return [
            " You're at a strong level - focus on consistency",
            " Take weekly tests to track maintenance",
            " Practice advanced topics: abstract ideas, hypothetical situations"
        ]

    def print_feedback_report(self, results: Dict):
        report = self.generate_feedback_report(results)
        ov = report['overview']
        sc = report['scores']

        print("\n" + "=" * 60)
        print(" IELTS SPEAKING FEEDBACK REPORT")
        print("=" * 60)
        print(f"Overall Band: {ov['overall_band']} - {ov['ielts_level']}")
        print(f"Topic: {ov['topic']} | Difficulty: {ov['difficulty']}")
        print("=" * 60)

        emoji_map = {'grammar': '', 'coherence': '', 'pronunciation': ''}
        for skill, data in sc.items():
            emoji = emoji_map.get(skill, '•')
            print(f"\n {emoji} {skill.upper()}: Band {data['band']}")
            print(f" {data['descriptor'][:100]}")
            print(f" {data['feedback'][:120]}")

        print("\n STRENGTHS:")
        for s in report['strengths']:
            print(f" • {s}")

        print("\n AREAS TO IMPROVE:")
        for w in report['weaknesses']:
            print(f" • {w}")

        print("\n IMPROVEMENT PLAN:")
        for item in report['improvement_plan']:
            print(f" [{item['priority']}] {item['focus']}")
            print(f" → {item['action'][:120]}")

        print("\n PER-QUESTION FEEDBACK:")
        for qf in report['question_feedback'][:8]:
            print(f" Q{qf['q']} (P{qf['part']}): {qf['quality']} | {qf['words']} words | {qf['duration']}s")

        print("\n NEXT STEPS:")
        for step in report['next_steps']:
            print(f" {step}")

        print("\n" + "=" * 60)
        return report

    def save_results(self, results: Dict, filename: str = None) -> str:
        if filename is None:
            filename = os.path.join(
                self.output_dir,
                f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )

        clean = {
            'topic': results['test']['topic'],
            'difficulty': results['test']['difficulty'],
            'completed_at': results['completed_at'],
            'scores': results.get('scores', {}),
            'recordings': []
        }
        for rec in results.get('recordings', []):
            clean['recordings'].append({
                'part': rec['part'],
                'q': rec['question_num'],
                'duration': rec['duration'],
                'transcript': rec['transcript'],
                'file': rec['audio_path']
            })

        with open(filename, 'w') as f:
            json.dump(clean, f, indent=2)
        print(f"\n Results saved: {filename}")
        return filename

    def get_progress_report(self) -> Dict:
        return self.tracker.get_improvement_summary(self.student_id)

    def export_progress_report(self, filepath: str = None) -> str:
        return self.tracker.export_report(self.student_id, filepath)


# ============================================================
# TEST FUNCTION
# ============================================================

def test_recording():
    """Quick test of the recording functionality."""
    from .test_generator import create_speaking_test_generator
    from .api import create_ai_engine # optional

    # Create a minimal AI engine for the test (mock)
    class MockAI:
        def generate(self, prompt, **kwargs):
            return '{"part1": {"introduction": "Test", "topics": []}, "part2": {}, "part3": {}}'

    ai = MockAI()
    generator = create_speaking_test_generator(ai)

    mock = IntegratedSpeakingMockTest(
        test_generator=generator,
        student_id="test_user",
        preload_models=False
    )

    print("Testing microphone... Speak for a few seconds...")
    result = mock.record_response(1, part=1, max_duration=10)
    print(f"\nDuration: {result['duration']:.1f}s")
    print(f"Transcript: {result['transcript'][:200] if result['transcript'] else '(empty)'}")
    return result


if __name__ == "__main__":
    test_recording()