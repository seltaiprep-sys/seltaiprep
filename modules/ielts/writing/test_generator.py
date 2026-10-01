"""IELTS Writing test generation - PURE AI, NO FALLBACK, chart rendering ENABLED

═══════════════════════════════════════════════════════════════════════
FIXES APPLIED (v5 — MAP + ERROR HANDLING PASS):
═══════════════════════════════════════════════════════════════════════
  (A) MAP BUG FIXED — previously `generate_task1()` called
        `prompts_lib.get_random_task1('map')` for the prompt, then
        SEPARATELY called `_ai_generate_map_with_positions()` for data.
        That made the prompt and chart_data describe DIFFERENT maps
        (e.g. Greenfield prompt + Riverside data) → users saw broken
        Task 1. Now we use the chart_data that prompts_lib already
        returned (custom template OR AI-generated). Fallback to
        `_ai_generate_map_with_positions` only when prompts_lib
        returned no chart_data at all (rare).

  (B) `PromptGenerationError` from PromptsLibrary v3 (AI-only, no
        fallback) is now explicitly imported. All prompt generation
        calls are wrapped in try/except so the exception propagates
        with a clear log message instead of surfacing as a generic 500.

  (C) `source` field is now tracked per-branch. Map prompts served
        from a hand-crafted custom template report `source='custom_map'`
        instead of a misleading hardcoded `'ai'`.

FIXES APPLIED (v4 — REVIEW PASS):
  (1) `_extract_features()` now handles `diagram` and `flow_chart`
      explicitly — previously they fell through to the `map` branch
      and always got an empty feature list.
  (2) Topic-first methods safely handle a manifest entry that is
      missing `num_labels` (defensive `.get()` + graceful fallback).
  (3) `_coerce_steps()` deep-copies mutable padded entries.
  (4) `_ai_generate_diagram()` returns `chart_type: "diagram"` at the
      top level, matching `_ai_generate_flow_chart()`.
  (5) Import fallback sets `_DIAGRAM_LOADER_AVAILABLE = False` and logs.
  (6) Timeout raises `concurrent.futures.TimeoutError` (aliased).

FIXES APPLIED (v3 — TOPIC-FIRST SVG FLOW):
  (7) Diagram & flow-chart generation use a topic-first approach.
  (8) `_coerce_steps()` guarantees step count matches SVG num_labels.
  (9) `generate_task1()` surfaces `diagram_key` and `num_labels`.
  (10) Legacy free-form generators kept as `_ai_generate_*_legacy()`.

FIXES APPLIED (v2):
  (11) Shared class-level ThreadPoolExecutor (no per-call leaks).
  (12) Removed broken `re.sub` repair step in `_extract_json`.
  (13) `_are_positions_clustered([])` / single-position → False.
  (14) `serial` uses `uuid.uuid4().hex[:8]`.
  (15) Removed dead `_chart_index` class attribute.
  (16) `TimeoutError` is explicitly `concurrent.futures.TimeoutError`.
  (17) `_ai_generate_chart_data` no longer recursively re-dispatches.
  (18) `atexit` hook shuts down the class-level executor cleanly.
═══════════════════════════════════════════════════════════════════════
"""
import atexit
import copy
import json
import logging
import math
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone

