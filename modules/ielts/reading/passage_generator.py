"""AI-only IELTS Reading generator – all types, improved prompts, global numbering.

v2.0 — CRITICAL FIXES APPLIED:
  (1) Full passage now passed to question-generation AI prompt
        (previously only first 2500 chars → questions about 2nd half
        of the passage were impossible to answer).
  (2) Question numbering is now GLOBAL across the test:
        Passage 1 → Q1–Q13
        Passage 2 → Q14–Q26
        Passage 3 → Q27–Q40
        (Previously every passage numbered Q1–Q13 → duplicates in
        frontend and Q14–Q40 never shown.)
  (3) Strict word-count in passage prompt (850-950 words) with
        auto-retry if AI returns under 700 words.
  (4) Fallback MCQs now produce DISTINCT options via negation /
        number-change / opposite-claim patterns (previously all
        options were true quotes from the passage → unsolvable).
  (5) Better type distribution across the 3 passages so each
        test covers at least 6 different IELTS question types.
  (6) Options normalization retained (fixes frontend crash when
        AI returns {"letter": "A", "text": "..."}).

Uses topics from topics.py (300+ topics).
"""

import json
import random
import logging
import re
import concurrent.futures
from typing import List, Dict, Optional, Any
import uuid

from .reading_test import Passage, Question
from .evaluator import AnswerEvaluator
from .topics import DEFAULT_TOPICS, TOPICS_BY_DIFFICULTY, ALL_TOPICS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Global question numbering — real IELTS uses 1–40 across 3 passages
# ═══════════════════════════════════════════════════════════════════
QUESTION_OFFSET = {1: 0, 2: 13, 3: 26}
PASSAGE_QUESTION_TARGET = {1: 13, 2: 13, 3: 14}


