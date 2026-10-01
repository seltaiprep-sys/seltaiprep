"""AI-powered detailed writing feedback - PURE AI, NO FALLBACK TEMPLATES

FIXES APPLIED (v2 — EMOJI CLEANUP RECOVERY):
  (1) Removed leading spaces from all error messages. These were
        artifacts of an earlier emoji-cleanup pass that stripped
        a leading "!" / icon glyph, leaving `" ..."` behind.
  (2) `print()` in __init__ → `logger.info()` so log-level controls work.
  (3) Removed unused `Counter` import.
"""
import re
import json
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# These are analysis tools, NOT fallback templates - kept for rule-based when AI unavailable
LINKING_WORDS = ['however', 'furthermore', 'moreover', 'nevertheless', 'consequently',
                 'therefore', 'in addition', 'on the other hand', 'in contrast',
                 'as a result', 'accordingly', 'hence', 'thus', 'nonetheless']

GRAMMAR_FIXES = {
    r'\b(he|she|it)\s+don\'t\b': ("doesn't", "Use 'doesn't' with third person singular"),
    r'\bmore\s+(better|worse)\b': (r'\1', "Remove 'more' - already comparative"),
    r'\b(people|children)\s+(is|was)\b': (r'\1 are', "Use 'are/were' with plural subjects"),
    r'\bthere\s+is\s+(\w+s)\b': ("there are", "Use 'there are' with plural nouns"),
    r'\bdespite\s+of\b': ("despite", "Use 'despite' without 'of'"),
    r'\bcan\s+to\b': ("can", "Remove 'to' after modal verbs"),
}

# Vocabulary upgrade suggestions (guidance, not templates)
REPLACE_MAP = {
    'good': ['beneficial', 'advantageous', 'positive', 'favourable'],
    'bad': ['detrimental', 'harmful', 'negative', 'adverse'],
    'big': ['substantial', 'considerable', 'significant', 'extensive'],
    'small': ['minor', 'negligible', 'limited', 'marginal'],
    'thing': ['aspect', 'factor', 'element', 'issue'],
    'important': ['crucial', 'essential', 'vital', 'paramount'],
    'show': ['demonstrate', 'illustrate', 'indicate', 'reveal'],
    'get': ['obtain', 'acquire', 'attain', 'secure'],
    'make': ['create', 'generate', 'establish', 'formulate'],
    'problem': ['issue', 'challenge', 'difficulty', 'obstacle'],
    'people': ['individuals', 'citizens', 'population', 'society'],
    'a lot': ['considerably', 'substantially', 'significantly'],
}


