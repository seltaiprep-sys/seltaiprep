"""Collocation checker for natural language assessment

FIXES APPLIED (v2):
  (1) Removed the module-level singleton `collocation_checker = CollocationChecker()`.
        Every other module in this package was updated to use a factory
        function and no import-time side effects. This file was the last
        holdout. Use `create_collocation_checker()` instead.

  (2) Substring matching replaced with word-boundary regex. Previously
        `"according to me"` would match inside `"according to medical
        experts"`, and `"compare to"` would match `"compare together"`.
        False positives in the "errors" list were the most common
        complaint. Now we use `\\b...\\b` on the full phrase.

  (3) `_get_context` highlighting is now case-insensitive and correct.
        The previous version searched `essay.lower()` but replaced on
        the original `context` string, so `"Compare To"` was never
        highlighted. It also mis-highlighted when the same phrase
        appeared earlier in the essay window.

  (4) Duplicate-collocation counting fixed. Previously, if `"make a
        decision"` appeared 5 times in the essay, it still only counted
        as 1 correct collocation. Now we count each occurrence (up to a
        sensible cap to avoid over-weighting one overused phrase).

  (5) Error list is deduplicated — a repeated error now appears once
        with a `count` field, rather than once per occurrence.

  (6) Task-1 language check is now case-insensitive on the boundary
        of the phrase (was already lowercased, but the phrase list
        contained mixed-case entries that could fail silently).

  (7) `band_contribution` is now documented and derived consistently
        (score / 2 → 0-5 band contribution range).

  (8) Added a small `NEGATIVE_COLLOCATION_WEIGHT` constant so the
        penalty per unique error is tunable in one place.

  (9) Type hints tidied. `Tuple` was imported but never used.
"""
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Tunable weights
NEGATIVE_COLLOCATION_WEIGHT = 0.3 # band penalty per unique error
MAX_OCCURRENCES_PER_COLLOCATION = 5 # cap so one overused phrase can't dominate


def _phrase_to_regex(phrase: str) -> re.Pattern:
    """
    Build a case-insensitive whole-word regex for a multi-word phrase.

    All whitespace inside the phrase becomes `\\s+` so "on the other hand"
    matches "on the other hand" too.
    """
    parts = [re.escape(p) for p in phrase.split()]
    pattern = r'\b' + r'\s+'.join(parts) + r'\b'
    return re.compile(pattern, re.IGNORECASE)


