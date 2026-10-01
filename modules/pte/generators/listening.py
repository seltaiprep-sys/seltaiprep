# ============================================================
# modules/pte/generators/listening.py
# ============================================================
"""PTE Listening Generator – Pure AI + TTS (no static fallback).

 UPDATED (audio):
  • Uses centralized deepgram_service (auto Aura-2 aware)
  • Deepgram Aura-2 as PRIMARY (48kHz, studio quality)
  • Edge TTS as fallback (24kHz, 26 accents)
  • Aura-2 voice diversity (9 voices, mixed accents)
  • No more direct HTTP calls → no speed-param bugs
"""

import logging
import random
import os
import uuid
import asyncio
import json
import re
import time
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils.ai_generator import PTEAIGenerator

# Use centralized Deepgram service (handles Aura-2 voice + speed correctly)
try:
    from ..utils.deepgram_service import deepgram_service
    DEEPGRAM_SERVICE_AVAILABLE = bool(deepgram_service and deepgram_service.api_key)
except Exception:
    deepgram_service = None
    DEEPGRAM_SERVICE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ─── CONFIG ────────────────────────────────────────────────────────────
DEFAULT_DEEPGRAM_VOICE = 'aura-2-asteria-en'

# Aura-2 voice pool for Listening diversity (mixed accents)
DEEPGRAM_LISTENING_VOICES = [
    'aura-2-asteria-en', # US female
    'aura-2-orion-en', # US male
    'aura-2-luna-en', # US female
    'aura-2-arcas-en', # US male
    'aura-2-stella-en', # US female
    'aura-2-perseus-en', # US male
    'aura-2-athena-en', # UK female
    'aura-2-helios-en', # UK male
    'aura-2-angus-en', # Irish male
]

# ─── EDGE TTS ──────────────────────────────────────────────────────────
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
    logger.info(" Edge TTS available (fallback for Listening)")
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning(" Edge TTS not installed. Install with: pip install edge-tts")


