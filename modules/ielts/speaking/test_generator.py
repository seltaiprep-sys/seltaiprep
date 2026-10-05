"""IELTS Speaking Test Generator - Graceful degradation with AI and optional audio

FIX:
  (1) All generation methods now retry 3 times with varied temperature
  (2) Failures return error dicts instead of raising ValueError
  (3) Added _validate_test() to ensure generated JSON has required fields
  (4) Added _safe_str() to strip and reject empty strings
  (5) total_questions now counts string-form questions correctly
  (6) Added _pick_topic() to avoid repeating recent topics
  (7) Per-part generation (part1/part2/part3) also retries
  (8) generate_complete_test_with_audio() degrades gracefully without a voice
  (9) Added create_speaking_test() alias — required by api.py blueprint
"""

import os
import json
import re
import logging
import random
import time
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)
# ═══════════════════════════════════════════════════════════════
# EXAMINER NAME POOL — random pick per test session
# ═══════════════════════════════════════════════════════════════
EXAMINER_NAMES = [
    "Sarah Mitchell",
    "James Anderson",
    "Emma Thompson",
    "Michael Roberts",
    "Olivia Bennett",
    "David Harrison",
    "Sophie Clarke",
    "Daniel Wright",
    "Rachel Foster",
    "Thomas Hughes",
]

def pick_examiner_name() -> str:
    """Return a random IELTS examiner name for this session."""
    import random
    return random.choice(EXAMINER_NAMES)

def fill_examiner_placeholders(data, name: str = None):
    """
    Recursively replace [Examiner Name] / [Examiner] placeholders.
    Handles dict, list, str.
    """
    if name is None:
        name = pick_examiner_name()
    if isinstance(data, str):
        return (data
            .replace("[Examiner Name]", name)
            .replace("[Examiner name]", name)
            .replace("[Examiner]", name)
            .replace("[EXAMINER]", name))
    if isinstance(data, dict):
        return {k: fill_examiner_placeholders(v, name) for k, v in data.items()}
    if isinstance(data, list):
        return [fill_examiner_placeholders(v, name) for v in data]
    return data




# ============================================================
# Constants
# ============================================================

PART1_TOPIC_POOL = [
    "hometown", "home", "family", "work", "study", "travel",
    "food", "technology", "hobbies", "music", "sports",
    "reading", "movies", "shopping", "weather", "health",
    "animals", "festivals", "transport", "environment",
    "education", "friends", "childhood", "daily routine",
]

PART2_TOPIC_POOL = [
    "a memorable trip", "a person you admire", "an important decision",
    "a difficult challenge", "a useful skill", "a happy memory",
    "a favourite book", "a piece of technology", "a public place",
    "a special gift", "an interesting job", "a cultural tradition",
    "a piece of music", "a childhood friend", "a life lesson",
]

TARGET_TOTAL_QUESTIONS = {
    "easy": 12,
    "medium": 14,
    "hard": 16,
}

MAX_GENERATION_ATTEMPTS = 3
ATTEMPT_TEMPERATURES = [0.7, 0.85, 0.6]


# ============================================================
# Helpers
# ============================================================


# ═══════════════════════════════════════════════════════════════
# SANITIZER — catches literal placeholder leaks from AI output
# ═══════════════════════════════════════════════════════════════
import re as _re

_PLACEHOLDER_PATTERNS = [
    _re.compile(r'^Generate .+? question \d', _re.IGNORECASE),
    _re.compile(r'^Generate question', _re.IGNORECASE),
    _re.compile(r'^REAL .+ question', _re.IGNORECASE),
    _re.compile(r'\[something interesting\]', _re.IGNORECASE),
    _re.compile(r'\[simple question\]', _re.IGNORECASE),
    _re.compile(r'\[Examiner\]'),
    _re.compile(r'\[Examiner Name\]', _re.IGNORECASE),
    _re.compile(r'\{selected_topic\}'),
    _re.compile(r'\{theme\}'),
    _re.compile(r'Generate .+ about \{', _re.IGNORECASE),
]

def _looks_like_placeholder(text: str) -> bool:
    """Return True if text contains literal placeholder garbage."""
    if not text or not isinstance(text, str):
        return True
    s = text.strip()
    if len(s) < 8:
        return True
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(s):
            return True
    return False

