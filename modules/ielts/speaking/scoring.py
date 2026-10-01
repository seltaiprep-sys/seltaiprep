"""IELTS Speaking Band Score Calculator - Graceful Degradation with dynamic improvement prediction"""

import logging
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)


class IELTSBandCalculator:
    """Calculate IELTS Speaking band scores from four criteria.

    Features:
    - Official IELTS weighting: ALL 4 criteria weighted equally at 25% each
    - CEFR mapping (C2 → A1)
    - Band descriptors from official IELTS handbook
    - Personalized feedback (can be enhanced with analysis data)
    - Dynamic improvement prediction based on current band
    - Correct handling of empty/zero responses (Band 0)
    """

    # Official IELTS Speaking band descriptors
    BANDS = {
        9: "Expert User - Speaks fluently with only rare repetition or self-correction. Any hesitation is content-related rather than lexical or grammatical.",
        8: "Very Good User - Speaks fluently with only occasional repetition or self-correction. Hesitation is usually content-related.",
        7: "Good User - Speaks at length without noticeable effort. Some hesitation, repetition, and self-correction may occur.",
        6: "Competent User - Is willing to speak at length, though may lose coherence at times due to occasional repetition.",
        5: "Modest User - Usually maintains flow of speech but uses repetition, self-correction, and slow speech to keep going.",
        4: "Limited User - Cannot respond without noticeable pauses and may speak slowly, with frequent repetition and self-correction.",
        3: "Extremely Limited User - Speaks with long pauses. Has limited ability to link simple sentences.",
        2: "Intermittent User - Speaks with great difficulty. Only produces isolated words or short phrases.",
        1: "Non User - Essentially has no ability to communicate beyond possibly a few isolated words.",
        0: "Did not attempt the test. No assessable response."
    }

    # CEFR mapping
    CEFR_MAP = {
        9: "C2", 8.5: "C2", 8: "C1", 7.5: "C1", 7: "C1",
        6.5: "B2", 6: "B2", 5.5: "B2",
        5: "B1", 4.5: "B1", 4: "B1",
        3.5: "A2", 3: "A2",
        2.5: "A1", 2: "A1", 1: "A1", 0: "Pre-A1"
    }

    # FIX: IELTS Speaking weights are EQUAL at 25% each.
    # (The previous 30/25/25/20 split is not how IELTS actually works.)
    DEFAULT_WEIGHTS = {
        "fluency_weight": 0.25, # Fluency & Coherence
        "lexical_weight": 0.25, # Lexical Resource
        "grammar_weight": 0.25, # Grammatical Range & Accuracy
        "pronunciation_weight": 0.25 # Pronunciation
    }

    # Improvement prediction thresholds (based on current band)
    IMPROVEMENT_THRESHOLDS = {
        (0, 5): 2.0, # Beginners can improve up to 2 bands
        (5, 7): 1.5, # Intermediate can improve up to 1.5 bands
        (7, 9): 1.0 # Advanced can improve up to 1 band
    }

    def __init__(self, weights: Dict = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        logger.info(f"IELTSBandCalculator initialized with weights: {self.weights}")

    # ============================================================
    # CORE CALCULATION
    # ============================================================

    def calculate(
        self,
        fluency: float,
        lexical: float,
        grammar: float,
        pronunciation: float,
        word_count: int = 0,
        audio_duration: float = 0.0,
    ) -> Dict:
        """
        Calculate overall band score from 4 criteria.

        Args:
            fluency: Fluency & Coherence score (0-9)
            lexical: Lexical Resource score (0-9)
            grammar: Grammatical Range & Accuracy score (0-9)
            pronunciation: Pronunciation score (0-9)
            word_count: Total words spoken (for validation)
            audio_duration: Total audio duration in seconds (for validation)

        Returns:
            Dict with overall band, descriptor, CEFR, criteria, and feedback
        """
        # FIX: Empty / no-audio / no-words -> Band 0
        if word_count == 0 and audio_duration == 0:
            return self._empty_result(
                reason="No response provided — nothing to assess."
            )

        # FIX: Too-short responses -> Band 0
        # A real IELTS speaking test requires at least ~50-100 words across all answers.
        if word_count > 0 and word_count < 10:
            return self._empty_result(
                reason=f"Response too short ({word_count} words) — no assessable content.",
                word_count=word_count,
                audio_duration=audio_duration,
            )

        # FIX: Clamp inputs to 0-9 range (0 allowed now, was 1.0 floor)
        try:
            fluency = max(0.0, min(9.0, float(fluency or 0)))
            lexical = max(0.0, min(9.0, float(lexical or 0)))
            grammar = max(0.0, min(9.0, float(grammar or 0)))
            pronunciation = max(0.0, min(9.0, float(pronunciation or 0)))
        except (TypeError, ValueError):
            fluency = lexical = grammar = pronunciation = 0.0

        # Weighted average (equal weights)
        overall = (
            fluency * self.weights["fluency_weight"] +
            lexical * self.weights["lexical_weight"] +
            grammar * self.weights["grammar_weight"] +
            pronunciation * self.weights["pronunciation_weight"]
        )

        # FIX: Round to nearest 0.5, floor 0.0 (was 1.0)
        band = self._round_to_half(overall)
        band = max(0.0, min(9.0, band))

        return {
            "overall_band": band,
            "band_descriptor": self.get_descriptor(band),
            "cefr_level": self.get_cefr(band),
            "criteria": {
                "fluency_coherence": round(fluency, 1),
                "lexical_resource": round(lexical, 1),
                "grammar_accuracy": round(grammar, 1),
                "pronunciation": round(pronunciation, 1)
            },
            "word_count": word_count,
            "audio_duration": round(audio_duration, 1),
            "feedback": self.generate_feedback(band, fluency, lexical, grammar, pronunciation),
            "weights_used": self.weights
        }

    # ============================================================
    # EMPTY / SHORT RESPONSE HANDLING
    # ============================================================

    def _empty_result(
        self,
        reason: str,
        word_count: int = 0,
        audio_duration: float = 0.0,
    ) -> Dict:
        """Return a fully-shaped Band 0 result for empty/short responses."""
        return {
            "overall_band": 0.0,
            "band_descriptor": self.BANDS[0],
            "cefr_level": self.CEFR_MAP[0],
            "criteria": {
                "fluency_coherence": 0.0,
                "lexical_resource": 0.0,
                "grammar_accuracy": 0.0,
                "pronunciation": 0.0
            },
            "word_count": word_count,
            "audio_duration": round(audio_duration, 1),
            "feedback": {
                "strengths": [],
                "areas_to_improve": [reason],
                "tips": [
                    "Attempt all three parts of the speaking test.",
                    "Part 1: aim for 2-4 sentences per answer.",
                    "Part 2: speak for the full 2 minutes.",
                    "Part 3: give extended, analytical answers.",
                ]
            },
            "weights_used": self.weights
        }

    # ============================================================
    # HELPERS
    # ============================================================

    def _round_to_half(self, value: float) -> float:
        """Round to nearest 0.5."""
        return round(value * 2) / 2

    def get_cefr(self, band: float) -> str:
        """Map IELTS band to CEFR level."""
        normalized = self._round_to_half(band)
        if normalized in self.CEFR_MAP:
            return self.CEFR_MAP[normalized]
        closest = min(self.CEFR_MAP.keys(), key=lambda x: abs(x - normalized))
        return self.CEFR_MAP.get(closest, "Unknown")

    def get_descriptor(self, band: float) -> str:
        """Get band descriptor text with graceful fallback."""
        if band <= 0:
            return self.BANDS[0]
        int_band = int(band)
        if int_band in self.BANDS:
            return self.BANDS[int_band]
        if band >= 8.5:
            return self.BANDS[8]
        elif band >= 7.5:
            return self.BANDS[7]
        elif band >= 6.5:
            return self.BANDS[6]
        elif band >= 5.5:
            return self.BANDS[5]
        elif band >= 4.5:
            return self.BANDS[4]
        elif band >= 3.5:
            return self.BANDS[3]
        elif band >= 2.5:
            return self.BANDS[2]
        elif band >= 1.5:
            return self.BANDS[1]
        else:
            return self.BANDS[0]

    # ============================================================
    # CONVENIENCE WRAPPER
    # ============================================================

    def calculate_from_scores(self, scores: Dict) -> Dict:
        """
        Calculate from a dictionary of scores.
         FIX: Missing values default to 0 (was 5).
        """
        return self.calculate(
            fluency=scores.get("fluency_coherence", 0) or 0,
            lexical=scores.get("lexical_resource", 0) or 0,
            grammar=scores.get("grammar_accuracy", 0) or 0,
            pronunciation=scores.get("pronunciation", 0) or 0,
            word_count=scores.get("word_count", 0) or 0,
            audio_duration=scores.get("audio_duration", 0.0) or 0.0,
        )

    # ============================================================
    # FEEDBACK GENERATION
    # ============================================================

    def generate_feedback(
        self,
        band: float,
        fluency: float,
        lexical: float,
        grammar: float,
        pronunciation: float,
        analysis_data: Optional[Dict] = None
    ) -> Dict:
        """
        Generate personalized feedback based on scores and optional analysis data.
        """
        feedback = {
            "strengths": [],
            "areas_to_improve": [],
            "tips": []
        }

        # Band 0 case
        if band <= 0 or (fluency == 0 and lexical == 0 and grammar == 0 and pronunciation == 0):
            feedback["areas_to_improve"].append(
                "No assessable response was detected."
            )
            feedback["tips"].append(
                "Please answer every question, even briefly, to receive a score."
            )
            return feedback

        # ---- Fluency feedback ----
        if fluency >= 7:
            feedback["strengths"].append("You speak fluently with good coherence")
        elif fluency >= 6:
            feedback["strengths"].append("You can speak at length with occasional pauses")
        else:
            feedback["areas_to_improve"].append("Work on speaking more fluently with fewer pauses")
            feedback["tips"].append("Practice speaking for 1-2 minutes on random topics daily")
            if analysis_data and analysis_data.get("emotion"):
                hesitation = analysis_data["emotion"].get("hesitation", 0)
                if hesitation > 0.5:
                    feedback["tips"].append(
                        "Your hesitation markers are high – try to reduce filler words like 'um' and 'uh'"
                    )

        # ---- Lexical feedback ----
        if lexical >= 7:
            feedback["strengths"].append("Good range of vocabulary with appropriate usage")
        elif lexical >= 6:
            feedback["strengths"].append("Adequate vocabulary for most topics")
        else:
            feedback["areas_to_improve"].append("Expand your vocabulary range")
            feedback["tips"].append("Learn 10 new words daily and practice using them in sentences")

        # ---- Grammar feedback ----
        if grammar >= 7:
            feedback["strengths"].append("Good grammatical control with few errors")
        elif grammar >= 6:
            feedback["strengths"].append("Generally good grammar with occasional errors")
        else:
            feedback["areas_to_improve"].append("Work on grammatical accuracy")
            feedback["tips"].append("Focus on tenses and sentence structures")
            if analysis_data and analysis_data.get("grammar"):
                errors = analysis_data["grammar"].get("errors", [])
                error_categories = [e.get("category") for e in errors if "category" in e]
                if "third_person" in error_categories or "third_person_s" in error_categories:
                    feedback["tips"].append(
                        "Practice third-person singular -s (he/she/it + verb+s)"
                    )
                if "agreement" in error_categories:
                    feedback["tips"].append(
                        "Work on subject-verb agreement (singular/plural)"
                    )
                if "double_past" in error_categories:
                    feedback["tips"].append(
                        "Use base form after 'didn't' (e.g., 'didn't go')"
                    )

        # ---- Pronunciation feedback ----
        if pronunciation >= 7:
            feedback["strengths"].append("Clear pronunciation that is easy to understand")
        elif pronunciation >= 6:
            feedback["strengths"].append("Generally clear pronunciation with some minor issues")
        else:
            feedback["areas_to_improve"].append("Work on clearer pronunciation")
            feedback["tips"].append("Listen to native speakers and practice shadowing")

        # ---- Overall tips (band-based) ----
        if band >= 8:
            feedback["tips"].append("Excellent performance! Focus on maintaining this level")
        elif band >= 7:
            feedback["tips"].append("You're doing well! Focus on polishing weaker areas")
        elif band >= 6:
            feedback["tips"].append(
                "Good foundation. Identify your weakest criterion and focus there"
            )
        elif band >= 5:
            feedback["tips"].append(
                "Focus on your weakest area first for maximum improvement"
            )
        else:
            feedback["tips"].append(
                "Start with basic speaking practice and vocabulary building"
            )

        return feedback

    # ============================================================
    # IMPROVEMENT PREDICTION
    # ============================================================

    def predict_band_improvement(
        self,
        current_scores: Dict,
        target_band: float
    ) -> Dict:
        """
        Predict what improvements are needed to reach target band.
         FIX: Missing values default to 0 (was 5).
        """
        current_result = self.calculate_from_scores(current_scores)
        current_band = current_result["overall_band"]

        if current_band >= target_band:
            return {
                "achievable": True,
                "message": f"You already meet or exceed Band {target_band}",
                "suggestions": ["Maintain your current level", "Focus on consistency"]
            }

        gap = target_band - current_band

        # Dynamic threshold based on current band
        achievable = False
        for (lo, hi), threshold in self.IMPROVEMENT_THRESHOLDS.items():
            if lo <= current_band < hi:
                achievable = gap <= threshold
                break
        else:
            achievable = gap <= 1.0

        # FIX: Default to 0 (was 5)
        criteria = [
            ("fluency_coherence", current_scores.get("fluency_coherence", 0) or 0,
             self.weights["fluency_weight"]),
            ("lexical_resource", current_scores.get("lexical_resource", 0) or 0,
             self.weights["lexical_weight"]),
            ("grammar_accuracy", current_scores.get("grammar_accuracy", 0) or 0,
             self.weights["grammar_weight"]),
            ("pronunciation", current_scores.get("pronunciation", 0) or 0,
             self.weights["pronunciation_weight"])
        ]

        # Sort by score (ascending) and then by weight (descending)
        sorted_criteria = sorted(criteria, key=lambda x: (x[1], -x[2]))
        weakest = sorted_criteria[0]

        suggestions = [
            f"Focus on improving {weakest[0].replace('_', ' ').title()} (current: {weakest[1]})",
            f"Need to improve by approximately {round(gap, 1)} bands overall",
            "Practice with timed speaking tests daily"
        ]

        if "grammar" in weakest[0]:
            suggestions.append(
                "Practice grammar exercises daily (focus on tenses, articles, and prepositions)"
            )
        elif "fluency" in weakest[0]:
            suggestions.append(
                "Record yourself and count hesitations. Aim to reduce filler words"
            )
        elif "lexical" in weakest[0]:
            suggestions.append(
                "Learn 5-10 new words daily and use them in sentences"
            )
        elif "pronunciation" in weakest[0]:
            suggestions.append(
                "Shadow native speakers (listen and repeat simultaneously)"
            )

        return {
            "achievable": achievable,
            "current_band": round(current_band, 1),
            "target_band": target_band,
            "gap": round(gap, 1),
            "weakest_criterion": weakest[0].replace('_', ' ').title(),
            "weakest_score": weakest[1],
            "suggestions": suggestions
        }


# Factory function (recommended)
def create_band_calculator(weights: Dict = None) -> IELTSBandCalculator:
    """Factory function to create IELTSBandCalculator."""
    return IELTSBandCalculator(weights)