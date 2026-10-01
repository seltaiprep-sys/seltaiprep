"""Tests for the test generator."""

import unittest
from .test_generator import IELTSReadingGenerator
from ..reading_test import ReadingTest


class TestIELTSReadingGenerator(unittest.TestCase):
    """Test cases for IELTSReadingGenerator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.generator = IELTSReadingGenerator(use_ai=False, seed=42)
    
    def test_generate_complete_test(self):
        """Test complete test generation."""
        test = self.generator.generate_complete_test()
        
        # Check basic structure
        self.assertIsInstance(test, ReadingTest)
        self.assertEqual(len(test.passages), 3)
        self.assertEqual(test.total_questions, 40)
        
        # Check question distribution
        question_counts = [len(p.questions) for p in test.passages]
        self.assertEqual(question_counts, [13, 13, 14])
        
        # Check total questions
        total = sum(question_counts)
        self.assertEqual(total, 40)
    
    def test_passage_validation(self):
        """Test passage validation."""
        test = self.generator.generate_complete_test()
        
        for i, passage in enumerate(test.passages):
            # Check passage has content
            self.assertIsNotNone(passage.content)
            self.assertTrue(len(passage.content) > 0)
            
            # Check expected question count
            expected_count = 13 if i < 2 else 14
            self.assertEqual(
                len(passage.questions),
                expected_count,
                f"Passage {i+1} has wrong number of questions"
            )
            
            # Check questions have required fields
            for question in passage.questions:
                self.assertIsNotNone(question.question_text)
                self.assertIsNotNone(question.correct_answer)
                self.assertIsNotNone(question.question_type)
    
    def test_difficulty_distribution(self):
        """Test difficulty distribution."""
        test = self.generator.generate_complete_test()
        
        difficulties = [p.difficulty for p in test.passages]
        self.assertIn("easy", difficulties)
        self.assertIn("medium", difficulties)
        self.assertIn("hard", difficulties)
    
    def test_topic_selection(self):
        """Test topic selection."""
        test = self.generator.generate_complete_test(
            topic_areas=["technology", "health"]
        )
        
        topics = [p.topic for p in test.passages]
        # Should use provided topics (may repeat)
        for topic in topics:
            self.assertIn(topic, ["technology", "health"])
    
    def test_custom_difficulty(self):
        """Test custom difficulty distribution."""
        test = self.generator.generate_complete_test(
            difficulty_mix={"easy": 14, "medium": 13, "hard": 13}
        )
        
        # First passage should have 14 questions (easy)
        self.assertEqual(len(test.passages[0].questions), 14)
        self.assertEqual(test.passages[0].difficulty, "easy")
    
    def test_question_types(self):
        """Test question type variety."""
        test = self.generator.generate_complete_test()
        
        all_types = set()
        for passage in test.passages:
            for question in passage.questions:
                all_types.add(question.question_type)
        
        # Should have at least 3 different question types
        self.assertGreaterEqual(len(all_types), 3)
    
    def test_answer_key_consistency(self):
        """Test answer key consistency."""
        test = self.generator.generate_complete_test()
        answer_key = test.get_answer_key()
        
        # Check all 40 questions have answers
        self.assertEqual(len(answer_key), 40)
        
        # Check answers match questions
        all_questions = test.get_all_questions()
        for question in all_questions:
            key = f"q{question.question_number}"
            self.assertIn(key, answer_key)
            self.assertEqual(answer_key[key], question.correct_answer)
    
    def test_json_serialization(self):
        """Test JSON serialization."""
        test = self.generator.generate_complete_test()
        json_str = test.to_json()
        
        self.assertIsInstance(json_str, str)
        
        # Parse back
        import json
        data = json.loads(json_str)
        self.assertEqual(data["total_questions"], 40)
        self.assertEqual(len(data["passages"]), 3)
    
    def test_reproducibility(self):
        """Test reproducibility with seed."""
        gen1 = IELTSReadingGenerator(use_ai=False, seed=123)
        gen2 = IELTSReadingGenerator(use_ai=False, seed=123)
        
        test1 = gen1.generate_complete_test()
        test2 = gen2.generate_complete_test()
        
        # Same seed should produce same test
        self.assertEqual(test1.to_json(), test2.to_json())


if __name__ == '__main__':
    unittest.main()