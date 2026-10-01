"""IELTS Task 1 Map Renderer - Production Ready

Renders a "before/after" comparison map for IELTS Writing Task 1.

v9.2 FIX — THREAD + BACKEND FIX:
- REMOVED nocairosvg entirely. It internally calls install_playwright
  with a bad API (`'BrowserType' object is not iterable`), which was
  crashing map rendering and wasting ~14s per map before falling back.
- Now uses a clean backend chain:
    1. cairosvg (real Cairo DLL) — fast, native
    2. playwright (headless browser) — works everywhere
  Same strategy as chart_renderer.py, so behaviour is consistent.
- Backend detection happens ONCE at import time (no per-render probes).

PRIOR FIXES (kept):
- Emits a SINGLE SVG root containing two side-by-side <g> panels, so the
  PNG output contains BOTH the BEFORE and AFTER maps.
- The changes table is drawn inside the SVG (as <rect>/<text>).
- The map title is rendered as SVG text.
- Returns a web URL ("/static/charts/<file>.png").
- Added icons for water / river / road / hospital.
- XML-escapes all dynamic text.
"""

import os
import random
import logging
import html as _html
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# SVG → PNG BACKEND SELECTION (done once, at import)
#
# Order:
# 1. cairosvg — real Cairo DLL (fast, native)
# 2. playwright — headless Chromium (works everywhere)
#
# nocairosvg is INTENTIONALLY SKIPPED — its install_playwright() call
# raises `TypeError: 'BrowserType' object is not iterable` on the
# versions currently shipped, and previously caused ~14s wasted per
# map render before falling back.
# ═══════════════════════════════════════════════════════════════════
_SVG_RENDERER = None
CAIRO_BACKEND = None


def _try_cairosvg():
    """Try to load native cairosvg (needs system Cairo DLL)."""
    try:
        import cairosvg as _m
        fn = getattr(_m, 'svg2png', None)
        if callable(fn):
            # Quick sanity check — will throw if Cairo DLL missing
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                    tmp = tf.name
                fn(
                    bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" '
                               b'width="10" height="10">'
                               b'<rect width="10" height="10" fill="red"/></svg>',
                    write_to=tmp,
                )
                ok = os.path.exists(tmp) and os.path.getsize(tmp) > 0
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                if ok:
                    return fn
            except Exception as e:
                logger.info(f"cairosvg sanity check failed: {e}")
    except (ImportError, OSError) as e:
        logger.info(f"cairosvg not available: {e}")
    except Exception as e:
        logger.info(f"cairosvg import error: {e}")
    return None