def sanitize_questions(data):
    """Remove questions that still contain placeholder text. Recursive."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k == 'questions' and isinstance(v, list):
                out[k] = [q for q in v if not (
                    isinstance(q, dict) and _looks_like_placeholder(q.get('question', ''))
                )]
            elif k == 'prompts' and isinstance(v, list):
                out[k] = [p for p in v if not _looks_like_placeholder(str(p))]
            else:
                out[k] = sanitize_questions(v)
        return out
    if isinstance(data, list):
        return [sanitize_questions(v) for v in data]
    return data


def _safe_str(value: Any, default: str = "") -> str:
    """Return stripped string if value is a non-empty string, else default."""
    if value is None:
        return default
    if not isinstance(value, str):
        return default
    v = value.strip()
    return v if v else default


def _count_valid_questions(qs) -> int:
    """Count real questions in a mixed list of dicts and strings."""
    n = 0
    for q in qs or []:
        if isinstance(q, str):
            if _safe_str(q):
                n += 1
        elif isinstance(q, dict):
            if _safe_str(q.get('question') or q.get('text')):
                n += 1
    return n


def _validate_test(result: Dict) -> Optional[str]:
    """Validate that a generated test has all required structure."""
    if not isinstance(result, dict):
        return "Result is not a dict"

    part1 = result.get('part1')
    if not isinstance(part1, dict):
        return "Missing or invalid part1"

    warmup_qs = (part1.get('warmup_section') or {}).get('questions') or []
    topic_qs = (part1.get('topic_section') or {}).get('questions') or []

    warmup_n = _count_valid_questions(warmup_qs)
    topic_n = _count_valid_questions(topic_qs)

    if warmup_n < 2:
        return f"Part 1 has too few warmup questions ({warmup_n})"
    if topic_n < 3:
        return f"Part 1 has too few topic questions ({topic_n})"

    part2 = result.get('part2')
    if not isinstance(part2, dict):
        return "Missing or invalid part2"

    topic_card = part2.get('topic_card') or {}
    title = _safe_str(topic_card.get('title'))
    prompts = topic_card.get('prompts') or []

    if not title:
        return "Part 2 topic_card has no title"

    valid_prompts = [p for p in prompts if _safe_str(p)]
    if len(valid_prompts) < 3:
        return f"Part 2 topic_card has too few prompts ({len(valid_prompts)})"

    part3 = result.get('part3')
    if not isinstance(part3, dict):
        return "Missing or invalid part3"

    part3_qs = part3.get('questions') or []
    part3_n = _count_valid_questions(part3_qs)
    if part3_n < 3:
        return f"Part 3 has too few questions ({part3_n})"

    return None


def _count_questions(result: Dict) -> int:
    """Count real questions in a full test."""
    count = 0

    part1 = result.get('part1') or {}
    count += _count_valid_questions((part1.get('warmup_section') or {}).get('questions'))
    count += _count_valid_questions((part1.get('topic_section') or {}).get('questions'))

    count += 1 # Part 2 cue card

    part3 = result.get('part3') or {}
    count += _count_valid_questions(part3.get('questions'))

    return count


# ============================================================
# SpeakingTest
# ============================================================

class SpeakingTest:
    """
    Generate IELTS Speaking tests using an AI engine.

    Features:
    - Full test with all 3 parts (Part 1, Part 2, Part 3)
    - Individual part generation
    - Optional audio via ExaminerVoice
    - Retry with varying temperature on failure
    - Robust JSON extraction + structural validation
    - Graceful degradation (never raises on generation failure)
    """

    def __init__(
        self,
        ai_engine: Any,
        voice: Optional[Any] = None,
        audio_cache_dir: str = "static/audio_cache/speaking",
    ):
        if not ai_engine:
            raise ValueError("AI Engine is required. No fallback templates available.")
        self.ai = ai_engine
        self.voice = voice
        self.audio_cache_dir = audio_cache_dir
        os.makedirs(self.audio_cache_dir, exist_ok=True)
        self._recent_topics: List[str] = []
        logger.info("SpeakingTest initialized with AI engine and voice: %s", voice is not None)

    # ============================================================
    # JSON EXTRACTION
    # ============================================================

    def _extract_json(self, response: str) -> Dict:
        """Extract JSON from AI response with robust cleaning."""
        if not response:
            raise ValueError("AI returned empty response")

        json_str = re.sub(r'```(?:json)?\s*|```\s*', '', response.strip())

        start = json_str.find('{')
        end = json_str.rfind('}')
        if start == -1 or end == -1:
            raise ValueError(f"No JSON found in AI response: {response[:200]}")

        json_str = json_str[start:end + 1]
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

        open_b = json_str.count('{') - json_str.count('}')
        open_s = json_str.count('[') - json_str.count(']')
        if open_b > 0:
            json_str += '}' * open_b
        if open_s > 0:
            json_str += ']' * open_s

        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            clean_json = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', json_str)
            try:
                return json.loads(clean_json, strict=False)
            except json.JSONDecodeError as e2:
                raise ValueError(
                    f"Failed to parse AI JSON after cleaning: {e2}\n"
                    f"Response: {json_str[:300]}"
                )

    # ============================================================
    # TOPIC PICKING
    # ============================================================

    def _pick_topic(self, pool: List[str]) -> str:
        """Pick a topic, avoiding ones used in the last 3 calls."""
        available = [t for t in pool if t not in self._recent_topics[-3:]]
        if not available:
            available = pool
        choice = random.choice(available)
        self._recent_topics.append(choice)
        self._recent_topics = self._recent_topics[-5:]
        return choice

    # ============================================================
    # FULL TEST
    # ============================================================

    def generate_full_speaking_test(
        self,
        difficulty: str = "medium",
        topic: str = None,
    ) -> Dict:
        """Generate a COMPLETE speaking test with all 3 parts."""
        if not self.ai:
            return {
                'error': 'AI Engine required. Cannot generate speaking test without AI.',
                'success': False,
            }

        selected_topic = _safe_str(topic) or self._pick_topic(PART1_TOPIC_POOL)
        prompt = self._build_full_test_prompt(difficulty, selected_topic)

        last_error = None
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            temperature = ATTEMPT_TEMPERATURES[attempt % len(ATTEMPT_TEMPERATURES)]
            try:
                response = self.ai.generate(
                    prompt, max_tokens=2500, temperature=temperature
                )
                result = self._extract_json(response)

                validation_error = _validate_test(result)
                if validation_error:
                    last_error = validation_error
                    logger.warning(
                        f"Attempt {attempt+1}: validation failed — {validation_error}"
                    )
                    continue

                result['total_questions'] = _count_questions(result)
                result['ai_generated'] = True
                result['difficulty'] = difficulty
                result['part1_topic'] = selected_topic
                result['success'] = True

                logger.info(
                    f" Speaking test generated "
                    f"(attempt {attempt+1}, {result['total_questions']} questions)"
                )
                return sanitize_questions(fill_examiner_placeholders(result))

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt+1} failed: {e}")
                if attempt < MAX_GENERATION_ATTEMPTS - 1:
                    time.sleep(0.5)

        logger.error(
            f"Speaking test generation failed after {MAX_GENERATION_ATTEMPTS} attempts: {last_error}"
        )
        return {
            'error': f'Speaking test generation failed: {last_error}',
            'success': False,
        }

    def _build_full_test_prompt(self, difficulty: str, topic: str) -> str:
        """Build the prompt for generating a complete test."""
        target_q = TARGET_TOTAL_QUESTIONS.get(difficulty, 14)
        return f"""You are an IELTS Speaking Examiner. Generate a COMPLETE speaking test with ALL 3 PARTS in order.

