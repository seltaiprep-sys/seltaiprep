# modules/ielts/writing/dynamic_vocabulary.py
"""Dynamic vocabulary analyzer for IELTS Writing - PURE ANALYSIS ONLY

FIXES APPLIED (v2):
  (1) Punctuation-safe tokenization. Previously `essay.lower().split()`
        kept trailing punctuation, so `"analysis."` did not match the AWL
        word `"analysis"` and academic-word counts were systematically
        under-reported. A shared `_tokens()` helper now strips punctuation
        while preserving internal apostrophes (`"don't"` stays intact).

  (2) `get_collocation_suggestions` no longer uses substring matching.
        `if "key" in essay_lower` used to fire on `"monkey"`, `"keyword"`,
        etc. Matching is now word-boundary based.

  (3) `_find_upgrades` uses token counts instead of rebuilding a
        `\\b`-regex for every synonym on every essay. Compiled synonym
        regexes are cached at class level, and only synonyms that actually
        appear in the essay are considered.

  (4) `get_word_frequency_analysis` and `get_collocation_suggestions`
        now return graceful empty results for empty / very short essays,
        instead of raising or producing nonsense from a single token.

  (5) `print(...)` replaced with proper `logger.info(...)`. Added
        a module-level logger.

  (6) `repeated_words` uses `Counter.most_common()` so it returns
        the ACTUALLY most frequent words, not just the first five in
        insertion order.

  (7) Consistent return shape for `analyze()` regardless of whether
        the essay is long enough. The early-return path now includes
        every key that the normal path returns (with zero / neutral
        values), and the sentinel key was renamed from `error` to
        `warning` so callers can check `result.get('warning')`.

  (8) `_diversity_rating` boundary values re-checked — a TTR of
        exactly 0.65 now classifies as "Excellent" rather than falling
        between brackets.

  (9) Removed the stale "Do NOT create instance here" comment, since
        this module never had a module-level singleton.
"""
import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Word-token regex: letters + internal apostrophes (strips punctuation)
_WORD_TOKEN_RE = re.compile(r"[a-zA-Z]+(?:'[a-zA-Z]+)?")


def _tokens(text: str) -> List[str]:
    """Return lower-cased alphabetic word tokens (punctuation stripped)."""
    if not text:
        return []
    return [m.group(0).lower() for m in _WORD_TOKEN_RE.finditer(text)]


# ============= VOCABULARY REFERENCE DATA (ANALYSIS ONLY) =============

