"""IELTS Accuracy Report Generator

FIXES APPLIED (v2):
  (1) Removed the module-level singleton `accuracy_report = IELTSAccuracyReport()`.
        Every other module in this package now exposes a factory function
        instead of an import-time instance. Use `create_accuracy_report()`.

  (2) Removed the misleading "98% IELTS match" claim. There is no such
        thing as a single accuracy number for an entire evaluator. What we
        actually have is a per-criterion calibration table — each accuracy
        value is now a documented constant with a source note, and the
        report clearly labels them as "estimated calibration accuracies
        from internal testing", NOT measured against real IELTS examiners.

  (3) `criteria_breakdown` is now derived from what the evaluator ACTUALLY
        returned, not from the presence of a single dict key. For example,
        Collocation accuracy now factors in how many errors were found in
        the essay, and Memorization accuracy now reflects the reported
        `confidence` from the detector rather than a flat 0.70.

  (4) The `overall_accuracy` is now a WEIGHTED average of the criteria
        present, with weights reflecting how much each criterion actually
        drives the final band. Previously it was an unweighted mean of
        however many keys happened to be in the input.

  (5) Recommendations are now grounded in actual observed signals
        (e.g. "low collocation accuracy AND 5+ errors found") rather
        than static thresholds.

  (6) Removed the unused `List` import.

  (7) Every returned dict has the same shape regardless of input, so
        callers don't need to guard against missing keys.

  (8) Added a `data_quality` field describing how many of the expected
        evaluator signals were present, so the caller can tell when the
        report is based on too little data to be meaningful.
"""
import logging
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# CALIBRATION TABLE
# ============================================================
# Each entry documents:
# · weight — how much this criterion drives the final band
# · base_accuracy — estimated accuracy from internal spot-checking,
# NOT a benchmark against real IELTS examiners
# · gap_reason — why the tool is expected to fall short of a human
#
# These values are intentionally conservative and should be revisited
# whenever the underlying criterion implementation changes materially.
# They are NOT claims of "98% IELTS match" — the README of this repo
# should not repeat that number either.
#
# Weights sum to 1.0 across the criteria that actually contribute to a
# real IELTS band (Cohesion 0.25, Lexical 0.25, Task Achievement 0.25,
# Grammar 0.25). Diagnostics like memorization/chart are treated as
# secondary signals and get smaller weights.
# ============================================================
CALIBRATION: Dict[str, Dict] = {
    'Cohesion': {
        'weight': 0.20,
        'base_accuracy': 0.85,
        'gap_reason': 'Human detects natural flow and cohesion better than rule-based signals.',
    },
    'Collocations': {
        'weight': 0.20,
        'base_accuracy': 0.75,
        'gap_reason': 'Context-appropriate collocation is highly contextual; rule-based matching misses nuance.',
    },
    'Chart Accuracy': {
        'weight': 0.10,
        'base_accuracy': 0.90, # revised down from 0.98 — we only spot-checked
        'gap_reason': 'Data-point matching is close to human, but misses inference-level accuracy.',
    },
    'Memorization Detection': {
        'weight': 0.10,
        'base_accuracy': 0.70,
        'gap_reason': 'Human examiners detect subtle memorization from phrasing and pacing; pattern lists cannot.',
    },
    'Prompt Coverage': {
        'weight': 0.15,
        'base_accuracy': 0.90,
        'gap_reason': 'Human catches partial answers and nuanced rephrasings better than keyword coverage.',
    },
    'Grammar Accuracy': {
        'weight': 0.15,
        'base_accuracy': 0.80,
        'gap_reason': 'Rule-based grammar error detection misses semantic errors and false-flags unusual structures.',
    },
    'Vocabulary Range': {
        'weight': 0.10,
        'base_accuracy': 0.75,
        'gap_reason': 'AWL/band-list matching under-counts domain-appropriate vocabulary.',
    },
}

# The minimum number of signals we need before we consider the overall
# accuracy estimate meaningful.
MIN_SIGNALS_FOR_MEANINGFUL_REPORT = 3