Difficulty: {difficulty}
Main Topic for Part 1: {topic}
Target total questions: approximately {target_q}

IMPORTANT: The test MUST follow this exact structure with all 3 parts:

PART 1 (4-5 minutes) - MUST include:
- Examiner greeting and introduction
- Name and ID check
- Test explanation
- Warmup questions about the candidate (at least 3)
- Topic-specific questions about {topic} (at least 5)

PART 2 (3-4 minutes) - MUST include:
- Examiner instructions for the long turn
- A UNIQUE cue card topic (DIFFERENT from Part 1 topic)
- 4 prompts to guide the candidate
- Preparation time (60 seconds)
- Speaking time (120 seconds)
- Follow-up question

PART 3 (4-5 minutes) - MUST include:
- Transition to abstract discussion
- 6-8 analytical questions related to Part 2 topic
- Closing remarks

Return ONLY valid JSON with this EXACT structure:

{{
    "test_title": "IELTS Speaking Test",
    "difficulty": "{difficulty}",
    "part1_topic": "{topic}",

    "part1": {{
        "examiner_greeting": "Good morning/afternoon. My name is [Examiner Name]. Can you tell me your full name, please?",
        "examiner_name_follow_up": "What would you like me to call you?",
        "examiner_id_check": "Could you show me your identification, please?",
        "examiner_thank_you": "Thank you. That's perfect.",
        "examiner_test_explanation": "Now, before we begin the test, let me explain how it works. I will ask you questions in three parts. Part 1 is about yourself and familiar topics. Part 2 is a long turn where you will speak for 1-2 minutes on a given topic. Part 3 is a discussion of more abstract ideas. Are you ready?",
        "examiner_begin": "Excellent. Let's begin.",

        "warmup_section": {{
            "examiner_transition": "Now, I'd like to ask you some questions about yourself.",
            "questions": [
                {{"question": "Where are you from originally?", "type": "description"}},
                {{"question": "Do you work or are you studying at the moment?", "type": "work_study"}},
                {{"question": "What do you enjoy most about your work or studies?", "type": "opinion"}}
            ]
        }},

        "topic_section": {{
            "examiner_transition": "Now, let's talk about {topic}.",
            "questions": [
                {{"question": "Generate a natural opening question about {topic}", "type": "opinion"}},
                {{"question": "Generate a second question about {topic}", "type": "description"}},
                {{"question": "Generate a comparison question about {topic}", "type": "comparison"}},
                {{"question": "Generate an experience question about {topic}", "type": "experience"}},
                {{"question": "Generate a preference question about {topic}", "type": "preference"}}
            ]
        }},

        "examiner_part1_closing": "Thank you. That's the end of Part 1. Now we'll move to Part 2."
    }},

    "part2": {{
        "examiner_intro": "Now, I'm going to give you a topic and I'd like you to talk about it for one to two minutes.",
        "examiner_instructions": "Before you speak, you'll have one minute to think about what you're going to say. You can make some notes if you wish.",
        "examiner_card_presentation": "Here is your topic card.",

        "topic_card": {{
            "title": "Describe [a unique topic NOT related to Part 1]",
            "prompts": [
                "first prompt question",
                "second prompt question",
                "third prompt question",
                "and explain final prompt question"
            ]
        }},

        "examiner_preparation_start": "Alright? You have one minute to prepare.",
        "examiner_preparation_end": "OK. Your preparation time is up.",
        "examiner_speaking_start": "Please start speaking now.",
        "examiner_speaking_end": "Thank you. Can I have the topic card back, please?",
        "examiner_follow_up_question": "Just one quick question: [simple follow-up]",

        "preparation_time": 60,
        "speaking_time": 120
    }},

    "part3": {{
        "examiner_intro": "Let's discuss some more general questions related to this topic.",
        "examiner_transition": "I'd like to ask you a few more abstract questions now.",
        "theme": "[theme based on Part 2 topic]",

        "questions": [
            {{"question": "Generate analytical question 1", "type": "analysis"}},
            {{"question": "Generate comparison question 2", "type": "comparison"}},
            {{"question": "Generate evaluation question 3", "type": "evaluation"}},
            {{"question": "Generate prediction question 4", "type": "prediction"}},
            {{"question": "Generate opinion question 5", "type": "opinion"}},
            {{"question": "Generate critical question 6", "type": "critical"}}
        ],

        "examiner_closing": "Thank you. That is the end of the speaking test.",
        "examiner_final": "You can relax now. Your results will be available shortly."
    }},

    "total_duration": "11-14 minutes"
}}

