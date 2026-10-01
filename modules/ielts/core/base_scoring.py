# modules/ielts/core/base_scoring.py
"""Base scoring utilities for IELTS"""

from __future__ import annotations

import logging
import math
from typing import List, Optional


logger = logging.getLogger(__name__)


class BaseScoring:
    """Core IELTS band scoring utilities"""

    BAND_DESCRIPTORS = {
        9: "Expert User",
        8: "Very Good User",
        7: "Good User",
        6: "Competent User",
        5: "Modest User",
        4: "Limited User",
        3: "Extremely Limited User",
        2: "Intermittent User",
        1: "Non User",
        0: "Did Not Attempt",
    }

    CEFR_MAPPING = {
        9: "C2",
        8: "C1",
        7: "C1",
        6: "B2",
        5: "B1",
        4: "B1",
        3: "A2",
        2: "A1",
        1: "A1",
        0: "Pre-A1",
    }

    # =====================================================
    # VALIDATION
    # =====================================================

    @staticmethod
    def _safe_score(score: Optional[float]) -> float:
        """
        Validate and normalize IELTS score.
        """
        try:
            if score is None:
                return 0.0

            score = float(score)

            if math.isnan(score):
                return 0.0

            return max(0.0, min(9.0, score))

        except (ValueError, TypeError):
            logger.warning("Invalid IELTS score received: %s", score)
            return 0.0

    # =====================================================
    # ROUNDING
    # =====================================================

    @classmethod
    def round_band(cls, score: Optional[float]) -> float:
        """
        Round IELTS score to nearest 0.5 band.

        IELTS official rounding:
        6.24 -> 6.0
        6.25 -> 6.5
        6.74 -> 6.5
        6.75 -> 7.0
        """

        score = cls._safe_score(score)

        integer = math.floor(score)
        decimal = score - integer

        if decimal < 0.25:
            return float(integer)

        if decimal < 0.75:
            return integer + 0.5

        return float(integer + 1)

    # =====================================================
    # DESCRIPTORS
    # =====================================================

    @classmethod
    def get_descriptor(cls, band: Optional[float]) -> str:
        """
        Get IELTS band descriptor.
        """

        band = cls.round_band(band)
        key = int(round(band))

        return cls.BAND_DESCRIPTORS.get(
            key,
            f"Band {band}"
        )

    # =====================================================
    # CEFR
    # =====================================================

    @classmethod
    def get_cefr(cls, band: Optional[float]) -> str:
        """
        Convert IELTS band to CEFR level.
        """

        band = cls.round_band(band)
        key = int(round(band))

        return cls.CEFR_MAPPING.get(key, "Unknown")

    # =====================================================
    # OVERALL BAND
    # =====================================================

    @classmethod
    def calculate_overall_band(cls, scores: List[float]) -> float:
        """
        Calculate IELTS overall band score.

        Example:
        [6.5, 7.0, 6.0, 6.5]
        """

        if not scores:
            return 0.0

        valid_scores = [
            cls._safe_score(score)
            for score in scores
        ]

        average = sum(valid_scores) / len(valid_scores)

        return cls.round_band(average)


# =====================================================
# CONVENIENCE FUNCTIONS
# =====================================================

def round_ielts_band(score: Optional[float]) -> float:
    """Round IELTS band score"""
    return BaseScoring.round_band(score)


def get_band_description(band: Optional[float]) -> str:
    """Get IELTS band descriptor"""
    return BaseScoring.get_descriptor(band)


def get_cefr_level(band: Optional[float]) -> str:
    """Get CEFR level from IELTS band"""
    return BaseScoring.get_cefr(band)


def calculate_overall_ielts_band(scores: List[float]) -> float:
    """Calculate overall IELTS band"""
    return BaseScoring.calculate_overall_band(scores)