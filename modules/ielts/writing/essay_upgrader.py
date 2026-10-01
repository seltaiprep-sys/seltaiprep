"""AI-powered essay upgrade - PURE AI, NO FALLBACK TEMPLATES with Auto-Generation & User-Preserve

FIXES APPLIED (v2):
  (1) CRITICAL — is_model_essay flag now correctly reflects the essay's origin:
        · upgrade_task1 → 'ai-chart-matched' branch: set is_model_essay=True
          (the essay is entirely AI-generated, not an upgrade of the user's text)
        · upgrade_task2 → 'ai-offtopic-fixed' branch: set is_model_essay=True
          (same reason)
      Previously both branches reported is_model_essay=False, causing the
      frontend to display the model's band as if it were the user's band.
  (2) _short_response_result now includes 'success': True so all callers
      can read result['success'] without a KeyError.
  (3) Every model-essay result now carries:
        · is_model_essay: True
        · user_score_unaffected: True
        · model_band: <target_band>
        · regenerated_reason: short explanation string
      so the frontend can render a clear "model essay" banner and keep the
      user's real band separate from the model's.
  (4) AI-generated branches (ai-chart-matched, ai-offtopic-fixed) are now
      unified through a single _build_model_essay_result() helper to avoid
      the two branches drifting apart in future edits.
"""
import re
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class EssayUpgrader:
    """Pure AI essay upgrader - NO TEMPLATE FALLBACKS with auto-generation & user-preserve"""

    # IELTS-specific minimum word counts.
    # Below these thresholds, we generate a MODEL essay (clearly flagged)
    # instead of trying to upgrade the user's essay. The model essay is
    # for learning only — it does NOT affect the user's score.
    MIN_UPGRADE_WORDS_TASK1 = 150
    MIN_UPGRADE_WORDS_TASK2 = 250
    # Absolute minimum below which the response is considered "empty"
    ABSOLUTE_MIN_WORDS = 20

    def __init__(self, ai_engine=None):
        if not ai_engine:
            raise ValueError(" AI Engine is required for EssayUpgrader. No fallback templates available.")

        self.ai = ai_engine
        logger.info("[EssayUpgrader] Initialized with pure AI mode + auto-generation")

    # ==================== JSON HELPERS ====================

    def _clean_json_response(self, text: str) -> str:
        """Clean and repair JSON response from AI"""
        if not text:
            return "{}"

        # Remove markdown code blocks
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)

        # Find the first { and last }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and start < end:
            text = text[start:end+1]
        else:
            return "{}"

        # Remove control characters (except newline and tab)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        # Fix common JSON issues
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)

        def fix_string(match):
            content = match.group(1)
            content = content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            content = re.sub(r'(?<!\\)"', r'\"', content)
            return f'"{content}"'

        text = re.sub(r':\s*"([^"]*)"', fix_string, text)
        text = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', text)

        return text

    def _parse_json_safely(self, text: str) -> Dict:
        """Safely parse JSON with multiple fallback strategies"""
        if not text:
            return {}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            cleaned = self._clean_json_response(text)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        try:
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if match:
                cleaned = self._clean_json_response(match.group())
                return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        try:
            essay_match = re.search(r'"upgraded_essay"\s*:\s*"([^"]+)"', text, re.DOTALL)
            if essay_match:
                essay_text = essay_match.group(1)
                essay_text = essay_text.replace('\\"', '"').replace('\\n', '\n')
                return {
                    "upgraded_essay": essay_text,
                    "key_improvements": ["Essay extracted from response"],
                    "error": "JSON parsing had issues but essay was extracted"
                }
        except Exception:
            pass

        return {
            "upgraded_essay": text[:500] if text else "Essay could not be upgraded.",
            "key_improvements": ["AI upgrade failed - returning original essay"],
            "error": "Could not parse JSON response"
        }

    # ==================== HELPERS ====================

    def _min_words_for(self, task_type: str) -> int:
        """Return the minimum word count for the given task."""
        if task_type in ('task1', '1'):
            return self.MIN_UPGRADE_WORDS_TASK1
        return self.MIN_UPGRADE_WORDS_TASK2

    def _short_response_result(self, original_essay: str, model_essay: str,
                                user_band: float, model_band: float,
                                word_count: int, task_type: str,
                                min_words: int,
                                generated_question: str = None,
                                generated_chart_data: dict = None) -> Dict:
        """
        Build the result dict for a short-response case.

        This is the ONLY place a model essay is returned for the
        "response-too-short" path. It is clearly flagged with
        `is_model_essay: True` so no caller can mistake it for an
        upgraded user essay, and it carries `user_band` (the real user's
        band) separately from `model_band` (the model's band).
        """
        result = {
            'success': True,
            # Model essay — for learning, NOT the user's writing
            'original_essay': original_essay,
            'upgraded_essay': model_essay,
            'model_essay': model_essay,
            'is_model_essay': True,
            'user_score_unaffected': True,
            'short_response': True,
            'short_response_words': word_count,
            'min_words_required': min_words,
            'task_type': task_type,
            # Separate bands — do NOT let downstream read user_band as model_band
            'user_band': round(float(user_band or 0), 1),
            'model_band': round(float(model_band or 0), 1),
            'regenerated_reason': 'response_too_short',
            'key_improvements': [
                f'Your response was only {word_count} words — below the '
                f'{min_words}-word minimum for {task_type.upper()}.',
                f'Here is a Band {model_band} model essay for the same question.',
                'Study how the chart data is described and compared.' if task_type == 'task1'
                    else 'Study how the introduction, body paragraphs, and conclusion are built.',
                'Use this only as a reference for structure and vocabulary — '
                'your own score remains unchanged.',
            ],
            'upgrader': 'model-essay-for-short-response',
            'preserved_user_essay': False,
        }
        if generated_question:
            result['generated_question'] = generated_question
        if generated_chart_data:
            result['generated_chart_data'] = generated_chart_data
        return result

    def _build_model_essay_result(self, original_essay: str, model_essay: str,
                                   user_band: float, model_band: float,
                                   task_type: str, regenerated_reason: str,
                                   key_improvements: list,
                                   auto_generate: bool = False,
                                   generated_question: str = None,
                                   generated_chart_data: dict = None) -> Dict:
        """
        Build a result for the case where an ENTIRELY NEW AI essay was
        generated (not an upgrade of the user's text). This is used by:
          · upgrade_task1 → ai-chart-matched (user's essay didn't mention
            the chart's labels/values)
          · upgrade_task2 → ai-offtopic-fixed (user's band was too low,
            essay likely off-topic)

        The returned dict is unmistakably flagged as a model essay, so no
        downstream caller can confuse its band with the user's band.
        """
        result = {
            'success': True,
            'original_essay': original_essay,
            'upgraded_essay': model_essay,
            'model_essay': model_essay,
            'is_model_essay': True,
            'user_score_unaffected': True,
            'short_response': False,
            'task_type': task_type,
            # Separate bands — never let downstream alias these
            'user_band': round(float(user_band or 0), 1),
            'model_band': round(float(model_band or 0), 1),
            'regenerated_reason': regenerated_reason,
            'key_improvements': key_improvements,
            'upgrader': 'model-essay-generated',
            'preserved_user_essay': False,
        }
        if auto_generate:
            if generated_question:
                result['generated_question'] = generated_question
            if generated_chart_data:
                result['generated_chart_data'] = generated_chart_data
        return result

    # ==================== CHART MATCH CHECK ====================

    def _check_chart_match(self, essay: str, chart_data: dict) -> bool:
        """Check if essay mentions chart labels/values"""
        if not chart_data:
            return False

        essay_lower = essay.lower()
        labels = chart_data.get('labels', [])

        if labels:
            label_match = sum(1 for l in labels if str(l).lower() in essay_lower)
            if label_match >= len(labels) * 0.3:
                return True

        datasets = chart_data.get('datasets', [])
        for ds in datasets:
            if isinstance(ds, dict) and ds.get('label'):
                if ds['label'].lower() in essay_lower:
                    return True

        all_values = []
        for ds in datasets:
            if isinstance(ds, dict):
                all_values.extend([str(v) for v in ds.get('values', [])])

        value_match = sum(1 for v in all_values if v in essay)
        if value_match >= len(all_values) * 0.2:
            return True

        return False

    # ==================== GENERATE CHART-MATCHED ESSAY ====================

    def _generate_chart_matched_essay(self, prompt: str, chart_data: dict,
                                       chart_type: str, target_band: float) -> str:
        """Generate a proper essay that matches the chart data - PURE AI"""

        labels = chart_data.get('labels', [])
        datasets = chart_data.get('datasets', [])

        data_desc = f"Chart type: {chart_type}\n"
        if labels:
            data_desc += f"Categories/Labels: {', '.join(str(l) for l in labels)}\n\n"

        for ds in datasets:
            if isinstance(ds, dict):
                ds_label = ds.get('label', 'Series')
                values = ds.get('values', [])
                data_desc += f"{ds_label} values:\n"
                for i, (label, val) in enumerate(zip(labels, values)):
                    data_desc += f" - {label}: {val}\n"
                data_desc += "\n"

        all_vals = []
        for ds in datasets:
            if isinstance(ds, dict):
                for i, v in enumerate(ds.get('values', [])):
                    all_vals.append((v, ds.get('label', ''), labels[i] if i < len(labels) else ''))

        if all_vals:
            highest = max(all_vals, key=lambda x: x[0])
            lowest = min(all_vals, key=lambda x: x[0])
            data_desc += f"OVERVIEW: Highest is {highest[1]} at {highest[0]} ({highest[2]}). Lowest is {lowest[1]} at {lowest[0]} ({lowest[2]}).\n"

        ai_prompt = f"""You are an IELTS examiner. Write a Band {target_band} Task 1 essay describing this chart.

CHART DATA:
{data_desc}

PROMPT: {prompt}

REQUIREMENTS:
- Start with a clear overview statement mentioning the highest and lowest
- Include specific data values in your description
- Compare values between categories
- Describe any notable trends
- Use academic vocabulary
- Write 150-180 words
- Return ONLY the essay text, no explanations or headers

Generate a UNIQUE, HIGH-QUALITY essay."""

        result = self.ai.generate(ai_prompt, max_tokens=800, temperature=0.7)

        if not result or len(result.strip()) < 100:
            raise ValueError(f" AI generated insufficient essay ({len(result) if result else 0} chars)")

        return result.strip()

    # ==================== GENERATE TASK 2 ESSAY ====================

    def _generate_task2_matched_essay(self, prompt: str, target_band: float) -> str:
        """Generate a proper Task 2 essay - PURE AI"""

        ai_prompt = f"""You are an IELTS examiner. Write a Band {target_band} Task 2 essay.

QUESTION: {prompt}

REQUIREMENTS:
- Clear position statement
- Introduction paragraph
- 2-3 body paragraphs with specific examples
- Counter-argument paragraph
- Strong conclusion
- Use academic vocabulary
- Write 250-280 words
- Return ONLY the essay, no explanations or headers

Generate a UNIQUE, HIGH-QUALITY essay."""

        result = self.ai.generate(ai_prompt, max_tokens=800, temperature=0.7)

        if not result or len(result.strip()) < 200:
            raise ValueError(f" AI generated insufficient essay ({len(result) if result else 0} chars)")

        return result.strip()

    # ==================== AUTO-GENERATE TASK 1 DATA ====================

    def generate_task1_data(self, topic: str = None, custom_prompt: str = None) -> Dict:
        """
        AI लाई Task 1 को प्रश्न र chart_data आफैं उत्पन्न गर्न भन्ने।

        Returns:
            Dict with keys: 'question' (str), 'chart_data' (dict)
        """
        if not self.ai:
            raise RuntimeError(" AI Engine not available")

        if not topic:
            topic = "average monthly rainfall in three cities for the year 2022"

        if not custom_prompt:
            custom_prompt = f"""You are an IELTS examiner. Create a Task 1 bar chart question about {topic}.

Return ONLY valid JSON with this structure:
{{
    "question": "Full IELTS Task 1 question prompt (at least 30 words)...",
    "chart_data": {{
        "labels": ["Category1", "Category2", "Category3"],
        "datasets": [
            {{"label": "Series1", "values": [10, 20, 30]}},
            {{"label": "Series2", "values": [40, 50, 60]}}
        ]
    }}
}}

Requirements:
- The question must be realistic and match the data.
- Include at least 3 labels and 2-3 datasets.
- Values should be realistic and varied.
- The data should have clear highest/lowest points for comparison.
- Use JSON format exactly as shown. Do not add any extra text."""

        try:
            response = self.ai.generate(custom_prompt, max_tokens=1000, temperature=0.7)
            if not response:
                raise ValueError("AI returned empty response")

            parsed = self._parse_json_safely(response)
            if not parsed or 'question' not in parsed or 'chart_data' not in parsed:
                raise ValueError("Generated JSON missing required keys")

            chart_data = parsed['chart_data']
            if 'labels' not in chart_data or 'datasets' not in chart_data:
                raise ValueError("chart_data missing 'labels' or 'datasets'")

            logger.info("[Upgrader] Successfully generated question and chart data")
            return {
                'question': parsed['question'],
                'chart_data': chart_data,
                'topic': topic
            }
        except Exception as e:
            logger.error(f"[Upgrader] Failed to generate data: {e}")
            raise RuntimeError(f" Failed to generate Task 1 data: {e}")

    # ==================== UPGRADE TASK 1 ====================

    def upgrade_task1(self, essay: str, prompt: str = None, chart_type: str = 'bar_chart',
                      current_scores: Dict = None, target_band: float = None,
                      chart_data: dict = None, auto_generate: bool = False,
                      preserve_user_essay: bool = True) -> Dict:
        """
        Upgrade Task 1 essay - PURE AI, NO TEMPLATES

        Args:
            preserve_user_essay (bool):
                True = always upgrade the user's essay (never replace it
                        with a fully AI-generated essay).
                False = regenerate from scratch if the essay doesn't match
                        the chart data.
        """
        if not self.ai:
            raise RuntimeError(" Cannot upgrade: AI Engine not available")

        if current_scores is None:
            current_scores = {'overall_band': 6.0}
        current = current_scores.get('overall_band', 6.0)

        if target_band is None:
            target_band = min(9.0, current + 0.5)

        generated_question = None
        generated_chart_data = None

        # Auto-generate if requested and chart_data is missing
        if auto_generate and not chart_data:
            logger.info("[Upgrader] Auto-generating Task 1 question and data...")
            generated = self.generate_task1_data()
            generated_question = generated['question']
            generated_chart_data = generated['chart_data']

            if not prompt:
                prompt = generated_question
                logger.info(f"[Upgrader] Using generated question: {prompt[:100]}...")
            else:
                logger.info("[Upgrader] Using provided prompt with auto-generated chart data")

            chart_data = generated_chart_data

        # Ensure prompt exists
        if not prompt:
            raise ValueError(" Prompt is required for Task 1 upgrade (and auto_generate failed to produce one)")

        # ─── SHORT-RESPONSE GUARD ───────────────────────────────────
        # If the user's response is too short to meaningfully upgrade,
        # generate a full Band X MODEL essay (clearly flagged), and do
        # NOT let its band be mistaken for the user's score.
        word_count = len((essay or "").strip().split())
        min_words = self._min_words_for('task1')

        if word_count < min_words:
            logger.info(
                f"[Upgrader] Task 1 essay only {word_count} words "
                f"(< {min_words}) — generating a MODEL essay for learning"
            )
            try:
                # Ensure we have chart_data to work with
                if not chart_data:
                    if auto_generate:
                        generated = self.generate_task1_data()
                        generated_question = generated['question']
                        generated_chart_data = generated['chart_data']
                        chart_data = generated_chart_data
                        if not prompt:
                            prompt = generated_question
                    else:
                        # No chart data and can't generate — fall through
                        raise ValueError("No chart_data available for model essay generation")

                if prompt and chart_data:
                    model_essay = self._generate_chart_matched_essay(
                        prompt, chart_data, chart_type, target_band
                    )
                    if model_essay:
                        return self._short_response_result(
                            original_essay=essay,
                            model_essay=model_essay,
                            user_band=current,
                            model_band=target_band,
                            word_count=word_count,
                            task_type='task1',
                            min_words=min_words,
                            generated_question=generated_question or prompt,
                            generated_chart_data=generated_chart_data or chart_data,
                        )
            except Exception as e:
                logger.error(f"[Upgrader] Model essay generation failed: {e}")
                # Fall through to normal path if model generation fails

        logger.info(f"[Upgrader] Upgrading Task 1 from band {current} to {target_band}")

        # ===== If preserve_user_essay is True, ALWAYS upgrade user's essay =====
        if preserve_user_essay:
            logger.info("[Upgrader] Preserving user's essay - upgrading the original text with chart data")
            try:
                result = self._ai_upgrade(essay, prompt, current, target_band, 'task1', chart_data=chart_data)
                if result:
                    if auto_generate:
                        result['generated_question'] = generated_question or prompt
                        result['generated_chart_data'] = generated_chart_data or chart_data
                        result['preserved_user_essay'] = True
                    return result
                else:
                    return {
                        'success': True,
                        'original_essay': essay,
                        'upgraded_essay': essay,
                        'original_band': current,
                        'target_band': target_band,
                        'key_improvements': ['AI upgrade failed - returning original essay'],
                        'upgrader': 'fallback',
                        'preserved_user_essay': True,
                        'is_model_essay': False,
                        'user_band': current,
                    }
            except Exception as e:
                logger.error(f"[Upgrader] AI upgrade failed: {e}")
                return {
                    'success': True,
                    'original_essay': essay,
                    'upgraded_essay': essay,
                    'original_band': current,
                    'target_band': target_band,
                    'key_improvements': [f'AI upgrade failed: {str(e)}'],
                    'upgrader': 'fallback',
                    'preserved_user_essay': True,
                    'is_model_essay': False,
                    'user_band': current,
                }

        # ===== If preserve_user_essay is False, check if we need to regenerate =====
        if chart_data and isinstance(chart_data, dict):
            if not self._check_chart_match(essay, chart_data):
                logger.info("[Upgrader] Essay doesn't match chart data - generating new chart-matched essay")
                try:
                    new_essay = self._generate_chart_matched_essay(
                        prompt, chart_data, chart_type, target_band
                    )
                    if new_essay:
                        # FIX: is_model_essay=True because the returned essay
                        # is entirely AI-generated, NOT an upgrade of
                        # the user's text. The user's band remains
                        # `current` and is exposed separately.
                        return self._build_model_essay_result(
                            original_essay=essay,
                            model_essay=new_essay,
                            user_band=current,
                            model_band=target_band,
                            task_type='task1',
                            regenerated_reason='chart_mismatch',
                            key_improvements=[
                                'Generated new essay that correctly matches the chart data',
                                'Includes all chart labels and specific values',
                                'Provides clear overview with highest/lowest comparison',
                                'Uses academic vocabulary and complex sentences',
                            ],
                            auto_generate=auto_generate,
                            generated_question=generated_question,
                            generated_chart_data=generated_chart_data,
                        )
                except Exception as e:
                    logger.error(f"[Upgrader] Chart-matched generation failed: {e}")
                    # fall through to normal upgrade

        # Normal AI upgrade (fallback) - also with chart_data
        try:
            result = self._ai_upgrade(essay, prompt, current, target_band, 'task1', chart_data=chart_data)
            if result:
                if auto_generate:
                    result['generated_question'] = generated_question or prompt
                    result['generated_chart_data'] = generated_chart_data or chart_data
                result['preserved_user_essay'] = True
                result['is_model_essay'] = False
                result['user_band'] = current
                return result
            else:
                return {
                    'success': True,
                    'original_essay': essay,
                    'upgraded_essay': essay,
                    'original_band': current,
                    'target_band': target_band,
                    'key_improvements': ['AI upgrade failed - returning original essay'],
                    'upgrader': 'fallback',
                    'preserved_user_essay': True,
                    'is_model_essay': False,
                    'user_band': current,
                }
        except Exception as e:
            logger.error(f"[Upgrader] AI upgrade failed: {e}")
            return {
                'success': True,
                'original_essay': essay,
                'upgraded_essay': essay,
                'original_band': current,
                'target_band': target_band,
                'key_improvements': [f'AI upgrade failed: {str(e)}'],
                'upgrader': 'fallback',
                'preserved_user_essay': True,
                'is_model_essay': False,
                'user_band': current,
            }

    # ==================== UPGRADE TASK 2 ====================

    def upgrade_task2(self, essay: str, prompt: str, current_scores: Dict,
                      target_band: float = None, preserve_user_essay: bool = True) -> Dict:
        """
        Upgrade Task 2 essay - PURE AI, NO TEMPLATES

        Args:
            preserve_user_essay (bool):
                True = always upgrade the user's essay.
                False = regenerate from scratch if the essay's band is
                        below 5.5 (likely off-topic).
        """
        if not self.ai:
            raise RuntimeError(" Cannot upgrade: AI Engine not available")

        current = current_scores.get('overall_band', 6.0)
        if target_band is None:
            target_band = min(9.0, current + 0.5)

        # ─── SHORT-RESPONSE GUARD ───────────────────────────────────
        word_count = len((essay or "").strip().split())
        min_words = self._min_words_for('task2')

        if word_count < min_words:
            logger.info(
                f"[Upgrader] Task 2 essay only {word_count} words "
                f"(< {min_words}) — generating a MODEL essay for learning"
            )
            try:
                model_essay = self._generate_task2_matched_essay(prompt, target_band)
                if model_essay:
                    return self._short_response_result(
                        original_essay=essay,
                        model_essay=model_essay,
                        user_band=current,
                        model_band=target_band,
                        word_count=word_count,
                        task_type='task2',
                        min_words=min_words,
                    )
            except Exception as e:
                logger.error(f"[Upgrader] Model essay generation failed: {e}")
                # Fall through to normal path if generation fails

        logger.info(f"[Upgrader] Upgrading Task 2 from band {current} to {target_band}")

        # ===== If preserve_user_essay is True, ALWAYS upgrade user's essay =====
        if preserve_user_essay:
            logger.info("[Upgrader] Preserving user's essay - upgrading the original text")
            try:
                result = self._ai_upgrade(essay, prompt, current, target_band, 'task2')
                if result:
                    result['preserved_user_essay'] = True
                    result['is_model_essay'] = False
                    result['user_band'] = current
                    return result
                else:
                    return {
                        'success': True,
                        'original_essay': essay,
                        'upgraded_essay': essay,
                        'original_band': current,
                        'target_band': target_band,
                        'key_improvements': ['AI upgrade failed - returning original essay'],
                        'upgrader': 'fallback',
                        'preserved_user_essay': True,
                        'is_model_essay': False,
                        'user_band': current,
                    }
            except Exception as e:
                logger.error(f"[Upgrader] AI upgrade failed: {e}")
                return {
                    'success': True,
                    'original_essay': essay,
                    'upgraded_essay': essay,
                    'original_band': current,
                    'target_band': target_band,
                    'key_improvements': [f'AI upgrade failed: {str(e)}'],
                    'upgrader': 'fallback',
                    'preserved_user_essay': True,
                    'is_model_essay': False,
                    'user_band': current,
                }

        # ===== If preserve_user_essay is False, check if essay is off-topic =====
        if current < 5.5:
            logger.info("[Upgrader] Low band essay detected - generating new on-topic essay")
            try:
                new_essay = self._generate_task2_matched_essay(prompt, target_band)
                if new_essay:
                    # FIX: is_model_essay=True because the returned essay is
                    # entirely AI-generated. Previously this branch
                    # returned False, causing the frontend to display
                    # the model's band as the user's band.
                    return self._build_model_essay_result(
                        original_essay=essay,
                        model_essay=new_essay,
                        user_band=current,
                        model_band=target_band,
                        task_type='task2',
                        regenerated_reason='off_topic_low_band',
                        key_improvements=[
                            'Generated new essay that properly addresses the prompt',
                            'Clear position statement and structure',
                            'Includes examples and counter-argument',
                            'Strong introduction and conclusion',
                        ],
                    )
            except Exception as e:
                logger.error(f"[Upgrader] Task 2 generation failed: {e}")
                raise RuntimeError(f" Failed to generate on-topic essay: {e}")

        # Normal AI upgrade (fallback)
        try:
            result = self._ai_upgrade(essay, prompt, current, target_band, 'task2')
            if result:
                result['preserved_user_essay'] = True
                result['is_model_essay'] = False
                result['user_band'] = current
                return result
            else:
                return {
                    'success': True,
                    'original_essay': essay,
                    'upgraded_essay': essay,
                    'original_band': current,
                    'target_band': target_band,
                    'key_improvements': ['AI upgrade failed - returning original essay'],
                    'upgrader': 'fallback',
                    'preserved_user_essay': True,
                    'is_model_essay': False,
                    'user_band': current,
                }
        except Exception as e:
            logger.error(f"[Upgrader] AI upgrade failed: {e}")
            return {
                'success': True,
                'original_essay': essay,
                'upgraded_essay': essay,
                'original_band': current,
                'target_band': target_band,
                'key_improvements': [f'AI upgrade failed: {str(e)}'],
                'upgrader': 'fallback',
                'preserved_user_essay': True,
                'is_model_essay': False,
                'user_band': current,
            }

    # ==================== AI UPGRADE HELPER ====================

    def _ai_upgrade(self, essay: str, prompt: str, current: float,
                    target: float, task: str, chart_data: dict = None) -> Optional[Dict]:
        """
        AI-powered essay upgrade - PURE AI with robust JSON handling.
        Now accepts chart_data for Task 1 to include in the upgrade prompt.
        """
        if task == 'task1' and chart_data:
            chart_desc = self._format_chart_data(chart_data)
            upgrade_prompt = f"""You are an expert IELTS tutor. Upgrade this Task 1 essay from Band {current} to Band {target}.

PROMPT: {prompt}

CHART DATA:
{chart_desc}

ORIGINAL ESSAY:
{essay[:1500]}

REQUIREMENTS FOR BAND {target}:
- Use the SPECIFIC data from the chart above – include exact numbers and comparisons.
- Write a clear overview of the main trends.
- Improve vocabulary, grammar, and cohesion.
- Follow the structure: Introduction, Overview, Body paragraphs with data, Conclusion.

Return ONLY valid JSON with this EXACT structure:
{{
    "upgraded_essay": "The complete upgraded essay text here...",
    "key_improvements": ["Improvement 1", "Improvement 2", "Improvement 3"]
}}

Do not add any text outside the JSON. Ensure the JSON is valid."""
        else:
            upgrade_prompt = f"""You are an expert IELTS tutor. Upgrade this {task.upper()} essay from Band {current} to Band {target}.

PROMPT: {prompt}

ORIGINAL ESSAY:
{essay[:1500]}

REQUIREMENTS FOR BAND {target}:
- Stronger vocabulary and academic phrasing
- More complex sentence structures
- Better cohesion and linking words
- Clearer task achievement
- Fewer errors

Return ONLY valid JSON with this EXACT structure:
{{
    "upgraded_essay": "The complete upgraded essay text here...",
    "key_improvements": ["Improvement 1", "Improvement 2", "Improvement 3"]
}}

Do not add any text outside the JSON. Ensure the JSON is valid."""

        try:
            response = self.ai.generate(upgrade_prompt, max_tokens=1500, temperature=0.5)
            if not response:
                logger.warning("[Upgrader] AI returned empty response")
                return None

            result = self._parse_json_safely(response)
            if not result:
                logger.warning("[Upgrader] Failed to parse JSON response")
                return None

            upgraded_essay = result.get('upgraded_essay', '')
            if not upgraded_essay or len(upgraded_essay) < 50:
                logger.warning(f"[Upgrader] Upgraded essay too short: {len(upgraded_essay) if upgraded_essay else 0} chars")
                if len(response) > 100:
                    cleaned = re.sub(r'\{[^{}]*\}', '', response)
                    cleaned = cleaned.strip()
                    if len(cleaned) > 100:
                        upgraded_essay = cleaned
                    else:
                        return None
                else:
                    return None

            return {
                'success': True,
                'original_essay': essay,
                'upgraded_essay': upgraded_essay,
                'original_band': current,
                'target_band': target,
                'key_improvements': result.get('key_improvements', [
                    'Improved vocabulary and academic style',
                    'Better sentence structures',
                    'Enhanced cohesion and flow'
                ]),
                'upgrader': 'ai',
                'is_model_essay': False,
                'user_band': current,
            }
        except Exception as e:
            logger.error(f"[Upgrader] AI upgrade error: {e}")
            return None

    def _format_chart_data(self, chart_data: dict) -> str:
        """Convert chart data into a readable text description for the upgrade prompt."""
        if not chart_data:
            return "No chart data provided."

        labels = chart_data.get('labels', [])
        datasets = chart_data.get('datasets', [])

        parts = []
        if chart_data.get('title'):
            parts.append(f"Title: {chart_data['title']}")
        if chart_data.get('type'):
            parts.append(f"Chart Type: {chart_data['type']}")

        if labels:
            parts.append("Categories/Labels: " + ", ".join(str(l) for l in labels))

        for ds in datasets:
            if isinstance(ds, dict):
                ds_label = ds.get('label', 'Series')
                values = ds.get('values', [])
                parts.append(f"{ds_label} values:")
                for i, (label, val) in enumerate(zip(labels, values)):
                    parts.append(f" - {label}: {val}")

        all_vals = []
        for ds in datasets:
            if isinstance(ds, dict):
                for i, v in enumerate(ds.get('values', [])):
                    all_vals.append((v, ds.get('label', ''), labels[i] if i < len(labels) else ''))
        if all_vals:
            highest = max(all_vals, key=lambda x: x[0])
            lowest = min(all_vals, key=lambda x: x[0])
            parts.append(f"OVERVIEW: Highest is {highest[1]} at {highest[0]} ({highest[2]}). Lowest is {lowest[1]} at {lowest[0]} ({lowest[2]}).")

        return "\n".join(parts) if parts else "Chart data provided but could not be formatted."

    # ==================== BATCH UPGRADE ====================

    def upgrade_batch(self, essays: list, prompts: list, task_type: str = 'task2',
                      preserve_user_essay: bool = True) -> list:
        """Upgrade multiple essays in batch"""
        results = []
        for i, (essay, prompt) in enumerate(zip(essays, prompts)):
            try:
                if task_type == 'task1':
                    result = self.upgrade_task1(
                        essay, prompt, 'bar_chart',
                        {'overall_band': 5.5},
                        preserve_user_essay=preserve_user_essay
                    )
                else:
                    result = self.upgrade_task2(
                        essay, prompt, {'overall_band': 5.5},
                        preserve_user_essay=preserve_user_essay
                    )
                results.append(result)
            except Exception as e:
                logger.error(f"Batch upgrade failed for essay {i}: {e}")
                results.append({
                    'success': False,
                    'original_essay': essay,
                    'upgraded_essay': essay,
                    'original_band': 0,
                    'key_improvements': [f'Upgrade failed: {str(e)}'],
                    'upgrader': 'failed',
                    'preserved_user_essay': preserve_user_essay,
                    'is_model_essay': False,
                    'user_band': 0,
                })
        return results


# ==================== FACTORY ====================

def create_essay_upgrader(ai_engine=None):
    """Factory function to create EssayUpgrader with AI engine"""
    if not ai_engine:
        raise ValueError(" AI Engine required to create EssayUpgrader")
    return EssayUpgrader(ai_engine)