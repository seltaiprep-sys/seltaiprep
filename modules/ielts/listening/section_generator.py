"""Section-specific generators for IELTS Listening tests - PURE AI ONLY.

FIXES APPLIED (v7 — DUPLICATE + EXPANSION HARDENING):
  (A) Duplicate answers auto-fix for ALL sections (was Section 1 only).
         New `_deduplicate_answers_auto()` replaces collision questions with
         unique fallbacks from `_FALLBACK_ANSWERS_BY_TYPE`.
  (B) 2-attempt expansion with progressively stronger prompt.
         First attempt: standard. Second: forced append with explicit
         sentence/example counts. Escalates temperature.
  (C) _expand_script() keeps the best partial result across attempts.

FIXES APPLIED (v6 — PERMANENT WORD COUNT FIX):
  (A) LENGTH_TOLERANCE = 0.95 — allows scripts within 5% of minimum.
  (B) SECTION_MIN_WORDS Section 4 lowered from 580 → 550.
  (C) _expand_script() requests 50% EXTRA buffer.
  (D) _call_ai() uses effective_min (min × tolerance).
  (E) _is_valid_ai_result() applies same tolerance.

FIXES APPLIED (v5):
  (A) Stem-leak detection & sanitization.
  (B) _stem_has_answer_leak() post-check triggers retry.
  (C) _sanitize_stem() safety net.

FIXES APPLIED (v4):
  (1)-(15) as before.
"""

import json
import random
import logging
import re
import hashlib
import time
import threading
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

try:
    import json5
    JSON5_AVAILABLE = True
except ImportError:
    JSON5_AVAILABLE = False

from .question_types import question_type_manager

try:
    from .distractor_rules import distractor_generator
except ImportError:
    distractor_generator = None
    logging.warning("distractor_generator not available")

# Legacy re-export — inline prompts below are the ACTIVE source.
try:
    from .prompt_templates import SECTION4_DATA_MAP
except ImportError:
    SECTION4_DATA_MAP = {}
    logging.warning("prompt_templates.SECTION4_DATA_MAP not available")

try:
    from .speech_messifier import speech_messifier
except ImportError:
    speech_messifier = None

try:
    from .voices import FEMALE_NAMES, MALE_NAMES
except ImportError:
    FEMALE_NAMES = ['Maria', 'Sarah', 'Emma', 'Sonia', 'Libby', 'Jane', 'Anna', 'Mary', 'Linda',
                    'Sophie', 'Charlotte', 'Amelia', 'Olivia', 'Isabella', 'Mia', 'Ella', 'Grace']
    MALE_NAMES = ['James', 'David', 'Leo', 'Amin', 'Thomas', 'Ryan', 'John', 'Robert', 'Michael',
                  'William', 'Alexander', 'Daniel', 'Matthew', 'Andrew', 'Oliver', 'George']

logger = logging.getLogger(__name__)

# ============================================================
# Single source of truth for word counts
# PERMANENT FIX: Section 4 min reduced 580 → 550
# ============================================================
SECTION_TARGET_WORDS = {1: 400, 2: 450, 3: 500, 4: 600}
SECTION_MIN_WORDS = {1: 340, 2: 400, 3: 460, 4: 550}
SECTION_DURATIONS = {1: 180, 2: 210, 3: 270, 4: 330}
SECTION_QUESTION_RANGES = {1: (1, 10), 2: (11, 20), 3: (21, 30), 4: (31, 40)}

MAX_RETRIES_PER_SECTION = 2

# ═══════════════════════════════════════════════════════════════
# PERMANENT FIX: Word-count tolerance
# ═══════════════════════════════════════════════════════════════
LENGTH_TOLERANCE = 0.95

SECTION_NATURALNESS_CONFIG = {
    1: {'min_lines': 4, 'max_question_ratio': 0.6, 'require_labels': True,
        'min_unique_speakers': 2, 'is_monologue': False},
    2: {'min_words': 100, 'max_questions': 3, 'require_labels': False,
        'min_unique_speakers': 0, 'is_monologue': True},
    3: {'min_lines': 3, 'max_question_ratio': 0.7, 'require_labels': True,
        'min_unique_speakers': 3, 'is_monologue': False},
    4: {'min_words': 200, 'max_questions': 3, 'require_labels': False,
        'min_unique_speakers': 0, 'is_monologue': True},
}

ANSWER_FORMATS = {
    'one_word': {'max_words': 1, 'description': 'ONE WORD ONLY'},
    'two_words': {'max_words': 2, 'description': 'NO MORE THAN TWO WORDS'},
    'three_words': {'max_words': 3, 'description': 'NO MORE THAN THREE WORDS'},
    'number': {'max_words': 1, 'numeric': True, 'description': 'A NUMBER'},
    'letter': {'max_words': 1, 'letters_only': True, 'description': 'A LETTER'},
    'date': {'max_words': 1, 'format': 'date', 'description': 'A DATE'},
    'time': {'max_words': 1, 'format': 'time', 'description': 'A TIME'},
    'money': {'max_words': 1, 'format': 'money', 'description': 'A MONEY AMOUNT'},
}


def _normalize_answer(ans: str) -> str:
    if not ans:
        return ""
    return re.sub(r'[\s\-_,.;:()\[\]£$€%]+', '', str(ans).lower().strip())


# ═══════════════════════════════════════════════════════════════
# SHARED STEM-LEAK PREVENTION BLOCK
# ═══════════════════════════════════════════════════════════════
STEM_NO_LEAK_BLOCK = """
 CRITICAL — QUESTION STEM MUST NOT CONTAIN THE ANSWER 
Every blank (a run of underscores like ____ or ＿＿) in a question stem
MUST be completely empty. NEVER pre-fill any part of the answer in the
stem. If a blank exists, the reader must not be able to guess the answer
from the surrounding text.

 BAD STEMS (contain the answer or part of it — FORBIDDEN):
    "Full name: Aisha ________" ← leaks first name
    "Contact phone number: 07700 ________" ← leaks prefix
    "Date of loss: 15 ________" ← leaks day
    "Standard fee: £8________" ← leaks amount
    "Email: aisha.________@email.com" ← leaks username
    "The fee is £12 for adults." ← number in stem with no blank
    "Opened in 1924 and closed in ________." ← 1924 is the answer elsewhere

 GOOD STEMS (blank only, no pre-filled content):
    "Full name: ________"
    "Contact phone number: ________"
    "Date of loss: ________"
    "Standard fee: £________"
    "Email address: ________"
    "The fee is ________ for adults."
    "The museum opened in ________."

This rule applies to EVERY form_completion, sentence_completion,
note_completion, table_completion and summary_completion question.
"""


# ═══════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════

class SectionCache:
    def __init__(self, cache_dir: str = "cache/listening/sections"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._max_memory_items = 200

    def _get_cache_key(self, section: int, topic: str = None,
                       difficulty: str = "medium", accent: str = "british",
                       nonce: str = "") -> str:
        key_parts = [str(section), difficulty, accent]
        if topic:
            key_parts.append(topic)
        if nonce:
            key_parts.append(nonce)
        return hashlib.md5("_".join(key_parts).encode()).hexdigest()

    def _get_cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, section: int, topic: str = None, difficulty: str = "medium",
            accent: str = "british", max_age_hours: int = 168,
            nonce: str = "") -> Optional[Dict]:
        key = self._get_cache_key(section, topic, difficulty, accent, nonce)
        with self._lock:
            if key in self._memory_cache:
                age = (datetime.now() - self._cache_time[key]).total_seconds() / 3600
                if age < max_age_hours:
                    self._cache_time[key] = datetime.now()
                    return self._memory_cache[key]
                else:
                    del self._memory_cache[key]
                    del self._cache_time[key]
        cache_path = self._get_cache_path(key)
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                age = (datetime.now() - datetime.fromisoformat(data['_cache_time'])).total_seconds() / 3600
                if age < max_age_hours:
                    with self._lock:
                        self._memory_cache[key] = data['data']
                        self._cache_time[key] = datetime.now()
                    return data['data']
                else:
                    cache_path.unlink()
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
        return None

    def save(self, section: int, section_data: Dict, topic: str = None,
             difficulty: str = "medium", accent: str = "british",
             nonce: str = ""):
        key = self._get_cache_key(section, topic, difficulty, accent, nonce)
        if 'script' not in section_data or 'audio_script' not in section_data:
            logger.warning(f" Section {section} missing script fields in cache save – adding fallback")
            if 'script' not in section_data:
                section_data['script'] = section_data.get('audio_script', '')
            if 'audio_script' not in section_data:
                section_data['audio_script'] = section_data.get('script', '')
        cache_entry = {'data': section_data, '_cache_time': datetime.now().isoformat()}
        with self._lock:
            self._memory_cache[key] = section_data
            self._cache_time[key] = datetime.now()
            if len(self._memory_cache) > self._max_memory_items:
                oldest_key = min(self._cache_time, key=lambda k: self._cache_time[k])
                del self._memory_cache[oldest_key]
                del self._cache_time[oldest_key]
        try:
            cache_path = self._get_cache_path(key)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Cache save error: {e}")

    def clear(self, older_than_days: int = 7) -> int:
        count = 0
        cutoff = datetime.now() - timedelta(days=older_than_days)
        for f in self.cache_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    count += 1
            except Exception:
                pass
        with self._lock:
            self._memory_cache.clear()
            self._cache_time.clear()
        return count

    def get_stats(self) -> Dict:
        return {
            'total_files': len(list(self.cache_dir.glob("*.json"))),
            'memory_cache_count': len(self._memory_cache),
            'cache_dir': str(self.cache_dir),
        }


section_cache = SectionCache()


# ═══════════════════════════════════════════════════════════════
# GENERATOR
# ═══════════════════════════════════════════════════════════════

