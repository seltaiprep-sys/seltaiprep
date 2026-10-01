"""Pre-made SVG diagram asset loader for IELTS Writing Task 1.

TOPIC-FIRST DESIGN:
    Instead of fuzzy-matching an AI-generated topic string against a
    manifest (which occasionally missed synonyms like "confectionery"
    for "chocolate"), we now pick a specific topic KEY up front and pass
    it through the pipeline. The chart renderer uses that key directly.

Flow:
    1. test_generator picks a random diagram topic key from the registry
    2. It tells the AI to generate steps for THAT exact topic
    3. It stores the key in chart_data["diagram_key"]
    4. chart_renderer reads chart_data["diagram_key"] and renders the
       exact SVG without any keyword matching
"""
import html
import json
import logging
import os
import random
import re
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MANIFEST_CACHE: Optional[Dict] = None
_MANIFEST_PATH_CACHE: Optional[str] = None


# ============================================================
# MANIFEST LOADING
# ============================================================
def _load_manifest(assets_dir: str) -> Dict:
    """Load manifest.json once per process, cache the parsed dict."""
    global _MANIFEST_CACHE, _MANIFEST_PATH_CACHE

    manifest_path = os.path.join(assets_dir, 'manifest.json')

    if _MANIFEST_CACHE is not None and _MANIFEST_PATH_CACHE == manifest_path:
        return _MANIFEST_CACHE

    if not os.path.exists(manifest_path):
        logger.warning(f"[DiagramAssets] No manifest at {manifest_path}")
        _MANIFEST_CACHE = {}
        _MANIFEST_PATH_CACHE = manifest_path
        return _MANIFEST_CACHE

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("manifest.json must be a JSON object")
        _MANIFEST_CACHE = data
        _MANIFEST_PATH_CACHE = manifest_path
        logger.info(f"[DiagramAssets] Loaded {len(data)} diagram templates")
    except Exception as e:
        logger.error(f"[DiagramAssets] Manifest load failed: {e}")
        _MANIFEST_CACHE = {}
        _MANIFEST_PATH_CACHE = manifest_path

    return _MANIFEST_CACHE


def reload_manifest(assets_dir: str = "static/diagram_assets") -> Dict:
    """Force a fresh load (useful after adding new SVGs)."""
    global _MANIFEST_CACHE, _MANIFEST_PATH_CACHE
    _MANIFEST_CACHE = None
    _MANIFEST_PATH_CACHE = None
    return _load_manifest(assets_dir)


# ============================================================
# TOPIC REGISTRY — used by test_generator to pick a topic up front
# ============================================================
def get_diagram_topics(assets_dir: str = "static/diagram_assets") -> List[Dict]:
    """
    Return the list of available diagram topics, each as:
        {
            "key": "chocolate_production",
            "title": "How chocolate is produced",
            "num_labels": 6,
            "description": "..."
        }
    """
    manifest = _load_manifest(assets_dir)
    topics: List[Dict] = []
    for key, entry in manifest.items():
        if key.startswith('_'):
            # Skip metadata keys like _generated_at
            continue
        topics.append({
            'key': key,
            'title': entry.get('title', key),
            'num_labels': int(entry.get('num_labels', 6)),
            'description': entry.get('description', ''),
        })
    return topics


def get_diagram_topic(key: str,
                      assets_dir: str = "static/diagram_assets") -> Optional[Dict]:
    """Look up a specific topic by key."""
    manifest = _load_manifest(assets_dir)
    entry = manifest.get(key)
    if not entry:
        return None
    return {
        'key': key,
        'title': entry.get('title', key),
        'num_labels': int(entry.get('num_labels', 6)),
        'description': entry.get('description', ''),
    }


def pick_random_diagram_topic(
    assets_dir: str = "static/diagram_assets",
    exclude: Optional[List[str]] = None,
) -> Optional[Dict]:
    """
    Return one random topic (dict), or None if the manifest is empty.
    `exclude` can be a list of keys to skip.
    """
    topics = get_diagram_topics(assets_dir)
    if exclude:
        exclude_set = set(exclude)
        topics = [t for t in topics if t['key'] not in exclude_set]
    if not topics:
        return None
    return random.choice(topics)


