"""Grammar analysis for IELTS Speaking module - Graceful Degradation with heuristic detection"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class GrammarResult:
    """Grammar analysis result with error density and complexity metrics"""
    # FIX: default 0.0 (was 5.0)
    score: float = 0.0
    errors: List[Dict] = field(default_factory=list)
    error_density: float = 0.0
    complexity: int = 0
    details: Dict = field(default_factory=dict)


class SpokenGrammarAnalyzer:
    """
    Analyze grammatical accuracy in spoken English.

     FIX: Empty/short input -> score 0.0
     FIX: Minimum 15 words required for a real score
     FIX: Exception -> score 0.0
    """

    # FIX: new constant
    MIN_WORDS_FOR_SCORE = 15

    # ----- Complex Patterns (Reward structures) -----
    COMPLEX_PATTERNS = {
        'conditionals': r'\bif\s+.*\bwould\b',
        'contrast': r'\b(?:although|though|even though)\b',
        'passive': r'\b(?:is|are|was|were)\s+\w+ed\b',
        'perfect_tenses': r'\b(?:have|has|had)\s+\w+ed\b',
        'modals': r'\b(?:will|would|might|could|should|must)\s+\w+\b',
        'complex_prepositions': r'\b(?:despite|in spite of|due to|owing to)\b',
        'correlative': r'\b(?:not only|but also)\b',
        'comparative': r'\b(?:the\s+\w+er.*the\s+\w+er)\b',
        'hedging': r'\b(?:perhaps|maybe|probably|tend to|tends to|seems to)\b',
        'relative_clauses': r'\b(?:which|who|whom|whose|where|when)\b',
    }

    # ----- Error Patterns (Penalize structures) -----
    ERROR_PATTERNS = [
        (r'\b(he|she|it)\s+don\'t\b', 'third_person', 'Subject-verb agreement: use "doesn\'t"'),
        (r'\b(people|children|parents|students|friends)\s+(is|was)\b', 'agreement', 'Subject-verb agreement with plural: use "are/were"'),
        (r'\bI\s+(is|are)\b', 'be_verb', 'Use "I am" not "I is/are"'),
        (r'\b(these|those)\s+is\b', 'demonstrative_agreement', 'Use "are" with these/those'),
        (r'\b(he|she|it)\s+([a-z]{2,}[^s])\b', 'third_person_s',
         'Missing -s for third person singular (he/she/it + verb+s)'),
        (r'\bdidn\'t\s+\w+ed\b', 'double_past', 'Use base form after "didn\'t" (e.g., "didn\'t go")'),
        (r'\bcan\s+to\b', 'modal_infinitive', 'No "to" after modal verbs (can, should, might)'),
        (r'\bmore\s+(better|worse)\b', 'double_comparative', 'Double comparative: use "better" alone'),
        (r'\b(a)\s+([aeiouAEIOU]\w*)', 'article_vowel', 'Use "an" before words starting with vowel sounds (e.g., "an apple")'),
        (r'\bmuch\s+(\w+s)\b', 'much_plural', 'Use "many" with plural countable nouns (e.g., "many people")'),
        (r'\bthere\s+is\s+\w+s\b', 'there_is_plural', 'Use "there are" for plural nouns'),
    ]

    IRREGULAR_PAST = {'went', 'made', 'took', 'saw', 'did', 'had', 'said', 'came', 'gave', 'found'}
    MODALS = {'can', 'may', 'might', 'will', 'would', 'could', 'should', 'must'}
    IRREGULAR_VERBS = {'go', 'do', 'have', 'say', 'make', 'take', 'see', 'come', 'give', 'find'}
    VERB_EXCEPTIONS = IRREGULAR_PAST | MODALS | {'ing', 'ed'}

    def __init__(self, detect_advanced_errors: bool = True):
        self.detect_advanced_errors = detect_advanced_errors
        logger.info(f"SpokenGrammarAnalyzer initialized (advanced_errors: {detect_advanced_errors})")

    # ============================================================
    # EMPTY / SHORT RESPONSE HANDLER
    # ============================================================

    def _empty_result(self, word_count: int, reason: str) -> GrammarResult:
        """Return a Band 0 result for empty/short input."""
        return GrammarResult(
            score=0.0,
            errors=[],
            error_density=0.0,
            complexity=0,
            details={
                "word_count": word_count,
                "message": reason,
                "empty": True,
            }
        )

    # ============================================================
    # MAIN ANALYZE
    # ============================================================

    def analyze(self, text: str) -> GrammarResult:
        """
        Analyze grammar in spoken text.

         FIX: Empty/short input -> score 0.0 (was 4.0 / 4.5)
         FIX: Exception -> score 0.0 (was 5.0)
         FIX: Minimum 15 words required for a real score
        """
        # FIX: Normalize input
        if text is None:
            text = ""
        text = str(text).strip()

        # FIX: Empty input -> Band 0
        if not text or len(text) < 3:
            return self._empty_result(0, "BAND 0 — No response provided.")

        words = text.split()
        word_count = len(words)

        # FIX: Too short -> Band 0 (was 4.5)
        if word_count < self.MIN_WORDS_FOR_SCORE:
            return self._empty_result(
                word_count,
                f"BAND 0 — Response too short ({word_count} words). "
                f"At least {self.MIN_WORDS_FOR_SCORE} words are needed for grammar analysis."
            )

        try:
            errors = self._detect_errors(text)
            error_density = len(errors) / (word_count / 100) if word_count > 0 else 0

            complexity = 0
            complexity_details = {}
            for name, pattern in self.COMPLEX_PATTERNS.items():
                count = len(re.findall(pattern, text, re.IGNORECASE))
                if count > 0:
                    complexity += 1
                    complexity_details[name] = count

            base_score = self._calculate_base_score(error_density)
            complexity_bonus = min(1.5, complexity * 0.25)

            if error_density > 4 and complexity < 3:
                complexity_bonus = max(0, complexity_bonus - 0.5)

            # FIX: Floor 0.0 (was 3.0)
            score = round(max(0.0, min(9.0, base_score + complexity_bonus)), 1)

            return GrammarResult(
                score=score,
                errors=errors[:8],
                error_density=round(error_density, 1),
                complexity=complexity,
                details={
                    "word_count": word_count,
                    "complex_structures_found": complexity,
                    "complex_breakdown": complexity_details,
                    "error_rate_per_100_words": round(error_density, 1),
                    "base_score": round(base_score, 1),
                    "complexity_bonus": round(complexity_bonus, 1)
                }
            )

        except Exception as e:
            logger.error(f"Grammar analysis error: {e}")
            # FIX: Exception -> 0.0 (was 5.0)
            return self._empty_result(
                word_count,
                "BAND 0 — Analysis unavailable due to an internal error."
            )

    def _detect_errors(self, text: str) -> List[Dict]:
        """Detect grammatical errors using regex patterns."""
        errors = []
        patterns = self.ERROR_PATTERNS if self.detect_advanced_errors else self.ERROR_PATTERNS[:6]

        for pattern, category, explanation in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matched_text = match.group()
                error_data = {
                    'text': matched_text,
                    'category': category,
                    'explanation': explanation,
                    'position': match.start()
                }

                if category == 'third_person_s':
                    if len(match.groups()) >= 2:
                        verb = match.group(2).lower()
                        if verb in self.VERB_EXCEPTIONS or len(verb) < 3:
                            continue
                        if verb in self.IRREGULAR_PAST:
                            continue

                errors.append(error_data)

        errors.sort(key=lambda x: x['position'])

        unique_errors = []
        last_end = -1
        for error in errors:
            if error['position'] > last_end + 5:
                unique_errors.append(error)
                last_end = error['position'] + len(error['text'])

        return unique_errors[:8]

    def _calculate_base_score(self, error_density: float) -> float:
        """Calculate base grammar score from error density."""
        if error_density < 0.5:
            return 8.5
        elif error_density < 1.0:
            return 7.5
        elif error_density < 2.0:
            return 6.5
        elif error_density < 4.0:
            return 5.5
        elif error_density < 6.0:
            return 4.5
        else:
            return 3.5

    def get_feedback(self, result: GrammarResult) -> List[str]:
        """Generate grammar improvement tips based on the analysis."""
        feedback = []
        error_types = {}

        for error in result.errors:
            cat = error['category']
            error_types[cat] = error_types.get(cat, 0) + 1

        if 'third_person' in error_types or 'third_person_s' in error_types:
            feedback.append(" Practice third person singular -s: 'he/she/it + verb+s' (e.g., 'he works')")

        if 'agreement' in error_types:
            feedback.append(" Work on subject-verb agreement: singular subjects need singular verbs")

        if 'double_past' in error_types:
            feedback.append(" Use base form after 'didn't' (not past tense) - e.g., 'didn't go' not 'didn't went'")

        if 'modal_infinitive' in error_types:
            feedback.append(" Don't use 'to' after modal verbs (can, should, might)")

        if 'article_vowel' in error_types:
            feedback.append(" Use 'an' before words starting with vowel sounds (an apple, an hour)")

        if 'there_is_plural' in error_types:
            feedback.append(" Use 'there are' for plural nouns (e.g., 'there are many people')")

        if 'much_plural' in error_types:
            feedback.append(" Use 'many' with countable plurals, 'much' with uncountables (e.g., 'much water', 'many books')")

        if result.complexity < 2:
            feedback.append(" Try using more complex structures: conditionals, passives, or relative clauses (which/who)")
        elif result.complexity < 4:
            feedback.append(" Good range! Add hedging language (might/could/perhaps) for a Band 7+")

        if result.error_density > 5:
            feedback.append(" Focus on basic grammar rules before attempting advanced structures")

        if not feedback:
            feedback.append(" Excellent grammar! Keep using varied structures to push to Band 8+")

        return feedback[:5]

    def analyze_sentence(self, sentence: str) -> Dict:
        """Quick analysis for a single sentence."""
        errors = self._detect_errors(sentence)

        return {
            "sentence": sentence,
            "error_count": len(errors),
            "errors": errors,
            "has_errors": len(errors) > 0,
            "suggestions": [e['explanation'] for e in errors[:3]]
        }


# ----- Factory Function -----
def create_grammar_analyzer(detect_advanced_errors: bool = True) -> SpokenGrammarAnalyzer:
    """Factory function to create a SpokenGrammarAnalyzer."""
    return SpokenGrammarAnalyzer(detect_advanced_errors)