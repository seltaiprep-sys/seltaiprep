"""Enhanced distractor generation for realistic IELTS Listening tests"""

import random
import logging
import re
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class DistractorGenerator:
    """
    Generate realistic IELTS Listening distractors with section‑aware behaviour.
    Supports self‑correction, hesitation, opinion change, and rephrasing.
    """

    # ============================================================
    # TEMPLATES (expanded)
    # ============================================================

    PRICE_DISTRACTORS = [
        "That'll be {wrong}... actually, it's {correct}.",
        "I thought it was {wrong}, sorry—system says {correct}.",
        "Let me check... {wrong}. No, it's {correct}.",
        "Normally {wrong}, but today it's {correct}.",
        "{wrong}... oh wait, I missed the discount. It's {correct}.",
        "The price is {wrong}? Actually, I just remembered—it's {correct}.",
        "It was {wrong} last week, but now it's {correct}.",
    ]

    DATE_DISTRACTORS = [
        "{wrong}... sorry, I mean {correct}.",
        "Let me check... not {wrong}, it's {correct}.",
        "{wrong} is full. Available: {correct}.",
        "I thought {wrong}, but actually {correct}.",
        "Did I say {wrong}? No, it's {correct}.",
        "The date was {wrong}, but we've moved it to {correct}.",
    ]

    TIME_DISTRACTORS = [
        "{wrong}... actually it's {correct}.",
        "I wrote {wrong}, but it's {correct}.",
        "{wrong} is wrong, correct time is {correct}.",
        "Wait, the time is {wrong}? No, it's {correct}.",
        "The schedule says {wrong}, but I think it's {correct}.",
    ]

    NUMBER_DISTRACTORS = [
        "{wrong}... wait, it's {correct}.",
        "I thought {wrong}, but actually {correct}.",
        "{wrong} → corrected to {correct}.",
        "Not {wrong}, it's {correct}.",
        "Double‑checking... {wrong}? No, {correct}.",
    ]

    NAME_DISTRACTORS = [
        "My name is {wrong}... actually {correct}.",
        "Is it {wrong}? No, it's {correct}.",
        "Sorry, I misspoke—{wrong} should be {correct}.",
        "Let me spell that: {wrong}? No, {correct}.",
    ]

    TEXT_DISTRACTORS = [
        "Actually, it's not that... it's {correct}.",
        "Let me correct myself... it's {correct}.",
        "I meant {correct}, not something else.",
        "Correction: it's {correct}.",
        "Wait, that's wrong—it's {correct}.",
        "I should say {correct}, not what I just said.",
    ]

    OPINION_CHANGE_TEMPLATES = [
        "I used to believe {old_view}, but now I think {new_view}.",
        "At first I thought {old_view}, however I've changed my mind—{new_view}.",
        "My initial view was {old_view}, but having considered it, I now believe {new_view}.",
        "I was going to say {old_view}, but actually {new_view}.",
    ]

    HESITATION_TEMPLATES = [
        "It's about... um... actually, it's around {correct}.",
        "The figure is... well... it's {correct}.",
        "I think it's... let me see... yes, {correct}.",
        "It could be... no, it's definitely {correct}.",
        "Approximately... I mean precisely {correct}.",
    ]

    CONFIRMATION_TEMPLATES = [
        "Oh wait, I was wrong. It's definitely {correct}.",
        "Let me double‑check... yes, it's {correct}.",
        "I stand corrected—it's {correct}.",
        "You're right, it's {correct}, not what I said.",
    ]

    FALSE_START_TEMPLATES = [
        "The answer is... well, it's {correct}.",
        "It's... actually it's {correct}.",
        "Let's see... the correct one is {correct}.",
    ]

    # Section‑specific template weighting
    SECTION_TEMPLATE_WEIGHTS = {
        1: {
            'self_correction': 0.5,
            'confirmation': 0.3,
            'hesitation': 0.2,
        },
        2: {
            'self_correction': 0.4,
            'confirmation': 0.1,
            'hesitation': 0.3,
            'false_start': 0.2,
        },
        3: {
            'self_correction': 0.3,
            'opinion_change': 0.3,
            'confirmation': 0.2,
            'hesitation': 0.2,
        },
        4: {
            'self_correction': 0.3,
            'hesitation': 0.3,
            'false_start': 0.2,
            'confirmation': 0.2,
        },
    }

    # ============================================================
    # GENERATION FUNCTIONS (enhanced)
    # ============================================================

    @classmethod
    def generate_price_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            answer = str(correct_answer).strip()
            currency_match = re.match(r'^([^\d]*)([\d.]+)([^\d]*)$', answer)
            if not currency_match:
                return None
            prefix, num_str, suffix = currency_match.groups()
            correct_num = float(num_str)
            factor = random.choice([0.7, 0.8, 1.2, 1.3, 1.5, 0.85, 1.15])
            wrong_num = round(correct_num * factor, 2)
            if wrong_num.is_integer():
                wrong_price = f"{prefix}{wrong_num:.0f}{suffix}"
            else:
                wrong_price = f"{prefix}{wrong_num:.2f}{suffix}"
            return random.choice(cls.PRICE_DISTRACTORS).format(
                wrong=wrong_price,
                correct=answer,
            )
        except Exception as e:
            logger.debug(f"Price distractor failed: {e}")
            return None

    @classmethod
    def generate_date_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            correct = str(correct_answer).strip()
            # Day number with suffix
            day_match = re.match(r'^(\d+)(st|nd|rd|th)?$', correct)
            if day_match:
                day_num = int(day_match.group(1))
                offset = random.choice([-2, -1, 1, 2, 3, -3, 4])
                wrong_num = day_num + offset
                if wrong_num < 1:
                    wrong_num = day_num + 1
                if wrong_num > 31:
                    wrong_num = day_num - 1
                # Suffix
                if 4 <= wrong_num <= 20 or 24 <= wrong_num <= 30:
                    suffix = 'th'
                else:
                    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(wrong_num % 10, 'th')
                wrong_date = f"{wrong_num}{suffix}"
                return random.choice(cls.DATE_DISTRACTORS).format(
                    wrong=wrong_date,
                    correct=correct,
                )
            # Month name
            months = ['January', 'February', 'March', 'April', 'May', 'June',
                      'July', 'August', 'September', 'October', 'November', 'December']
            if correct in months:
                wrong_months = [m for m in months if m != correct]
                if wrong_months:
                    wrong_date = random.choice(wrong_months)
                    return f"I thought it was {wrong_date}, but actually it's {correct}."
            # Other date formats (e.g., 12th March)
            date_parts = re.split(r'\s+', correct)
            if len(date_parts) >= 2:
                # Try to change day or month
                day_part = date_parts[0]
                month_part = ' '.join(date_parts[1:])
                if month_part in months:
                    wrong_month = random.choice([m for m in months if m != month_part])
                    wrong_date = f"{day_part} {wrong_month}"
                    return f"I said {wrong_date}, but I meant {correct}."
            return None
        except Exception as e:
            logger.debug(f"Date distractor failed: {e}")
            return None

    @classmethod
    def generate_time_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            correct = str(correct_answer).strip()
            if not re.search(r"\d", correct):
                return None
            # Try to parse time (HH:MM AM/PM)
            time_match = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)?$', correct, re.I)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                ampm = time_match.group(3) or ('AM' if hour < 12 else 'PM')
                # Adjust hour
                offset_hour = random.choice([-1, 1, 2, -2])
                wrong_hour = hour + offset_hour
                if wrong_hour < 1:
                    wrong_hour = hour + 1
                if wrong_hour > 12:
                    wrong_hour = hour - 1
                wrong_time = f"{wrong_hour}:{minute:02d} {ampm}"
                return random.choice(cls.TIME_DISTRACTORS).format(
                    wrong=wrong_time,
                    correct=correct,
                )
            return None
        except Exception as e:
            logger.debug(f"Time distractor failed: {e}")
            return None

    @classmethod
    def generate_number_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            correct = str(correct_answer).strip()
            # Extract numeric part
            num_match = re.search(r'(\d+)', correct)
            if not num_match:
                return None
            correct_num = int(num_match.group(1))
            offset = random.choice([-2, -1, 1, 2, 3, -3, 5])
            wrong_num = correct_num + offset
            if wrong_num < 0:
                wrong_num = correct_num + 1
            wrong_str = str(wrong_num)
            # Preserve any suffix (e.g., "years", "people")
            suffix = correct.replace(num_match.group(0), '')
            wrong_answer = wrong_str + suffix
            return random.choice(cls.NUMBER_DISTRACTORS).format(
                wrong=wrong_answer,
                correct=correct,
            )
        except Exception as e:
            logger.debug(f"Number distractor failed: {e}")
            return None

    @classmethod
    def generate_name_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            correct = str(correct_answer).strip()
            if len(correct) < 2:
                return None
            # If full name
            if " " in correct:
                first, last = correct.split(" ", 1)
                replacements = {
                    "Sarah": "Sandra", "David": "Daniel", "James": "John",
                    "Maria": "Marie", "Emily": "Emma", "Michael": "Matthew",
                    "Robert": "Richard", "Jessica": "Jennifer", "Amanda": "Amber",
                    "Anna": "Anne", "Thomas": "Tom", "Henry": "Harry",
                    "William": "Will", "Alexander": "Alex", "Charlotte": "Charlie",
                }
                wrong_first = replacements.get(first, first + "e")
                wrong_name = f"{wrong_first} {last}"
                return random.choice(cls.NAME_DISTRACTORS).format(
                    wrong=wrong_name,
                    correct=correct,
                )
            # Single name
            wrong = correct[:-1] + random.choice(["a", "e", "i", "o", "u"])
            return random.choice(cls.NAME_DISTRACTORS).format(
                wrong=wrong,
                correct=correct,
            )
        except Exception as e:
            logger.debug(f"Name distractor failed: {e}")
            return None

    @classmethod
    def generate_text_distractor(cls, correct_answer: str) -> Optional[str]:
        try:
            text = str(correct_answer).strip()
            if len(text) < 3:
                return None
            return random.choice(cls.TEXT_DISTRACTORS).format(correct=text)
        except Exception as e:
            logger.debug(f"Text distractor failed: {e}")
            return None

    @classmethod
    def generate_opinion_change(cls, correct_answer: str, topic: Optional[str] = None) -> Optional[str]:
        """Generate an opinion change distractor (for Section 3)."""
        try:
            text = str(correct_answer).strip()
            if len(text) < 5:
                return None
            # Generate a slightly different viewpoint
            old_view = text
            # Make a variation: change a key word or add a modifier
            words = text.split()
            if len(words) > 3:
                # Replace a word with a synonym or opposite
                synonyms = {
                    "good": "bad", "positive": "negative", "increase": "decrease",
                    "beneficial": "harmful", "effective": "ineffective",
                }
                for i, w in enumerate(words):
                    if w.lower() in synonyms:
                        words[i] = synonyms[w.lower()]
                        break
                new_view = " ".join(words)
                if new_view != text:
                    return random.choice(cls.OPINION_CHANGE_TEMPLATES).format(
                        old_view=text,
                        new_view=new_view,
                    )
            return random.choice(cls.OPINION_CHANGE_TEMPLATES).format(
                old_view=text,
                new_view=text, # fallback
            )
        except Exception as e:
            logger.debug(f"Opinion change failed: {e}")
            return None

    # ============================================================
    # SECTION‑AWARE DISTRACTOR GENERATION
    # ============================================================

    @classmethod
    def generate_distractors_for_fields(
        cls,
        fields: List[Dict[str, Any]],
        num_distractors: int = 3,
        section: int = 1,
        topic: Optional[str] = None,
    ) -> Dict[int, str]:
        """
        Generate distractors for fields with section‑aware behaviour.
        Returns dict mapping field index -> distractor string.
        """
        if not fields or num_distractors <= 0:
            return {}

        # Filter out fields with empty answers
        valid_fields = [(i, f) for i, f in enumerate(fields) if f.get('a', '')]
        if not valid_fields:
            return {}

        generator_map = {
            "money": cls.generate_price_distractor,
            "date": cls.generate_date_distractor,
            "time": cls.generate_time_distractor,
            "number": cls.generate_number_distractor,
            "name": cls.generate_name_distractor,
            "text": cls.generate_text_distractor,
        }

        # Prioritize fields with explicit types
        priority_fields = []
        other_fields = []
        for i, f in valid_fields:
            t = f.get("t", "text")
            if t in generator_map:
                priority_fields.append((i, f))
            else:
                other_fields.append((i, f))

        distractors = {}
        selected_count = 0

        # Try priority fields first
        if priority_fields:
            sample_size = min(num_distractors, len(priority_fields))
            for i, f in random.sample(priority_fields, sample_size):
                field_type = f.get("t", "text")
                answer = f.get("a", "")
                gen_func = generator_map.get(field_type, cls.generate_text_distractor)
                distractor = gen_func(answer)
                if distractor:
                    # For section 3, add opinion change possibility
                    if section == 3 and random.random() < 0.3:
                        opinion = cls.generate_opinion_change(answer, topic)
                        if opinion:
                            distractor = opinion
                    distractors[i] = distractor
                    selected_count += 1

        # Fill remaining with other fields
        remaining = num_distractors - selected_count
        if remaining > 0 and other_fields:
            sample_size = min(remaining, len(other_fields))
            for i, f in random.sample(other_fields, sample_size):
                field_type = f.get("t", "text")
                answer = f.get("a", "")
                gen_func = generator_map.get(field_type, cls.generate_text_distractor)
                distractor = gen_func(answer)
                if distractor:
                    if section == 3 and random.random() < 0.3:
                        opinion = cls.generate_opinion_change(answer, topic)
                        if opinion:
                            distractor = opinion
                    distractors[i] = distractor
                    selected_count += 1

        logger.info(f"Generated {len(distractors)} distractors (target: {num_distractors}) for section {section}")
        return distractors

    # ============================================================
    # INTELLIGENT SCRIPT INSERTION
    # ============================================================

    @classmethod
    def insert_distractors_into_script(
        cls,
        script_lines: List[str],
        distractors: Dict[int, str],
        speaker_a: str = "Customer",
        speaker_b: str = "Agent",
    ) -> List[str]:
        """
        Insert distractors naturally into script lines.
        Tries to insert at a natural pause (after comma, before a period, etc.)
        """
        if not script_lines or not distractors:
            return script_lines

        modified = []
        for i, line in enumerate(script_lines):
            if i in distractors:
                distractor_text = distractors[i]
                # Try to find a speaker label
                match = re.match(r'^([A-Za-z][A-Za-z0-9_\- ]*):\s*(.*)$', line)
                if match:
                    speaker, text = match.groups()
                    # Insert distractor at a natural point:
                    # 1. After the first sentence (if there are multiple sentences)
                    sentences = re.split(r'(?<=[.!?])\s+', text)
                    if len(sentences) > 1:
                        # Insert after the first sentence
                        first = sentences[0]
                        rest = ' '.join(sentences[1:])
                        # Try to place the distractor before the second sentence
                        if random.choice([True, False]):
                            new_text = f"{first} {distractor_text}. {rest}"
                        else:
                            new_text = f"{first}. {distractor_text} {rest}"
                    else:
                        # Insert before a period or at the end
                        if '.' in text:
                            parts = text.split('.', 1)
                            if len(parts) > 1:
                                new_text = f"{parts[0]}. {distractor_text}. {parts[1]}".strip()
                            else:
                                new_text = f"{text} {distractor_text}"
                        else:
                            new_text = f"{text} {distractor_text}"
                    modified.append(f"{speaker}: {new_text}")
                else:
                    # No speaker label - add as a new line from random speaker
                    speaker = random.choice([speaker_a, speaker_b])
                    modified.append(f"{speaker}: {distractor_text} {line}")
            else:
                modified.append(line)
        return modified


# ============================================================
# SINGLETON
# ============================================================

distractor_generator = DistractorGenerator()