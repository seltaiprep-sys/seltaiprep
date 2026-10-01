# modules/ielts/writing/coherence_map.py
"""Generate visual flow map of essay coherence and cohesion - PURE ANALYSIS ONLY

FIXES APPLIED (v5):
  (1) CRITICAL — Emoji cleanup accidentally emptied the
        `topic_indicator` (was ""/""). Both branches became
        empty strings, breaking the flow-map legend. Now uses
        ASCII-safe `[Y]`/`[N]` markers.

FIXES APPLIED (v4):
  (2) Removed unused `Optional` import (cleanup).

FIXES APPLIED (v3):
  (3) CRITICAL — Per-boundary transition scores. The v2 code returned
        a CUMULATIVE sum from `_check_paragraph_transitions()` and then
        compared it against running index `i+1` inside the flow map. That
        meant a "good" first boundary could cause a LATER boundary to be
        mislabeled, and vice versa. Now `_check_paragraph_transitions()`
        returns a LIST of per-boundary scores (0.0 / 0.5 / 1.0), and the
        flow map uses `boundary_scores[i]` directly.

FIXES APPLIED (v2):
  (4) Removed the duplicate LINKING_WORDS table (imports from scoring.py).
  (5) Word-boundary matching for linking-word detection.
  (6) Token-based pronoun detection.
  (7) Transition score returned as float (not int-truncated).
  (8) ASCII flow map padded to fixed inner width.
  (9) print() → logger.info().
  (10) Consistent shape via _empty_result().
"""
import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# SHARED LINKING WORDS
# ─────────────────────────────────────────────────────────────
try:
    from .scoring import AdvancedCoherenceAnalyzer as _ACA
    LINKING_WORDS: Dict[str, List[str]] = dict(_ACA.TRANSITION_WORDS)
    _LINKING_SOURCE = "scoring.AdvancedCoherenceAnalyzer.TRANSITION_WORDS"
except Exception: # pragma: no cover — defensive only
    LINKING_WORDS = {
        'addition': ['furthermore', 'moreover', 'in addition', 'additionally', 'also'],
        'contrast': ['however', 'nevertheless', 'on the other hand', 'conversely',
                       'although', 'whereas'],
        'cause': ['therefore', 'consequently', 'as a result', 'thus', 'hence'],
        'sequence': ['firstly', 'secondly', 'thirdly', 'finally', 'then', 'next'],
        'conclusion': ['in conclusion', 'to conclude', 'overall', 'in summary'],
    }
    _LINKING_SOURCE = "local fallback"


# Words that signal a logical link to the previous paragraph
PARAGRAPH_TRANSITION_WORDS = [
    'however', 'moreover', 'furthermore', 'in contrast', 'similarly',
    'consequently', 'therefore', 'nevertheless', 'nonetheless', 'additionally',
]

# Pronouns that signal a text-internal reference (used for partial credit)
REFERENCE_PRONOUNS = {'it', 'they', 'this', 'these', 'those', 'such'}

# Word-token regex
_WORD_TOKEN_RE = re.compile(r"[a-zA-Z]+(?:'[a-zA-Z]+)?")

# Sentence-splitting regex
_SENTENCE_SPLIT_RE = re.compile(r'[.!?]+')

# v5: ASCII-safe indicators (were ""/"" before emoji cleanup)
TOPIC_YES = "[Y]"
TOPIC_NO = "[N]"


def _tokens(text: str) -> List[str]:
    """Return lower-cased alphabetic word tokens (punctuation stripped)."""
    if not text:
        return []
    return [m.group(0).lower() for m in _WORD_TOKEN_RE.finditer(text)]


def _phrase_regex(phrase: str) -> re.Pattern:
    """Word-boundary, whitespace-tolerant, case-insensitive regex for a phrase."""
    parts = [re.escape(p) for p in phrase.split()]
    return re.compile(r'\b' + r'\s+'.join(parts) + r'\b', re.IGNORECASE)