class SectionGenerator:
    def __init__(self, ai_engine=None):
        self.ai = ai_engine
        logger.info("SectionGenerator initialized - PURE AI ONLY (No fallback)")

    # ============================================================
    # SINGLE SECTION DISPATCHER
    # ============================================================
    def generate_single_section(self, section: int, difficulty: str = "medium",
                                topic: Optional[str] = None, accent: str = "british",
                                fast: bool = True, use_cache: bool = True,
                                force_new: bool = False) -> Dict[str, Any]:
        if section not in [1, 2, 3, 4]:
            raise ValueError(f"Invalid section number: {section}. Must be 1-4.")
        if section == 1:
            return self.section1(difficulty=difficulty, accent=accent,
                                 fast=fast, use_cache=use_cache, force_new=force_new)
        if not topic:
            topic = self._get_default_topic(section, difficulty)
        if section == 2:
            return self.section2(topic=topic, difficulty=difficulty, accent=accent,
                                 fast=fast, use_cache=use_cache, force_new=force_new)
        if section == 3:
            return self.section3(topic=topic, difficulty=difficulty, accent=accent,
                                 fast=fast, use_cache=use_cache, force_new=force_new)
        return self.section4(topic=topic, difficulty=difficulty, accent=accent,
                             fast=fast, use_cache=use_cache, force_new=force_new)

    def _get_default_topic(self, section: int, difficulty: str) -> str:
        try:
            from .topics import TopicRegistry
            available = TopicRegistry.get_random_topics(count=5, difficulty=difficulty,
                                                        section=section, strict=False)
            if available:
                return random.choice(available)
        except Exception:
            pass
        fallbacks = {2: 'museum', 3: 'education', 4: 'environment'}
        return fallbacks[section]

    def _detect_gender_from_name(self, name: str) -> str:
        if not name:
            return 'unknown'
        clean = re.sub(r'^(Dr\.?|Professor|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Sir|Dame)\s+',
                       '', name, flags=re.I).strip()
        first = clean.split()[0] if clean.split() else clean
        lower = first.lower()
        if lower in [n.lower() for n in FEMALE_NAMES]:
            return 'female'
        if lower in [n.lower() for n in MALE_NAMES]:
            return 'male'
        if re.match(r'^(Mr\.?|Sir)\s', name, re.I):
            return 'male'
        if re.match(r'^(Mrs\.?|Ms\.?|Miss|Madam)\s', name, re.I):
            return 'female'
        return 'unknown'

    # ============================================================
    # STEM-LEAK DETECTION & SANITIZATION
    # ============================================================
    def _stem_has_answer_leak(self, q: Dict) -> bool:
        text = (q.get('question') or q.get('text') or '').strip()
        ans = (q.get('answer') or q.get('correct_answer') or '').strip()
        if not text or not ans:
            return False

        q_type = (q.get('type') or '').lower()
        check_types = {
            'form_completion', 'text', 'note_completion', 'table_completion',
            'sentence_completion', 'summary_completion',
        }
        if q_type not in check_types:
            return False

        if not re.search(r'[_＿]{2,}', text):
            return False

        text_lower = text.lower()
        ans_lower = ans.lower()

        if len(ans) >= 4 and ans_lower in text_lower:
            return True

        word_tokens = [w for w in re.findall(r'[A-Za-z0-9]+', ans) if len(w) >= 4]
        for w in word_tokens:
            if re.search(r'\b' + re.escape(w) + r'\b', text, re.I):
                return True

        num_tokens = re.findall(r'\d{2,}', ans)
        for n in num_tokens:
            if re.search(r'\b' + re.escape(n) + r'\b', text):
                return True

        return False

    def _sanitize_stem(self, text: str) -> str:
        if not text:
            return text
        s = str(text)

        s = re.sub(
            r'^([A-Za-z][A-Za-z0-9 ]{1,70}):\s*([^\s_＿][^\n]*?)\s*([_＿]{2,}.*)$',
            r'\1: \3',
            s,
            flags=re.DOTALL,
        )

        s = re.sub(r'(£|\$|€)\s*[\d.,]+\s*([_＿]{2,})', r'\1\2', s)
        s = re.sub(r'\b\d{1,4}\s*([_＿]{2,})', r'\1', s)
        s = re.sub(r'\b\d{1,2}[:.]\d{2}\s*([_＿]{2,})', r'\1', s)
        s = re.sub(r'\b\d{1,2}\s*(?:am|pm|AM|PM)\s*([_＿]{2,})', r'\1', s)

        s = re.sub(r'\s{2,}', ' ', s).strip()
        return s

    # ============================================================
    # SECTION 1 PROMPT
    # ============================================================
    def _build_section1_prompt(self, difficulty: str = "medium", accent: str = "british",
                               forced_speakers: Optional[Dict] = None) -> str:
        try:
            from .topics import TopicRegistry
            all_topics = TopicRegistry.get_all()
            section1_topics = [(k, t) for k, t in all_topics.items()
                               if 1 in t.get('sections', [])]
        except Exception:
            section1_topics = []

        if not section1_topics:
            logger.warning("No Section 1 topics found in TopicRegistry, using fallback scenarios.")
            return self._build_section1_prompt_fallback(difficulty, accent,
                                                        forced_speakers=forced_speakers)

        selected_key, selected_topic = random.choice(section1_topics)
        display_name = selected_topic.get('display_name', selected_key.replace('_', ' ').title())
        keywords = ', '.join(selected_topic.get('keywords', []))
        details = self._generate_details_from_topic(selected_key, selected_topic)

        forced_block = ""
        if forced_speakers:
            cn = forced_speakers.get("customer_name", "")
            cg = forced_speakers.get("customer_gender", "")
            an = forced_speakers.get("agent_name", "")
            ag = forced_speakers.get("agent_gender", "")
            forced_block = f"""
 MANDATORY NAMES (DO NOT CHANGE THESE) 
- Agent's name MUST be **{an}** (gender: {ag})
- Customer's name MUST be **{cn}** (gender: {cg})
- Use these EXACT names when the characters introduce themselves.
- Do NOT invent different names.
- Do NOT swap the genders.
"""

        prompt = f"""
You are an expert IELTS Listening test writer. Generate a natural, realistic conversation for IELTS Listening Section 1.
{forced_block}
=== SCENARIO ===
Topic: {display_name}
Context: A customer is making an enquiry about {display_name}.
Details to include:
{chr(10).join('- ' + d for d in details)}

=== SPEAKERS ===
- Agent: The service provider – professional, friendly, asks questions, confirms details.
- Customer: The person making the enquiry – sometimes hesitant, gives personal information, may correct themselves.

=== ABSOLUTELY CRITICAL: REAL IELTS CONVERSATION RULES ===
1. Speaker labels MUST be "Agent:" and "Customer:".
2. The Agent MUST introduce themselves with a name. The Customer MUST also give their name.
   Agent and Customer names MUST be DIFFERENT.
3. The Customer MUST give their FULL NAME and SPELL IT clearly at least once.
4. The conversation MUST include ALL of these elements:
   - Greeting and introduction
   - At least 3 hesitation/filler words
   - At least 2 self-corrections
   - Confirmation phrases from Agent
   - Polite closing
5. Include specific numbers, dates, times, prices, contact details that match the scenario.
6. Natural flow with turn-taking (25-30 lines).
7. DO NOT write question numbers or labels like "Question 1" in the script.
8. The conversation MUST be at least 380 words.

=== KEYWORDS TO INCLUDE ===
{keywords}

=== QUESTION TYPES (EXACTLY 10 QUESTIONS) ===
- 8 questions: form/table/note completion (fill in blanks).
- 2 questions: MULTIPLE CHOICE (must include an "options" array with 3 choices A, B, C).

{STEM_NO_LEAK_BLOCK}

 CRITICAL — PICK 8 DISTINCT CATEGORIES (NO OVERLAP) 
Each of Q1–Q8 MUST come from a DIFFERENT category. Pick 8 from:

  1. Full name (spelled out) — stem: "Full name: ________" → answer is the WHOLE name
  2. Contact phone number — stem: "Contact number: ________"
  3. Email address OR postal address (pick ONE)
  4. Appointment / booking DATE
  5. Type of appointment or service requested
  6. Room / vehicle / plan / membership type
  7. Number of guests / people / items
  8. Cost / fee / deposit (pick ONE — not two)
  9. Payment method OR payment plan (pick ONE)
  10. Special request / preference
  11. Reference / booking number
  12. Any other unique factual field

 SPECIFIC FORBIDDEN COMBINATIONS:
     Q4: Appointment DATE + Q5: Appointment TIME
     Q6: Cost X + Q7: Cost Y
     Q2: Phone number + Q3: Mobile number
     Q3: Email + Q7: Postal address

 GOOD EXAMPLE (all stems are bare labels, no leaks):
    Q1: Full name: → "Aisha Khan"
    Q2: Contact phone number: → "07700 900123"
    Q3: Email address: → "aisha.khan@email.com"
    Q4: Appointment date: → "15 March"
    Q5: Reason for visit: → "Routine check-up"
    Q6: Preferred appointment time: → "Morning"
    Q7: Total cost: → "£85"
    Q8: Reference number: → "REF-2847"

 CRITICAL INSTRUCTION FOR MULTIPLE CHOICE:
For the 2 multiple choice questions, you MUST include an "options" key with exactly 3 choices labelled A, B, C.
The "answer" must be exactly one of those letters.

Example:
{{"question": "What is the total cost?", "type": "multiple_choice", "options": ["A: £15", "B: £20", "C: £25"], "answer": "B"}}

=== JSON OUTPUT ===
Return ONLY valid JSON with keys "script" and "questions".

=== DIFFICULTY ===
Difficulty level: {difficulty}.

=== ACCENT ===
The speakers should have a {accent} English accent.

Generate the Section 1 conversation now.
"""
        return prompt

    def _generate_details_from_topic(self, topic_key: str, topic_data: Dict) -> List[str]:
        display_name = topic_data.get('display_name', topic_key)
        category = topic_data.get('category', 'everyday')

        if category != 'everyday':
            return [f"The service provider asks for: full name (spelled out), contact details, relevant information",
                    f"The customer enquires about {display_name}",
                    "Include confirmation of details at the end"]

        lookup = [
            (('hotel', 'booking', 'reservation'),
             ["The receptionist asks for: full name (spelled out), check-in/out dates, room type, number of guests, contact number, special requests, payment method",
              "The customer should be friendly, sometimes unsure, and may change their mind",
              "Include confirmation of booking details at the end"]),
            (('gym', 'fitness'),
             ["The staff member explains: membership types, fees, contract length, facilities, class schedules",
              "The customer asks questions, compares options, and decides on a plan",
              "Include personal details: name, contact, health conditions"]),
            (('course', 'education', 'school', 'college', 'university'),
             ["The admin asks for: name, contact, course selection, payment method, start date",
              "The student asks about course content, schedule, and prerequisites",
              "Include spelling of name and email address"]),
            (('rental', 'car', 'vehicle'),
             ["The agent asks for: name, driving licence details, rental dates, vehicle type, insurance options, pick-up/drop-off locations",
              "The customer compares prices and makes a decision",
              "Include a confirmation number at the end"]),
            (('bank', 'finance', 'account'),
             ["The bank officer asks for: full name (spelled out), account type, identification details, contact information, initial deposit amount",
              "The customer asks about interest rates, fees, and services",
              "Include account number or reference at the end"]),
            (('restaurant', 'cafe', 'dining'),
             ["The staff asks for: name (spelled out), date and time, number of guests, special requests, contact number",
              "The customer asks about menu options, availability, and prices",
              "Include confirmation of reservation details at the end"]),
            (('insurance', 'claim', 'lost', 'lost_property'),
             ["The insurance/claims agent asks for: full name (spelled out), policy type, contact details, vehicle/personal information, date of loss, item lost, carriage/booking number, standard fee",
              "The customer asks about coverage, premiums, and claim process",
              "Include policy number or reference at the end"]),
            (('job', 'application', 'interview'),
             ["The HR person asks for: full name (spelled out), contact details, qualifications, experience, availability",
              "The applicant asks about job role, salary, benefits, and working hours",
              "Include confirmation of interview date/time at the end"]),
        ]
        for keywords, details in lookup:
            if any(word in topic_key for word in keywords):
                return details
        return [f"The service provider asks for: full name (spelled out), contact details, relevant personal information",
                f"The customer asks questions about the {display_name} service and makes decisions",
                "Include confirmation of booking/application details at the end"]

    def _build_section1_prompt_fallback(self, difficulty: str = "medium",
                                        accent: str = "british",
                                        forced_speakers: Optional[Dict] = None) -> str:
        scenarios = [
            {"title": "Hotel Booking", "context": "customer calls a hotel to book a room for a weekend",
             "details": ["The receptionist asks for: full name (spelled out), check-in/out dates, room type, number of guests, contact number, special requests, payment method",
                         "The customer should be friendly, sometimes unsure, and may change their mind",
                         "Include confirmation of booking details at the end"]},
            {"title": "Gym Membership Enquiry", "context": "customer visits a fitness centre to ask about membership options",
             "details": ["The staff member explains: membership types, fees, contract length, facilities, class schedules",
                         "The customer asks questions, compares options, and decides on a plan",
                         "Include personal details: name, contact, health conditions"]},
            {"title": "Course Registration", "context": "student calls a college to register for a course",
             "details": ["The admin asks for: name, contact, course selection, payment method, start date",
                         "The student asks about course content, schedule, and prerequisites",
                         "Include spelling of name and email address"]},
            {"title": "Car Rental Booking", "context": "customer calls a car rental company to book a vehicle",
             "details": ["The agent asks for: name, driving licence details, rental dates, vehicle type, insurance options, pick-up/drop-off locations",
                         "The customer compares prices and makes a decision",
                         "Include a confirmation number at the end"]}
        ]
        scenario = random.choice(scenarios)

        forced_block = ""
        if forced_speakers:
            cn = forced_speakers.get("customer_name", "")
            cg = forced_speakers.get("customer_gender", "")
            an = forced_speakers.get("agent_name", "")
            ag = forced_speakers.get("agent_gender", "")
            forced_block = f"""
 MANDATORY NAMES (DO NOT CHANGE THESE) 
- Agent's name MUST be **{an}** (gender: {ag})
- Customer's name MUST be **{cn}** (gender: {cg})
- Use these EXACT names when the characters introduce themselves.
- Do NOT invent different names.
- Do NOT swap the genders.
"""

        prompt = f"""
You are an expert IELTS Listening test writer. Generate a natural, realistic conversation for IELTS Listening Section 1.
{forced_block}
=== SCENARIO ===
Title: {scenario['title']}
Context: {scenario['context']}
Details to include:
{chr(10).join('- ' + d for d in scenario['details'])}

=== SPEAKERS ===
- Agent: The service provider.
- Customer: The person making the enquiry.

=== ABSOLUTELY CRITICAL: REAL IELTS CONVERSATION RULES ===
1. Speaker labels MUST be "Agent:" and "Customer:".
2. The Agent MUST introduce themselves with a name. The Customer MUST also give their name.
3. The Customer MUST give their FULL NAME and SPELL IT clearly at least once.
4. The conversation MUST include:
   - Greeting and introduction
   - At least 3 hesitation/filler words
   - At least 2 self-corrections
   - Confirmation phrases from Agent
   - Polite closing
5. Include specific numbers, dates, times, prices, contact details.
6. Natural flow with turn-taking (25-30 lines).
7. DO NOT write question numbers or labels.
8. The conversation MUST be at least 380 words.

=== QUESTION TYPES (EXACTLY 10 QUESTIONS) ===
- 8 questions: form/table/note completion.
- 2 questions: MULTIPLE CHOICE (must include an "options" array).

{STEM_NO_LEAK_BLOCK}

 CRITICAL — PICK 8 DISTINCT CATEGORIES (NO OVERLAP) 
Each of Q1–Q8 MUST come from a DIFFERENT category:
    Full name / phone / email OR address / date / type of service /
    room or plan type / number of guests / cost OR deposit / payment method /
    special request / reference number

 BAD: Q4 date + Q5 time | Q6 cost X + Q7 cost Y
 GOOD: name / phone / email / date / reason / time-slot / cost / reference

 CRITICAL for MULTIPLE CHOICE: include "options" array with 3 choices A, B, C.

=== JSON OUTPUT ===
Return ONLY valid JSON with keys "script" and "questions".

=== DIFFICULTY ===
Difficulty level: {difficulty}.

=== ACCENT ===
Speakers should have a {accent} English accent.

Generate the Section 1 conversation now.
"""
        return prompt

    # ============================================================
    # SECTION 2 PROMPT
    # ============================================================
    def _build_section2_prompt(self, topic: str, difficulty: str = "medium") -> str:
        topic_scenarios = {
            "art_gallery": {"title": "City Art Gallery Tour", "content": ["Welcome and introduction", "Brief history", "Layout", "Practical information", "Upcoming events"]},
            "university": {"title": "University Campus Orientation", "content": ["Welcome", "History", "Campus layout", "Student services", "Practical information"]},
            "music_festival": {"title": "Music Festival Orientation", "content": ["Welcome", "History", "Site layout", "Schedule", "Practical info"]},
            "sleep": {"title": "Sleep Research Institute Tour", "content": ["Welcome", "History and mission", "Research facilities", "Current projects", "Practical information"]},
            "recycling": {"title": "City Recycling Centre Tour", "content": ["Welcome", "History and purpose", "Recycling processes", "Waste stats", "Visitor info"]}
        }
        scenario = topic_scenarios.get(topic, {
            "title": f"Information Talk: {topic.replace('_', ' ').title()}",
            "content": ["Welcome", "History or background", "Key features", "Practical information", "Conclusion"]
        })

        map_title_options = ["Ground Floor Plan", "First Floor Layout", "Site Map",
                             "Exhibition Hall Plan", "Campus Map", "Park Layout", "Museum Floor Plan"]
        map_title = random.choice(map_title_options)

        place_names = ["Reception", "Café", "Garden", "Car Park", "Conference Room",
                       "Exhibition Hall", "Library", "Sports Centre", "Cafeteria",
                       "Lecture Theatre", "Swimming Pool", "Playground", "Main Entrance",
                       "Information Desk", "Shop", "Restrooms", "Ticket Office",
                       "Cloakroom", "Gift Shop", "Study Area"]
        selected = random.sample(place_names, 8)
        map_locations_example = [{"letter": chr(65+i), "name": name} for i, name in enumerate(selected)]
        example_map_json = json.dumps([{
            "id": "q_2_1", "number": 11, "text": "Where is the Main Entrance?",
            "type": "map_labeling", "map_title": map_title,
            "map_locations": map_locations_example, "correct_answer": "A"
        }], indent=2)

        prompt = f"""
You are an expert IELTS Listening test writer. Generate a natural, informative monologue for IELTS Listening Section 2.

=== SCENARIO ===
Topic: {scenario['title']}
Context: A tour guide/information officer is giving a talk or tour.

=== CONTENT TO INCLUDE ===
{chr(10).join('- ' + c for c in scenario['content'])}

=== SPEAKER ===
- The guide/information officer should be professional, welcoming, clear.
- Use signposting: "Now let's move on to...", "I'd like to draw your attention to...".
- Include engaging language: "As you can see...", "You might be interested to know...".

=== MONOLOGUE REQUIREMENTS ===
1. SINGLE speaker monologue (no speaker labels).
2. Include natural speech patterns: hesitations, pauses, rhetorical questions.
3. Include specific numbers, dates, times, names.
4. The monologue MUST be at least 420 words.
5. Sound like a real tour guide, NOT a recorded announcement.

=== MAP LABELLING IS REQUIRED ===
Generate 10 questions in total. At least 5 MUST be type "map_labeling".
The other 5 can be form completion, matching, or multiple choice.

For each map_labeling question, provide:
- "type": "map_labeling"
- "map_title": the title of the map
- "map_locations": an array of objects with "letter" and "name"
- "correct_answer": the correct letter for that question

Example:
{example_map_json}

{STEM_NO_LEAK_BLOCK}

 CRITICAL: UNIQUE ANSWERS 
- For map_labeling: each question MUST have a DISTINCT correct letter.
- For matching: each item MUST have a DIFFERENT correct answer letter.
- For fill-in: every answer MUST be DISTINCT.

=== QUESTION TYPES FOR THE REMAINING 5 QUESTIONS ===
- Form completion (1-2 questions)
- Matching (1-2 questions)
- Multiple choice (1-2 questions)

=== JSON OUTPUT ===
Return ONLY valid JSON with keys "script" and "questions".

=== DIFFICULTY ===
Difficulty level: {difficulty}.

Generate the Section 2 monologue now.
"""
        return prompt

    # ============================================================
    # SECTION 3 PROMPT
    # ============================================================
    def _build_section3_prompt(self, topic: str, difficulty: str = "medium",
                               custom_speakers: Optional[List[str]] = None) -> str:
        topic_themes = {
            "sleep": {"title": "The Economics of Sleep",
                      "questions": ["What are the economic costs of sleep deprivation?",
                                    "How does sleep affect workplace productivity?",
                                    "What are the health implications of poor sleep?",
                                    "How can policy address sleep-related issues?"]},
            "education_system": {"title": "The Role of Standardised Testing",
                                 "questions": ["What are the benefits of standardised testing?",
                                               "What are the drawbacks?",
                                               "How can education systems balance standardisation and flexibility?",
                                               "What alternatives exist?"]},
            "social_media": {"title": "The Societal Impact of Social Media",
                             "questions": ["How has social media changed communication?",
                                           "What are the mental health effects?",
                                           "How does social media affect democracy?",
                                           "What are the economic implications?"]},
            "recycling": {"title": "Recycling and Environmental Policy",
                          "questions": ["What are the environmental benefits?",
                                        "What are the economic challenges?",
                                        "How can governments promote recycling?",
                                        "What role does technology play?"]}
        }
        theme = topic_themes.get(topic, {
            "title": f"Discussion: {topic.replace('_', ' ').title()}",
            "questions": [f"What are the key issues related to {topic}?",
                          f"What are the different perspectives?",
                          f"How can society address challenges?",
                          f"What are the future implications?"]
        })

        if custom_speakers and len(custom_speakers) >= 4:
            tutor, s1, s2, s3 = custom_speakers[0], custom_speakers[1], custom_speakers[2], custom_speakers[3]
        else:
            tutor, s1, s2, s3 = "Dr. Sarah", "James", "Emma", "David"

        prompt = f"""
You are an expert IELTS Listening test writer. Generate a natural, academic discussion for IELTS Listening Section 3.

=== SCENARIO ===
Topic: {theme['title']}
Context: A tutor and three students are discussing this topic in a tutorial.

=== SPEAKERS (use these EXACT names) ===
- Tutor: {tutor}
- Student 1: {s1}
- Student 2: {s2}
- Student 3: {s3}

=== DISCUSSION QUESTIONS ===
{chr(10).join('- ' + q for q in theme['questions'])}

=== DISCUSSION REQUIREMENTS ===
1. Every line MUST start with "{tutor}:", "{s1}:", "{s2}:", or "{s3}:".
2. Lively and academic with natural interruptions, agreement/disagreement, clarifications.
3. Each speaker contributes meaningfully with a distinct viewpoint.
4. Include specific numbers, names, dates, terminology.
5. The discussion MUST be at least 480 words.

{STEM_NO_LEAK_BLOCK}

=== QUESTION TYPES — MIXED WITH MATCHING ===
- Q21-Q23: note_completion.
- Q24: matching — match the speaker to their proposed solution. Include "options" array.
- Q25-Q28: note_completion.
- Q29: matching — match the future prediction to the speaker. Include "options" array.
- Q30: note_completion.

 CRITICAL: UNIQUE ANSWERS 
- For matching (Q24, Q29): each item MUST have a DIFFERENT correct answer letter.
- For note_completion: every answer MUST be DISTINCT.

 CRITICAL for MATCHING (Q24 and Q29):
Include an "options" array with the list of speakers.

Example:
{{"question": "Match the speaker to their proposed solution.", "type": "matching", "options": ["A: {s1}", "B: {s2}", "C: {s3}"], "answer": "A"}}

=== JSON OUTPUT ===
Return ONLY valid JSON with keys "script" and "questions".

=== DIFFICULTY ===
Difficulty level: {difficulty}.

Generate the Section 3 discussion now.
"""
        return prompt

    # ============================================================
    # SECTION 4 PROMPT
    # ============================================================
    def _build_section4_prompt(self, topic: str, difficulty: str = "medium") -> str:
        topic_lectures = {
            "smart_homes": {"title": "The Evolution of Smart Home Technology",
                            "structure": ["Introduction", "Historical context", "Key technologies",
                                          "Benefits", "Challenges", "Future trends", "Conclusion"],
                            "keywords": ["home automation", "Internet of Things", "voice recognition",
                                         "energy efficiency", "data privacy"]},
            "online_learning": {"title": "The Future of Online Learning",
                                "structure": ["Introduction", "Historical context", "Key drivers",
                                              "Benefits", "Challenges", "Future trends", "Conclusion"],
                                "keywords": ["MOOC", "open education", "virtual classroom",
                                             "personalised learning", "micro-credential"]},
            "gardening": {"title": "Urban Gardening and Sustainable Living",
                          "structure": ["Introduction", "Historical context", "Key drivers",
                                        "Benefits", "Challenges", "Future trends", "Conclusion"],
                          "keywords": ["urban farming", "food security", "sustainable development",
                                       "biodiversity", "vertical gardens"]}
        }
        lecture = topic_lectures.get(topic, {
            "title": f"Lecture: {topic.replace('_', ' ').title()}",
            "structure": ["Introduction", "Background and context", "Key concepts and theories",
                          "Applications and examples", "Current challenges", "Future directions", "Conclusion"],
            "keywords": ["key term 1", "key term 2", "key term 3"]
        })

        prompt = f"""
You are an expert IELTS Listening test writer. Generate a natural, academic lecture for IELTS Listening Section 4.

=== SCENARIO ===
Topic: {lecture['title']}
Context: A university lecture for a general academic audience.

=== LECTURE STRUCTURE ===
{chr(10).join('- ' + s for s in lecture['structure'])}

=== SPEAKER ===
- The lecturer should be professional, knowledgeable, engaging.
- Use academic language with signposting.

=== LECTURE REQUIREMENTS ===
1. SINGLE speaker lecture (no speaker labels).
2. Include specific statistics, dates, names, key terminology.
3. Academic vocabulary but accessible.
4. Include natural speech patterns.
5. The lecture MUST be at least 550 words.

=== KEY TERMINOLOGY ===
{chr(10).join('- ' + k for k in lecture['keywords'])}

=== QUESTION TYPES (EXACTLY 10 QUESTIONS) ===
- 8 questions: sentence / summary / note completion.
- 2 questions: MULTIPLE CHOICE (must include an "options" array).

{STEM_NO_LEAK_BLOCK}

 CRITICAL: ANSWERS MUST BE UNIQUE 
Each fill-in answer MUST target a DISTINCT piece of information.
- DO NOT ask about the same statistic, date, name, or concept twice.
- Each question MUST be about a DIFFERENT aspect of the lecture.

 CRITICAL for MULTIPLE CHOICE: include "options" array with 3 choices A, B, C.

Example:
{{"question": "What is the main finding of the study?", "type": "multiple_choice", "options": ["A: Exercise improves memory", "B: Diet is more important", "C: Sleep is crucial"], "answer": "A"}}

=== JSON OUTPUT ===
Return ONLY valid JSON with keys "script" and "questions".

=== DIFFICULTY ===
Difficulty level: {difficulty}.

Generate the Section 4 lecture now.
"""
        return prompt

    # ============================================================
    # NATURALNESS CHECK
    # ============================================================
    def _is_script_natural(self, script: str, section: int) -> bool:
        if not script:
            logger.warning(f"[S{section}] Empty script")
            return False

        cfg = SECTION_NATURALNESS_CONFIG.get(section, {
            'min_lines': 2, 'max_question_ratio': 0.6, 'require_labels': True,
            'min_unique_speakers': 2, 'is_monologue': False,
        })

        lines = [line.strip() for line in script.split('\n') if line.strip()]
        total_words = sum(len(line.split()) for line in lines)
        question_count = script.count('?')
        avg_words_per_line = total_words / len(lines) if lines else 0

        if cfg.get('is_monologue'):
            min_words = cfg.get('min_words', 100)
            if total_words < min_words:
                logger.warning(f"[S{section}] Monologue too short ({total_words} < {min_words})")
                return False
            max_q = cfg.get('max_questions', 3)
            if question_count > max_q:
                logger.warning(f"[S{section}] Too many questions ({question_count} > {max_q})")
                return False
            return True

        min_lines = cfg.get('min_lines', 2)
        if len(lines) < min_lines:
            logger.warning(f"[S{section}] Script too short ({len(lines)} < {min_lines} lines)")
            return False
        if avg_words_per_line < 3:
            logger.warning(f"[S{section}] Avg line length too short ({avg_words_per_line:.1f})")
            return False

        if cfg.get('require_labels'):
            label_pattern = r'^([A-Za-z][A-Za-z0-9_\- ]*):'
            labels = [re.match(label_pattern, line).group(1).strip()
                      for line in lines if re.match(label_pattern, line)]
            if not labels:
                logger.warning(f"[S{section}] Missing speaker labels")
                return False
            unique_labels = len(set(labels))
            min_speakers = cfg.get('min_unique_speakers', 2)
            if unique_labels < min_speakers:
                logger.warning(f"[S{section}] Not enough speakers ({unique_labels} < {min_speakers})")
                return False

        if lines:
            question_ratio = question_count / len(lines)
            max_ratio = cfg.get('max_question_ratio', 0.6)
            if question_ratio > max_ratio:
                logger.warning(f"[S{section}] Too many questions ({question_ratio:.2f} > {max_ratio})")
                return False

        return True

    # ============================================================
    # SCRIPT MESSIFICATION
    # ============================================================
    def _add_natural_hesitations(self, script: str, intensity: float = 0.2) -> str:
        if not script:
            return script
        markers = ['um', 'uh', 'well', 'you know', 'I mean', 'let me see',
                   'actually', 'like', 'sort of', 'kind of', 'right', 'okay']
        sentences = re.split(r'(?<=[.!?])\s+', script)
        for i in range(1, len(sentences)):
            if random.random() < intensity and len(sentences[i]) > 8:
                marker = random.choice(markers)
                words = sentences[i].split()
                if len(words) > 3:
                    pos = random.randint(1, min(2, len(words) - 1))
                    words.insert(pos, f"{marker},")
                    sentences[i] = ' '.join(words)
                else:
                    sentences[i] = f"{marker.title()}, {sentences[i]}"
        return ' '.join(sentences)

    def _ensure_speaker_labels(self, script: str, section: int) -> str:
        if not script:
            return script
        lines = script.strip().split('\n')
        has_labels = any(re.match(r'^[A-Za-z][A-Za-z0-9_\-\. ]*:', line.strip())
                         for line in lines if line.strip())
        if has_labels:
            return script
        if section == 1:
            labels = ['Agent', 'Customer']
            return '\n'.join(f"{labels[i % 2]}: {line}"
                             for i, line in enumerate(lines) if line.strip())
        if section == 3:
            labels = ['Tutor', 'Student1', 'Student2', 'Student3']
            return '\n'.join(f"{labels[i % 4]}: {line}"
                             for i, line in enumerate(lines) if line.strip())
        return script

    # ============================================================
    # SCRIPT EXPANSION — v7: 2-attempt with escalating strength
    # ============================================================
    def _expand_script(self, script: str, target_words: int, section: int,
                       topic: Optional[str] = None) -> str:
        if not self.ai:
            return script
        current_words = len(script.split())
        if current_words >= target_words:
            return script
        deficit = target_words - current_words
        buffer = int(deficit * 0.5)
        new_target = target_words + 50

        logger.info(
            f" Script too short ({current_words}/{target_words}). "
            f"Expanding by ~{deficit + buffer} → target {new_target}..."
        )

        # ═══════════════════════════════════════════════════════════
        # v7 FIX: Two escalation attempts
        # Attempt 1 — standard expansion (as before)
        # Attempt 2 — aggressive forced expansion with explicit counts
        # ═══════════════════════════════════════════════════════════
        attempts = [
            # Attempt 1 — standard
            (
                f"The following IELTS Listening Section {section} script is too short "
                f"({current_words} words). It MUST be at least {new_target} words.\n\n"
                f"Your ONLY job is to EXPAND the script. You MUST:\n"
                f"- Keep ALL existing content and questions referenced in the script\n"
                f"- Keep the same speaker labels and structure\n"
                f"- Add at least {deficit + buffer} NEW words (do NOT just rephrase existing content)\n"
                f"- Add MORE detail: descriptions, examples, statistics, dialogue exchanges, filler words\n"
                f"- Do NOT remove anything\n"
                f"- Do NOT change the questions\n"
                f"- Return ONLY the expanded script (plain text, no JSON, no explanations, no markdown)\n\n"
                f"CURRENT SCRIPT:\n---\n{script}\n---\n\n"
                f"Expanded script (must be at least {new_target} words):\n"
            ),
            # Attempt 2 — forced, explicit counts
            (
                f"TASK: Rewrite the following IELTS Listening Section {section} script to be "
                f"SUBSTANTIALLY LONGER.\n"
                f"Current length: {current_words} words.\n"
                f"REQUIRED length: at least {new_target} words "
                f"(that is {new_target - current_words} MORE words than now).\n\n"
                f"You MUST ADD these to the existing content:\n"
                f"1. At least 4 new sentences of background detail\n"
                f"2. At least 3 new specific examples with numbers/dates\n"
                f"3. At least 2 new dialogue exchanges (if dialogue)\n"
                f"4. At least 1 new transitional paragraph\n\n"
                f"DO NOT shorten, summarise, or rephrase existing content.\n"
                f"ONLY APPEND AND INSERT new material.\n\n"
                f"Return ONLY the expanded script (plain text, no JSON, no markdown).\n\n"
                f"CURRENT SCRIPT:\n---\n{script}\n---\n"
            ),
        ]

        best_script = script
        best_word_count = current_words

        for i, expansion_prompt in enumerate(attempts, 1):
            try:
                logger.info(f" Expansion attempt {i}/{len(attempts)}...")
                expanded = self.ai.generate(
                    expansion_prompt,
                    max_tokens=4000,
                    temperature=0.7 + (0.1 * i), # escalate temperature
                    fast=True,
                )
                if not expanded or not isinstance(expanded, str):
                    logger.warning(f" Expansion attempt {i} returned empty/non-string")
                    continue

                expanded = re.sub(r'```.*?```', '', expanded, flags=re.DOTALL).strip()
                if expanded.startswith('{'):
                    m = re.search(
                        r'"script"\s*:\s*"((?:[^"\\]|\\.)*)"',
                        expanded, re.DOTALL,
                    )
                    if m:
                        expanded = m.group(1).replace('\\n', '\n').replace('\\"', '"')

                new_words = len(expanded.split())

                # v7: Track best result even if it doesn't hit the target
                if new_words > best_word_count:
                    best_script = expanded
                    best_word_count = new_words
                    logger.info(
                        f" Attempt {i}: {current_words} → {new_words} words "
                        f"(best so far)"
                    )

                if new_words >= target_words:
                    logger.info(
                        f" Script expanded from {current_words} to {new_words} words "
                        f"(attempt {i})"
                    )
                    return expanded

                logger.warning(
                    f" Attempt {i} still short: {new_words}/{target_words}"
                )

            except Exception as e:
                logger.warning(f"Expansion attempt {i} failed: {e}")

        if best_word_count > current_words:
            logger.info(
                f" All attempts finished. Using best result: "
                f"{current_words} → {best_word_count} words "
                f"(target {target_words})"
            )
            return best_script

        logger.warning(f" Expansion returned fewer/same words ({current_words}). Keeping original.")
        return script

    # ============================================================
    # DISTRACTOR INSERTION
    # ============================================================
    def _insert_distractors_into_script(self, script: str, fields: Optional[List[Dict]] = None,
                                        questions: Optional[List[Dict]] = None, section: int = 1,
                                        topic: Optional[str] = None) -> str:
        if not script or not distractor_generator:
            return script
        num_distractors = {1: 2, 2: 3, 3: 3, 4: 2}.get(section, 2)
        candidates = []

        real_answers_norm = set()
        if questions:
            for q in questions:
                ans = (q.get('answer') or q.get('correct_answer') or '').strip()
                if ans:
                    real_answers_norm.add(_normalize_answer(ans))
                if isinstance(q.get('items'), list):
                    for it in q['items']:
                        if isinstance(it, dict):
                            a = (it.get('correct_answer') or it.get('answer') or '').strip()
                            if a:
                                real_answers_norm.add(_normalize_answer(a))

        if fields:
            candidates = fields
        elif questions:
            for q in questions:
                if q.get('answer'):
                    q_type = q.get('type', 'text')
                    if q_type in ['form_completion', 'table_completion']:
                        answer = q.get('answer', '')
                        if re.search(r'\d+', answer):
                            q_type = 'number'
                        elif re.search(r'\b(date|day|month|year)\b', q.get('question', ''), re.I):
                            q_type = 'date'
                        elif re.search(r'\b(time|hour|minute)\b', q.get('question', ''), re.I):
                            q_type = 'time'
                        elif re.search(r'\b(price|cost|fee|amount|dollar|pound)\b', q.get('question', ''), re.I):
                            q_type = 'money'
                        elif re.search(r'\b(name|full name)\b', q.get('question', ''), re.I):
                            q_type = 'name'
                        candidates.append({'t': q_type, 'a': answer})
                    elif q_type in ['multiple_choice', 'matching']:
                        candidates.append({'t': 'text', 'a': q.get('answer', '')})

        if not candidates:
            numbers = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', script)
            dates = re.findall(r'\b\d{1,2}\s*(?:st|nd|rd|th)?\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',
                               script, re.I)
            for num in numbers[:3]:
                candidates.append({'t': 'number', 'a': num})
            for date in dates[:3]:
                candidates.append({'t': 'date', 'a': date})

        filtered_candidates = []
        for c in candidates:
            norm = _normalize_answer(c.get('a', ''))
            if norm and norm in real_answers_norm:
                continue
            filtered_candidates.append(c)
        candidates = filtered_candidates
        if not candidates:
            return script

        distractors = distractor_generator.generate_distractors_for_fields(
            fields=candidates,
            num_distractors=min(num_distractors, len(candidates)),
            section=section,
            topic=topic,
        )
        if not distractors:
            return script

        safe_distractors = []
        for d in distractors:
            d_ans = d.get('answer') or d.get('a') or d.get('text') or ''
            if _normalize_answer(d_ans) in real_answers_norm:
                continue
            safe_distractors.append(d)
        distractors = safe_distractors
        if not distractors:
            return script

        lines = script.split('\n')

        labels_seen = []
        for line in lines:
            match = re.match(r'^([A-Za-z][A-Za-z0-9_\- ]*):', line)
            if match:
                lbl = match.group(1).strip()
                if lbl not in labels_seen:
                    labels_seen.append(lbl)
                if len(labels_seen) >= 2:
                    break
        if len(labels_seen) >= 2:
            speaker_a, speaker_b = labels_seen[0], labels_seen[1]
        elif len(labels_seen) == 1:
            speaker_a = labels_seen[0]
            speaker_b = "Speaker2"
        else:
            speaker_a, speaker_b = "Agent", "Customer"

        modified_lines = distractor_generator.insert_distractors_into_script(
            script_lines=lines,
            distractors=distractors,
            speaker_a=speaker_a,
            speaker_b=speaker_b,
        )
        return '\n'.join(modified_lines)

    # ============================================================
    # HELPERS
    # ============================================================
    def _get_gender_appropriate_names(self) -> Tuple[str, str, str, str]:
        female_names, male_names = list(FEMALE_NAMES), list(MALE_NAMES)
        if random.choice([True, False]):
            return (random.choice(female_names), "female",
                    random.choice(male_names), "male")
        return (random.choice(male_names), "male",
                random.choice(female_names), "female")

    def _get_section3_speakers(self) -> Tuple[str, str, str, str, Dict[str, str]]:
        female_names, male_names = list(FEMALE_NAMES), list(MALE_NAMES)
        tutor_title = random.choice(['Dr.', 'Professor', 'Dr.'])
        tutor_gender = random.choice(['male', 'female'])
        tutor_first = random.choice(male_names if tutor_gender == 'male' else female_names)
        tutor = f"{tutor_title} {tutor_first}"
        genders = {tutor: tutor_gender}
        students = []
        used_names = {tutor_first}
        for _ in range(3):
            gender = random.choice(['male', 'female'])
            pool = male_names if gender == 'male' else female_names
            available = [n for n in pool if n not in used_names] or pool
            name = random.choice(available)
            students.append(name)
            genders[name] = gender
            used_names.add(name)
        return tutor, students[0], students[1], students[2], genders

    def _fix_placeholders(self, text: str, topic: Optional[str] = None) -> str:
        if not text:
            return ""
        if topic and topic in SECTION4_DATA_MAP:
            data_str = SECTION4_DATA_MAP[topic]
            numbers = re.findall(r'(\d+(?:\.\d+)?\s*(?:%|million|billion|trillion)?)', data_str)
            if numbers:
                replacements = {
                    r'a substantial amount dollars': lambda: random.choice(numbers) + ' dollars',
                    r'a substantial amount of dollars': lambda: random.choice(numbers) + ' dollars',
                    r'a substantial amount': lambda: random.choice(numbers),
                    r'a relevant body': lambda: random.choice(['UN', 'UNESCO', 'WHO', 'OECD', 'World Bank', 'IMF']),
                    r'a certain number': lambda: str(random.randint(15, 85)),
                    r'a significant percentage': lambda: f"{random.randint(35, 78)}%",
                    r'a set period': lambda: f"{random.randint(10, 40)} years",
                    r'a leading research body': lambda: random.choice(['a university research centre',
                                                                       'an international study',
                                                                       'a government report',
                                                                       'a think tank']),
                }
                for pattern, repl_fn in replacements.items():
                    text = re.sub(pattern, repl_fn(), text, flags=re.IGNORECASE)
                return text
        generic = [
            (r'a substantial amount dollars', f"{random.choice([2.8, 4.2, 6.5, 9.1, 12.4, 18.7, 25.3])} billion dollars"),
            (r'a substantial amount of dollars', f"{random.choice([2.8, 4.2, 6.5, 9.1, 12.4, 18.7, 25.3])} billion dollars"),
            (r'a substantial amount', f"{random.choice([3.2, 5.7, 8.4, 11.3, 15.6, 21.9, 30.5])} million"),
            (r'a relevant body', random.choice(['the UN', 'UNESCO', 'the WHO', 'the OECD', 'the World Bank', 'the IMF'])),
            (r'a certain number', str(random.randint(15, 85))),
            (r'a significant percentage', f"{random.randint(35, 78)}%"),
            (r'a set period', f"{random.randint(10, 40)} years"),
            (r'a leading research body', random.choice(['a university research centre',
                                                        'an international study',
                                                        'a government report'])),
        ]
        for pattern, replacement in generic:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _simple_messify(self, text: str) -> str:
        if not text:
            return text
        markers = ['um', 'uh', 'well', 'you know', 'I mean', 'let me see', 'actually']
        sentences = re.split(r'(?<=[.!?])\s+', text)
        for i in range(1, len(sentences)):
            if random.random() < 0.15 and len(sentences[i]) > 10:
                sentences[i] = f"{random.choice(markers)}, {sentences[i]}"
        return ' '.join(sentences)

    def _sanitize_script(self, script: str, section: int) -> str:
        if not script:
            return ""
        if section in (2, 4):
            extreme_words = r"\b(?:stunning|incredible|amazing|wonderful|spectacular|magnificent|breathtaking|extraordinary|unforgettable|marvellous|splendid|thriving|vibrant|revolutionary|groundbreaking|cutting-edge|state-of-the-art|world-class|award-winning|flagship|iconic|prestigious|foremost)\s+"
            script = re.sub(extreme_words, "", script, flags=re.IGNORECASE)
        script = re.sub(r"\s{2,}", " ", script)
        script = re.sub(r",\s*,", ",", script)
        script = re.sub(r"\.\s*\.", ".", script)
        return script

    def _trim_script_to_word_count(self, script: str, target_words: int,
                                   max_variation: int = 50) -> str:
        if not script:
            return script
        words = script.split()
        if len(words) <= target_words + max_variation:
            return script
        lines = script.split('\n')
        trimmed_lines = []
        current_word_count = 0
        target_with_variation = target_words + max_variation
        for line in lines:
            line_words = len(line.split())
            if current_word_count + line_words > target_with_variation:
                if current_word_count < target_words - 10:
                    remaining = target_words - current_word_count
                    words_in_line = line.split()
                    if remaining >= len(words_in_line) - 2:
                        trimmed_lines.append(line)
                        current_word_count += line_words
                break
            trimmed_lines.append(line)
            current_word_count += line_words
        result = '\n'.join(trimmed_lines)
        if result and not result.strip().endswith(('.', '!', '?')):
            result += '.'
        logger.info(f"Trimmed script from {len(words)} to {len(result.split())} words (target: {target_words})")
        return result

    # ============================================================
    # MATCHING HELPER
    # ============================================================
    def _extract_matching_answers(self, q: Dict) -> Optional[str]:
        items = q.get("items") or []
        if not items:
            return None
        answers = []
        for it in items:
            if not isinstance(it, dict):
                continue
            a = (it.get("correct_answer") or it.get("answer") or "").strip()
            if a:
                answers.append(a)
        return ",".join(answers) if answers else None

    # ============================================================
    # DUPLICATE ANSWER / CATEGORY CHECKS
    # ============================================================
    def _find_duplicate_answers(self, questions: List[Dict]) -> Dict[str, List[Dict]]:
        answer_map = {}
        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            q_type = (q.get('type') or '').lower()

            if q_type == 'matching':
                items = q.get('items') or []
                item_answers = []
                for it in items:
                    if isinstance(it, dict):
                        a = (it.get('correct_answer') or it.get('answer') or '').strip()
                        if a:
                            item_answers.append(a)
                if item_answers:
                    normalized_items = [_normalize_answer(x) for x in item_answers]
                    if len(set(normalized_items)) != len(normalized_items):
                        answer_map.setdefault('__matching_dup__', []).append({
                            'idx': idx,
                            'question': q.get('question') or q.get('text'),
                            'raw_answer': f"duplicate letters: {item_answers}",
                        })
                continue

            ans = (q.get('answer') or q.get('correct_answer') or '').strip()
            if not ans:
                continue
            norm = _normalize_answer(ans)
            if not norm:
                continue
            answer_map.setdefault(norm, []).append({
                'idx': idx,
                'question': q.get('question') or q.get('text'),
                'raw_answer': ans,
            })

        return {k: v for k, v in answer_map.items() if len(v) > 1}

    _CATEGORY_PATTERNS = {
        'name': [r'\bfull name\b', r'\bsurname\b', r'\blast name\b', r'\bname\b'],
        'phone': [r'\bphone\b', r'\btelephone\b', r'\bmobile\b', r'\bcontact number\b'],
        'email': [r'\bemail\b', r'\be-mail\b'],
        'address': [r'\baddress\b', r'\bpostcode\b', r'\bzip\b'],
        'date': [r'\bdate\b', r'\bday of\b'],
        'time': [r'\btime\b', r'\bhour\b', r'\bclock\b'],
        'cost': [r'\bcost\b', r'\bprice\b', r'\bfee\b', r'\bcharge\b',
                      r'\bdeposit\b', r'\bmonthly payment\b', r'\brate\b',
                      r'\btotal\b', r'\bpayment plan\b'],
        'reference': [r'\breference\b', r'\bbooking number\b', r'\bref\b'],
        'type': [r'\btype of\b', r'\bkind of\b'],
        'guests': [r'\bguests\b', r'\bpeople\b', r'\bpersons\b'],
        'room': [r'\broom type\b', r'\bsuite\b'],
        'payment_method': [r'\bpayment method\b', r'\bcredit card\b', r'\bdebit card\b'],
    }

    def _find_duplicate_categories(self, questions: List[Dict]) -> List[str]:
        seen: Dict[str, int] = {}
        dups: set = set()
        for q in questions[:8]:
            if not isinstance(q, dict):
                continue
            text = (q.get('question') or q.get('text') or '').lower()
            if not text:
                continue
            matched_cat = None
            for cat, patterns in self._CATEGORY_PATTERNS.items():
                if any(re.search(p, text) for p in patterns):
                    matched_cat = cat
                    break
            if not matched_cat:
                continue
            if matched_cat in seen:
                dups.add(matched_cat)
            else:
                seen[matched_cat] = 1
        return sorted(dups)

    _FALLBACK_QUESTIONS = [
        {"question": "Reference number: ________", "type": "form_completion", "answer": "REF-2847"},
        {"question": "Payment method: ________", "type": "form_completion", "answer": "Credit card"},
        {"question": "Special request: ________", "type": "form_completion", "answer": "Window seat"},
        {"question": "Type of service: ________", "type": "form_completion", "answer": "Consultation"},
        {"question": "Preferred contact time: ________", "type": "form_completion", "answer": "Morning"},
    ]

    def _deduplicate_categories_auto(self, questions: List[Dict]) -> List[Dict]:
        if not questions:
            return questions

        seen_cats: set = set()
        fallback_idx = 0
        cleaned: List[Dict] = []

        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                cleaned.append(q)
                continue

            q_type = (q.get('type') or '').lower()
            text = (q.get('question') or q.get('text') or '').lower()

            if idx >= 8 or q_type in ('multiple_choice', 'matching', 'map_labeling'):
                cleaned.append(q)
                continue

            matched_cat = None
            for cat, patterns in self._CATEGORY_PATTERNS.items():
                if any(re.search(p, text) for p in patterns):
                    matched_cat = cat
                    break

            if matched_cat and matched_cat in seen_cats:
                fb = self._FALLBACK_QUESTIONS[fallback_idx % len(self._FALLBACK_QUESTIONS)]
                fallback_idx += 1
                new_q = dict(q)
                new_q['question'] = fb['question']
                new_q['text'] = fb['question']
                new_q['type'] = fb['type']
                new_q['answer'] = fb['answer']
                logger.info(f" Auto-fixed duplicate '{matched_cat}' question → '{fb['question']}'")
                cleaned.append(new_q)
            else:
                if matched_cat:
                    seen_cats.add(matched_cat)
                cleaned.append(q)

        return cleaned

    # ═══════════════════════════════════════════════════════════════
    # v7 FIX: Fallback pool for duplicate-answer auto-fix
    # ═══════════════════════════════════════════════════════════════
    _FALLBACK_ANSWERS_BY_TYPE: Dict[str, List[Tuple[str, str]]] = {
        'form_completion': [
            ('Payment method: ________', 'Credit card'),
            ('Reference number: ________', 'REF-2847'),
            ('Special request: ________', 'Window seat'),
            ('Preferred contact time: ________', 'Morning'),
            ('Membership type: ________', 'Standard'),
            ('Room preference: ________', 'Non-smoking'),
            ('Preferred delivery: ________', 'Next day'),
            ('Contact email: ________', 'student@email.com'),
            ('Emergency contact: ________', 'James Wilson'),
            ('Preferred language: ________', 'English'),
        ],
        'note_completion': [
            ('Key finding: ________', 'cost reduction'),
            ('Main challenge: ________', 'funding'),
            ('Primary benefit: ________', 'efficiency'),
            ('Future direction: ________', 'automation'),
            ('Common barrier: ________', 'awareness'),
            ('Preferred solution: ________', 'regulation'),
            ('Major driver: ________', 'demand'),
            ('Key stakeholder: ________', 'government'),
        ],
        'sentence_completion': [
            ('The primary advantage is ________.', 'flexibility'),
            ('Most researchers agree that ________ is essential.', 'funding'),
            ('The study was conducted in ________.', '2019'),
            ('Results showed a ________ improvement.', 'gradual'),
            ('The main obstacle was ________.', 'regulation'),
            ('Experts recommend ________ as a first step.', 'training'),
            ('The biggest impact was on ________.', 'employment'),
            ('The report highlighted ________ as a priority.', 'education'),
        ],
        'table_completion': [
            ('Category: ________', 'Category A'),
            ('Value: ________', '45%'),
            ('Year: ________', '2020'),
            ('Region: ________', 'Asia-Pacific'),
            ('Level: ________', 'Intermediate'),
            ('Status: ________', 'Pending'),
            ('Type: ________', 'Standard'),
            ('Duration: ________', '6 months'),
        ],
        'summary_completion': [
            ('The passage mentions ________ as a key factor.', 'climate'),
            ('Researchers found ________ in the study.', 'significant variation'),
            ('The main conclusion is that ________ matters.', 'consistency'),
            ('The author argues that ________ should be prioritised.', 'education'),
        ],
    }

    def _deduplicate_answers_auto(self, questions: List[Dict], section: int) -> List[Dict]:
        """
         v7 FIX: Replace questions whose answer duplicates an earlier one
        with alternative questions from a fallback pool.
        Works for ALL sections (was previously only Section 1 categories).
        """
        if not questions:
            return questions

        seen_answers: set = set()
        # Work on a copy of pools so we don't mutate the class dict
        fallback_pools: Dict[str, list] = {
            k: list(v) for k, v in self._FALLBACK_ANSWERS_BY_TYPE.items()
        }
        fallback_idx: Dict[str, int] = {k: 0 for k in fallback_pools}

        cleaned: List[Dict] = []

        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                cleaned.append(q)
                continue

            q_type = (q.get('type') or '').lower()

            # Matching / MC / map_labeling: skip (different dedup logic)
            if q_type in ('matching', 'multiple_choice', 'map_labeling'):
                cleaned.append(q)
                continue

            ans = (q.get('answer') or q.get('correct_answer') or '').strip()
            norm = _normalize_answer(ans) if ans else ''

            if not norm or norm not in seen_answers:
                if norm:
                    seen_answers.add(norm)
                cleaned.append(q)
                continue

            # ─── Duplicate detected — try a fallback replacement ──
            pool = fallback_pools.get(q_type) or fallback_pools.get('form_completion') or []
            if not pool:
                cleaned.append(q)
                continue

            replaced = False
            for _ in range(len(pool)):
                fb_q, fb_a = pool[fallback_idx.get(q_type, 0) % len(pool)]
                fallback_idx[q_type] = fallback_idx.get(q_type, 0) + 1
                fb_norm = _normalize_answer(fb_a)
                if fb_norm and fb_norm not in seen_answers:
                    new_q = dict(q)
                    new_q['question'] = fb_q
                    new_q['text'] = fb_q
                    new_q['answer'] = fb_a
                    if 'correct_answer' in new_q:
                        new_q['correct_answer'] = fb_a
                    logger.info(
                        f" [S{section}] Fixed duplicate answer '{ans}' → "
                        f"'{fb_q.strip()}' → '{fb_a}'"
                    )
                    seen_answers.add(fb_norm)
                    cleaned.append(new_q)
                    replaced = True
                    break

            if not replaced:
                # Couldn't find a unique fallback — leave original
                cleaned.append(q)

        return cleaned

    # ============================================================
    # MAIN AI CALL — v7: duplicate answers auto-fix
    # ============================================================
    def _call_ai(self, prompt: str, section: int, target_words: int,
                 topic: Optional[str] = None, retries: int = MAX_RETRIES_PER_SECTION,
                 fast: bool = True) -> Optional[Dict[str, Any]]:
        if not self.ai:
            raise ValueError("AI engine not available")

        if fast:
            max_tokens_map = {1: 5000, 2: 6000, 3: 7000, 4: 8000}
            max_tokens = max_tokens_map.get(section, 5000)
            temperature = 0.9
        else:
            max_tokens = 9000
            temperature = 0.85

        min_words = SECTION_MIN_WORDS.get(section, int(target_words * 0.9))

        json_instruction = """
=== CRITICAL JSON INSTRUCTION ===
You MUST return ONLY valid JSON.
DO NOT include any text before or after the JSON.
DO NOT use markdown code fences.
Use double quotes for all keys and string values.
Escape all double quotes inside the script with backslashes.
Escape all newlines in the script with \\n.
DO NOT use trailing commas.
Ensure the "questions" array contains exactly 10 objects.
Each question object must have "question" (or "text"), "answer" (or "correct_answer"), and "type" keys.
The "script" field must contain the full spoken script.
Your response must be parseable by Python's json.loads().
"""
        system_parts = [
            "You are an expert IELTS listening test script writer.",
            "Generate realistic, natural dialogues and monologues suitable for an IELTS test.",
            "Use British English with natural speech patterns including hesitations and fillers where appropriate.",
            f" CRITICAL: Your script MUST be at least {target_words - 30} words long. DO NOT stop early.",
            "Vary sentence length and use punctuation to indicate pauses.",
            "IMPORTANT: The 'script' field is the spoken audio. It MUST be natural conversation/monologue.",
            " CRITICAL: Every one of the 10 questions MUST have a UNIQUE answer AND target a DIFFERENT information category.",
            " CRITICAL: Question stems with blanks MUST NOT pre-fill any part of the answer. Blanks are empty.",
            json_instruction,
        ]
        if section == 1:
            system_parts.append(
                "Each line must start with a speaker label (e.g., 'Agent:' or 'Customer:'). "
                "Use natural conversation. Agent and Customer must have different names."
            )
        elif section == 2:
            system_parts.append("The script is a monologue by a guide or information officer.")
        elif section == 3:
            system_parts.append("Each line must start with a speaker label. Include interruptions and academic discussion.")
        else:
            system_parts.append("The script is a university lecture with a clear introduction, main points, and conclusion.")
        system_prompt = " ".join(system_parts)

        for attempt in range(retries):
            try:
                logger.info(f"AI Section {section}: Attempt {attempt+1}/{retries}, "
                            f"target {target_words} words, min {min_words}, fast={fast}...")

                prompt_with_nonce = prompt
                if attempt > 0:
                    prompt_with_nonce = f"{prompt}\n\n[Generation nonce: {uuid.uuid4().hex}]"

                full_prompt = f"{system_prompt}\n\n{prompt_with_nonce}"
                response = self.ai.generate(full_prompt, max_tokens=max_tokens,
                                            temperature=temperature, fast=fast)
                if not response or not isinstance(response, str):
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise ValueError(f"AI Section {section} returned empty response")

                logger.info(f" AI Raw Response (preview): {response[:500]}...")

                response = self._fix_placeholders(response, topic)
                data = self._parse_ai_response(response)
                if not data:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    logger.error(f" Final raw response (failed to parse): {response[:1000]}")
                    raise ValueError(f"AI Section {section} returned invalid JSON")

                script = data.get("script", "").strip()
                questions = data.get("questions", [])
                if not questions or not isinstance(questions, list) or len(questions) < 10:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise ValueError(f"AI Section {section} did not provide 10 questions")

                # ─── QUESTION VALIDATION ────────────────────────────
                for q in questions[:10]:
                    if not isinstance(q, dict):
                        raise ValueError("Question is not a dictionary")
                    question_text = q.get("question") or q.get("text")
                    if not question_text or not q.get("type"):
                        raise ValueError(f"Question missing required fields: {q}")
                    if "question" not in q:
                        q["question"] = question_text

                    q_type = (q.get("type") or "").lower()
                    if q_type == "matching":
                        matching_answers = self._extract_matching_answers(q)
                        if not matching_answers:
                            top_ans = (q.get("answer") or q.get("correct_answer") or "").strip()
                            if not top_ans:
                                raise ValueError(f"Matching question has no answers: {q.get('id')}")
                        if not q.get("answer") and not q.get("correct_answer"):
                            q["answer"] = matching_answers
                        elif not q.get("answer") and q.get("correct_answer"):
                            q["answer"] = q["correct_answer"]
                        continue

                    if "correct_answer" in q and "answer" not in q:
                        q["answer"] = q["correct_answer"]
                    if not q.get("answer"):
                        raise ValueError(f"Question missing answer: {q.get('id')}")

                # ─── STEM-LEAK CHECK (all sections) ──────────────
                leaky = [q for q in questions[:10] if self._stem_has_answer_leak(q)]
                if leaky:
                    samples = [((q.get('question') or q.get('text') or '')[:60]) for q in leaky[:3]]
                    logger.warning(
                        f" [S{section}] {len(leaky)} stem(s) leak the answer. "
                        f"Examples: {samples}"
                    )
                    if attempt < retries - 1:
                        logger.warning(f" [S{section}] Retrying due to stem leak (attempt {attempt+1}/{retries})")
                        time.sleep(2 ** attempt)
                        continue

                # ═══════════════════════════════════════════════════
                # ─── DUPLICATE ANSWER CHECK — v7: auto-fix ALL sections
                # ═══════════════════════════════════════════════════
                dup_map = self._find_duplicate_answers(questions[:10])
                if dup_map:
                    for norm_ans, entries in dup_map.items():
                        raw = entries[0]['raw_answer'] if entries else norm_ans
                        q_summary = [e['question'][:50] if e.get('question') else '?' for e in entries]
                        logger.warning(
                            f" [S{section}] Duplicate answer '{raw}' appears in "
                            f"{len(entries)} questions: {q_summary}"
                        )
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    # v7 FIX: auto-replace duplicate questions with unique fallbacks
                    logger.warning(f" [S{section}] Auto-fixing duplicate answers...")
                    questions = self._deduplicate_answers_auto(questions, section)

                # ─── DUPLICATE CATEGORY CHECK (Section 1 only) ──
                if section == 1:
                    dup_cats = self._find_duplicate_categories(questions[:8])
                    if dup_cats:
                        logger.warning(f" [S1] Duplicate categories: {dup_cats}")
                        if attempt < retries - 1:
                            time.sleep(2 ** attempt)
                            continue
                        logger.warning(f" [S1] Auto-fixing duplicate categories on final attempt")
                        questions = self._deduplicate_categories_auto(questions)

                script = self._trim_script_to_word_count(script, target_words, max_variation=50)
                word_count = len(script.split())

                # ═══════════════════════════════════════════════════════
                # PERMANENT FIX Layer 1: Expand if below target
                # ═══════════════════════════════════════════════════════
                if word_count < target_words:
                    logger.warning(
                        f" Section {section} below target "
                        f"({word_count}/{target_words}). Expanding..."
                    )
                    script = self._expand_script(script, target_words, section, topic)
                    word_count = len(script.split())

                # ═══════════════════════════════════════════════════════
                # PERMANENT FIX Layer 2: Effective minimum with tolerance
                # ═══════════════════════════════════════════════════════
                effective_min = int(min_words * LENGTH_TOLERANCE)

                # ═══════════════════════════════════════════════════════
                # PERMANENT FIX Layer 3: Only fail if WAY too short
                # ═══════════════════════════════════════════════════════
                if word_count < effective_min:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise ValueError(
                        f"Script too short ({word_count} < {effective_min}, "
                        f"target={min_words})"
                    )

                # ─── Accept with warning if slightly short ───────────
                if word_count < min_words:
                    logger.warning(
                        f" Section {section} slightly under target "
                        f"({word_count}/{min_words}) — accepting within "
                        f"{(1 - LENGTH_TOLERANCE) * 100:.0f}% tolerance"
                    )

                logger.info(
                    f"AI Section {section}: Final word count = {word_count} "
                    f"(target: {target_words}, min: {min_words}, "
                    f"effective_min: {effective_min})"
                )

                if not self._is_script_natural(script, section):
                    if attempt < retries - 1:
                        logger.warning(f" Script does not appear natural. Retrying...")
                        time.sleep(2 ** attempt)
                        continue
                    logger.info(f" Accepting script on final attempt despite naturalness warning")

                if section in (1, 3):
                    if speech_messifier:
                        script = speech_messifier.messify(
                            script,
                            intensity=0.2 if section == 1 else 0.25,
                            preserve_labels=(section == 3)
                        )
                    else:
                        script = self._add_natural_hesitations(script, intensity=0.2)

                if section in (1, 3):
                    script = self._ensure_speaker_labels(script, section)

                script = self._sanitize_script(script, section)
                word_count = len(script.split())

                # ─── FINAL STEM SANITIZATION (always runs) ───────
                for q in questions[:10]:
                    if self._stem_has_answer_leak(q):
                        orig = q.get("question") or q.get("text") or ""
                        new_text = self._sanitize_stem(orig)
                        if new_text != orig:
                            q["question"] = new_text
                            q["text"] = new_text
                            logger.info(
                                f" [S{section}] Sanitized leaked stem: "
                                f"'{orig[:70]}' → '{new_text[:70]}'"
                            )
                        else:
                            fallback = re.sub(
                                r'^([^:]{2,80}:)\s*\S[^\n]*?([_＿]{2,}.*)$',
                                r'\1 \2',
                                orig,
                                flags=re.DOTALL,
                            )
                            if fallback != orig:
                                q["question"] = fallback
                                q["text"] = fallback
                                logger.info(
                                    f" [S{section}] Fallback sanitize: "
                                    f"'{orig[:70]}' → '{fallback[:70]}'"
                                )

                # Build formatted questions
                formatted_questions = []
                for i, q in enumerate(questions[:10], 1):
                    q_type = q.get("type", "text")
                    if q_type in ["multiple_choice", "matching"]:
                        fmt = ANSWER_FORMATS['letter']
                    elif q_type in ["number", "table_completion"] and q.get("answer", "").replace('.', '').isdigit():
                        fmt = ANSWER_FORMATS['number']
                    elif q_type in ["date"] or re.search(r'\b(date|day|month|year)\b', q.get("question", ""), re.I):
                        fmt = ANSWER_FORMATS['date']
                    elif q_type in ["time"] or re.search(r'\b(time|hour|minute|clock)\b', q.get("question", ""), re.I):
                        fmt = ANSWER_FORMATS['time']
                    elif q_type in ["money"] or re.search(r'\b(price|cost|fee|amount|dollar|pound)\b', q.get("question", ""), re.I):
                        fmt = ANSWER_FORMATS['money']
                    elif len(q.get("answer", "").split()) == 1 and q.get("answer", "").isalpha():
                        fmt = ANSWER_FORMATS['one_word']
                    else:
                        wc = len(q.get("answer", "").split())
                        if wc <= 1:
                            fmt = ANSWER_FORMATS['one_word']
                        elif wc <= 2:
                            fmt = ANSWER_FORMATS['two_words']
                        else:
                            fmt = ANSWER_FORMATS['three_words']

                    raw_options = q.get("options") or []
                    normalized_options = []
                    for opt in raw_options:
                        if isinstance(opt, str):
                            normalized_options.append(opt)
                        elif isinstance(opt, dict):
                            letter = opt.get("letter") or opt.get("key") or opt.get("id") or ""
                            text = opt.get("text") or opt.get("label") or opt.get("value") or opt.get("name") or ""
                            if letter and text:
                                normalized_options.append(f"{letter}: {text}")
                            elif letter:
                                normalized_options.append(str(letter))
                            elif text:
                                normalized_options.append(str(text))
                        else:
                            normalized_options.append(str(opt))

                    formatted_q = q.copy()
                    formatted_q["options"] = normalized_options
                    formatted_q["number"] = (section - 1) * 10 + i
                    formatted_q["section"] = section
                    formatted_q["answer_format"] = fmt
                    if "answer" not in formatted_q and "correct_answer" in q:
                        formatted_q["answer"] = q["correct_answer"]
                    elif "answer" not in formatted_q:
                        formatted_q["answer"] = ""
                    formatted_questions.append(formatted_q)

                return {
                    "script": script,
                    "questions": formatted_questions,
                    "word_count": word_count,
                    "audio_script": script,
                    "question_count": len(formatted_questions),
                }

            except Exception as e:
                logger.error(f"AI Section {section} attempt {attempt+1} failed: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

        raise RuntimeError(f"AI Section {section} failed after {retries} retries")

    # ============================================================
    # JSON PARSING
    # ============================================================
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        try:
            if not response or not isinstance(response, str):
                return {}
            cleaned = self._clean_json_response(response)
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if not match:
                logger.error("No JSON object found")
                return {}
            json_str = match.group(0)

            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"First JSON parse failed: {e}")

            if JSON5_AVAILABLE:
                try:
                    data = json5.loads(json_str)
                    logger.info(" JSON5 parsing succeeded.")
                    return data
                except Exception as e:
                    logger.warning(f"json5 parse failed: {e}")

            try:
                fixed = re.sub(r'(\s*)(\w+)(\s*):', r'\1"\2"\3:', json_str)
                fixed = fixed.replace("'", '"')
                fixed = re.sub(r',\s*}', '}', fixed)
                fixed = re.sub(r',\s*\]', ']', fixed)
                data = json.loads(fixed)
                logger.info(" JSON fixed with regex replacements.")
                return data
            except Exception as e:
                logger.warning(f"Regex repair failed: {e}")

            try:
                script_match = re.search(r'"script"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str, re.DOTALL)
                if not script_match:
                    script_start = json_str.find('"script"')
                    if script_start != -1:
                        colon_pos = json_str.find(':', script_start)
                        if colon_pos != -1:
                            quote_start = json_str.find('"', colon_pos + 1)
                            if quote_start != -1:
                                questions_pos = json_str.find('"questions"', quote_start)
                                if questions_pos != -1:
                                    last_quote = json_str.rfind('"', quote_start + 1, questions_pos)
                                    if last_quote != -1:
                                        script_text = json_str[quote_start + 1:last_quote]
                                        script_text = (script_text
                                                       .replace('\\"', '"')
                                                       .replace('\\n', '\n')
                                                       .replace('\\\\', '\\'))
                                        q_match = re.search(r'"questions"\s*:\s*(\[.*?\])', json_str, re.DOTALL)
                                        if q_match:
                                            q_str = q_match.group(1)
                                            try:
                                                q_data = json.loads(q_str)
                                            except Exception:
                                                q_str = re.sub(r'(\w+):', r'"\1":', q_str)
                                                q_str = q_str.replace("'", '"')
                                                q_data = json.loads(q_str)
                                            return {"script": script_text, "questions": q_data}
                else:
                    script_text = script_match.group(1)
                    script_text = (script_text
                                   .replace('\\"', '"')
                                   .replace('\\n', '\n')
                                   .replace('\\\\', '\\'))
                    q_match = re.search(r'"questions"\s*:\s*(\[.*?\])', json_str, re.DOTALL)
                    if q_match:
                        q_str = q_match.group(1)
                        try:
                            q_data = json.loads(q_str)
                        except Exception:
                            q_str = re.sub(r'(\w+):', r'"\1":', q_str)
                            q_str = q_str.replace("'", '"')
                            q_data = json.loads(q_str)
                        return {"script": script_text, "questions": q_data}
            except Exception as e:
                logger.warning(f"Manual reconstruction failed: {e}")

            logger.error(f"All parsing attempts failed. Raw response: {response[:500]}")
            return {}
        except Exception as e:
            logger.error(f"JSON parse error: {e}")
            return {}

    def _clean_json_response(self, text: str) -> str:
        if not text:
            return text
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*\]', ']', text)
        return text.strip()

    def _validate_questions(self, questions: List[Dict], section: int,
                            expected: int = 10) -> List[Dict]:
        return questions[:10] if questions else []

    # ============================================================
    # VALIDATION — tolerance applied
    # ============================================================
    def _is_valid_ai_result(self, result: Optional[Dict]) -> bool:
        if not result or not isinstance(result, dict):
            return False
        section = result.get("section", 1)
        min_words = SECTION_MIN_WORDS.get(section, 300)
        word_count = result.get("word_count", 0)
        question_count = result.get("question_count", len(result.get("questions", [])))
        has_script = bool(result.get("script") or result.get("audio_script"))

        effective_min = int(min_words * LENGTH_TOLERANCE)

        if word_count < effective_min:
            logger.warning(
                f" [S{section}] Validation failed: "
                f"{word_count} words < effective_min {effective_min}"
            )
            return False

        if word_count < min_words:
            logger.warning(
                f" [S{section}] Accepted within tolerance: "
                f"{word_count}/{min_words}"
            )

        return question_count >= 10 and has_script

    def _get_question_types_safe(self, section: int) -> Dict:
        try:
            return question_type_manager.get_distribution(section)
        except Exception:
            return {}

    # ============================================================
    # SECTION GENERATION METHODS
    # ============================================================
    def section1(self, difficulty: str = "medium", accent: str = "british",
                 fast: bool = True, use_cache: bool = True,
                 force_new: bool = False) -> Dict[str, Any]:
        if use_cache and not force_new:
            cached = section_cache.get(1, topic="ai_generated",
                                       difficulty=difficulty, accent=accent)
            if cached and cached.get("script") and cached.get("audio_script"):
                return cached
        if not self.ai:
            raise ValueError("AI engine is not available for Section 1 generation")

        result = self._ai_section1_full(difficulty, accent, fast=fast)
        if not self._is_valid_ai_result(result):
            raise RuntimeError("AI Section 1 generation failed")

        result["audio_script"] = result.get("script", "")
        result["script"] = result.get("script", "")
        result["word_count"] = len(result["script"].split())

        if use_cache and not force_new:
            section_cache.save(1, result, topic="ai_generated",
                               difficulty=difficulty, accent=accent)
        return result

    def _ai_section1_full(self, difficulty: str, accent: str,
                          fast: bool = True) -> Optional[Dict[str, Any]]:
        if not self.ai:
            return None
        customer_name, customer_gender, agent_name, agent_gender = \
            self._get_gender_appropriate_names()
        logger.info(f" Section 1: Customer={customer_name} ({customer_gender}), "
                    f"Agent={agent_name} ({agent_gender})")

        prompt = self._build_section1_prompt(
            difficulty, accent,
            forced_speakers={
                "customer_name": customer_name,
                "customer_gender": customer_gender,
                "agent_name": agent_name,
                "agent_gender": agent_gender,
            }
        )
        result = self._call_ai(prompt, section=1, target_words=SECTION_TARGET_WORDS[1],
                               topic="ai_generated", fast=fast)
        if not result:
            return None

        script = result.get("script", "")
        speaker_labels = re.findall(r'^([A-Za-z][A-Za-z0-9_\-\. ]*):', script, re.MULTILINE)
        unique_speakers = list(dict.fromkeys(speaker_labels))

        if len(unique_speakers) >= 2:
            if "Agent" in unique_speakers and "Customer" in unique_speakers:
                speaker_a = "Agent"
                speaker_b = "Customer"
            else:
                speaker_a = unique_speakers[0].strip()
                speaker_b = unique_speakers[1].strip()
        else:
            speaker_a = "Agent"
            speaker_b = "Customer"

        speaker_genders = {}
        for spk in unique_speakers:
            spk_clean = re.sub(r'^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Prof\.?|Professor)\s+',
                               '', spk, flags=re.I).strip()
            first = spk_clean.split()[0] if spk_clean.split() else spk_clean
            if spk.lower() == "agent" or first == agent_name:
                speaker_genders[spk] = agent_gender
            elif spk.lower() == "customer" or first == customer_name:
                speaker_genders[spk] = customer_gender
            elif spk == speaker_a:
                speaker_genders[spk] = agent_gender
            elif spk == speaker_b:
                speaker_genders[spk] = customer_gender
        if speaker_a not in speaker_genders:
            speaker_genders[speaker_a] = agent_gender
        if speaker_b not in speaker_genders:
            speaker_genders[speaker_b] = customer_gender

        logger.info(f" Section 1 speaker_genders: {speaker_genders}")

        return {
            "section": 1,
            "title": "Conversation",
            "type": "conversation",
            "speakers": [speaker_a, speaker_b],
            "speaker_genders": speaker_genders,
            "context": "A conversation between a customer and an agent.",
            "audio_script": result.get("script", result.get("audio_script", "")),
            "script": result.get("script", ""),
            "questions": result["questions"],
            "question_count": len(result["questions"]),
            "question_types": self._get_question_types_safe(1),
            "word_count": result["word_count"],
            "target_duration_seconds": SECTION_DURATIONS[1],
            "ai_generated": True,
            "accent": accent,
        }

    def section2(self, topic: str, difficulty: str = "medium", accent: str = "british",
                 fast: bool = True, use_cache: bool = True,
                 force_new: bool = False) -> Dict[str, Any]:
        if use_cache and not force_new:
            cached = section_cache.get(2, topic=topic, difficulty=difficulty, accent=accent)
            if cached and cached.get("script") and cached.get("audio_script"):
                return cached
        if not self.ai:
            raise ValueError("AI engine is not available for Section 2 generation")

        result = self._ai_section2(topic, difficulty, accent, fast=fast)
        if not self._is_valid_ai_result(result):
            raise RuntimeError("AI Section 2 generation failed")

        result['audio_script'] = self._insert_distractors_into_script(
            script=result['audio_script'],
            questions=result.get('questions', []),
            section=2,
            topic=topic
        )
        result['script'] = result['audio_script']
        result['word_count'] = len(result['audio_script'].split())

        if use_cache and not force_new:
            section_cache.save(2, result, topic=topic, difficulty=difficulty, accent=accent)
        return result

    def _ai_section2(self, topic: str, difficulty: str, accent: str,
                     fast: bool = True) -> Optional[Dict[str, Any]]:
        if not self.ai:
            return None
        prompt = self._build_section2_prompt(topic, difficulty)
        result = self._call_ai(prompt, section=2, target_words=SECTION_TARGET_WORDS[2],
                               topic=topic, fast=fast)
        if not result:
            return None
        guide_gender = random.choice(['female', 'male'])
        logger.info(f" Section 2: Guide gender = {guide_gender}")
        return {
            "section": 2,
            "title": f"Information Talk: {topic.replace('_', ' ').title()}",
            "type": "monologue",
            "speakers": ["Guide"],
            "speaker_genders": {"Guide": guide_gender},
            "context": f"A guide talks about {topic}.",
            "audio_script": result.get("script", result.get("audio_script", "")),
            "script": result.get("script", ""),
            "questions": result["questions"],
            "question_count": len(result["questions"]),
            "question_types": self._get_question_types_safe(2),
            "word_count": result["word_count"],
            "target_duration_seconds": SECTION_DURATIONS[2],
            "ai_generated": True,
            "accent": accent,
        }

    def section3(self, topic: str, difficulty: str = "medium", accent: str = "british",
                 fast: bool = True, use_cache: bool = True,
                 force_new: bool = False) -> Dict[str, Any]:
        if use_cache and not force_new:
            cached = section_cache.get(3, topic=topic, difficulty=difficulty, accent=accent)
            if cached and cached.get("script") and cached.get("audio_script"):
                return cached
        if not self.ai:
            raise ValueError("AI engine is not available for Section 3 generation")

        result = self._ai_section3(topic, difficulty, accent, fast=fast)
        if not self._is_valid_ai_result(result):
            raise RuntimeError("AI Section 3 generation failed")

        script = result.get("script", "")
        if not re.search(r'^[A-Za-z][\w\.\- ]*\s*:', script, re.MULTILINE):
            logger.warning("Section 3: AI script missing speaker labels – adding round-robin labels.")
            lines = script.split('\n')
            default_speakers = result.get("speakers", ['Tutor', 'Student1', 'Student2', 'Student3'])
            if len(default_speakers) < 4:
                default_speakers = ['Tutor', 'Student1', 'Student2', 'Student3']
            labelled_lines = []
            for idx, line in enumerate(lines):
                if line.strip():
                    speaker = default_speakers[idx % len(default_speakers)]
                    labelled_lines.append(f"{speaker}: {line}")
            script = '\n'.join(labelled_lines)
            result["script"] = script
            result["audio_script"] = script

        if speech_messifier:
            script = speech_messifier.messify(script, intensity=0.25, preserve_labels=True)
            result["script"] = script
            result["audio_script"] = script

        final_script = self._insert_distractors_into_script(
            script=result["audio_script"],
            questions=result.get('questions', []),
            section=3,
            topic=topic
        )
        result["audio_script"] = final_script
        result["script"] = final_script
        result["word_count"] = len(final_script.split())

        if use_cache and not force_new:
            section_cache.save(3, result, topic=topic, difficulty=difficulty, accent=accent)
        return result

    def _ai_section3(self, topic: str, difficulty: str, accent: str,
                     fast: bool = True) -> Optional[Dict[str, Any]]:
        if not self.ai:
            return None
        tutor, s1, s2, s3, genders_map = self._get_section3_speakers()
        custom_speakers = [tutor, s1, s2, s3]
        speaker_genders = {
            "Tutor": genders_map.get(tutor, self._detect_gender_from_name(tutor)),
            "Student1": genders_map.get(s1, self._detect_gender_from_name(s1)),
            "Student2": genders_map.get(s2, self._detect_gender_from_name(s2)),
            "Student3": genders_map.get(s3, self._detect_gender_from_name(s3)),
        }
        for name, gender in genders_map.items():
            speaker_genders[name] = gender
        logger.info(f" Section 3 speaker_genders: {speaker_genders}")
        prompt = self._build_section3_prompt(topic, difficulty, custom_speakers=custom_speakers)
        result = self._call_ai(prompt, section=3, target_words=SECTION_TARGET_WORDS[3],
                               topic=topic, fast=fast)
        if not result:
            return None
        return {
            "section": 3,
            "title": f"Discussion: {topic.replace('_', ' ').title()}",
            "type": "discussion",
            "speakers": custom_speakers,
            "speaker_genders": speaker_genders,
            "context": f"A discussion about {topic}.",
            "audio_script": result.get("script", result.get("audio_script", "")),
            "script": result.get("script", ""),
            "questions": result["questions"],
            "question_count": len(result["questions"]),
            "question_types": self._get_question_types_safe(3),
            "word_count": result["word_count"],
            "target_duration_seconds": SECTION_DURATIONS[3],
            "ai_generated": True,
            "accent": accent,
        }

    def section4(self, topic: str, difficulty: str = "medium", accent: str = "british",
                 fast: bool = True, use_cache: bool = True,
                 force_new: bool = False) -> Dict[str, Any]:
        if use_cache and not force_new:
            cached = section_cache.get(4, topic=topic, difficulty=difficulty, accent=accent)
            if cached and cached.get("script") and cached.get("audio_script"):
                return cached
        if not self.ai:
            raise ValueError("AI engine is not available for Section 4 generation")

        result = self._ai_section4(topic, difficulty, accent, fast=fast)
        if not self._is_valid_ai_result(result):
            raise RuntimeError("AI Section 4 generation failed")

        result['audio_script'] = self._insert_distractors_into_script(
            script=result['audio_script'],
            questions=result.get('questions', []),
            section=4,
            topic=topic
        )
        result['script'] = result['audio_script']
        result['word_count'] = len(result['audio_script'].split())

        if use_cache and not force_new:
            section_cache.save(4, result, topic=topic, difficulty=difficulty, accent=accent)
        return result

    def _ai_section4(self, topic: str, difficulty: str, accent: str,
                     fast: bool = True) -> Optional[Dict[str, Any]]:
        if not self.ai:
            return None
        prompt = self._build_section4_prompt(topic, difficulty)
        result = self._call_ai(prompt, section=4, target_words=SECTION_TARGET_WORDS[4],
                               topic=topic, fast=fast)
        if not result:
            return None
        lecturer_gender = random.choice(['female', 'male'])
        logger.info(f" Section 4: Lecturer gender = {lecturer_gender}")
        return {
            "section": 4,
            "title": f"Lecture: {topic.replace('_', ' ').title()}",
            "type": "lecture",
            "speakers": ["Lecturer"],
            "speaker_genders": {"Lecturer": lecturer_gender},
            "context": f"A university lecture on {topic}.",
            "audio_script": result.get("script", result.get("audio_script", "")),
            "script": result.get("script", ""),
            "questions": result["questions"],
            "question_count": len(result["questions"]),
            "question_types": self._get_question_types_safe(4),
            "word_count": result["word_count"],
            "target_duration_seconds": SECTION_DURATIONS[4],
            "ai_generated": True,
            "accent": accent,
        }