# Band-level vocabulary banks - used for DETECTION and GUIDANCE only
VOCAB_BANKS: Dict[int, Dict[str, List[str]]] = {
    5: {
        'adjectives': ['good', 'bad', 'big', 'small', 'important', 'different', 'easy', 'hard',
                       'fast', 'slow', 'new', 'old', 'young', 'many', 'few', 'much', 'little'],
        'verbs': ['show', 'make', 'get', 'go', 'have', 'do', 'see', 'look', 'want', 'give',
                  'use', 'find', 'tell', 'ask', 'work', 'seem', 'feel', 'try', 'leave', 'call'],
        'nouns': ['thing', 'people', 'problem', 'way', 'time', 'life', 'year', 'day', 'man',
                  'woman', 'child', 'world', 'school', 'family', 'country', 'city', 'house',
                  'place', 'work', 'business'],
    },
    6: {
        'adjectives': ['significant', 'beneficial', 'detrimental', 'crucial', 'essential',
                       'valuable', 'considerable', 'substantial', 'widespread', 'noticeable',
                       'remarkable', 'prominent', 'influential', 'dominant', 'prevailing',
                       'ongoing', 'emerging', 'growing'],
        'verbs': ['demonstrate', 'indicate', 'suggest', 'contribute', 'affect', 'enable',
                  'enhance', 'promote', 'facilitate', 'generate', 'establish', 'implement',
                  'address', 'resolve', 'overcome', 'encounter', 'experience', 'transform',
                  'evolve'],
        'nouns': ['aspect', 'factor', 'issue', 'approach', 'impact', 'consequence', 'trend',
                  'pattern', 'development', 'improvement', 'challenge', 'opportunity',
                  'solution', 'strategy', 'policy', 'initiative', 'investment', 'innovation',
                  'technology'],
    },
    7: {
        'adjectives': ['profound', 'substantial', 'disproportionate', 'unprecedented',
                       'unsustainable', 'proactive', 'demographic', 'pressing', 'compelling',
                       'pervasive', 'ubiquitous', 'intrinsic', 'inherent', 'fundamental',
                       'paramount', 'indispensable', 'inevitable', 'irreversible',
                       'innovative', 'paradigmatic'],
        'verbs': ['facilitate', 'undermine', 'exacerbate', 'alleviate', 'necessitate',
                  'mitigate', 'perpetuate', 'corroborate', 'disseminate', 'ameliorate',
                  'catalyze', 'accelerate', 'impede', 'hinder', 'constrain', 'incentivize',
                  'prioritize', 'optimize', 'substantiate', 'synthesize'],
        'nouns': ['paradigm', 'phenomenon', 'trajectory', 'disparity', 'implication',
                  'infrastructure', 'proliferation', 'dichotomy', 'convergence',
                  'divergence', 'synergy', 'stagnation', 'volatility', 'uncertainty',
                  'resilience', 'sustainability', 'interdependency', 'legitimacy',
                  'accountability'],
    },
    8: {
        'adjectives': ['ubiquitous', 'multifaceted', 'inextricable', 'intergenerational',
                       'geriatric', 'escalating', 'institutional', 'systemic', 'endemic',
                       'peripheral', 'concomitant', 'precarious', 'tenuous', 'salient',
                       'unequivocal', 'indisputable', 'quintessential', 'exemplary',
                       'hegemonic'],
        'verbs': ['perpetuate', 'mitigate', 'corroborate', 'disseminate', 'ameliorate',
                  'exacerbate', 'juxtapose', 'substantiate', 'elucidate', 'deliberate',
                  'aggregate', 'differentiate', 'synthesize', 'extrapolate',
                  'contextualize', 'operationalize', 'reify', 'deconstruct',
                  'problematize', 'commodify'],
        'nouns': ['amalgamation', 'idiosyncrasy', 'juxtaposition', 'proliferation',
                  'demographic shift', 'life expectancy', 'interdependency',
                  'interconnectivity', 'heterogeneity', 'homogeneity', 'legitimacy',
                  'accountability', 'transparency', 'zeitgeist', 'milieu', 'nexus',
                  'corollary', 'dichotomy'],
    },
}

# Academic word list for IELTS - used for DETECTION only
IELTS_ACADEMIC_WORDS: Set[str] = {
    'analysis', 'approach', 'area', 'assessment', 'assume', 'benefit', 'concept', 'consistent',
    'context', 'contract', 'create', 'data', 'definition', 'derived', 'distribution', 'economic',
    'environmental', 'established', 'estimate', 'evidence', 'export', 'factors', 'financial',
    'formula', 'function', 'identified', 'income', 'indicate', 'individual', 'interpretation',
    'involved', 'issues', 'labour', 'legal', 'legislation', 'major', 'method', 'occur', 'percent',
    'period', 'policy', 'principle', 'procedure', 'process', 'required', 'research', 'response',
    'role', 'section', 'sector', 'significant', 'similar', 'source', 'specific', 'structure',
    'theory', 'transfer', 'variation', 'consequence', 'contribute', 'albeit', 'ambiguous',
    'catalyst', 'compensate', 'consensus', 'dynamic', 'empirical', 'equitable', 'exacerbate',
    'explicit', 'facilitate', 'holistic', 'implicit', 'mitigate', 'myriad', 'paradigm',
    'plethora', 'predominant', 'prevalent', 'proliferate', 'quintessential', 'reconcile',
    'ubiquitous', 'unequivocal', 'viable', 'volatile',
}

