"""Backward-compatibility shim for IELTS Reading generator.

═══════════════════════════════════════════════════════════════════════
PURPOSE
═══════════════════════════════════════════════════════════════════════
The actual IELTS Reading generator implementation moved to
`passage_generator.py` in v2.0, which added:

  (1) Full passage passed to question-generation prompt
      (previously only first 2500 chars → questions about 2nd half
      were impossible to answer).

  (2) Global question numbering across the test:
      Passage 1 → Q1–Q13
      Passage 2 → Q14–Q26
      Passage 3 → Q27–Q40
      (Previously every passage renumbered 1–13 → duplicates in
      frontend and Q14–Q40 never shown.)

  (3) Strict word count in passage prompt (850–950 words) with
      auto-retry if AI returns under 700 words.

  (4) Meaningful fallback MCQs (distractors via negation /
      number-change / opposite-claim). Previously all options were
      true quotes → MCQ was unsolvable.

  (5) Better type distribution across the 3 passages so each test
      covers 6+ distinct IELTS question types.

  (6) Option normalization retained (fixes frontend crash when AI
      returns {"letter": "A", "text": "..."}).

═══════════════════════════════════════════════════════════════════════
WHY THIS FILE STILL EXISTS
═══════════════════════════════════════════════════════════════════════
Multiple places in the codebase still import the old path:

    from modules.ielts.reading.test_generator import IELTSReadingGenerator

  • app.py (line ~66, ~837, ~1574, ~1599, ~2389)
  • Possibly PTE / UKVI / speaking modules
  • Admin scripts / test scripts
  • Any code written before v2.0

This file keeps those imports working. It does NOT contain any logic —
it simply re-exports `IELTSReadingGenerator` from `passage_generator`.

═══════════════════════════════════════════════════════════════════════
DO NOT
═══════════════════════════════════════════════════════════════════════
  • Do NOT add new logic here.
  • Do NOT duplicate any generation code.
  • Do NOT modify `__all__` without also updating consumers.
  • Do NOT delete this file — breaking all old imports.

If you want to change generator behaviour, edit `passage_generator.py`.

═══════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════
Old style (still works — re-exports from passage_generator):

    from modules.ielts.reading.test_generator import IELTSReadingGenerator
    generator = IELTSReadingGenerator(ai_engine=ai_engine)
    test = generator.generate_complete_test("medium")

New style (preferred — points directly at the real implementation):

    from modules.ielts.reading.passage_generator import IELTSReadingGenerator
    generator = IELTSReadingGenerator(ai_engine=ai_engine)
    test = generator.generate_complete_test("medium")

Both styles produce the exact same class object.
═══════════════════════════════════════════════════════════════════════
"""

from .passage_generator import IELTSReadingGenerator

# Re-export so both paths resolve to the same class object.
# When someone does `from .test_generator import IELTSReadingGenerator`,
# Python's module system returns the SAME class that lives in
# passage_generator — no duplication, no divergence.
__all__ = ["IELTSReadingGenerator"]


# ═══════════════════════════════════════════════════════════════════
# Sanity check — verify the re-export resolved correctly at import time.
# This will raise ImportError immediately if passage_generator is
# missing or broken, so we fail fast rather than later at call time.
# ═══════════════════════════════════════════════════════════════════
if IELTSReadingGenerator.__module__ != "modules.ielts.reading.passage_generator":
    # Extremely defensive — this should never happen. If it does,
    # somebody has duplicated the class in this file by mistake.
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    _logger.warning(
        " test_generator.IELTSReadingGenerator is defined in %r, "
        "expected it from 'modules.ielts.reading.passage_generator'. "
        "This shim should ONLY re-export — check for duplicated class "
        "definitions.",
        IELTSReadingGenerator.__module__,
    )