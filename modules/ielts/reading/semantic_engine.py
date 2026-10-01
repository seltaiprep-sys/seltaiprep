"""NLP and semantic analysis for answer evaluation with enhanced synonym support and robust error handling."""

import re
import logging
from typing import List, Dict, Set, Tuple, Optional, Union
from difflib import SequenceMatcher
from collections import Counter

logger = logging.getLogger(__name__)

class SemanticAnalyzer:
    """
    Analyze semantic similarity between answers and expected responses.
    Uses synonym matching, token similarity, and sequence matching.
    """

    def __init__(self):
        self.synonyms = self._load_synonyms()
        # Stop words: we keep negation words to preserve meaning
        self.stop_words = {
            'a', 'an', 'and', 'the', 'of', 'to', 'in', 'for', 'on', 'with',
            'at', 'by', 'from', 'up', 'down', 'off', 'over', 'under'
        }
        # Negation words - these are important for meaning
        self.negation_words = {'no', 'not', 'never', 'none', 'neither', 'nor'}

    def _load_synonyms(self) -> Dict[str, Set[str]]:
        """Load comprehensive synonym dictionary."""
        return {
            # Adjectives
            'important': {'significant', 'crucial', 'vital', 'essential', 'key', 'major', 'paramount', 'critical', 'notable'},
            'large': {'big', 'great', 'substantial', 'considerable', 'massive', 'huge', 'enormous', 'vast', 'immense'},
            'small': {'little', 'minor', 'slight', 'minimal', 'tiny', 'negligible', 'insignificant', 'petite'},
            'good': {'great', 'excellent', 'superior', 'fine', 'positive', 'beneficial', 'favorable', 'desirable'},
            'bad': {'poor', 'negative', 'detrimental', 'harmful', 'adverse', 'unfavorable', 'undesirable'},
            'high': {'tall', 'elevated', 'lofty', 'top', 'upward', 'great', 'substantial'},
            'low': {'small', 'minor', 'short', 'diminished', 'reduced', 'inferior', 'decreased'},
            'new': {'recent', 'modern', 'novel', 'fresh', 'contemporary', 'latest', 'current'},
            'old': {'ancient', 'historic', 'antique', 'outdated', 'former', 'previous', 'elderly'},
            'fast': {'rapid', 'swift', 'quick', 'speedy', 'hasty', 'brisk', 'express'},
            'slow': {'gradual', 'leisurely', 'steady', 'moderate', 'gentle', 'sluggish'},

            # Verbs
            'increase': {'rise', 'grow', 'expand', 'climb', 'surge', 'escalate', 'magnify', 'amplify', 'swell'},
            'decrease': {'decline', 'fall', 'drop', 'reduce', 'diminish', 'shrink', 'lessen', 'curtail', 'cut'},
            'show': {'demonstrate', 'indicate', 'reveal', 'display', 'exhibit', 'illustrate', 'portray', 'depict'},
            'cause': {'lead to', 'result in', 'bring about', 'generate', 'produce', 'induce', 'trigger', 'elicit'},
            'effect': {'impact', 'influence', 'consequence', 'outcome', 'result', 'ramification', 'repercussion'},
            'change': {'alter', 'modify', 'transform', 'adjust', 'vary', 'evolve', 'convert', 'revise'},
            'develop': {'evolve', 'advance', 'progress', 'grow', 'mature', 'proceed', 'unfold', 'elaborate'},
            'achieve': {'accomplish', 'attain', 'gain', 'reach', 'score', 'secure', 'fulfill', 'realize'},
            'analyze': {'examine', 'investigate', 'scrutinize', 'study', 'explore', 'inspect', 'probe', 'evaluate'},
            'compare': {'contrast', 'differentiate', 'distinguish', 'relate', 'parallel', 'correlate'},
            'emphasize': {'highlight', 'stress', 'accentuate', 'underscore', 'focus', 'spotlight'},

            # Nouns
            'reason': {'cause', 'justification', 'explanation', 'basis', 'rationale', 'motive', 'ground'},
            'result': {'outcome', 'consequence', 'effect', 'product', 'corollary', 'byproduct', 'outgrowth'},
            'problem': {'issue', 'obstacle', 'challenge', 'difficulty', 'dilemma', 'predicament', 'setback'},
            'solution': {'answer', 'resolution', 'remedy', 'cure', 'panacea', 'quick fix', 'way out'},
            'idea': {'notion', 'concept', 'thought', 'belief', 'conception', 'impression', 'hypothesis'},
            'method': {'approach', 'technique', 'procedure', 'process', 'system', 'strategy', 'tactic'},
            'advantage': {'benefit', 'asset', 'strength', 'profit', 'gain', 'upper hand', 'edge', 'breakthrough'},
            'disadvantage': {'drawback', 'weakness', 'deficit', 'handicap', 'liability', 'shortcoming', 'pitfall'},
            'growth': {'expansion', 'development', 'progress', 'advancement', 'escalation', 'boom', 'prosperity'},
            'value': {'worth', 'merit', 'importance', 'significance', 'usefulness', 'benefit'},
        }

    def calculate_similarity(self, answer: Optional[str], expected: Optional[str]) -> float:
        """
        Calculate semantic similarity between answer and expected response.
        Returns a float between 0.0 and 1.0.
        """
        if not answer or not expected:
            return 0.0

        # Handle None or empty strings
        if not answer.strip() or not expected.strip():
            return 0.0

        # Normalize
        answer_norm = self._normalize(answer)
        expected_norm = self._normalize(expected)

        if not answer_norm or not expected_norm:
            return 0.0

        # Exact match after normalization
        if answer_norm == expected_norm:
            return 1.0

        # Token-based similarity
        answer_tokens = set(answer_norm.split())
        expected_tokens = set(expected_norm.split())

        # Jaccard similarity (token overlap)
        intersection = len(answer_tokens & expected_tokens)
        union = len(answer_tokens | expected_tokens)
        jaccard = intersection / union if union > 0 else 0.0

        # Synonym match score
        synonym_matches = 0
        for a_token in answer_tokens:
            for e_token in expected_tokens:
                if self._are_synonyms(a_token, e_token):
                    synonym_matches += 1
                    break
        # Normalize synonym score by expected length
        expected_len = len(expected_tokens) or 1
        synonym_score = min(1.0, synonym_matches / expected_len)

        # Sequence similarity (character-level)
        seq_score = SequenceMatcher(None, answer_norm, expected_norm).ratio()

        # Combine scores
        final = (jaccard * 0.35) + (seq_score * 0.35) + (synonym_score * 0.30)
        return min(1.0, final)

    def _normalize(self, text: str) -> str:
        """Normalize text: lowercase, remove punctuation, strip extra spaces, keep negation words."""
        if not text:
            return ""

        # Lowercase
        text = text.lower()

        # Remove punctuation except apostrophes (to keep contractions)
        text = re.sub(r'[^\w\s\']', ' ', text)

        # Remove extra whitespace
        text = ' '.join(text.split())

        # Keep negation words but remove stop words
        words = []
        for w in text.split():
            if w in self.negation_words:
                words.append(w) # Keep negation
            elif w not in self.stop_words and len(w) > 2:
                words.append(w)

        return ' '.join(words)

    def _are_synonyms(self, word1: str, word2: str) -> bool:
        """Check if two words are synonyms (case-insensitive)."""
        if not word1 or not word2:
            return False
        w1 = word1.lower().strip()
        w2 = word2.lower().strip()
        if w1 == w2:
            return True
        # Check direct synonyms
        for key, syns in self.synonyms.items():
            key_low = key.lower()
            if (w1 == key_low or w1 in syns) and (w2 == key_low or w2 in syns):
                return True
        # Check compound phrases (e.g., "lead to" and "result in")
        if ' ' in w1 or ' ' in w2:
            # For phrases, we can just check if they share significant words
            w1_parts = set(w1.split())
            w2_parts = set(w2.split())
            common = w1_parts & w2_parts
            if len(common) >= min(len(w1_parts), len(w2_parts)) * 0.5:
                return True
        return False

    def extract_key_concepts(self, text: str) -> List[str]:
        """Extract key concepts (main keywords) from text."""
        if not text:
            return []
        norm = self._normalize(text)
        words = [w for w in norm.split() if len(w) > 3]
        freq = Counter(words)
        # Return up to 5 most common
        return [word for word, _ in freq.most_common(5)]

    def check_paraphrase(self, answer: str, expected: str, threshold: float = 0.6) -> bool:
        """Check if answer is a valid paraphrase of expected response."""
        similarity = self.calculate_similarity(answer, expected)
        return similarity >= threshold

    def extract_numeric_answers(self, text: str) -> List[float]:
        """Extract all numeric values from text."""
        if not text:
            return []
        matches = re.findall(r'[-+]?\d*\.?\d+', text)
        return [float(m) for m in matches]

    def compare_numeric(self, answer: Union[float, int, str, None],
                        expected: Union[float, int, str, None],
                        relative_tolerance: float = 0.1,
                        absolute_tolerance: float = 0.01) -> bool:
        """
        Compare numeric values with tolerance.
        - relative_tolerance: allowed relative difference (e.g., 0.1 = 10%)
        - absolute_tolerance: allowed absolute difference (for values close to zero)
        """
        try:
            ans = float(answer) if answer is not None else None
            exp = float(expected) if expected is not None else None
        except (ValueError, TypeError):
            return False

        if ans is None or exp is None:
            return False

        if exp == 0:
            return abs(ans) <= absolute_tolerance

        # Relative difference
        rel_diff = abs(ans - exp) / abs(exp)
        abs_diff = abs(ans - exp)

        # Allow if either relative or absolute tolerance is met
        return rel_diff <= relative_tolerance or abs_diff <= absolute_tolerance

    def find_keywords_in_passage(self, passage: str, expected_answer: str) -> List[str]:
        """
        Find keywords from expected answer that appear in the passage.
        Useful for verifying answer context.
        """
        if not passage or not expected_answer:
            return []

        passage_clean = self._normalize(passage)
        exp_clean = self._normalize(expected_answer)
        exp_words = set(exp_clean.split())

        found = []
        for word in exp_words:
            if word in passage_clean:
                found.append(word)
        return found

    def passage_context_match(self, passage: str, answer: str, expected: str) -> float:
        """
        Check if both answer and expected are supported by passage context.
        Returns a score based on keyword overlap.
        """
        answer_keywords = self.extract_key_concepts(answer)
        expected_keywords = self.extract_key_concepts(expected)

        if not answer_keywords or not expected_keywords:
            return 0.0

        passage_keywords = self.extract_key_concepts(passage)

        # Count how many from expected also appear in passage
        exp_in_passage = sum(1 for k in expected_keywords if k in passage_keywords)

        # Count how many from answer also appear in passage
        ans_in_passage = sum(1 for k in answer_keywords if k in passage_keywords)

        # Combine: we want both to be supported
        if exp_in_passage == 0 and ans_in_passage == 0:
            return 0.0

        # Weighted: expected accuracy is more important
        score = (0.6 * exp_in_passage / max(1, len(expected_keywords))) + \
                (0.4 * ans_in_passage / max(1, len(answer_keywords)))
        return min(1.0, score)


# Convenience function
def evaluate_answer_semantic(answer: str, expected: str, passage: Optional[str] = None) -> Dict:
    """
    Evaluate an answer semantically.
    Returns a dict with similarity score, paraphrase flag, and context match.
    """
    analyzer = SemanticAnalyzer()
    sim = analyzer.calculate_similarity(answer, expected)
    is_paraphrase = analyzer.check_paraphrase(answer, expected)
    result = {
        'similarity': sim,
        'is_paraphrase': is_paraphrase,
        'is_correct': sim >= 0.6, # threshold for correct
    }
    if passage:
        result['context_match'] = analyzer.passage_context_match(passage, answer, expected)
    return result