"""Tests for the scoring module."""

import unittest
from ..scoring import BandScoreCalculator


class TestBandScoreCalculator(unittest.TestCase):
    """Test cases for BandScoreCalculator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.calculator = BandScoreCalculator()
    
    def test_academic_band_scores(self):
        """Test academic band score conversion."""
        test_cases = [
            (40, 9.0),
            (39, 9.0),
            (38, 8.5),
            (35, 8.0),
            (30, 7.0),
            (25, 6.0),
            (20, 5.0),
            (15, 4.0),
            (10, 2.5),
            (5, 1.5),
            (0, 0.0),
        ]
        
        for correct, expected_band in test_cases:
            result = self.calculator.calculate_score(correct, 40, "academic")
            self.assertEqual(
                result.band_score,
                expected_band,
                f"Failed for {correct} correct answers"
            )
    
    def test_general_training_scores(self):
        """Test general training band score conversion."""
        result = self.calculator.calculate_score(40, 40, "general")
        self.assertEqual(result.band_score, 9.0)
    
    def test_skill_levels(self):
        """Test skill level descriptions."""
        test_cases = [
            (9.0, "Expert User"),
            (7.5, "Very Good User"),
            (6.5, "Competent User"),
            (5.5, "Modest User"),
            (4.5, "Limited User"),
            (3.0, "Extremely Limited/Intermittent User"),
        ]
        
        for band, expected_level in test_cases:
            level_info = self.calculator._get_skill_level(band)
            self.assertEqual(level_info["level"], expected_level)
    
    def test_recommendations(self):
        """Test recommendation generation."""
        result = self.calculator.calculate_score(25, 40, "academic")
        self.assertIsInstance(result.recommendations, list)
        self.assertTrue(len(result.recommendations) > 0)
    
    def test_predict_band_score(self):
        """Test band score prediction."""
        # Need 30 correct for band 7.0
        # Currently have 20 correct, 10 questions remaining
        additional, accuracy = self.calculator.predict_band_score(
            current_correct=20,
            remaining_questions=10,
            target_band=7.0
        )
        
        self.assertEqual(additional, 10) # Need 10 more correct
        self.assertEqual(accuracy, 100.0) # Need 100% accuracy on remaining
    
    def test_section_scores(self):
        """Test section score calculation."""
        passage_results = [
            {"correct_count": 10, "total_questions": 13},
            {"correct_count": 8, "total_questions": 13},
            {"correct_count": 12, "total_questions": 14}
        ]
        
        scores = self.calculator.calculate_section_scores(passage_results)
        
        self.assertAlmostEqual(scores["Passage 1"], (10/13)*100, 1)
        self.assertAlmostEqual(scores["Passage 2"], (8/13)*100, 1)
        self.assertAlmostEqual(scores["Passage 3"], (12/14)*100, 1)


if __name__ == '__main__':
    unittest.main()