class PTEListening:
    """
    Full PTE Listening test generator – AI content only.

    Realism features:
      • 16-question realistic distribution
      • Easy → Medium → Hard progression within the test
      • Unique topic per question (no repetition)
      • Difficulty-aware AI prompts
      • Parallel generation with retry
      • Aura-2 TTS voice diversity (9 voices)
    """

    PROTOCOL_VERSION = 3 #  bumped: Aura-2 + voice diversity

    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        self.audio_cache_dir = os.path.join(base_dir, 'static', 'audio_cache', 'pte')
        os.makedirs(self.audio_cache_dir, exist_ok=True)
        self.deepgram_service = deepgram_service if DEEPGRAM_SERVICE_AVAILABLE else None
        self.edge_available = EDGE_TTS_AVAILABLE

        if self.deepgram_service:
            logger.info(
                f" Deepgram Aura-2 TTS available as PRIMARY "
                f"({len(DEEPGRAM_LISTENING_VOICES)} voices)"
            )
        else:
            logger.warning(" DEEPGRAM_API_KEY not set; using Edge TTS only.")

        # ─── 26 NATURAL HUMAN-LIKE VOICES FOR EDGE TTS (fallback) ───
        self.voice_pool = [
            'en-US-JennyNeural', 'en-US-AriaNeural', 'en-US-SaraNeural',
            'en-US-ElizabethNeural', 'en-US-MichelleNeural',
            'en-US-GuyNeural', 'en-US-DavisNeural', 'en-US-TonyNeural',
            'en-US-BrianNeural', 'en-US-JasonNeural',
            'en-GB-SoniaNeural', 'en-GB-MiaNeural', 'en-GB-LibbyNeural',
            'en-GB-HollieNeural', 'en-GB-RyanNeural', 'en-GB-ThomasNeural',
            'en-GB-AlfieNeural', 'en-GB-ElliotNeural',
            'en-AU-NatashaNeural', 'en-AU-WilliamNeural',
            'en-IN-NeerjaNeural', 'en-IN-PrabhatNeural',
            'en-CA-ClaraNeural', 'en-CA-LiamNeural',
            'en-NZ-MollyNeural', 'en-NZ-MitchellNeural',
        ]

    # ══════════════════════════════════════════════════════════════════
    # OFFICIAL PTE 2026 LISTENING QUESTION ORDER (16 questions)
    # ══════════════════════════════════════════════════════════════════
    QUESTION_ORDER = [
        ('summarize_spoken_text', 2),
        ('multiple_choice_single', 2),
        ('multiple_choice_multiple', 1),
        ('fill_blanks', 2),
        ('highlight_correct_summary', 2),
        ('select_missing_word', 1),
        ('highlight_incorrect_words', 2),
        ('write_from_dictation', 4),
    ] # Total = 16

    DIFFICULTY_BANDS = [
        (0.00, 0.40, 'easy'),
        (0.40, 0.80, 'medium'),
        (0.80, 1.00, 'hard'),
    ]

    DIFFICULTY_DESCRIPTORS = {
        'easy': (
            "Use clear, straightforward academic English. Each sentence should express "
            "one main idea. Avoid unusual vocabulary and complex clause structures. "
            "Facts should be simple and directly stated."
        ),
        'medium': (
            "Use formal academic English suitable for university-level listeners. "
            "Include 1-2 specific facts, statistics, or research references. "
            "Some sentences may contain subordinate clauses, but main ideas stay clear."
        ),
        'hard': (
            "Use sophisticated academic English with nuanced argumentation. "
            "Include multiple specific data points, references to research, or subtle "
            "qualifications. Sentences may contain complex structures. For multiple-choice "
            "questions, make distractors very close to the correct answer so that only "
            "careful listening separates them."
        ),
    }

    ACADEMIC_TOPICS = [
        'quantum mechanics', 'astrophysics', 'thermodynamics',
        'classical mechanics', 'electromagnetism', 'condensed matter physics',
        'nuclear physics', 'plasma physics', 'particle physics',
        'stellar evolution', 'galactic astronomy', 'cosmology',
        'planetary science', 'orbital mechanics', 'astrobiology',
        'genetics', 'evolutionary biology', 'ecology',
        'cell biology', 'microbiology', 'zoology',
        'botany', 'molecular biology', 'neuroscience',
        'immunology', 'bioinformatics', 'embryology',
        'endocrinology', 'gastroenterology', 'hematology',
        'organic chemistry', 'inorganic chemistry', 'biochemistry',
        'physical chemistry', 'analytical chemistry', 'environmental chemistry',
        'polymer chemistry', 'pharmacology', 'toxicology',
        'electrochemistry', 'photochemistry', 'geochemistry',
        'geology', 'oceanography', 'meteorology',
        'seismology', 'volcanology', 'hydrology',
        'climatology', 'conservation biology', 'pollution control',
        'biodiversity', 'environmental policy', 'renewable energy systems',
        'paleontology', 'mineralogy',
        'ancient history', 'medieval history', 'modern history',
        'renaissance history', 'world wars history', 'colonial history',
        'political history', 'economic history', 'art history',
        'social history', 'cultural history', 'military history',
        'social stratification', 'cultural anthropology', 'criminology',
        'migration studies', 'urban sociology', 'family sociology',
        'gender studies', 'sociological theory', 'demography',
        'ethnography',
        'cognitive psychology', 'behavioral psychology', 'clinical psychology',
        'developmental psychology', 'social psychology', 'psychopathology',
        'neuropsychology', 'educational psychology', 'organizational psychology',
        'health psychology', 'sports psychology', 'forensic psychology',
        'microeconomics', 'macroeconomics', 'international trade',
        'public finance', 'labor economics', 'behavioral economics',
        'econometrics', 'development economics', 'environmental economics',
        'financial economics',
        'algebra', 'calculus', 'statistics',
        'topology', 'graph theory', 'cryptography',
        'machine learning', 'computer networking', 'databases',
        'algorithms', 'artificial intelligence', 'software engineering',
        'mechanical engineering', 'civil engineering', 'electrical engineering',
        'chemical engineering', 'aerospace engineering', 'robotics',
        'material science', 'biomedical engineering', 'structural engineering',
        'industrial engineering',
        'anatomy', 'physiology', 'pathology',
        'epidemiology', 'oncology', 'cardiology',
        'neurology', 'psychiatry', 'rheumatology',
        'nephrology', 'ophthalmology', 'orthopedics',
        'gerontology',
        'linguistics', 'logic', 'ethics',
        'aesthetics', 'comparative literature', 'critical theory',
        'philosophy of science',
        'marketing', 'finance', 'organizational behavior',
        'supply chain management', 'strategic management',
    ]

    GENERAL_TOPICS = [
        'technology and society', 'environmental conservation',
        'urban development', 'public health',
        'education reform', 'workplace culture',
        'social media', 'travel and tourism',
        'cultural diversity', 'sports and fitness',
        'family dynamics', 'financial literacy',
        'digital privacy', 'renewable energy',
        'community service', 'mental health',
        'globalization', 'food security',
        'housing crisis', 'public transport',
        'art and culture', 'space exploration',
        'telecommunication', 'political systems',
        'human rights', 'consumer behavior',
        'entertainment industry', 'ethical dilemmas',
    ]

    # ══════════════════════════════════════════════════════════════════
    # Topic helpers
    # ══════════════════════════════════════════════════════════════════
    def _pick_topic(self, used_topics: set) -> str:
        pool = self.ACADEMIC_TOPICS + self.GENERAL_TOPICS
        available = [t for t in pool if t not in used_topics]
        if not available:
            used_topics.clear()
            available = pool
        topic = random.choice(available)
        used_topics.add(topic)
        return topic

    def _get_random_voice(self) -> str:
        return random.choice(self.voice_pool)

    def _get_random_deepgram_voice(self) -> str:
        return random.choice(DEEPGRAM_LISTENING_VOICES)

    @staticmethod
    def _build_difficulty_schedule(total: int) -> List[str]:
        schedule = []
        for i in range(total):
            frac = (i + 0.5) / total
            level = 'medium'
            for lo, hi, lvl in PTEListening.DIFFICULTY_BANDS:
                if lo <= frac < hi:
                    level = lvl
                    break
            schedule.append(level)
        return schedule

    def _difficulty_hint(self, level: str) -> str:
        return self.DIFFICULTY_DESCRIPTORS.get(level, self.DIFFICULTY_DESCRIPTORS['medium'])

    def _clean_text_for_tts(self, text: str) -> str:
        if not text:
            return text
        text = re.sub(r'[\x00-\x1f\x7f]', '', text)
        text = re.sub(r'[^\x00-\x7F]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _enhance_text_for_tts(self, text: str) -> str:
        if not text:
            return ''
        return self._clean_text_for_tts(text)

    # ═══════════════════════════════════════════════════════════════════
    # DEEPGRAM AURA-2 (PRIMARY) — uses centralized service
    # ═══════════════════════════════════════════════════════════════════
    def _generate_audio_deepgram(self, text: str, prefix: str = "audio") -> Optional[str]:
        """
        Generate audio via centralized DeepgramService.
        Uses Aura-2 voices with random diversity.
        Falls back to None on any failure (caller will try Edge).
        """
        if not text or not self.deepgram_service:
            return None
        try:
            plain_text = self._enhance_text_for_tts(text)
            voice = self._get_random_deepgram_voice()
            filename = f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(self.audio_cache_dir, filename)

            # Call centralized service — handles speed + Aura-2 correctly
            audio_buffer = self.deepgram_service.text_to_speech(
                text=plain_text,
                voice=voice,
                speed=1.0,
                output_format='mp3',
            )
            if not audio_buffer:
                return None

            # Write to disk
            with open(filepath, 'wb') as f:
                f.write(audio_buffer.read())

            size = os.path.getsize(filepath)
            if size > 5000:
                logger.info(
                    f" Audio via Deepgram Aura-2: {filename} "
                    f"({size // 1024} KB, voice={voice})"
                )
                return f"/static/audio_cache/pte/{filename}"

            logger.warning(f"Deepgram audio too small: {size} bytes")
            try: os.remove(filepath)
            except: pass
            return None

        except Exception as e:
            logger.error(f"Deepgram TTS error for {prefix}: {e}")
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except: pass
            return None

    # ═══════════════════════════════════════════════════════════════════
    # EDGE TTS (FALLBACK)
    # ═══════════════════════════════════════════════════════════════════
    def _generate_audio_edge_tts(self, text: str, prefix: str = "audio") -> Optional[str]:
        if not text or not self.edge_available:
            return None
        try:
            plain_text = self._enhance_text_for_tts(text)
            voice = self._get_random_voice()
            filename = f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(self.audio_cache_dir, filename)

            async def generate():
                communicate = edge_tts.Communicate(plain_text, voice)
                await communicate.save(filepath)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(generate())
            finally:
                loop.close()

            size = os.path.getsize(filepath)
            if size > 5000:
                logger.info(
                    f" Audio via Edge TTS (fallback): {filename} "
                    f"({len(plain_text.split())} words) [Voice: {voice}]"
                )
                return f"/static/audio_cache/pte/{filename}"

            logger.warning(f"Edge TTS audio too small: {size} bytes")
            try: os.remove(filepath)
            except: pass
            return None

        except Exception as e:
            logger.error(f"Edge TTS error: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # PRIMARY → FALLBACK ORDER (Deepgram Aura-2 first, then Edge)
    # ═══════════════════════════════════════════════════════════════════
    def _generate_audio_with_fallback(self, text: str, prefix: str = "audio") -> Optional[str]:
        # Deepgram Aura-2 PRIMARY
        if self.deepgram_service:
            url = self._generate_audio_deepgram(text, prefix)
            if url:
                return url
            logger.warning(f"Deepgram failed for {prefix}, trying Edge TTS...")

        # Edge FALLBACK
        if self.edge_available:
            url = self._generate_audio_edge_tts(text, prefix)
            if url:
                return url

        logger.error(f"All TTS providers failed for {prefix}")
        return None

    def _get_text_for_audio(self, question: Dict) -> Optional[str]:
        prompt = question.get('prompt', {})
        text = (prompt.get('passage') or prompt.get('sentence') or
                prompt.get('text') or prompt.get('script') or question.get('text'))
        if not text:
            text = prompt.get('question') or question.get('question')

        if text and isinstance(text, str):
            text = text.strip()
            qtype = question.get('type', '')

            if qtype == 'fill_blanks':
                correct_words = (question.get('correct_answer') or
                                 prompt.get('blanks') or prompt.get('correct'))
                if correct_words and isinstance(correct_words, list) and len(correct_words) > 0:
                    for word in correct_words:
                        if isinstance(word, str):
                            text = text.replace('[BLANK]', word, 1)
                else:
                    text = text.replace('[BLANK]', ' ')

            if qtype == 'select_missing_word':
                correct_idx = question.get('correct_answer')
                options = prompt.get('options')
                correct_word = None

                if (correct_idx is not None and options and
                    isinstance(correct_idx, int) and correct_idx < len(options)):
                    correct_word = options[correct_idx]
                elif (correct_idx is not None and options and
                      isinstance(correct_idx, str) and correct_idx in options):
                    correct_word = correct_idx

                if correct_word:
                    text = re.sub(r'_{4,}', correct_word, text)
                    text = text.replace('[BLANK]', correct_word)
                else:
                    text = re.sub(r'_{4,}', ' ', text)
                    text = text.replace('[BLANK]', ' ')

            if qtype == 'highlight_incorrect_words':
                text = re.sub(r'\[\s*INCORRECT\s*\]', '', text, flags=re.IGNORECASE)
                text = re.sub(r'\s+', ' ', text).strip()

            MAX_TTS_CHARS = 1800
            if len(text) > MAX_TTS_CHARS:
                cut_at = text.rfind(' ', 0, MAX_TTS_CHARS)
                if cut_at == -1:
                    cut_at = MAX_TTS_CHARS
                text = text[:cut_at] + "..."

            if len(text) > 10:
                return text
        return None

    def _attach_audio_to_questions(self, questions: List[Dict]) -> None:
        def generate_for_question(q):
            audio_url = q.get('audio_url')
            if audio_url:
                if audio_url.startswith('/'):
                    rel_path = audio_url[1:]
                else:
                    rel_path = audio_url
                filepath = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                    rel_path
                )
                if os.path.exists(filepath):
                    return q

            text = self._get_text_for_audio(q)
            if not text:
                q['audio_url'] = None
                return q

            audio_url = self._generate_audio_with_fallback(
                text, prefix=f"listening_{q.get('id', 'q')}"
            )
            q['audio_url'] = audio_url
            q['voice_used'] = (
                'deepgram_aura2' if audio_url and self.deepgram_service
                else 'edge_tts' if audio_url
                else None
            )
            return q

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(generate_for_question, q) for q in questions]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Parallel audio generation error: {e}")

    def _ensure_audio_for_question(self, q: Dict) -> Dict:
        audio_url = q.get('audio_url')
        if audio_url:
            if audio_url.startswith('/'):
                rel_path = audio_url[1:]
            else:
                rel_path = audio_url
            filepath = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                rel_path
            )
            if os.path.exists(filepath):
                return q

        text = self._get_text_for_audio(q)
        if not text:
            q['audio_url'] = None
            return q

        prefix = f"listening_{q.get('id', 'q')}"
        audio_url = self._generate_audio_with_fallback(text, prefix=prefix)
        q['audio_url'] = audio_url
        if audio_url:
            q['voice_used'] = 'deepgram_aura2' if self.deepgram_service else 'edge_tts'
            logger.info(f" Regenerated audio for Q{q.get('id')}: {audio_url}")
        else:
            q['voice_used'] = None
            logger.warning(f" Failed to regenerate audio for Q{q.get('id')}")
        return q

    def regenerate_audio_for_question(self, question_id: str, question_data: Dict) -> Optional[str]:
        text = self._get_text_for_audio(question_data)
        if not text:
            logger.error(f" No text found for question {question_id}")
            return None

        prefix = f"listening_{question_id}"
        audio_url = self._generate_audio_with_fallback(text, prefix=prefix)
        if audio_url:
            logger.info(f" Regenerated audio for question {question_id}: {audio_url}")
            return audio_url
        logger.error(f" Failed to regenerate audio for question {question_id}")
        return None

    # ══════════════════════════════════════════════════════════════════
    # QUESTION GENERATION
    # ══════════════════════════════════════════════════════════════════
    def _generate_all_questions_parallel(self, difficulty: str) -> List[Dict]:
        task_list: List[tuple] = []
        for qtype, count in self.QUESTION_ORDER:
            section = 1 if qtype == 'summarize_spoken_text' else 2
            for i in range(count):
                task_list.append((qtype, section, i + 1))

        total = len(task_list)
        difficulty_schedule = self._build_difficulty_schedule(total)

        logger.info(f" Generating {total} listening questions with difficulty progression...")

        used_topics: set = set()
        used_signatures: set = set()

        def generate_one(task_with_level):
            (qtype, section, idx), level = task_with_level
            for retry in range(3):
                topic = self._pick_topic(used_topics)
                q = self._generate_single_question(topic, qtype, level, section, idx)
                if q:
                    sig = self._content_signature(q)
                    if sig and sig in used_signatures:
                        logger.warning(f" Duplicate content for {qtype} — retrying")
                        continue
                    if sig:
                        used_signatures.add(sig)
                    q['topic'] = topic
                    q['difficulty_level'] = level
                    return q
                logger.warning(f"Retry {retry+1}/3 for {qtype} (item {idx})")
                time.sleep(0.3)
            logger.error(f"Failed {qtype} (item {idx}) after 3 retries")
            return None

        paired_tasks = list(zip(task_list, difficulty_schedule))

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(generate_one, t) for t in paired_tasks]
            for future in as_completed(futures):
                try:
                    q = future.result()
                    if q:
                        results.append(q)
                except Exception as e:
                    logger.error(f"Parallel question generation error: {e}")

        return self._reorder_questions(results)

    @staticmethod
    def _content_signature(q: Dict) -> Optional[str]:
        try:
            prompt = q.get('prompt') or {}
            if not isinstance(prompt, dict):
                return None
            text = (
                prompt.get('text') or prompt.get('passage') or
                prompt.get('sentence') or prompt.get('script') or ''
            )
            if not isinstance(text, str):
                return None
            return str(hash(text.strip()[:150].lower()))
        except Exception:
            return None

    def _debug_log_questions(self, questions: List[Dict]) -> None:
        logger.info("=" * 70)
        logger.info(f" Generated {len(questions)} Listening Questions")
        logger.info("=" * 70)
        for idx, q in enumerate(questions, 1):
            q_type = q.get('type', 'unknown')
            q_id = q.get('id', 'N/A')
            level = q.get('difficulty_level', '-')
            topic = q.get('topic', '-')
            prompt = q.get('prompt', {})
            script = (prompt.get('passage') or prompt.get('text') or
                      prompt.get('sentence') or prompt.get('script') or '')
            word_count = len(script.split())
            if len(script) > 300:
                script = script[:300] + "... [TRUNCATED]"
            options = prompt.get('options') or prompt.get('summaries') or []
            correct = q.get('correct_answer')
            if correct is None:
                correct = prompt.get('correct')
            correct_display = str(correct) if correct is not None else ''
            audio = q.get('audio_url', 'None')
            voice_used = q.get('voice_used', 'N/A')
            logger.info(
                f"Q{idx:2d} [{q_type:25s}] ID: {q_id} | LVL: {level} | TOPIC: {topic}\n"
                f" SCRIPT ({word_count}w): {script}\n"
                f" Options: {options if options else 'N/A'}\n"
                f" Correct: {correct_display}\n"
                f" Audio: {audio}\n"
                f" Voice: {voice_used}\n"
                f" ─────────────────────────────────────────────────────────────"
            )
        logger.info("=" * 70)

    def generate_full_test(self, difficulty: str = "medium", generate_audio: bool = True) -> Dict[str, Any]:
        try:
            questions = self._generate_all_questions_parallel(difficulty)
            if not questions:
                return self._error_response(difficulty, 'No questions generated.')

            questions = self._ensure_options_and_answers(questions, difficulty)
            questions = self._reorder_questions(questions)

            if generate_audio:
                self._attach_audio_to_questions(questions)
                for i, q in enumerate(questions):
                    questions[i] = self._ensure_audio_for_question(q)

            from collections import Counter
            type_counts = Counter(q.get('type', 'unknown') for q in questions)
            level_counts = Counter(q.get('difficulty_level', 'unknown') for q in questions)
            logger.info(
                f" PTE Listening generated: {len(questions)} questions "
                f"types={dict(type_counts)} levels={dict(level_counts)}"
            )

            result = {
                'success': True,
                'questions': questions,
                'total_questions': len(questions),
                'duration': '30-40 minutes',
                'difficulty': difficulty,
                'year': 2026,
                'audio_generated': sum(1 for q in questions if q.get('audio_url')),
                'official_pattern': True,
                'official_version': 'PTE Academic 2026',
                'test_type': 'pte_listening',
                'protocol_version': self.PROTOCOL_VERSION,
                'difficulty_progression': True,
                'tts_primary': 'deepgram_aura2' if self.deepgram_service else 'edge_tts',
            }
            self._debug_log_questions(questions)
            return result
        except Exception as e:
            logger.exception(f"Listening test generation failed: {e}")
            return self._error_response(difficulty, str(e))

    def _error_response(self, difficulty: str, msg: str) -> Dict[str, Any]:
        return {
            'success': False,
            'error': msg,
            'questions': [],
            'total_questions': 0,
            'duration': 'N/A',
            'difficulty': difficulty,
            'year': 2026,
            'audio_generated': 0,
            'test_type': 'pte_listening',
            'protocol_version': self.PROTOCOL_VERSION,
        }

    def _ensure_options_and_answers(self, questions: List[Dict], difficulty: str) -> List[Dict]:
        for idx, q in enumerate(questions):
            qtype = q.get('type')
            if qtype in ['multiple_choice_single', 'multiple_choice_multiple']:
                prompt = q.get('prompt', {})
                options = prompt.get('options')
                if not options or len(options) < 4:
                    logger.warning(f"Q {q.get('id')} ({qtype}) missing options. Regenerating...")
                    level = q.get('difficulty_level', difficulty)
                    topic = q.get('topic', self._pick_topic(set()))
                    new_q = self._generate_single_question(
                        topic, qtype, level, q.get('section_num', 1), idx + 1
                    )
                    if new_q:
                        if 'audio_url' in q:
                            new_q['audio_url'] = q['audio_url']
                        if 'voice_used' in q:
                            new_q['voice_used'] = q['voice_used']
                        if 'topic' in q:
                            new_q['topic'] = q['topic']
                        questions[idx] = new_q
                        logger.info(f" Regenerated Q {new_q.get('id')}")
        return questions

    def _reorder_questions(self, questions: List[Dict]) -> List[Dict]:
        priority = {}
        for idx, (qtype, _) in enumerate(self.QUESTION_ORDER):
            priority[qtype] = idx
        for q in questions:
            if 'type' not in q:
                q['type'] = 'unknown'
        questions.sort(key=lambda q: priority.get(q.get('type'), 999))
        return questions

    def _generate_single_question(
        self,
        topic: str,
        qtype: str,
        level: str,
        section: int,
        index: int,
    ) -> Optional[Dict]:
        try:
            if qtype == 'summarize_spoken_text':
                return self._gen_summarize_spoken_text(topic, level)
            elif qtype == 'multiple_choice_single':
                return self._gen_multiple_choice(topic, level, multiple=False)
            elif qtype == 'multiple_choice_multiple':
                return self._gen_multiple_choice(topic, level, multiple=True)
            elif qtype == 'fill_blanks':
                return self._gen_fill_blanks(topic, level)
            elif qtype == 'highlight_correct_summary':
                return self._gen_highlight_summary(topic, level)
            elif qtype == 'select_missing_word':
                return self._gen_select_missing_word(topic, level)
            elif qtype == 'highlight_incorrect_words':
                return self._gen_highlight_incorrect_words(topic, level)
            elif qtype == 'write_from_dictation':
                return self._gen_write_from_dictation(topic, level)
            return None
        except Exception as e:
            logger.error(f"Error generating {qtype}: {e}")
            return None

    def _ai_generate(self, prompt: str) -> Optional[str]:
        try:
            return PTEAIGenerator._ai_generate(prompt, max_tokens=600, temperature=0.7)
        except AttributeError:
            logger.error("PTEAIGenerator._ai_generate not found — check ai_generator.py")
            return None
        except Exception as e:
            logger.warning(f"AI generation failed: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════
    # Individual question generators (unchanged logic)
    # ══════════════════════════════════════════════════════════════════
    def _gen_summarize_spoken_text(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt = f"""Write a 90-95 second academic lecture script (about 220-240 words) about "{topic}" for PTE Summarize Spoken Text.

DIFFICULTY GUIDANCE:
{hint}

Include 4-5 main points, natural spoken language, and a clear conclusion.
Use academic vocabulary suitable for IELTS Band 7-9.
Return ONLY the lecture text (no JSON, no headings, no markers).
The script must be at least 220 words to ensure sufficient audio duration."""
        script = self._ai_generate(prompt)

        if not script or len(script.split()) < 200:
            logger.error(f"AI failed to generate sufficient script for '{topic}'")
            return None

        return {
            "id": f"summarize_spoken_text_{random.randint(1000,9999)}",
            "type": "summarize_spoken_text",
            "type_name": "Summarize Spoken Text",
            "prompt": {
                "instruction": "Listen to the lecture and summarize the main points in 50-70 words.",
                "passage": script,
                "script": script,
            },
            "correct_answer": "Summary not verified",
            "section_num": 1,
            "difficulty": level,
            "year": 2026,
        }

    def _gen_multiple_choice(self, topic: str, level: str, multiple: bool = False) -> Optional[Dict]:
        qtype = "multiple_choice_multiple" if multiple else "multiple_choice_single"
        hint = self._difficulty_hint(level)

        num_options = 4 if level == 'easy' else random.choice([4, 5])
        letters = ['A', 'B', 'C', 'D', 'E'][:num_options]

        correct_rule = (
            'Exactly 2 or 3 correct answers. Provide their 0-based indices in "correct".'
            if multiple else
            'Exactly 1 correct answer. Provide its 0-based index in "correct" as a single-element array.'
        )

        prompt = f"""Generate a short passage (45-60 words) about "{topic}" for PTE Listening {qtype}.

DIFFICULTY GUIDANCE:
{hint}

CRITICAL INSTRUCTIONS:
1. Create a passage about the topic.
2. Create a question about the passage.
3. Provide EXACTLY {num_options} meaningful, distinct options ({', '.join(letters)}).
4. The options must be REAL content, NOT "Option A", "Option B", etc.
5. {correct_rule}

Return ONLY valid JSON:
{{"passage": "...", "question": "...", "options": [{', '.join([f'"..."' for _ in letters])}], "correct": [0]}}"""

        result = self._ai_generate(prompt)
        if not result:
            logger.error(f"AI failed for {qtype} on topic {topic}")
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None

            data = json.loads(result[start:end])
            passage = data.get('passage', '')
            options = data.get('options', [])

            if not passage or len(passage.split()) < 25:
                return None
            if not options or len(options) < 4:
                return None

            generic_exact = {'option a', 'option b', 'option c', 'option d',
                             'option 1', 'option 2', 'option 3', 'option 4'}
            is_generic = all(
                str(opt).strip().lower() in generic_exact
                for opt in options
            )
            if is_generic:
                logger.warning("MCQ options were generic placeholders")
                return None

            options = options[:num_options]

            correct = data.get('correct')
            if not correct:
                correct = [0] if not multiple else [0, 1]
            elif not isinstance(correct, list):
                correct = [correct]

            try:
                correct = [int(c) for c in correct]
            except (TypeError, ValueError):
                return None

            if any(c < 0 or c >= len(options) for c in correct):
                return None

            correct = sorted(set(correct))

            if multiple:
                if len(correct) < 2 or len(correct) > 3:
                    return None
            else:
                if len(correct) != 1:
                    correct = [correct[0]]

            return {
                "id": f"{qtype}_{random.randint(1000,9999)}",
                "type": qtype,
                "type_name": "Multiple Choice (Multiple)" if multiple else "Multiple Choice (Single)",
                "prompt": {
                    "instruction": f"Listen and choose {'all' if multiple else 'the'} correct answer(s).",
                    "passage": passage,
                    "question": data.get('question', 'What is the main idea?'),
                    "options": options,
                    "correct": correct,
                },
                "correct_answer": correct,
                "section_num": 2,
                "difficulty": level,
                "year": 2026,
            }
        except Exception as e:
            logger.error(f"Error parsing AI response for {qtype}: {e}")
            return None

    def _gen_fill_blanks(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        num_blanks = random.choice([4, 5])

        prompt = f"""Write a passage (55-70 words) about "{topic}" for PTE Listening Fill in the Blanks.

DIFFICULTY GUIDANCE:
{hint}

Include {num_blanks} missing words marked as [BLANK].
Provide a list of 4 options for each blank (only ONE is the correct word from the passage).

Return ONLY valid JSON:
{{"text": "... [BLANK] ...", "blanks": ["word1", "word2"], "options": [["opt1","opt2","opt3","opt4"], ...]}}"""

        result = self._ai_generate(prompt)
        if not result:
            logger.error(f"AI failed for Fill in the Blanks on {topic}")
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            data = json.loads(result[start:end])

            text = data.get("text", "")
            blanks_list = data.get("blanks", [])
            options_list = data.get("options", [])

            if not text or not blanks_list:
                return None
            if not options_list or len(options_list) < len(blanks_list):
                logger.warning(f"Invalid options for Fill in the Blanks on {topic}")
                return None

            blank_markers = text.count('[BLANK]')
            if blank_markers != len(blanks_list):
                logger.warning(f"[BLANK] count ({blank_markers}) != blanks length ({len(blanks_list)}) — skipping")
                return None

            for i, correct_word in enumerate(blanks_list):
                if i >= len(options_list):
                    return None
                if correct_word not in options_list[i]:
                    logger.warning(f"Blank {i} correct word missing from its options")
                    return None

            return {
                "id": f"fill_blanks_{random.randint(1000,9999)}",
                "type": "fill_blanks",
                "type_name": "Fill in the Blanks",
                "prompt": {
                    "instruction": "Listen and fill in the missing words.",
                    "text": text,
                    "blanks": blanks_list,
                    "options": options_list,
                    "correct": blanks_list,
                },
                "correct_answer": blanks_list,
                "section_num": 2,
                "difficulty": level,
                "year": 2026,
            }
        except Exception as e:
            logger.error(f"Error parsing Fill in the Blanks: {e}")
            return None

    def _gen_highlight_summary(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt = f"""Write a short passage (55-70 words) about "{topic}" for PTE Highlight Correct Summary.

DIFFICULTY GUIDANCE:
{hint}

Then generate 4 summaries (A, B, C, D) where only one is correct.
The incorrect summaries must be plausible but misrepresent the passage.

Return ONLY valid JSON:
{{"passage": "...", "summaries": ["A", "B", "C", "D"], "correct": 0}}"""

        result = self._ai_generate(prompt)
        if not result:
            logger.error(f"AI failed for Highlight Correct Summary on {topic}")
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            data = json.loads(result[start:end])
            passage = data.get("passage", "")
            summaries = data.get("summaries", [])
            correct = data.get("correct", 0)

            if not passage or len(summaries) < 4:
                return None

            try:
                correct = int(correct)
            except (TypeError, ValueError):
                correct = 0
            if correct < 0 or correct >= len(summaries):
                correct = 0

            return {
                "id": f"highlight_correct_summary_{random.randint(1000,9999)}",
                "type": "highlight_correct_summary",
                "type_name": "Highlight Correct Summary",
                "prompt": {
                    "instruction": "Listen and select the correct summary.",
                    "passage": passage,
                    "question": "Which summary is correct?",
                    "summaries": summaries[:4],
                    "correct": correct,
                },
                "correct_answer": correct,
                "section_num": 2,
                "difficulty": level,
                "year": 2026,
            }
        except Exception as e:
            logger.error(f"Error parsing Highlight Correct Summary: {e}")
            return None

    def _gen_select_missing_word(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt = f"""Write a short passage (45-55 words) about "{topic}" for PTE Select Missing Word.

DIFFICULTY GUIDANCE:
{hint}

The last word (or phrase) should be missing (replace with ________).
Provide 4 candidate options where only one completes the sentence correctly.

Return ONLY valid JSON:
{{"passage": "... ________", "options": ["word1", "word2", "word3", "word4"], "correct": 0}}"""

        result = self._ai_generate(prompt)
        if not result:
            logger.error(f"AI failed for Select Missing Word on {topic}")
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            data = json.loads(result[start:end])
            passage = data.get("passage", "")
            options = data.get("options", [])
            correct = data.get("correct", 0)

            if not passage or len(options) < 4:
                return None

            try:
                correct = int(correct)
            except (TypeError, ValueError):
                correct = 0
            if correct < 0 or correct >= len(options):
                correct = 0

            return {
                "id": f"select_missing_word_{random.randint(1000,9999)}",
                "type": "select_missing_word",
                "type_name": "Select Missing Word",
                "prompt": {
                    "instruction": "Listen and select the missing word.",
                    "passage": passage,
                    "options": options[:4],
                    "correct": correct,
                },
                "correct_answer": correct,
                "section_num": 2,
                "difficulty": level,
                "year": 2026,
            }
        except Exception as e:
            logger.error(f"Error parsing Select Missing Word: {e}")
            return None

    def _gen_highlight_incorrect_words(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt = f"""Generate a passage (55-70 words) about "{topic}" for PTE Highlight Incorrect Words.

DIFFICULTY GUIDANCE:
{hint}

Include 3-5 words that are incorrect. Mark them with [INCORRECT] IMMEDIATELY AFTER the word.

Return ONLY valid JSON:
{{"text": "... word [INCORRECT] ...", "incorrect": ["word1", "word2"], "correct_words": ["word1_original"]}}"""

        result = self._ai_generate(prompt)
        if not result:
            logger.error(f"AI failed for Highlight Incorrect Words on {topic}")
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            data = json.loads(result[start:end])

            raw_text = data.get("text", "")
            incorrect = data.get("incorrect", [])

            cleaned_text = re.sub(r' ?\[INCORRECT\]', '', raw_text, flags=re.IGNORECASE)
            cleaned_text = re.sub(r'\[([^\]]+)\]', r'\1', cleaned_text)
            cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

            if not cleaned_text or not incorrect:
                return None
            if not isinstance(incorrect, list) or len(incorrect) < 2:
                return None

            return {
                "id": f"highlight_incorrect_words_{random.randint(1000,9999)}",
                "type": "highlight_incorrect_words",
                "type_name": "Highlight Incorrect Words",
                "prompt": {
                    "instruction": "Listen and select the words that are incorrect.",
                    "text": cleaned_text,
                    "incorrect_words": incorrect,
                    "correct_words": data.get("correct_words", []),
                },
                "correct_answer": incorrect,
                "section_num": 2,
                "difficulty": level,
                "year": 2026,
            }
        except Exception as e:
            logger.error(f"Error parsing Highlight Incorrect Words: {e}")
            return None

    def _gen_write_from_dictation(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt = f"""Generate a 10-15 word sentence about "{topic}" for PTE Write from Dictation.

DIFFICULTY GUIDANCE:
{hint}

Use academic language. Return ONLY the sentence (no JSON, no markers, no quotes)."""

        sentence = self._ai_generate(prompt)
        if not sentence:
            logger.error(f"AI failed for Write from Dictation on {topic}")
            return None

        sentence = sentence.strip().strip('"').strip("'").strip()
        if sentence.endswith('...'):
            sentence = sentence[:-3].strip()

        word_count = len(sentence.split())
        if word_count < 8 or word_count > 20:
            logger.warning(f"WFD sentence length out of range: {word_count} words")
            return None

        return {
            "id": f"write_from_dictation_{random.randint(1000,9999)}",
            "type": "write_from_dictation",
            "type_name": "Write from Dictation",
            "prompt": {
                "instruction": "Type exactly what you hear.",
                "sentence": sentence,
                "correct_answer": sentence,
            },
            "correct_answer": sentence,
            "section_num": 2,
            "difficulty": level,
            "year": 2026,
        }


# ─── SINGLETON ──────────────────────────────────────────────────────
pte_listening = PTEListening()