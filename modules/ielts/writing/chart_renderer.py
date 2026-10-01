"""IELTS Official Style Chart Renderer - Matplotlib for charts, Mermaid.js for diagrams/flowcharts

FIXES APPLIED (v9.2 — DISTINCT BAR PATTERNS):
  (0a) `IELTS_BAR_PATTERNS` — replaced '/' and '\' (visually similar,
        often looked identical on small charts) with DENSER 3-char patterns:
        '///' → '\\\\\\' → 'xxx' → '|||' → ... Each is now visually
        unambiguous even at small sizes.
  (0b) `_render_bar_ielts()` — added fill-tone alternation. Series now
        differ by BOTH fill shade AND hatch pattern → guaranteed clarity
        for 2-series charts (e.g. Men/Women) and multi-series ones.

FIXES APPLIED (v9.1 — THREAD-SAFE MATPLOTLIB):
  (1) All `plt.savefig()` calls replaced with `fig.savefig()`.
        `plt.savefig()` operates on the GLOBAL "current figure" (plt.gcf()),
        which is not thread-safe. Under 8 parallel workers this caused
        "RendererAgg ... Done" exceptions and produced blank error images.
        `fig.savefig()` operates on the specific Figure instance → safe.
  (2) `plt.subplots()` + `plt.close(fig)` wrapped in `_MPL_LOCK`
        (RLock) because pyplot's figure registry (`Gcf`) is global state.
        Plotting + savefig run WITHOUT the lock → true parallelism.

FIXES APPLIED (v9 — CONCURRENCY BUMP):
  (3) `ThreadPoolExecutor(max_workers=8)` (was 2). Enough parallelism
        for 10-user bursts without exhausting matplotlib memory.

FIXES APPLIED (v8 — PROFESSIONAL FLOWCHART + PLAYWRIGHT):
  (4) `_render_flow_chart_ielts` completely redesigned with stadium
        shapes, diamonds for decisions, number badges, colored arrows.
  (5) `_resolve_svg_converter()` — nocairosvg probe removed
        (its svg2png() signature differs and fails); playwright is
        used directly with `wait_until="networkidle"`.

FIXES APPLIED (v6 — WINDOWS DIAGRAM FIX):
  (6) `render()` no longer routes diagram/flow_chart through mermaid.

FIXES APPLIED (v5 — TOPIC-FIRST DIAGRAMS):
  (7) `_render_diagram_ielts` / `_render_flow_chart_ielts` read
        `chart_data['diagram_key']` directly.

FIXES APPLIED (v4):
  (8) `_resolve_svg_converter()` probes svglib → cairosvg → playwright.
  (9) `ChartRenderer.__init__` calls `_register_renderer(self)`.

FIXES APPLIED (v3):
  (10) `_render_map_ielts` delegates to SVG MapRenderer with fallback.

FIXES APPLIED (v2):
  (11) Figure leaks fixed; thread-pool shutdown via atexit.
  (12) PDF / PIL / error-chart resources released in finally blocks.
"""
import atexit
import base64
import hashlib
import logging
import os
import random
import shutil
import threading
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor

import numpy as np

warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

logger = logging.getLogger(__name__)

# ========== Matplotlib Setup ==========
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle, Polygon, Circle
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    MATPLOTLIB_AVAILABLE = True
except ImportError as e:
    MATPLOTLIB_AVAILABLE = False
    logger.error(f"matplotlib not available: {e}")
    raise ImportError(
        " matplotlib is required for chart rendering. "
        "Please install: pip install matplotlib"
    )


# ═══════════════════════════════════════════════════════════════════
# v9.1 — Matplotlib is NOT thread-safe.
#
# Two distinct thread-safety issues:
# 1. pyplot's figure registry (`Gcf`) is global mutable state.
# Concurrent `plt.subplots()` / `plt.close()` calls can race.
# 2. `plt.savefig()` operates on `plt.gcf()` — the GLOBAL "current
# figure". If Thread-A finishes plotting figure A and calls
# plt.savefig() while Thread-B just switched the "current figure"
# to B, Thread-A saves the wrong figure (or crashes with
# "RendererAgg ... Done").
#
# FIX:
# • Replace every `plt.savefig(...)` with `fig.savefig(...)`.
# `fig.savefig()` operates on a specific Figure instance → safe.
# • Wrap `plt.subplots()` and `plt.close(fig)` in `_MPL_LOCK`.
# These are fast; contention is negligible.
# • Plotting + savefig run WITHOUT the lock → true parallelism.
# ═══════════════════════════════════════════════════════════════════
_MPL_LOCK = threading.RLock()


# ========== Mermaid.js Setup ==========
MERMAID_AVAILABLE = False
MERMAID_BACKEND = None
try:
    import mermaidx
    MERMAID_AVAILABLE = True
    MERMAID_BACKEND = "mermaidx"
    logger.info(" Mermaid.js (mermaidx) loaded successfully")
except ImportError:
    logger.warning(" Mermaid.js not available. Install with: pip install mermaidx")
    logger.warning(" Fallback: matplotlib will be used for diagrams/flowcharts")

# ========== SVG → PNG Converter Setup ==========
SVG_CONVERTER = None
_SVG_TO_PNG = None


def _resolve_svg_converter():
    """
    Return (name, callable) for a working SVG→PNG converter.

    Probe order (Windows-friendly):
      1. svglib + reportlab — pure Python
      2. cairosvg.svg2png — native Cairo (needs DLL)
      3. playwright (headless) — browser-based, works everywhere

    NOTE: nocairosvg is intentionally skipped — its svg2png() signature
    differs from cairosvg's and fails with 'BrowserType' object is not
    iterable. Playwright works reliably on Windows.
    """
    import tempfile

    # ─── 1. svglib + reportlab ────
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        from io import BytesIO

        def _svglib_render(bytestring=None, write_to=None, **kwargs):
            if bytestring is None or write_to is None:
                raise TypeError("svglib render requires bytestring and write_to")
            if isinstance(bytestring, str):
                bytestring = bytestring.encode('utf-8')
            drawing = svg2rlg(BytesIO(bytestring))
            if drawing is None:
                raise RuntimeError("svglib returned None (could not parse SVG)")
            renderPM.drawToFile(drawing, write_to, fmt="PNG")

        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                tmp = tf.name
            _svglib_render(
                bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" '
                           b'width="10" height="10">'
                           b'<rect width="10" height="10" fill="red"/></svg>',
                write_to=tmp,
            )
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.remove(tmp)
                logger.info(" svglib + reportlab works")
                return "svglib+reportlab", _svglib_render
            else:
                logger.warning("svglib produced empty output — rejecting")
        except Exception as e:
            logger.warning(f"svglib sanity check failed: {e}")

    except ImportError as e:
        logger.info(f"svglib not installed: {e}")
    except Exception as e:
        logger.warning(f"svglib import failed: {e}")

    # ─── 2. cairosvg (native) ───
    try:
        import cairosvg as _c
        svg2png = getattr(_c, 'svg2png', None)
        if callable(svg2png):
            try:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                    tmp = tf.name
                svg2png(
                    bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" '
                               b'width="10" height="10">'
                               b'<rect width="10" height="10" fill="red"/></svg>',
                    write_to=tmp,
                )
                if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                    os.remove(tmp)
                    logger.info(" cairosvg native library works")
                    return "cairosvg.svg2png", svg2png
            except Exception as e:
                logger.warning(f"cairosvg sanity check failed: {e}")
    except ImportError:
        logger.info("cairosvg not installed")
    except OSError as e:
        logger.warning(f"cairosvg native library could not be loaded: {e}")
    except Exception as e:
        logger.warning(f"cairosvg import failed: {e}")

    # ─── 3. playwright (with networkidle wait) ────
    try:
        from playwright.sync_api import sync_playwright

        def _playwright_render(bytestring=None, write_to=None, **kwargs):
            if bytestring is None or write_to is None:
                raise TypeError("playwright render requires bytestring and write_to")
            if isinstance(bytestring, bytes):
                svg_str = bytestring.decode('utf-8')
            else:
                svg_str = str(bytestring)
            html = (
                '<!DOCTYPE html><html><body style="margin:0;background:white">'
                f'{svg_str}'
                '</body></html>'
            )
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1200, "height": 900})
                page.set_content(html, wait_until="networkidle")
                page.wait_for_timeout(500)
                page.screenshot(path=write_to, full_page=True)
                browser.close()

        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                tmp = tf.name
            _playwright_render(
                bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" '
                           b'width="10" height="10">'
                           b'<rect width="10" height="10" fill="red"/></svg>',
                write_to=tmp,
            )
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.remove(tmp)
                logger.info(" playwright works")
                return "playwright", _playwright_render
        except Exception as e:
            logger.warning(f"playwright sanity check failed: {e}")

    except ImportError:
        logger.info("playwright not installed")

    return None, None