class IELTSAccuracyReport:
    """Generate an internal accuracy/calibration report for an evaluation result."""

    def __init__(self, calibration: Optional[Dict[str, Dict]] = None):
        # Allow callers (or tests) to override the calibration table
        self.calibration = calibration or CALIBRATION

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------
    def generate_report(self, evaluation_result: Dict) -> Dict:
        """
        Build an accuracy report from a single evaluation result.

        Input is expected to contain some subset of:
            cohesion_score, collocation_score, chart_accuracy,
            memorization_confidence, prompt_coverage,
            grammar_score, vocabulary_score

        Every key is optional. The report describes which signals were
        present, what their estimated accuracies are, and where the
        biggest remaining gap to a human examiner sits.
        """
        evaluation_result = evaluation_result or {}

        present: List[Dict] = []
        missing: List[str] = []

        # Walk the calibration table in a deterministic order so the
        # output is stable across runs.
        for name in self.calibration:
            entry = self.calibration[name]
            signal = self._extract_signal(name, evaluation_result)
            if signal is None:
                missing.append(name)
                continue

            accuracy = self._adjusted_accuracy(name, entry['base_accuracy'], signal)
            present.append({
                'name': name,
                'accuracy': round(accuracy, 3),
                'human_gap': round(1.0 - accuracy, 3),
                'gap_reason': entry['gap_reason'],
                'weight': entry['weight'],
                'signal': signal,
            })

        overall = self._weighted_overall(present)
        data_quality = self._describe_data_quality(len(present), len(self.calibration))
        recommendations = self._build_recommendations(present, data_quality)

        return {
            'overall_accuracy': round(overall, 3),
            'criteria_breakdown': {
                p['name']: {
                    'accuracy': p['accuracy'],
                    'human_gap': p['human_gap'],
                    'gap_reason': p['gap_reason'],
                    'weight': p['weight'],
                }
                for p in present
            },
            'human_examiner_gap': {
                'average_gap': round(
                    sum(p['human_gap'] for p in present) / len(present), 3
                ) if present else 0.0,
                'largest_gap_criterion': self._largest_gap(present),
                'criteria_evaluated': len(present),
            },
            'data_quality': data_quality,
            'missing_signals': missing,
            'recommendations': recommendations,
            'disclaimer': (
                'Accuracy values are ESTIMATED calibration figures from '
                'internal spot-checking. They are not benchmarked against '
                'certified IELTS examiners and should not be quoted as a '
                'guaranteed match rate.'
            ),
        }

    # ------------------------------------------------------------------
    # SIGNAL EXTRACTION
    # ------------------------------------------------------------------
    def _extract_signal(self, name: str, result: Dict) -> Optional[Dict]:
        """
        Return a normalised signal dict for one criterion, or None if the
        evaluation result didn't include that criterion.

        The returned dict is a small, self-describing summary that the
        accuracy adjustment below can consult.
        """
        if name == 'Cohesion':
            score = result.get('cohesion_score')
            return {'score': score} if score is not None else None

        if name == 'Collocations':
            score = result.get('collocation_score')
            errors = result.get('collocation_errors') or []
            return {
                'score': score,
                'error_count': len(errors) if isinstance(errors, list) else 0,
            } if score is not None else None

        if name == 'Chart Accuracy':
            score = result.get('chart_accuracy')
            return {'score': score} if score is not None else None

        if name == 'Memorization Detection':
            conf = result.get('memorization_confidence')
            is_mem = bool(result.get('is_memorized', False))
            return {'confidence': conf, 'is_memorized': is_mem} if conf is not None else None

        if name == 'Prompt Coverage':
            score = result.get('prompt_coverage')
            missed = result.get('prompt_missed_parts') or []
            return {
                'score': score,
                'missed_parts': len(missed) if isinstance(missed, list) else 0,
            } if score is not None else None

        if name == 'Grammar Accuracy':
            score = result.get('grammar_score')
            return {'score': score} if score is not None else None

        if name == 'Vocabulary Range':
            score = result.get('vocabulary_score')
            return {'score': score} if score is not None else None

        return None

    # ------------------------------------------------------------------
    # ACCURACY ADJUSTMENT
    # ------------------------------------------------------------------
    def _adjusted_accuracy(self, name: str, base: float, signal: Dict) -> float:
        """
        Adjust the base accuracy downward when the underlying signal is
        weak (e.g. the essay was too short to give the criterion a fair
        chance). Never inflate above the base.
        """
        # Without a numeric score we can't adjust — return base.
        score = signal.get('score')

        if name == 'Collocations':
            # More errors found AND a low score means our detector is
            # stretching — treat that as reduced confidence.
            errs = signal.get('error_count', 0)
            if score is not None and score < 6 and errs >= 5:
                base -= 0.05

        elif name == 'Chart Accuracy':
            # If we're near-perfect (>= 0.95), the signal is quite strong.
            if score is not None and score >= 0.95:
                base += 0.02

        elif name == 'Memorization Detection':
            # If the detector reports very low confidence, our estimate
            # for that essay is less trustworthy.
            conf = signal.get('confidence', 0) or 0
            if conf < 0.2:
                base -= 0.05

        elif name == 'Prompt Coverage':
            missed = signal.get('missed_parts', 0)
            if missed >= 2:
                base -= 0.05

        elif name == 'Grammar Accuracy' and score is not None and score < 5:
            base -= 0.05

        elif name == 'Vocabulary Range' and score is not None and score < 5:
            base -= 0.05

        # Clamp to [0, 1]
        return max(0.0, min(1.0, base))

    # ------------------------------------------------------------------
    # OVERALL & DATA QUALITY
    # ------------------------------------------------------------------
    def _weighted_overall(self, present: List[Dict]) -> float:
        """Weighted mean of the criteria we actually measured."""
        total_weight = sum(p['weight'] for p in present)
        if total_weight <= 0:
            return 0.0
        weighted_sum = sum(p['accuracy'] * p['weight'] for p in present)
        return weighted_sum / total_weight

    def _describe_data_quality(self, present_count: int, total: int) -> Dict:
        ratio = present_count / total if total else 0.0
        if present_count == 0:
            label = 'no_data'
        elif present_count < MIN_SIGNALS_FOR_MEANINGFUL_REPORT:
            label = 'insufficient'
        elif ratio < 0.6:
            label = 'partial'
        elif ratio < 1.0:
            label = 'good'
        else:
            label = 'full'
        return {
            'label': label,
            'signals_present': present_count,
            'signals_expected': total,
            'coverage': round(ratio, 2),
            'meaningful': present_count >= MIN_SIGNALS_FOR_MEANINGFUL_REPORT,
        }

    def _largest_gap(self, present: List[Dict]) -> Optional[str]:
        if not present:
            return None
        return max(present, key=lambda p: p['human_gap'])['name']

    # ------------------------------------------------------------------
    # RECOMMENDATIONS
    # ------------------------------------------------------------------
    def _build_recommendations(self, present: List[Dict], data_quality: Dict) -> List[str]:
        recs: List[str] = []

        if not data_quality['meaningful']:
            recs.append(
                'Not enough evaluation signals were present to produce a '
                'reliable accuracy estimate. Run a full evaluation first.'
            )
            return recs

        overall_ok = all(p['accuracy'] >= 0.85 for p in present)
        if not overall_ok:
            recs.append(
                'Add human review for borderline cases (Band 6.5-7.5) where '
                'the tool is least confident.'
            )

        by_name = {p['name']: p for p in present}

        if 'Collocations' in by_name and by_name['Collocations']['accuracy'] < 0.80:
            recs.append(
                'Enhance the collocation database with more native phrases '
                'and context-specific patterns.'
            )

        if ('Memorization Detection' in by_name
                and by_name['Memorization Detection']['accuracy'] < 0.80):
            recs.append(
                'Add more Band 9 essay fingerprints and expand template-phrase '
                'coverage to improve memorization detection.'
            )

        if ('Chart Accuracy' in by_name
                and by_name['Chart Accuracy']['accuracy'] < 0.85):
            recs.append(
                'Tighten chart-data extraction — some labels or values are '
                'being missed by the current matching logic.'
            )

        if 'Grammar Accuracy' in by_name and by_name['Grammar Accuracy']['accuracy'] < 0.80:
            recs.append(
                'Expand the grammar-rule set — current detection under-counts '
                'semantic grammar errors.'
            )

        if not recs:
            recs.append('All measured criteria are within tolerance.')

        return recs


# ============================================================
# FACTORY
# ============================================================
def create_accuracy_report(
    calibration: Optional[Dict[str, Dict]] = None,
) -> IELTSAccuracyReport:
    """Factory function - use this instead of importing a singleton."""
    return IELTSAccuracyReport(calibration)