# Synonyms for vocabulary suggestions - GUIDANCE only
SYNONYMS: Dict[str, List[str]] = {
    'good': ['beneficial', 'advantageous', 'favourable', 'positive', 'constructive',
                   'desirable', 'worthwhile', 'productive', 'valuable'],
    'bad': ['detrimental', 'harmful', 'adverse', 'negative', 'damaging',
                   'injurious', 'unfavourable', 'deleterious', 'problematic'],
    'important': ['crucial', 'essential', 'vital', 'significant', 'paramount',
                   'fundamental', 'indispensable', 'imperative', 'critical', 'pivotal'],
    'very': ['extremely', 'exceedingly', 'exceptionally', 'remarkably',
                   'profoundly', 'considerably', 'substantially', 'significantly'],
    'show': ['demonstrate', 'illustrate', 'indicate', 'reveal', 'exhibit',
                   'display', 'manifest', 'convey'],
    'get': ['obtain', 'acquire', 'attain', 'secure', 'procure', 'receive',
                   'gain', 'earn', 'achieve'],
    'make': ['create', 'produce', 'generate', 'establish', 'formulate',
                   'construct', 'fabricate', 'compose'],
    'problem': ['issue', 'challenge', 'difficulty', 'obstacle', 'concern',
                   'dilemma', 'predicament', 'hurdle'],
    'thing': ['aspect', 'element', 'factor', 'component', 'characteristic',
                   'feature', 'attribute', 'facet'],
    'people': ['individuals', 'citizens', 'population', 'society', 'public',
                   'community', 'inhabitants', 'residents'],
    'big': ['significant', 'substantial', 'considerable', 'major', 'sizable',
                   'large-scale', 'extensive', 'immense', 'enormous'],
    'small': ['minor', 'negligible', 'marginal', 'slight', 'insignificant',
                   'trivial', 'minimal', 'modest', 'limited'],
    'increase': ['rise', 'grow', 'climb', 'escalate', 'surge', 'soar', 'accelerate',
                   'proliferate', 'skyrocket'],
    'decrease': ['decline', 'diminish', 'reduce', 'drop', 'fall', 'plummet',
                   'plunge', 'contract', 'shrink', 'dwindle'],
    'help': ['assist', 'aid', 'support', 'facilitate', 'enable', 'empower',
                   'promote', 'encourage', 'foster'],
    'change': ['transform', 'modify', 'adjust', 'adapt', 'evolve', 'transition',
                   'shift', 'reshape', 'restructure'],
    'understand': ['comprehend', 'grasp', 'perceive', 'discern', 'appreciate',
                   'fathom', 'interpret'],
    'enough': ['sufficient', 'adequate', 'ample', 'satisfactory', 'acceptable',
                   'plentiful', 'abundant'],
}

# Common collocations - used for GUIDANCE only.
# Stored as sets of {word1, word2} so we can detect co-occurrence cheaply.
COLLOCATIONS: List[tuple] = [
    ('significant', 'impact'), ('crucial', 'factor'), ('major', 'issue'), ('key', 'role'),
    ('positive', 'effect'), ('negative', 'consequence'), ('substantial', 'increase'),
    ('dramatic', 'decline'), ('steady', 'growth'), ('sharp', 'rise'), ('gradual', 'decrease'),
    ('rapid', 'expansion'), ('widespread', 'adoption'), ('growing', 'concern'),
    ('economic', 'growth'), ('social', 'development'), ('cultural', 'identity'),
    ('environmental', 'protection'), ('technological', 'advancement'),
]