def _make_playwright_renderer():
    """Build a playwright-based svg2png-compatible function."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        logger.info(f"playwright not available: {e}")
        return None

    def _playwright_svg2png(bytestring=None, write_to=None, **kwargs):
        if bytestring is None or write_to is None:
            raise TypeError(
                "playwright svg2png requires `bytestring` and `write_to`"
            )
        if isinstance(bytestring, bytes):
            svg_str = bytestring.decode('utf-8', errors='replace')
        else:
            svg_str = str(bytestring)

        html_doc = (
            '<!DOCTYPE html>'
            '<html><head><meta charset="utf-8"></head>'
            '<body style="margin:0;padding:0;background:#ffffff">'
            f'{svg_str}'
            '</body></html>'
        )

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": 1200, "height": 900}
                )
                page.set_content(html_doc, wait_until="networkidle")
                page.wait_for_timeout(300)
                page.screenshot(path=write_to, full_page=True)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    return _playwright_svg2png


# ─── Backend selection chain ───────────────────────────────────
_SVG_RENDERER = _try_cairosvg()
if _SVG_RENDERER is not None:
    CAIRO_BACKEND = "cairosvg"
    logger.info(" MapRenderer backend: cairosvg (native Cairo DLL)")
else:
    _SVG_RENDERER = _make_playwright_renderer()
    if _SVG_RENDERER is not None:
        CAIRO_BACKEND = "playwright"
        logger.info(" MapRenderer backend: playwright (headless Chromium)")
    else:
        CAIRO_BACKEND = None
        logger.warning(
            " MapRenderer: no SVG→PNG backend available. "
            "Install one of:\n"
            " pip install playwright && playwright install chromium\n"
            " (recommended on Windows — no Cairo DLL required)"
        )

CAIRO_AVAILABLE = _SVG_RENDERER is not None


# ─── Layout constants ───────────────────────────────────
PANEL_W = 520
PANEL_H = 580
PANEL_GAP = 20
TITLE_H = 40
TABLE_GAP = 20
TOTAL_W = PANEL_W * 2 + PANEL_GAP # 1060
ROW_H = 16
TABLE_HEADER_H = 22


class MapRenderer:
    """Render IELTS Task 1 maps to PNG using cairosvg or playwright."""

    def __init__(self, output_dir: str = "static/charts"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        if not CAIRO_AVAILABLE:
            logger.error(
                "[MapRenderer] No SVG→PNG converter available. "
                "Install playwright: pip install playwright && playwright install chromium"
            )
            self._available = False
        else:
            self._available = True
            logger.info(
                f"[MapRenderer] Initialized with backend: {CAIRO_BACKEND}, "
                f"output dir: {output_dir}"
            )

    # ============================================================
    # PUBLIC API
    # ============================================================

    def render(self, chart_data: Dict, filename: Optional[str] = None) -> str:
        """
        Render a map from chart_data and save as PNG.

        Returns:
            str: Web-relative path to the saved PNG (e.g. "/static/charts/map_1234.png")
        """
        if not self._available:
            raise RuntimeError(
                "Map rendering unavailable: no SVG→PNG converter installed. "
                "Run: pip install playwright && playwright install chromium"
            )

        try:
            # ─── Validate & normalize input ────────────────────
            before = chart_data.get("before", []) or []
            after = chart_data.get("after", []) or []
            if not before or not after:
                raise ValueError(
                    "chart_data must contain 'before' and 'after' lists "
                    "with at least one item."
                )

            if len(before) != len(after):
                logger.warning(
                    "Before and after lists have different lengths. "
                    "Truncating to shorter length."
                )
                min_len = min(len(before), len(after))
                before = before[:min_len]
                after = after[:min_len]

            positions_before = chart_data.get("positions_before", []) or []
            positions_after = chart_data.get("positions_after", []) or []

            if not positions_before or len(positions_before) < len(before):
                positions_before = self._generate_default_positions(len(before))
            if not positions_after or len(positions_after) < len(after):
                positions_after = self._generate_default_positions(len(after))

            title = chart_data.get("title", "Map Comparison") or "Map Comparison"
            changes = list(chart_data.get("changes", []) or [])
            while len(changes) < len(before):
                changes.append("Modified")
            changes = changes[:len(before)]

            # ─── Build the single SVG (both panels + changes table) ─
            before_inner = self._make_svg_panel(before, positions_before, "BEFORE")
            after_inner = self._make_svg_panel(after, positions_after, "AFTER")

            table_y = TITLE_H + PANEL_H + TABLE_GAP
            changes_svg, changes_h = self._build_changes_svg(
                before, after, changes, y_start=table_y
            )

            total_h = table_y + changes_h + 20 # +20 bottom padding

            svg_content = (
                f'<svg width="{TOTAL_W}" height="{total_h}" '
                f'xmlns="http://www.w3.org/2000/svg">'
                f'<text x="{TOTAL_W // 2}" y="24" text-anchor="middle" '
                f'font-size="14" font-family="Georgia,serif" font-weight="bold" fill="#222">'
                f'{self._esc(title)}</text>'
                f'<g transform="translate(0,{TITLE_H})">{before_inner}</g>'
                f'<g transform="translate({PANEL_W + PANEL_GAP},{TITLE_H})">{after_inner}</g>'
                f'{changes_svg}'
                f'</svg>'
            )

            # ─── Determine filename ────────────────────────────
            if filename is None:
                filename = (
                    f"map_{random.randint(1000, 9999)}_"
                    f"{random.randint(1000, 9999)}"
                )
            if not filename.endswith('.png'):
                filename += '.png'

            filepath = os.path.join(self.output_dir, filename)

            # ─── Rasterize via selected backend ────────────────
            _SVG_RENDERER(
                bytestring=svg_content.encode('utf-8'),
                write_to=filepath,
            )

            # Verify output exists
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                raise RuntimeError(
                    f"Backend {CAIRO_BACKEND} produced empty file: {filepath}"
                )

            logger.info(
                f"[MapRenderer/{CAIRO_BACKEND}] Map saved: {filepath} "
                f"({os.path.getsize(filepath)} bytes)"
            )
            return f"/static/charts/{filename}"

        except Exception as e:
            logger.error(f"[MapRenderer] Failed to render map: {e}", exc_info=True)
            raise RuntimeError(f"Map rendering failed: {e}")

    # ============================================================
    # INTERNAL — one panel (inner <g> content, no <svg> wrapper)
    # ============================================================

    def _make_svg_panel(self, items, positions, label: str) -> str:
        """
        Return the *inner* content of one map panel.
        No <svg> wrapper — the caller places this inside a <g transform>.
        """
        parts = []

        # Background + frame
        parts.append(f'<rect width="{PANEL_W}" height="{PANEL_H}" fill="#fdfdf8"/>')
        parts.append(f'<rect x="6" y="6" width="{PANEL_W - 12}" height="{PANEL_H - 12}" '
                     f'fill="none" stroke="#333" stroke-width="2.5"/>')
        parts.append(f'<rect x="12" y="12" width="{PANEL_W - 24}" height="{PANEL_H - 24}" '
                     f'fill="none" stroke="#333" stroke-width="0.5"/>')

        # Panel label + divider
        parts.append(f'<text x="{PANEL_W // 2}" y="35" text-anchor="middle" font-size="15" '
                     f'fill="#222" font-family="Georgia,serif" font-weight="bold">{self._esc(label)}</text>')
        parts.append(f'<line x1="80" y1="42" x2="{PANEL_W - 80}" y2="42" stroke="#333" stroke-width="1"/>')

        # North indicator
        parts.append(f'<text x="{PANEL_W // 2}" y="18" text-anchor="middle" font-size="9" '
                     f'fill="#666" font-family="Georgia,serif">NORTH</text>')
        parts.append(f'<polygon points="{PANEL_W // 2},12 {PANEL_W // 2 - 4},18 {PANEL_W // 2 + 4},18" fill="#666"/>')

        # ─── Roads (decorative background) ───────
        parts.append('<line x1="250" y1="50" x2="250" y2="540" stroke="#c8c8c8" stroke-width="10"/>')
        parts.append('<line x1="250" y1="50" x2="250" y2="540" stroke="#b0b0b0" stroke-width="5"/>')
        parts.append('<line x1="250" y1="50" x2="250" y2="540" stroke="white" stroke-width="2" stroke-dasharray="10,7"/>')
        parts.append('<polygon points="250,60 240,75 260,75" fill="#888"/>')
        parts.append('<line x1="15" y1="300" x2="235" y2="300" stroke="#c8c8c8" stroke-width="5"/>')
        parts.append('<line x1="265" y1="300" x2="505" y2="300" stroke="#c8c8c8" stroke-width="5"/>')
        parts.append('<line x1="15" y1="300" x2="235" y2="300" stroke="#b0b0b0" stroke-width="5"/>')
        parts.append('<line x1="265" y1="300" x2="505" y2="300" stroke="#b0b0b0" stroke-width="5"/>')
        parts.append('<line x1="15" y1="300" x2="235" y2="300" stroke="white" stroke-width="1.5" stroke-dasharray="6,5"/>')
        parts.append('<line x1="265" y1="300" x2="505" y2="300" stroke="white" stroke-width="1.5" stroke-dasharray="6,5"/>')
        parts.append('<polygon points="480,295 495,300 480,305" fill="#888"/>')
        parts.append('<text x="265" y="180" font-size="8" fill="#555" font-family="Arial" font-weight="bold">Main Road</text>')
        parts.append('<text x="380" y="293" font-size="7" fill="#555" font-family="Arial">Station Road</text>')

        # ─── Feature colors by type ─────────────────────────
        colors = {
            "forest": "#e8f5e9,#4caf50", "park": "#e8f5e9,#4caf50", "farm": "#fffde7,#f9a825",
            "water": "#e3f2fd,#1e88e5", "river": "#e3f2fd,#1e88e5", "road": "#eceff1,#78909c",
            "house": "#fff3e0,#e65100", "housing": "#fff3e0,#e65100", "shop": "#fce4ec,#c62828",
            "supermarket": "#fce4ec,#c62828", "school": "#e8eaf6,#3949ab", "hospital": "#ffebee,#b71c1c",
            "church": "#f3e5f5,#7b1fa2", "building": "#f5f5f5,#757575", "office": "#f5f5f5,#757575",
            "sports": "#e8f5e9,#2e7d32", "parking": "#eceff1,#78909c"
        }

        # ─── Feature boxes + icons ──────────────────────────
        for i, item in enumerate(items):
            if i >= len(positions):
                continue
            pos = positions[i]
            if not isinstance(pos, dict):
                continue

            x = int(pos.get("x", 260))
            y = int(pos.get("y", 300))
            ftype = pos.get("type", "building")

            name = item.split("(")[0].strip()[:16] if isinstance(item, str) else str(item)[:16]
            size = ""
            if isinstance(item, str) and "(" in item:
                size = item.split("(")[1].replace(")", "").strip()[:12]

            box_w, box_h = 80, 50
            color_pair = colors.get(ftype, "#f5f5f5,#757575")
            fill, stroke = color_pair.split(",")

            parts.append(
                f'<rect x="{x - box_w / 2}" y="{y - box_h / 2}" width="{box_w}" height="{box_h}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" rx="3"/>'
            )

            # ─── Icons by type ─────────────────────────────
            if ftype in ("forest", "park"):
                parts.append(f'<circle cx="{x}" cy="{y - 4}" r="8" fill="none" stroke="#388e3c" stroke-width="1.5"/>')
                parts.append(f'<circle cx="{x - 3}" cy="{y - 6}" r="3" fill="#4caf50"/>')
            elif ftype == "farm":
                parts.append(f'<line x1="{x - 10}" y1="{y - 2}" x2="{x + 10}" y2="{y - 2}" stroke="#f9a825" stroke-width="1"/>')
            elif ftype in ("house", "housing"):
                parts.append(f'<rect x="{x - 8}" y="{y - 6}" width="8" height="6" fill="none" stroke="#e65100" stroke-width="1"/>')
                parts.append(f'<polygon points="{x - 10},{y - 6} {x},{y - 12} {x + 10},{y - 6}" fill="none" stroke="#e65100" stroke-width="0.8"/>')
            elif ftype in ("shop", "supermarket"):
                parts.append(f'<rect x="{x - 8}" y="{y - 5}" width="16" height="8" fill="none" stroke="#c62828" stroke-width="1"/>')
            elif ftype == "school":
                parts.append(f'<rect x="{x - 6}" y="{y - 5}" width="12" height="8" fill="none" stroke="#3949ab" stroke-width="1"/>')
                parts.append(f'<polygon points="{x - 2},{y - 10} {x},{y - 5} {x + 2},{y - 10}" fill="none" stroke="#3949ab" stroke-width="0.6"/>')
            elif ftype == "church":
                parts.append(f'<polygon points="{x},{y - 14} {x - 7},{y - 2} {x + 7},{y - 2}" fill="none" stroke="#7b1fa2" stroke-width="1"/>')
                parts.append(f'<line x1="{x}" y1="{y - 14}" x2="{x}" y2="{y - 17}" stroke="#7b1fa2" stroke-width="0.8"/>')
            elif ftype == "sports":
                parts.append(f'<circle cx="{x}" cy="{y - 4}" r="7" fill="none" stroke="#2e7d32" stroke-width="1.5"/>')
            elif ftype in ("water", "river"):
                parts.append(
                    f'<path d="M {x - 12} {y} q 3 -4 6 0 q 3 4 6 0 q 3 -4 6 0" '
                    f'fill="none" stroke="#1e88e5" stroke-width="1.4"/>'
                )
            elif ftype == "road":
                parts.append(f'<line x1="{x - 12}" y1="{y}" x2="{x + 12}" y2="{y}" stroke="#546e7a" stroke-width="2.2"/>')
                parts.append(f'<line x1="{x - 12}" y1="{y}" x2="{x + 12}" y2="{y}" stroke="white" stroke-width="0.6" stroke-dasharray="2,2"/>')
            elif ftype == "hospital":
                parts.append(f'<rect x="{x - 2.5}" y="{y - 9}" width="5" height="14" fill="#b71c1c"/>')
                parts.append(f'<rect x="{x - 9}" y="{y - 2.5}" width="18" height="5" fill="#b71c1c"/>')
            else:
                parts.append(f'<rect x="{x - 6}" y="{y - 5}" width="12" height="8" fill="none" stroke="#757575" stroke-width="1"/>')

            # Name + optional size label (below the box)
            parts.append(
                f'<text x="{x}" y="{y + box_h / 2 + 14}" text-anchor="middle" font-size="8" '
                f'fill="#333" font-family="Arial" font-weight="bold">{self._esc(name)}</text>'
            )
            if size:
                parts.append(
                    f'<text x="{x}" y="{y + box_h / 2 + 24}" text-anchor="middle" font-size="6.5" '
                    f'fill="#777" font-family="Arial">({self._esc(size)})</text>'
                )

        # ─── South label + scale bar ────────────────────────
        parts.append(f'<text x="{PANEL_W // 2}" y="555" text-anchor="middle" font-size="9" '
                     f'fill="#666" font-family="Georgia,serif">SOUTH</text>')
        parts.append('<rect x="380" y="550" width="60" height="5" fill="white" stroke="#333" stroke-width="0.6"/>')
        parts.append('<rect x="380" y="550" width="30" height="5" fill="#333"/>')
        parts.append('<text x="380" y="562" font-size="5.5" fill="#666">0</text>'
                     '<text x="410" y="562" font-size="5.5" fill="#666">50m</text>'
                     '<text x="440" y="562" font-size="5.5" fill="#666">100m</text>')

        return ''.join(parts)

    # ============================================================
    # INTERNAL — changes table as SVG
    # ============================================================

    def _build_changes_svg(self, before, after, changes, y_start: int) -> Tuple[str, int]:
        """
        Draw the changes table as SVG elements.
        Returns (svg_string, total_table_height).
        """
        parts = []

        # Header row
        parts.append(
            f'<rect x="10" y="{y_start}" width="{TOTAL_W - 20}" height="{TABLE_HEADER_H}" '
            f'fill="#f0f0f0" stroke="#ccc" stroke-width="0.6"/>'
        )
        parts.append(f'<text x="20" y="{y_start + 15}" font-size="10" font-family="Arial" '
                     f'font-weight="bold" fill="#222">Before</text>')
        parts.append(f'<text x="280" y="{y_start + 15}" font-size="10" font-family="Arial" '
                     f'font-weight="bold" fill="#222">After</text>')
        parts.append(f'<text x="540" y="{y_start + 15}" font-size="10" font-family="Arial" '
                     f'font-weight="bold" fill="#222">Change</text>')

        y = y_start + TABLE_HEADER_H
        for i, (b, a, c) in enumerate(zip(before, after, changes)):
            shade = "#fafafa" if i % 2 else "#ffffff"
            parts.append(
                f'<rect x="10" y="{y}" width="{TOTAL_W - 20}" height="{ROW_H}" '
                f'fill="{shade}" stroke="#e5e5e5" stroke-width="0.4"/>'
            )
            bn = self._esc((b or "")[:32])
            an = self._esc((a or "")[:32])
            cn = self._esc((c or "")[:70])
            parts.append(f'<text x="20" y="{y + 12}" font-size="9" font-family="Arial" fill="#333">{bn}</text>')
            parts.append(f'<text x="280" y="{y + 12}" font-size="9" font-family="Arial" fill="#333">{an}</text>')
            parts.append(f'<text x="540" y="{y + 12}" font-size="8" font-family="Arial" fill="#555">{cn}</text>')
            y += ROW_H

        total_h = TABLE_HEADER_H + len(before) * ROW_H
        return ''.join(parts), total_h

    # ============================================================
    # INTERNAL — utilities
    # ============================================================

    def _generate_default_positions(self, num_features: int) -> list:
        """Generate default positions for features if not provided."""
        positions = []
        cols = max(1, int(num_features ** 0.5) + 1)
        rows = (num_features + cols - 1) // cols

        for i in range(num_features):
            row = i // cols
            col = i % cols
            x = 80 + (col * (400 / max(cols - 1, 1))) if cols > 1 else 260
            y = 100 + (row * (350 / max(rows - 1, 1))) if rows > 1 else 280
            x += random.randint(-15, 15)
            y += random.randint(-15, 15)
            x = int(max(50, min(470, x)))
            y = int(max(60, min(540, y)))
            positions.append({'x': x, 'y': y, 'type': 'building'})
        return positions

    @staticmethod
    def _esc(s) -> str:
        """XML-escape a string for safe embedding in SVG text."""
        return _html.escape(str(s if s is not None else ""), quote=True)


# ─── Factory function ──────────────────────────────────
def create_map_renderer(output_dir: str = "static/charts") -> MapRenderer:
    """Factory function to create MapRenderer instance."""
    return MapRenderer(output_dir)