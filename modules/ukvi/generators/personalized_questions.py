# modules/ukvi/generators/personalized_questions.py
"""Wrapper for AI-powered personalized question generation"""

from typing import Dict

from .interview_generator import UKVIInterviewGenerator


class PersonalizedQuestionGenerator:
    def __init__(self, ai_engine=None):
        self.ai = ai_engine
        self.generator = UKVIInterviewGenerator(ai_engine)

    def generate_personalized(self, profile: Dict, difficulty: str = "medium") -> Dict:
        """Generate personalized questions with tips."""
        result = self.generator.generate_interview(
            difficulty=difficulty,
            visa_type='student',
            profile=profile
        )

        # Check if generation failed
        if not result.get('success', False):
            return {
                'error': result.get('error', 'Failed to generate personalized questions'),
                'success': False
            }

        return {
            "personalized_questions": result.get('questions', []),
            "key_topics": ["Course curriculum", "University facts", "Financial breakdown", "Career plan"],
            "potential_weak_spots": ["Course knowledge depth", "University choice", "Return intention"],
            "interview_tips": ["Research university website", "Have cost figures ready", "Prepare home ties evidence"],
            "ai_generated": result.get('ai_generated', False),
        }