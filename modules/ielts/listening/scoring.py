"""IELTS Listening band score calculation and conversion"""

from typing import Dict

from ..core.base_scoring import BaseScoring

# Official IELTS Listening band score conversion tables
LISTENING_BAND_SCORES = { # Academic
    40: 9.0, 39: 9.0,
    38: 8.5, 37: 8.5,
    36: 8.0, 35: 8.0,
    34: 7.5, 33: 7.5,
    32: 7.0, 31: 7.0, 30: 7.0,
    29: 6.5, 28: 6.5, 27: 6.5,
    26: 6.0, 25: 6.0, 24: 6.0, 23: 6.0,
    22: 5.5, 21: 5.5, 20: 5.5, 19: 5.5,
    18: 5.0, 17: 5.0, 16: 5.0,
    15: 4.5, 14: 4.5, 13: 4.5,
    12: 4.0, 11: 4.0, 10: 4.0,
    9: 3.5, 8: 3.5,
    7: 3.0, 6: 3.0,
    5: 2.5, 4: 2.5,
    3: 2.0, 2: 2.0,
    1: 1.0, 0: 0.0,
}

LISTENING_BAND_SCORES_GT = { # General Training
    40: 9.0, 39: 9.0,
    38: 8.5, 37: 8.5,
    36: 8.0, 35: 7.5,
    34: 7.5, 33: 7.0,
    32: 7.0, 31: 6.5,
    30: 6.5, 29: 6.0,
    28: 6.0, 27: 5.5,
    26: 5.5, 25: 5.0,
    24: 5.0, 23: 4.5,
    22: 4.5, 21: 4.0,
    20: 4.0, 19: 4.0,
    18: 3.5, 17: 3.5,
    16: 3.0, 15: 3.0,
    14: 2.5, 13: 2.5,
    12: 2.0, 11: 2.0,
    10: 1.5, 9: 1.5,
    8: 1.0, 7: 1.0,
    6: 1.0, 5: 1.0,
    4: 1.0, 3: 1.0,
    2: 1.0, 1: 1.0,
    0: 0.0,
}


class ListeningScoring(BaseScoring):
    """IELTS Listening-specific scoring logic"""

    @staticmethod
    def get_band(correct: int, is_academic: bool = True) -> float:
        """Convert number of correct answers (0-40) to IELTS band score."""
        if not isinstance(correct, (int, float)):
            correct = 0
        correct = max(0, min(40, int(correct)))

        table = LISTENING_BAND_SCORES if is_academic else LISTENING_BAND_SCORES_GT
        return table.get(correct, 0.0)

    @classmethod
    def get_descriptor(cls, band: float) -> str:
        """Return band descriptor (e.g. 'Good user', 'Expert user')"""
        if hasattr(BaseScoring, 'get_descriptor'):
            return BaseScoring.get_descriptor(band)
        return ""

    @classmethod
    def get_cefr(cls, band: float) -> str:
        """Return CEFR level for the band score"""
        if hasattr(BaseScoring, 'get_cefr'):
            return BaseScoring.get_cefr(band)
        return "A1"

    @staticmethod
    def get_score_pct(correct: int) -> float:
        """Return percentage score (0-100)"""
        correct = max(0, min(40, correct))
        return round((correct / 40) * 100, 1)

    @staticmethod
    def get_correct_needed(target_band: float, is_academic: bool = True) -> int:
        """Return minimum correct answers needed to achieve target band"""
        table = LISTENING_BAND_SCORES if is_academic else LISTENING_BAND_SCORES_GT
        for correct in sorted(table.keys()):
            if table[correct] >= target_band:
                return correct
        return 40

    @staticmethod
    def get_band_range(start: int = 0, end: int = 40, is_academic: bool = True) -> Dict[int, float]:
        """Get band scores for a range of correct answers"""
        table = LISTENING_BAND_SCORES if is_academic else LISTENING_BAND_SCORES_GT
        return {
            correct: table.get(correct, 0.0)
            for correct in range(max(0, start), min(40, end) + 1)
        }


# =============================================================================
# Module-level convenience functions
# =============================================================================

def get_listening_band_score(correct_count: int, is_academic: bool = True) -> float:
    """Quick access: correct count → band score"""
    return ListeningScoring.get_band(correct_count, is_academic)


def get_listening_band_descriptor(band_score: float) -> str:
    """Quick access: band score → descriptor"""
    return ListeningScoring.get_descriptor(band_score)


def get_listening_cefr(band_score: float) -> str:
    """Quick access: band score → CEFR level"""
    return ListeningScoring.get_cefr(band_score)


def get_correct_for_band(target_band: float, is_academic: bool = True) -> int:
    """Quick access: target band → minimum correct answers needed"""
    return ListeningScoring.get_correct_needed(target_band, is_academic)