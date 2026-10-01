"""Utility functions for IELTS Reading module."""

from .helpers import (
    count_words,
    count_sentences,
    extract_keywords,
    estimate_readability,
    clean_text,
    normalize_answer,
    truncate_text,
    validate_question_number,
    validate_answer_length,
    answer_in_passage,
    get_question_type_info,
    generate_serial_number,
    generate_test_id,
    format_time,
    format_percentage,
    format_band_score,
    safe_get,
    calculate_accuracy,
    calculate_average,
    get_grade_label,
    ALL_QUESTION_TYPES,
)

# New imports for paraphraser and validators
from .paraphraser import IELTSParaphraser
from .validators import (
    validate_passage,
    validate_question,
    validate_test_structure,
    verify_answers_in_passage,
    validate_answer_length as validate_answer_len,
    validate_question_number as validate_q_num,
    extract_questions_from_test,
    count_questions_in_test,
    get_question_type_stats,
    all_answers_answered,
)

__all__ = [
    # Text analysis
    "count_words",
    "count_sentences",
    "extract_keywords",
    "estimate_readability",
    
    # Text cleaning
    "clean_text",
    "normalize_answer",
    "truncate_text",
    
    # Validation
    "validate_question_number",
    "validate_answer_length",
    "answer_in_passage",
    "validate_passage",
    "validate_question",
    "validate_test_structure",
    "verify_answers_in_passage",
    "validate_answer_len",
    "validate_q_num",
    "extract_questions_from_test",
    "count_questions_in_test",
    "get_question_type_stats",
    "all_answers_answered",
    
    # Question types
    "get_question_type_info",
    "ALL_QUESTION_TYPES",
    
    # Random generation
    "generate_serial_number",
    "generate_test_id",
    
    # Formatting
    "format_time",
    "format_percentage",
    "format_band_score",
    
    # Dict/JSON
    "safe_get",
    
    # Statistics
    "calculate_accuracy",
    "calculate_average",
    "get_grade_label",
    
    # Paraphraser
    "IELTSParaphraser",
]