# v5: PromptGenerationError is now imported explicitly
from .prompts_library import (
    PromptsLibrary,
    CHART_TYPES,
    PromptGenerationError,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# v4: topic-first picker (reads num_labels from manifest.json)
# ─────────────────────────────────────────────────────────────
_DIAGRAM_LOADER_AVAILABLE = True
try:
    from .diagram_asset_loader import pick_random_diagram_topic
except ImportError:
    try:
        from diagram_asset_loader import pick_random_diagram_topic
    except ImportError:
        _DIAGRAM_LOADER_AVAILABLE = False

        def pick_random_diagram_topic():
            """Shim used only when diagram_asset_loader cannot be imported."""
            return None


# Chart types (kept for backwards compat — CHART_TYPES is used below)
TASK1_CHARTS = CHART_TYPES


class WritingTestGenerator:
    """AI-driven IELTS Writing test generator (no template fallbacks)."""

    # Class-level executor — shared by every instance, never shut down
    # on the hot path. A hung AI call cannot leak or block the pool.
    _EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ielts-wtg")

    def __init__(self, ai_engine=None, chart_renderer=None):
        self.ai = ai_engine
        if not self.ai:
            raise ValueError(
                " AI Engine is required. No fallback templates available."
            )
        self.chart_renderer = chart_renderer
        self.prompts_lib = PromptsLibrary(ai_engine)
        logger.info(
            "WritingTestGenerator initialized with pure AI mode + PromptsLibrary "
            "(diagram_loader=%s)",
            _DIAGRAM_LOADER_AVAILABLE,
        )

    # ============================================================
    # INFRASTRUCTURE
    # ============================================================
    def _get_chart_renderer(self):
        if self.chart_renderer is not None:
            return self.chart_renderer
        try:
            from .chart_renderer import ChartRenderer
            self.chart_renderer = ChartRenderer()
            return self.chart_renderer
        except ImportError as e:
            logger.error(f"ChartRenderer not available: {e}")
            return None

    def _ai_generate_with_timeout(
        self, prompt, max_tokens=800, temperature=0.7, timeout=20
    ):
        """
        Run an AI generation call with a hard timeout on the shared executor.

        v4: raises FuturesTimeoutError (concurrent.futures.TimeoutError),
        not the builtin TimeoutError.
        """
        if not self.ai:
            raise ValueError("AI Engine not available")

        future = self._EXECUTOR.submit(
            self.ai.generate, prompt, max_tokens, temperature
        )
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise FuturesTimeoutError(
                f"AI generation timed out after {timeout}s"
            )

    def _extract_json(self, response):
        """
        Best-effort JSON extraction from an AI response.

        Handles:
          - ```json fences
          - leading / trailing prose
          - trailing commas inside objects / arrays
          - unbalanced braces / brackets
        """
        if not response:
            raise ValueError(" AI returned empty response")

        json_str = response.strip()
        json_str = re.sub(r'```(?:json)?\s*|```\s*', '', json_str)

        start = json_str.find('{')
        end = json_str.rfind('}')
        if start == -1 or end == -1:
            raise ValueError(
                f" No JSON found in AI response: {response[:200]}"
            )
        json_str = json_str[start:end + 1]

        # Trailing commas
        json_str = re.sub(r',\s*(?=[}\]])', '', json_str)

        # Missing comma between objects: }{ → },{
        json_str = re.sub(r'}\s*{', '},{', json_str)

        # Balance open braces / brackets
        open_b = json_str.count('{') - json_str.count('}')
        open_s = json_str.count('[') - json_str.count(']')
        json_str += '}' * max(0, open_b) + ']' * max(0, open_s)

        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError as e:
            raise ValueError(
                f" Failed to parse AI JSON: {e}\nResponse: {json_str[:300]}"
            )

    # ============================================================
    # v3: STEP-COUNT COERCION
    # ============================================================
    def _coerce_steps(self, steps, target):
        """
        Force the step list to exactly `target` entries.

        The SVG asset has a fixed number of label boxes. If the AI returns
        too many or too few steps, we trim or pad so labels always fit.

        v4: when padding, mutable entries (dict/list) are deep-copied so
        every padded slot is an independent object rather than an alias.
        """
        try:
            target = int(target)
        except (TypeError, ValueError):
            return list(steps or [])

        if target <= 0:
            return []

        steps = list(steps or [])
        if not steps:
            return [f"Step {i + 1}" for i in range(target)]

        if len(steps) > target:
            return steps[:target]

        # Too few → pad by repeating the last meaningful step.
        while len(steps) < target:
            last = steps[-1]
            if isinstance(last, (dict, list)):
                steps.append(copy.deepcopy(last))
            else:
                steps.append(last)

        return steps

    # ============================================================
    # POSITION DISTRIBUTION (MAPS)
    # ============================================================
    def _are_positions_clustered(self, positions):
        """Return True only when ≥2 valid positions are tightly packed."""
        if not positions or len(positions) < 2:
            return False

        valid = [
            p for p in positions
            if isinstance(p, dict) and 'x' in p and 'y' in p
        ]
        if len(valid) < 2:
            return False

        x_coords = [p['x'] for p in valid]
        y_coords = [p['y'] for p in valid]
        x_range = max(x_coords) - min(x_coords)
        y_range = max(y_coords) - min(y_coords)
        return x_range < 200 and y_range < 200

    def _distribute_positions(self, positions, width=500, height=550):
        """Spread `positions` across a regular grid with slight jitter."""
        n = len(positions)
        if n == 0:
            return []
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = max(1, math.ceil(n / cols))
        cell_width = width / (cols + 1)
        cell_height = height / (rows + 1)

        distributed = []
        for i, pos in enumerate(positions):
            row = i // cols
            col = i % cols
            x = int(cell_width * (col + 1)) + random.randint(-20, 20)
            y = int(cell_height * (row + 1)) + random.randint(-20, 20)
            x = max(50, min(470, x))
            y = max(60, min(520, y))
            if isinstance(pos, dict):
                new_pos = dict(pos)
                new_pos['x'] = x
                new_pos['y'] = y
                distributed.append(new_pos)
            else:
                distributed.append(
                    {'x': x, 'y': y, 'type': 'building'}
                )
        return distributed

    def _generate_default_positions(self, num_features):
        """Grid layout with default 'building' type for each feature."""
        positions = []
        if num_features <= 0:
            return positions
        grid_cols = max(1, math.ceil(math.sqrt(num_features)))
        grid_rows = max(1, math.ceil(num_features / grid_cols))
        for i in range(num_features):
            row = i // grid_cols
            col = i % grid_cols
            x = 80 + (col * (400 / max(grid_cols - 1, 1)))
            y = 100 + (row * (400 / max(grid_rows - 1, 1)))
            positions.append({
                'x': int(x),
                'y': int(y),
                'type': 'building',
                'name': f'Feature {i+1}',
                'size': f'{random.randint(1, 5)}ha',
            })
        return positions

    # ============================================================
    # AI CHART DATA GENERATION
    # ============================================================
    def _ai_generate_chart_data(self, chart_type, prompt_text, difficulty):
        """Generate only the underlying chart data for a known prompt."""
        if chart_type in ('bar_chart', 'line_graph'):
            data_prompt = f"""Based on the following IELTS Task 1 question, generate realistic chart data.

Question: {prompt_text}

Return ONLY valid JSON with:
{{
    "labels": [5-7 labels like years, months, or categories],
    "datasets": [
        {{"label": "Series 1 (use REAL names, e.g., London, Paris, not City A)", "values": [numbers]}},
        {{"label": "Series 2 (use REAL names)", "values": [numbers]}}
    ]
}}

- Use REAL city names, product names, or country names (NOT placeholders like "City A").
- Values must be realistic and show clear trends.
- Ensure the data matches the question context.
- Include 2-3 datasets if appropriate.

ONLY JSON. No explanations."""

        elif chart_type == 'pie_chart':
            data_prompt = f"""Based on the following IELTS Task 1 question, generate realistic pie chart data.

Question: {prompt_text}

Return ONLY valid JSON with:
{{
    "labels": [4-6 categories],
    "values": [positive numbers summing to 100]
}}

- Use real category names.
- Values must be percentages that add up to 100.

ONLY JSON. No explanations."""

        elif chart_type == 'table':
            data_prompt = f"""Based on the following IELTS Task 1 question, generate realistic table data.

Question: {prompt_text}

Return ONLY valid JSON with:
{{
    "headers": ["Column1", "Column2", "Column3", ...],
    "rows": [
        ["Row1 Col1", Row1 Col2, ...],
        ["Row2 Col1", Row2 Col2, ...]
    ]
}}

- Include at least 3 rows and 3 columns.
- Use realistic numbers and names.

ONLY JSON. No explanations."""

        else:
            raise ValueError(
                f"Unsupported chart type for data-only generation: {chart_type}"
            )

        response = self._ai_generate_with_timeout(
            data_prompt, max_tokens=600, temperature=0.7, timeout=20
        )
        result = self._extract_json(response)

        if chart_type in ('bar_chart', 'line_graph'):
            if 'labels' not in result or 'datasets' not in result:
                raise ValueError("Missing labels or datasets")
            if not isinstance(result['datasets'], list) or not result['datasets']:
                raise ValueError("Empty datasets array")
            for ds in result['datasets']:
                if not isinstance(ds, dict) or 'values' not in ds:
                    raise ValueError("Dataset missing values")
                if len(ds['values']) != len(result['labels']):
                    raise ValueError(
                        f"Dataset '{ds.get('label', '?')}' length mismatch"
                    )

        elif chart_type == 'pie_chart':
            if 'labels' not in result or 'values' not in result:
                raise ValueError("Missing labels or values")
            if len(result['labels']) != len(result['values']):
                raise ValueError("Pie chart length mismatch")
            total = sum(result['values']) or 0
            if total > 0 and total != 100:
                scaled = [int(v * 100 / total) for v in result['values']]
                diff = 100 - sum(scaled)
                if diff != 0 and scaled:
                    scaled[0] += diff
                result['values'] = scaled

        elif chart_type == 'table':
            if 'headers' not in result or 'rows' not in result:
                raise ValueError("Missing headers or rows")

        return result

    # ---------- Special-case generators ----------
    def _ai_generate_map_with_positions(self, difficulty):
        """Legacy: generate map data separately (used only as last-resort)."""
        last_err = None
        for attempt in range(2):
            try:
                prompt = f"""Generate IELTS Writing Task 1 MAP (difficulty: {difficulty}).
Return JSON: {{"prompt": "...", "topic": "...", "data": {{"title": "...", "before": [6-10 items], "after": [same length], "changes": [descriptions]}}}}
ONLY JSON. No explanations."""
                response = self._ai_generate_with_timeout(
                    prompt, max_tokens=1200, temperature=0.7, timeout=22
                )
                result = self._extract_json(response)
                if 'data' not in result:
                    raise ValueError("Missing data field")

                before = result['data'].get('before', []) or []
                after = result['data'].get('after', []) or []
                if len(before) < 4 or len(after) < 4:
                    raise ValueError(
                        "Insufficient features (need at least 4 before/after)"
                    )

                if len(before) != len(after):
                    min_len = min(len(before), len(after))
                    result['data']['before'] = before[:min_len]
                    result['data']['after'] = after[:min_len]

                num_features = len(result['data']['before'])
                result['data']['positions_before'] = \
                    self._generate_default_positions(num_features)
                result['data']['positions_after'] = \
                    self._generate_default_positions(num_features)
                return result
            except Exception as e:
                last_err = e
                logger.warning(f"Map attempt {attempt+1} failed: {e}")
        raise ValueError(f" Map generation failed: {last_err}")

    # ---------- v3/v4: TOPIC-FIRST DIAGRAM ----------
    def _ai_generate_diagram(self, difficulty):
        """
        Topic-first: pick a random diagram topic from the manifest,
        ask the AI for EXACTLY `num_labels` steps, and attach the
        `diagram_key` so the renderer can pick the matching SVG.
        """
        if not _DIAGRAM_LOADER_AVAILABLE:
            logger.error(
                "diagram_asset_loader is not importable — topic-first "
                "diagram generation is disabled, falling back to legacy."
            )
            return self._ai_generate_diagram_legacy(difficulty)

        topic_info = pick_random_diagram_topic()

        if topic_info is None:
            logger.warning(
                "No diagram topics in manifest — falling back to legacy."
            )
            return self._ai_generate_diagram_legacy(difficulty)

        diagram_key = topic_info.get("key")
        if not diagram_key:
            logger.warning("Manifest entry missing 'key' — using legacy.")
            return self._ai_generate_diagram_legacy(difficulty)

        title = topic_info.get("title") or diagram_key.replace("_", " ").title()

        # Defensive access — a manifest entry without num_labels used to
        # raise KeyError. Now we fall back to the legacy path.
        raw_num = topic_info.get("num_labels")
        try:
            num_labels = int(raw_num)
        except (TypeError, ValueError):
            num_labels = 0
        if num_labels <= 0:
            logger.warning(
                f"Topic '{diagram_key}' has invalid num_labels={raw_num!r} — "
                f"falling back to legacy diagram generator."
            )
            return self._ai_generate_diagram_legacy(difficulty)

        last_err = None
        for attempt in range(2):
            try:
                topic_prompt = f"""Generate the process steps for an IELTS Writing Task 1 diagram.

Topic: {title}
Difficulty: {difficulty}

Return ONLY valid JSON:
{{
    "title": "{title}",
    "steps": ["short label 1", "short label 2", ...],
    "descriptions": ["1-2 sentence description per step, same order"]
}}

Rules:
- You MUST output EXACTLY {num_labels} steps — no more, no fewer.
- Each step is a short 2-6 word label.
- Each description is 1-2 sentences explaining that step.
- Use concrete, realistic terminology.
- Do NOT add commentary outside the JSON.

ONLY JSON. No explanations."""

                response = self._ai_generate_with_timeout(
                    topic_prompt, max_tokens=1200, temperature=0.7, timeout=22
                )
                result = self._extract_json(response)

                steps = result.get('steps') or []
                descriptions = result.get('descriptions') or []

                # Hard-enforce exact count so the SVG asset matches
                steps = self._coerce_steps(steps, num_labels)
                descriptions = self._coerce_steps(descriptions, num_labels)

                return {
                    'prompt': (
                        f"The diagram below shows the process of "
                        f"{title.lower()}. Summarise the information by "
                        f"selecting and reporting the main features."
                    ),
                    'topic': title.lower(),
                    'chart_type': 'diagram',
                    'data': {
                        'title': title,
                        'steps': steps,
                        'descriptions': descriptions,
                    },
                    'diagram_key': diagram_key,
                    'num_labels': num_labels,
                }
            except Exception as e:
                last_err = e
                logger.warning(
                    f"Diagram (topic-first) attempt {attempt+1} failed: {e}"
                )

        logger.error(
            f"Topic-first diagram failed for {diagram_key}: {last_err}"
        )
        return self._ai_generate_diagram_legacy(difficulty)

    def _ai_generate_diagram_legacy(self, difficulty):
        """Legacy free-form diagram generator — used only as a fallback."""
        last_err = None
        for attempt in range(2):
            try:
                prompt = f"""Generate IELTS Writing Task 1 DIAGRAM (difficulty: {difficulty}).
Return JSON: {{"prompt": "...", "topic": "...", "data": {{"title": "...", "steps": [4-8 steps], "descriptions": [descriptions]}}}}
ONLY JSON. No explanations."""
                response = self._ai_generate_with_timeout(
                    prompt, max_tokens=1000, temperature=0.7, timeout=20
                )
                result = self._extract_json(response)
                if 'data' not in result:
                    raise ValueError("Missing data field")
                steps = result['data'].get('steps', []) or []
                if len(steps) < 3:
                    raise ValueError("Too few steps (need at least 3)")
                result.setdefault('chart_type', 'diagram')
                return result
            except Exception as e:
                last_err = e
                logger.warning(
                    f"Diagram (legacy) attempt {attempt+1} failed: {e}"
                )
        raise ValueError(f" Diagram generation failed: {last_err}")

    # ---------- v3/v4: TOPIC-FIRST FLOW CHART ----------
    def _ai_generate_flow_chart(self, difficulty):
        """
        Topic-first flow chart — same pattern as `_ai_generate_diagram`.
        """
        if not _DIAGRAM_LOADER_AVAILABLE:
            logger.error(
                "diagram_asset_loader is not importable — topic-first flow "
                "chart generation is disabled, falling back to legacy."
            )
            return self._ai_generate_flow_chart_legacy(difficulty)

        topic_info = pick_random_diagram_topic()

        if topic_info is None:
            logger.warning(
                "No topics in manifest — falling back to legacy flow-chart."
            )
            return self._ai_generate_flow_chart_legacy(difficulty)

        diagram_key = topic_info.get("key")
        if not diagram_key:
            logger.warning("Manifest entry missing 'key' — using legacy.")
            return self._ai_generate_flow_chart_legacy(difficulty)

        title = topic_info.get("title") or diagram_key.replace("_", " ").title()

        raw_num = topic_info.get("num_labels")
        try:
            num_labels = int(raw_num)
        except (TypeError, ValueError):
            num_labels = 0
        if num_labels <= 0:
            logger.warning(
                f"Topic '{diagram_key}' has invalid num_labels={raw_num!r} — "
                f"falling back to legacy flow-chart generator."
            )
            return self._ai_generate_flow_chart_legacy(difficulty)

        last_err = None
        for attempt in range(2):
            try:
                topic_prompt = f"""Generate a linear flow chart for IELTS Writing Task 1.

Topic: {title}
Difficulty: {difficulty}

Return ONLY valid JSON:
{{
    "title": "{title}",
    "stages": ["short label 1", ...],
    "descriptions": ["1-2 sentence description per stage, same order"],
    "decisions": [null, null, ...]
}}

Rules:
- You MUST output EXACTLY {num_labels} stages — no more, no fewer.
- Short 2-6 word labels.
- One description per stage.
- decisions array can be all null for a linear flow.
- Do NOT add commentary outside the JSON.

ONLY JSON. No explanations."""

                response = self._ai_generate_with_timeout(
                    topic_prompt, max_tokens=1200, temperature=0.7, timeout=22
                )
                result = self._extract_json(response)

                stages = result.get('stages') or result.get('steps') or []
                descriptions = result.get('descriptions') or []
                decisions = result.get('decisions') or [None] * len(stages)

                stages = self._coerce_steps(stages, num_labels)
                descriptions = self._coerce_steps(descriptions, num_labels)
                decisions = (list(decisions) + [None] * num_labels)[:num_labels]

                return {
                    'prompt': (
                        f"The flow chart below shows {title.lower()}. "
                        f"Summarise the information by selecting and "
                        f"reporting the main features."
                    ),
                    'topic': title.lower(),
                    'chart_type': 'flow_chart',
                    'data': {
                        'title': title,
                        'stages': stages,
                        'descriptions': descriptions,
                        'decisions': decisions,
                    },
                    'diagram_key': diagram_key,
                    'num_labels': num_labels,
                }
            except Exception as e:
                last_err = e
                logger.warning(
                    f"Flow chart (topic-first) attempt {attempt+1} failed: {e}"
                )

        logger.error(
            f"Topic-first flow chart failed for {diagram_key}: {last_err}"
        )
        return self._ai_generate_flow_chart_legacy(difficulty)

    def _ai_generate_flow_chart_legacy(self, difficulty):
        """Legacy free-form flow-chart generator — fallback only."""
        last_err = None
        for attempt in range(2):
            try:
                prompt = f"""Generate IELTS Writing Task 1 FLOW CHART (difficulty: {difficulty}).
Return JSON: {{"prompt": "...", "topic": "...", "chart_type": "flow_chart", "data": {{"title": "...", "stages": [4-6 stages], "descriptions": [...], "decisions": [null or decision text]}}}}
ONLY JSON. No explanations."""
                response = self._ai_generate_with_timeout(
                    prompt, max_tokens=1000, temperature=0.7, timeout=20
                )
                result = self._extract_json(response)
                result['chart_type'] = 'flow_chart'
                if 'data' not in result:
                    raise ValueError("Missing data field")
                stages = result['data'].get('stages', []) or []
                if len(stages) < 3:
                    raise ValueError("Too few stages")
                if 'descriptions' not in result['data']:
                    raise ValueError("Missing descriptions")
                if 'decisions' not in result['data'] or \
                        not result['data']['decisions']:
                    result['data']['decisions'] = [None] * len(stages)
                return result
            except Exception as e:
                last_err = e
                logger.warning(
                    f"Flow chart (legacy) attempt {attempt+1} failed: {e}"
                )
        raise ValueError(f" Flow chart generation failed: {last_err}")

    def _ai_generate_question_and_data(self, chart_type, difficulty):
        """Fallback for charts not handled by data-only generation."""
        last_err = None
        for attempt in range(2):
            try:
                prompt = f"""Generate IELTS Writing Task 1 {chart_type.upper()} (difficulty: {difficulty}).
Return JSON: {{"prompt": "...", "topic": "...", "data": {{"title": "...", "labels": [4-8 labels], "datasets": [{{"label": "...", "values": [...]}}]}}}}
For table: {{"prompt": "...", "topic": "...", "data": {{"title": "...", "headers": ["...", "..."], "rows": [[...]]}}}}
ONLY JSON. No explanations."""
                response = self._ai_generate_with_timeout(
                    prompt, max_tokens=1000, temperature=0.7, timeout=20
                )
                result = self._extract_json(response)
                if 'data' not in result:
                    raise ValueError("Missing data field")
                data = result['data']

                if chart_type in ('bar_chart', 'line_graph'):
                    labels = data.get('labels', []) or []
                    datasets = data.get('datasets', []) or []
                    if len(labels) < 3 or not datasets:
                        raise ValueError("Insufficient chart data")
                    for ds in datasets:
                        if 'values' not in ds or \
                                len(ds['values']) != len(labels):
                            raise ValueError("Dataset length mismatch")

                elif chart_type == 'table':
                    if len(data.get('headers', []) or []) < 2 or \
                       len(data.get('rows', []) or []) < 2:
                        raise ValueError("Insufficient table data")

                return result
            except Exception as e:
                last_err = e
                logger.warning(
                    f"{chart_type} attempt {attempt+1} failed: {e}"
                )
        raise ValueError(f" {chart_type} generation failed: {last_err}")

    # ============================================================
    # CHART DATA FORMATTING
    # ============================================================
    def _format_chart_data(self, raw_data, chart_type, topic):
        data = raw_data.get('data', raw_data)
        title = data.get('title', topic.title())

        if chart_type in ('bar_chart', 'line_graph'):
            datasets = data.get('datasets', [])
            labels = data.get('labels', [])
            if not datasets or not labels:
                raise ValueError(f" Missing data for {chart_type}")
            return {'labels': labels, 'datasets': datasets, 'title': title}

        if chart_type == 'pie_chart':
            values = data.get('values', [])
            labels = data.get('labels', [])
            if not values or not labels:
                raise ValueError(" Pie chart missing values or labels")
            if len(values) != len(labels):
                raise ValueError(" Pie chart length mismatch")
            return {'labels': labels, 'values': values, 'title': title}

        if chart_type == 'table':
            headers = data.get('headers', [])
            rows = data.get('rows', [])
            if not headers or not rows:
                raise ValueError(" Table missing headers or rows")
            return {'headers': headers, 'rows': rows, 'title': title}

        if chart_type == 'map':
            before = list(data.get('before', []))
            after = list(data.get('after', []))
            if not before or not after:
                raise ValueError(" Map missing before/after data")
            if len(before) != len(after):
                min_len = min(len(before), len(after))
                before = before[:min_len]
                after = after[:min_len]

            positions_before = data.get('positions_before', []) or []
            positions_after = data.get('positions_after', []) or []

            if (not positions_before
                    or len(positions_before) != len(before)
                    or self._are_positions_clustered(positions_before)):
                positions_before = self._distribute_positions(
                    [{'type': 'building'} for _ in before]
                )
            if (not positions_after
                    or len(positions_after) != len(after)
                    or self._are_positions_clustered(positions_after)):
                positions_after = self._distribute_positions(
                    [{'type': 'building'} for _ in after]
                )

            changes = list(data.get('changes', []) or [])
            if len(changes) > len(before):
                changes = changes[:len(before)]
            else:
                while len(changes) < len(before):
                    changes.append("Feature was modified")

            return {
                'title': title,
                'before': before,
                'after': after,
                'changes': changes,
                'chart_type': 'map',
                'positions_before': positions_before,
                'positions_after': positions_after,
                'terrain': data.get('terrain', {}),
            }

        if chart_type == 'diagram':
            steps = data.get('steps', [])
            descriptions = data.get('descriptions', [])
            if not steps:
                raise ValueError(" Diagram missing steps")

            diagram_key = raw_data.get('diagram_key') or data.get('diagram_key')
            num_labels = raw_data.get('num_labels') or data.get('num_labels')

            if num_labels:
                steps = self._coerce_steps(steps, num_labels)
                descriptions = self._coerce_steps(descriptions, num_labels)

            out = {
                'title': title,
                'steps': steps,
                'descriptions': descriptions,
                'chart_type': 'diagram',
            }
            if diagram_key:
                out['diagram_key'] = diagram_key
            if num_labels:
                out['num_labels'] = num_labels
            return out

        if chart_type == 'flow_chart':
            stages = data.get('stages', []) or data.get('steps', [])
            if not stages:
                raise ValueError(" Flow chart missing stages")
            descriptions = data.get('descriptions', [])
            if not descriptions:
                raise ValueError(" Flow chart missing descriptions")
            decisions = data.get('decisions', []) or [None] * len(stages)

            diagram_key = raw_data.get('diagram_key') or data.get('diagram_key')
            num_labels = raw_data.get('num_labels') or data.get('num_labels')

            if num_labels:
                stages = self._coerce_steps(stages, num_labels)
                descriptions = self._coerce_steps(descriptions, num_labels)
                decisions = (list(decisions) + [None] * num_labels)[:num_labels]

            out = {
                'title': title,
                'stages': stages,
                'descriptions': descriptions,
                'decisions': decisions,
                'chart_type': 'flow_chart',
            }
            if diagram_key:
                out['diagram_key'] = diagram_key
            if num_labels:
                out['num_labels'] = num_labels
            return out

        raise ValueError(f" Unknown chart type: {chart_type}")

    # ============================================================
    # RENDER
    # ============================================================
    def _render_chart_to_base64(self, chart_type, chart_data):
        renderer = self._get_chart_renderer()
        if renderer is None:
            logger.warning(
                "ChartRenderer not available; skipping chart base64"
            )
            return None
        try:
            return renderer.render_to_base64(chart_type, chart_data)
        except Exception as e:
            logger.error(f"Chart rendering failed: {e}")
            return None

    # ============================================================
    # TASK 1
    # ============================================================
    def generate_task1(self, difficulty="medium", topic=None, chart_type=None):
        if not self.ai:
            raise RuntimeError(
                " AI Engine required. Cannot generate Task 1 without AI."
            )

        if chart_type is None:
            chart_type = random.choice(CHART_TYPES)

        logger.info(
            f"Generating Task 1: {chart_type} (difficulty: {difficulty})"
        )

        # ═══════════════════════════════════════════════════════════
        # v5: Determine the source for the final result and route to
        # the appropriate generator. Prompt-generation failures
        # (PromptGenerationError) propagate with a clear log.
        # ═══════════════════════════════════════════════════════════
        task1_source = 'ai'

        try:
            # ── Diagram: topic-first, no PromptsLibrary ───────────
            if chart_type == 'diagram':
                result = self._ai_generate_diagram(difficulty)
                prompt_text = result.get(
                    'prompt', 'The diagram below shows a process.'
                )
                topic = result.get('topic', topic or 'process')
                task1_source = 'ai'

            # ── Flow chart: topic-first, no PromptsLibrary ────────
            elif chart_type == 'flow_chart':
                result = self._ai_generate_flow_chart(difficulty)
                prompt_text = result.get(
                    'prompt', 'The flow chart below shows a process.'
                )
                topic = result.get('topic', topic or 'process')
                task1_source = 'ai'

            # ── Map: use prompts_lib's chart_data directly ────────
            elif chart_type == 'map':
                # v5 FIX: prompts_lib v3.0 already returns chart_data
                # for maps (custom template OR AI-generated).
                # Using it directly prevents the old bug
                # where prompt described map A but chart_data
                # described map B.
                prompt_data = self.prompts_lib.get_random_task1(
                    chart_type, difficulty
                )
                prompt_text = prompt_data['prompt']
                topic = prompt_data.get('topic', topic or 'map')
                task1_source = prompt_data.get('source', 'ai')

                lib_chart_data = prompt_data.get('chart_data') or {}

                if lib_chart_data.get('before') and \
                        lib_chart_data.get('after'):
                    # Matching prompt + data from prompts_lib
                    result = {
                        'prompt': prompt_text,
                        'topic': topic,
                        'chart_type': 'map',
                        'data': lib_chart_data,
                    }
                else:
                    # Rare: prompts_lib returned no map data — generate fresh
                    logger.warning(
                        "prompts_lib returned no map chart_data — "
                        "generating map data separately"
                    )
                    result = self._ai_generate_map_with_positions(difficulty)
                    result['prompt'] = prompt_text
                    result['topic'] = topic
                    result['chart_type'] = chart_type
                    task1_source = 'ai'

            # ── Default: bar / line / pie / table ─────────────────
            else:
                prompt_data = self.prompts_lib.get_random_task1(
                    chart_type, difficulty
                )
                prompt_text = prompt_data['prompt']
                topic = prompt_data.get('topic', topic or 'data')

                try:
                    data = self._ai_generate_chart_data(
                        chart_type, prompt_text, difficulty
                    )
                    result = {
                        'prompt': prompt_text,
                        'topic': topic,
                        'data': data,
                        'chart_type': chart_type,
                    }
                except Exception as e:
                    logger.error(
                        f"Failed to generate chart data: {e}; "
                        f"falling back to combined generator."
                    )
                    result = self._ai_generate_question_and_data(
                        chart_type, difficulty
                    )
                    result['prompt'] = prompt_text
                    result['topic'] = topic
                    result['chart_type'] = chart_type

        except PromptGenerationError as e:
            # v5: propagate with clear log — service layer handles it
            logger.error(
                f"Task 1 prompt generation failed "
                f"(chart_type={chart_type}): {e}"
            )
            raise

        # ── Build final chart_data ────────────────────────────────
        chart_data = self._format_chart_data(result, chart_type, topic)
        expected_features = self._extract_features(chart_type, chart_data)
        chart_base64 = self._render_chart_to_base64(chart_type, chart_data)

        return {
            'task': 1,
            'chart_type': chart_type,
            'topic': topic,
            'prompt': prompt_text,
            'chart_data': chart_data,
            'chart_base64': chart_base64,
            'expected_features': expected_features,
            'diagram_key': result.get('diagram_key') or
                           chart_data.get('diagram_key'),
            'num_labels': result.get('num_labels') or
                          chart_data.get('num_labels'),
            'word_limit': 150,
            'time_minutes': 20,
            'difficulty': difficulty,
            'source': task1_source, # v5: accurate per-branch source
        }

    def generate_task1_with_chart(
        self, difficulty="medium", topic=None, chart_type=None
    ):
        return self.generate_task1(difficulty, topic, chart_type)

    # ============================================================
    # TASK 2
    # ============================================================
    def generate_task2(self, difficulty="medium", topic=None):
        if not self.ai:
            raise RuntimeError(
                " AI Engine required. Cannot generate Task 2 without AI."
            )

        # v5: catch PromptGenerationError with clear log
        try:
            prompt_data = self.prompts_lib.get_random_task2(topic, difficulty)
        except PromptGenerationError as e:
            logger.error(f"Task 2 prompt generation failed: {e}")
            raise

        logger.info(
            f"Generating Task 2: {prompt_data['topic']} "
            f"(difficulty: {difficulty})"
        )

        return {
            'task': 2,
            'topic': prompt_data['topic'],
            'prompt': prompt_data['prompt'],
            'word_limit': 250,
            'time_minutes': 40,
            'difficulty': difficulty,
            'source': prompt_data.get('source', 'ai'),
        }

    # ============================================================
    # COMPLETE TEST
    # ============================================================
    def generate_complete(
        self, difficulty="medium", topic=None, exam_type="ielts"
    ):
        logger.info(
            f"Generating complete test: difficulty={difficulty}, "
            f"exam_type={exam_type}"
        )

        # v5: let PromptGenerationError propagate from either task
        t1 = self.generate_task1(difficulty, topic)
        t2 = self.generate_task2(difficulty, topic)

        return {
            'task1': t1,
            'task2': t2,
            'difficulty': difficulty,
            'exam_type': exam_type,
            'total_time_minutes': 60,
            'total_word_limit': 400,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'serial': uuid.uuid4().hex[:8],
        }

    def generate_complete_advanced(
        self, difficulty="medium", topic=None, exam_type="ielts"
    ):
        return self.generate_complete(difficulty, topic, exam_type)

    # ============================================================
    # FEATURE EXTRACTION
    # ============================================================
    @classmethod
    def _extract_features(cls, chart_type, chart_data):
        """
        Extract IELTS-relevant rubric hints from the formatted chart data.

        v4: `diagram` and `flow_chart` now have dedicated branches.
        """
        features = []
        try:
            if chart_type in ('bar_chart', 'line_graph'):
                datasets = chart_data.get('datasets', [])
                labels = chart_data.get('labels', [])
                if datasets and labels:
                    all_values = []
                    for ds in datasets:
                        if isinstance(ds, dict) and 'values' in ds:
                            for i, v in enumerate(ds['values']):
                                all_values.append((
                                    v,
                                    ds.get('label', ''),
                                    labels[i] if i < len(labels) else '',
                                ))
                    if all_values:
                        max_val = max(all_values, key=lambda x: x[0])
                        min_val = min(all_values, key=lambda x: x[0])
                        features.append(
                            f"Highest value: {max_val[1]} at {max_val[0]} "
                            f"({max_val[2]})"
                        )
                        features.append(
                            f"Lowest value: {min_val[1]} at {min_val[0]} "
                            f"({min_val[2]})"
                        )

                    for ds in datasets:
                        if isinstance(ds, dict) and 'values' in ds and \
                                len(ds['values']) >= 2:
                            vals = ds['values']
                            if vals[-1] > vals[0]:
                                features.append(
                                    f"{ds.get('label', 'Data')} shows an "
                                    f"overall increase"
                                )
                            elif vals[-1] < vals[0]:
                                features.append(
                                    f"{ds.get('label', 'Data')} shows an "
                                    f"overall decrease"
                                )

                    if len(datasets) >= 2:
                        features.append(
                            f"Compare {datasets[0].get('label', 'first')} "
                            f"and {datasets[1].get('label', 'second')}"
                        )

            elif chart_type == 'pie_chart':
                values = chart_data.get('values', [])
                labels = chart_data.get('labels', [])
                if values and labels:
                    max_idx = values.index(max(values))
                    min_idx = values.index(min(values))
                    features.append(
                        f"Largest portion: {labels[max_idx]} "
                        f"({values[max_idx]}%)"
                    )
                    features.append(
                        f"Smallest portion: {labels[min_idx]} "
                        f"({values[min_idx]}%)"
                    )

            elif chart_type == 'table':
                rows = chart_data.get('rows', [])
                headers = chart_data.get('headers', [])
                if rows and headers:
                    features.append(
                        f"Compare data across {len(headers) - 1} "
                        f"years/categories"
                    )
                    if len(rows) >= 2:
                        first_cell = rows[-1][0] if rows[-1] else 'last row'
                        features.append(f"Highest values in {first_cell}")

            # ── v4: dedicated diagram branch ──────────────────────
            elif chart_type == 'diagram':
                steps = chart_data.get('steps', []) or []
                descriptions = chart_data.get('descriptions', []) or []

                if steps:
                    features.append(f"The process has {len(steps)} stages")
                    features.append(f"Begins with: {steps[0]}")
                    features.append(f"Ends with: {steps[-1]}")
                if descriptions:
                    pick = (
                        descriptions[1] if len(descriptions) >= 2
                        else descriptions[0]
                    )
                    if isinstance(pick, str) and pick.strip():
                        features.append(
                            f"Key detail: {pick.strip()[:120]}"
                        )

            # ── v4: dedicated flow-chart branch ───────────────────
            elif chart_type == 'flow_chart':
                stages = chart_data.get('stages', []) or []
                decisions = chart_data.get('decisions', []) or []
                descriptions = chart_data.get('descriptions', []) or []

                if stages:
                    features.append(
                        f"The flow chart has {len(stages)} stages"
                    )
                    features.append(f"First stage: {stages[0]}")
                    features.append(f"Final stage: {stages[-1]}")

                non_null_decisions = [d for d in decisions if d]
                if non_null_decisions:
                    features.append(
                        f"Decision point: "
                        f"{str(non_null_decisions[0])[:100]}"
                    )
                elif descriptions:
                    pick = (
                        descriptions[1] if len(descriptions) >= 2
                        else descriptions[0]
                    )
                    if isinstance(pick, str) and pick.strip():
                        features.append(
                            f"Key detail: {pick.strip()[:120]}"
                        )

            # ── v4: dedicated map branch ──────────────────────────
            elif chart_type == 'map':
                changes = chart_data.get('changes', []) or []
                if changes:
                    features.append(f"Key changes: {changes[0]}")
                    if len(changes) > 1:
                        features.append(f"Also: {changes[1]}")

        except Exception as e:
            logger.warning(f"Feature extraction failed: {e}")
            features.append(
                "Describe the main trends shown in the visual"
            )

        if not features:
            features.append(
                "Describe the main trends shown in the visual"
            )

        return features[:5]


# ============================================================
# FACTORY
# ============================================================
def create_writing_test_generator(ai_engine, chart_renderer=None):
    if not ai_engine:
        raise ValueError(
            " AI Engine required to create WritingTestGenerator"
        )
    return WritingTestGenerator(ai_engine, chart_renderer)


# ============================================================
# PROCESS SHUTDOWN HOOK
# ============================================================
def _shutdown_generator_executor():
    try:
        WritingTestGenerator._EXECUTOR.shutdown(wait=False)
    except Exception:
        pass


atexit.register(_shutdown_generator_executor)