# ============================================================
# RENDER — uses an explicit key, no matching needed
# ============================================================
def render_diagram_from_asset(
    asset_key: str,
    chart_data: Dict,
    output_dir: str = "static/charts",
    assets_dir: str = "static/diagram_assets",
    filename: Optional[str] = None,
) -> Optional[str]:
    """
    Render a pre-made SVG by key and rasterize it to PNG.

    `chart_data` must contain:
        · 'title' — string (optional, falls back to manifest title)
        · 'steps' — list of strings in order, exactly `num_labels` long

    Returns web-relative PNG URL, or None on failure.
    """
    # Import the SVG converter that chart_renderer resolved
    try:
        from .chart_renderer import _SVG_TO_PNG
    except Exception as e:
        logger.error(f"[DiagramAssets] Could not import chart_renderer: {e}")
        return None

    if _SVG_TO_PNG is None:
        logger.warning("[DiagramAssets] No SVG→PNG converter — cannot render")
        return None

    manifest = _load_manifest(assets_dir)
    if asset_key not in manifest:
        logger.warning(f"[DiagramAssets] Unknown asset key: {asset_key}")
        return None

    entry = manifest[asset_key]
    svg_filename = entry.get('file')
    if not svg_filename:
        logger.warning(f"[DiagramAssets] No file for asset: {asset_key}")
        return None

    svg_path = os.path.join(assets_dir, svg_filename)
    if not os.path.exists(svg_path):
        logger.warning(f"[DiagramAssets] Missing SVG file: {svg_path}")
        return None

    # ── Load SVG ──
    try:
        with open(svg_path, 'r', encoding='utf-8') as f:
            svg_content = f.read()
    except Exception as e:
        logger.error(f"[DiagramAssets] Read failed: {e}")
        return None

    # ── Prepare values ──
    title = chart_data.get('title') or entry.get('title', '')
    steps = list(chart_data.get('steps') or [])
    expected = int(entry.get('num_labels', len(steps)))

    # Pad or trim steps to match the SVG's label count exactly
    if len(steps) < expected:
        steps = steps + [''] * (expected - len(steps))
    elif len(steps) > expected:
        steps = steps[:expected]

    # ── Substitute {{title}} ──
    svg_content = svg_content.replace('{{title}}', _xml_escape(title))

    # ── Substitute {{label_N}} ──
    for i, step in enumerate(steps, start=1):
        svg_content = svg_content.replace(
            '{{label_' + str(i) + '}}', _xml_escape(step)
        )

    # Clean any remaining placeholders
    svg_content = re.sub(r'\{\{label_\d+\}\}', '', svg_content)
    svg_content = re.sub(r'\{\{[^}]+\}\}', '', svg_content)

    # ── Filename ──
    if filename is None:
        filename = f"diagram_{asset_key}_{uuid.uuid4().hex[:6]}.png"
    if not filename.endswith('.png'):
        filename += '.png'

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    # ── Rasterize ──
    try:
        try:
            _SVG_TO_PNG(bytestring=svg_content.encode('utf-8'), write_to=filepath)
        except TypeError:
            _SVG_TO_PNG(svg_content.encode('utf-8'), filepath)
    except Exception as e:
        logger.error(f"[DiagramAssets] Rasterize failed: {e}")
        return None

    if not os.path.exists(filepath):
        logger.error(f"[DiagramAssets] Output not written: {filepath}")
        return None

    logger.info(f"[DiagramAssets] Rendered '{asset_key}' → {filepath}")
    return f"/static/charts/{filename}"


# ============================================================
# HELPERS
# ============================================================
def _xml_escape(s: str) -> str:
    """XML-escape a string for safe embedding in SVG text."""
    return html.escape(str(s if s is not None else ''), quote=True)


def list_available_assets(assets_dir: str = "static/diagram_assets") -> Dict:
    """Return the raw manifest (for admin/debugging)."""
    return dict(_load_manifest(assets_dir))


# ============================================================
# LEGACY: kept for backwards compatibility, not used by new flow
# ============================================================
def match_diagram(topic: str = "", title: str = "",
                  assets_dir: str = "static/diagram_assets") -> Optional[str]:
    """
    DEPRECATED — kept only so old code paths don't break.

    New code should use `pick_random_diagram_topic()` +
    `render_diagram_from_asset()` with the resulting key, instead of
    matching a free-form topic string.
    """
    manifest = _load_manifest(assets_dir)
    if not manifest:
        return None
    haystack = f"{topic} {title}".lower().strip()
    if not haystack:
        return None
    best_key = None
    best_score = 0
    for key, entry in manifest.items():
        if key.startswith('_'):
            continue
        score = 0
        for kw in entry.get('keywords', []):
            kw_l = str(kw).lower().strip()
            if kw_l and kw_l in haystack:
                score += len(kw_l)
        if score > best_score:
            best_score = score
            best_key = key
    return best_key if best_score > 0 else None


__all__ = [
    'get_diagram_topics',
    'get_diagram_topic',
    'pick_random_diagram_topic',
    'render_diagram_from_asset',
    'reload_manifest',
    'list_available_assets',
    'match_diagram', # deprecated, kept for backward compat
]