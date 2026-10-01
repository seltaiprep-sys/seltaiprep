"""IELTS Writing Prompts Library - v3.1 (AI-ONLY, no fallbacks)

═══════════════════════════════════════════════════════════════════════
WHAT'S NEW IN v3.1 — RELIABILITY PASS
═══════════════════════════════════════════════════════════════════════

  FIX 1 — Relaxed Task 2 word-count validation.
     Previously the validator rejected prompts outside 30-80 words
     but the error message claimed "expected 40-60". Real IELTS
     prompts regularly run 25-30 words. Range is now 25-80 with a
     matching error message. Fixes the "word count out of range: 29"
     failure where DeepSeek produced 3 consecutive 29-word prompts.

  FIX 2 — Task 2 question-type rotation on failure.
     Previously `get_random_task2()` picked ONE question type at
     random and (via `_call_ai_with_retry`) retried that SAME type
     3 times. If the chosen type produced consistently-rejected
     output (e.g. `two_part_question`), the whole call failed with
     a 503 to the user. Now the library shuffles all question types
     and tries them in order, so a failed type is transparently
     followed by the next. Users no longer see generation failures.

  FIX 3 — Error messages cleaned of orphaned leading spaces.

═══════════════════════════════════════════════════════════════════════
WHAT'S IN v3.0 — AI-ONLY EDITION
═══════════════════════════════════════════════════════════════════════

   All fallback templates REMOVED. Every prompt now comes from the
     AI engine. If the AI fails, we RETRY up to 3 times with a
     different prompt or temperature, then raise a clear exception.

   FIX 1 (retained) — Task 1 generates chart_data alongside prompt.
   FIX 2 (retained) — Rich Task 2 prompt with real IELTS examples.
   FIX 4 (retained) — Cache with multiple versions per key +
                        force_new bypass.
   FIX 5 (retained) — Robust JSON extractor (object / array).

   NEW — Retry policy:
     • 3 attempts per request
     • Backoff: 0s → 2s → 5s
     • Temperature: 0.7 → 0.9 → 1.0 (more random on retry)
     • On total failure → raise PromptGenerationError

   NEW — chart_data validation on every AI response; if invalid,
     retry. Only on the 3rd failure do we raise.

   NEW — Custom map templates retained (they are hand-crafted and
     treated as first-class content, not a "fallback").

   The `source` field on returned dicts is always 'ai' (or
     'custom_map' for the hand-crafted maps). No 'fallback' anymore.
═══════════════════════════════════════════════════════════════════════
"""

import random
import json
import logging
import re
import time
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════
class PromptGenerationError(Exception):
    """Raised when the AI engine fails to produce a valid prompt
    after all retries."""
    pass


# ═══════════════════════════════════════════════════════════════════
# TOPIC CATEGORIES (keywords only — used to route Task 2 topic)
# ═══════════════════════════════════════════════════════════════════
TOPIC_CATEGORIES = {
    'technology': [
        'technology', 'internet', 'social media', 'artificial intelligence',
        'automation', 'digital', 'smartphone', 'computer', 'online',
    ],
    'education': [
        'education', 'school', 'university', 'learning', 'teaching',
        'students', 'exam', 'curriculum', 'academic',
    ],
    'environment': [
        'environment', 'climate', 'pollution', 'sustainability', 'renewable',
        'conservation', 'recycling', 'carbon', 'green energy',
    ],
    'health': [
        'health', 'healthcare', 'medicine', 'obesity', 'exercise', 'diet',
        'mental health', 'hospital', 'wellness', 'nutrition',
    ],
    'society': [
        'society', 'community', 'population', 'urban', 'rural', 'housing',
        'transport', 'inequality', 'poverty', 'welfare',
    ],
    'culture': [
        'culture', 'tradition', 'heritage', 'globalization', 'multicultural',
        'diversity', 'language', 'art', 'museum',
    ],
    'economy': [
        'economy', 'economic', 'business', 'employment', 'tax', 'wealth',
        'poverty', 'trade', 'investment', 'market',
    ],
    'government': [
        'government', 'policy', 'regulation', 'taxation', 'public services',
        'law', 'politics', 'democracy', 'citizenship',
    ],
    'work': [
        'work', 'career', 'employment', 'job', 'workplace', 'remote work',
        'office', 'salary', 'productivity', 'unemployment',
    ],
    'family': [
        'family', 'parenting', 'children', 'elderly', 'marriage',
        'household', 'generation', 'childcare',
    ],
    'sports': [
        'sports', 'athlete', 'fitness', 'game', 'competition', 'team',
        'exercise', 'stadium', 'olympics',
    ],
    'travel': [
        'travel', 'tourism', 'destination', 'adventure', 'vacation',
        'trip', 'holiday', 'backpacking',
    ],
}

