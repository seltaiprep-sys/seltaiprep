# modules/pte/generators/reading.py
"""PTE Reading Generator – Pure AI, topic-aware, difficulty-progressive."""

import logging
import random
import json
import time
import hashlib
from typing import Dict, Any, Optional, List

from ..utils.ai_generator import PTEAIGenerator

logger = logging.getLogger(__name__)


class PTEReading:
    """
    PTE Reading generator.

    Realism features:
      • Real PTE question-type distribution (13-17 questions, randomized)
      • Easy → Medium → Hard progression within a test
      • Unique topic per question (rotates through the pool)
      • Difficulty-aware AI prompts (vocabulary + content complexity)
      • Server-side shuffle for reorder paragraphs
    """

    # ══════════════════════════════════════════════════════════════════
    # BASE QUESTION COUNTS (used as randomization template)
    # Each test picks a random value from the ranges below.
    # ══════════════════════════════════════════════════════════════════
    TYPE_COUNT_RANGES = {
        'multiple_choice_single': (1, 2), # 1-2
        'multiple_choice_multiple': (1, 2), # 1-2
        'reorder_paragraphs': (2, 3), # 2-3
        'fill_blanks': (4, 5), # 4-5
        'reading_fill_blanks': (4, 5), # 4-5
    }
    # Total range = 12-17 questions (real PTE: 13-15)

    MAX_QUESTIONS_HARD_CAP = 20

    OFFICIAL_DURATION_MINUTES = 30
    OFFICIAL_DURATION_SECONDS = 1800

    PROTOCOL_VERSION = 3 # v3 = randomized type counts per test

    # ══════════════════════════════════════════════════════════════════
    # DIFFICULTY PROGRESSION
    # ══════════════════════════════════════════════════════════════════
    DIFFICULTY_BANDS = [
        (0.00, 0.40, 'easy'), # first 40%
        (0.40, 0.80, 'medium'), # middle 40%
        (0.80, 1.00, 'hard'), # final 20%
    ]

    # ══════════════════════════════════════════════════════════════════
    # DIFFICULTY-AWARE CONTENT DESCRIPTORS
    # ══════════════════════════════════════════════════════════════════
    DIFFICULTY_DESCRIPTORS = {
        'easy': (
            "Use straightforward academic English. Each sentence should express "
            "one clear idea. Avoid unusual vocabulary and complex clause structures. "
            "Any facts should be simple and directly stated."
        ),
        'medium': (
            "Use formal academic English suitable for university-level readers. "
            "Include 1-2 specific facts, statistics, or research references. "
            "Some sentences may contain subordinate clauses, but the main idea "
            "of each paragraph should remain clear."
        ),
        'hard': (
            "Use sophisticated academic English with nuanced argumentation. "
            "Include multiple specific data points, references to research, or "
            "subtle qualifications. Sentences may contain complex structures "
            "(subordination, coordination, hedging). The distractor options in "
            "any multiple-choice question should be very close to the correct "
            "answer so that only careful reading separates them."
        ),
    }

    ACADEMIC_TOPICS = [
        'quantum mechanics', 'astrophysics', 'thermodynamics',
        'classical mechanics', 'electromagnetism', 'condensed matter physics',
        'nuclear physics', 'plasma physics', 'particle physics',
        'stellar evolution', 'galactic astronomy', 'cosmology',
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

    def __init__(self):
        try:
            self.ai_gen = PTEAIGenerator()
            logger.info(" PTEReading initialized")
        except Exception as e:
            logger.error(f" PTEAIGenerator init failed: {e}")
            self.ai_gen = None

    # ─── Topic helpers ────────────────────────────────────────────────
    def _pick_topic(self, used_topics: set) -> str:
        pool = self.ACADEMIC_TOPICS + self.GENERAL_TOPICS
        available = [t for t in pool if t not in used_topics]
        if not available:
            used_topics.clear()
            available = pool
        topic = random.choice(available)
        used_topics.add(topic)
        return topic

    # ══════════════════════════════════════════════════════════════════
    # RANDOMIZED TYPE COUNTS (called per test generation)
    # Each test gets a different mix of question types within the
    # ranges defined by TYPE_COUNT_RANGES.
    # ══════════════════════════════════════════════════════════════════
    def _build_type_counts(self) -> Dict[str, int]:
        """
        Build a random per-test distribution of question types.

        Ranges:
          • multiple_choice_single: 1–2
          • multiple_choice_multiple: 1–2
          • reorder_paragraphs: 2–3
          • fill_blanks: 4–5
          • reading_fill_blanks: 4–5

        Total per test: 12–17 questions (real PTE: 13–15)
        """
        counts = {}
        for qtype, (lo, hi) in self.TYPE_COUNT_RANGES.items():
            counts[qtype] = random.randint(lo, hi)

        total = sum(counts.values())

        # Safety: clamp to hard cap
        if total > self.MAX_QUESTIONS_HARD_CAP:
            # Trim from largest counts first
            excess = total - self.MAX_QUESTIONS_HARD_CAP
            for qtype in sorted(counts, key=lambda k: counts[k], reverse=True):
                while excess > 0 and counts[qtype] > 1:
                    counts[qtype] -= 1
                    excess -= 1
                if excess <= 0:
                    break

        logger.info(f" Randomized type counts: {counts} (total={sum(counts.values())})")
        return counts

    # ─── Difficulty helpers ───────────────────────────────────────────
    @staticmethod
    def _build_difficulty_schedule(total: int) -> List[str]:
        schedule = []
        for i in range(total):
            frac = (i + 0.5) / total
            level = 'medium'
            for lo, hi, lvl in PTEReading.DIFFICULTY_BANDS:
                if lo <= frac < hi:
                    level = lvl
                    break
            schedule.append(level)
        return schedule

    def _difficulty_hint(self, level: str) -> str:
        return self.DIFFICULTY_DESCRIPTORS.get(level, self.DIFFICULTY_DESCRIPTORS['medium'])

    # ─── Entry points ─────────────────────────────────────────────────
    @staticmethod
    def generate_full_test(difficulty: str = "medium", topic: Optional[str] = None) -> Dict[str, Any]:
        instance = PTEReading()
        return instance._generate_full_test_impl(difficulty, topic)

    def _generate_full_test_impl(self, difficulty: str, topic: Optional[str]) -> Dict[str, Any]:
        if not self.ai_gen:
            return self._error_response(difficulty, "AI engine not available.")

        questions = self._generate_questions_individually(difficulty, forced_topic=topic)

        if not questions:
            return self._error_response(difficulty, "AI failed to generate any questions.")

        questions = self._reorder_questions(questions)
        valid_questions = self._validate_questions(questions)
        if not valid_questions:
            return self._error_response(difficulty, "Generated questions are invalid.")

        if len(valid_questions) > self.MAX_QUESTIONS_HARD_CAP:
            logger.warning(
                f" Generated {len(valid_questions)} questions — "
                f"capping to {self.MAX_QUESTIONS_HARD_CAP}"
            )
            valid_questions = valid_questions[:self.MAX_QUESTIONS_HARD_CAP]

        from collections import Counter
        type_counts = Counter(q.get('type', 'unknown') for q in valid_questions)
        level_counts = Counter(q.get('difficulty_level', 'unknown') for q in valid_questions)
        logger.info(
            f" PTE Reading generated: {len(valid_questions)} questions "
            f"types={dict(type_counts)} levels={dict(level_counts)}"
        )

        return {
            'success': True,
            'questions': valid_questions,
            'total_questions': len(valid_questions),
            'duration': f'{self.OFFICIAL_DURATION_MINUTES} minutes',
            'duration_seconds': self.OFFICIAL_DURATION_SECONDS,
            'difficulty': difficulty,
            'year': 2026,
            'official_pattern': True,
            'official_version': 'PTE Academic 2026',
            'test_type': 'pte_reading',
            'protocol_version': self.PROTOCOL_VERSION,
            'difficulty_progression': True,
            'randomized_type_counts': True,
        }

    # ══════════════════════════════════════════════════════════════════
    # Generation loop — randomized distribution + unique topics + difficulty
    # ══════════════════════════════════════════════════════════════════
    def _generate_questions_individually(
        self,
        difficulty: str,
        forced_topic: Optional[str] = None,
    ) -> List[Dict]:
        """
        Generates questions one by one, in the exact order they will be served.

        Each question gets:
          • Its own unique topic (except the first, which may be forced)
          • A difficulty level from an easy→medium→hard schedule
          • Randomized counts per type (per test)
        """
        # Build randomized distribution for this test
        type_counts = self._build_type_counts()

        # Build the ordered list of qtypes based on the randomized counts
        type_order: List[str] = []
        for qtype, count in type_counts.items():
            type_order.extend([qtype] * count)

        # Interleave types so the test doesn't run e.g. 5 fill-blanks in a row
        random.shuffle(type_order)

        total = len(type_order)
        difficulty_schedule = self._build_difficulty_schedule(total)

        used_topics: set = set()
        used_signatures: set = set()
        questions: List[Dict] = []

        for idx, qtype in enumerate(type_order):
            level = difficulty_schedule[idx]

            # First question honours forced_topic if provided
            if idx == 0 and forced_topic:
                topic = forced_topic
                used_topics.add(topic)
            else:
                topic = self._pick_topic(used_topics)

            q: Optional[Dict] = None
            last_candidate: Optional[Dict] = None #  keep best-of-retries

            for retry in range(3):
                sub_topic = topic if retry == 0 else self._pick_topic(used_topics)

                if qtype == 'multiple_choice_single':
                    q = self._gen_multiple_choice(sub_topic, level, multiple=False)
                elif qtype == 'multiple_choice_multiple':
                    q = self._gen_multiple_choice(sub_topic, level, multiple=True)
                elif qtype == 'reorder_paragraphs':
                    q = self._gen_reorder_paragraphs(sub_topic, level)
                elif qtype == 'fill_blanks':
                    q = self._gen_fill_blanks(sub_topic, level)
                elif qtype == 'reading_fill_blanks':
                    q = self._gen_reading_fill_blanks(sub_topic, level)

                if q:
                    sig = self._content_signature(q)
                    if sig and sig in used_signatures:
                        logger.warning(
                            f" Duplicate content for {qtype} "
                            f"(item {idx+1}/{total}, retry {retry+1}/3)"
                        )
                        last_candidate = q # keep as fallback
                        q = None
                        continue
                    used_signatures.add(sig)
                    break

                # AI returned None — retry
                logger.warning(f"Retry {retry+1}/3 for {qtype} (item {idx+1}/{total})")

            # If all retries failed only because of duplicate detection,
            # accept the last valid candidate rather than dropping the question.
            if q is None and last_candidate is not None:
                logger.warning(
                    f" Accepting last candidate for {qtype} "
                    f"(item {idx+1}/{total}) — dedup could not find a unique one"
                )
                q = last_candidate
                sig = self._content_signature(q)
                if sig:
                    used_signatures.add(sig)

            if q:
                q['topic'] = topic
                q['difficulty_level'] = level
                q['position'] = idx + 1
                questions.append(q)
            else:
                logger.error(f" Failed {qtype} (item {idx+1}/{total}) after 3 retries")

        logger.info(
            f" PTE Reading: {len(questions)} questions "
            f"(expected {total})"
        )
        return questions

    # ══════════════════════════════════════════════════════════════════
    # CONTENT SIGNATURE — FIXED
    # Handles every question type currently generated:
    # • multiple_choice_* → prompt.passage / prompt.question
    # • fill_blanks → prompt.text
    # • reading_fill_blanks→ prompt.text
    # • reorder_paragraphs → prompt.sentences (sorted, shuffle-invariant)
    #
    # Falls back to hashing the whole prompt JSON so we never collide
    # on the empty-string hash (which was the original bug).
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _content_signature(q: Dict) -> Optional[str]:
        try:
            prompt = q.get('prompt') or {}
            if not isinstance(prompt, dict):
                return None

            # Primary text fields (used by MC, fill_blanks, reading_fill_blanks)
            text = (
                prompt.get('text')
                or prompt.get('passage')
                or prompt.get('question')
            )

            # Fallback for reorder_paragraphs — no text/passage/question key.
            # Sort sentences so the server-side shuffle doesn't affect the
            # signature between identical generations.
            if not text:
                sentences = prompt.get('sentences') or prompt.get('paragraphs') or []
                if isinstance(sentences, list) and sentences:
                    text = ' '.join(sorted(str(s) for s in sentences))

            # Last-resort: hash the whole prompt so we never collide on ''
            if not isinstance(text, str) or not text.strip():
                text = json.dumps(prompt, sort_keys=True, default=str)

            # Use sha1 (stable across processes) instead of built-in hash()
            normalized = text.strip()[:200].lower().encode('utf-8')
            return hashlib.sha1(normalized).hexdigest()[:16]

        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════
    # MULTIPLE CHOICE
    # ══════════════════════════════════════════════════════════════════
    def _gen_multiple_choice(self, topic: str, level: str, multiple: bool = False) -> Optional[Dict]:
        qtype = "multiple_choice_multiple" if multiple else "multiple_choice_single"
        hint = self._difficulty_hint(level)

        correct_rule = (
            'Exactly 2 or 3 correct answers. Provide their 0-based indices in "correct".'
            if multiple else
            'Exactly 1 correct answer. Provide its 0-based index in "correct" as a single-element array.'
        )

        num_options = 4 if level == 'easy' else random.choice([4, 5])
        letters = ['A', 'B', 'C', 'D', 'E'][:num_options]

        prompt = f"""Write a {level.upper()}-difficulty academic passage (110-160 words) about: "{topic}".

DIFFICULTY GUIDANCE:
{hint}

Then create a challenging {qtype} question based strictly on the passage.
{correct_rule}
Provide exactly {num_options} options ({', '.join(letters)}).
All distractors must be plausible but clearly incorrect based on the passage.

Return ONLY valid JSON in this exact format:
{{
  "passage": "...",
  "question": "...",
  "options": {json.dumps(["option " + l for l in letters])},
  "correct": [0]
}}"""

        response = self._ai_generate(prompt)
        if not response:
            return None
        data = self._extract_single_json(response)
        if not data:
            return None

        passage = data.get('passage', '')
        options = data.get('options', [])
        correct = data.get('correct', [])

        if not isinstance(passage, str) or len(passage.strip()) < 50:
            logger.warning("MC: passage too short")
            return None

        if not isinstance(options, list) or len(options) < 4:
            logger.warning("MC: options missing or <4")
            return None

        options = [o for o in options if isinstance(o, str) and o.strip()]
        if len(options) < 4:
            logger.warning("MC: not enough non-empty options")
            return None

        try:
            correct = [int(c) for c in correct]
        except (TypeError, ValueError):
            logger.warning("MC: correct contains non-int values")
            return None

        if any(c < 0 or c >= len(options) for c in correct):
            logger.warning(f"MC: correct index out of range: {correct}")
            return None

        correct = sorted(set(correct))

        if multiple:
            if len(correct) < 2 or len(correct) > 3:
                logger.warning(f"MC-Multi: expected 2-3 correct, got {len(correct)}")
                return None
        else:
            if len(correct) != 1:
                logger.warning(f"MC-Single: expected exactly 1 correct, got {len(correct)}")
                return None

        return {
            "id": f"{qtype}_{random.randint(1000, 9999)}",
            "type": qtype,
            "prompt": {
                "passage": passage,
                "question": data.get("question", "What is the main argument?"),
                "options": options,
                "correct": correct,
            },
            "correct_answer": correct,
            "difficulty": level,
            "year": 2026,
        }

    # ══════════════════════════════════════════════════════════════════
    # REORDER PARAGRAPHS — server-side shuffle
    # ══════════════════════════════════════════════════════════════════
    def _gen_reorder_paragraphs(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)

        prompt = f"""Write a coherent academic paragraph (4-5 sentences) about "{topic}".

DIFFICULTY GUIDANCE:
{hint}

The sentences must form a logical flow with a clear topic sentence, supporting details, and a concluding thought.
The correct logical order is the order in which you write them.

Return ONLY valid JSON in this exact format:
{{
  "sentences": ["sentence 1", "sentence 2", "sentence 3", "sentence 4"]
}}"""

        response = self._ai_generate(prompt)
        if not response:
            return None
        data = self._extract_single_json(response)
        if not data:
            return None

        sentences_correct = data.get("sentences", [])
        if not isinstance(sentences_correct, list) or len(sentences_correct) < 4:
            logger.warning("Reorder: not enough sentences")
            return None

        sentences_correct = [s.strip() for s in sentences_correct if isinstance(s, str) and s.strip()]
        if len(sentences_correct) < 4:
            logger.warning("Reorder: not enough valid sentences")
            return None

        if len(set(sentences_correct)) < len(sentences_correct):
            logger.warning("Reorder: duplicate sentences")
            return None

        # Server-side shuffle — send a shuffled display order + a matching permutation
        n = len(sentences_correct)
        indices = list(range(n))
        random.shuffle(indices)

        shuffled_sentences = [sentences_correct[i] for i in indices]

        correct_order = [0] * n
        for display_pos, orig_idx in enumerate(indices):
            correct_order[orig_idx] = display_pos

        if sorted(correct_order) != list(range(n)):
            logger.error("Reorder: invalid permutation")
            return None

        return {
            "id": f"reorder_{random.randint(1000, 9999)}",
            "type": "reorder_paragraphs",
            "prompt": {
                "sentences": shuffled_sentences,
                "correct_order": correct_order,
                "paragraphs": shuffled_sentences,
            },
            "correct_answer": correct_order,
            "difficulty": level,
            "year": 2026,
        }

    # ══════════════════════════════════════════════════════════════════
    # FILL IN THE BLANKS (Reading & Writing)
    # ══════════════════════════════════════════════════════════════════
    def _gen_fill_blanks(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        num_blanks = random.choice([4, 5])

        prompt = f"""Write an academic passage (90-120 words) about "{topic}".

DIFFICULTY GUIDANCE:
{hint}

Remove exactly {num_blanks} key words and replace them with [BLANK].
Provide {num_blanks} option groups — one per blank — each with 4 candidate words.
Only ONE candidate per blank is the correct word from the original passage.
Distractors must be plausible synonyms or words that fit grammatically but not semantically.

Return ONLY valid JSON:
{{
  "text": "passage with [BLANK] placeholders",
  "blanks": ["w1", "w2", ...],
  "options": [["o1","o2","o3","o4"], ["o1","o2","o3","o4"], ...]
}}"""

        response = self._ai_generate(prompt)
        if not response:
            return None
        data = self._extract_single_json(response)
        if not data:
            return None

        text = data.get('text', '')
        blanks = data.get('blanks', [])
        options = data.get('options', [])

        if not isinstance(text, str) or '[BLANK]' not in text:
            logger.warning("Fill: no [BLANK] markers found")
            return None

        marker_count = text.count('[BLANK]')
        if marker_count < num_blanks:
            logger.warning(f"Fill: only {marker_count} markers (need {num_blanks})")
            return None

        if not isinstance(blanks, list) or len(blanks) < num_blanks:
            logger.warning("Fill: not enough blanks")
            return None

        if not isinstance(options, list) or len(options) < num_blanks:
            logger.warning("Fill: not enough option groups")
            return None

        for idx, opt_set in enumerate(options[:num_blanks]):
            if not isinstance(opt_set, list) or len(opt_set) < 3:
                logger.warning(f"Fill: option group {idx} invalid")
                return None
            if idx < len(blanks) and blanks[idx] not in opt_set:
                logger.warning(f"Fill: correct word '{blanks[idx]}' missing from options {idx}")
                return None

        return {
            "id": f"fill_{random.randint(1000, 9999)}",
            "type": "fill_blanks",
            "prompt": {
                "text": text,
                "blanks": blanks[:num_blanks],
                "options": options[:num_blanks],
            },
            "correct_answer": blanks[:num_blanks],
            "difficulty": level,
            "year": 2026,
        }

    # ══════════════════════════════════════════════════════════════════
    # READING FILL IN THE BLANKS (word pool)
    # ══════════════════════════════════════════════════════════════════
    def _gen_reading_fill_blanks(self, topic: str, level: str) -> Optional[Dict]:
        hint = self._difficulty_hint(level)
        num_blanks = 4 if level == 'easy' else 5
        pool_size = num_blanks * 2

        prompt = f"""Write an academic passage (90-120 words) about "{topic}".

DIFFICULTY GUIDANCE:
{hint}

Remove exactly {num_blanks} key words and replace them with ________ (8 underscores).
Provide a pool of {pool_size} words: the {num_blanks} correct words plus {num_blanks} strong distractors.
Distractors must be grammatically plausible but semantically incorrect.

Return ONLY valid JSON:
{{
  "text": "passage with ________ placeholders",
  "blanks": ["w1", "w2", ...],
  "options": ["correct1", "correct2", ..., "distractor1", "distractor2", ...]
}}"""

        response = self._ai_generate(prompt)
        if not response:
            return None
        data = self._extract_single_json(response)
        if not data:
            return None

        text = data.get('text', '')
        blanks = data.get('blanks', [])
        options = data.get('options', [])

        if not isinstance(text, str) or '________' not in text:
            logger.warning("ReadingFill: no underscore markers")
            return None

        marker_count = text.count('________')
        if marker_count < num_blanks:
            logger.warning(f"ReadingFill: only {marker_count} markers")
            return None

        if not isinstance(blanks, list) or len(blanks) < num_blanks:
            logger.warning("ReadingFill: not enough blanks")
            return None

        if not isinstance(options, list) or len(options) < pool_size:
            logger.warning("ReadingFill: pool too small")
            return None

        options = [o for o in options if isinstance(o, str) and o.strip()]
        if len(options) < pool_size:
            logger.warning("ReadingFill: not enough valid pool words")
            return None

        for correct_word in blanks[:num_blanks]:
            if correct_word not in options:
                logger.warning(f"ReadingFill: '{correct_word}' missing from pool")
                return None

        return {
            "id": f"rfill_{random.randint(1000, 9999)}",
            "type": "reading_fill_blanks",
            "prompt": {
                "text": text,
                "blanks": blanks[:num_blanks],
                "options": options,
            },
            "correct_answer": blanks[:num_blanks],
            "difficulty": level,
            "year": 2026,
        }

    # ══════════════════════════════════════════════════════════════════
    # AI wrapper
    # ══════════════════════════════════════════════════════════════════
    def _ai_generate(self, prompt: str, retries: int = 2) -> Optional[str]:
        for attempt in range(retries):
            try:
                result = PTEAIGenerator._ai_generate(prompt, max_tokens=700, temperature=0.7)
                if result:
                    return result
                logger.warning(f"Attempt {attempt+1}: AI returned None.")
            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed: {e}")
                time.sleep(0.5)
        return None

    def _extract_single_json(self, response: str) -> Optional[Dict]:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            return json.loads(response[start:end])
        except json.JSONDecodeError:
            return None

    # ══════════════════════════════════════════════════════════════════
    # Validation + ordering
    # ══════════════════════════════════════════════════════════════════
    def _validate_questions(self, questions: List[Dict]) -> List[Dict]:
        valid = []
        for q in questions:
            if not q.get('type'):
                continue

            if 'correct_answer' not in q:
                prompt = q.get('prompt', {})
                if isinstance(prompt, dict) and 'correct' in prompt:
                    q['correct_answer'] = prompt['correct']
                elif isinstance(prompt, dict) and 'correct_answer' in prompt:
                    q['correct_answer'] = prompt['correct_answer']
                elif isinstance(prompt, dict) and 'correct_order' in prompt:
                    q['correct_answer'] = prompt['correct_order']
                else:
                    continue

            qtype = q['type']

            if qtype in ('multiple_choice_single', 'multiple_choice_multiple'):
                prompt = q.get('prompt') or {}
                options = prompt.get('options') or []
                correct = q.get('correct_answer') or []
                if not isinstance(options, list) or len(options) < 4:
                    continue
                try:
                    correct_ints = [int(c) for c in correct]
                except (TypeError, ValueError):
                    continue
                if any(c < 0 or c >= len(options) for c in correct_ints):
                    continue
                q['correct_answer'] = sorted(set(correct_ints))
                q['prompt']['correct'] = q['correct_answer']

            elif qtype == 'reorder_paragraphs':
                prompt = q.get('prompt') or {}
                sentences = prompt.get('sentences') or []
                correct_order = q.get('correct_answer') or []
                if not isinstance(sentences, list) or len(sentences) < 4:
                    continue
                if not isinstance(correct_order, list) or len(correct_order) != len(sentences):
                    continue
                if sorted(correct_order) != list(range(len(sentences))):
                    continue

            if 'id' not in q:
                q['id'] = f"{q['type']}_{random.randint(1000, 9999)}"

            valid.append(q)
        return valid

    def _reorder_questions(self, questions: List[Dict]) -> List[Dict]:
        """
        Preserve the generated order (already interleaved + difficulty-progressive).
        Do NOT regroup by type — that would break the difficulty progression.
        """
        for q in questions:
            if 'type' not in q:
                q['type'] = 'unknown'

        if all('position' in q for q in questions):
            questions.sort(key=lambda q: q.get('position', 999))
        return questions

    def _error_response(self, difficulty: str, error_msg: str) -> Dict:
        return {
            'success': False,
            'error': error_msg,
            'questions': [],
            'total_questions': 0,
            'duration': 'N/A',
            'difficulty': difficulty,
            'year': 2026,
            'test_type': 'pte_reading',
            'protocol_version': self.PROTOCOL_VERSION,
        }


pte_reading = PTEReading()