class CoherenceFlowMap:
    """Analyze and visualize essay coherence and flow - ANALYSIS ONLY, NO GENERATION"""

    BOX_INNER_WIDTH = 65

    def __init__(self):
        logger.info(
            f"[CoherenceFlowMap] Initialized - pure analysis mode "
            f"(linking words source: {_LINKING_SOURCE})"
        )

    # ==================================================================
    # PUBLIC API
    # ==================================================================
    def analyze(self, essay: str) -> Dict:
        """Create a coherence flow map of the essay."""
        if not essay or len(essay.strip()) < 50:
            return self._empty_result('Essay too short for coherence analysis')

        paragraphs = [p.strip() for p in essay.split('\n\n') if p.strip()]
        if not paragraphs:
            paragraphs = [essay]

        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(essay) if s.strip()]

        # Per-paragraph analysis
        paragraph_analysis: List[Dict] = []
        for i, para in enumerate(paragraphs):
            para_sentences = [
                s.strip() for s in _SENTENCE_SPLIT_RE.split(para) if s.strip()
            ]
            linking_count = self._count_linking_words_in_paragraph(para)

            topic_sentence = para_sentences[0] if para_sentences else ''
            has_topic_sentence = (
                len(topic_sentence.split()) > 5 and len(para_sentences) > 1
            )

            paragraph_analysis.append({
                'number': i + 1,
                'sentences': len(para_sentences),
                'linking_words': linking_count,
                'topic_sentence': topic_sentence[:100] + ('...' if len(topic_sentence) > 100 else ''),
                'has_topic_sentence': has_topic_sentence,
                'coherence_score': self._calculate_paragraph_coherence(para),
            })

        # Overall metrics
        total_linking = sum(
            sum(p['linking_words'].values()) for p in paragraph_analysis
        )
        expected_linking = max(3, len(sentences) // 3)
        linking_ratio = (
            min(1.0, total_linking / expected_linking) if expected_linking > 0 else 0.5
        )

        # v3: boundary_scores is a LIST — one per boundary
        boundary_scores = self._check_paragraph_transitions(paragraphs)
        transition_total = sum(boundary_scores)

        overall_score = int(
            50 +
            (linking_ratio * 25) +
            (10 if len(paragraphs) >= 3 else 0) +
            (10 if transition_total >= 1.0 else 0) +
            (5 if transition_total >= 2.0 else 0)
        )
        overall_score = min(100, max(0, overall_score))

        flow_map = self._generate_flow_map(paragraph_analysis, boundary_scores)

        return {
            'overall_coherence_score': overall_score,
            'paragraph_count': len(paragraphs),
            'sentence_count': len(sentences),
            'linking_word_count': total_linking,
            'paragraph_analysis': paragraph_analysis,
            'has_transitions': transition_total >= 1.0,
            'transition_score': round(transition_total, 2),
            'transition_quality': self._rate_transition_quality(transition_total),
            'boundary_scores': boundary_scores,
            'flow_map': flow_map,
            'issues': self._identify_issues(
                paragraph_analysis, sentences, total_linking,
                len(paragraphs), transition_total,
            ),
            'suggestions': self._get_suggestions(
                paragraph_analysis, total_linking, transition_total, len(paragraphs),
            ),
            'warning': None,
        }

    def get_coherence_summary(self, essay: str) -> Dict:
        """Get a quick coherence summary."""
        analysis = self.analyze(essay)
        return {
            'score': analysis['overall_coherence_score'],
            'grade': self._score_to_grade(analysis['overall_coherence_score']),
            'paragraphs': analysis['paragraph_count'],
            'transitions': ('Good' if analysis['has_transitions']
                            else 'Needs improvement'),
            'main_issue': analysis['issues'][0] if analysis['issues'] else 'None',
        }

    # ==================================================================
    # LINKING-WORD COUNTING
    # ==================================================================
    @staticmethod
    def _count_linking_words_in_paragraph(paragraph: str) -> Dict[str, int]:
        if not paragraph:
            return {}
        counts: Dict[str, int] = {}
        for category, words in LINKING_WORDS.items():
            total = 0
            for w in words:
                rx = _phrase_regex(w)
                total += len(rx.findall(paragraph))
            if total > 0:
                counts[category] = total
        return counts

    # ==================================================================
    # PARAGRAPH COHERENCE
    # ==================================================================
    def _calculate_paragraph_coherence(self, paragraph: str) -> int:
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
        if len(sentences) < 2:
            return 30

        score = 50

        for i in range(len(sentences) - 1):
            next_tokens = set(_tokens(sentences[i + 1]))
            if next_tokens & REFERENCE_PRONOUNS:
                score += 5

        tokens = _tokens(paragraph)
        if tokens:
            unique_ratio = len(set(tokens)) / len(tokens)
            if unique_ratio > 0.4:
                score += 10

        short_sentences = [s for s in sentences if len(s.split()) < 5]
        if len(short_sentences) > len(sentences) * 0.3:
            score -= 15

        return max(0, min(100, score))

    # ==================================================================
    # v3 — PARAGRAPH TRANSITIONS (returns LIST, not float)
    # ==================================================================
    def _check_paragraph_transitions(self, paragraphs: List[str]) -> List[float]:
        """
        Return a per-boundary transition score list.

        boundary_scores[i] is the score for the transition from
        paragraphs[i] to paragraphs[i+1]:
            1.0 -> explicit linker at the boundary
            0.5 -> pronoun-only reference ("this/these/those/such")
            0.0 -> no link detected
        """
        if len(paragraphs) < 2:
            return []

        scores: List[float] = []
        for i in range(len(paragraphs) - 1):
            first_words = paragraphs[i].split()
            second_words = paragraphs[i + 1].split()

            first_end = first_words[-15:] if len(first_words) > 15 else first_words
            second_start = second_words[:15] if len(second_words) > 15 else second_words

            combined = ' '.join(first_end + second_start)
            combined_tokens = set(_tokens(combined))

            has_explicit = any(
                tw in combined_tokens or _phrase_regex(tw).search(combined)
                for tw in PARAGRAPH_TRANSITION_WORDS
            )
            if has_explicit:
                scores.append(1.0)
                continue

            second_start_tokens = set(_tokens(' '.join(second_start)))
            if second_start_tokens & {'this', 'these', 'those', 'such'}:
                scores.append(0.5)
                continue

            scores.append(0.0)

        return scores

    @staticmethod
    def _rate_transition_quality(score: float) -> str:
        if score >= 2.0:
            return 'excellent'
        if score >= 1.0:
            return 'good'
        if score >= 0.5:
            return 'weak'
        return 'poor'

    # ==================================================================
    # v5 — ASCII FLOW MAP (uses per-boundary scores + ASCII topic markers)
    # ==================================================================
    def _generate_flow_map(self, paragraph_analysis: List[Dict],
                            boundary_scores: List[float]) -> str:
        """Build the ASCII flow map with fixed-width box borders."""
        w = self.BOX_INNER_WIDTH
        top = "+" + "-" * w + "+"
        mid = "+" + "-" * w + "+"
        bottom = "+" + "-" * w + "+"

        lines = [top, self._box_line("ESSAY FLOW MAP", w, center=True), mid]

        if not paragraph_analysis:
            lines.append(self._box_line("No paragraphs to analyze", w, center=True))
            lines.append(bottom)
            return '\n'.join(lines)

        for i, para in enumerate(paragraph_analysis):
            coherence_level = max(0, min(10, para['coherence_score'] // 10))
            bar = "#" * coherence_level + "." * (10 - coherence_level)
            # v5: ASCII-safe topic indicator (was "" / "")
            topic_indicator = TOPIC_YES if para['has_topic_sentence'] else TOPIC_NO
            linking_count = sum(para['linking_words'].values())

            line1 = (f"Para {para['number']}: [{bar}] "
                     f"Coherence: {para['coherence_score']}%")
            line2 = (f" Sentences: {para['sentences']} | "
                     f"Topic: {topic_indicator} | Linking: {linking_count}")

            lines.append(self._box_line(line1, w))
            lines.append(self._box_line(line2, w))

            if i < len(paragraph_analysis) - 1:
                # v3: use PER-BOUNDARY score, not cumulative
                score = boundary_scores[i] if i < len(boundary_scores) else 0.0
                if score >= 1.0:
                    arrow = "v (good transition)"
                elif score >= 0.5:
                    arrow = "v (weak transition -- reference only)"
                else:
                    arrow = "v (no transition detected)"
                lines.append(self._box_line(arrow, w, center=True))

        lines.append(bottom)
        lines.append("")
        lines.append("Legend: # = strong coherence | . = weak coherence")
        lines.append(f" {TOPIC_YES} = has topic sentence | {TOPIC_NO} = missing topic sentence")

        return '\n'.join(lines)

    @staticmethod
    def _box_line(content: str, width: int, center: bool = False) -> str:
        """Return one `| ... |` line padded to exactly `width` characters."""
        if len(content) > width:
            content = content[:width - 3] + "..."
        if center:
            content = content.center(width)
        else:
            content = content.ljust(width)
        return "|" + content + "|"

    # ==================================================================
    # ISSUE IDENTIFICATION
    # ==================================================================
    def _identify_issues(self, paragraph_analysis: List[Dict],
                          sentences: List[str],
                          total_linking: int,
                          num_paragraphs: int,
                          transitions: float) -> List[str]:
        issues: List[str] = []

        if num_paragraphs < 3:
            issues.append(
                f"Essay has only {num_paragraphs} paragraph(s) -- aim for 4-5 paragraphs"
            )

        weak_paras = [p for p in paragraph_analysis if p['coherence_score'] < 50]
        if weak_paras:
            nums = ', '.join(str(p['number']) for p in weak_paras)
            issues.append(f"Paragraph(s) {nums} have weak internal coherence")

        if total_linking < 5 and len(sentences) > 15:
            issues.append(
                f"Only {total_linking} linking words found -- add more transitions"
            )

        no_topic = [p for p in paragraph_analysis if not p['has_topic_sentence']]
        if no_topic:
            nums = ', '.join(str(p['number']) for p in no_topic)
            issues.append(f"Paragraph(s) {nums} missing clear topic sentences")

        if transitions < 1.0 and num_paragraphs > 1:
            issues.append(
                "No clear transitions between paragraphs -- ideas may feel disconnected"
            )

        long_paras = [p for p in paragraph_analysis if p['sentences'] > 8]
        if long_paras:
            nums = ', '.join(str(p['number']) for p in long_paras)
            issues.append(f"Paragraph(s) {nums} are too long -- consider splitting")

        return issues[:5]

    # ==================================================================
    # SUGGESTIONS
    # ==================================================================
    def _get_suggestions(self, paragraph_analysis: List[Dict],
                          total_linking: int,
                          transitions: float,
                          num_paragraphs: int) -> List[str]:
        suggestions: List[str] = []

        if num_paragraphs < 3:
            suggestions.append(
                "[TIP] Use standard 4-5 paragraph structure: Introduction, Body (2-3), Conclusion"
            )

        if total_linking < 6:
            suggestions.append(
                "[TIP] Increase linking words: however, therefore, furthermore, "
                "in contrast, consequently"
            )
        elif total_linking > 15:
            suggestions.append(
                "[TIP] Slightly reduce linking words -- too many can sound forced"
            )

        if transitions < 1.0 and num_paragraphs > 1:
            suggestions.append(
                "[TIP] Add transition sentences: 'Building on this...', 'In contrast to...'"
            )

        no_topic = [p for p in paragraph_analysis if not p['has_topic_sentence']]
        if no_topic:
            suggestions.append(
                "[TIP] Start each body paragraph with a clear topic sentence stating the main idea"
            )

        weak_paras = [p for p in paragraph_analysis if p['coherence_score'] < 50]
        if weak_paras:
            suggestions.append(
                "[TIP] Improve paragraph coherence by keeping each paragraph focused on ONE main idea"
            )

        if not suggestions:
            suggestions.append(
                "[OK] Strong coherence! For Band 7+, use more sophisticated transitions "
                "like 'Consequently', 'Nevertheless'"
            )

        return suggestions[:5]

    # ==================================================================
    # HELPERS
    # ==================================================================
    def _empty_result(self, warning: str) -> Dict:
        return {
            'overall_coherence_score': 0,
            'paragraph_count': 0,
            'sentence_count': 0,
            'linking_word_count': 0,
            'paragraph_analysis': [],
            'has_transitions': False,
            'transition_score': 0.0,
            'transition_quality': 'insufficient',
            'boundary_scores': [],
            'flow_map': 'Essay too short for coherence analysis',
            'issues': ['Essay too short (minimum 50 characters for analysis)'],
            'suggestions': ['Write a longer essay for proper coherence analysis'],
            'warning': warning,
        }

    @staticmethod
    def _score_to_grade(score: int) -> str:
        if score >= 80:
            return "Excellent"
        if score >= 65:
            return "Good"
        if score >= 50:
            return "Adequate"
        if score >= 35:
            return "Needs Improvement"
        return "Poor"


def create_coherence_map() -> CoherenceFlowMap:
    """Factory function."""
    return CoherenceFlowMap()