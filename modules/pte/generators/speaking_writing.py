# modules/pte/generators/speaking_writing.py
"""PTE Speaking & Writing Generator – Pure AI, No Static Fallback."""

import logging
import random
import json
import time
import os
import uuid
import hashlib
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils.ai_generator import PTEAIGenerator
from ..utils.image_generator import pte_image_generator, MATPLOTLIB_AVAILABLE

try:
    from modules.audio.unified_service import audio_service
    AUDIO_SERVICE_AVAILABLE = True
    logging.getLogger(__name__).info(" Audio service available for PTE Speaking")
except ImportError:
    AUDIO_SERVICE_AVAILABLE = False
    audio_service = None
    logging.getLogger(__name__).warning(" Audio service not available")

logger = logging.getLogger(__name__)


class PTESpeakingWriting:
    """
    PTE S&W generator – AI only, no static fallback.

    Realism features:
      • Official 2026 PTE distribution (~38 questions)
      • Easy → Medium → Hard progression within the test
      • Unique topic per question (no repetition)
      • Difficulty-aware AI prompts
      • Parallel generation with retry + dedup
      • Deepgram Aura voice diversity (via audio_service)
    """

    PROTOCOL_VERSION = 4 #  bumped: voice diversity added

    # ─── OFFICIAL PTE 2026 S&W QUESTION ORDER ─────────────────────────
    QUESTION_ORDER = [
        ('read_aloud', 6), # official: 6-7
        ('repeat_sentence', 10), # official: 10-12
        ('describe_image', 6), # official: 6-7
        ('re_tell_lecture', 3), # official: 3-4
        ('answer_short_question', 10), # official: 10-12
        ('summarize_written_text', 2), # official: 2-3
        ('write_essay', 1), # official: 1-2
    ] # Total = 38

    # ══════════════════════════════════════════════════════════════════
    # DIFFICULTY PROGRESSION
    # ══════════════════════════════════════════════════════════════════
    DIFFICULTY_BANDS = [
        (0.00, 0.40, 'easy'),
        (0.40, 0.80, 'medium'),
        (0.80, 1.00, 'hard'),
    ]

    DIFFICULTY_DESCRIPTORS = {
        'easy': (
            "Use clear, straightforward academic English. Each sentence expresses "
            "one main idea. Avoid unusual vocabulary and complex clause structures. "
            "Facts are simple and directly stated."
        ),
        'medium': (
            "Use formal academic English suitable for university-level readers. "
            "Include 1-2 specific facts or examples. Some sentences may contain "
            "subordinate clauses, but main ideas remain clear."
        ),
        'hard': (
            "Use sophisticated academic English with nuanced argumentation. "
            "Include specific data points, references to research, or subtle "
            "qualifications. Sentences may contain complex structures "
            "(subordination, coordination, hedging)."
        ),
    }

    # ══════════════════════════════════════════════════════════════════
    # LEVEL-AWARE LENGTH LIMITS (word counts)
    # ══════════════════════════════════════════════════════════════════
    LENGTH_LIMITS = {
        'read_aloud': {'easy': (22, 38), 'medium': (32, 48), 'hard': (42, 60)},
        'repeat_sentence': {'easy': (8, 14), 'medium': (12, 17), 'hard': (15, 22)},
        're_tell_lecture': {'easy': (55, 85), 'medium': (75, 105), 'hard': (95, 130)},
        'summarize_written_text': {'easy': (70, 105), 'medium': (80, 110), 'hard': (95, 140)},
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
        'organic chemistry', 'inorganic chemistry', 'biochemistry',
        'physical chemistry', 'analytical chemistry', 'polymer chemistry',
        'pharmacology', 'toxicology', 'geochemistry',
        'geology', 'oceanography', 'meteorology',
        'seismology', 'volcanology', 'hydrology',
        'climatology', 'conservation biology', 'pollution control',
        'biodiversity', 'environmental policy', 'renewable energy systems',
        'paleontology', 'mineralogy',
        'ancient history', 'medieval history', 'modern history',
        'renaissance history', 'political history', 'economic history',
        'art history', 'social history', 'cultural history',
        'social stratification', 'cultural anthropology', 'criminology',
        'migration studies', 'urban sociology', 'family sociology',
        'gender studies', 'sociological theory', 'demography',
        'cognitive psychology', 'behavioral psychology', 'clinical psychology',
        'developmental psychology', 'social psychology', 'neuropsychology',
        'educational psychology', 'organizational psychology',
        'microeconomics', 'macroeconomics', 'international trade',
        'public finance', 'labor economics', 'behavioral economics',
        'econometrics', 'development economics', 'environmental economics',
        'algebra', 'calculus', 'statistics',
        'topology', 'graph theory', 'cryptography',
        'machine learning', 'computer networking', 'algorithms',
        'artificial intelligence', 'software engineering',
        'mechanical engineering', 'civil engineering', 'electrical engineering',
        'chemical engineering', 'aerospace engineering', 'robotics',
        'material science', 'biomedical engineering', 'structural engineering',
        'anatomy', 'physiology', 'pathology',
        'epidemiology', 'oncology', 'cardiology',
        'neurology', 'psychiatry', 'immunology',
        'linguistics', 'logic', 'ethics',
        'aesthetics', 'comparative literature', 'critical theory',
        'marketing', 'finance', 'supply chain management',
        'strategic management',
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

    ESSAY_PROMPT_TYPES = [
        "Argumentative — agree or disagree with a statement.",
        "Discussion — discuss both views and give your own opinion.",
        "Problem-Solution — describe the problem and propose solutions.",
        "Advantages-Disadvantages — discuss pros and cons.",
        "Cause-Effect — analyze causes and consequences.",
    ]

    def __init__(self):
        logger.info(" PTESpeakingWriting initialized")
        try:
            self.ai_gen = PTEAIGenerator()
        except Exception as e:
            logger.error(f" PTEAIGenerator init failed: {e}")
            self.ai_gen = None

    # ══════════════════════════════════════════════════════════════════
    # Topic + difficulty helpers
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

    @staticmethod
    def _build_difficulty_schedule(total: int) -> List[str]:
        schedule = []
        for i in range(total):
            frac = (i + 0.5) / total
            level = 'medium'
            for lo, hi, lvl in PTESpeakingWriting.DIFFICULTY_BANDS:
                if lo <= frac < hi:
                    level = lvl
                    break
            schedule.append(level)
        return schedule

    def _difficulty_hint(self, level: str) -> str:
        return self.DIFFICULTY_DESCRIPTORS.get(level, self.DIFFICULTY_DESCRIPTORS['medium'])

    @staticmethod
    def _expected_total() -> int:
        return sum(count for _, count in PTESpeakingWriting.QUESTION_ORDER)

    def _get_length_bounds(self, qtype: str, level: str):
        """Return (min, max) word count bounds for a question type and difficulty."""
        table = self.LENGTH_LIMITS.get(qtype)
        if not table:
            return (0, 9999)
        return table.get(level, table.get('medium', (0, 9999)))

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC ENTRY
    # ══════════════════════════════════════════════════════════════════
    def generate_full_test(
        self,
        difficulty: str = "medium",
        topic: Optional[str] = None,
        generate_images: bool = True,
        generate_audio: bool = True,
    ) -> Dict[str, Any]:
        return self._generate_full_test_impl(difficulty, topic, generate_images, generate_audio)

    def _generate_full_test_impl(
        self,
        difficulty: str,
        topic: Optional[str],
        generate_images: bool,
        generate_audio: bool,
    ) -> Dict[str, Any]:
        if not self.ai_gen:
            return self._error_response(difficulty, "AI engine not available.")

        logger.info(
            f" Individual generation (with image + audio support) — "
            f"target {self._expected_total()} questions"
        )
        questions = self._generate_questions_individually(
            difficulty, forced_topic=topic,
            generate_images=generate_images,
            generate_audio=generate_audio,
        )

        if not questions:
            return self._error_response(difficulty, "All AI attempts failed")

        questions = self._reorder_questions(questions)
        valid = self._validate_questions(questions)
        if not valid:
            return self._error_response(difficulty, "Generated questions are invalid")

        from collections import Counter
        type_counts = Counter(q.get('type', 'unknown') for q in valid)
        level_counts = Counter(q.get('difficulty_level', 'unknown') for q in valid)
        expected = self._expected_total()
        logger.info(
            f" PTE S&W generated: {len(valid)}/{expected} questions "
            f"types={dict(type_counts)} levels={dict(level_counts)}"
        )

        if len(valid) < expected:
            logger.warning(
                f" Only {len(valid)}/{expected} questions generated — "
                f"some AI attempts failed after retries"
            )

        return {
            'success': True,
            'questions': valid,
            'total_questions': len(valid),
            'expected_questions': expected,
            'duration': '45-60 minutes',
            'difficulty': difficulty,
            'year': 2026,
            'audio_generated': sum(1 for q in valid if q.get('prompt', {}).get('audio_url')),
            'official_pattern': True,
            'official_version': 'PTE Academic 2026',
            'test_type': 'pte_speaking_writing',
            'protocol_version': self.PROTOCOL_VERSION,
            'difficulty_progression': True,
        }

    def _ai_generate(self, prompt: str, retries: int = 2) -> Optional[str]:
        for attempt in range(retries):
            try:
                # FIX: use instance's ai_gen, not class's private static
                result = self.ai_gen._ai_generate(
                    prompt, max_tokens=500, temperature=0.7
                )
                if result:
                    return result
                logger.warning(f"AI attempt {attempt+1} returned None")
            except Exception as e:
                logger.warning(f"AI attempt {attempt+1} failed: {e}")
                time.sleep(0.5)
        return None

    def _extract_json(self, response: str) -> Optional[Dict]:
        if not response:
            return None
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            return json.loads(response[start:end])
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════
    # AUDIO GENERATION
    # ══════════════════════════════════════════════════════════════════
    def _generate_audio_for_question(
        self, text: str, question_type: str, question_id: str, timeout: int = 45
    ) -> Optional[str]:
        """
        Generate audio for a single question via audio_service.

         Voice diversity: uses stable hash of question_id to pick
        a different Deepgram Aura voice per question.
        """
        if not AUDIO_SERVICE_AVAILABLE or not audio_service:
            logger.warning(" audio_service unavailable — skipping audio generation")
            return None

        if not text or not text.strip():
            return None

        # Stable voice index per question (0–8 range for 9-voice pool)
        try:
            voice_idx = int(hashlib.md5(question_id.encode('utf-8')).hexdigest()[:8], 16) % 9
        except Exception:
            voice_idx = 0

        # Clean filename (no duplicate prefixes)
        filename = f"{question_type}_{uuid.uuid4().hex[:10]}.mp3"

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    audio_service.generate_speaking_audio,
                    text=text,
                    part=1,
                    question_num=voice_idx, #  voice diversity!
                    custom_filename=filename,
                )
                result = future.result(timeout=timeout)
                if result:
                    logger.info(
                        f" Audio OK for {question_type} (voice_idx={voice_idx})"
                    )
                    return result
                else:
                    logger.warning(f" No audio URL returned for {question_type}")
        except Exception as e:
            logger.warning(f"Audio generation failed for {question_type}: {e}")
        return None

    # ══════════════════════════════════════════════════════════════════
    # MAIN GENERATION LOOP
    # ══════════════════════════════════════════════════════════════════
    def _generate_questions_individually(
        self,
        difficulty: str,
        forced_topic: Optional[str] = None,
        generate_images: bool = True,
        generate_audio: bool = True,
    ) -> List[Dict]:
        task_list: List[tuple] = []
        for qtype, count in self.QUESTION_ORDER:
            for i in range(count):
                task_list.append((qtype, i + 1))
        total = len(task_list)
        difficulty_schedule = self._build_difficulty_schedule(total)

        used_topics: set = set()
        used_signatures: set = set()
        questions: List[Dict] = []
        audio_queue: List[tuple] = []

        for idx, ((qtype, seq), level) in enumerate(zip(task_list, difficulty_schedule)):
            if idx == 0 and forced_topic:
                topic = forced_topic
                used_topics.add(topic)
            else:
                topic = self._pick_topic(used_topics)

            q = None
            for retry in range(3):
                sub_topic = topic if retry == 0 else self._pick_topic(used_topics)

                if qtype == 'read_aloud':
                    q = self._gen_read_aloud(sub_topic, level)
                elif qtype == 'repeat_sentence':
                    q = self._gen_repeat_sentence(sub_topic, level)
                elif qtype == 'describe_image':
                    q = self._gen_describe_image(sub_topic, level, generate_images)
                elif qtype == 're_tell_lecture':
                    q = self._gen_re_tell_lecture(sub_topic, level)
                elif qtype == 'answer_short_question':
                    q = self._gen_answer_short_question(sub_topic, level)
                elif qtype == 'summarize_written_text':
                    q = self._gen_summarize_written_text(sub_topic, level)
                elif qtype == 'write_essay':
                    q = self._gen_write_essay(sub_topic, level)

                if q:
                    sig = self._content_signature(q)
                    if sig and sig in used_signatures:
                        logger.warning(f" Duplicate content for {qtype} — retrying")
                        q = None
                        continue
                    used_signatures.add(sig)
                    break

                logger.warning(f"Retry {retry+1}/3 for {qtype} (item {seq})")

            if q:
                q['topic'] = topic
                q['difficulty_level'] = level
                q['position'] = idx + 1
                questions.append(q)

                if generate_audio and qtype in (
                    'repeat_sentence', 're_tell_lecture', 'answer_short_question'
                ):
                    text = (
                        q['prompt'].get('sentence')
                        or q['prompt'].get('lecture')
                        or q['prompt'].get('question', '')
                    )
                    if text:
                        audio_queue.append((q, text, qtype))
            else:
                logger.error(f" Failed to generate {qtype} (item {seq}) after 3 retries")

        # ─── Parallel audio generation (best-effort) ──────────────
        if audio_queue:
            logger.info(f" Generating {len(audio_queue)} audio files in parallel...")
            try:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {
                        executor.submit(
                            self._generate_audio_for_question,
                            text, qtype, q['id'], 45,
                        ): q
                        for q, text, qtype in audio_queue
                    }
                    try:
                        for future in as_completed(futures, timeout=240):
                            q = futures[future]
                            try:
                                audio_url = future.result()
                                if audio_url:
                                    q['prompt']['audio_url'] = audio_url
                            except Exception as e:
                                logger.warning(f"Audio future failed: {e}")
                    except Exception as e:
                        logger.warning(f"Some audio futures timed out: {e}")
            except Exception as e:
                logger.warning(f"Parallel audio generation failed: {e}")

        expected = self._expected_total()
        logger.info(f" PTE S&W: {len(questions)} questions collected (expected {expected})")

        if len(questions) < expected:
            logger.warning(f" Missing {expected - len(questions)} questions")

        return questions

    @staticmethod
    def _content_signature(q: Dict) -> Optional[str]:
        """
        Stable dedup signature (md5, not salted hash).
         Now includes image_description for describe_image questions.
        """
        try:
            prompt = q.get('prompt') or {}
            if not isinstance(prompt, dict):
                return None
            text = (
                prompt.get('text') or
                prompt.get('passage') or
                prompt.get('sentence') or
                prompt.get('lecture') or
                prompt.get('question') or
                prompt.get('prompt') or
                prompt.get('image_description') or '' #  added
            )
            if not isinstance(text, str):
                return None
            normalized = text.strip()[:150].lower()
            return hashlib.md5(normalized.encode('utf-8')).hexdigest()
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════
    # INDIVIDUAL GENERATORS (difficulty-aware, level-aware length)
    # ══════════════════════════════════════════════════════════════════
    def _gen_read_aloud(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        length_range = {'easy': "25-35", 'medium': "35-45", 'hard': "45-55"}.get(level, "30-40")

        text = self._ai_generate(
            f"Write a short academic paragraph ({length_range} words) about '{topic}' for PTE Read Aloud.\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            "Return only the paragraph text (no JSON, no markers)."
        )
        if not text:
            return None
        cleaned = text.strip().strip('"').strip("'")
        word_count = len(cleaned.split())

        # Level-aware bounds
        lo, hi = self._get_length_bounds('read_aloud', level)
        if word_count < lo or word_count > hi:
            logger.warning(f"Read Aloud length out of range for {level}: {word_count} (expected {lo}-{hi})")
            return None

        return {
            "id": f"read_aloud_{uuid.uuid4().hex[:10]}", #  collision-free
            "type": "read_aloud",
            "type_name": "Read Aloud",
            "prompt": {
                "instruction": "Read the text aloud as naturally as possible.",
                "text": cleaned,
            },
            "difficulty": level,
            "year": 2026,
        }

    def _gen_repeat_sentence(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        length = "10-13" if level == 'easy' else ("13-16" if level == 'medium' else "16-19")
        sent = self._ai_generate(
            f"Generate a {length}-word academic sentence about '{topic}' for PTE Repeat Sentence.\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            "Return only the sentence (no JSON, no quotes)."
        )
        if not sent:
            return None
        sentence = sent.strip().strip('"').strip("'")
        if sentence.endswith('...'):
            sentence = sentence[:-3].strip()
        word_count = len(sentence.split())

        lo, hi = self._get_length_bounds('repeat_sentence', level)
        if word_count < lo or word_count > hi:
            logger.warning(f"Repeat Sentence length out of range for {level}: {word_count} (expected {lo}-{hi})")
            return None

        return {
            "id": f"repeat_sentence_{uuid.uuid4().hex[:10]}",
            "type": "repeat_sentence",
            "type_name": "Repeat Sentence",
            "prompt": {
                "instruction": "Listen to the sentence and repeat it exactly.",
                "sentence": sentence,
                "audio_url": None,
            },
            "correct_answer": sentence,
            "difficulty": level,
            "year": 2026,
        }

    def _gen_describe_image(
        self, topic: str, level: str, generate_images: bool = True
    ) -> Optional[Dict]:
        image_url = None
        base64_image = None
        image_description = ""
        key_points = []
        chart_type = None

        static_dir = os.path.join('static', 'images', 'pte_images')
        os.makedirs(static_dir, exist_ok=True)

        if generate_images and MATPLOTLIB_AVAILABLE:
            chart_types = ['bar_chart', 'line_graph', 'pie_chart', 'column_graph',
                           'radar_chart', 'scatter_plot', 'area_chart', 'table']
            chart_type = random.choice(chart_types)
            try:
                image_data = pte_image_generator.generate_image(
                    chart_type=chart_type,
                    difficulty=level,
                    save_to_disk=True,
                )
                if image_data.get('success'):
                    image_url = image_data.get('image_url')
                    base64_image = image_data.get('base64')
                    if image_data.get('description'):
                        image_description = image_data['description']
                        key_points = self._extract_key_points_from_description(image_description)
                    else:
                        image_description = f"This {chart_type.replace('_', ' ')} shows data."
                        key_points = ["Data trends visible", "Multiple categories", "Variations exist"]
            except Exception as e:
                logger.warning(f"Image generation error: {e}")

        if not image_url:
            hint = self._difficulty_hint(level)
            result = self._ai_generate(
                f"Describe an image related to '{topic}' for PTE Describe Image.\n\n"
                f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
                "Provide a vivid description (3-4 sentences). "
                'Return JSON: {"image_description":"...", "key_points":["p1","p2","p3"]}'
            )
            data = self._extract_json(result) if result else None
            if data:
                image_description = data.get("image_description", "")
                key_points = data.get("key_points", [])
            else:
                return None

        if not image_description:
            return None

        return {
            "id": f"describe_image_{uuid.uuid4().hex[:10]}",
            "type": "describe_image",
            "type_name": "Describe Image",
            "prompt": {
                "instruction": "Describe the image in as much detail as possible.",
                "image_description": image_description,
                "image_url": image_url,
                "base64": base64_image,
                "key_points": key_points,
                "chart_type": chart_type if image_url else None,
            },
            "difficulty": level,
            "year": 2026,
        }

    def _gen_re_tell_lecture(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        length = "60-80" if level == 'easy' else ("80-100" if level == 'medium' else "100-120")
        lecture = self._ai_generate(
            f"Write a short academic lecture ({length} words) about '{topic}' for PTE Re-tell Lecture.\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            "Return only the lecture text (no JSON, no headings)."
        )
        if not lecture:
            return None
        lecture_text = lecture.strip().strip('"').strip("'")
        word_count = len(lecture_text.split())

        lo, hi = self._get_length_bounds('re_tell_lecture', level)
        if word_count < lo or word_count > hi:
            logger.warning(f"Re-tell Lecture length out of range for {level}: {word_count} (expected {lo}-{hi})")
            return None

        return {
            "id": f"re_tell_lecture_{uuid.uuid4().hex[:10]}",
            "type": "re_tell_lecture",
            "type_name": "Re-tell Lecture",
            "prompt": {
                "instruction": "Listen to the lecture and retell it in your own words.",
                "lecture": lecture_text,
                "audio_url": None,
            },
            "difficulty": level,
            "year": 2026,
        }

    def _gen_answer_short_question(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        result_text = self._ai_generate(
            f"Create a short question and its 1-3 word answer about '{topic}' "
            f"for PTE Answer Short Question.\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            'Return JSON: {"question":"...", "answer":"one or two words"}'
        )
        data = self._extract_json(result_text) if result_text else None
        if not data or not data.get('question') or not data.get('answer'):
            return None

        question_text = str(data.get("question", "")).strip()
        answer_text = str(data.get("answer", "")).strip()

        if not question_text or not answer_text:
            return None

        # Tighter: real PTE ASQ answers are 1-3 words max
        if len(answer_text.split()) > 3:
            logger.warning(f"ASQ answer too long: '{answer_text}'")
            return None

        return {
            "id": f"answer_short_question_{uuid.uuid4().hex[:10]}",
            "type": "answer_short_question",
            "type_name": "Answer Short Question",
            "prompt": {
                "instruction": "Answer the question with one word or a short phrase.",
                "question": question_text,
                "correct_answer": answer_text,
                "audio_url": None,
            },
            "correct_answer": answer_text,
            "difficulty": level,
            "year": 2026,
        }

    def _extract_key_points_from_description(self, description: str) -> List[str]:
        sentences = [s.strip() for s in description.split('.') if len(s.strip()) > 10]
        if len(sentences) >= 3:
            return sentences[:3]
        elif sentences:
            return (sentences + ["Data patterns visible", "Variations exist"])[:3]
        else:
            return ["Clear trends", "Categories visible", "Variations exist"]

    def _gen_summarize_written_text(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        length = "80-100" if level != 'hard' else "100-120"
        passage = self._ai_generate(
            f"Write a short academic passage ({length} words) about '{topic}' for PTE Summarize Written Text.\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            "Return only the passage (no JSON, no markers)."
        )
        if not passage:
            return None
        passage_text = passage.strip().strip('"').strip("'")
        word_count = len(passage_text.split())

        lo, hi = self._get_length_bounds('summarize_written_text', level)
        if word_count < lo or word_count > hi:
            logger.warning(f"SST length out of range for {level}: {word_count} (expected {lo}-{hi})")
            return None

        return {
            "id": f"summarize_written_text_{uuid.uuid4().hex[:10]}",
            "type": "summarize_written_text",
            "type_name": "Summarize Written Text",
            "prompt": {
                "instruction": "Write a one-sentence summary (5-75 words).",
                "passage": passage_text,
            },
            "difficulty": level,
            "year": 2026,
        }

    def _gen_write_essay(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        prompt_type = random.choice(self.ESSAY_PROMPT_TYPES)
        prompt_text = self._ai_generate(
            f"Generate an IELTS/PTE-style essay prompt about '{topic}' for PTE Write Essay.\n\n"
            f"Prompt style: {prompt_type}\n\n"
            f"DIFFICULTY GUIDANCE:\n{hint}\n\n"
            "Return ONLY the prompt sentence (no JSON, no markers)."
        )
        if not prompt_text:
            return None
        prompt_clean = prompt_text.strip().strip('"').strip("'")
        if len(prompt_clean.split()) < 8:
            return None

        return {
            "id": f"write_essay_{uuid.uuid4().hex[:10]}",
            "type": "write_essay",
            "type_name": "Write Essay",
            "prompt": {
                "instruction": "Write a 200-300 word essay.",
                "prompt": prompt_clean,
                "topic": topic,
                "prompt_type": prompt_type,
            },
            "difficulty": level,
            "year": 2026,
        }

    # ══════════════════════════════════════════════════════════════════
    # Post-processing
    # ══════════════════════════════════════════════════════════════════
    def _reorder_questions(self, questions: List[Dict]) -> List[Dict]:
        """Order questions by 'position' (already set during generation)."""
        for q in questions:
            if 'type' not in q:
                q['type'] = 'unknown'
        # Position is set during generation; sort by it
        questions.sort(key=lambda q: q.get('position', 9999))
        return questions

    def _validate_questions(self, questions: List[Dict]) -> List[Dict]:
        valid = []
        for q in questions:
            if not q.get('type') or not q.get('prompt'):
                continue
            prompt = q.get('prompt', {})
            if not prompt or not isinstance(prompt, dict) or len(prompt) == 0:
                continue

            qtype = q['type']
            if qtype == 'read_aloud' and not prompt.get('text'):
                continue
            if qtype == 'repeat_sentence' and not prompt.get('sentence'):
                continue
            if qtype == 'describe_image' and not (prompt.get('image_url') or prompt.get('image_description')):
                continue
            if qtype == 're_tell_lecture' and not prompt.get('lecture'):
                continue
            if qtype == 'answer_short_question' and not prompt.get('question'):
                continue
            if qtype == 'summarize_written_text' and not prompt.get('passage'):
                continue
            if qtype == 'write_essay' and not prompt.get('prompt'):
                continue

            if 'id' not in q:
                q['id'] = f"{q['type']}_{uuid.uuid4().hex[:10]}"
            valid.append(q)
        return valid

    def _error_response(self, difficulty: str, error_msg: str) -> Dict:
        return {
            'success': False,
            'error': error_msg,
            'questions': [],
            'total_questions': 0,
            'expected_questions': self._expected_total(),
            'duration': 'N/A',
            'difficulty': difficulty,
            'year': 2026,
            'audio_generated': 0,
            'test_type': 'pte_speaking_writing',
            'protocol_version': self.PROTOCOL_VERSION,
        }


# ─── Singleton ───
pte_speaking_writing = PTESpeakingWriting()