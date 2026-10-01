"""Question type definitions for IELTS Listening"""

import random
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class QuestionTypeManager:
    """Manage question type distribution for each section."""

    # ============================================================
    # QUESTION TYPE METADATA
    # ============================================================
    QUESTION_TYPES = {
        'form_completion': {
            'name': 'Form Completion',
            'instruction': 'Complete the form below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.',
            'section': [1],
        },
        'table_completion': {
            'name': 'Table Completion',
            'instruction': 'Complete the table below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.',
            'section': [1, 2],
        },
        'note_completion': {
            'name': 'Note Completion',
            'instruction': 'Complete the notes below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.',
            'section': [2, 4],
        },
        'sentence_completion': {
            'name': 'Sentence Completion',
            'instruction': 'Complete the sentences below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.',
            'section': [2, 3, 4],
        },
        'multiple_choice': {
            'name': 'Multiple Choice',
            'instruction': 'Choose the correct letter, A, B or C.',
            'answer_format': 'Write the correct letter A, B or C.',
            'section': [2, 3],
        },
        'short_answer': {
            'name': 'Short Answer Questions',
            'instruction': 'Answer the questions below.',
            'answer_format': 'Write NO MORE THAN THREE WORDS for each answer.',
            'section': [1, 2],
        },
        'matching': {
            'name': 'Matching',
            'instruction': 'Match each statement with the correct option.',
            'answer_format': 'Write the correct letter A-F.',
            'section': [2, 3],
        },
        'map_labeling': {
            'name': 'Map/Plan Labeling',
            'instruction': 'Label the map below.',
            'answer_format': 'Write the correct letter A-H next to questions.',
            'section': [2],
        },
        'flow_chart': {
            'name': 'Flow Chart Completion',
            'instruction': 'Complete the flow chart below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS for each answer.',
            'section': [3, 4],
        },
        'summary_completion': {
            'name': 'Summary Completion',
            'instruction': 'Complete the summary below.',
            'answer_format': 'Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.',
            'section': [4],
        },
    }

    # ============================================================
    # DISTRIBUTION (section -> list of (type, count))
    # ============================================================
    SECTION_DISTRIBUTION = {
        1: [
            ('form_completion', 8), # Q1-8: form completion
            ('multiple_choice', 2), # Q9-10: MCQ
        ],
        2: [
            ('table_completion', 5), # Q11-15
            ('map_labeling', 3), # Q16-18
            ('multiple_choice', 2), # Q19-20
        ],
        3: [
            ('multiple_choice', 3),
            ('matching', 4),
            ('sentence_completion', 3),
        ],
        4: [
            ('note_completion', 4),
            ('flow_chart', 3),
            ('summary_completion', 3),
        ],
    }

    # ============================================================
    # VALIDATION
    # ============================================================
    @classmethod
    def validate(cls) -> bool:
        """Check that all section distributions are valid and sum to 10."""
        for section, dist in cls.SECTION_DISTRIBUTION.items():
            total = 0
            for q_type, count in dist:
                if q_type not in cls.QUESTION_TYPES:
                    raise ValueError(f"Unknown question type '{q_type}' in section {section}")
                if not isinstance(count, int) or count <= 0:
                    raise ValueError(f"Invalid count {count} for type '{q_type}' in section {section}")
                total += count
            if total != 10:
                raise ValueError(f"Section {section} distribution sums to {total}, expected 10")
        return True

    @classmethod
    def get_distribution(cls, section: int) -> List[Tuple[str, int]]:
        """Get the question type distribution for a section."""
        return cls.SECTION_DISTRIBUTION.get(section, [('note_completion', 10)])

    @classmethod
    def get_type_info(cls, q_type: str) -> Dict[str, Any]:
        """Get metadata for a question type."""
        return cls.QUESTION_TYPES.get(q_type, {})

    @classmethod
    def get_instruction(cls, section: int) -> str:
        """Get the combined instructions for all types in a section."""
        distribution = cls.get_distribution(section)
        instructions = []
        for q_type, count in distribution:
            info = cls.get_type_info(q_type)
            if not info:
                continue
            name = info.get("name", q_type)
            instruction = info.get("instruction", "")
            answer_format = info.get("answer_format", "")
            if instruction:
                instructions.append(f"{name}: {instruction}")
            if answer_format:
                instructions.append(f" {answer_format}")
        return "\n\n".join(instructions)

    # ============================================================
    # QUESTION GENERATION (WITH ANSWER VALIDATION)
    # ============================================================
    @classmethod
    def generate_question_format(
        cls,
        q_type: str,
        question_num: int,
        field: Optional[Dict[str, Any]] = None,
        options: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a question dict with the appropriate structure.

        Args:
            q_type: The question type key.
            question_num: The question number (1-10 within section).
            field: Optional dict with 'q' (question text) and 'a' (answer).
            options: Optional list of option texts (for MCQ/matching).

        Returns:
            A dictionary with keys: number, type, question, answer,
            and optionally options, max_words, etc.

        Raises:
            ValueError: If the answer is not valid for the question type
                        (e.g., MCQ answer not in the provided options).
        """
        field = field or {}
        question_text = field.get('q', '')
        answer = field.get('a', '')
        info = cls.get_type_info(q_type)

        base: Dict[str, Any] = {
            'number': question_num,
            'type': q_type,
            'answer': answer,
            'question': question_text,
            'instruction': info.get('instruction', ''),
            'answer_format': info.get('answer_format', ''),
        }

        # Type-specific formatting and validation
        if q_type in ('form_completion', 'table_completion'):
            base['question'] = f"Write: {question_text}" if question_text else ""
        elif q_type == 'note_completion':
            base['question'] = f"{question_text}: ________"
        elif q_type in ('sentence_completion', 'short_answer'):
            base['question'] = question_text
            if q_type == 'short_answer':
                base['max_words'] = 3
        elif q_type in ('multiple_choice', 'matching'):
            base['question'] = question_text
            # Set default options if none provided
            if q_type == 'multiple_choice':
                if options is None:
                    options = ['A', 'B', 'C']
            else: # matching
                if options is None:
                    options = ['A', 'B', 'C', 'D', 'E', 'F']
            
            base['options'] = options

            # VALIDATE ANSWER: Ensure it is one of the options
            valid_letters = [chr(ord('A') + i) for i in range(len(options))]
            if answer:
                # Normalize answer: strip, uppercase
                normalized_answer = str(answer).strip().upper()
                
                # If answer is already a letter and in valid letters, keep it
                if normalized_answer in valid_letters:
                    # Good, but we also need to ensure the corresponding option exists
                    # (it should, since options length is at least that)
                    base['answer'] = normalized_answer
                else:
                    # Try to find the option text that matches the answer (case-insensitive)
                    matched = False
                    for idx, opt in enumerate(options):
                        # Compare stripped text, ignore case
                        if opt.strip().lower() == normalized_answer.lower():
                            base['answer'] = valid_letters[idx]
                            matched = True
                            break
                        # Also try to match if answer is a substring of option (e.g., answer "B" not matched but option "Option B")
                        # We'll be more lenient: if answer is in option text, assume it's that option
                        # but this might be ambiguous, so we only do it if there's a single match
                        # Actually, simpler: we can check if answer is a number and map to letter
                    if not matched:
                        # If answer is a digit (e.g., "1"), map to letter
                        if normalized_answer.isdigit():
                            idx = int(normalized_answer) - 1
                            if 0 <= idx < len(options):
                                base['answer'] = valid_letters[idx]
                                matched = True
                        # If still not matched, raise error or warn and set to first option
                        if not matched:
                            logger.warning(
                                f"Answer '{answer}' not found in options {options} for "
                                f"question {question_num}. Setting answer to first option."
                            )
                            base['answer'] = valid_letters[0]
            else:
                # No answer provided; set to first option
                base['answer'] = valid_letters[0]
                logger.warning(f"No answer provided for MCQ/matching question {question_num}; set to first option.")
        elif q_type == 'map_labeling':
            base['question'] = f"Label map: {question_text}"
            base['options'] = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        elif q_type == 'flow_chart':
            base['question'] = f"Complete flow: {question_text}"
        elif q_type == 'summary_completion':
            base['question'] = f"Complete summary: {question_text}"

        return base

    # ============================================================
    # HELPER: GET RANDOM TYPE FOR A SECTION
    # ============================================================
    @classmethod
    def get_random_type_for_section(cls, section: int) -> str:
        """Return a random question type used in a given section."""
        dist = cls.get_distribution(section)
        types = [q_type for q_type, count in dist for _ in range(count)]
        return random.choice(types) if types else 'note_completion'

    # ============================================================
    # GET ALLOWED TYPES
    # ============================================================
    @classmethod
    def get_types_for_section(cls, section: int) -> List[str]:
        """Return the list of question types used in a section."""
        dist = cls.get_distribution(section)
        return [q_type for q_type, count in dist]


# Run validation on module load
try:
    QuestionTypeManager.validate()
except ValueError as e:
    logging.warning(f"QuestionTypeManager validation failed: {e}")

# Singleton instance
question_type_manager = QuestionTypeManager()