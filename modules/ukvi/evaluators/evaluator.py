# modules/ukvi/evaluators/evaluator.py
"""UKVI Evaluation – AI-based scoring and feedback"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class UKVIEvaluator:
    def __init__(self, ai_engine):
        """Store AI engine for evaluation (may be used for advanced scoring)."""
        self.ai = ai_engine

    def evaluate(self, question: str, answer: str, category: str = 'general') -> Dict:
        """
        Evaluate a single answer.
        For now, a rule‑based fallback is used; can be upgraded to AI.
        """
        word_count = len(answer.split())
        # Simple scoring
        if word_count < 10:
            score = 5.0
        elif word_count < 30:
            score = 6.5
        elif word_count < 60:
            score = 7.5
        else:
            score = 8.0

        # Adjust for category
        if category == 'financial' and not any(c.isdigit() for c in answer):
            score -= 0.5
        if category == 'ties_to_home' and 'return' not in answer.lower():
            score -= 0.5

        score = max(0.0, min(9.0, score))

        # Simple feedback
        if word_count < 10:
            feedback = "Your answer is too short. Please provide more detail."
        elif score < 6.0:
            feedback = "Your answer lacks specificity. Be more concrete."
        else:
            feedback = "Good response, well structured."

        return {
            'overall_score': round(score, 1),
            'fluency': round(score, 1),
            'grammar': round(score, 1),
            'vocabulary': round(score, 1),
            'coherence': round(score, 1),
            'feedback': feedback,
            'word_count': word_count,
        }

    def final_evaluation(self, evaluations: List[Dict], profile: Dict) -> Dict:
        """Aggregate scores from all answers into a final interview evaluation."""
        if not evaluations:
            return {
                'overall_score': 0.0,
                'recommendations': ['No answers provided'],
                'credibility_avg': 0,
            }

        # Average overall scores
        scores = [e.get('overall_score', 0) for e in evaluations if isinstance(e.get('overall_score'), (int, float))]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Collect all feedback
        all_feedback = [e.get('feedback', '') for e in evaluations if e.get('feedback')]

        # Determine recommendation based on average
        if avg_score >= 7.0:
            rec = "Strong performance. Continue practising to maintain your level."
        elif avg_score >= 5.0:
            rec = "Good effort. Work on providing more detailed and specific answers."
        else:
            rec = "Needs improvement. Focus on fluency and providing concrete evidence."

        recommendations = [rec] + (all_feedback[:3] if all_feedback else [])

        # Credibility average – just a simulated metric
        credibility_avg = min(100, int(avg_score * 12)) # rough mapping

        return {
            'overall_score': round(avg_score, 1),
            'recommendations': recommendations,
            'credibility_avg': credibility_avg,
        }