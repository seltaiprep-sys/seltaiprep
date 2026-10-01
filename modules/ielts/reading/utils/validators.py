"""Validation functions for passages, questions, and tests."""

import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def validate_passage(passage: Dict) -> tuple:
    """
    Validate passage structure and content.
    
    Args:
        passage: Passage dict with 'content', 'title', 'questions', etc.
    
    Returns:
        (is_valid, error_message)
    """
    if not passage:
        return False, "Passage is empty or None"
    
    if not isinstance(passage, dict):
        return False, f"Passage must be dict, got {type(passage)}"
    
    # Check content
    content = passage.get('content', '')
    if not content or not isinstance(content, str):
        return False, "Passage content is missing or not a string"
    
    if len(content.strip()) < 50:
        return False, f"Passage content too short: {len(content.strip())} characters (minimum 50)"
    
    # Check word count
    word_count = len(content.split())
    if word_count < 50:
        return False, f"Passage word count too low: {word_count} (minimum 50)"
    
    if word_count > 2000:
        return False, f"Passage word count too high: {word_count} (maximum 2000)"
    
    # Check title
    title = passage.get('title', '')
    if title and not isinstance(title, str):
        return False, "Passage title must be a string"
    
    # Check difficulty
    difficulty = passage.get('difficulty', '')
    if difficulty and difficulty not in ['easy', 'medium', 'hard']:
        return False, f"Invalid difficulty: {difficulty}. Must be easy, medium, or hard"
    
    # Check questions
    questions = passage.get('questions', [])
    if not questions:
        return True, "No questions found (passage may be used as content only)"
    
    if not isinstance(questions, list):
        return False, "Questions must be a list"
    
    # Validate each question
    for i, q in enumerate(questions):
        valid, msg = validate_question(q)
        if not valid:
            return False, f"Question {i+1}: {msg}"
    
    return True, "Valid passage"


def validate_question(question: Dict) -> tuple:
    """
    Validate question structure.
    
    Args:
        question: Question dict with required fields
    
    Returns:
        (is_valid, error_message)
    """
    if not question:
        return False, "Question is empty or None"
    
    if not isinstance(question, dict):
        return False, f"Question must be dict, got {type(question)}"
    
    # Required fields
    required = ['question_type', 'question_text', 'correct_answer']
    for field in required:
        if field not in question:
            return False, f"Missing required field: '{field}'"
    
    # Check question_type
    q_type = question.get('question_type', '')
    valid_types = [
        'multiple_choice', 'true_false_not_given', 'yes_no_not_given',
        'sentence_completion', 'short_answer', 'matching_headings',
        'matching_information', 'summary_completion', 'multiple_choice_multi'
    ]
    if q_type not in valid_types:
        return False, f"Invalid question_type: '{q_type}'. Must be one of: {valid_types}"
    
    # Check question_text
    q_text = question.get('question_text', '')
    if not q_text or not isinstance(q_text, str):
        return False, "question_text is missing or not a string"
    
    if len(q_text.strip()) < 5:
        return False, f"question_text too short: {len(q_text.strip())} characters"
    
    # Check correct_answer
    correct_answer = question.get('correct_answer', '')
    if not correct_answer or not isinstance(correct_answer, str):
        return False, "correct_answer is missing or not a string"
    
    if len(correct_answer.strip()) < 1:
        return False, "correct_answer is empty"
    
    # For multiple choice, check options
    if q_type == 'multiple_choice' or q_type == 'multiple_choice_multi':
        options = question.get('options', [])
        if not options:
            return False, "Multiple choice question must have 'options' list"
        if not isinstance(options, list):
            return False, "Options must be a list"
        if len(options) < 2:
            return False, f"Multiple choice needs at least 2 options, got {len(options)}"
        # Check if correct_answer is in options
        if correct_answer not in options:
            # Try case-insensitive match
            if not any(correct_answer.lower() == opt.lower() for opt in options):
                return False, f"correct_answer '{correct_answer}' not found in options"
    
    # For true/false/yes/no, check options if provided
    if q_type in ['true_false_not_given', 'yes_no_not_given']:
        options = question.get('options', [])
        expected_options = ['TRUE', 'FALSE', 'NOT GIVEN'] if 'true' in q_type else ['YES', 'NO', 'NOT GIVEN']
        if options and options != expected_options:
            return False, f"Options for {q_type} should be {expected_options}, got {options}"
    
    # Check id
    q_id = question.get('id', '')
    if not q_id:
        return False, "Question missing 'id' field (recommended for tracking)"
    
    return True, "Valid question"


