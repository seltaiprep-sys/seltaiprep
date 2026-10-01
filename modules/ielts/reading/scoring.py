"""Band score calculation according to official IELTS standards."""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class BandScoreCalculator:
    ACADEMIC_BAND_SCORES = {
        40: 9.0, 39: 9.0, 38: 8.5, 37: 8.5,
        36: 8.0, 35: 8.0, 34: 7.5, 33: 7.5,
        32: 7.0, 31: 7.0, 30: 7.0, 29: 6.5,
        28: 6.5, 27: 6.0, 26: 6.0, 25: 6.0,
        24: 5.5, 23: 5.5, 22: 5.5, 21: 5.0,
        20: 5.0, 19: 5.0, 18: 4.5, 17: 4.5,
        16: 4.0, 15: 4.0, 14: 3.5, 13: 3.5,
        12: 3.0, 11: 3.0, 10: 2.5, 9: 2.5,
        8: 2.0, 7: 2.0, 6: 1.5, 5: 1.5,
        4: 1.0, 3: 1.0, 2: 1.0, 1: 1.0, 0: 0.0
    }

    def __init__(self, test_type='academic'):
        self.test_type = test_type
        self.score_table = self.ACADEMIC_BAND_SCORES

    def calculate_score(self, correct_answers: int, total_questions: int = 40) -> float:
        if total_questions == 0:
            return 0.0
        if total_questions != 40:
            normalized = int((correct_answers / total_questions) * 40)
            correct_answers = max(0, min(40, normalized))
        correct_answers = max(0, min(40, correct_answers))
        return self.score_table.get(correct_answers, 0.0)

    def calculate_from_results(self, result_dict: Dict) -> float:
        correct = result_dict.get('correct_count', 0)
        total = result_dict.get('total_questions', 40)
        return self.calculate_score(correct, total)

    def get_skill_level(self, band_score: float) -> Dict:
        band = round(band_score * 2) / 2
        levels = {
            (8.5, 9.0): {'level': 'Expert User', 'description': 'Fully operational command.'},
            (7.5, 8.0): {'level': 'Very Good User', 'description': 'Operational command with occasional inaccuracies.'},
            (6.5, 7.0): {'level': 'Good User', 'description': 'Operational command with occasional misunderstandings.'},
            (5.5, 6.0): {'level': 'Competent User', 'description': 'Generally effective command despite some errors.'},
            (4.5, 5.0): {'level': 'Modest User', 'description': 'Partial command, copes with overall meaning.'},
            (3.5, 4.0): {'level': 'Limited User', 'description': 'Basic competence limited to familiar situations.'},
            (0.0, 3.0): {'level': 'Basic User', 'description': 'Very limited ability.'}
        }
        for (low, high), info in levels.items():
            if low <= band <= high:
                return info
        return {'level': 'Unknown', 'description': 'Band score outside range.'}