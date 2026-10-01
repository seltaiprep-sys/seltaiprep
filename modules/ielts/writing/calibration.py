"""IELTS Writing calibration data — real essay samples with official bands.

PURPOSE
═══════
Measure the evaluator's accuracy against REAL IELTS essays with
official band scores. Use this to:
    1. Establish a baseline MAE (mean absolute error)
    2. Tune thresholds in scoring.py / evaluator.py
    3. Detect accuracy regressions after code changes

HOW TO USE
══════════
    1. Collect 100-200 real IELTS essays with official band scores
       - Source: official IELTS practice materials, verified teachers
    2. Add them to CALIBRATION_SAMPLES below
    3. Run:
         python -m modules.ielts.writing.calibration
    4. Check the MAE. Target: < 0.5 band (examiner level)
    5. If MAE > 0.7 → adjust thresholds in scoring.py

EXPECTED GAINS
══════════════
    - 0 samples: N/A
    - 50 samples: ±0.5 MAE achievable with threshold tuning
    - 200 samples: ±0.3 MAE achievable (near examiner level)
"""
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CALIBRATION DATASET
#
# Format: (essay_text, official_band, task_type)
#
# PLACEHOLDER — replace with real essays once collected.
# Do NOT use AI-generated essays here; they defeat the purpose.
# ═══════════════════════════════════════════════════════════════════
CALIBRATION_SAMPLES: List[Tuple[str, float, str]] = [
    # ── Task 1 samples ──────────────────────────────────────────
    # (
    # "The bar chart illustrates the number of international "
    # "students enrolled in five countries between 2010 and 2020. "
    # "Overall, it is clear that...",
    # 6.5,
    # "task1",
    # ),

    # ── Task 2 samples ──────────────────────────────────────────
    # (
    # "Some people believe that university education should be "
    # "free for all students. To what extent do you agree or "
    # "disagree? In recent years...",
    # 7.0,
    # "task2",
    # ),
]


def measure_mae(evaluator) -> dict:
    """
    Run the evaluator against every calibration sample.

    Returns:
        {
            'mae': float | None,
            'count': int,
            'max_error': float,
            'median_error': float,
            'by_task': {'task1_mae': float|None, 'task2_mae': float|None},
            'samples': [{'official': float, 'predicted': float, 'error': float}]
        }
    """
    if not CALIBRATION_SAMPLES:
        logger.warning(
            "No calibration samples found. Add real IELTS essays to "
            "CALIBRATION_SAMPLES in calibration.py first."
        )
        return {
            'mae': None,
            'count': 0,
            'max_error': None,
            'median_error': None,
            'by_task': {},
            'samples': [],
        }

    errors = []
    by_task = {'task1': [], 'task2': []}
    detailed_samples = []

    for i, (essay, official_band, task_type) in enumerate(CALIBRATION_SAMPLES):
        try:
            result = evaluator.evaluate(essay, task_type, prompt="")
            predicted = result.get('overall_band', 0)
            error = abs(predicted - official_band)
            errors.append(error)
            by_task.setdefault(task_type, []).append(error)
            detailed_samples.append({
                'index': i,
                'task_type': task_type,
                'official': official_band,
                'predicted': predicted,
                'error': round(error, 2),
            })
        except Exception as e:
            logger.error(f"Calibration sample {i} failed: {e}")

    if not errors:
        return {
            'mae': None,
            'count': 0,
            'max_error': None,
            'median_error': None,
            'by_task': {},
            'samples': [],
        }

    sorted_errors = sorted(errors)
    median_error = sorted_errors[len(sorted_errors) // 2]

    return {
        'mae': round(sum(errors) / len(errors), 3),
        'count': len(errors),
        'max_error': round(max(errors), 2),
        'median_error': round(median_error, 2),
        'by_task': {
            'task1_mae': (round(sum(by_task['task1']) / len(by_task['task1']), 3)
                          if by_task.get('task1') else None),
            'task2_mae': (round(sum(by_task['task2']) / len(by_task['task2']), 3)
                          if by_task.get('task2') else None),
        },
        'samples': detailed_samples,
    }


def print_report(stats: dict) -> None:
    """Pretty-print the calibration report."""
    print("=" * 65)
    print(" IELTS Writing Evaluator — Calibration Report")
    print("=" * 65)

    if stats['count'] == 0:
        print(" No samples. Add real IELTS essays to CALIBRATION_SAMPLES")
        print("=" * 65)
        return

    print(f" Samples evaluated : {stats['count']}")
    print(f" Mean Absolute Err : {stats['mae']} band")
    print(f" Median Error : {stats['median_error']} band")
    print(f" Max Error : {stats['max_error']} band")
    print()
    print(f" Task 1 MAE : {stats['by_task'].get('task1_mae', 'N/A')}")
    print(f" Task 2 MAE : {stats['by_task'].get('task2_mae', 'N/A')}")
    print()
    print(" Accuracy Rating:")
    mae = stats['mae']
    if mae < 0.35:
        print(" EXCELLENT — Near examiner level")
    elif mae < 0.55:
        print(" GOOD — Industry standard (GPT-4 level)")
    elif mae < 0.75:
        print(" ACCEPTABLE — Practice tool level")
    else:
        print(" POOR — Needs recalibration")
    print("=" * 65)


if __name__ == '__main__':
    import logging as _log
    _log.basicConfig(level=_log.INFO, format='%(levelname)s: %(message)s')

    from .evaluator import EssayEvaluator

    # NOTE: pass a real AI engine here if you want AI-path calibration
    # e.g. from your_ai_module import DeepSeekEngine
    # engine = DeepSeekEngine(...)
    # evaluator = EssayEvaluator(engine)
    evaluator = EssayEvaluator(ai_engine=None) # rule-based only

    stats = measure_mae(evaluator)
    print_report(stats)