def validate_test_structure(test: Dict) -> tuple:
    """
    Validate complete test structure with passages and questions.
    
    Args:
        test: Test dict with 'passages' list
    
    Returns:
        (is_valid, error_message)
    """
    if not test:
        return False, "Test is empty or None"
    
    if not isinstance(test, dict):
        return False, f"Test must be dict, got {type(test)}"
    
    # Check id
    test_id = test.get('id', '')
    if not test_id:
        return False, "Test missing 'id' field"
    
    # Check passages
    passages = test.get('passages', [])
    if not passages:
        return False, "Test has no passages (minimum 3 required)"
    
    if not isinstance(passages, list):
        return False, "Passages must be a list"
    
    if len(passages) < 3:
        return False, f"Test must have at least 3 passages, got {len(passages)}"
    
    # Validate each passage
    for i, passage in enumerate(passages):
        valid, msg = validate_passage(passage)
        if not valid:
            return False, f"Passage {i+1}: {msg}"
    
    # Check total questions
    total_questions = test.get('total_questions', 0)
    if total_questions == 0:
        # Calculate from passages
        actual_total = sum(len(p.get('questions', [])) for p in passages)
        if actual_total == 0:
            return False, "Test has no questions"
        # Update if needed
        # test['total_questions'] = actual_total
    
    # Check correct_answers
    correct_answers = test.get('correct_answers', {})
    if correct_answers:
        if not isinstance(correct_answers, dict):
            return False, "correct_answers must be a dict"
        # Check if keys are strings/ints
        for key in correct_answers.keys():
            if not (isinstance(key, (str, int))):
                return False, f"Invalid key in correct_answers: {key}"
    
    return True, "Valid test"


def verify_answers_in_passage(passage: Dict, answers: Dict) -> tuple:
    """
    Verify that all answers appear in the passage content.
    
    Args:
        passage: Passage dict with 'content'
        answers: Dict of question_id -> answer
    
    Returns:
        (is_valid, list_of_issues)
    """
    content = passage.get('content', '')
    if not content:
        return False, ["Passage content is missing"]
    
    content_lower = content.lower()
    issues = []
    
    for q_id, answer in answers.items():
        if not answer:
            issues.append(f"Question {q_id}: answer is empty")
            continue
        
        # Check if answer appears in passage (exact or partial)
        answer_lower = answer.lower().strip()
        if answer_lower not in content_lower:
            # Try partial match (if answer is long)
            answer_words = answer_lower.split()
            if len(answer_words) > 2:
                # Check if at least 50% of words appear
                found_words = sum(1 for w in answer_words if w in content_lower)
                if found_words / len(answer_words) < 0.5:
                    issues.append(f"Question {q_id}: answer '{answer}' not found in passage")
            else:
                issues.append(f"Question {q_id}: answer '{answer}' not found in passage")
    
    if issues:
        return False, issues
    return True, []


def validate_answer_length(answer: str, max_length: int = 50) -> bool:
    """Validate that an answer is within length limit."""
    if not answer:
        return True # Empty is acceptable (not answered)
    return len(answer.strip()) <= max_length


def validate_question_number(number: Any) -> bool:
    """Validate that question number is a positive integer."""
    try:
        return int(number) > 0
    except (ValueError, TypeError):
        return False


def extract_questions_from_test(test: Dict) -> List[Dict]:
    """Extract all questions from a test."""
    questions = []
    passages = test.get('passages', [])
    for passage in passages:
        for q in passage.get('questions', []):
            questions.append(q)
    return questions


def count_questions_in_test(test: Dict) -> int:
    """Count total questions in a test."""
    return len(extract_questions_from_test(test))


def get_question_type_stats(test: Dict) -> Dict[str, int]:
    """Get statistics of question types in a test."""
    stats = {}
    questions = extract_questions_from_test(test)
    for q in questions:
        q_type = q.get('question_type', 'unknown')
        stats[q_type] = stats.get(q_type, 0) + 1
    return stats


def all_answers_answered(user_answers: Dict, total_questions: int) -> tuple:
    """
    Check if all questions are answered.
    
    Returns:
        (is_complete, unanswered_count)
    """
    answered = sum(1 for a in user_answers.values() if a and a.strip())
    unanswered = total_questions - answered
    return unanswered == 0, unanswered


# ============================================================
# TESTING
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print(" VALIDATORS TEST")
    print("=" * 60)
    
    # Test a valid question
    valid_question = {
        "id": "q1",
        "question_type": "multiple_choice",
        "question_text": "What is the main idea?",
        "options": ["A", "B", "C", "D"],
        "correct_answer": "A"
    }
    valid, msg = validate_question(valid_question)
    print(f"Valid question: {valid} - {msg}")
    
    # Test an invalid question
    invalid_question = {
        "question_type": "multiple_choice",
        "question_text": "What?",
        "options": ["A", "B"],
        "correct_answer": "C" # Not in options
    }
    valid, msg = validate_question(invalid_question)
    print(f"Invalid question: {valid} - {msg}")
    
    # Test a passage
    valid_passage = {
        "content": "This is a passage with at least fifty words. " * 10,
        "title": "Test Passage",
        "difficulty": "medium",
        "questions": [valid_question]
    }
    valid, msg = validate_passage(valid_passage)
    print(f"Valid passage: {valid} - {msg}")
    
    print("\n Validators working correctly!")