SVG_CONVERTER, _SVG_TO_PNG = _resolve_svg_converter()

if SVG_CONVERTER:
    logger.info(f" SVG→PNG converter ready: {SVG_CONVERTER}")
else:
    logger.warning(" No working SVG→PNG converter found.")
    logger.warning(" Install one of: pip install playwright && playwright install chromium")
    logger.warning(" Mermaid diagrams and SVG maps will fall back to matplotlib.")


# ═══════════════════════════════════════════════════════════════════
# IELTS Official Colors
# ═══════════════════════════════════════════════════════════════════
IELTS_GRAYSCALE = ['#000000', '#333333', '#555555', '#777777',
                   '#999999', '#BBBBBB', '#DDDDDD', '#EEEEEE']

# v9.2 — Distinct bar patterns so series are visually unambiguous.
# Previously `/` and `\` were almost indistinguishable on small charts.
# New order puts visually DIFFERENT patterns first, each repeated 3×
# to make the hatch density clearly visible at any size.
IELTS_BAR_PATTERNS = [
    '///', # series 1 — forward diagonal (dense)
    '\\\\\\', # series 2 — backward diagonal (dense)
    'xxx', # series 3 — cross-hatch
    '|||', # series 4 — vertical
    '---', # series 5 — horizontal
    '+++', # series 6 — plus grid
    '...', # series 7 — dots
    'ooo', # series 8 — small circles
    'OOO', # series 9 — large circles
    '***', # series 10 — stars
]

# v9.2 — Alternate fill tones (used together with hatch patterns)
# so that even 2-series charts (Men/Women) are unambiguous.
IELTS_BAR_FILLS = [
    '#ffffff', # white
    '#f0f0f0', # very light gray
    '#e0e0e0', # light gray
    '#d0d0d0', # medium-light gray
    '#c0c0c0', # medium gray
    '#b0b0b0', # darker gray
    '#a0a0a0', # darker still
    '#909090', # dark gray
    '#808080', # darker
    '#707070', # darkest
]

FONT_FAMILY = 'Arial, Helvetica, sans-serif'
TITLE_FONT_SIZE = 14
LABEL_FONT_SIZE = 11
TICK_FONT_SIZE = 10
LEGEND_FONT_SIZE = 10

CHART_DPI = 300

DEFAULT_DIAGRAM_ASSETS_DIR = "static/diagram_assets"


