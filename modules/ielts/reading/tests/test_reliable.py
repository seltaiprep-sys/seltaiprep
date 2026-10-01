"""Tests for the reliable generator."""

import unittest
from ..reliable_generator import ReliableGenerator


class TestReliableGenerator(unittest.TestCase):
    """Test cases for ReliableGenerator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.generator = ReliableGenerator()
    
    def test_generate_passage(self):
        """Test passage generation."""
        passage = self.generator.generate_passage(
            difficulty="medium",
            topic="technology",
            question_count=13,
            passage_number=1
        )
        
        self.assertIsNotNone(passage)
        self.assertTrue(len(passage.content) > 0)
        self.assertEqual(len(passage.questions), 13)
        self.assertEqual(passage.difficulty, "medium")
    
    def test_question_creation(self):
        """Test question creation."""
        content = "Test passage content for question generation."
        question = self.generator._create_question(
            q_type="multiple_choice",
            content=content,
            question_number=1,
            passage_number=1
        )
        
        self.assertIsNotNone(question)
        self.assertEqual(question.question_type, "multiple_choice")
        self.assertTrue(len(question.options) > 0)
    
    def test_fallback_content(self):
        """Test fallback to alternative topics."""
        passage = self.generator.generate_passage(
            difficulty="easy",
            topic="nonexistent_topic",
            question_count=13,
            passage_number=1
        )
        
        self.assertIsNotNone(passage)
        self.assertTrue(len(passage.content) > 0)


if __name__ == '__main__':
    unittest.main()