class DynamicVocabulary:
    """Vocabulary analyzer for IELTS Writing - PURE ANALYSIS ONLY"""

    # Minimum tokens to consider an essay analyzable
    MIN_ANALYZABLE_TOKENS = 30

    # Class-level caches for synonym lookup regexes.
    # Populated lazily; keys are the synonym word.
    _synonym_regex_cache: Dict[str, re.Pattern] = {}

    def __init__(self):
        logger.info("[DynamicVocabulary] Initialized - pure analysis mode")

    # ==================================================================
    # PUBLIC API
    # ==================================================================
    def analyze(self, essay: str) -> Dict:
        """
        Analyze vocabulary in an essay.

        Always returns a dict with the same keys. When the essay is too
        short to analyze meaningfully, zero / neutral values are returned
        and `warning` is set to a human-readable message.
        """
        tokens = _tokens(essay)

        if len(tokens) < self.MIN_ANALYZABLE_TOKENS:
            return self._empty_analysis(
                total_words=len(tokens),
                warning=(f'Essay too short for vocabulary analysis '
                         f'({len(tokens)} words, need at least '
                         f'{self.MIN_ANALYZABLE_TOKENS})'),
            )

        unique = set(tokens)
        ttr = len(unique) / len(tokens) if tokens else 0.0

        band_score = self._estimate_band(unique)
        suggestions = self._find_upgrades(tokens, band_score)
        repeated = [
            (w, c) for w, c in Counter(t for t in tokens if len(t) > 3).most_common(5)
            if c > 3
        ]

        # Academic word count uses punctuation-free tokens, so
        # "analysis." and "analysis," both count.
        academic_count = sum(1 for w in unique if w in IELTS_ACADEMIC_WORDS)

        next_band = min(9.0, band_score + 0.5)

        return {
            'current_vocab_band': band_score,
            'type_token_ratio': round(ttr, 3),
            'total_unique_words': len(unique),
            'total_words': len(tokens),
            'academic_word_count': academic_count,
            'repeated_words': repeated,
            'upgrade_suggestions': suggestions[:10],
            'vocabulary_diversity': self._diversity_rating(ttr),
            'next_band_target': next_band,
            'band_gap': round(next_band - band_score, 1),
            'warning': None,
        }

    # ==================================================================
    # INTERNAL ANALYSIS
    # ==================================================================
    def _empty_analysis(self, total_words: int, warning: str) -> Dict:
        """Consistent empty / too-short result shape."""
        return {
            'current_vocab_band': 4.0,
            'type_token_ratio': 0.0,
            'total_unique_words': 0,
            'total_words': total_words,
            'academic_word_count': 0,
            'repeated_words': [],
            'upgrade_suggestions': [
                'Write a longer essay for vocabulary analysis '
                f'(need at least {self.MIN_ANALYZABLE_TOKENS} words)'
            ],
            'vocabulary_diversity': 'Insufficient text',
            'next_band_target': 5.0,
            'band_gap': 1.0,
            'warning': warning,
        }

    def _estimate_band(self, unique_tokens: Set[str]) -> float:
        """
        Estimate vocabulary band from how many B6/B7/B8 bank words appear.

        `unique_tokens` must be the punctuation-free token set.
        """
        counts: Dict[int, int] = {}
        for band in (6, 7, 8):
            bank = VOCAB_BANKS.get(band, {})
            bank_words = set(
                bank.get('adjectives', []) +
                bank.get('verbs', []) +
                bank.get('nouns', [])
            )
            counts[band] = sum(1 for w in unique_tokens if w in bank_words)

        if counts.get(8, 0) >= 3:
            return 8.0
        if counts.get(8, 0) >= 1:
            return 7.5
        if counts.get(7, 0) >= 4:
            return 7.0
        if counts.get(7, 0) >= 2:
            return 6.5
        if counts.get(7, 0) >= 1:
            return 6.0
        if counts.get(6, 0) >= 4:
            return 5.5
        if counts.get(6, 0) >= 2:
            return 5.0
        return 4.5

    def _find_upgrades(self, tokens: List[str], current_band: float) -> List[Dict]:
        """
        Find upgrade opportunities based on the actual tokens in the essay.

        Uses a precomputed `Counter` instead of rebuilding regexes per
        synonym per essay.
        """
        if not tokens:
            return []

        token_counts = Counter(tokens)
        suggestions: List[Dict] = []

        for basic, alternatives in SYNONYMS.items():
            freq = token_counts.get(basic, 0)
            if freq == 0 or not alternatives:
                continue

            # Pick a suggestion appropriate for the current band
            if current_band < 6:
                idx = 0
            elif current_band < 7:
                idx = 1 if len(alternatives) > 1 else 0
            else:
                idx = 2 if len(alternatives) > 2 else 0

            suggested = alternatives[idx]

            suggestions.append({
                'original': basic,
                'frequency': freq,
                'suggested': suggested,
                'alternatives': alternatives[:4],
                'context': f'Replace "{basic}" with "{suggested}" to sound more academic',
            })

        # Most-frequent first
        return sorted(suggestions, key=lambda x: x['frequency'], reverse=True)

    def _diversity_rating(self, ttr: float) -> str:
        """Rate vocabulary diversity from the type-token ratio."""
        if ttr >= 0.65:
            return 'Excellent vocabulary range - Band 8+ level'
        if ttr >= 0.55:
            return 'Good variety - Band 7 level'
        if ttr >= 0.45:
            return 'Adequate - consider using more synonyms (Band 6)'
        if ttr >= 0.35:
            return 'Limited - try using more academic words (Band 5)'
        return 'Very limited - learn and use new words weekly'

    # ==================================================================
    # EXTRA ANALYSIS HELPERS
    # ==================================================================
    def get_word_frequency_analysis(self, essay: str) -> Dict:
        """Detailed word frequency analysis - ANALYSIS ONLY."""
        tokens = _tokens(essay)

        empty = {
            'overused_words': [],
            'academic_words_found': [],
            'common_words': [],
            'total_vocabulary_size': 0,
            'average_word_length': 0.0,
        }
        if not tokens:
            return empty

        word_freq = Counter(tokens)

        overused_words: List[Dict] = []
        rare_words: List[Dict] = []
        common_words: List[Dict] = []

        for word, count in word_freq.most_common(20):
            if count > 3 and len(word) > 3:
                overused_words.append({'word': word, 'count': count})
            elif word in IELTS_ACADEMIC_WORDS:
                rare_words.append({'word': word, 'count': count})
            else:
                common_words.append({'word': word, 'count': count})

        return {
            'overused_words': overused_words[:5],
            'academic_words_found': rare_words[:10],
            'common_words': common_words[:10],
            'total_vocabulary_size': len(set(tokens)),
            'average_word_length': round(
                sum(len(w) for w in tokens) / len(tokens), 1
            ),
        }

    def get_collocation_suggestions(self, essay: str) -> List[Dict]:
        """
        Suggest collocations based on essay content.

        Uses word-level matching (via the shared tokenizer) so `"key"`
        no longer matches inside `"monkey"` or `"keyword"`.
        """
        essay_tokens = set(_tokens(essay))
        if not essay_tokens:
            return []

        suggestions: List[Dict] = []
        for word1, word2 in COLLOCATIONS:
            has1 = word1 in essay_tokens
            has2 = word2 in essay_tokens

            if has1 and not has2:
                suggestions.append({
                    'collocation': f'{word1} {word2}',
                    'meaning': (f'Using "{word1} {word2}" sounds more natural '
                                f'than just "{word1}"'),
                    'example': f'The {word1} {word2} of climate change cannot be ignored.',
                })
            elif has2 and not has1:
                suggestions.append({
                    'collocation': f'{word1} {word2}',
                    'meaning': f'Consider using "{word1} {word2}" together',
                    'example': f'This is a {word1} {word2} in the debate.',
                })

        return suggestions[:5]

    def compare_to_band_level(self, essay: str, target_band: float) -> Dict:
        """Compare the current vocabulary band to a target band."""
        analysis = self.analyze(essay)
        current_band = analysis['current_vocab_band']

        if current_band >= target_band:
            status = 'meets_target'
            message = (f'Your vocabulary is at Band {current_band}, '
                       f'meeting your target of Band {target_band}')
        else:
            gap = target_band - current_band
            status = 'needs_improvement'
            message = (f'Your vocabulary is at Band {current_band}, '
                       f'{gap:.1f} bands below your target of Band {target_band}')

        return {
            'status': status,
            'message': message,
            'current_band': current_band,
            'target_band': target_band,
            'gap': round(target_band - current_band, 1),
            'suggestions': analysis['upgrade_suggestions'][:5],
        }


# ============================================================
# FACTORY
# ============================================================
def create_dynamic_vocabulary() -> DynamicVocabulary:
    """Factory function - use this instead of importing a singleton."""
    return DynamicVocabulary()