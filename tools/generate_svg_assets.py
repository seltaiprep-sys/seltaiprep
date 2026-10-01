"""Generate hand-drawn-style SVG diagrams for all manifest entries.

Run once to populate static/diagram_assets/ with ready-to-use SVGs,
and to sync `num_labels` into manifest.json so downstream code (the
topic-first picker in diagram_asset_loader) knows how many labels
each SVG expects.

Usage:
    python tools/generate_svg_assets.py

Requires:
    - manifest.json already placed at static/diagram_assets/manifest.json
    - Python 3.8+
    - No external dependencies

FIXES APPLIED (v3 — TOPIC-FIRST SUPPORT):
  (1) `main()` now writes back `num_labels` into every manifest entry
        so `pick_random_diagram_topic()` can return the exact step count
        without inspecting each SVG.
  (2) `main()` also writes `generated_at` + `generated_by` metadata to
        the manifest so it's obvious the file was auto-synced.
  (3) Exposed `DIAGRAMS` at module level — downstream code can import
        it directly if it wants the icon-list view rather than just the
        manifest.

FIXES APPLIED (v2):
  (4) `_icon_fire` — replaced vague teardrop with a clear two-layer flame.
  (5) `_icon_gear` — replaced sun-like circle-with-rays with a proper cog.
  (6) `_icon_box` — replaced picture-frame look with a clear cardboard box.
  (7) `_icon_bolt` — tightened the lightning zigzag.
  (8) `_icon_mine` — added a pit shadow so it reads as a quarry/mine.
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
ASSETS_DIR = "static/diagram_assets"
MANIFEST_PATH = os.path.join(ASSETS_DIR, "manifest.json")

CANVAS_W = 900
ROW_H = 96 # vertical space per stage row
TOP_MARGIN = 100 # space for title
BOTTOM_MARGIN = 40

# Hand-drawn feel — SVG filter parameters.
# NOTE: svglib (pure-Python renderer) does NOT support feTurbulence,
# so keep SKETCH_SCALE = 0.0 if you plan to use svglib. Set it to 1.8
# only if you have a filter-capable renderer (cairosvg, playwright).
SKETCH_SEED = 3
SKETCH_FREQ = 0.028
SKETCH_SCALE = 0.0

# Fonts (Google Fonts style — falls back gracefully)
TITLE_FONT = "'Caveat', 'Patrick Hand', 'Comic Sans MS', cursive"
LABEL_FONT = "'Caveat', 'Patrick Hand', 'Comic Sans MS', cursive"

# Colors
CLR_INK = "#1a1a1a"
CLR_PAPER = "#ffffff"
CLR_ICON_FILL = "#f0e8d0" # warm cream
CLR_BOX_FILL = "#f5efe0" # light parchment
CLR_NUMBER_BG = "#ffffff"


# ─────────────────────────────────────────────────────────────
# DIAGRAM DEFINITIONS
# ─────────────────────────────────────────────────────────────
# Each entry: key → list of (label, icon_name) tuples.
# `num_labels` in the manifest is derived as len(steps) at write time.
DIAGRAMS: Dict[str, List[Tuple[str, str]]] = {
    "chocolate_production": [
        ("Harvesting", "seed"),
        ("Fermentation", "tank"),
        ("Drying", "sun"),
        ("Roasting", "fire"),
        ("Grinding", "gear"),
        ("Moulding", "box"),
    ],
    "photosynthesis": [
        ("Sunlight absorbed", "sun"),
        ("CO2 + water intake", "drop"),
        ("Oxygen released", "leaf"),
        ("Glucose produced", "leaf"),
        ("Stored as energy", "bolt"),
    ],
    "coffee_processing": [
        ("Picking", "seed"),
        ("Washing", "water"),
        ("Drying", "sun"),
        ("Roasting", "fire"),
        ("Grinding", "gear"),
        ("Packaging", "box"),
    ],
    "glass_recycling": [
        ("Collection", "bin"),
        ("Sorting by colour", "filter"),
        ("Washing & crushing", "water"),
        ("Melting", "fire"),
        ("Moulding", "box"),
        ("New bottles", "bottle"),
    ],
    "tea_production": [
        ("Plucking", "leaf"),
        ("Withering", "wind"),
        ("Rolling", "gear"),
        ("Oxidation", "sun"),
        ("Drying", "fire"),
        ("Packing", "box"),
    ],
    "cement_manufacturing": [
        ("Extracting limestone", "mine"),
        ("Crushing", "hammer"),
        ("Mixing with clay", "tank"),
        ("Heating in kiln", "kiln"),
        ("Cooling clinker", "wind"),
        ("Grinding to powder", "gear"),
    ],
    "wastewater_treatment": [
        ("Screening", "filter"),
        ("Primary settling", "tank"),
        ("Aeration", "wind"),
        ("Secondary settling", "tank"),
        ("Disinfection", "drop"),
        ("Sludge treatment", "gear"),
        ("Release to river", "water"),
    ],
    "bee_life_cycle": [
        ("Egg", "egg"),
        ("Larva", "seed"),
        ("Pupa", "box"),
        ("Adult bee", "bee"),
    ],
    "honey_making": [
        ("Collecting nectar", "bee"),
        ("Storing in hive", "box"),
        ("Evaporating water", "wind"),
        ("Sealing with wax", "drop"),
        ("Harvesting honey", "jar"),
    ],
    "silk_production": [
        ("Rearing silkworms", "seed"),
        ("Feeding mulberry leaves", "leaf"),
        ("Spinning cocoons", "egg"),
        ("Boiling cocoons", "fire"),
        ("Unwinding thread", "gear"),
        ("Weaving fabric", "box"),
    ],
    "bread_making": [
        ("Mixing ingredients", "tank"),
        ("Kneading dough", "hammer"),
        ("First rising", "wind"),
        ("Shaping loaves", "box"),
        ("Baking", "oven"),
        ("Cooling & packing", "box"),
    ],
    "cheese_making": [
        ("Collecting milk", "cow"),
        ("Pasteurising", "fire"),
        ("Adding rennet", "drop"),
        ("Curd cutting", "hammer"),
        ("Pressing", "box"),
        ("Ageing", "tank"),
    ],
    "beer_brewing": [
        ("Malting barley", "wheat"),
        ("Mashing", "tank"),
        ("Boiling with hops", "fire"),
        ("Fermenting", "tank"),
        ("Conditioning", "wind"),
        ("Bottling", "bottle"),
    ],
    "wool_processing": [
        ("Shearing sheep", "sheep"),
        ("Cleaning wool", "water"),
        ("Carding", "gear"),
        ("Spinning", "gear"),
        ("Weaving", "box"),
        ("Finishing cloth", "leaf"),
    ],
    "leather_tanning": [
        ("Soaking hides", "water"),
        ("Liming", "tank"),
        ("Fleshing", "hammer"),
        ("Tanning", "drop"),
        ("Dyeing", "drop"),
        ("Finishing", "box"),
    ],
    "brick_manufacturing": [
        ("Extracting clay", "mine"),
        ("Moulding", "box"),
        ("Drying", "sun"),
        ("Firing in kiln", "kiln"),
        ("Cooling & stacking", "box"),
    ],
    "steel_production": [
        ("Mining iron ore", "mine"),
        ("Blast furnace", "kiln"),
        ("Removing impurities", "filter"),
        ("Casting", "drop"),
        ("Rolling", "gear"),
        ("Finishing", "box"),
    ],
    "oil_refining": [
        ("Extracting crude oil", "drill"),
        ("Heating", "fire"),
        ("Fractional distillation", "tank"),
        ("Separating fuels", "filter"),
        ("Refining", "gear"),
        ("Storing", "tank"),
    ],
    "electricity_generation": [
        ("Burning coal", "fire"),
        ("Heating water", "tank"),
        ("Steam generation", "cloud"),
        ("Spinning turbine", "gear"),
        ("Generating power", "bolt"),
        ("Distribution", "bolt"),
    ],
    "solar_power": [
        ("Sunlight captured", "sun"),
        ("Panel conversion", "box"),
        ("DC electricity", "bolt"),
        ("Inverter to AC", "gear"),
        ("Home supply", "house"),
    ],
    "wind_turbine": [
        ("Wind blows", "wind"),
        ("Blades rotate", "wind"),
        ("Gearbox spins", "gear"),
        ("Generator converts", "bolt"),
        ("Power to grid", "bolt"),
    ],
    "hydroelectric": [
        ("Water stored", "water"),
        ("Released downhill", "drop"),
        ("Turbine spins", "gear"),
        ("Generator", "bolt"),
        ("Transformer", "box"),
        ("Power to grid", "bolt"),
    ],
    "natural_gas": [
        ("Drilling", "drill"),
        ("Extraction", "tank"),
        ("Processing", "filter"),
        ("Compression", "gear"),
        ("Pipeline transport", "truck"),
    ],
    "nuclear_power": [
        ("Mining uranium", "mine"),
        ("Enrichment", "gear"),
        ("Reactor fission", "atom"),
        ("Heat exchange", "fire"),
        ("Steam turbine", "gear"),
        ("Electricity", "bolt"),
    ],
    "rock_cycle": [
        ("Magma", "fire"),
        ("Igneous rock", "box"),
        ("Weathering", "rain"),
        ("Sediment", "drop"),
        ("Sedimentary rock", "box"),
        ("Metamorphic rock", "box"),
    ],
    "carbon_cycle": [
        ("CO2 in atmosphere", "cloud"),
        ("Photosynthesis", "leaf"),
        ("Plants & animals", "tree"),
        ("Decomposition", "leaf"),
        ("Fossilisation", "mine"),
        ("Combustion", "fire"),
    ],
    "nitrogen_cycle": [
        ("N2 in atmosphere", "cloud"),
        ("Nitrogen fixation", "bolt"),
        ("Nitrification", "drop"),
        ("Plant uptake", "leaf"),
        ("Decomposition", "leaf"),
        ("Denitrification", "cloud"),
    ],
    "recycling_process": [
        ("Household waste", "bin"),
        ("Collection truck", "truck"),
        ("Sorting facility", "filter"),
        ("Processing", "gear"),
        ("New products", "box"),
        ("Consumer purchase", "bag"),
    ],
    "plastic_recycling": [
        ("Collection", "bin"),
        ("Sorting by type", "filter"),
        ("Shredding", "hammer"),
        ("Washing", "water"),
        ("Melting into pellets", "fire"),
        ("New plastic products", "box"),
    ],
    "aluminium_recycling": [
        ("Collection", "bin"),
        ("Crushing", "hammer"),
        ("Melting", "fire"),
        ("Casting ingots", "drop"),
        ("Rolling into sheets", "gear"),
    ],
    "rice_production": [
        ("Ploughing paddy", "mine"),
        ("Planting seedlings", "seed"),
        ("Irrigating", "water"),
        ("Harvesting", "wheat"),
        ("Threshing", "gear"),
        ("Milling & packing", "box"),
    ],
    "cotton_production": [
        ("Planting", "seed"),
        ("Growing", "leaf"),
        ("Harvesting", "wheat"),
        ("Ginning", "gear"),
        ("Spinning", "gear"),
        ("Weaving fabric", "box"),
    ],
    "salt_production": [
        ("Collecting seawater", "water"),
        ("Evaporation ponds", "sun"),
        ("Crystallisation", "drop"),
        ("Harvesting salt", "bag"),
        ("Refining & packing", "box"),
    ],
    "sugar_production": [
        ("Harvesting cane", "wheat"),
        ("Washing", "water"),
        ("Crushing", "gear"),
        ("Juice extraction", "drop"),
        ("Boiling", "fire"),
        ("Crystallising & packing", "box"),
    ],
    "olive_oil_production": [
        ("Harvesting olives", "olive"),
        ("Washing", "water"),
        ("Crushing", "gear"),
        ("Pressing", "box"),
        ("Filtering & bottling", "bottle"),
    ],
    "wine_making": [
        ("Harvesting grapes", "grape"),
        ("Crushing", "gear"),
        ("Fermenting", "tank"),
        ("Ageing in barrels", "box"),
        ("Filtering", "filter"),
        ("Bottling", "bottle"),
    ],
    "yoghurt_production": [
        ("Milk collection", "cow"),
        ("Pasteurising", "fire"),
        ("Adding cultures", "drop"),
        ("Incubating", "tank"),
        ("Cooling & packing", "box"),
    ],
    "instant_noodles": [
        ("Mixing flour & water", "tank"),
        ("Rolling dough", "gear"),
        ("Cutting into strips", "hammer"),
        ("Steaming", "cloud"),
        ("Frying & drying", "fire"),
        ("Packing with seasoning", "box"),
    ],
    "fish_canning": [
        ("Catching", "boat"),
        ("Cleaning", "drop"),
        ("Cutting", "hammer"),
        ("Cooking", "fire"),
        ("Filling cans", "can"),
        ("Sealing & labelling", "box"),
    ],
    "milk_processing": [
        ("Milking cows", "cow"),
        ("Filtering", "filter"),
        ("Pasteurising", "fire"),
        ("Homogenising", "gear"),
        ("Cooling", "wind"),
        ("Bottling & delivery", "truck"),
    ],
    "lemonade_production": [
        ("Squeezing lemons", "drop"),
        ("Mixing with sugar", "tank"),
        ("Adding water", "water"),
        ("Carbonating", "cloud"),
        ("Bottling", "bottle"),
    ],
    "toothpaste_production": [
        ("Mixing ingredients", "tank"),
        ("Adding flavour", "drop"),
        ("Grinding to paste", "gear"),
        ("Filling tubes", "box"),
        ("Sealing & packing", "box"),
    ],
    "soap_production": [
        ("Mixing oils & lye", "tank"),
        ("Heating", "fire"),
        ("Saponification", "drop"),
        ("Adding fragrance", "leaf"),
        ("Moulding & cutting", "box"),
    ],
}


# ─────────────────────────────────────────────────────────────
# ICON PRIMITIVES (all return SVG snippets centred on cx, cy)
# ─────────────────────────────────────────────────────────────
ICON_STROKE = f'stroke="{CLR_INK}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"'
ICON_FILL = f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.6"'


def _icon_sun(cx, cy):
    r = 14
    rays = []
    import math
    for a in range(0, 360, 45):
        rad = math.radians(a)
        x1 = cx + (r + 3) * math.cos(rad)
        y1 = cy + (r + 3) * math.sin(rad)
        x2 = cx + (r + 8) * math.cos(rad)
        y2 = cy + (r + 8) * math.sin(rad)
        rays.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {ICON_STROKE}/>')
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" {ICON_FILL}/>' + "".join(rays)


def _icon_cloud(cx, cy):
    return (
        f'<path d="M {cx-16} {cy+4} a 8 8 0 0 1 8 -8 a 10 10 0 0 1 18 -4 '
        f'a 9 9 0 0 1 14 6 a 7 7 0 0 1 -2 12 h -30 a 7 7 0 0 1 -8 -6 Z" '
        f'{ICON_FILL}/>'
    )


def _icon_rain(cx, cy):
    drops = []
    for dx in (-10, 0, 10):
        drops.append(f'<line x1="{cx+dx}" y1="{cy+4}" x2="{cx+dx-3}" y2="{cy+16}" {ICON_STROKE}/>')
    return _icon_cloud(cx, cy - 4) + "".join(drops)


def _icon_water(cx, cy):
    return (
        f'<path d="M {cx-18} {cy} q 6 -8 12 0 q 6 8 12 0 q 6 -8 12 0" {ICON_STROKE}/>'
        f'<path d="M {cx-18} {cy+8} q 6 -8 12 0 q 6 8 12 0 q 6 -8 12 0" {ICON_STROKE}/>'
    )


def _icon_tree(cx, cy):
    return (
        f'<line x1="{cx}" y1="{cy+12}" x2="{cx}" y2="{cy+22}" {ICON_STROKE}/>'
        f'<path d="M {cx-14} {cy+12} L {cx} {cy-12} L {cx+14} {cy+12} Z" {ICON_FILL}/>'
        f'<path d="M {cx-10} {cy+2} L {cx} {cy-18} L {cx+10} {cy+2} Z" {ICON_FILL}/>'
    )


def _icon_seed(cx, cy):
    return (
        f'<ellipse cx="{cx}" cy="{cy}" rx="8" ry="12" {ICON_FILL}/>'
        f'<path d="M {cx} {cy-10} q -3 5 0 10" {ICON_STROKE}/>'
    )


def _icon_plant(cx, cy):
    return (
        f'<line x1="{cx}" y1="{cy+14}" x2="{cx}" y2="{cy-8}" {ICON_STROKE}/>'
        f'<path d="M {cx} {cy-2} q -14 -4 -14 -12 q 12 0 14 10 Z" {ICON_FILL}/>'
        f'<path d="M {cx} {cy-8} q 14 -4 14 -12 q -12 0 -14 10 Z" {ICON_FILL}/>'
    )


def _icon_leaf(cx, cy):
    return (
        f'<path d="M {cx-14} {cy+8} q 8 -22 28 -22 q 0 20 -22 26 Z" {ICON_FILL}/>'
        f'<path d="M {cx-10} {cy+6} q 6 -12 20 -18" {ICON_STROKE}/>'
    )


def _icon_drop(cx, cy):
    return f'<path d="M {cx} {cy-14} q 12 12 12 20 a 12 12 0 1 1 -24 0 q 0 -8 12 -20 Z" {ICON_FILL}/>'


def _icon_factory(cx, cy):
    return (
        f'<rect x="{cx-20}" y="{cy-6}" width="40" height="20" {ICON_FILL}/>'
        f'<rect x="{cx-14}" y="{cy-18}" width="6" height="12" {ICON_FILL}/>'
        f'<rect x="{cx-4}" y="{cy-22}" width="6" height="16" {ICON_FILL}/>'
        f'<rect x="{cx+6}" y="{cy-16}" width="6" height="10" {ICON_FILL}/>'
    )


def _icon_house(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-4}" width="28" height="18" {ICON_FILL}/>'
        f'<path d="M {cx-18} {cy-4} L {cx} {cy-20} L {cx+18} {cy-4} Z" {ICON_FILL}/>'
    )


def _icon_truck(cx, cy):
    return (
        f'<rect x="{cx-20}" y="{cy-8}" width="24" height="14" {ICON_FILL}/>'
        f'<rect x="{cx+4}" y="{cy-4}" width="14" height="10" {ICON_FILL}/>'
        f'<circle cx="{cx-12}" cy="{cy+10}" r="4" {ICON_FILL}/>'
        f'<circle cx="{cx+10}" cy="{cy+10}" r="4" {ICON_FILL}/>'
    )


def _icon_car(cx, cy):
    return (
        f'<path d="M {cx-18} {cy+4} l 4 -10 l 28 0 l 4 10 Z" {ICON_FILL}/>'
        f'<circle cx="{cx-10}" cy="{cy+8}" r="4" {ICON_FILL}/>'
        f'<circle cx="{cx+10}" cy="{cy+8}" r="4" {ICON_FILL}/>'
    )


def _icon_ship(cx, cy):
    return (
        f'<path d="M {cx-20} {cy+4} l 40 0 l -6 10 l -28 0 Z" {ICON_FILL}/>'
        f'<line x1="{cx}" y1="{cy+4}" x2="{cx}" y2="{cy-14}" {ICON_STROKE}/>'
        f'<path d="M {cx} {cy-14} l 12 10 l -12 4 Z" {ICON_FILL}/>'
    )


def _icon_boat(cx, cy):
    return (
        f'<path d="M {cx-16} {cy+4} q 16 8 32 0 l -4 8 l -24 0 Z" {ICON_FILL}/>'
        f'<line x1="{cx}" y1="{cy+4}" x2="{cx}" y2="{cy-12}" {ICON_STROKE}/>'
        f'<path d="M {cx} {cy-12} l 10 8 l -10 4 Z" {ICON_FILL}/>'
    )


def _icon_cow(cx, cy):
    return (
        f'<ellipse cx="{cx}" cy="{cy}" rx="16" ry="11" {ICON_FILL}/>'
        f'<circle cx="{cx-14}" cy="{cy-10}" r="6" {ICON_FILL}/>'
        f'<line x1="{cx-8}" y1="{cy+10}" x2="{cx-8}" y2="{cy+18}" {ICON_STROKE}/>'
        f'<line x1="{cx+8}" y1="{cy+10}" x2="{cx+8}" y2="{cy+18}" {ICON_STROKE}/>'
    )


def _icon_sheep(cx, cy):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="12" {ICON_FILL}/>'
        f'<circle cx="{cx-10}" cy="{cy+2}" r="6" {ICON_FILL}/>'
        f'<circle cx="{cx-4}" cy="{cy-8}" r="6" {ICON_FILL}/>'
        f'<circle cx="{cx+6}" cy="{cy-6}" r="6" {ICON_FILL}/>'
    )


def _icon_bee(cx, cy):
    return (
        f'<ellipse cx="{cx}" cy="{cy}" rx="12" ry="8" {ICON_FILL}/>'
        f'<line x1="{cx-6}" y1="{cy-8}" x2="{cx-6}" y2="{cy+8}" {ICON_STROKE}/>'
        f'<line x1="{cx+2}" y1="{cy-8}" x2="{cx+2}" y2="{cy+8}" {ICON_STROKE}/>'
        f'<ellipse cx="{cx-4}" cy="{cy-8}" rx="6" ry="4" fill="#d8f0ff" stroke="{CLR_INK}" stroke-width="0.8"/>'
    )


def _icon_fish(cx, cy):
    return (
        f'<path d="M {cx+16} {cy} q -18 -12 -32 0 q 14 12 32 0 Z" {ICON_FILL}/>'
        f'<circle cx="{cx+8}" cy="{cy-2}" r="1.6" fill="{CLR_INK}"/>'
        f'<path d="M {cx+16} {cy} l 8 -6 l 0 12 Z" {ICON_FILL}/>'
    )


def _icon_egg(cx, cy):
    return f'<ellipse cx="{cx}" cy="{cy}" rx="9" ry="13" {ICON_FILL}/>'


def _icon_wheat(cx, cy):
    lines = []
    for i in range(4):
        y = cy - 10 + i * 6
        lines.append(f'<path d="M {cx} {y} q -6 2 -8 6" {ICON_STROKE}/>')
        lines.append(f'<path d="M {cx} {y} q 6 2 8 6" {ICON_STROKE}/>')
    return f'<line x1="{cx}" y1="{cy-14}" x2="{cx}" y2="{cy+14}" {ICON_STROKE}/>' + "".join(lines)


def _icon_coffee(cx, cy):
    return (
        f'<path d="M {cx-12} {cy-8} h 20 v 12 a 6 6 0 0 1 -6 6 h -8 a 6 6 0 0 1 -6 -6 Z" {ICON_FILL}/>'
        f'<path d="M {cx+10} {cy-2} a 5 5 0 0 1 0 8" {ICON_STROKE}/>'
    )


def _icon_tea(cx, cy):
    return (
        f'<path d="M {cx-12} {cy-2} h 20 v 6 a 8 8 0 0 1 -8 8 h -4 a 8 8 0 0 1 -8 -8 Z" {ICON_FILL}/>'
        f'<path d="M {cx+10} {cy+2} a 5 5 0 0 1 0 8" {ICON_STROKE}/>'
        f'<path d="M {cx-6} {cy-8} q 2 -6 4 0" {ICON_STROKE}/>'
    )


def _icon_grape(cx, cy):
    circles = []
    for row, count in [(0, 3), (8, 2), (16, 1)]:
        for i in range(count):
            x = cx - (count - 1) * 5 + i * 10
            circles.append(f'<circle cx="{x}" cy="{cy-6+row}" r="4.5" {ICON_FILL}/>')
    return "".join(circles) + f'<line x1="{cx}" y1="{cy-14}" x2="{cx}" y2="{cy-20}" {ICON_STROKE}/>'


def _icon_olive(cx, cy):
    return (
        f'<ellipse cx="{cx-6}" cy="{cy}" rx="6" ry="9" fill="#a8c66c" stroke="{CLR_INK}" stroke-width="1.4"/>'
        f'<ellipse cx="{cx+6}" cy="{cy+2}" rx="6" ry="9" fill="#a8c66c" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )


def _icon_jar(cx, cy):
    return (
        f'<rect x="{cx-10}" y="{cy-8}" width="20" height="22" rx="3" {ICON_FILL}/>'
        f'<rect x="{cx-12}" y="{cy-12}" width="24" height="6" rx="2" {ICON_FILL}/>'
    )


def _icon_bottle(cx, cy):
    return (
        f'<rect x="{cx-6}" y="{cy-6}" width="12" height="22" rx="2" {ICON_FILL}/>'
        f'<rect x="{cx-3}" y="{cy-14}" width="6" height="10" {ICON_FILL}/>'
        f'<rect x="{cx-4}" y="{cy-16}" width="8" height="3" {ICON_FILL}/>'
    )


def _icon_can(cx, cy):
    return (
        f'<rect x="{cx-9}" y="{cy-12}" width="18" height="26" rx="2" {ICON_FILL}/>'
        f'<line x1="{cx-9}" y1="{cy-6}" x2="{cx+9}" y2="{cy-6}" {ICON_STROKE}/>'
        f'<line x1="{cx-9}" y1="{cy+6}" x2="{cx+9}" y2="{cy+6}" {ICON_STROKE}/>'
    )


def _icon_box(cx, cy):
    """Cardboard box — lid overhang, centre tape, small fold flaps."""
    lid = (
        f'<rect x="{cx-16}" y="{cy-14}" width="32" height="7" '
        f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )
    body = (
        f'<rect x="{cx-14}" y="{cy-8}" width="28" height="18" '
        f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )
    tape = (
        f'<line x1="{cx}" y1="{cy-14}" x2="{cx}" y2="{cy+10}" '
        f'stroke="{CLR_INK}" stroke-width="1.0"/>'
    )
    fold_l = (
        f'<path d="M {cx-14} {cy-7} L {cx-14} {cy-2} L {cx-9} {cy-7} Z" '
        f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.0"/>'
    )
    fold_r = (
        f'<path d="M {cx+14} {cy-7} L {cx+14} {cy-2} L {cx+9} {cy-7} Z" '
        f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.0"/>'
    )
    return lid + body + tape + fold_l + fold_r


def _icon_paper(cx, cy):
    return (
        f'<rect x="{cx-12}" y="{cy-14}" width="24" height="30" {ICON_FILL}/>'
        f'<line x1="{cx-7}" y1="{cy-6}" x2="{cx+7}" y2="{cy-6}" {ICON_STROKE}/>'
        f'<line x1="{cx-7}" y1="{cy}" x2="{cx+7}" y2="{cy}" {ICON_STROKE}/>'
        f'<line x1="{cx-7}" y1="{cy+6}" x2="{cx+3}" y2="{cy+6}" {ICON_STROKE}/>'
    )


def _icon_fire(cx, cy):
    """Two-layer flame: wide base, pointed tip, orange core."""
    outer = (
        f'<path d="M {cx} {cy-16} '
        f'C {cx-6} {cy-8} {cx-14} {cy-2} {cx-14} {cy+4} '
        f'C {cx-14} {cy+12} {cx-6} {cy+16} {cx} {cy+16} '
        f'C {cx+6} {cy+16} {cx+14} {cy+12} {cx+14} {cy+4} '
        f'C {cx+14} {cy-2} {cx+6} {cy-8} {cx} {cy-16} Z" '
        f'fill="#f5c97b" stroke="{CLR_INK}" stroke-width="1.8"/>'
    )
    inner = (
        f'<path d="M {cx} {cy-4} '
        f'C {cx-3} {cy+1} {cx-6} {cy+4} {cx-6} {cy+8} '
        f'C {cx-6} {cy+12} {cx-3} {cy+14} {cx} {cy+14} '
        f'C {cx+3} {cy+14} {cx+6} {cy+12} {cx+6} {cy+8} '
        f'C {cx+6} {cy+4} {cx+3} {cy+1} {cx} {cy-4} Z" '
        f'fill="#e08a2e" stroke="none"/>'
    )
    return outer + inner


def _icon_kiln(cx, cy):
    return (
        f'<rect x="{cx-16}" y="{cy-10}" width="32" height="22" rx="3" {ICON_FILL}/>'
        f'<path d="M {cx-8} {cy+4} q 8 -10 16 0" {ICON_STROKE}/>'
        f'<rect x="{cx-4}" y="{cy-16}" width="8" height="6" {ICON_FILL}/>'
    )


def _icon_hammer(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-8}" width="20" height="10" rx="2" {ICON_FILL}/>'
        f'<line x1="{cx+4}" y1="{cy-3}" x2="{cx+16}" y2="{cy+12}" {ICON_STROKE}/>'
    )


def _icon_drill(cx, cy):
    return (
        f'<rect x="{cx-10}" y="{cy-14}" width="20" height="14" rx="2" {ICON_FILL}/>'
        f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy+16}" {ICON_STROKE}/>'
        f'<polygon points="{cx-4},{cy+14} {cx+4},{cy+14} {cx},{cy+20}" {ICON_FILL}/>'
    )


def _icon_gear(cx, cy):
    """Proper cog: thick rectangular teeth around a hollow ring, dark hub."""
    import math
    outer_r = 14
    inner_r = 10
    tooth_len = 5
    teeth = []
    for i in range(8):
        a = i * 45
        rad = math.radians(a)
        tx = cx + (outer_r - 1) * math.cos(rad)
        ty = cy + (outer_r - 1) * math.sin(rad)
        perp = rad + math.pi / 2
        half = 3
        x1 = tx - half * math.cos(perp)
        y1 = ty - half * math.sin(perp)
        x2 = tx + half * math.cos(perp)
        y2 = ty + half * math.sin(perp)
        x1b = x1 + tooth_len * math.cos(rad) * 0.15
        y1b = y1 + tooth_len * math.sin(rad) * 0.15
        x2b = x2 + tooth_len * math.cos(rad) * 0.15
        y2b = y2 + tooth_len * math.sin(rad) * 0.15
        teeth.append(
            f'<line x1="{x1b:.1f}" y1="{y1b:.1f}" x2="{x2b:.1f}" y2="{y2b:.1f}" '
            f'stroke="{CLR_INK}" stroke-width="4.5" stroke-linecap="butt"/>'
        )
    ring = (
        f'<circle cx="{cx}" cy="{cy}" r="{inner_r}" '
        f'fill="{CLR_ICON_FILL}" stroke="{CLR_INK}" stroke-width="1.8"/>'
    )
    hub = f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="{CLR_INK}"/>'
    return "".join(teeth) + ring + hub


def _icon_bolt(cx, cy):
    """Lightning bolt — tightened zigzag."""
    return (
        f'<polygon points="'
        f'{cx+2},{cy-16} '
        f'{cx-8},{cy-1} '
        f'{cx-1},{cy-1} '
        f'{cx-4},{cy+16} '
        f'{cx+8},{cy-2} '
        f'{cx+1},{cy-2}" '
        f'{ICON_FILL}/>'
    )


def _icon_mine(cx, cy):
    """Mountain silhouette with a pit shadow inside."""
    mountain = (
        f'<path d="M {cx-20} {cy+10} L {cx-4} {cy-10} L {cx+4} {cy+10} Z" {ICON_FILL}/>'
        f'<path d="M {cx-4} {cy-10} L {cx+10} {cy+4} L {cx+20} {cy+10} L {cx-2} {cy+10} Z" {ICON_FILL}/>'
    )
    pit = (
        f'<path d="M {cx-8} {cy+10} Q {cx} {cy+4} {cx+8} {cy+10}" '
        f'fill="{CLR_PAPER}" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )
    return mountain + pit


def _icon_quarry(cx, cy):
    return (
        f'<rect x="{cx-16}" y="{cy-2}" width="32" height="12" {ICON_FILL}/>'
        f'<path d="M {cx-16} {cy-2} l 6 -6 l 6 4 l 6 -4 l 6 4 l 6 -6 l 0 8 Z" {ICON_FILL}/>'
    )


def _icon_wave(cx, cy):
    return (
        f'<path d="M {cx-18} {cy} q 4 -8 8 0 q 4 8 8 0 q 4 -8 8 0 q 4 8 8 0" {ICON_STROKE}/>'
    )


def _icon_wind(cx, cy):
    return (
        f'<path d="M {cx-16} {cy-6} h 22 q 4 0 4 4" {ICON_STROKE}/>'
        f'<path d="M {cx-16} {cy} h 26" {ICON_STROKE}/>'
        f'<path d="M {cx-16} {cy+6} h 22 q 4 0 4 -4" {ICON_STROKE}/>'
    )


def _icon_atom(cx, cy):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="3" fill="{CLR_INK}"/>'
        f'<ellipse cx="{cx}" cy="{cy}" rx="14" ry="6" {ICON_STROKE}/>'
        f'<ellipse cx="{cx}" cy="{cy}" rx="14" ry="6" transform="rotate(60 {cx} {cy})" {ICON_STROKE}/>'
        f'<ellipse cx="{cx}" cy="{cy}" rx="14" ry="6" transform="rotate(-60 {cx} {cy})" {ICON_STROKE}/>'
    )


def _icon_reactor(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-14}" width="28" height="28" rx="4" {ICON_FILL}/>'
        f'<circle cx="{cx}" cy="{cy}" r="7" {ICON_STROKE}/>'
        f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{CLR_INK}"/>'
    )


def _icon_recycle(cx, cy):
    return (
        f'<path d="M {cx-2} {cy-14} l 6 8 l -4 0 l 0 10 l -8 0 l 0 -10 l -4 0 Z" {ICON_FILL}/>'
        f'<circle cx="{cx+8}" cy="{cy+6}" r="3" fill="none" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )


def _icon_bin(cx, cy):
    return (
        f'<path d="M {cx-12} {cy-8} h 24 l -2 20 h -20 Z" {ICON_FILL}/>'
        f'<line x1="{cx-14}" y1="{cy-8}" x2="{cx+14}" y2="{cy-8}" {ICON_STROKE}/>'
        f'<line x1="{cx-8}" y1="{cy-14}" x2="{cx+8}" y2="{cy-14}" {ICON_STROKE}/>'
    )


def _icon_filter(cx, cy):
    return (
        f'<polygon points="{cx-14},{cy-10} {cx+14},{cy-10} {cx+4},{cy+2} {cx+4},{cy+12} {cx-4},{cy+12} {cx-4},{cy+2}" {ICON_FILL}/>'
    )


def _icon_pump(cx, cy):
    return (
        f'<rect x="{cx-10}" y="{cy-8}" width="20" height="16" rx="3" {ICON_FILL}/>'
        f'<line x1="{cx}" y1="{cy-8}" x2="{cx}" y2="{cy-16}" {ICON_STROKE}/>'
        f'<rect x="{cx-3}" y="{cy-16}" width="6" height="4" {ICON_FILL}/>'
    )


def _icon_tank(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-10}" width="28" height="22" rx="4" {ICON_FILL}/>'
        f'<line x1="{cx-14}" y1="{cy-2}" x2="{cx+14}" y2="{cy-2}" {ICON_STROKE}/>'
        f'<ellipse cx="{cx}" cy="{cy-10}" rx="14" ry="3" {ICON_FILL}/>'
    )


def _icon_oven(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-10}" width="28" height="22" rx="3" {ICON_FILL}/>'
        f'<rect x="{cx-10}" y="{cy-6}" width="20" height="14" rx="2" fill="#ffe0b0" stroke="{CLR_INK}" stroke-width="1.4"/>'
    )


def _icon_tap(cx, cy):
    return (
        f'<rect x="{cx-4}" y="{cy-14}" width="8" height="20" {ICON_FILL}/>'
        f'<path d="M {cx-4} {cy+6} h 14 v 4" {ICON_STROKE}/>'
        f'<path d="M {cx+10} {cy+10} q 0 4 -2 6" {ICON_STROKE}/>'
    )


def _icon_bag(cx, cy):
    return (
        f'<path d="M {cx-12} {cy-4} l 4 -10 h 16 l 4 10 v 14 h -24 Z" {ICON_FILL}/>'
        f'<path d="M {cx-4} {cy-14} q 4 -8 8 0" {ICON_STROKE}/>'
    )


def _icon_dollar(cx, cy):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="12" {ICON_FILL}/>'
        f'<text x="{cx}" y="{cy+5}" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="{CLR_INK}">$</text>'
    )


def _icon_chart(cx, cy):
    return (
        f'<rect x="{cx-14}" y="{cy-6}" width="6" height="14" {ICON_FILL}/>'
        f'<rect x="{cx-4}" y="{cy-12}" width="6" height="20" {ICON_FILL}/>'
        f'<rect x="{cx+6}" y="{cy-2}" width="6" height="10" {ICON_FILL}/>'
    )


def _icon_star(cx, cy):
    import math
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        r = 14 if i % 2 == 0 else 6
        pts.append(f"{cx + r*math.cos(a):.1f},{cy + r*math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" {ICON_FILL}/>'


def _icon_check(cx, cy):
    return f'<path d="M {cx-10} {cy} l 7 7 l 14 -16" {ICON_STROKE} stroke-width="3"/>'


def _icon_arrow(cx, cy):
    return (
        f'<line x1="{cx-12}" y1="{cy}" x2="{cx+8}" y2="{cy}" {ICON_STROKE}/>'
        f'<polygon points="{cx+6},{cy-6} {cx+16},{cy} {cx+6},{cy+6}" fill="{CLR_INK}"/>'
    )


def _icon_blank(cx, cy):
    return f'<circle cx="{cx}" cy="{cy}" r="10" {ICON_STROKE}/>'


ICON_MAP = {
    "sun": _icon_sun, "cloud": _icon_cloud, "rain": _icon_rain,
    "water": _icon_water, "tree": _icon_tree, "seed": _icon_seed,
    "plant": _icon_plant, "leaf": _icon_leaf, "drop": _icon_drop,
    "factory": _icon_factory, "house": _icon_house, "truck": _icon_truck,
    "car": _icon_car, "ship": _icon_ship, "boat": _icon_boat,
    "cow": _icon_cow, "sheep": _icon_sheep, "bee": _icon_bee,
    "fish": _icon_fish, "egg": _icon_egg, "wheat": _icon_wheat,
    "coffee": _icon_coffee, "tea": _icon_tea, "grape": _icon_grape,
    "olive": _icon_olive, "jar": _icon_jar, "bottle": _icon_bottle,
    "can": _icon_can, "box": _icon_box, "paper": _icon_paper,
    "fire": _icon_fire, "kiln": _icon_kiln, "hammer": _icon_hammer,
    "drill": _icon_drill, "gear": _icon_gear, "bolt": _icon_bolt,
    "mine": _icon_mine, "quarry": _icon_quarry, "wave": _icon_wave,
    "wind": _icon_wind, "atom": _icon_atom, "reactor": _icon_reactor,
    "recycle": _icon_recycle, "bin": _icon_bin, "filter": _icon_filter,
    "pump": _icon_pump, "tank": _icon_tank, "oven": _icon_oven,
    "tap": _icon_tap, "bag": _icon_bag, "dollar": _icon_dollar,
    "chart": _icon_chart, "star": _icon_star, "check": _icon_check,
    "arrow": _icon_arrow, "blank": _icon_blank,
}


# ─────────────────────────────────────────────────────────────
# SVG BUILDER
# ─────────────────────────────────────────────────────────────
def _svg_header(canvas_h: int) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} {canvas_h}" width="{CANVAS_W}" height="{canvas_h}">
  <defs>
    <filter id="sketch" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="fractalNoise" baseFrequency="{SKETCH_FREQ}" numOctaves="2" seed="{SKETCH_SEED}"/>
      <feDisplacementMap in="SourceGraphic" scale="{SKETCH_SCALE}"/>
    </filter>
    <style>
      .title {{ font-family: {TITLE_FONT}; font-size: 28px; font-weight: 700; fill: {CLR_INK}; }}
      .label {{ font-family: {LABEL_FONT}; font-size: 20px; font-weight: 600; fill: {CLR_INK}; }}
      .numb {{ font-family: {LABEL_FONT}; font-size: 22px; font-weight: 700; fill: {CLR_INK}; }}
      .ink {{ stroke: {CLR_INK}; stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
      .box {{ fill: {CLR_BOX_FILL}; stroke: {CLR_INK}; stroke-width: 1.8; }}
      .arrow {{ stroke: {CLR_INK}; stroke-width: 2; fill: none; marker-end: url(#arrowhead); }}
    </style>
    <marker id="arrowhead" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">
      <polygon points="0 0, 10 4, 0 8" fill="{CLR_INK}"/>
    </marker>
  </defs>
  <rect width="{CANVAS_W}" height="{canvas_h}" fill="{CLR_PAPER}"/>
'''


def _svg_footer() -> str:
    return "</svg>"


def build_svg(steps: List[Tuple[str, str]]) -> str:
    """Build one vertical-flow SVG for the given step list."""
    n = len(steps)
    canvas_h = TOP_MARGIN + n * ROW_H + BOTTOM_MARGIN

    parts = [_svg_header(canvas_h)]

    # Title placeholder
    parts.append(
        f'<text x="{CANVAS_W // 2}" y="44" text-anchor="middle" class="title">'
        f'{{{{title}}}}</text>'
    )

    col_x = CANVAS_W // 2
    first_y = TOP_MARGIN + ROW_H // 2

    for i, (label_text, icon_name) in enumerate(steps):
        y = first_y + i * ROW_H

        # Number badge
        badge_x = col_x - 260
        parts.append(
            f'<circle cx="{badge_x}" cy="{y}" r="18" '
            f'fill="{CLR_NUMBER_BG}" stroke="{CLR_INK}" stroke-width="1.8"/>'
        )
        parts.append(
            f'<text x="{badge_x}" y="{y + 7}" text-anchor="middle" class="numb">{i + 1}</text>'
        )

        # Icon box
        icon_x = col_x - 140
        parts.append(
            f'<rect x="{icon_x - 26}" y="{y - 26}" width="52" height="52" rx="8" '
            f'fill="{CLR_PAPER}" stroke="{CLR_INK}" stroke-width="1.6"/>'
        )
        icon_fn = ICON_MAP.get(icon_name, _icon_blank)
        parts.append(f'{icon_fn(icon_x, y)}')

        # Label box
        box_x = col_x - 60
        box_w = 340
        box_h = 48
        parts.append(
            f'<rect x="{box_x}" y="{y - box_h // 2}" width="{box_w}" height="{box_h}" '
            f'rx="10" class="box"/>'
        )
        parts.append(
            f'<text x="{box_x + box_w // 2}" y="{y + 7}" text-anchor="middle" class="label">'
            f'{{{{label_{i + 1}}}}}</text>'
        )

        # Arrow to next
        if i < n - 1:
            next_y = first_y + (i + 1) * ROW_H
            arrow_x = col_x - 140
            parts.append(
                f'<path d="M {arrow_x} {y + 30} L {arrow_x} {next_y - 32}" '
                f'class="arrow"/>'
            )

    parts.append(_svg_footer())
    return "".join(parts)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    if not os.path.exists(MANIFEST_PATH):
        print(f" Manifest not found: {MANIFEST_PATH}")
        print(" Create manifest.json first.")
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    generated = 0
    skipped = 0
    num_labels_synced = 0

    # ── Generate SVGs + sync num_labels back into the manifest ──
    for key, steps in DIAGRAMS.items():
        entry = manifest.get(key)
        if not entry:
            print(f" Skipping '{key}' — not in manifest")
            skipped += 1
            continue

        out_path = os.path.join(ASSETS_DIR, entry["file"])
        try:
            # Write the SVG
            svg = build_svg(steps)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(svg)

            # Sync num_labels into the manifest entry
            entry["num_labels"] = len(steps)
            num_labels_synced += 1

            print(f" {entry['file']:35} ({len(steps)} steps)")
            generated += 1
        except Exception as e:
            print(f" {key}: {e}")
            skipped += 1

    # ── Stamp manifest with generation metadata ──
    manifest["_generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["_generated_by"] = "tools/generate_svg_assets.py"

    # ── Write the updated manifest back to disk ──
    try:
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print()
        print(f" Manifest updated with num_labels for {num_labels_synced} entries")
    except Exception as e:
        print(f" Could not write manifest: {e}")

    print()
    print(f"Generated: {generated}")
    print(f"Skipped: {skipped}")
    print(f"Output: {ASSETS_DIR}/")


if __name__ == "__main__":
    main()