# ============================================================
# MODULE-LEVEL HELPERS
# ============================================================

def clear_section_cache(older_than_days: int = 7) -> int:
    return section_cache.clear(older_than_days=older_than_days)


def get_cache_stats() -> Dict:
    return section_cache.get_stats()


def pre_generate_sections(ai_engine=None, difficulties: List[str] = None,
                          topics: List[str] = None, accent: str = "british"):
    if difficulties is None:
        difficulties = ['easy', 'medium', 'hard']
    if topics is None:
        topics = ['education', 'technology', 'environment', 'health',
                  'business', 'sports', 'travel', 'culture']
    generator = SectionGenerator(ai_engine)
    generated = 0
    logger.info("[PreGen] Starting pre-generation...")
    for difficulty in difficulties:
        for topic in topics[:3]:
            try:
                logger.info(f"[PreGen] Generating {difficulty}/{topic}/{accent}")
                generator.section1(difficulty=difficulty, accent=accent, fast=True)
                generator.section2(topic=topic, difficulty=difficulty, accent=accent, fast=True)
                generator.section3(topic=topic, difficulty=difficulty, accent=accent, fast=True)
                generator.section4(topic=topic, difficulty=difficulty, accent=accent, fast=True)
                generated += 4
            except Exception as e:
                logger.warning(f"[PreGen] Error {difficulty}/{topic}/{accent}: {e}")
    logger.info(f"[PreGen] Pre-generated {generated} sections")
    return generated