class ChartRenderer:
    """Generate IELTS Task 1 chart images — Matplotlib + Mermaid.js (assets)."""

    def __init__(self, output_dir="static/charts", use_cache=True):
        if not MATPLOTLIB_AVAILABLE:
            raise RuntimeError(
                " matplotlib is required for chart rendering. "
                "Please install: pip install matplotlib"
            )

        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.use_cache = use_cache
        self._cache = {}
        # v9 — bumped from 2 → 8 workers.
        self._executor = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="chart-renderer"
        )
        self._closed = False

        _register_renderer(self)

        if SVG_CONVERTER:
            logger.info(
                f"[ChartRenderer] Maps will use SVG renderer "
                f"(converter={SVG_CONVERTER})"
            )
        else:
            logger.info(
                "[ChartRenderer] Maps will use matplotlib grid fallback "
                "(no SVG converter available)"
            )

        logger.info(f"[ChartRenderer] Initialized - output dir: {output_dir}, DPI: {CHART_DPI}")
        logger.info(f"[ChartRenderer] Mermaid.js available: {MERMAID_AVAILABLE} (backend: {MERMAID_BACKEND})")
        logger.info(f"[ChartRenderer] Thread pool workers: 8 (thread-safe savefig)")

    # ==================== LIFECYCLE ====================

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._executor.shutdown(wait=False)
        except Exception as e:
            logger.warning(f"[ChartRenderer] Executor shutdown failed: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ==================== PUBLIC METHODS ====================

    def render(self, chart_type: str, chart_data: dict, filename: str = None) -> str:
        if not chart_data:
            raise ValueError(" chart_data is required for rendering")

        if filename is None:
            filename = f"chart_{uuid.uuid4().hex[:8]}.png"
        elif not filename.endswith('.png'):
            filename += '.png'

        if self.use_cache:
            cache_key = hashlib.md5(
                f"{chart_type}{str(chart_data)}{filename}".encode()
            ).hexdigest()
            cache_path = os.path.join(self.output_dir, f"cache_{cache_key}.png")
            if os.path.exists(cache_path):
                logger.info(f"[Cache] Returning cached: {cache_path}")
                return f"/static/charts/cache_{cache_key}.png"

        logger.info(f"Rendering {chart_type}: {filename}")

        try:
            if chart_type == 'multi_visual':
                result = self._render_multi_visual_ielts(chart_data, filename)
            elif chart_type == 'map':
                result = self._render_map_ielts(chart_data, filename)
            elif chart_type in ('diagram', 'flow_chart'):
                logger.info(
                    "[Diagram] Skipping mermaid (broken on Windows) — "
                    "using asset/matplotlib path"
                )
                if chart_type == 'diagram':
                    result = self._render_diagram_ielts(chart_data, filename)
                else:
                    result = self._render_flow_chart_ielts(chart_data, filename)
            elif chart_type in ['bar_chart', 'bar']:
                result = self._render_bar_ielts(chart_data, filename)
            elif chart_type in ['line_graph', 'line']:
                result = self._render_line_ielts(chart_data, filename)
            elif chart_type == 'pie_chart':
                result = self._render_pie_ielts(chart_data, filename)
            elif chart_type == 'table':
                result = self._render_table_ielts(chart_data, filename)
            else:
                raise ValueError(f" Unknown chart type: {chart_type}")

            if self.use_cache and result:
                cache_key = hashlib.md5(
                    f"{chart_type}{str(chart_data)}{filename}".encode()
                ).hexdigest()
                cache_path = os.path.join(self.output_dir, f"cache_{cache_key}.png")
                if not os.path.exists(cache_path):
                    try:
                        shutil.copy2(os.path.join(self.output_dir, filename), cache_path)
                    except Exception as e:
                        logger.warning(f"[Cache] Could not write cache file: {e}")

            return result

        except Exception as e:
            logger.error(f"Chart rendering failed: {e}", exc_info=True)
            return self._render_error_chart(str(e), filename)

    def render_to_base64(self, chart_type: str, chart_data: dict) -> str:
        filename = f"temp_{uuid.uuid4().hex[:8]}.png"
        filepath = self.render(chart_type, chart_data, filename)

        if filepath.startswith('/static/charts/cache_'):
            filepath = os.path.join(self.output_dir, os.path.basename(filepath))
        else:
            filepath = os.path.join(self.output_dir, filename)

        if not os.path.exists(filepath):
            raise RuntimeError(f"Chart file not found: {filepath}")

        try:
            with open(filepath, 'rb') as f:
                img_data = f.read()
        finally:
            if filename.startswith('temp_') and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

        return base64.b64encode(img_data).decode('utf-8')

    def render_async(self, chart_type: str, chart_data: dict):
        if self._closed:
            raise RuntimeError("ChartRenderer has been closed")
        return self._executor.submit(self.render, chart_type, chart_data, None)

    def render_to_pdf(self, chart_type: str, chart_data: dict, filename: str = None) -> str:
        try:
            from matplotlib.backends.backend_pdf import PdfPages
        except ImportError:
            logger.warning("PDF export not available. Install matplotlib with PDF support.")
            return self.render(chart_type, chart_data, filename)

        if filename is None:
            filename = f"chart_{uuid.uuid4().hex[:8]}.pdf"
        elif not filename.endswith('.pdf'):
            filename += '.pdf'

        png_filename = filename.replace('.pdf', '.png')
        png_path = self.render(chart_type, chart_data, png_filename)
        png_abs = os.path.join(self.output_dir, os.path.basename(png_path))

        pdf_path = os.path.join(self.output_dir, filename)
        fig = None
        try:
            with _MPL_LOCK:
                fig = plt.figure(figsize=(12, 8))
            try:
                img = plt.imread(png_abs)
                plt.imshow(img)
                plt.axis('off')
                with PdfPages(pdf_path) as pdf:
                    pdf.savefig(fig)
            finally:
                if fig is not None:
                    with _MPL_LOCK:
                        plt.close(fig)
                    fig = None
        except Exception as e:
            logger.error(f"[PDF] Failed to write PDF: {e}")
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
            return png_path
        finally:
            if os.path.exists(png_abs):
                try:
                    os.remove(png_abs)
                except Exception:
                    pass

        return f"/static/charts/{filename}"

    # ==================== ERROR CHART ====================

    def _render_error_chart(self, error_msg: str, filename: str) -> str:
        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(10, 6))
        try:
            ax.text(
                0.5, 0.5,
                f" Chart Generation Error\n\n{error_msg[:100]}\n\nPlease try again.",
                ha='center', va='center', fontsize=12, color='black', wrap=True,
            )
            ax.axis('off')
            ax.set_facecolor('white')
            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
            logger.error(f"Error chart saved: {filepath}")
            return f"/static/charts/{filename}"
        finally:
            with _MPL_LOCK:
                plt.close(fig)

    # ==================== MULTI-VISUAL ====================

    def _render_multi_visual_ielts(self, chart_data: dict, filename: str) -> str:
        from PIL import Image

        panels = chart_data.get('panels', [])
        if len(panels) != 2:
            raise ValueError(
                f" multi_visual requires exactly 2 panels, got {len(panels)}"
            )

        temp_paths = []
        try:
            for i, panel in enumerate(panels):
                sub_type = panel.get('chart_type', 'bar_chart')
                sub_data = panel.get('chart_data', {})
                if not sub_data:
                    raise ValueError(f" Panel {i} has empty chart_data")

                temp_name = f"temp_{uuid.uuid4().hex[:8]}_panel{i}.png"
                saved_cache = self.use_cache
                self.use_cache = False
                try:
                    self.render(sub_type, sub_data, temp_name)
                finally:
                    self.use_cache = saved_cache
                temp_paths.append(os.path.join(self.output_dir, temp_name))

            imgs = []
            for p in temp_paths:
                if not os.path.exists(p):
                    raise RuntimeError(f" Panel image missing: {p}")
                im = Image.open(p)
                im.load()
                imgs.append(im)

            target_h = max(im.size[1] for im in imgs)
            resized = []
            for im in imgs:
                w, h = im.size
                new_w = max(1, int(w * (target_h / h)))
                resized.append(im.resize((new_w, target_h), Image.LANCZOS))

            gap = 30
            total_w = sum(im.size[0] for im in resized) + gap
            combined = Image.new('RGB', (total_w, target_h), 'white')
            try:
                x = 0
                for im in resized:
                    combined.paste(im, (x, 0))
                    x += im.size[0] + gap

                filepath = os.path.join(self.output_dir, filename)
                combined.save(filepath, dpi=(CHART_DPI, CHART_DPI))
            finally:
                try:
                    combined.close()
                except Exception:
                    pass

            logger.info(
                f"[IELTS Multi-Visual] Saved: {filepath} "
                f"({total_w}x{target_h}, panels={[p['chart_type'] for p in panels]})"
            )
            return f"/static/charts/{filename}"

        finally:
            for p in temp_paths:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    # ==================== MERMAID.JS DIAGRAM RENDERER ====================
    # NOTE (v6): Retained for reference — `render()` no longer calls it.

    def _render_diagram_mermaid(self, chart_data: dict, filename: str, chart_type: str) -> str:
        steps = chart_data.get('steps', [])
        descriptions = chart_data.get('descriptions', [])
        title = chart_data.get('title', 'Process Diagram')

        if not steps or len(steps) < 3:
            logger.warning("Steps missing or too few, falling back to matplotlib")
            if chart_type == 'diagram':
                return self._render_diagram_ielts(chart_data, filename)
            else:
                return self._render_flow_chart_ielts(chart_data, filename)

        lines = ["graph TD"]
        lines.append(f"%% {title}")

        for i, (step, desc) in enumerate(zip(steps, descriptions)):
            node_id = f"S{i+1}"
            step_label = step[:25]
            if len(step) > 25:
                step_label += "..."
            lines.append(f' {node_id}["{step_label}"]')
            if i < len(steps) - 1:
                lines.append(f' {node_id} --> S{i+2}')

        for i, desc in enumerate(descriptions):
            if desc:
                node_id = f"S{i+1}"
                clean_desc = desc.replace('"', '\\"').replace('\n', ' ')
                lines.append(f' click {node_id} callback "{clean_desc}"')

        mermaid_code = "\n".join(lines)
        logger.debug(f"Mermaid code:\n{mermaid_code}")

        filepath = os.path.join(self.output_dir, filename)

        try:
            import mermaidx

            try:
                png_data = mermaidx.render(mermaid_code, output_format="png")

                if not isinstance(png_data, (bytes, bytearray)):
                    if hasattr(png_data, 'render'):
                        try:
                            png_data = png_data.render(output_format="png")
                        except TypeError:
                            png_data = png_data.render()
                    elif hasattr(png_data, 'to_png'):
                        png_data = png_data.to_png()
                    elif hasattr(png_data, 'png_bytes'):
                        png_data = png_data.png_bytes
                    elif hasattr(png_data, 'bytes'):
                        png_data = png_data.bytes
                    else:
                        raise TypeError(
                            f"mermaidx returned unsupported type "
                            f"{type(png_data).__name__}; no PNG conversion path found"
                        )

                if isinstance(png_data, str):
                    png_data = png_data.encode('utf-8')

                with open(filepath, 'wb') as f:
                    f.write(png_data)
                logger.info(f"[Mermaid] Diagram saved via mermaidx PNG: {filepath}")
                return f"/static/charts/{filename}"
            except Exception as e:
                logger.warning(f"mermaidx PNG render failed: {e}, trying SVG conversion")

            try:
                svg_content = mermaidx.render(mermaid_code, output_format="svg")
            except Exception as e2:
                logger.warning(f"mermaidx SVG render failed: {e2}, falling back to matplotlib")
                if chart_type == 'diagram':
                    return self._render_diagram_ielts(chart_data, filename)
                else:
                    return self._render_flow_chart_ielts(chart_data, filename)

            if isinstance(svg_content, (bytes, bytearray)):
                svg_str = svg_content.decode('utf-8', errors='replace')
            else:
                svg_str = str(svg_content)

            if _SVG_TO_PNG is not None:
                try:
                    _SVG_TO_PNG(bytestring=svg_str.encode('utf-8'), write_to=filepath)
                except TypeError:
                    _SVG_TO_PNG(svg_str.encode('utf-8'), filepath)
                logger.info(f"[Mermaid] Diagram saved via mermaidx + {SVG_CONVERTER}: {filepath}")
                return f"/static/charts/{filename}"
            else:
                svg_filepath = filepath.replace('.png', '.svg')
                try:
                    with open(svg_filepath, 'w', encoding='utf-8') as f:
                        f.write(svg_str)
                except Exception:
                    pass
                logger.warning(
                    "No working SVG→PNG converter available. "
                    f"SVG kept at {svg_filepath}; falling back to matplotlib."
                )
                if chart_type == 'diagram':
                    return self._render_diagram_ielts(chart_data, filename)
                else:
                    return self._render_flow_chart_ielts(chart_data, filename)

        except Exception as e:
            logger.error(f"Mermaid rendering failed: {e}, falling back to matplotlib")
            if chart_type == 'diagram':
                return self._render_diagram_ielts(chart_data, filename)
            else:
                return self._render_flow_chart_ielts(chart_data, filename)

    # ==================== MAP RENDERER ====================

    def _render_map_ielts(self, chart_data: dict, filename: str) -> str:
        before = chart_data.get('before', [])
        after = chart_data.get('after', [])
        if not before or not after:
            raise ValueError(" Map missing 'before' or 'after' data")

        try:
            from .map_renderer import MapRenderer, CAIRO_AVAILABLE
        except Exception as e:
            logger.info(f"[Map] MapRenderer module not importable ({e}) — using matplotlib grid")
            return self._render_map_matplotlib(chart_data, filename)

        if not CAIRO_AVAILABLE:
            logger.info("[Map] SVG converter not available — using matplotlib grid fallback")
            return self._render_map_matplotlib(chart_data, filename)

        try:
            logger.info("[Map] Rendering with SVG-based MapRenderer (high quality)")
            renderer = MapRenderer(output_dir=self.output_dir)
            result = renderer.render(chart_data, filename)
            logger.info(f"[Map] SVG render succeeded: {result}")
            return result
        except Exception as e:
            logger.warning(
                f"[Map] SVG renderer failed ({e}) — falling back to matplotlib grid"
            )
            return self._render_map_matplotlib(chart_data, filename)

    def _render_map_matplotlib(self, chart_data: dict, filename: str) -> str:
        before = chart_data.get('before', [])
        after = chart_data.get('after', [])
        title = chart_data.get('title', 'Map Comparison')
        changes = chart_data.get('changes', [])

        if not before or not after:
            raise ValueError(" Map missing 'before' or 'after' data")

        has_changes = bool(changes)

        with _MPL_LOCK:
            fig = plt.figure(figsize=(20, 11.5), facecolor='white')
        try:
            if has_changes:
                map_bottom = 0.30
                map_height = 0.62
            else:
                map_bottom = 0.04
                map_height = 0.88

            ax1 = fig.add_axes([0.02, map_bottom, 0.47, map_height])
            ax2 = fig.add_axes([0.51, map_bottom, 0.47, map_height])

            fig.suptitle(title, fontsize=22, fontweight='bold', color='black', y=0.965)

            positions_before = self._grid_positions(len(before))
            positions_after = self._grid_positions(len(after))

            box_w_b, box_h_b = self._grid_box_size(len(before))
            box_w_a, box_h_a = self._grid_box_size(len(after))
            box_w = min(box_w_b, box_w_a)
            box_h = min(box_h_b, box_h_a)

            def draw_map(ax, items, positions, label):
                ax.set_xlim(0, 520)
                ax.set_ylim(0, 580)
                ax.set_aspect('equal')
                ax.axis('off')
                ax.set_title(label, fontsize=20, fontweight='bold', color='black', pad=16)

                ax.add_patch(Rectangle((0, 0), 520, 580, facecolor='#f8f8f8',
                                       edgecolor='black', linewidth=2.5,
                                       fill=True, zorder=0))
                ax.add_patch(Rectangle((10, 10), 500, 560, facecolor='none',
                                       edgecolor='black', linewidth=0.6,
                                       fill=False, zorder=1))

                ax.annotate('N', xy=(260, 22), ha='center', fontsize=14,
                            fontweight='bold', color='black', zorder=6)
                ax.annotate('', xy=(260, 34), xytext=(260, 54),
                            arrowprops=dict(arrowstyle='->', color='black', lw=2),
                            zorder=6)

                ax.plot([380, 440], [552, 552], color='black', linewidth=2.5, zorder=6)
                ax.plot([380, 380], [548, 556], color='black', linewidth=1.5, zorder=6)
                ax.plot([440, 440], [548, 556], color='black', linewidth=1.5, zorder=6)
                ax.text(380, 566, '0', fontsize=9, ha='center', color='black', zorder=6)
                ax.text(410, 566, '50m', fontsize=9, ha='center', color='black', zorder=6)
                ax.text(440, 566, '100m', fontsize=9, ha='center', color='black', zorder=6)

                grays = ['#e8e8e8', '#d5d5d5', '#bebebe', '#a8a8a8',
                         '#929292', '#7c7c7c', '#666666', '#505050']

                for i, item in enumerate(items):
                    if i >= len(positions):
                        continue
                    pos = positions[i]
                    x = int(pos['x'])
                    y = int(pos['y'])

                    full_name = item if isinstance(item, str) else str(item)
                    name = full_name.split("(")[0].strip()
                    size = ""
                    if "(" in full_name:
                        size = full_name.split("(")[1].replace(")", "").strip()

                    gray_shade = grays[i % len(grays)]

                    rect = Rectangle((x - box_w / 2, y - box_h / 2), box_w, box_h,
                                     facecolor=gray_shade, edgecolor='black',
                                     linewidth=2, zorder=3)
                    ax.add_patch(rect)

                    lines = self._wrap_text(name, max_chars=15, max_lines=3)
                    n_lines = len(lines)
                    font_size = 12 if n_lines == 1 else (11 if n_lines == 2 else 10)
                    line_height = 13

                    total_text_h = (n_lines - 1) * line_height
                    start_y = y + total_text_h / 2

                    for k, line in enumerate(lines):
                        ax.text(x, start_y - k * line_height, line,
                                ha='center', va='center',
                                fontsize=font_size, fontweight='bold',
                                color='black', zorder=4)

                    if size:
                        ax.text(x, y - box_h / 2 - 10, f'({size})',
                                ha='center', va='center', fontsize=9,
                                color='#444', zorder=4)

            draw_map(ax1, before, positions_before, "BEFORE")
            draw_map(ax2, after, positions_after, "AFTER")

            if changes:
                table_data = [["Feature", "Change"]]
                for b, c in zip(before[:6], changes[:6]):
                    b_name = b.split("(")[0].strip() if isinstance(b, str) else str(b)
                    c_text = c if isinstance(c, str) else str(c)
                    table_data.append([b_name[:45], c_text[:95]])

                ax_table = fig.add_axes([0.05, 0.02, 0.90, 0.22])
                ax_table.axis('off')

                table = ax_table.table(
                    cellText=table_data, loc='center', cellLoc='left',
                    colWidths=[0.32, 0.68],
                )
                table.auto_set_font_size(False)
                table.set_fontsize(13)
                table.scale(1, 2.6)

                for j in range(2):
                    cell = table[(0, j)]
                    cell.set_facecolor('#333333')
                    cell.set_text_props(weight='bold', color='white', fontsize=15)

                for i in range(1, len(table_data)):
                    for j in range(2):
                        cell = table[(i, j)]
                        if i % 2 == 0:
                            cell.set_facecolor('#f0f0f0')
                        cell.set_text_props(color='black', fontsize=12)

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Map (matplotlib grid)] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ==================== GRID LAYOUT FOR MAPS ====================

    @staticmethod
    def _grid_positions(n: int, map_w: int = 520, map_h: int = 580) -> list:
        import math

        if n <= 0:
            return []

        if n == 1:
            cols, rows = 1, 1
        elif n == 2:
            cols, rows = 2, 1
        elif n <= 4:
            cols, rows = 2, 2
        elif n <= 6:
            cols, rows = 3, 2
        elif n <= 9:
            cols, rows = 3, 3
        elif n <= 12:
            cols, rows = 4, 3
        else:
            cols = 4
            rows = math.ceil(n / 4)

        top = 90
        bottom = 80
        left = 25
        right = 25

        usable_w = map_w - left - right
        usable_h = map_h - top - bottom

        cell_w = usable_w / cols
        cell_h = usable_h / rows

        positions = []
        for i in range(n):
            r = i // cols
            c = i % cols
            x = left + cell_w * (c + 0.5)
            y = map_h - top - cell_h * (r + 0.5)
            positions.append({'x': int(x), 'y': int(y)})

        return positions

    @staticmethod
    def _grid_box_size(n: int, map_w: int = 520, map_h: int = 580) -> tuple:
        import math

        if n <= 0:
            return 120, 75

        if n == 1:
            cols, rows = 1, 1
        elif n == 2:
            cols, rows = 2, 1
        elif n <= 4:
            cols, rows = 2, 2
        elif n <= 6:
            cols, rows = 3, 2
        elif n <= 9:
            cols, rows = 3, 3
        elif n <= 12:
            cols, rows = 4, 3
        else:
            cols = 4
            rows = math.ceil(n / 4)

        top = 90
        bottom = 80
        left = 25
        right = 25

        cell_w = (map_w - left - right) / cols
        cell_h = (map_h - top - bottom) / rows

        box_w = min(150, int(cell_w - 8))
        box_h = min(85, int(cell_h - 8))

        return max(70, box_w), max(50, box_h)

    # ==================== TEXT WRAPPING HELPER ====================

    @staticmethod
    def _wrap_text(text: str, max_chars: int = 15, max_lines: int = 3) -> list:
        words = (text or "").split()
        if not words:
            return [""]
        lines = []
        current = ""
        for w in words:
            if not current:
                current = w
            elif len(current) + 1 + len(w) <= max_chars:
                current += " " + w
            else:
                lines.append(current)
                current = w
                if len(lines) == max_lines - 1:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines:
            joined = " ".join(lines)
            full = (text or "").strip()
            if len(joined) < len(full) and len(lines[-1]) + 3 <= max_chars:
                lines[-1] = lines[-1] + "..."
        return lines[:max_lines]

    def _generate_positions_for_map(self, num_features: int) -> list:
        import math
        positions = []
        cols = math.ceil(math.sqrt(num_features))
        rows = math.ceil(num_features / cols)

        for i in range(num_features):
            row = i // cols
            col = i % cols
            x = 90 + (col * (340 / max(cols - 1, 1))) if cols > 1 else 260
            y = 90 + (row * (380 / max(rows - 1, 1))) if rows > 1 else 280
            x += random.randint(-12, 12)
            y += random.randint(-12, 12)
            x = max(90, min(430, x))
            y = max(90, min(470, y))
            types = ['building', 'house', 'shop', 'school',
                     'hospital', 'park', 'housing', 'church']
            positions.append({'x': int(x), 'y': int(y), 'type': random.choice(types)})
        return positions

    # ==================== BAR CHART ====================

    def _render_bar_ielts(self, chart_data: dict, filename: str) -> str:
        labels = chart_data.get('labels', [])
        datasets = chart_data.get('datasets', [])

        if not labels:
            raise ValueError(" Bar chart missing 'labels'")
        if not datasets:
            raise ValueError(" Bar chart missing 'datasets'")

        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(12, 7))
        try:
            ax.set_facecolor('white')

            x = np.arange(len(labels))
            width = 0.7 / len(datasets)

            for i, dataset in enumerate(datasets):
                values = dataset.get('values', [])
                if not values:
                    raise ValueError(f" Dataset {i} missing 'values'")
                if len(values) != len(labels):
                    raise ValueError(f" Dataset {i} values length mismatch")

                label = dataset.get('label', f'Series {i+1}')
                offset = (i - len(datasets) / 2 + 0.5) * width

                # v9.2 — alternate fill tone + distinct hatch pattern
                fill_tone = IELTS_BAR_FILLS[i % len(IELTS_BAR_FILLS)]
                hatch_pattern = IELTS_BAR_PATTERNS[i % len(IELTS_BAR_PATTERNS)]

                bars = ax.bar(
                    x + offset, values, width, label=label,
                    color=fill_tone, edgecolor='black', linewidth=1.5,
                    hatch=hatch_pattern,
                )

                for bar, val in zip(bars, values):
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + max(values) * 0.01,
                        str(val), ha='center', va='bottom',
                        fontsize=9, fontweight='bold', color='black',
                    )

            ax.set_xlabel('Categories', fontsize=12, fontweight='bold', color='black')
            ax.set_ylabel('Values', fontsize=12, fontweight='bold', color='black')
            ax.set_title(chart_data.get('title', 'Bar Chart'),
                         fontsize=14, fontweight='bold', color='black', pad=15)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=10, color='black')
            ax.legend(loc='upper left', frameon=True, framealpha=1,
                      edgecolor='black', fontsize=10)
            ax.grid(True, alpha=0.3, axis='y', linestyle='--', color='#888888')
            ax.set_axisbelow(True)

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Bar] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ==================== LINE GRAPH ====================

    def _render_line_ielts(self, chart_data: dict, filename: str) -> str:
        labels = chart_data.get('labels', [])
        datasets = chart_data.get('datasets', [])

        if not labels:
            raise ValueError(" Line graph missing 'labels'")
        if not datasets:
            raise ValueError(" Line graph missing 'datasets'")

        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(12, 7))
        try:
            ax.set_facecolor('white')

            line_styles = ['-', '--', '-.', ':']
            marker_styles = ['o', 's', '^', 'D', 'v', 'p', '*', 'x']

            for i, dataset in enumerate(datasets):
                values = dataset.get('values', [])
                if not values:
                    raise ValueError(f" Dataset {i} missing 'values'")
                if len(values) != len(labels):
                    raise ValueError(f" Dataset {i} values length mismatch")

                label = dataset.get('label', f'Series {i+1}')
                color = IELTS_GRAYSCALE[(i * 2) % len(IELTS_GRAYSCALE)]
                ax.plot(
                    labels, values,
                    marker=marker_styles[i % len(marker_styles)],
                    linewidth=2.5, markersize=8, label=label,
                    color=color, linestyle=line_styles[i % len(line_styles)],
                )

                for x, y in zip(labels, values):
                    ax.annotate(
                        str(y), (x, y), textcoords="offset points",
                        xytext=(0, 12), ha='center',
                        fontsize=9, fontweight='bold', color='black',
                    )

            ax.set_xlabel('Categories', fontsize=12, fontweight='bold', color='black')
            ax.set_ylabel('Values', fontsize=12, fontweight='bold', color='black')
            ax.set_title(chart_data.get('title', 'Line Graph'),
                         fontsize=14, fontweight='bold', color='black', pad=15)
            ax.legend(loc='upper left', frameon=True, framealpha=1,
                      edgecolor='black', fontsize=10)
            ax.grid(True, alpha=0.3, linestyle='--', color='#888888')
            ax.set_axisbelow(True)

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Line] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ==================== PIE CHART ====================

    def _render_pie_ielts(self, chart_data: dict, filename: str) -> str:
        labels = chart_data.get('labels', [])
        values = chart_data.get('values', [])

        if not labels:
            raise ValueError(" Pie chart missing 'labels'")
        if not values:
            raise ValueError(" Pie chart missing 'values'")
        if len(labels) != len(values):
            raise ValueError(" Labels length vs Values length mismatch")

        total = sum(values)
        if abs(total - 100) > 5 and total > 0:
            values = [v * 100 / total for v in values]

        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(10, 8))
        try:
            ax.set_facecolor('white')

            grays = ['#333333', '#555555', '#777777', '#999999',
                     '#BBBBBB', '#DDDDDD', '#EEEEEE', '#FFFFFF']

            wedges, texts, autotexts = ax.pie(
                values, labels=labels, autopct='%1.1f%%',
                startangle=90, colors=grays[:len(labels)],
                textprops={'fontsize': 11, 'color': 'black', 'fontweight': 'bold'},
            )

            for autotext in autotexts:
                autotext.set_color('white')
                autotext.set_fontweight('bold')
                autotext.set_fontsize(11)

            ax.set_title(chart_data.get('title', 'Pie Chart'),
                         fontsize=14, fontweight='bold', color='black', pad=20)
            ax.axis('equal')

            ax.legend(wedges, labels, title="Categories", loc="center left",
                      bbox_to_anchor=(1, 0, 0.5, 1), fontsize=10,
                      title_fontsize=11, frameon=True, edgecolor='black')

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Pie] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ==================== TABLE ====================

    def _render_table_ielts(self, chart_data: dict, filename: str) -> str:
        headers = chart_data.get('headers', [])
        rows = chart_data.get('rows', [])

        if not headers and not rows:
            raise ValueError(" Table missing both 'headers' and 'rows'")
        if not rows:
            raise ValueError(" Table missing 'rows' data")

        table_data = [headers] + rows if headers else rows

        n_cols = len(table_data[0]) if table_data else 1
        n_rows = len(table_data)
        fig_width = max(10, n_cols * 1.5)
        fig_height = max(4, n_rows * 0.5)

        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        try:
            ax.axis('off')
            ax.set_facecolor('white')

            table = ax.table(
                cellText=table_data, loc='center', cellLoc='center',
                colWidths=[0.15] * n_cols,
            )

            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1.2, 1.5)

            if headers:
                for j in range(n_cols):
                    table[(0, j)].set_facecolor('#333333')
                    table[(0, j)].set_text_props(weight='bold', color='white', fontsize=11)

            for i in range(1, n_rows):
                for j in range(n_cols):
                    if i % 2 == 0:
                        table[(i, j)].set_facecolor('#f0f0f0')
                    table[(i, j)].set_text_props(color='black')

            ax.set_title(chart_data.get('title', 'Data Table'),
                         fontsize=14, fontweight='bold', color='black', pad=20)

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Table] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ============================================================================
    # DIAGRAM — TOPIC-FIRST: use diagram_key, fallback to match_diagram
    # ============================================================================

    def _render_diagram_ielts(self, chart_data: dict, filename: str) -> str:
        """
        Process diagram.

        Order of preference:
          1. chart_data['diagram_key'] — explicit key from the topic-first picker
          2. match_diagram() — legacy keyword matching (backward compat)
          3. Matplotlib box-and-arrow fallback
        """
        try:
            from .diagram_asset_loader import (
                match_diagram,
                render_diagram_from_asset,
            )

            diagram_key = chart_data.get('diagram_key')
            if diagram_key:
                logger.info(
                    f"[Diagram] Using explicit diagram_key='{diagram_key}'"
                )
                result = render_diagram_from_asset(
                    asset_key=diagram_key,
                    chart_data=chart_data,
                    output_dir=self.output_dir,
                    assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                    filename=filename,
                )
                if result:
                    logger.info(f"[Diagram] Rendered via diagram_key: {diagram_key}")
                    return result
                logger.warning(
                    f"[Diagram] Render for key '{diagram_key}' returned None"
                )

            if not diagram_key:
                asset_key = match_diagram(
                    topic=chart_data.get('topic', ''),
                    title=chart_data.get('title', ''),
                    assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                )
                if asset_key:
                    logger.info(
                        f"[Diagram] Matched SVG asset '{asset_key}' "
                        f"(legacy) for topic='{chart_data.get('topic', '')}'"
                    )
                    result = render_diagram_from_asset(
                        asset_key=asset_key,
                        chart_data=chart_data,
                        output_dir=self.output_dir,
                        assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                        filename=filename,
                    )
                    if result:
                        return result
                else:
                    logger.info(
                        "[Diagram] No diagram_key and no keyword match — "
                        "using matplotlib fallback"
                    )
        except ImportError as e:
            logger.info(
                f"[Diagram] diagram_asset_loader not available ({e}) — "
                f"using matplotlib fallback"
            )
        except Exception as e:
            logger.warning(
                f"[Diagram] Asset library failed unexpectedly ({e}) — "
                f"using matplotlib fallback"
            )

        # ── Matplotlib fallback ──
        steps = chart_data.get('steps', [])
        descriptions = chart_data.get('descriptions', [])
        title = chart_data.get('title', 'Process Diagram')

        if not steps:
            raise ValueError(" Diagram missing 'steps' array")
        if len(steps) < 3:
            raise ValueError(f" Diagram needs at least 3 steps, got {len(steps)}")

        if len(descriptions) < len(steps):
            descriptions = descriptions + [''] * (len(steps) - len(descriptions))

        circled_numbers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
                           "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳"]

        with _MPL_LOCK:
            fig, ax = plt.subplots(figsize=(14, max(10, len(steps) * 0.9)))
        try:
            ax.set_xlim(0, 10)
            ax.set_ylim(0, len(steps) + 1)
            ax.axis('off')
            ax.set_facecolor('white')
            fig.suptitle(title, fontsize=16, fontweight='bold', color='black', y=0.98)

            grays = ['#333333', '#555555', '#777777', '#999999', '#BBBBBB', '#DDDDDD']

            for i, (step, desc) in enumerate(zip(steps, descriptions)):
                y_pos = len(steps) - i
                color = grays[i % len(grays)]

                ax.text(1.5, y_pos, circled_numbers[i], fontsize=26,
                        ha='center', va='center',
                        color=color, fontweight='bold', zorder=6)

                rect = FancyBboxPatch((2.3, y_pos - 0.55), 3.2, 1.1,
                                      boxstyle="round,pad=0.1",
                                      facecolor='white', edgecolor='black',
                                      linewidth=2, zorder=4)
                ax.add_patch(rect)
                ax.text(3.9, y_pos, step, fontsize=11, ha='center', va='center',
                        fontweight='bold', color='black', wrap=True)

                rect2 = FancyBboxPatch((6.0, y_pos - 0.55), 3.6, 1.1,
                                       boxstyle="round,pad=0.1", facecolor='white',
                                       edgecolor='black', linewidth=1.5, zorder=3)
                ax.add_patch(rect2)
                ax.text(7.8, y_pos, desc[:120], fontsize=10,
                        ha='center', va='center', color='black', wrap=True)

                if i < len(steps) - 1:
                    ax.annotate('', xy=(3.9, y_pos - 0.9), xytext=(3.9, y_pos - 1.3),
                                arrowprops=dict(arrowstyle='->', color='black', lw=2,
                                                connectionstyle='arc3,rad=0'))

            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(filepath, dpi=CHART_DPI, bbox_inches='tight', facecolor='white')
        finally:
            with _MPL_LOCK:
                plt.close(fig)

        logger.info(f"[IELTS Diagram (Matplotlib Fallback)] Saved: {os.path.join(self.output_dir, filename)}")
        return f"/static/charts/{filename}"

    # ============================================================================
    # FLOW CHART — PROFESSIONAL REDESIGN (v8) + THREAD-SAFE (v9.1)
    # ============================================================================

    def _render_flow_chart_ielts(self, chart_data: dict, filename: str) -> str:
        """
        Flow chart — Professional redesign with:
          • Stadium shapes (start/end), rectangles (process), diamonds (decision)
          • Colored badge circles with stage numbers
          • Right-side description panels with accent bars
          • Colored arrow connectors with arrowheads
          • Title banner + legend
        """
        try:
            from .diagram_asset_loader import (
                match_diagram,
                render_diagram_from_asset,
            )

            asset_chart_data = dict(chart_data)
            if 'steps' not in asset_chart_data and 'stages' in asset_chart_data:
                asset_chart_data['steps'] = list(asset_chart_data['stages'])

            diagram_key = chart_data.get('diagram_key')
            if diagram_key:
                logger.info(
                    f"[FlowChart] Using explicit diagram_key='{diagram_key}'"
                )
                result = render_diagram_from_asset(
                    asset_key=diagram_key,
                    chart_data=asset_chart_data,
                    output_dir=self.output_dir,
                    assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                    filename=filename,
                )
                if result:
                    logger.info(f"[FlowChart] Rendered via diagram_key: {diagram_key}")
                    return result
                logger.warning(
                    f"[FlowChart] Render for key '{diagram_key}' returned None"
                )

            if not diagram_key:
                asset_key = match_diagram(
                    topic=chart_data.get('topic', ''),
                    title=chart_data.get('title', ''),
                    assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                )
                if asset_key:
                    logger.info(
                        f"[FlowChart] Matched SVG asset '{asset_key}' (legacy)"
                    )
                    result = render_diagram_from_asset(
                        asset_key=asset_key,
                        chart_data=asset_chart_data,
                        output_dir=self.output_dir,
                        assets_dir=DEFAULT_DIAGRAM_ASSETS_DIR,
                        filename=filename,
                    )
                    if result:
                        return result
                else:
                    logger.info(
                        "[FlowChart] No diagram_key and no keyword match — "
                        "using professional matplotlib fallback"
                    )
        except ImportError as e:
            logger.info(
                f"[FlowChart] diagram_asset_loader not available ({e}) — "
                f"using professional matplotlib fallback"
            )
        except Exception as e:
            logger.warning(
                f"[FlowChart] Asset library failed unexpectedly ({e}) — "
                f"using professional matplotlib fallback"
            )

        # ══════════════════════════════════════════════════════════════
        # PROFESSIONAL MATPLOTLIB FLOWCHART
        # ══════════════════════════════════════════════════════════════
        stages = chart_data.get('stages', []) or chart_data.get('steps', [])
        descriptions = list(chart_data.get('descriptions', []))
        decisions = chart_data.get('decisions', [])
        title = chart_data.get('title', 'Flow Chart')

        if not stages:
            raise ValueError(" Flow chart missing 'stages' array")
        if len(stages) < 3:
            raise ValueError(
                f" Flow chart needs at least 3 stages, got {len(stages)}"
            )

        while len(descriptions) < len(stages):
            descriptions.append('')

        COLORS = {
            'start_end': {'fill': '#D1FAE5', 'edge': '#10B981', 'text': '#065F46'},
            'process': {'fill': '#DBEAFE', 'edge': '#3B82F6', 'text': '#1E40AF'},
            'decision': {'fill': '#FEF3C7', 'edge': '#F59E0B', 'text': '#92400E'},
            'arrow': '#64748B',
            'title_bg': '#1E293B',
            'title_text': '#FFFFFF',
            'desc_bg': '#F8FAFC',
            'desc_edge': '#CBD5E1',
            'canvas': '#FFFFFF',
            'shadow': '#E2E8F0',
        }

        NUM_STAGES = len(stages)
        fig_h = max(11, NUM_STAGES * 1.35 + 2.5)
        fig_w = 16

        with _MPL_LOCK:
            fig = plt.figure(figsize=(fig_w, fig_h), facecolor=COLORS['canvas'])
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.axis('off')
        ax.set_facecolor(COLORS['canvas'])

        try:
            # ── Title banner ──
            ax.add_patch(FancyBboxPatch(
                (5, 93.5), 90, 5.5,
                boxstyle="round,pad=0.3,rounding_size=0.6",
                facecolor=COLORS['title_bg'],
                edgecolor=COLORS['title_bg'],
                linewidth=0, zorder=10,
            ))
            ax.text(
                50, 96.4, title,
                ha='center', va='center',
                fontsize=17, fontweight='bold',
                color=COLORS['title_text'], zorder=11,
            )

            FLOW_CX = 30
            SHAPE_W = 32
            SHAPE_H = 6.2
            DESC_X = 60
            DESC_W = 35
            DESC_H = 6.2

            top = 88
            bottom = 6
            step_y = (top - bottom) / (NUM_STAGES - 1) if NUM_STAGES > 1 else 0

            for i, stage in enumerate(stages):
                y = top - i * step_y
                desc = descriptions[i] if i < len(descriptions) else ''

                is_decision = i < len(decisions) and decisions[i] is not None
                is_first = (i == 0)
                is_last = (i == NUM_STAGES - 1)

                if is_decision:
                    shape_key = 'decision'
                elif is_first or is_last:
                    shape_key = 'start_end'
                else:
                    shape_key = 'process'

                col = COLORS[shape_key]

                # ── Shape with shadow ──
                if is_decision:
                    dx, dy = SHAPE_W * 0.75, SHAPE_H * 1.15
                    diamond_pts = [
                        (FLOW_CX, y + dy),
                        (FLOW_CX + dx, y),
                        (FLOW_CX, y - dy),
                        (FLOW_CX - dx, y),
                    ]
                    shadow_pts = [(px + 0.5, py - 0.5) for px, py in diamond_pts]
                    ax.add_patch(Polygon(
                        shadow_pts, closed=True,
                        facecolor=COLORS['shadow'],
                        edgecolor='none', zorder=2,
                    ))
                    ax.add_patch(Polygon(
                        diamond_pts, closed=True,
                        facecolor=col['fill'],
                        edgecolor=col['edge'],
                        linewidth=2.2, zorder=3,
                    ))
                elif is_first or is_last:
                    ax.add_patch(FancyBboxPatch(
                        (FLOW_CX - SHAPE_W / 2 + 0.5, y - SHAPE_H / 2 - 0.5),
                        SHAPE_W, SHAPE_H,
                        boxstyle=f"round,pad=0.1,rounding_size={SHAPE_H / 2}",
                        facecolor=COLORS['shadow'], edgecolor='none', zorder=2,
                    ))
                    ax.add_patch(FancyBboxPatch(
                        (FLOW_CX - SHAPE_W / 2, y - SHAPE_H / 2),
                        SHAPE_W, SHAPE_H,
                        boxstyle=f"round,pad=0.1,rounding_size={SHAPE_H / 2}",
                        facecolor=col['fill'],
                        edgecolor=col['edge'],
                        linewidth=2.2, zorder=3,
                    ))
                else:
                    ax.add_patch(FancyBboxPatch(
                        (FLOW_CX - SHAPE_W / 2 + 0.5, y - SHAPE_H / 2 - 0.5),
                        SHAPE_W, SHAPE_H,
                        boxstyle="round,pad=0.1,rounding_size=0.8",
                        facecolor=COLORS['shadow'], edgecolor='none', zorder=2,
                    ))
                    ax.add_patch(FancyBboxPatch(
                        (FLOW_CX - SHAPE_W / 2, y - SHAPE_H / 2),
                        SHAPE_W, SHAPE_H,
                        boxstyle="round,pad=0.1,rounding_size=0.8",
                        facecolor=col['fill'],
                        edgecolor=col['edge'],
                        linewidth=2.2, zorder=3,
                    ))

                # ── Stage number badge ──
                badge_x = FLOW_CX - SHAPE_W / 2 - 3.5
                ax.add_patch(Circle(
                    (badge_x, y), 1.6,
                    facecolor=col['edge'], edgecolor='white',
                    linewidth=1.5, zorder=5,
                ))
                ax.text(
                    badge_x, y, str(i + 1),
                    ha='center', va='center',
                    fontsize=10, fontweight='bold',
                    color='white', zorder=6,
                )

                # ── Stage text ──
                stage_text = stage.strip()
                if len(stage_text) > 28:
                    words = stage_text.split()
                    line1, line2 = '', ''
                    for w in words:
                        if len(line1) + len(w) + 1 <= 28 and not line2:
                            line1 += (' ' if line1 else '') + w
                        else:
                            line2 += (' ' if line2 else '') + w
                    if len(line2) > 32:
                        line2 = line2[:29] + '...'
                    ax.text(
                        FLOW_CX, y + 0.9, line1,
                        ha='center', va='center',
                        fontsize=10.5, fontweight='bold',
                        color=col['text'], zorder=6,
                    )
                    ax.text(
                        FLOW_CX, y - 1.2, line2,
                        ha='center', va='center',
                        fontsize=10.5, fontweight='bold',
                        color=col['text'], zorder=6,
                    )
                else:
                    ax.text(
                        FLOW_CX, y, stage_text,
                        ha='center', va='center',
                        fontsize=11, fontweight='bold',
                        color=col['text'], zorder=6,
                    )

                # ── Arrow to next stage ──
                if i < NUM_STAGES - 1:
                    next_y = top - (i + 1) * step_y

                    if is_decision:
                        start_y = y - SHAPE_H * 1.15
                    else:
                        start_y = y - SHAPE_H / 2

                    next_is_decision = (
                        (i + 1) < len(decisions) and decisions[i + 1] is not None
                    )
                    if next_is_decision:
                        end_y = next_y + SHAPE_H * 1.15
                    else:
                        end_y = next_y + SHAPE_H / 2

                    ax.annotate(
                        '',
                        xy=(FLOW_CX, end_y),
                        xytext=(FLOW_CX, start_y),
                        arrowprops=dict(
                            arrowstyle='-|>',
                            color=COLORS['arrow'],
                            lw=2.0,
                            mutation_scale=18,
                        ),
                        zorder=1,
                    )

                    if is_decision and decisions[i]:
                        mid_y = (start_y + end_y) / 2
                        ax.text(
                            FLOW_CX + 2.5, mid_y, 'Yes',
                            ha='left', va='center',
                            fontsize=9, style='italic',
                            color=COLORS['arrow'], zorder=5,
                        )
                        ax.text(
                            FLOW_CX - 2.5, y, decisions[i],
                            ha='right', va='center',
                            fontsize=8.5, style='italic',
                            color='#94A3B8', zorder=5,
                        )

                # ── Description panel ──
                if desc:
                    desc_wrapped = self._wrap_text(desc, max_chars=42, max_lines=3)
                    desc_h = DESC_H + (len(desc_wrapped) - 1) * 1.3

                    ax.add_patch(FancyBboxPatch(
                        (DESC_X, y - desc_h / 2),
                        DESC_W, desc_h,
                        boxstyle="round,pad=0.15,rounding_size=0.5",
                        facecolor=COLORS['desc_bg'],
                        edgecolor=COLORS['desc_edge'],
                        linewidth=1.0, zorder=3,
                    ))
                    ax.add_patch(Rectangle(
                        (DESC_X, y - desc_h / 2),
                        0.6, desc_h,
                        facecolor=col['edge'],
                        edgecolor='none', zorder=4,
                    ))

                    if len(desc_wrapped) == 1:
                        ax.text(
                            DESC_X + DESC_W / 2 + 0.3, y, desc_wrapped[0],
                            ha='center', va='center',
                            fontsize=9, color='#334155',
                            zorder=5, style='italic',
                        )
                    else:
                        for k, line in enumerate(desc_wrapped):
                            offset = (len(desc_wrapped) - 1) / 2 - k
                            ax.text(
                                DESC_X + DESC_W / 2 + 0.3, y + offset * 1.3, line,
                                ha='center', va='center',
                                fontsize=9, color='#334155',
                                zorder=5, style='italic',
                            )

            # ── Column headers ──
            header_y = 90.5
            ax.text(
                FLOW_CX, header_y, 'PROCESS',
                ha='center', va='center',
                fontsize=10, fontweight='bold',
                color='#64748B', zorder=6, alpha=0.8,
            )
            ax.text(
                DESC_X + DESC_W / 2, header_y, 'DETAILS',
                ha='center', va='center',
                fontsize=10, fontweight='bold',
                color='#64748B', zorder=6, alpha=0.8,
            )

            # ── Legend ──
            legend_y = 2.8
            legend_items = [
                ('Start / End', COLORS['start_end']),
                ('Process', COLORS['process']),
                ('Decision', COLORS['decision']),
            ]
            legend_x = 8
            for label, col in legend_items:
                ax.add_patch(FancyBboxPatch(
                    (legend_x, legend_y - 0.9), 2.6, 1.8,
                    boxstyle="round,pad=0.1,rounding_size=0.3",
                    facecolor=col['fill'],
                    edgecolor=col['edge'],
                    linewidth=1.4, zorder=5,
                ))
                ax.text(
                    legend_x + 3.3, legend_y, label,
                    ha='left', va='center',
                    fontsize=9, color='#475569', zorder=5,
                )
                legend_x += 15

            # ── Save ──
            filepath = os.path.join(self.output_dir, filename)
            fig.savefig(
                filepath,
                dpi=CHART_DPI,
                bbox_inches='tight',
                facecolor=COLORS['canvas'],
                pad_inches=0.15,
            )

            logger.info(
                f"[IELTS Flow Chart (Professional)] Saved: "
                f"{os.path.join(self.output_dir, filename)}"
            )
            return f"/static/charts/{filename}"
        finally:
            with _MPL_LOCK:
                plt.close(fig)


# ==================== FACTORY FUNCTION ====================

def create_chart_renderer(output_dir="static/charts", use_cache=True):
    """Factory function to create ChartRenderer instance."""
    return ChartRenderer(output_dir, use_cache)


# ==================== PROCESS SHUTDOWN HOOK ====================
_LIVE_RENDERERS = []


def _register_renderer(renderer: ChartRenderer):
    _LIVE_RENDERERS.append(renderer)


def _shutdown_all_renderers():
    for r in list(_LIVE_RENDERERS):
        try:
            r.close()
        except Exception:
            pass
    _LIVE_RENDERERS.clear()


atexit.register(_shutdown_all_renderers)