CHART_TYPES = [
    'bar_chart', 'line_graph', 'pie_chart', 'table', 'map', 'diagram', 'flow_chart'
]
DIFFICULTY_LEVELS = ['easy', 'medium', 'hard']

# Cache config
CACHE_VERSIONS_PER_KEY = 5

# Retry policy
MAX_RETRIES = 3
RETRY_DELAYS = [0, 2, 5]                # seconds
RETRY_TEMPERATURES = [0.7, 0.9, 1.0]    # more random on retry

# ✅ v3.1 — Task 2 word-count range (relaxed; matches real IELTS)
TASK2_MIN_WORDS = 25
TASK2_MAX_WORDS = 80


# ═══════════════════════════════════════════════════════════════════
# CUSTOM MAP TEMPLATES
#
# These are HAND-CRAFTED maps (not AI-generated). They are treated
# as first-class content — not a fallback. The library will prefer
# them for `map` requests with 60% probability, and use AI generation
# for the remaining 40% (to add variety).
# ═══════════════════════════════════════════════════════════════════
CUSTOM_MAP_TEMPLATES = [
    {
        "name": "Greenfield Town Center Development",
        "topic": "town development",
        "title": "Development of Greenfield Town Center (1990-2020)",
        "prompt": (
            "The map below shows the development of a small town called "
            "Greenfield from 1990 to 2020. Summarise the information by "
            "selecting and reporting the main features, and make "
            "comparisons where relevant."
        ),
        "chart_type": "map",
        "data": {
            "title": "Development of Greenfield Town Center (1990-2020)",
            "before": [
                "Houses (south-west area)", "River (east boundary)",
                "Open field (north-east)", "Grassland (north-west)",
                "Vacant land (south-east)",
            ],
            "after": [
                "Church (south-west area)", "River (east boundary)",
                "Main Road with housing (north-east)", "Farmland (north-west)",
                "Park with pond (south-east)",
            ],
            "changes": [
                "Houses demolished and replaced by a new Church",
                "River remained unchanged",
                "Open field developed into Main Road with housing",
                "Grassland converted to Farmland",
                "Vacant land turned into Park with a pond",
            ],
            "positions_before": [
                {"x": 150, "y": 420, "type": "house"},
                {"x": 420, "y": 280, "type": "water"},
                {"x": 350, "y": 130, "type": "default"},
                {"x": 150, "y": 140, "type": "default"},
                {"x": 380, "y": 400, "type": "default"},
            ],
            "positions_after": [
                {"x": 150, "y": 420, "type": "church"},
                {"x": 420, "y": 280, "type": "water"},
                {"x": 350, "y": 130, "type": "road"},
                {"x": 150, "y": 140, "type": "farm"},
                {"x": 380, "y": 400, "type": "park"},
            ],
        },
    },
    {
        "name": "Riverside Village Development",
        "topic": "village development",
        "title": "Riverside Village Development (2000-2020)",
        "prompt": (
            "The map below shows the development of Riverside village "
            "from 2000 to 2020. Summarise the information by selecting "
            "and reporting the main features, and make comparisons where "
            "relevant."
        ),
        "chart_type": "map",
        "data": {
            "title": "Riverside Village Development (2000-2020)",
            "before": [
                "Farmland (north)", "Woodland (south)", "River (east)",
                "Village centre (west)", "Small shops (north-west)",
            ],
            "after": [
                "Housing estate (north)", "Woodland (south)", "River (east)",
                "Village centre (west)", "Supermarket (north-west)",
            ],
            "changes": [
                "Farmland converted to Housing estate",
                "Woodland remained unchanged",
                "River remained unchanged",
                "Village centre expanded",
                "Small shops replaced by Supermarket",
            ],
            "positions_before": [
                {"x": 260, "y": 120, "type": "farm"},
                {"x": 260, "y": 460, "type": "forest"},
                {"x": 440, "y": 280, "type": "water"},
                {"x": 120, "y": 280, "type": "building"},
                {"x": 160, "y": 140, "type": "shop"},
            ],
            "positions_after": [
                {"x": 260, "y": 120, "type": "housing"},
                {"x": 260, "y": 460, "type": "forest"},
                {"x": 440, "y": 280, "type": "water"},
                {"x": 120, "y": 280, "type": "building"},
                {"x": 160, "y": 140, "type": "supermarket"},
            ],
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
# MAIN CLASS
# ═══════════════════════════════════════════════════════════════════
class PromptsLibrary:
    """
    IELTS Writing Prompt library (v3.1, AI-only).

    Every prompt comes from the AI engine. No fallback templates.
    Failed generations are retried up to 3 times, then raise
    PromptGenerationError.

    Backward-compatible with v1.0 / v2.0 / v3.0 method signatures.
    """

    def __init__(self, ai_engine=None):
        if not ai_engine:
            raise ValueError("AI Engine is required.")

        self.ai = ai_engine
        self._cache: Dict[str, List[Dict]] = {}

        logger.info("[PromptsLibrary] Initialized (v3.1 — AI-only)")
        logger.info(
            f"[PromptsLibrary] Loaded {len(CUSTOM_MAP_TEMPLATES)} "
            f"hand-crafted map templates"
        )

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════
    def _detect_topic_category(self, topic: str) -> str:
        if not topic:
            return random.choice(list(TOPIC_CATEGORIES.keys()))
        topic_lower = topic.lower()
        for category, keywords in TOPIC_CATEGORIES.items():
            for keyword in keywords:
                if keyword in topic_lower:
                    return category
        return random.choice(list(TOPIC_CATEGORIES.keys()))

    # ─── Robust JSON extractor (object OR array) ─────────────────
    def _extract_json(self, response: str) -> Union[Dict, List]:
        """
        Extract JSON from an AI response.

        • Strips markdown ```json fences
        • Handles BOTH {...} objects and [...] arrays
        • Ignores preamble / trailing text
        • Respects string boundaries (won't be fooled by { inside strings)
        """
        if not response:
            raise ValueError("Empty AI response")

        response = response.strip()

        # Remove markdown fences
        response = re.sub(r'^```(?:json)?\s*', '', response)
        response = re.sub(r'\s*```\s*$', '', response)
        response = response.strip()

        for opener, closer in (('{', '}'), ('[', ']')):
            start = response.find(opener)
            if start == -1:
                continue

            depth = 0
            end = -1
            in_string = False
            escape_next = False

            for i in range(start, len(response)):
                ch = response[i]

                if escape_next:
                    escape_next = False
                    continue

                if ch == '\\':
                    escape_next = True
                    continue

                if ch == '"':
                    in_string = not in_string
                    continue

                if in_string:
                    continue

                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

            if end == -1:
                continue

            candidate = response[start:end]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                logger.debug(
                    f"[PromptsLibrary] JSON decode failed on {opener}: {e}"
                )
                continue

        raise ValueError("No valid JSON object or array found in response")

    # ─── chart_data validator ────────────────────────────────────
    def _validate_chart_data(self, chart_type: str, chart_data: Dict) -> bool:
        """Return True if chart_data has everything the renderer needs."""
        if not isinstance(chart_data, dict):
            return False

        if chart_type in ('bar_chart', 'line_graph'):
            labels = chart_data.get('labels') or []
            datasets = chart_data.get('datasets') or []
            if not labels or not datasets:
                return False
            if not isinstance(datasets, list):
                return False
            for ds in datasets:
                if not isinstance(ds, dict):
                    return False
                if not ds.get('values'):
                    return False
            return True

        if chart_type == 'pie_chart':
            labels = chart_data.get('labels') or []
            values = chart_data.get('values') or []
            return bool(labels and values and len(labels) == len(values))

        if chart_type == 'table':
            headers = chart_data.get('headers') or []
            rows = chart_data.get('rows') or []
            return bool(rows and (headers or rows))

        if chart_type == 'map':
            return bool(chart_data.get('before') and chart_data.get('after'))

        if chart_type == 'diagram':
            return bool(chart_data.get('steps'))

        if chart_type == 'flow_chart':
            return bool(chart_data.get('stages') or chart_data.get('steps'))

        return False

    # ─── Generic retry wrapper ───────────────────────────────────
    def _call_ai_with_retry(
        self,
        build_prompt,
        parse_and_validate,
        context: str,
    ):
        """
        Call the AI up to MAX_RETRIES times.

        Args:
            build_prompt(attempt_idx) → str : returns the prompt for attempt
            parse_and_validate(text) → Any : parses + validates the response;
                                              should raise on failure
            context: str for logging

        Raises:
            PromptGenerationError if all retries fail.
        """
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            delay = RETRY_DELAYS[attempt]
            if delay > 0:
                logger.info(
                    f"[PromptsLibrary] Retry {attempt + 1}/{MAX_RETRIES} "
                    f"for {context} in {delay}s..."
                )
                time.sleep(delay)

            temperature = RETRY_TEMPERATURES[attempt]

            try:
                prompt = build_prompt(attempt)
                response = self.ai.generate(
                    prompt,
                    max_tokens=900 if 'task1' in context else 500,
                    temperature=temperature,
                )
                if not response:
                    raise ValueError("AI returned empty response")

                result = parse_and_validate(response)
                if result is not None:
                    return result
                raise ValueError("Parsed result was None")

            except Exception as e:
                last_error = e
                logger.warning(
                    f"[PromptsLibrary] Attempt {attempt + 1}/{MAX_RETRIES} "
                    f"failed for {context}: {e}"
                )

        raise PromptGenerationError(
            f"AI failed after {MAX_RETRIES} attempts for {context}: "
            f"{last_error}"
        )

    # ═══════════════════════════════════════════════════════════════
    # TOPIC GENERATION (Task 2 helper)
    # ═══════════════════════════════════════════════════════════════
    def generate_random_topic(self, difficulty: str = "medium") -> str:
        """Generate a short Task 2 topic phrase (3-5 words)."""

        def build_prompt(_attempt: int) -> str:
            return (
                f"Generate a random IELTS Task 2 essay topic for "
                f"{difficulty} level.\n"
                f"Just output a short topic phrase (3-5 words), nothing else."
            )

        def parse_and_validate(response: str) -> Optional[str]:
            topic = (response or '').strip().strip('"').strip()
            if topic and 2 < len(topic) < 60:
                topic = topic.rstrip('.').strip()
                if topic:
                    return topic
            return None

        return self._call_ai_with_retry(
            build_prompt=build_prompt,
            parse_and_validate=parse_and_validate,
            context='task2_topic',
        )

    # ═══════════════════════════════════════════════════════════════
    # TASK 1
    # ═══════════════════════════════════════════════════════════════
    def get_random_task1(
        self,
        chart_type: Optional[str] = None,
        difficulty: Optional[str] = None,
        force_new: bool = False,
    ) -> Dict:
        """
        Return a Task 1 prompt + chart_data.

        For `map` chart_type:
          • 60% chance → hand-crafted CUSTOM_MAP_TEMPLATES (still
            treated as first-class content, not a fallback)
          • 40% chance → AI-generated map

        For all other chart types: 100% AI-generated.
        """
        if not chart_type:
            chart_type = random.choice(CHART_TYPES)
        if not difficulty:
            difficulty = random.choice(DIFFICULTY_LEVELS)

        # ── Map: prefer hand-crafted templates sometimes ──────
        if chart_type == 'map' and CUSTOM_MAP_TEMPLATES:
            if random.random() < 0.6:
                template = random.choice(CUSTOM_MAP_TEMPLATES)
                logger.info(
                    f"[PromptsLibrary] Using custom map: {template['name']}"
                )
                return {
                    'topic': template['topic'],
                    'prompt': template['prompt'],
                    'difficulty': difficulty,
                    'chart_type': 'map',
                    'chart_data': dict(template['data']),
                    'title': template['title'],
                    'source': 'custom_map',
                }
            # else fall through to AI-generated map

        # ── Cache rotation ────────────────────────────────────
        cache_key = f"task1_{chart_type}_{difficulty}"
        if not force_new:
            cached_list = self._cache.get(cache_key) or []
            if cached_list:
                result = random.choice(cached_list)
                logger.debug(
                    f"[PromptsLibrary] Task1 cache HIT "
                    f"({cache_key}, {len(cached_list)} versions)"
                )
                return dict(result)

        # ── AI generation with retries ────────────────────────
        result = self._generate_task1_prompt(chart_type, difficulty)

        versions = self._cache.setdefault(cache_key, [])
        versions.append(result)
        if len(versions) > CACHE_VERSIONS_PER_KEY:
            versions.pop(0)

        return dict(result)

    def _generate_task1_prompt(self, chart_type: str, difficulty: str) -> Dict:
        """
        AI-only Task 1 generation with retry. Raises PromptGenerationError
        if all retries fail.
        """
        chart_hints = {
            'bar_chart': {
                'desc': 'compares categories using vertical bars',
                'shape': 'labels (5-8 categories) + 1-2 datasets',
            },
            'line_graph': {
                'desc': 'shows trends over time',
                'shape': 'labels (5-8 years) + 1-3 datasets',
            },
            'pie_chart': {
                'desc': 'shows proportions of a whole',
                'shape': 'labels + values (no datasets)',
            },
            'table': {
                'desc': 'presents data in rows and columns',
                'shape': 'headers + rows',
            },
            'map': {
                'desc': 'shows a place that changed over time',
                'shape': 'before + after + changes',
            },
            'diagram': {
                'desc': 'illustrates a linear process',
                'shape': 'steps (4-6) + descriptions',
            },
            'flow_chart': {
                'desc': 'shows stages in a sequence',
                'shape': 'stages (4-6) + descriptions',
            },
        }
        hint = chart_hints.get(
            chart_type, {'desc': 'shows data', 'shape': 'labels + values'}
        )

        def build_prompt(attempt: int) -> str:
            variety_hint = ''
            if attempt == 1:
                variety_hint = (
                    "\n\nNOTE: A previous attempt failed validation. "
                    "Please make sure the chart_data field is COMPLETE and "
                    "matches the exact shape described above."
                )
            elif attempt >= 2:
                variety_hint = (
                    "\n\nNOTE: Two previous attempts failed. Use a DIFFERENT "
                    "topic and make sure chart_data is EXACTLY the shape "
                    "described above with realistic numbers."
                )

            return f"""You are an IELTS Writing Task 1 question writer.

Generate ONE authentic task with REAL data.

**Chart type:** {chart_type}
**What it does:** {hint['desc']}
**Difficulty:** {difficulty}
**Required chart_data shape:** {hint['shape']}{variety_hint}

Return ONLY a valid JSON object with this EXACT shape:

{{
  "topic": "2-3 word topic",
  "prompt": "The {chart_type.replace('_', ' ')} below shows [specific content]. Summarise the information by selecting and reporting the main features, and make comparisons where relevant.",
  "chart_data": {{ ... see requirements below ... }}
}}

**chart_data requirements for {chart_type}:**
- For bar_chart / line_graph:
    "chart_data": {{
      "title": "...",
      "labels": ["A", "B", "C", "D", "E"],
      "datasets": [
        {{"label": "Series 1", "values": [10.5, 20.3, 15.2, 18.7, 22.1]}}
      ]
    }}
- For pie_chart:
    "chart_data": {{
      "title": "...",
      "labels": ["X", "Y", "Z"],
      "values": [45, 30, 25]
    }}
- For table:
    "chart_data": {{
      "title": "...",
      "headers": ["Column A", "Column B"],
      "rows": [["row1a", 100], ["row1b", 200]]
    }}
- For map:
    "chart_data": {{
      "title": "...",
      "before": ["feature 1 (location)", "feature 2 (location)"],
      "after": ["feature 1' (location)", "feature 2' (location)"],
      "changes": ["description of change 1", "description of change 2"]
    }}
- For diagram / flow_chart:
    "chart_data": {{
      "title": "...",
      "steps": ["Step 1 ...", "Step 2 ...", "Step 3 ..."],
      "descriptions": ["detail about step 1", "detail about step 2", "detail about step 3"]
    }}

**CRITICAL RULES:**
- The prompt must be 40-60 words and end with "make comparisons where relevant."
- Use realistic numbers (no zeros, no placeholders)
- NO personal pronouns (I, you, we)
- Academic, neutral tone
- chart_data MUST match the exact shape above — this is validated

Return ONLY the JSON object. No markdown. No explanation."""

        def parse_and_validate(response: str) -> Optional[Dict]:
            parsed = self._extract_json(response)
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected dict, got {type(parsed).__name__}")

            chart_data = parsed.get('chart_data') or {}
            if not self._validate_chart_data(chart_type, chart_data):
                raise ValueError(
                    f"chart_data failed validation for {chart_type}: "
                    f"keys={list(chart_data.keys())}"
                )

            prompt_text = (parsed.get('prompt') or '').strip()
            if not prompt_text:
                raise ValueError("Missing prompt text")

            return {
                'topic': parsed.get('topic', 'data'),
                'prompt': prompt_text,
                'difficulty': difficulty,
                'chart_type': chart_type,
                'chart_data': chart_data,
                'source': 'ai',
            }

        return self._call_ai_with_retry(
            build_prompt=build_prompt,
            parse_and_validate=parse_and_validate,
            context=f'task1_{chart_type}',
        )

    # ═══════════════════════════════════════════════════════════════
    # TASK 2
    # ═══════════════════════════════════════════════════════════════
    def get_random_task2(
        self,
        topic: Optional[str] = None,
        difficulty: Optional[str] = None,
        question_type: Optional[str] = None,
        force_new: bool = False,
    ) -> Dict:
        """
        Return a Task 2 prompt (100% AI-generated).

        ✅ v3.1: If `question_type` is None, this method shuffles all
        question types and tries them in order — so a single failing
        type (e.g. `two_part_question`) can no longer cause the whole
        call to fail. If a specific `question_type` is given, only
        that type is attempted (caller wants a specific format).

        Raises PromptGenerationError only if every type fails.
        """
        if not difficulty:
            difficulty = random.choice(DIFFICULTY_LEVELS)

        if not topic:
            topic = self.generate_random_topic(difficulty)

        cache_key = f"task2_{difficulty}_{topic}_{question_type or 'any'}"

        if not force_new:
            cached_list = self._cache.get(cache_key) or []
            if cached_list:
                result = random.choice(cached_list)
                logger.debug(
                    f"[PromptsLibrary] Task2 cache HIT ({cache_key})"
                )
                return dict(result)

        result = self._generate_task2_prompt(topic, difficulty, question_type)

        versions = self._cache.setdefault(cache_key, [])
        versions.append(result)
        if len(versions) > CACHE_VERSIONS_PER_KEY:
            versions.pop(0)

        return dict(result)

    def _generate_task2_prompt(
        self,
        topic: str,
        difficulty: str,
        question_type: Optional[str] = None,
    ) -> Dict:
        """
        AI-only Task 2 generation with retry.

        ✅ v3.1 — When `question_type` is None, we shuffle all 5 types
        and try each in order. Each type gets its own retry budget via
        `_call_ai_with_retry`. This means one stubborn type (e.g.
        `two_part_question` producing short prompts) can no longer
        cause the whole request to fail with a 503.
        """
        # All available Task 2 question formats
        formats = [
            {
                'type': 'discuss_both_views',
                'instruction': 'Discuss both views and give your own opinion.',
                'description': (
                    'Present two opposing viewpoints on an issue, '
                    'then state your own position.'
                ),
            },
            {
                'type': 'agree_disagree',
                'instruction': 'To what extent do you agree or disagree?',
                'description': 'Take a stance on a statement and defend it.',
            },
            {
                'type': 'advantages_disadvantages',
                'instruction': 'Do the advantages outweigh the disadvantages?',
                'description': (
                    'Weigh both sides of a trend or development, then conclude.'
                ),
            },
            {
                'type': 'problem_solution',
                'instruction': (
                    'What are the causes, and what solutions can you suggest?'
                ),
                'description': (
                    'Analyse root causes of a problem, then propose '
                    'practical solutions.'
                ),
            },
            {
                'type': 'two_part_question',
                'instruction': 'Answer both questions.',
                'description': (
                    'Two related questions (e.g. "Why is this happening? '
                    'Is this positive or negative?").'
                ),
            },
        ]

        # ✅ v3.1 — Which types to try, and in what order?
        if question_type:
            # Caller asked for a specific type — only try that one
            ordered = [f for f in formats if f['type'] == question_type]
            if not ordered:
                # Unknown type → fall back to trying all
                ordered = list(formats)
                random.shuffle(ordered)
        else:
            # Try all types, shuffled, until one succeeds
            ordered = list(formats)
            random.shuffle(ordered)

        last_error: Optional[Exception] = None

        for chosen in ordered:
            try:
                return self._try_task2_type(chosen, topic, difficulty)
            except PromptGenerationError as e:
                last_error = e
                logger.warning(
                    f"[PromptsLibrary] Task 2 type '{chosen['type']}' "
                    f"failed, trying next type..."
                )
                continue

        # Every type failed — this is genuinely rare
        raise PromptGenerationError(
            f"All Task 2 question types failed for topic='{topic}', "
            f"difficulty='{difficulty}'. Last error: {last_error}"
        )

    def _try_task2_type(self, chosen: Dict, topic: str, difficulty: str) -> Dict:
        """Generate + validate a Task 2 prompt for ONE specific type."""

        def build_prompt(attempt: int) -> str:
            variety_hint = ''
            if attempt == 1:
                variety_hint = (
                    "\n\nNOTE: The previous attempt was rejected. Ensure the "
                    "question is 25-80 words and ends with the exact "
                    "instruction provided."
                )
            elif attempt >= 2:
                variety_hint = (
                    "\n\nNOTE: Two previous attempts were rejected. Try a "
                    "DIFFERENT angle on this topic while keeping the exact "
                    "instruction at the end. 25-80 words strictly."
                )

            return f"""You are an expert IELTS Writing Task 2 question writer.

Generate ONE authentic Task 2 question.

**Topic:** {topic}
**Question type:** {chosen['type']} — {chosen['description']}
**Required instruction:** "{chosen['instruction']}"
**Difficulty:** {difficulty}{variety_hint}

**CRITICAL RULES:**
1. Total length: 25-80 words (including the instruction)
2. The question MUST end with: "{chosen['instruction']}"
3. Tone: academic, neutral, formal
4. NO personal pronouns (I, you, we, our)
5. NO emotional or loaded language
6. Must be debatable — multiple valid viewpoints exist
7. Must be specific enough to write a 250-word essay

**STYLE EXAMPLES (from real IELTS):**

Example A (discuss both views):
"Some people believe that children should start school as early as possible, while others think they should start at the age of seven. Discuss both views and give your own opinion."

Example B (agree/disagree):
"Some people think that governments should spend more money on public transportation than on new roads. To what extent do you agree or disagree?"

Example C (problem/solution):
"In many cities, traffic congestion is becoming a serious problem. What are the causes of this problem, and what measures can be taken to solve it?"

**Return ONLY a valid JSON object:**
{{
  "topic": "{topic}",
  "prompt": "the complete 25-80 word question ending with the required instruction",
  "question_type": "{chosen['type']}"
}}

Return ONLY the JSON object. No markdown. No explanation. No extra text."""

        def parse_and_validate(response: str) -> Optional[Dict]:
            parsed = self._extract_json(response)
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected dict, got {type(parsed).__name__}")

            q = (parsed.get('prompt') or '').strip()
            if not q:
                raise ValueError("Empty Task 2 prompt")

            wc = len(q.split())
            # ✅ v3.1 — relaxed range (was 30-80, now 25-80)
            if wc < TASK2_MIN_WORDS or wc > TASK2_MAX_WORDS:
                raise ValueError(
                    f"Task 2 word count out of range: {wc} "
                    f"(expected {TASK2_MIN_WORDS}-{TASK2_MAX_WORDS})"
                )

            return {
                'topic': parsed.get('topic', topic),
                'prompt': q,
                'difficulty': difficulty,
                'question_type': parsed.get('question_type', chosen['type']),
                'source': 'ai',
            }

        return self._call_ai_with_retry(
            build_prompt=build_prompt,
            parse_and_validate=parse_and_validate,
            context=f'task2_{chosen["type"]}',
        )

    # ═══════════════════════════════════════════════════════════════
    # INTROSPECTION
    # ═══════════════════════════════════════════════════════════════
    def get_all_chart_types(self) -> List[str]:
        return CHART_TYPES.copy()

    def get_statistics(self) -> Dict:
        return {
            'mode': 'v3_1_ai_only',
            'custom_maps': len(CUSTOM_MAP_TEMPLATES),
            'max_retries': MAX_RETRIES,
            'retry_delays_seconds': RETRY_DELAYS,
            'task2_word_range': [TASK2_MIN_WORDS, TASK2_MAX_WORDS],
            'cache_keys': len(self._cache),
            'cache_versions_per_key': CACHE_VERSIONS_PER_KEY,
        }


# ═══════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════
def create_prompts_library(ai_engine=None) -> PromptsLibrary:
    if not ai_engine:
        raise ValueError("AI Engine required")
    return PromptsLibrary(ai_engine)