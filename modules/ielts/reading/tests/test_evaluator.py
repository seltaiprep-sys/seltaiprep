"""Tests for the answer evaluator."""

import unittest
from ..evaluator import AnswerEvaluator
from ..reading_test import ReadingTest, Passage, Question


class TestAnswerEvaluator(unittest.TestCase):
    """Test cases for AnswerEvaluator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.evaluator = AnswerEvaluator(fuzzy_threshold=0.8)
        self.test = self._create_sample_test()
    
    def _create_sample_test(self) -> ReadingTest:
        """Create a sample test for evaluation."""
        passages = []
        
        for p_idx in range(3):
            questions = []
            count = 13 if p_idx < 2 else 14
            
            for q_idx in range(count):
                q_num = sum([13, 13, 14][:p_idx]) + q_idx + 1
                
                if q_idx % 4 == 0:
                    question = Question(
                        id=f"q{p_idx+1}_{q_idx+1}",
                        question_type="true_false_not_given",
                        question_text=f"Statement {q_num}",
                        options=["TRUE", "FALSE", "NOT GIVEN"],
                        correct_answer="TRUE",
                        explanation="Test explanation",
                        question_number=q_num
                    )
                elif q_idx % 4 == 1:
                    question = Question(
                        id=f"q{p_idx+1}_{q_idx+1}",
                        question_type="multiple_choice",
                        question_text=f"Question {q_num}",
                        options=["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],
                        correct_answer="A) Option 1",
                        explanation="Test explanation",
                        question_number=q_num
                    )
                else:
                    question = Question(
                        id=f"q{p_idx+1}_{q_idx+1}",
                        question_type="sentence_completion",
                        question_text=f"Complete sentence {q_num}",
                        correct_answer="example",
                        explanation="Test explanation",
                        question_number=q_num
                    )
                
                questions.append(question)
            
            passages.append(Passage(
                id=f"passage_{p_idx+1}",
                title=f"Passage {p_idx+1}",
                content="Test content",
                difficulty="medium",
                topic="test",
                word_count=100,
                questions=questions,
                passage_number=p_idx+1
            ))
        
        return ReadingTest(
            id="test_001",
            title="Test Reading Test",
            passages=passages
        )
    
    def test_evaluate_all_correct(self):
        """Test evaluation with all correct answers."""
        user_answers = {
            f"q{q.question_number}": q.correct_answer
            for q in self.test.get_all_questions()
        }
        
        results = self.evaluator.evaluate_test(self.test, user_answers)
        
        self.assertEqual(results["correct_count"], 40)
        self.assertEqual(results["incorrect_count"], 0)
        self.assertEqual(results["score"], 100.0)
        self.assertEqual(results["band_score"], 9.0)
    
    def test_evaluate_all_incorrect(self):
        """Test evaluation with all incorrect answers."""
        user_answers = {
            f"q{q.question_number}": "wrong answer"
            for q in self.test.get_all_questions()
        }
        
        results = self.evaluator.evaluate_test(self.test, user_answers)
        
        self.assertEqual(results["correct_count"], 0)
        self.assertEqual(results["incorrect_count"], 40)
        self.assertEqual(results["score"], 0.0)
    
    def test_evaluate_partial_answers(self):
        """Test evaluation with some unanswered questions."""
        all_questions = self.test.get_all_questions()
        user_answers = {}
        
        # Answer only first 20 questions correctly
        for i, question in enumerate(all_questions[:20]):
            user_answers[f"q{question.question_number}"] = question.correct_answer
        
        results = self.evaluator.evaluate_test(self.test, user_answers)
        
        self.assertEqual(results["correct_count"], 20)
        self.assertEqual(results["unanswered_count"], 20)
        self.assertEqual(results["score"], 50.0)
    
    def test_tfng_evaluation(self):
        """Test True/False/Not Given evaluation."""
        # Test various answer formats
        test_cases = [
            ("TRUE", "true", True),
            ("TRUE", "T", True),
            ("FALSE", "false", True),
            ("FALSE", "F", True),
            ("NOT GIVEN", "not given", True),
            ("NOT GIVEN", "NG", True),
            ("TRUE", "FALSE", False),
        ]
        
        for correct, user, expected in test_cases:
            result = self.evaluator._evaluate_tfng(correct, user)
            self.assertEqual(result, expected, f"Failed for {correct} vs {user}")
    
    def test_fuzzy_matching(self):
        """Test fuzzy matching for completion questions."""
        test_cases = [
            ("environment", "enviroment", True), # Typo
            ("climate change", "climate changes", True), # Plural
            ("global warming", "global wariming", True), # Spelling
            ("carbon dioxide", "oxygen", False), # Different word
        ]
        
        for correct, user, expected in test_cases:
            is_correct, similarity = self.evaluator._evaluate_completion(correct, user)
            self.assertEqual(is_correct, expected, f"Failed for {correct} vs {user}")
    
    def test_band_score_calculation(self):
        """Test band score calculation."""
        test_cases = [
            (40, 9.0),
            (35, 8.0),
            (30, 7.0),
            (25, 6.0),
            (20, 5.0),
            (15, 4.0),
            (10, 2.5),
            (0, 0.0),
        ]
        
        for correct, expected_band in test_cases:
            band = self.evaluator._calculate_band_score(correct)
            self.assertEqual(band, expected_band, f"Failed for {correct} correct")
    
    def test_feedback_generation(self):
        """Test feedback generation."""
        results = {
            "score": 75.0,
            "band_score": 7.0,
            "correct_count": 30,
            "total_questions": 40,
            "question_results": []
        }
        
        feedback = self.evaluator._generate_feedback(results)
        self.assertIsInstance(feedback, list)
        self.assertTrue(len(feedback) > 0)


if __name__ == '__main__':
    unittest.main()