CRITICAL REQUIREMENTS:
1. Part 1 MUST come BEFORE Part 2 in the output
2. Part 2 topic MUST be different from Part 1 topic
3. Part 3 questions MUST relate to Part 2 topic
4. Generate UNIQUE, REALISTIC questions for each part
5. Use natural, professional examiner language
6. EVERY question must have a "question" field with actual text

Generate a COMPLETE test with ALL THREE PARTS now. Return ONLY valid JSON."""

    # ============================================================
    # INDIVIDUAL PART GENERATORS
    # ============================================================

    def generate_part1(self, topic: str = None, difficulty: str = "medium") -> Dict:
        """Generate ONLY Part 1 questions. Retries 3 times."""
        if not self.ai:
            return {'error': 'AI Engine required.', 'success': False}

        selected_topic = _safe_str(topic) or self._pick_topic(PART1_TOPIC_POOL)
        prompt = f"""You are a certified IELTS Speaking Examiner (British English).

TARGET: Candidates aiming for Band 6.5-8.0.
STYLE: Natural examiner phrasing — same as real Cambridge IELTS Speaking tests.

Topic: {selected_topic}
Difficulty: {difficulty}

TASK: Generate Part 1 (Introduction + Interview). Output ONLY the JSON below.

CRITICAL RULES:
  1. Every "question" field must contain a REAL, grammatically complete question
     that an examiner would actually say out loud.
  2. NEVER output instruction text like "Generate question 1" — those are placeholders,
     not real questions.
  3. Use natural British English. No markdown. No extra text outside JSON.