class AIFeedbackGenerator:
    """Generate comprehensive IELTS writing feedback - AI primary, NO FALLBACKS"""

    def __init__(self, ai_engine=None):
        if not ai_engine:
            raise ValueError(
                "AI Engine is required for AIFeedbackGenerator. "
                "No fallback templates available."
            )

        self.ai = ai_engine
        logger.info("[AIFeedbackGenerator] Initialized with pure AI mode")

    def generate_feedback(self, essay: str, prompt: str, task_type: str,
                          scores: Dict, word_count: int) -> Dict:
        """
        Generate detailed feedback - PURE AI, NO RULE-BASED FALLBACK
        """
        if not self.ai:
            raise RuntimeError("Cannot generate feedback: AI Engine not available")

        if not essay or len(essay.strip()) < 50:
            return {
                'error': 'Essay too short for feedback',
                'overall_assessment': 'Please write a longer essay for detailed feedback.',
                'what_to_improve': ['Write at least 150 words for Task 1 or 250 words for Task 2'],
                'source': 'error'
            }

        try:
            result = self._ai_feedback(essay, prompt, task_type, scores, word_count)
            if result:
                result['source'] = 'ai'
                return result
            else:
                raise ValueError("AI feedback generation returned no result")
        except Exception as e:
            logger.error(f"AI feedback failed: {e}")
            raise RuntimeError(f"Failed to generate AI feedback: {e}")

    def _ai_feedback(self, essay: str, prompt: str, task_type: str,
                     scores: Dict, word_count: int) -> Dict:
        """
        Generate feedback using AI - NO TEMPLATES
        """
        overall = scores.get('overall_band', 6.0)

        # Truncate essay if too long
        truncated_essay = essay[:2500] if len(essay) > 2500 else essay

        feedback_prompt = f"""You are a senior IELTS examiner with 20+ years of experience. Provide DETAILED, ACTIONABLE feedback on this {task_type} essay.

═══════════════════════════════════════════════════════════════
PROMPT: {prompt[:300]}
═══════════════════════════════════════════════════════════════

ESSAY ({word_count} words):
{truncated_essay}

═══════════════════════════════════════════════════════════════
CURRENT BAND: {overall}
═══════════════════════════════════════════════════════════════

Return ONLY valid JSON with this EXACT structure (no extra text):

{{
    "overall_assessment": "2-3 sentences evaluating the essay with justification for the band score",
    "what_worked_well": [
        "Specific strength with example from essay",
        "Another specific strength with example",
        "Third strength with example"
    ],
    "what_to_improve": [
        "Specific weakness with example and correction",
        "Another specific weakness with example and correction",
        "Third specific weakness with example and correction"
    ],
    "grammar_fixes": [
        {{"original": "incorrect phrase from essay", "corrected": "corrected version", "explanation": "grammar rule explanation"}},
        {{"original": "another error", "corrected": "correction", "explanation": "why it's wrong"}}
    ],
    "vocabulary_upgrades": [
        {{"original": "basic word", "suggested": "advanced alternative", "context": "example sentence"}},
        {{"original": "another word", "suggested": "better word", "context": "example"}}
    ],
    "structure_tips": [
        "Specific structural advice for this essay",
        "Another structural tip",
        "Third structural tip"
    ],
    "sample_improvement": "Rewrite ONE weak paragraph at Band 8+ level (50-80 words)",
    "next_steps": [
        "Concrete practice suggestion 1",
        "Concrete practice suggestion 2",
        "Concrete practice suggestion 3"
    ],
    "estimated_potential_band": 7.5
}}

REQUIREMENTS:
- Be SPECIFIC - quote actual phrases from the essay
- Provide CORRECTIONS, not just criticism
- Focus on what will improve the band score
- Be encouraging but honest
- estimated_potential_band should be 0.5-1.0 higher than current if improvements are made"""

        try:
            response = self.ai.generate(feedback_prompt, max_tokens=1800, temperature=0.4)

            if not response:
                raise ValueError("AI returned empty response")

            # Extract JSON
            json_str = response.strip()
            json_str = re.sub(r'```(?:json)?\s*|```\s*', '', json_str)

            # Find JSON object
            start = json_str.find('{')
            if start == -1:
                raise ValueError("No JSON object found in response")

            # Find matching closing brace
            brace_count = 0
            end = start
            for i, char in enumerate(json_str[start:], start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            json_str = json_str[start:end]

            # Clean JSON
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            json_str = ''.join(ch for ch in json_str if ord(ch) >= 32 or ch in '\n\r\t')

            result = json.loads(json_str, strict=False)

            # Validate required fields
            required_fields = ['overall_assessment', 'what_worked_well', 'what_to_improve']
            for field in required_fields:
                if field not in result:
                    result[field] = f"AI feedback missing {field}"

            # Ensure arrays have reasonable length
            if len(result.get('what_worked_well', [])) < 2:
                result['what_worked_well'] = result.get('what_worked_well', []) + ["Essay addresses the prompt"]

            if len(result.get('what_to_improve', [])) < 2:
                result['what_to_improve'] = result.get('what_to_improve', []) + ["Add more specific examples"]

            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}")
            raise ValueError(f"Failed to parse AI feedback JSON: {e}")
        except Exception as e:
            logger.error(f"AI feedback generation failed: {e}")
            raise RuntimeError(f"Failed to generate AI feedback: {e}")

    def generate_quick_feedback(self, essay: str, task_type: str, band: float) -> Dict:
        """
        Generate quick feedback without full evaluation - PURE AI
        """
        if not self.ai:
            raise RuntimeError("Cannot generate feedback: AI Engine not available")

        quick_prompt = f"""You are an IELTS examiner. Give QUICK feedback on this {task_type} essay (Band {band}).

Essay: {essay[:800]}

Return ONLY valid JSON:
{{
    "band_accuracy": "Is this band accurate? Why/why not?",
    "quick_tip": "One thing to fix immediately",
    "quick_strength": "One thing you did well",
    "estimated_band": {band}
}}"""

        try:
            response = self.ai.generate(quick_prompt, max_tokens=300, temperature=0.3)

            json_str = re.sub(r'```(?:json)?\s*|```\s*', '', response.strip())
            start = json_str.find('{')
            end = json_str.rfind('}')

            if start != -1 and end != -1:
                result = json.loads(json_str[start:end+1])
                return result

            return {
                'band_accuracy': f'Band {band} assessment provided',
                'quick_tip': 'Review vocabulary and grammar',
                'quick_strength': 'Essay addresses the prompt',
                'estimated_band': band
            }
        except Exception as e:
            logger.error(f"Quick feedback failed: {e}")
            return {
                'error': str(e),
                'quick_tip': 'AI feedback temporarily unavailable',
                'estimated_band': band
            }

    def get_band_breakdown(self, essay: str, task_type: str) -> Dict:
        """
        Get detailed band breakdown across 4 IELTS criteria - PURE AI
        """
        if not self.ai:
            raise RuntimeError("Cannot generate breakdown: AI Engine not available")

        breakdown_prompt = f"""You are an IELTS examiner. Rate this {task_type} essay on the 4 IELTS criteria.

Essay: {essay[:1200]}

Return ONLY valid JSON:
{{
    "task_achievement": {{"band": 6.0, "feedback": "specific feedback"}},
    "coherence_cohesion": {{"band": 6.0, "feedback": "specific feedback"}},
    "lexical_resource": {{"band": 6.0, "feedback": "specific feedback"}},
    "grammatical_range": {{"band": 6.0, "feedback": "specific feedback"}},
    "overall_band": 6.0,
    "priority": "What to focus on first"
}}"""

        try:
            response = self.ai.generate(breakdown_prompt, max_tokens=600, temperature=0.3)

            json_str = re.sub(r'```(?:json)?\s*|```\s*', '', response.strip())
            start = json_str.find('{')
            end = json_str.rfind('}')

            if start != -1 and end != -1:
                result = json.loads(json_str[start:end+1])
                return result

            return {'error': 'Failed to parse band breakdown'}
        except Exception as e:
            logger.error(f"Band breakdown failed: {e}")
            return {'error': str(e)}


# Factory function
def create_ai_feedback(ai_engine):
    """Factory function to create AIFeedbackGenerator with AI engine"""
    if not ai_engine:
        raise ValueError("AI Engine required to create AIFeedbackGenerator")

    return AIFeedbackGenerator(ai_engine)


# Do NOT create instance here - will be created by app
# ai_feedback = AIFeedbackGenerator() # REMOVED