class CollocationChecker:
    """Check if vocabulary is used in natural, native-like combinations."""

    # Class-level caches so we don't rebuild regexes on every essay.
    _correct_regex_cache: Dict[str, re.Pattern] = {}
    _incorrect_regex_cache: Dict[str, re.Pattern] = {}

    def __init__(self):
        # Common correct collocations (verb+noun, adj+noun, adv+adj, Task-1 language)
        self.correct_collocations: Dict[str, str] = {
            # Verb + Noun
            'make a decision': 'make a decision',
            'take action': 'take action',
            'reach a conclusion': 'reach a conclusion',
            'conduct research': 'conduct research',
            'draw attention': 'draw attention',
            'pose a threat': 'pose a threat',
            'raise awareness': 'raise awareness',
            'gain insight': 'gain insight',
            'hold a view': 'hold a view',
            'express an opinion': 'express an opinion',

            # Adjective + Noun
            'significant impact': 'significant impact',
            'key factor': 'key factor',
            'major concern': 'major concern',
            'critical issue': 'critical issue',
            'viable alternative': 'viable alternative',
            'comprehensive approach': 'comprehensive approach',
            'substantial increase': 'substantial increase',
            'gradual decline': 'gradual decline',
            'sharp contrast': 'sharp contrast',
            'clear correlation': 'clear correlation',

            # Adverb + Adjective
            'highly significant': 'highly significant',
            'extremely important': 'extremely important',
            'particularly relevant': 'particularly relevant',
            'relatively stable': 'relatively stable',
            'strongly correlated': 'strongly correlated',

            # Task 1 specific
            'account for': 'account for',
            'constitute': 'constitute',
            'fluctuate': 'fluctuate',
            'peak at': 'peak at',
            'plateau at': 'plateau at',
            'soar to': 'soar to',
            'plummet to': 'plummet to',
        }

        # Common incorrect collocations (L1 interference, textbook errors)
        self.incorrect_collocations: Dict[str, str] = {
            'make a research': 'conduct/do research',
            'do a decision': 'make a decision',
            'say an opinion': 'express/give an opinion',
            'discuss about': 'discuss (no preposition)',
            'explain about': 'explain',
            'according to me': 'in my opinion',
            'on my opinion': 'in my opinion',
            'in other side': 'on the other hand',
            'by other hand': 'on the other hand',
            # NOTE: "compare to" was previously flagged as an error, but it
            # is not — "compare to" and "compare with" are both standard.
            # We removed that entry to avoid false positives.
        }

        # Task 1 specific patterns (used as positive signals)
        self.task1_patterns: Dict[str, List[str]] = {
            'comparison': ['compared to', 'in comparison with', 'similar to', 'different from'],
            'trends': ['upward trend', 'downward trend', 'fluctuating pattern', 'steady increase'],
            'magnitude': ['significantly higher', 'marginally lower', 'substantially greater'],
            'exception': ['with the exception of', 'apart from', 'except for'],
        }

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------
    def check_essay(self, essay: str, task_type: str = 'task2') -> Dict:
        """
        Check an essay for natural collocations.

        Returns:
            {
                'score': 0-10,
                'band_contribution': score / 2 (0-5),
                'errors': list of {incorrect, correction, context, count},
                'suggestions': list of human-readable tips,
                'naturalness_rating': short label,
                'correct_collocations_found': list of phrases that were used well,
                'task1_language_score': 0-1 (only when task_type='task1'),
            }
        """
        if not essay or not essay.strip():
            return self._empty_result()

        correct_hits: Dict[str, int] = {}
        error_hits: Dict[str, int] = {}

        # ── Positive collocations ────────────────────────────────
        for phrase in self.correct_collocations:
            rx = self._get_correct_regex(phrase)
            count = len(rx.findall(essay))
            if count:
                correct_hits[phrase] = count

        # ── Negative collocations ────────────────────────────────
        for phrase in self.incorrect_collocations:
            rx = self._get_incorrect_regex(phrase)
            count = len(rx.findall(essay))
            if count:
                error_hits[phrase] = count

        # ── Build the errors list (deduplicated, with context) ───
        errors: List[Dict] = []
        for phrase, count in error_hits.items():
            errors.append({
                'incorrect': phrase,
                'correction': self.incorrect_collocations[phrase],
                'context': self._get_context(essay, phrase),
                'count': count,
            })
        # Sort errors by occurrence count, most frequent first
        errors.sort(key=lambda e: e['count'], reverse=True)

        # ── Task 1 specific language check ───────────────────────
        task1_language_score: Optional[float] = None
        if task_type == 'task1':
            task1_language_score = self._check_task1_language(essay)
            if task1_language_score < 0.6:
                errors.append({
                    'incorrect': 'Weak Task 1 language',
                    'correction': ('Use more precise language: "accounted for", '
                                   '"constituted", "peaked at"'),
                    'context': 'Your description lacks Task 1 specific vocabulary',
                    'count': 1,
                })

        # ── Score (0-10) ─────────────────────────────────────────
        # Total positive occurrences, capped so one overused phrase
        # can't overwhelm the signal.
        total_positive = sum(
            min(c, MAX_OCCURRENCES_PER_COLLOCATION) for c in correct_hits.values()
        )
        total_negative = sum(e['count'] for e in errors)

        total_signals = total_positive + total_negative
        if total_signals == 0:
            # Nothing to judge — neutral score
            score = 6.0
        else:
            # Accuracy = how much of the collocation signal was correct
            accuracy = total_positive / total_signals
            # Map 0.0 → 3.0, 0.5 → 6.5, 1.0 → 10.0
            score = 3.0 + (accuracy * 7.0)

        # Apply a modest penalty per unique error type
        unique_error_types = len(error_hits)
        score -= unique_error_types * NEGATIVE_COLLOCATION_WEIGHT
        final_score = max(3.0, min(10.0, score))

        # ── Suggestions ──────────────────────────────────────────
        suggestions: List[str] = []
        for error in errors[:3]:
            suggestions.append(
                f"Instead of '{error['incorrect']}', use '{error['correction']}'"
            )
        if not suggestions and correct_hits:
            suggestions.append(
                "Good use of natural collocations — keep building your repertoire."
            )

        return {
            'score': round(final_score, 1),
            # score/2 → 0-5 band contribution
            'band_contribution': round(final_score / 2, 1),
            'errors': errors,
            'suggestions': suggestions[:3],
            'naturalness_rating': self._get_naturalness_rating(final_score),
            'correct_collocations_found': sorted(correct_hits.keys()),
            'task1_language_score': task1_language_score,
        }

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------
    @classmethod
    def _get_correct_regex(cls, phrase: str) -> re.Pattern:
        rx = cls._correct_regex_cache.get(phrase)
        if rx is None:
            rx = _phrase_to_regex(phrase)
            cls._correct_regex_cache[phrase] = rx
        return rx

    @classmethod
    def _get_incorrect_regex(cls, phrase: str) -> re.Pattern:
        rx = cls._incorrect_regex_cache.get(phrase)
        if rx is None:
            rx = _phrase_to_regex(phrase)
            cls._incorrect_regex_cache[phrase] = rx
        return rx

    def _get_context(self, essay: str, phrase: str, window: int = 40) -> str:
        """
        Return a short snippet around the FIRST occurrence of `phrase`.

        The highlighted version wraps the phrase in **[ ]**, preserving
        the original casing of the matched text.
        """
        rx = re.compile(r'\b' + r'\s+'.join(re.escape(p) for p in phrase.split()) + r'\b',
                        re.IGNORECASE)
        m = rx.search(essay)
        if not m:
            return ""

        start = max(0, m.start() - window)
        end = min(len(essay), m.end() + window)

        snippet = essay[start:end]
        # Replace the matched span with the highlighted version, preserving case
        matched_text = essay[m.start():m.end()]
        # Use a positional replace to avoid touching earlier identical substrings
        rel_start = m.start() - start
        rel_end = m.end() - start
        highlighted = snippet[:rel_start] + f"**[{matched_text}]**" + snippet[rel_end:]
        return highlighted

    def _check_task1_language(self, essay: str) -> float:
        """Return the fraction of key Task-1 phrases present in the essay (0-1)."""
        required_phrases = [
            'overall', 'in contrast', 'compared to', 'higher than', 'lower than',
            'increased', 'decreased', 'remained stable', 'fluctuated',
        ]
        found_count = 0
        for phrase in required_phrases:
            rx = _phrase_to_regex(phrase)
            if rx.search(essay):
                found_count += 1
        return found_count / len(required_phrases)

    def _get_naturalness_rating(self, score: float) -> str:
        """Convert a 0-10 score to a short naturalness label."""
        if score >= 8:
            return "Excellent - very natural, native-like phrasing"
        if score >= 6.5:
            return "Good - mostly natural with minor issues"
        if score >= 5:
            return "Adequate - some unnatural phrasing"
        return "Limited - frequent unnatural collocations"

    def _empty_result(self) -> Dict:
        return {
            'score': 0.0,
            'band_contribution': 0.0,
            'errors': [],
            'suggestions': ['Write an essay to receive collocation feedback.'],
            'naturalness_rating': 'No content',
            'correct_collocations_found': [],
            'task1_language_score': None,
        }


# ============================================================
# FACTORY
# ============================================================
def create_collocation_checker() -> CollocationChecker:
    """
    Factory function — use this instead of importing a singleton.

    Each caller gets a fresh instance. The regex caches are class-level,
    so there is no cost to creating multiple instances.
    """
    return CollocationChecker()