EXAMPLES of real Part 1 questions (do NOT copy verbatim):
  • "Where is your hometown?"
  • "What do you like most about your hometown?"
  • "Has your hometown changed much in recent years?"

Return ONLY valid JSON with this EXACT structure:

{{
    "examiner_greeting": "Good morning/afternoon. My name is [Examiner]. Can you tell me your full name, please?",
    "examiner_name_follow_up": "What can I call you?",
    "examiner_id_check": "Can I see your identification, please?",
    "examiner_thank_you": "Thank you.",
    "examiner_test_explanation": "Now, let me explain how the test works... Are you ready?",
    "examiner_begin": "Excellent. Let's begin.",
    "warmup_section": {{
        "questions": [
            {{"question": "REAL question about the candidate (e.g. 'Where are you from?')", "type": "description"}},
            {{"question": "REAL question about work/study (e.g. 'Do you work or study?')", "type": "work_study"}},
            {{"question": "REAL question about daily life (e.g. 'What do you usually do in the evenings?')", "type": "daily_life"}}
        ]
    }},
    "topic_section": {{
        "questions": [
            {{"question": "REAL question 1 about {selected_topic} — opinion style", "type": "opinion"}},
            {{"question": "REAL question 2 about {selected_topic} — description style", "type": "description"}},
            {{"question": "REAL question 3 about {selected_topic} — comparison style", "type": "comparison"}},
            {{"question": "REAL question 4 about {selected_topic} — experience style", "type": "experience"}},
            {{"question": "REAL question 5 about {selected_topic} — preference style", "type": "preference"}}
        ]
    }},
    "examiner_closing": "Thank you. That's the end of Part 1."
}}

Generate NATURAL, REALISTIC questions. Return ONLY valid JSON."""

        last_error = None
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            try:
                response = self.ai.generate(
                    prompt,
                    max_tokens=1200,
                    temperature=ATTEMPT_TEMPERATURES[attempt % len(ATTEMPT_TEMPERATURES)],
                )
                result = self._extract_json(response)

                warmup = (result.get('warmup_section') or {}).get('questions') or []
                topic_qs = (result.get('topic_section') or {}).get('questions') or []
                if _count_valid_questions(warmup) < 2 or _count_valid_questions(topic_qs) < 4:
                    last_error = (
                        f"Part1 missing questions "
                        f"(warmup={_count_valid_questions(warmup)}, "
                        f"topic={_count_valid_questions(topic_qs)})"
                    )
                    continue

                result['success'] = True
                result['topic'] = selected_topic
                return sanitize_questions(fill_examiner_placeholders(result))
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Part1 attempt {attempt+1} failed: {e}")

        return {
            'error': f'Part 1 generation failed: {last_error}',
            'success': False,
        }

    def generate_part2(self, category: str = None, difficulty: str = "medium") -> Dict:
        """Generate ONLY Part 2 cue card. Retries 3 times."""
        if not self.ai:
            return {'error': 'AI Engine required.', 'success': False}

        prompt = f"""You are a certified IELTS Speaking Examiner (British English).