class IELTSReadingGenerator:
    def __init__(self, ai_engine, use_paraphrasing=True):
        if not ai_engine:
            raise ValueError("AI engine required")
        self.ai = ai_engine
        self.evaluator = AnswerEvaluator()
        self.use_paraphrasing = use_paraphrasing

    # ═══════════════════════════════════════════════════════════════
    # Option normalization — prevents frontend "opt.match is not a function"
    # ═══════════════════════════════════════════════════════════════
    def _normalize_options(self, raw_options) -> List[str]:
        """
        Normalize options to a list of strings.
        Handles strings, dicts {"letter": "A", "text": "..."}, and any
        other type (falls back to str()).
        """
        if not raw_options:
            return []
        if not isinstance(raw_options, (list, tuple)):
            raw_options = [raw_options]

        normalized = []
        for opt in raw_options:
            if isinstance(opt, str):
                normalized.append(opt)
            elif isinstance(opt, dict):
                letter = (
                    opt.get("letter")
                    or opt.get("key")
                    or opt.get("id")
                    or opt.get("option")
                    or ""
                )
                text = (
                    opt.get("text")
                    or opt.get("label")
                    or opt.get("value")
                    or opt.get("name")
                    or opt.get("description")
                    or ""
                )
                if letter and text:
                    normalized.append(f"{letter}: {text}")
                elif letter:
                    normalized.append(str(letter))
                elif text:
                    normalized.append(str(text))
                else:
                    try:
                        normalized.append(json.dumps(opt, ensure_ascii=False))
                    except Exception:
                        normalized.append(str(opt))
            else:
                normalized.append(str(opt))
        return normalized

    # ═══════════════════════════════════════════════════════════════
    # MAIN ENTRY — generate complete 3-passage test
    # ═══════════════════════════════════════════════════════════════
    def generate_complete_test(self, difficulty="medium", topic_areas=None):
        if not topic_areas:
            available = TOPICS_BY_DIFFICULTY.get(difficulty, DEFAULT_TOPICS)
            if len(available) < 3:
                available = DEFAULT_TOPICS
            topic_areas = random.sample(available, min(3, len(available)))
        elif len(topic_areas) < 3:
            available = TOPICS_BY_DIFFICULTY.get(difficulty, DEFAULT_TOPICS)
            extra = [t for t in available if t not in topic_areas]
            topic_areas += random.sample(extra, 3 - len(topic_areas))
        elif len(topic_areas) > 3:
            topic_areas = topic_areas[:3]

        passages = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for idx, topic in enumerate(topic_areas):
                # Real IELTS difficulty: P1 easy, P2 medium, P3 hard
                diff = ['easy', 'medium', 'hard'][idx % 3]
                futures.append(executor.submit(
                    self._generate_single_passage,
                    topic, diff, idx + 1
                ))
            for future in concurrent.futures.as_completed(futures):
                try:
                    passage = future.result(timeout=180)
                    if passage:
                        passages.append(passage)
                except Exception as e:
                    logger.error(f"Passage generation failed: {e}")
                    raise RuntimeError(f"AI generation failed: {e}")

        if len(passages) != 3:
            raise RuntimeError(f"Only {len(passages)}/3 passages generated")

        # Sort by passage_number (thread completion order is random)
        passages.sort(key=lambda p: p.passage_number)

        from .reading_test import ReadingTest
        test = ReadingTest()
        test.passages = passages
        test.total_questions = sum(len(p.questions) for p in passages)
        test.id = str(uuid.uuid4())
        test.title = f"IELTS Reading Practice Test - {difficulty.title()}"
        return test

    # ═══════════════════════════════════════════════════════════════
    # Generate one passage + its questions
    # ═══════════════════════════════════════════════════════════════
    def _generate_single_passage(self, topic, difficulty, passage_num):
        content = self._ai_generate_passage(topic, difficulty, passage_num)
        if not content:
            raise RuntimeError(f"Empty content for {topic}")

        questions = self._generate_questions_from_content(
            content, passage_num, difficulty
        )

        # ═══════════════════════════════════════════════════════════
        # FIX 2 — GLOBAL question numbering (1–40, not 1–13 per passage)
        # ═══════════════════════════════════════════════════════════
        target = PASSAGE_QUESTION_TARGET.get(passage_num, 13)
        base = QUESTION_OFFSET.get(passage_num, 0)

        # Trim or pad to target
        if len(questions) > target:
            questions = questions[:target]
        elif len(questions) < target:
            while len(questions) < target:
                q = self._create_fallback_question(content, passage_num, difficulty)
                questions.append(q)

        # Renumber globally
        for idx, q in enumerate(questions):
            q.question_number = base + idx + 1
            q.id = f"q{base + idx + 1}"

        sorted_q = sorted(questions, key=lambda q: q.question_number)

        passage_obj = Passage(
            id=str(uuid.uuid4()),
            title=f"Reading Passage {passage_num}",
            content=content,
            difficulty=difficulty,
            topic=topic,
            word_count=len(content.split()),
            questions=sorted_q,
            passage_number=passage_num,
            estimated_time=20
        )
        logger.info(
            f"[Reading] Passage {passage_num} ({topic}, {difficulty}): "
            f"{len(content.split())} words, {len(sorted_q)} questions "
            f"(Q{base + 1}–Q{base + len(sorted_q)})"
        )
        return passage_obj

    # ═══════════════════════════════════════════════════════════════
    # FIX 3 — AI PASSAGE with strict word count
    # ═══════════════════════════════════════════════════════════════
    def _ai_generate_passage(self, topic, difficulty, passage_num):
        prompt = f"""Generate an IELTS Academic Reading passage on the topic "{topic}".

CRITICAL REQUIREMENTS (must follow exactly):

**WORD COUNT:** Exactly 850-950 words. Passages under 800 or over 1000 words will be REJECTED.
Count carefully before returning.

**STRUCTURE:** 5-6 paragraphs, each 140-180 words.

**STYLE:** Academic English, similar to Cambridge IELTS Academic books 11-18.
  - Complex sentence structures (relative clauses, conditionals, passive voice)
  - Academic vocabulary (e.g., "significant", "consequently", "phenomenon")
  - NO conversational phrases, NO first/second person ("I", "you", "we")

**CONTENT ELEMENTS (must include ALL):**
  - At least 3 specific statistics or research findings
  - At least 2 named researchers, experts, or institutions (fabricated but plausible)
  - At least 1 historical reference (year + event)
  - Cause-and-effect relationships
  - Contrasting viewpoints on the topic

**PARAGRAPH STRUCTURE:**
  - Para 1: Introduction + main thesis
  - Para 2-3: Supporting evidence / examples
  - Para 4: Contrasting perspective
  - Para 5: Implications / conclusion

**DO NOT:**
  - Include any title, heading, or subheading
  - Use bullet points or numbered lists
  - Include questions or prompts
  - Use first/second person

**Topic:** {topic}
**Difficulty:** {difficulty}

Return ONLY the passage text, starting directly with the first sentence."""

        response = self.ai.generate(prompt, max_tokens=2500, temperature=0.7)
        if not response or len(response.strip()) < 200:
            return None

        response = response.strip()

        # Auto-retry if passage is too short
        wc = len(response.split())
        if wc < 700:
            logger.warning(f"[Reading] Passage too short ({wc} words), retrying...")
            retry_prompt = prompt + f"\n\n IMPORTANT: Your previous attempt was only {wc} words. Expand to 850-950 words."
            response2 = self.ai.generate(retry_prompt, max_tokens=2800, temperature=0.7)
            if response2 and len(response2.split()) > wc:
                response = response2.strip()
                logger.info(f"[Reading] Retry successful: {len(response.split())} words")

        return response

    # ═══════════════════════════════════════════════════════════════
    # Type distribution — better coverage across 3 passages
    # ═══════════════════════════════════════════════════════════════
    def _get_required_types(self, passage_num: int) -> Dict[str, int]:
        """
         FIX 5 — Each passage now covers a broader mix of types,
        so the whole test hits 6+ distinct IELTS question types.
        """
        if passage_num == 1:
            # Passage 1 — factual / easy
            return {
                'true_false_not_given': 4,
                'multiple_choice': 3,
                'sentence_completion': 3,
                'short_answer': 3,
                'yes_no_not_given': 0,
                'matching_headings': 0,
                'matching_information': 0,
                'summary_completion': 0,
            }
        elif passage_num == 2:
            # Passage 2 — heading match + summary
            return {
                'matching_headings': 5,
                'true_false_not_given': 3,
                'multiple_choice': 2,
                'summary_completion': 3,
                'yes_no_not_given': 0,
                'sentence_completion': 0,
                'short_answer': 0,
                'matching_information': 0,
            }
        else:
            # Passage 3 — hardest, opinion-based
            return {
                'yes_no_not_given': 5,
                'matching_information': 4,
                'sentence_completion': 3,
                'multiple_choice': 2,
                'true_false_not_given': 0,
                'short_answer': 0,
                'matching_headings': 0,
                'summary_completion': 0,
            }

    # ═══════════════════════════════════════════════════════════════
    # AI QUESTION GENERATION
    # ═══════════════════════════════════════════════════════════════
    def _generate_questions_from_content(self, content, passage_num, difficulty):
        target = PASSAGE_QUESTION_TARGET.get(passage_num, 13)
        required_types = self._get_required_types(passage_num)

        type_instructions = []
        for qtype, count in required_types.items():
            if count > 0:
                type_instructions.append(f"- {count} questions of type '{qtype}'")
        type_instruction_str = "\n".join(type_instructions)

        # ═══════════════════════════════════════════════════════════
        # FIX 1 — FULL passage (no truncation)
        # ═══════════════════════════════════════════════════════════
        prompt = f"""You are an expert IELTS Reading question writer. Generate {target} questions in STRICT JSON format.

**REQUIRED QUESTION TYPES (exact counts):**
{type_instruction_str}

**CRITICAL RULES:**

1. **ANSWER SOURCE:** Every "correct_answer" MUST be a DIRECT QUOTE or NEAR-QUOTE from the passage.
   - Good: passage says "carbon emissions rose 27%" → answer: "27%"
   - Bad: answer contains words NOT present in the passage

2. **PARAGRAPH REFERENCE:** Must be accurate (1-6). Count paragraphs carefully.

3. **DIFFICULTY:** {difficulty}
   - easy → literal, direct statements
   - medium → requires simple inference
   - hard → complex inference, tone, vocabulary

4. **TYPE-SPECIFIC RULES:**

   **TRUE/FALSE/NOT GIVEN:**
     - TRUE: passage directly supports the statement
     - FALSE: passage directly contradicts the statement
     - NOT GIVEN: passage is silent on this
     - Statement must paraphrase a passage sentence

   **YES/NO/NOT GIVEN:**
     - About the author's CLAIM/OPINION, not a fact
     - Look for evaluative language ("should", "must", "important")

   **MULTIPLE CHOICE:**
     - Provide exactly 4 options (A, B, C, D)
     - Distractors must be plausible but wrong
     - Wrong options often contain true info from the WRONG paragraph

   **MATCHING HEADINGS:**
     - Provide 6 headings for the 4-5 paragraphs (2 extra as distractors)
     - Headings should be short (3-6 words)

   **SENTENCE COMPLETION:**
     - Blank must be filled with 1-3 words FROM the passage
     - Answer is a content word (not "the", "is", "of")

   **SHORT ANSWER:**
     - Answer is a short phrase (2-5 words) from the passage

   **MATCHING INFORMATION:**
     - Info must be explicitly stated in ONE specific paragraph

   **SUMMARY COMPLETION:**
     - Blanks filled with words/phrases FROM the passage

**OUTPUT FORMAT (STRICT JSON array):**
Each object must have:
- "type": one of (true_false_not_given, yes_no_not_given, multiple_choice, sentence_completion, short_answer, matching_headings, matching_information, summary_completion)
- "text" or "statement": the question text
- "correct_answer": string OR list of strings (for matching)
- "options": list of strings (only for multiple_choice, matching_headings, matching_information)
- "paragraph_reference": integer 1-6
- "explanation": optional short justification

**FULL PASSAGE:**
{content}

Return ONLY the JSON array. No markdown. No explanation. No extra text."""

        response = self.ai.generate(prompt, max_tokens=3500, temperature=0.1)

        questions = []
        try:
            raw = response.strip()
            # Strip markdown fences if AI added them
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                qs = json.loads(match.group())
            else:
                logger.warning("[Reading] No JSON array in AI response — fallback")
                return self._generate_fallback_questions(content, passage_num, difficulty)
        except Exception as e:
            logger.error(f"[Reading] JSON parse error: {e}")
            return self._generate_fallback_questions(content, passage_num, difficulty)

        all_types = [
            "true_false_not_given", "yes_no_not_given", "multiple_choice",
            "sentence_completion", "short_answer", "matching_headings",
            "matching_information", "summary_completion"
        ]

        for idx, qdict in enumerate(qs[:target]):
            qtype = qdict.get('type', 'multiple_choice')
            if qtype not in all_types:
                qtype = 'multiple_choice'

            q_text = qdict.get('text') or qdict.get('statement') or "Question"
            correct = qdict.get('correct_answer', '')

            # Normalize options to strings
            raw_options = qdict.get('options', [])
            normalized_options = self._normalize_options(raw_options)

            para_ref = f"Paragraph {qdict.get('paragraph_reference', random.randint(1, 5))}"

            question = Question(
                id=f"q{passage_num}_{idx + 1}",
                question_type=qtype,
                question_text=q_text,
                options=normalized_options,
                correct_answer=correct,
                explanation=qdict.get('explanation', ''),
                paragraph_reference=para_ref,
                question_number=idx + 1, # will be renumbered globally later
            )
            questions.append(question)

        # ═══════════════════════════════════════════════════════════
        # POST-VALIDATION — ensure required type counts
        # ═══════════════════════════════════════════════════════════
        type_counts = {}
        for q in questions:
            type_counts[q.question_type] = type_counts.get(q.question_type, 0) + 1

        for qtype, needed in required_types.items():
            if needed > 0 and type_counts.get(qtype, 0) < needed:
                missing_count = needed - type_counts.get(qtype, 0)
                for _ in range(missing_count):
                    fallback = self._create_fallback_question(
                        content, passage_num, difficulty, force_type=qtype
                    )
                    fallback.question_number = len(questions) + 1
                    questions.append(fallback)

        if len(questions) > target:
            questions = questions[:target]
        elif len(questions) < target:
            while len(questions) < target:
                fallback = self._create_fallback_question(
                    content, passage_num, difficulty
                )
                fallback.question_number = len(questions) + 1
                questions.append(fallback)

        return questions[:target]

    # ═══════════════════════════════════════════════════════════════
    # VERIFICATION (disabled for now — placeholder for future)
    # ═══════════════════════════════════════════════════════════════
    def _verify_question(self, question, passage):
        return True

    # ═══════════════════════════════════════════════════════════════
    # FALLBACK — used if AI JSON parse fails completely
    # ═══════════════════════════════════════════════════════════════
    def _generate_fallback_questions(self, content, passage_num, difficulty):
        target = PASSAGE_QUESTION_TARGET.get(passage_num, 13)
        questions = []
        required = self._get_required_types(passage_num)

        # First, honor required types
        for qtype, count in required.items():
            for _ in range(count):
                if len(questions) >= target:
                    break
                q = self._create_fallback_question(
                    content, passage_num, difficulty, force_type=qtype
                )
                questions.append(q)

        # Fill remaining with mixed
        while len(questions) < target:
            q = self._create_fallback_question(content, passage_num, difficulty)
            questions.append(q)

        return questions[:target]

    # ═══════════════════════════════════════════════════════════════
    # Fallback dispatcher
    # ═══════════════════════════════════════════════════════════════
    def _create_fallback_question(self, passage, passage_num, difficulty, force_type=None):
        if force_type:
            qtype = force_type
        else:
            types = (
                ['true_false_not_given'] * 3 + ['multiple_choice'] * 3 +
                ['sentence_completion'] * 2 + ['short_answer'] * 2 +
                ['matching_headings'] * 1 + ['matching_information'] * 1 +
                ['summary_completion'] * 1
            )
            qtype = random.choice(types)

        sentences = re.split(r'[.!?]\s+', passage)
        valid_sentences = [s for s in sentences if len(s.split()) > 10]
        if not valid_sentences:
            valid_sentences = [passage[:100]]

        if qtype == 'true_false_not_given':
            return self._create_tfng_fallback(passage, valid_sentences, passage_num)
        elif qtype == 'yes_no_not_given':
            return self._create_ynng_fallback(passage, valid_sentences, passage_num)
        elif qtype == 'multiple_choice':
            return self._create_mcq_fallback(valid_sentences, passage_num)
        elif qtype == 'sentence_completion':
            return self._create_sentence_completion_fallback(valid_sentences, passage_num)
        elif qtype == 'short_answer':
            return self._create_short_answer_fallback(valid_sentences, passage_num)
        elif qtype == 'matching_headings':
            return self._create_matching_headings_fallback(passage, passage_num)
        elif qtype == 'matching_information':
            return self._create_matching_information_fallback(passage, passage_num)
        elif qtype == 'summary_completion':
            return self._create_summary_completion_fallback(passage, passage_num)
        else:
            return self._create_mcq_fallback(valid_sentences, passage_num)

    # ═══════════════════════════════════════════════════════════════
    # Individual fallback creators
    # ═══════════════════════════════════════════════════════════════

    def _create_tfng_fallback(self, passage, sentences, passage_num):
        sent = random.choice(sentences)
        words = sent.split()
        if len(words) > 6:
            rnd = random.random()
            if rnd < 0.45:
                answer = "TRUE"
                statement = ' '.join(words[:random.randint(4, min(8, len(words)))]).capitalize()
                statement = statement.rstrip(',.') + "."
            elif rnd < 0.8:
                answer = "FALSE"
                statement = ' '.join(words[:random.randint(3, min(6, len(words)))]).capitalize()
                statement = "It is not the case that " + statement.lower().rstrip('.') + "."
            else:
                answer = "NOT GIVEN"
                statement = (
                    "The passage does not mention "
                    + ' '.join(words[:random.randint(3, min(5, len(words)))]).capitalize()
                    + "."
                )
        else:
            answer = "TRUE"
            statement = sent[:80] + "."
        if len(statement) > 100:
            statement = statement[:97] + "..."
        return Question(
            id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
            question_type="true_false_not_given",
            question_text=(
                "Do the following statements agree with the information "
                "given in the passage?\n\n"
                "Write TRUE if the statement agrees with the information, "
                "FALSE if the statement contradicts the information, or "
                "NOT GIVEN if there is no information on this.\n\n"
                f"{statement}"
            ),
            options=["TRUE", "FALSE", "NOT GIVEN"],
            correct_answer=answer,
            explanation="Based on passage content.",
            paragraph_reference=f"Paragraph {random.randint(1, 5)}"
        )

    def _create_ynng_fallback(self, passage, sentences, passage_num):
        q = self._create_tfng_fallback(passage, sentences, passage_num)
        q.question_type = 'yes_no_not_given'
        q.options = ["YES", "NO", "NOT GIVEN"]
        q.question_text = (
            "Do the following statements agree with the claims of the writer?\n\n"
            "Write YES if the statement agrees with the writer's claims, "
            "NO if the statement contradicts the writer's claims, or "
            "NOT GIVEN if it is impossible to say what the writer thinks.\n\n"
            + q.question_text.split('\n\n')[-1]
        )
        if q.correct_answer == "TRUE":
            q.correct_answer = "YES"
        elif q.correct_answer == "FALSE":
            q.correct_answer = "NO"
        return q

    def _create_mcq_fallback(self, sentences, passage_num):
        """
         FIX 4 — Meaningful MCQ with DISTINCT distractors.
        Distractors are: negation, number change, opposite claim.
        """
        if not sentences:
            sentences = ["The passage does not provide sufficient information."]

        correct_sent = random.choice(sentences)
        correct_text = correct_sent[:120].strip()

        distractors = []

        # D1 — Negation
        lowered = correct_sent.lower()
        if 'not ' in lowered:
            d1 = correct_sent.replace(' not ', ' ', 1)
            # Fix capitalization issue with simpler approach
            d1 = d1.strip()
        else:
            d1 = re.sub(r'\b(is|are|was|were)\b', r'\1 not', correct_sent, count=1)
        if d1 and d1 != correct_sent:
            distractors.append(d1[:120].strip())

        # D2 — Number change
        nums = re.findall(r'\b(\d+(?:\.\d+)?)\b', correct_sent)
        if nums:
            old = nums[0]
            try:
                if '.' in old:
                    new = str(round(float(old) * random.uniform(0.4, 0.6), 1))
                else:
                    new = str(int(old) + random.randint(2, 10))
                d2 = correct_sent.replace(old, new, 1)
                if d2 != correct_sent:
                    distractors.append(d2[:120].strip())
            except Exception:
                pass
        else:
            # No numbers — use word swap
            swaps = [('increase', 'decrease'), ('more', 'less'),
                     ('higher', 'lower'), ('rise', 'fall')]
            for a, b in swaps:
                if a in correct_sent.lower():
                    d2 = re.sub(a, b, correct_sent, count=1, flags=re.IGNORECASE)
                    if d2 != correct_sent:
                        distractors.append(d2[:120].strip())
                        break

        # D3 — Generic opposite claim
        distractors.append("The passage does not discuss this topic.")

        # Ensure we have at least 3 distractors
        pool = [s[:120].strip() for s in sentences if s.strip() != correct_sent.strip()]
        random.shuffle(pool)
        while len(distractors) < 3 and pool:
            candidate = pool.pop()
            if candidate not in distractors and candidate != correct_text:
                distractors.append(candidate)

        # Fallback safety
        while len(distractors) < 3:
            distractors.append("None of the above statements is supported.")

        options = [correct_text] + distractors[:3]
        random.shuffle(options)

        return Question(
            id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
            question_type="multiple_choice",
            question_text="According to the passage, which of the following is TRUE?",
            options=options,
            correct_answer=correct_text,
            explanation="Explicitly stated in the passage.",
            paragraph_reference=f"Paragraph {random.randint(1, 5)}"
        )

    def _create_sentence_completion_fallback(self, sentences, passage_num):
        sent = random.choice(sentences)
        words = sent.split()
        if len(words) > 8:
            content_words = [
                w for w in words
                if len(w) > 4 and w.lower() not in [
                    'that', 'this', 'with', 'from', 'they', 'have',
                    'been', 'their', 'them', 'would', 'could', 'should'
                ]
            ]
            if content_words:
                blank_word = random.choice(content_words).strip('.,;:!?')
                if blank_word:
                    q_text = sent.replace(blank_word, "__________", 1)
                    return Question(
                        id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
                        question_type="sentence_completion",
                        question_text=f"Complete the sentence with a word from the passage:\n\n{q_text}",
                        options=[],
                        correct_answer=blank_word,
                        explanation="Word found in the passage.",
                        paragraph_reference=f"Paragraph {random.randint(1, 5)}"
                    )
        return self._create_mcq_fallback(sentences, passage_num)

    def _create_short_answer_fallback(self, sentences, passage_num):
        sent = random.choice(sentences)
        words = sent.split()
        if len(words) > 6:
            start = random.randint(0, max(0, len(words) - 4))
            phrase = ' '.join(words[start:start + 3]).strip('.,;:!?')
            if len(phrase) > 4:
                return Question(
                    id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
                    question_type="short_answer",
                    question_text="What is mentioned about this topic in the passage? (Answer with a short phrase from the passage.)",
                    options=[],
                    correct_answer=phrase,
                    explanation="Short answer from the passage.",
                    paragraph_reference=f"Paragraph {random.randint(1, 5)}"
                )
        return self._create_mcq_fallback(sentences, passage_num)

    def _create_matching_headings_fallback(self, passage, passage_num):
        paragraphs = [p.strip() for p in passage.split('\n\n') if len(p) > 50]
        if len(paragraphs) < 3:
            sentences = re.split(r'[.!?]\s+', passage)
            paragraphs = [
                ' '.join(sentences[i:i + 3])
                for i in range(0, min(15, len(sentences)), 3)
            ]
            paragraphs = [p for p in paragraphs if len(p) > 50]

        headings = []
        for para in paragraphs[:4]:
            words = para.split()
            heading = ' '.join(words[:min(8, len(words))]).capitalize()
            if len(heading) > 30:
                heading = heading[:27] + "..."
            headings.append(heading)

        extra = ["General overview", "Historical context", "Future implications"]
        needed_extra = max(0, 6 - len(headings))
        all_headings = headings + random.sample(extra, min(needed_extra, len(extra)))
        random.shuffle(all_headings)

        options = [f"{chr(65 + i)}) {h}" for i, h in enumerate(all_headings)]
        correct = options[0] if options else "A) Main idea"

        return Question(
            id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
            question_type="matching_headings",
            question_text="Choose the correct heading for the first paragraph from the list of headings below.",
            options=options,
            correct_answer=correct,
            explanation="Best summarises the first paragraph.",
            paragraph_reference="Paragraph 1"
        )

    def _create_matching_information_fallback(self, passage, passage_num):
        sentences = re.split(r'[.!?]\s+', passage)
        valid = [s for s in sentences if len(s) > 30]
        if len(valid) >= 3:
            info = random.choice(valid)[:80]
            para_options = [f"Paragraph {i + 1}" for i in range(min(4, len(valid)))]
            correct = para_options[0] if para_options else "Paragraph 1"
            return Question(
                id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
                question_type="matching_information",
                question_text=f"Which paragraph contains the following information?\n\n{info}",
                options=para_options,
                correct_answer=correct,
                explanation="Explicitly stated in the indicated paragraph.",
                paragraph_reference="Paragraph 1"
            )
        return self._create_mcq_fallback(valid or [passage[:100]], passage_num)

    def _create_summary_completion_fallback(self, passage, passage_num):
        sentences = re.split(r'[.!?]\s+', passage)
        valid = [s for s in sentences if len(s.split()) > 10]
        if len(valid) >= 3:
            selected = random.sample(valid, min(3, len(valid)))
            blanks = []
            summary_parts = []
            for s in selected:
                words = s.split()
                content_words = [
                    w for w in words
                    if len(w) > 4 and w.lower() not in [
                        'that', 'this', 'with', 'from', 'they',
                        'have', 'been', 'their', 'them'
                    ]
                ]
                if content_words:
                    blank_word = random.choice(content_words).strip('.,;:!?')
                    if blank_word:
                        s_blank = s.replace(blank_word, "__________", 1)
                        blanks.append(blank_word)
                        summary_parts.append(s_blank)
            if blanks:
                summary = ' '.join(summary_parts)
                return Question(
                    id=f"q{passage_num}_{uuid.uuid4().hex[:4]}",
                    question_type="summary_completion",
                    question_text=(
                        "Complete the summary below using words from the passage.\n\n"
                        f"{summary}"
                    ),
                    options=[],
                    correct_answer=', '.join(blanks),
                    explanation="Missing words found in the passage.",
                    paragraph_reference=f"Paragraph {random.randint(1, 5)}"
                )
        return self._create_mcq_fallback(valid, passage_num)