TARGET: Candidates aiming for Band 6.5-8.0.
STYLE: Natural examiner phrasing — same as real Cambridge IELTS Part 2 cue cards.

Difficulty: {difficulty}

TASK: Generate Part 2 (Long Turn — cue card). Output ONLY the JSON below.

CRITICAL RULES:
  1. "title" MUST be a complete, real IELTS cue-card topic.
     GOOD: "Describe a book you recently read"
     BAD : "Describe [something interesting]"  ← placeholder, NOT allowed
  2. "prompts" MUST be 4 specific bullet sub-points (what/where/when/why/how).
  3. "examiner_follow_up_question" MUST be a complete, real follow-up question.
     GOOD: "Do you often read books like that?"
     BAD : "Just one quick question: [simple question]"  ← placeholder, NOT allowed
  4. Use natural British English. No markdown. No extra text outside JSON.

Return ONLY valid JSON with this EXACT structure:

{{
    "examiner_intro": "Now, I'm going to give you a topic to talk about for 1-2 minutes.",
    "examiner_instructions": "You have one minute to prepare. You can make notes.",
    "examiner_card_presentation": "Here is your topic card.",
    "topic_card": {{
        "title": "Describe a REAL specific topic (e.g. 'a book you recently read')",
        "prompts": [
            "what it is",
            "where/when it happened",
            "what happened or who was involved",
            "why it is memorable or important to you"
        ]
    }},
    "examiner_preparation_start": "You have one minute. Please prepare.",
    "examiner_preparation_end": "Your preparation time is up.",
    "examiner_speaking_start": "OK. Please start speaking now.",
    "examiner_speaking_end": "Thank you. Can I have the card back?",
    "examiner_follow_up_question": "A REAL short follow-up question about the topic above",
    "preparation_time": 60,
    "speaking_time": 120
}}

Generate a UNIQUE, REAL IELTS topic. Return ONLY valid JSON."""

        last_error = None
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            try:
                response = self.ai.generate(
                    prompt,
                    max_tokens=800,
                    temperature=ATTEMPT_TEMPERATURES[attempt % len(ATTEMPT_TEMPERATURES)],
                )
                result = self._extract_json(response)

                tc = result.get('topic_card') or {}
                if not _safe_str(tc.get('title')) or len([
                    p for p in (tc.get('prompts') or []) if _safe_str(p)
                ]) < 4:
                    last_error = "Part2 topic_card incomplete (need ≥4 prompts)"
                    continue

                result['success'] = True
                return sanitize_questions(fill_examiner_placeholders(result))
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Part2 attempt {attempt+1} failed: {e}")

        return {
            'error': f'Part 2 generation failed: {last_error}',
            'success': False,
        }

    def generate_part3(self, part2_topic: str = None, difficulty: str = "medium") -> Dict:
        """Generate ONLY Part 3 discussion questions. Retries 3 times."""
        if not self.ai:
            return {'error': 'AI Engine required.', 'success': False}

        theme = _safe_str(part2_topic) or "modern life and society"
        prompt = f"""You are a certified IELTS Speaking Examiner (British English).

TARGET: Candidates aiming for Band 6.5-8.0.
STYLE: Natural examiner phrasing — same as real Cambridge IELTS Part 3 discussion.

Theme: {theme}
Difficulty: {difficulty}

TASK: Generate Part 3 (Two-way Discussion). Output ONLY the JSON below.

CRITICAL RULES:
  1. Every "question" must be a REAL, complete, abstract/analytical question
     that builds on the Part 2 theme.
  2. NEVER output instruction text like "Generate analytical question 1" —
     those are placeholders, not real questions.
  3. Each question must encourage a longer, abstract answer (not yes/no).
  4. Use natural British English. No markdown. No extra text outside JSON.

EXAMPLES of real Part 3 questions (do NOT copy verbatim):
  • "Why do you think some people find it difficult to do X?"
  • "How has X changed compared to the past?"
  • "Do you think X will become more common in the future? Why?"

Return ONLY valid JSON:

{{
    "examiner_intro": "Let's discuss some more general questions.",
    "examiner_transition": "I'd like to ask you some abstract questions now.",
    "theme": "{theme}",
    "questions": [
        {{"question": "REAL analytical question about {theme}", "type": "analysis"}},
        {{"question": "REAL comparison question about {theme}", "type": "comparison"}},
        {{"question": "REAL evaluation question about {theme}", "type": "evaluation"}},
        {{"question": "REAL prediction question about {theme}", "type": "prediction"}},
        {{"question": "REAL opinion question about {theme}", "type": "opinion"}},
        {{"question": "REAL critical thinking question about {theme}", "type": "critical"}}
    ],
    "examiner_closing": "Thank you. That is the end of the speaking test."
}}

Generate 6 ABSTRACT, ANALYTICAL, REAL questions. Return ONLY valid JSON."""

        last_error = None
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            try:
                response = self.ai.generate(
                    prompt,
                    max_tokens=1000,
                    temperature=ATTEMPT_TEMPERATURES[attempt % len(ATTEMPT_TEMPERATURES)],
                )
                result = self._extract_json(response)

                qs = result.get('questions') or []
                if _count_valid_questions(qs) < 4:
                    last_error = f"Part3 has only {_count_valid_questions(qs)} valid questions (need ≥4)"
                    continue

                result['success'] = True
                return sanitize_questions(fill_examiner_placeholders(result))
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Part3 attempt {attempt+1} failed: {e}")

        return {
            'error': f'Part 3 generation failed: {last_error}',
            'success': False,
        }

    # ============================================================
    # COMPLETE TEST WRAPPER
    # ============================================================

    def generate_complete_test(
        self,
        difficulty: str = "medium",
        topic: str = None,
        exam_type: str = "ielts",
    ) -> Dict:
        """Generate complete test with all 3 parts. Returns error dict on failure."""
        if not self.ai:
            return {'error': 'AI Engine required.', 'success': False}

        logger.info(f"Generating complete speaking test: difficulty={difficulty}, topic={topic}")

        result = self.generate_full_speaking_test(difficulty, topic)

        if not result.get('success'):
            return result

        return {
            "title": "IELTS Speaking Test",
            "difficulty": difficulty,
            "exam_type": exam_type,
            "total_duration": "11-14 minutes",
            **result,
            "has_audio": False,
            "ai_generated": True,
            "parts_included": ["part1", "part2", "part3"],
        }

    def generate_complete_test_with_audio(
        self,
        difficulty: str = "medium",
        topic: Optional[str] = None,
        exam_type: str = "ielts",
    ) -> Dict:
        """Generate complete test with audio files. Degrades gracefully."""
        test = self.generate_complete_test(difficulty, topic, exam_type)

        if not test.get('success'):
            return test

        if not self.voice:
            test['has_audio'] = False
            test['audio_warning'] = 'No ExaminerVoice provided'
            return test

        try:
            logger.info("Generating examiner audio...")
            audio = self.voice.generate_full_test_audio(test)
            test['audio_files'] = audio
            test['has_audio'] = True
            total = sum(len(v) for v in audio.values())
            logger.info(f"Generated {total} audio files")
        except Exception as e:
            logger.warning(f"Audio generation failed: {e}")
            test['has_audio'] = False
            test['audio_error'] = str(e)

        return test


# ============================================================
# FACTORY FUNCTIONS
# ============================================================

def create_speaking_test_generator(
    ai_engine: Any,
    voice: Optional[Any] = None,
    audio_cache_dir: str = "static/audio_cache/speaking",
) -> SpeakingTest:
    """Factory function to create a SpeakingTest instance."""
    return SpeakingTest(ai_engine, voice, audio_cache_dir)


# NEW: alias used by modules/ielts/speaking/api.py and app.py
def create_speaking_test(
    ai_engine: Any,
    voice: Optional[Any] = None,
    audio_cache_dir: str = "static/audio_cache/speaking",
) -> SpeakingTest:
    """
    Backwards-compatible alias for create_speaking_test_generator.

    Some modules (e.g. speaking/api.py, speaking/__init__.py) import
    `create_speaking_test`. This alias keeps those imports working
    without duplicating logic.
    """
    return SpeakingTest(ai_engine, voice, audio_cache_dir)


__all__ = [
    'SpeakingTest',
    'create_speaking_test_generator',
    'create_speaking_test', # NEW
    'PART1_TOPIC_POOL',
    'PART2_TOPIC_POOL',
]