from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from .. import safety
from ..config import ROOT, get
from .registry import register

SW = 13.333
SH = 7.5

THEMES: dict[str, dict[str, str]] = {
    "midnight": {
        "bg": "0B1220", "surface": "13213A", "alt": "1B2C4B",
        "text": "F8FAFC", "muted": "A9BCD8", "accent": "22D3EE",
        "accent2": "F97316", "good": "34D399", "bad": "FB7185",
    },
    "aurora": {
        "bg": "071A1A", "surface": "0E2D2B", "alt": "15413D",
        "text": "F0FDFA", "muted": "A7D8D2", "accent": "2DD4BF",
        "accent2": "C084FC", "good": "86EFAC", "bad": "FDA4AF",
    },
    "corporate": {
        "bg": "F8FAFC", "surface": "FFFFFF", "alt": "E8EEF6",
        "text": "0F172A", "muted": "475569", "accent": "2563EB",
        "accent2": "0F766E", "good": "15803D", "bad": "BE123C",
    },
    "creative": {
        "bg": "FFF8F3", "surface": "FFFFFF", "alt": "FDE8D7",
        "text": "1C1917", "muted": "6B5B52", "accent": "EA580C",
        "accent2": "7C3AED", "good": "16A34A", "bad": "E11D48",
    },
    "pitch": {
        "bg": "0A0A0A", "surface": "171717", "alt": "262626",
        "text": "FAFAFA", "muted": "B8B8B8", "accent": "FACC15",
        "accent2": "60A5FA", "good": "4ADE80", "bad": "F87171",
    },
    "premium": {
        "bg": "0C0A09", "surface": "1C1917", "alt": "292524",
        "text": "FFFBEB", "muted": "D6C7A1", "accent": "D4AF37",
        "accent2": "A78BFA", "good": "86EFAC", "bad": "FB7185",
    },
}

THEME_ALIASES = {
    "high": "premium", "lux": "premium", "luxury": "premium", "gold": "premium",
    "executive": "corporate", "business": "corporate", "light": "corporate",
    "dark": "midnight", "modern": "midnight", "startup": "pitch",
}

SLIDE_TYPES = (
    "title", "agenda", "section", "content", "cards", "stats", "timeline",
    "chart", "quote", "comparison", "table", "image", "closing",
)


def _rgb(value: str) -> RGBColor:
    text = str(value).lstrip("#")
    return RGBColor.from_string(text[:6].upper())


def _value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _items(value: Any, limit: int = 8) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    value = parsed
                else:
                    value = [text]
            except json.JSONDecodeError:
                value = [text]
        else:
            value = [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]
    if isinstance(value, (list, tuple)):
        return [_value(v).strip() for v in value if _value(v).strip()][:limit]
    return [_value(value)]


def _spec(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if (not text or text.lower() in {"template path", "template", "pitch deck", "slide objects"}
                or re.fullmatch(r"<[^>]+>", text)):
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _write_path(path: str) -> Path:
    text = _value(path).strip().strip('"').strip("'")
    if not text or "<" in text or ">" in text or "path_to_" in text.lower():
        text = "pitch_deck.pptx"
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() and candidate.parent == Path("."):
        candidate = ROOT / "data" / "drafts" / candidate.name
    return safety.validate_write(candidate)


def _read_path(path: str) -> Path:
    text = _value(path).strip().strip('"').strip("'")
    lowered = text.lower()
    placeholder = ("<" in text or ">" in text or "path_to_deck" in lowered
                   or "path_to_" in lowered or not lowered.endswith(".pptx"))
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        root_candidate = ROOT / candidate
        if root_candidate.exists() and not placeholder:
            return safety.validate_read(root_candidate)
    if not candidate.is_absolute() or placeholder:
        drafts = ROOT / "data" / "drafts"
        pattern = "*.pptx" if placeholder or not candidate.name.lower().endswith(".pptx") else candidate.name
        matches = sorted(drafts.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return safety.validate_read(matches[0])
        if placeholder:
            raise ValueError(
                f"Path '{text}' is a placeholder and no generated presentation was found. "
                "Copy the full path from the CREATED PPTX PATH line returned by pptx_create."
            )
    return safety.validate_read(candidate)


def _theme_key(name: Any) -> str:
    key = str(name or get("designer.default_theme", "midnight")).strip().lower()
    key = THEME_ALIASES.get(key, key)
    return key if key in THEMES else "midnight"


def _theme(name: Any) -> dict[str, str]:
    return THEMES[_theme_key(name)]


def _background(slide, colors: dict[str, str]) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(colors["bg"])


def _shape(slide, kind, x: float, y: float, w: float, h: float,
           fill: str, line: str | None = None, radius: float | None = None):
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(fill)
    if line:
        shp.line.color.rgb = _rgb(line)
        shp.line.width = Pt(1)
    else:
        shp.line.fill.background()
    if radius is not None and hasattr(shp, "adjustments") and len(shp.adjustments):
        shp.adjustments[0] = radius
    return shp


def _text(slide, text: Any, x: float, y: float, w: float, h: float,
          size: float, color: str, bold: bool = False,
          align=PP_ALIGN.LEFT, font: str = "Aptos", valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.text = _value(text)
    p.alignment = align
    p.space_after = Pt(0)
    for run in p.runs:
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = _rgb(color)
    return box


def _bullets(slide, items: list[str], x: float, y: float, w: float, h: float,
             colors: dict[str, str], size: float = 19) -> None:
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    for index, item in enumerate(items[:6]):
        p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        p.text = item if item[:2] in ("- ", "• ") else "• " + item
        p.space_after = Pt(12)
        p.line_spacing = 1.05
        for run in p.runs:
            run.font.name = "Aptos"
            run.font.size = Pt(size)
            run.font.color.rgb = _rgb(colors["text"])


def _header(slide, spec: dict[str, Any], colors: dict[str, str], index: int,
            total: int, kicker: str = "") -> None:
    _shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 0.16, SH, colors["accent"])
    label = _value(spec.get("kicker") or kicker).upper()
    if label:
        _text(slide, label, 0.7, 0.36, 8.5, 0.3, 11, colors["accent"], True)
    title = _value(spec.get("title") or "Untitled slide")
    _text(slide, title, 0.7, 0.72, 10.9, 0.85, 31, colors["text"], True)
    subtitle = _value(spec.get("subtitle") or "")
    if subtitle:
        _text(slide, subtitle, 0.7, 1.55, 10.9, 0.4, 14, colors["muted"])
    _text(slide, f"{index:02d} / {total:02d}", 11.8, 0.44, 0.9, 0.3,
          10, colors["muted"], False, PP_ALIGN.RIGHT)
    _text(slide, "THAMBI DESIGN SYSTEM", 0.7, 7.02, 6.0, 0.22, 8, colors["muted"])


def _note(slide, spec: dict[str, Any]) -> None:
    note = _value(spec.get("note") or "")
    if not note:
        return
    try:
        slide.notes_slide.notes_text_frame.text = "Designer note: " + note
    except Exception:
        pass


def _title_slide(prs, spec: dict[str, Any], colors: dict[str, str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, SW, 0.18, colors["accent"])
    _shape(slide, MSO_SHAPE.OVAL, 9.1, -1.2, 5.6, 5.6, colors["alt"])
    _shape(slide, MSO_SHAPE.OVAL, 10.5, 4.6, 3.8, 3.8, colors["surface"])
    kicker = _value(spec.get("kicker") or "PRESENTATION")
    _text(slide, kicker.upper(), 0.9, 1.25, 8.0, 0.35, 13, colors["accent"], True)
    _text(slide, _value(spec.get("title") or "Presentation"), 0.9, 1.8, 9.5, 1.9,
          52, colors["text"], True)
    subtitle = _value(spec.get("subtitle") or "")
    if subtitle:
        _text(slide, subtitle, 0.92, 3.75, 8.8, 0.8, 20, colors["muted"])
    author = _value(spec.get("author") or "")
    if author:
        _text(slide, author, 0.92, 5.7, 7.0, 0.4, 14, colors["text"], True)
    _text(slide, _value(spec.get("date") or ""), 0.92, 6.12, 5.0, 0.3, 11, colors["muted"])
    _note(slide, spec)


def _closing_slide(prs, spec: dict[str, Any], colors: dict[str, str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _shape(slide, MSO_SHAPE.RECTANGLE, 0, SH - 0.18, SW, 0.18, colors["accent2"])
    _text(slide, _value(spec.get("title") or "Thank you"), 0.9, 2.0, 11.5, 1.3,
          46, colors["text"], True, PP_ALIGN.CENTER)
    cta = _value(spec.get("subtitle") or spec.get("cta") or "Let's build something meaningful.")
    _text(slide, cta, 1.4, 3.5, 10.5, 0.7, 20, colors["muted"], False, PP_ALIGN.CENTER)
    contact = _value(spec.get("contact") or "")
    if contact:
        _text(slide, contact, 1.4, 4.5, 10.5, 0.5, 15, colors["accent"], True, PP_ALIGN.CENTER)
    _note(slide, spec)


def _content_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "KEY IDEA")
    items = _items(spec.get("points") or spec.get("bullets") or spec.get("items"), 6)
    _bullets(slide, items, 0.8, 2.05, 7.4, 4.4, colors)
    callout = _value(spec.get("callout") or "")
    if callout:
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 8.6, 2.05, 3.9, 3.2,
               colors["surface"], radius=0.08)
        _shape(slide, MSO_SHAPE.RECTANGLE, 8.6, 2.05, 0.12, 3.2, colors["accent2"])
        _text(slide, "WHY IT MATTERS", 9.0, 2.35, 3.1, 0.3, 11, colors["accent2"], True)
        _text(slide, callout, 9.0, 2.8, 3.1, 2.0, 17, colors["text"])
    _note(slide, spec)


def _cards_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "FRAMEWORK")
    cards = spec.get("cards") or []
    if isinstance(cards, str):
        cards = _items(cards, 4)
        cards = [{"title": c} for c in cards]
    cards = cards[:4] if isinstance(cards, list) else []
    if not cards:
        cards = [{"title": f"Pillar {n + 1}", "body": "Add concise evidence here."} for n in range(3)]
    gap = 0.3
    width = (11.9 - gap * (len(cards) - 1)) / len(cards)
    for n, card in enumerate(cards):
        if not isinstance(card, dict):
            card = {"title": _value(card)}
        x = 0.7 + n * (width + gap)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.05, width, 4.35,
               colors["surface"], radius=0.06)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, 2.05, width, 0.12,
               colors["accent"] if n % 2 == 0 else colors["accent2"])
        _text(slide, f"0{n + 1}", x + 0.28, 2.4, width - 0.56, 0.4,
              13, colors["accent"], True)
        _text(slide, _value(card.get("title") or "Card"), x + 0.28, 2.95,
              width - 0.56, 0.8, 21, colors["text"], True)
        _text(slide, _value(card.get("body") or ""), x + 0.28, 3.85,
              width - 0.56, 2.1, 15, colors["muted"])
    _note(slide, spec)


def _stats_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "IMPACT")
    stats = spec.get("stats") or []
    stats_list = []
    if isinstance(stats, dict):
        stats_list = [{"value": k, "label": v} for k, v in stats.items()]
    elif isinstance(stats, list):
        stats_list = [s if isinstance(s, dict) else {"value": _value(s)} for s in stats]
    stats_list = stats_list[:4] or [
        {"value": "42%", "label": "Faster delivery"},
        {"value": "3.1x", "label": "Higher conversion"},
        {"value": "18", "label": "Pilot users"},
    ]
    gap = 0.35
    width = (11.9 - gap * (len(stats_list) - 1)) / len(stats_list)
    for n, stat in enumerate(stats_list):
        x = 0.7 + n * (width + gap)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.15, width, 3.6,
               colors["surface"], radius=0.08)
        _text(slide, _value(stat.get("value") or "—"), x + 0.2, 2.7,
              width - 0.4, 1.2, 42, colors["accent"], True, PP_ALIGN.CENTER)
        _text(slide, _value(stat.get("label") or ""), x + 0.25, 4.2,
              width - 0.5, 0.9, 16, colors["text"], True, PP_ALIGN.CENTER)
    _note(slide, spec)


def _timeline_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "ROADMAP")
    steps = spec.get("steps") or spec.get("milestones") or []
    steps = _items(steps, 5) or ["Discover", "Design", "Build", "Validate", "Scale"]
    y = 2.45
    _shape(slide, MSO_SHAPE.RECTANGLE, 0.95, y + 0.22, 11.4, 0.06, colors["alt"])
    gap = 11.4 / max(1, len(steps) - 1) if len(steps) > 1 else 0
    for n, step in enumerate(steps):
        if isinstance(step, dict):
            label = _value(step.get("title") or step.get("label") or "Step")
            detail = _value(step.get("body") or "")
        else:
            label, detail = _value(step), ""
        x = 0.95 + n * gap
        _shape(slide, MSO_SHAPE.OVAL, x - 0.16, y + 0.02, 0.46, 0.46,
               colors["accent"] if n == 0 else colors["surface"],
               line=colors["accent"])
        _text(slide, label, max(0.5, x - 1.0), y - 0.75, 2.0, 0.5,
              16, colors["text"], True, PP_ALIGN.CENTER)
        if detail:
            _text(slide, detail, max(0.5, x - 1.0), y + 0.75, 2.0, 1.2,
                  12, colors["muted"], False, PP_ALIGN.CENTER)
    _note(slide, spec)


def _chart_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "EVIDENCE")
    labels = _items(spec.get("labels"), 8) or ["Q1", "Q2", "Q3", "Q4"]
    values = spec.get("values") or [12, 19, 26, 34]
    if isinstance(values, str):
        values = [float(v.strip()) for v in values.split(",") if v.strip()]
    values = [float(v) for v in values][:8]
    if not values:
        values = [12, 19, 26, 34]
    chart_data = CategoryChartData()
    chart_data.categories = labels[:len(values)]
    chart_data.add_series(_value(spec.get("series") or "Result"), values)
    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.9), Inches(2.05),
        Inches(8.0), Inches(4.45), chart_data,
    )
    chart = frame.chart
    chart.has_legend = len(spec.get("series") or "") > 0
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.value_axis.has_major_gridlines = True
    chart.value_axis.tick_labels.font.size = Pt(10)
    chart.category_axis.tick_labels.font.size = Pt(10)
    if chart.series:
        chart.series[0].format.fill.solid()
        chart.series[0].format.fill.fore_color.rgb = _rgb(colors["accent"])
    insight = _value(spec.get("callout") or "")
    if insight:
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 9.25, 2.2, 3.25, 3.8,
               colors["surface"], radius=0.08)
        _text(slide, "READOUT", 9.6, 2.5, 2.6, 0.3, 11, colors["accent2"], True)
        _text(slide, insight, 9.6, 2.95, 2.6, 2.5, 16, colors["text"])
    _note(slide, spec)


def _quote_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "VOICE")
    quote = _value(spec.get("quote") or spec.get("title") or "Insight goes here.")
    _text(slide, "“", 0.85, 1.9, 1.2, 1.0, 72, colors["accent"], True)
    _text(slide, quote, 1.5, 2.15, 10.3, 2.7, 30, colors["text"], True)
    author = _value(spec.get("author") or "")
    if author:
        _text(slide, "— " + author, 1.5, 5.15, 8.0, 0.5, 16, colors["muted"])
    _note(slide, spec)


def _comparison_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "OPTIONS")
    left_title = _value(spec.get("left_title") or "Option A")
    right_title = _value(spec.get("right_title") or "Option B")
    left_items = _items(spec.get("left_points"), 5)
    right_items = _items(spec.get("right_points"), 5)
    for x, title, items, accent in (
        (0.7, left_title, left_items, colors["accent"]),
        (6.85, right_title, right_items, colors["accent2"]),
    ):
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.05, 5.75, 4.4,
               colors["surface"], radius=0.06)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, 2.05, 5.75, 0.12, accent)
        _text(slide, title, x + 0.35, 2.4, 5.05, 0.55, 22, colors["text"], True)
        _bullets(slide, items, x + 0.35, 3.15, 5.0, 2.9, colors, 16)
    _note(slide, spec)


def _table_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "DETAIL")
    headers = _items(spec.get("headers"), 6) or ["Item", "Value", "Status"]
    rows = spec.get("rows") or []
    rows = [r for r in rows if isinstance(r, (list, tuple))][:7] if isinstance(rows, list) else []
    if not rows:
        rows = [["Scope", "Defined", "Ready"], ["Risk", "Low", "Monitored"]]
    shape = slide.shapes.add_table(
        len(rows) + 1, len(headers), Inches(0.8), Inches(2.15),
        Inches(11.7), Inches(4.2),
    )
    table = shape.table
    for col, header in enumerate(headers):
        cell = table.cell(0, col)
        cell.text = _value(header)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(colors["accent"])
        for p in cell.text_frame.paragraphs:
            for run in p.runs:
                run.font.bold = True
                run.font.size = Pt(14)
                run.font.color.rgb = _rgb(colors["bg"])
    for row_index, row in enumerate(rows, 1):
        for col in range(len(headers)):
            cell = table.cell(row_index, col)
            cell.text = _value(row[col]) if col < len(row) else ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(colors["surface"] if row_index % 2 else colors["alt"])
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(13)
                    run.font.color.rgb = _rgb(colors["text"])
    _note(slide, spec)


def _image_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "VISUAL")
    image = _value(spec.get("image") or spec.get("path") or "")
    if not image:
        raise ValueError("image slide requires an 'image' path")
    p = safety.validate_read(image)
    slide.shapes.add_picture(str(p), Inches(0.9), Inches(2.05), width=Inches(11.5), height=Inches(4.0))
    caption = _value(spec.get("caption") or "")
    if caption:
        _text(slide, caption, 0.9, 6.2, 11.5, 0.4, 13, colors["muted"], False, PP_ALIGN.CENTER)
    _note(slide, spec)


def _section_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, SW, 0.18, colors["accent2"])
    _text(slide, _value(spec.get("kicker") or "SECTION"), 0.9, 2.0, 6.0, 0.4,
          13, colors["accent2"], True)
    _text(slide, _value(spec.get("title") or "Section"), 0.9, 2.6, 11.5, 1.4,
          46, colors["text"], True)
    subtitle = _value(spec.get("subtitle") or "")
    if subtitle:
        _text(slide, subtitle, 0.9, 4.3, 10.0, 0.7, 20, colors["muted"])
    _text(slide, f"{i:02d} / {total:02d}", 11.4, 6.7, 1.0, 0.3,
          11, colors["muted"], False, PP_ALIGN.RIGHT)
    _note(slide, spec)


def _agenda_slide(prs, spec: dict[str, Any], colors: dict[str, str], i: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, colors)
    _header(slide, spec, colors, i, total, "STRUCTURE")
    items = _items(spec.get("items") or spec.get("points"), 6) or ["Context", "Idea", "Evidence", "Next steps"]
    for n, item in enumerate(items):
        y = 2.05 + n * 0.78
        _shape(slide, MSO_SHAPE.OVAL, 0.85, y, 0.48, 0.48, colors["accent"])
        _text(slide, str(n + 1), 0.85, y + 0.08, 0.48, 0.3, 13, colors["bg"], True, PP_ALIGN.CENTER)
        _text(slide, item, 1.55, y + 0.02, 10.4, 0.45, 20, colors["text"], True)
    _note(slide, spec)


_BUILDERS = {
    "title": lambda prs, s, c, i, t: _title_slide(prs, s, c),
    "closing": lambda prs, s, c, i, t: _closing_slide(prs, s, c),
    "content": _content_slide,
    "cards": _cards_slide,
    "stats": _stats_slide,
    "timeline": _timeline_slide,
    "chart": _chart_slide,
    "quote": _quote_slide,
    "comparison": _comparison_slide,
    "table": _table_slide,
    "image": _image_slide,
    "section": _section_slide,
    "agenda": _agenda_slide,
}


_SLIDE_ALIASES = {
    "title": "title", "titleslide": "title", "cover": "title",
    "agenda": "agenda", "agendaslide": "agenda", "outline": "agenda",
    "section": "section", "section slide": "section", "divider": "section",
    "content": "content", "contentslide": "content", "text": "content",
    "cards": "cards", "cardsslide": "cards", "framework": "cards",
    "stats": "stats", "statsslide": "stats", "metrics": "stats",
    "timeline": "timeline", "timelineslide": "timeline", "roadmap": "timeline",
    "chart": "chart", "chartslide": "chart", "graph": "chart",
    "quote": "quote", "quoteslide": "quote", "testimonial": "quote",
    "comparison": "comparison", "comparisonslide": "comparison", "versus": "comparison",
    "table": "table", "tableslide": "table",
    "image": "image", "imageslide": "image", "visual": "image",
    "closing": "closing", "closingslide": "closing", "end": "closing", "thankyou": "closing",
}


def _slide_key(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", _value(value).lower()).strip()
    return key.replace(" ", "")


def _slide_kind(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", _value(value).lower()).strip()
    compact = key.replace(" ", "")
    return _SLIDE_ALIASES.get(compact) or _SLIDE_ALIASES.get(key) or "content"


def _normalize_slide(item: dict[str, Any]) -> dict[str, Any]:
    spec = dict(item)
    raw = _value(spec.get("content") or spec.get("body") or "")
    declared = spec.get("type") or spec.get("kind") or "content"
    kind = _slide_kind(declared)
    if _slide_key(declared) in _SLIDE_ALIASES or kind != "content":
        spec["type"] = kind
    if not raw:
        return spec
    m = re.match(r"^(title|agenda|section|content|cards|stats|timeline|chart|quote|"
                 r"comparison|table|image|closing)\s*slide\s*:\s*(.*)$", raw, re.IGNORECASE)
    if m:
        kind = _slide_kind(m.group(1))
        spec["type"] = kind
        raw = m.group(2).strip()
    parent = re.search(r"\((.*)\)\s*$", raw)
    tail = parent.group(1) if parent else ""
    title = raw[:parent.start()].strip(" -:") if parent else raw.strip(" -:")
    bits = [b.strip() for b in tail.split(",") if b.strip()] if tail else []
    if title:
        spec.setdefault("title", title)
    if kind in ("title", "closing"):
        spec.setdefault("subtitle", tail or "")
    elif kind == "agenda":
        spec.setdefault("items", bits or ["Problem", "Solution", "Proof", "Scale"])
    elif kind == "cards":
        spec.setdefault("cards", [{"title": b} for b in bits] or [])
    elif kind == "stats":
        stats: dict[str, str] = {}
        for bit in bits:
            parts = re.split(r"\s+", bit, maxsplit=1)
            if len(parts) == 2 and re.search(r"\d", parts[0]):
                stats[parts[0]] = parts[1].strip(" -")
        spec.setdefault("stats", stats or {})
    elif kind == "timeline":
        spec.setdefault("steps", bits or [])
    elif kind == "quote":
        spec.setdefault("quote", title)
        if parent:
            spec.setdefault("author", tail)
    elif kind == "comparison":
        left = re.search(r"Left\s*:\s*(.*?)(?:Right\s*:|$)", raw, re.IGNORECASE)
        right = re.search(r"Right\s*:\s*(.*)$", raw, re.IGNORECASE)
        spec.setdefault("left_title", left.group(1).strip(" -,") if left else "Status quo")
        spec.setdefault("right_title", right.group(1).strip(" -,") if right else "With THAMBI")
    elif kind == "chart":
        spec.setdefault("callout", title)
    else:
        spec.setdefault("points", bits or [title])
    return spec


def _default_slides(data: dict[str, Any]) -> list[dict[str, Any]]:
    points = _items(data.get("points"), 6)
    slides = [{"type": "title", "title": _value(data.get("title") or "Pitch Deck"),
               "subtitle": _value(data.get("subtitle") or "")}]
    if points:
        slides.append({"type": "content", "title": _value(data.get("section_title") or "The opportunity"),
                       "points": points})
    slides.append({"type": "closing", "title": _value(data.get("closing") or "Thank you"),
                   "subtitle": _value(data.get("cta") or "")})
    return slides


def _build(data: dict[str, Any]) -> Presentation:
    colors = _theme(data.get("theme"))
    raw = data.get("slides")
    slides = _items(raw, 40) if isinstance(raw, str) and not raw.strip().startswith("[") else raw
    if isinstance(slides, str):
        text = slides.strip()
        if re.fullmatch(r"\[[A-Za-z_ ]+\]", text) or not text:
            slides = []
        else:
            try:
                slides = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                slides = []
    if not isinstance(slides, list) or not slides:
        slides = _default_slides(data)
    if len(slides) > 40:
        raise ValueError("Presentation cannot exceed 40 slides")
    normalized: list[dict[str, Any]] = []
    for item in slides:
        if isinstance(item, str):
            item = {"type": "content", "title": item, "points": []}
        if not isinstance(item, dict):
            raise ValueError("Each slide must be an object or a string title")
        item = _normalize_slide(item)
        kind = str(item.get("type") or "content").lower()
        if kind not in _BUILDERS:
            raise ValueError(f"Unknown slide type '{kind}'. Available: {', '.join(SLIDE_TYPES)}")
        normalized.append(item)
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    prs.core_properties.title = _value(data.get("title") or "Presentation")
    prs.core_properties.subject = _value(data.get("subtitle") or "Designed by THAMBI")
    prs.core_properties.author = _value(data.get("author") or "THAMBI")
    default_titles = {
        "title": _value(data.get("title") or "Pitch Deck"),
        "agenda": "Story flow",
        "section": "The opportunity",
        "content": "Key points",
        "cards": "Our strengths",
        "stats": "The numbers",
        "timeline": "Roadmap",
        "chart": "The evidence",
        "quote": "In their words",
        "comparison": "Side by side",
        "table": "The details",
        "image": "Visual story",
        "closing": "Thank you",
    }
    total = len(normalized)
    for index, spec in enumerate(normalized, 1):
        kind = str(spec.get("type") or "content").lower()
        if not spec.get("title"):
            spec = dict(spec, title=default_titles.get(kind, _value(data.get("title") or "Pitch Deck")))
        _BUILDERS[kind](prs, spec, colors, index, total)
    return prs


@register("pptx_template", "Return a high-quality presentation spec template, themes, and slide types.",
          {"theme": "optional theme name"}, category="designer")
def pptx_template(theme: str = "midnight") -> str:
    key = _theme_key(theme)
    selected = THEMES[key]
    sample = {
        "theme": key,
        "title": "Melouz Technologies - Growth Deck",
        "subtitle": "A high-impact 2026 pitch narrative",
        "author": "Melouz Technologies",
        "slides": [
            {"type": "title", "title": "Designing What Matters", "subtitle": "A clear, confident pitch", "kicker": "MEL OUZ / 2026"},
            {"type": "agenda", "title": "Story flow", "items": ["Problem", "Solution", "Proof", "Scale"]},
            {"type": "content", "title": "One problem, sharply defined", "points": ["Manual workflows waste expert time", "Quality varies between teams", "Decisions lack shared evidence"], "callout": "Frame the pain in one memorable sentence."},
            {"type": "cards", "title": "Our advantage", "cards": [{"title": "Speed", "body": "Ship in days, not months."}, {"title": "Quality", "body": "Reusable design system."}, {"title": "Trust", "body": "Evidence at every step."}]},
            {"type": "stats", "title": "Traction", "stats": {"42%": "Faster delivery", "3.1x": "Higher conversion", "18": "Pilot users"}},
            {"type": "timeline", "title": "Roadmap", "steps": ["Discover", "Design", "Build", "Validate", "Scale"]},
            {"type": "chart", "title": "Growth signal", "labels": ["Q1", "Q2", "Q3", "Q4"], "values": [12, 19, 26, 34], "callout": "Four quarters of compounding adoption."},
            {"type": "quote", "title": "Customer voice", "quote": "It finally feels like one team.", "author": "Pilot customer"},
            {"type": "comparison", "title": "Why us", "left_title": "Status quo", "left_points": ["Slow", "Fragmented"], "right_title": "With THAMBI", "right_points": ["Fast", "Consistent"]},
            {"type": "closing", "title": "Let's build it", "subtitle": "Pilot with us in 30 days", "contact": "melouz.in"},
        ],
    }
    return json.dumps({
        "themes": sorted(THEMES),
        "theme_aliases": sorted(THEME_ALIASES),
        "requested_theme": _value(theme) or key,
        "selected_theme": key,
        "theme_note": "" if _theme_key(theme) == str(theme or "").strip().lower()
                       else f"'{theme}' resolved to '{key}'.",
        "slide_types": list(SLIDE_TYPES),
        "design_rules": [
            "One takeaway per slide; headlines state conclusions, not labels.",
            "Use 6-6 rule: max six lines and six words per line where possible.",
            "Use cards, stats, timelines, and charts instead of dense paragraphs.",
            "Keep 0.7 inch margins, consistent alignment, and generous whitespace.",
            "Never invent metrics; mark placeholders clearly if data is not supplied.",
        ],
        "template": sample,
    }, indent=2, ensure_ascii=False)


@register("pptx_create", "Create a polished 16:9 PowerPoint deck from a design spec after confirmation.",
          {"path": "destination .pptx path", "spec": "full JSON design spec (object or string)",
           "theme": "theme name", "title": "deck title", "subtitle": "deck subtitle",
           "slides": "JSON list of slide objects", "points": "JSON list of bullet points",
           "author": "author name", "overwrite": "true to replace existing file"},
          ["path"], category="designer", dangerous=True, read_only=False)
def pptx_create(path: str, spec: str | dict | None = None, theme: str = "midnight",
                title: str = "", subtitle: str = "", slides: Any = None,
                points: Any = None, author: str = "", overwrite: bool | str = False) -> str:
    data = _spec(spec)
    if slides is not None:
        data["slides"] = slides
    if points is not None:
        data["points"] = points
    if title:
        data["title"] = title
    if subtitle:
        data["subtitle"] = subtitle
    if author:
        data["author"] = author
    data["theme"] = _theme_key(theme if spec is None or "theme" not in data else data["theme"])
    destination = _write_path(path)
    if destination.suffix.lower() != ".pptx":
        raise ValueError("Presentation path must end with .pptx")
    overwrite_flag = overwrite is True or str(overwrite).lower() in ("1", "true", "yes")
    if destination.exists() and not overwrite_flag:
        raise FileExistsError(f"{destination} exists. Set overwrite=true and confirm to replace it.")
    prs = _build(data)
    action = "Overwrite" if destination.exists() else "Create"
    if not safety.require_confirmation(
        f"{action} presentation?",
        f"{destination}\nSlides: {len(prs.slides)}\nTheme: {data['theme']}",
        action="pptx_create",
    ):
        raise PermissionError("Presentation creation denied by user")
    if destination.exists():
        shutil.copy2(destination, destination.with_suffix(".pptx.aether.bak"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".pptx.aether.tmp")
    prs.save(str(tmp))
    os.replace(tmp, destination)
    return (f"CREATED PPTX PATH: {destination}\n"
            f"Created {len(prs.slides)} slide presentation at {destination}")


def _pitch_spec(topic: str, audience: str, theme: str, count: int,
                tone: str, highlights: Any) -> dict[str, Any]:
    title = _value(topic).strip() or "Pitch Deck"
    who = _value(audience).strip() or "investors"
    style = _value(tone).strip() or "confident and concise"
    points = _items(highlights, 6) or [
        f"Frame {title} as the answer to one urgent problem.",
        "Show the sharpest proof point first.",
        "Close with one clear, specific ask.",
    ]
    library: list[dict[str, Any]] = [
        {"type": "title", "title": title,
         "subtitle": f"A {style} pitch for {who}", "kicker": "PITCH DECK",
         "note": "Replace placeholders with verified facts before sharing."},
        {"type": "agenda", "title": "The story",
         "items": ["Problem", "Solution", "Proof", "Business model", "The ask"]},
        {"type": "content", "title": "The problem",
         "points": ["Name the painful status quo in one sentence",
                    "Quantify time, cost, or risk with verified data",
                    "Say who feels it most and why now"],
         "callout": "If you cannot prove it yet, mark it as an assumption."},
        {"type": "cards", "title": "The solution",
         "cards": [
             {"title": "Sharp focus", "body": f"{title} solves one problem exceptionally well."},
             {"title": "Fast proof", "body": "A short pilot turns claims into evidence."},
             {"title": "Clear value", "body": "Show the outcome in the customer's language."},
         ]},
        {"type": "content", "title": "Why it works", "points": points,
         "callout": "Use only facts you can defend."},
         {"type": "stats", "title": "Traction - replace placeholders",
          "stats": [{"value": "—", "label": "Pilot users"},
                    {"value": "—", "label": "Revenue or savings"},
                    {"value": "—", "label": "Retention"}],
          "note": "Placeholder metrics; do not present as real data."},
        {"type": "timeline", "title": "Roadmap",
         "steps": ["Validate", "Pilot", "Launch", "Scale"]},
        {"type": "comparison", "title": "Why us",
         "left_title": "Status quo", "left_points": ["Slow", "Fragmented", "Hard to prove"],
         "right_title": title, "right_points": ["Focused", "Measurable", "Fast to pilot"]},
        {"type": "quote", "title": "Customer voice - placeholder",
         "quote": "Add one verified customer sentence here.",
         "author": "Customer name, role"},
        {"type": "table", "title": "Business model",
         "headers": ["Revenue line", "Who pays", "Status"],
         "rows": [["Subscription", "Target customer", "Replace with fact"],
                  ["Services", "Pilot partners", "Replace with fact"]]},
        {"type": "content", "title": "The ask",
         "points": ["State the exact amount or commitment you need",
                    "Say what it unlocks in the next 6-12 months",
                    "End with one memorable line"],
         "callout": "Make the next step easy to say yes to."},
        {"type": "closing", "title": "Let's build it",
         "subtitle": "Pilot with us in 30 days",
         "contact": "Replace with contact details"},
    ]
    count = min(len(library), max(5, int(count)))
    chosen = library[:count - 1] + [library[-1]]
    return {
        "theme": _theme_key(theme),
        "title": title,
        "subtitle": f"A {style} pitch for {who}",
        "author": "THAMBI Designer",
        "slides": chosen,
    }


def _deck_spec_from_pptx(path: str, theme: str = "premium") -> dict[str, Any]:
    source = _read_path(path)
    prs = Presentation(source)
    slides: list[dict[str, Any]] = []
    for index, slide in enumerate(prs.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text.strip()
            if text and not text.startswith("THAMBI DESIGN SYSTEM") and not re.fullmatch(r"\d\d / \d\d", text):
                if text not in texts:
                    texts.append(text)
        title = texts[0] if texts else f"Slide {index}"
        kind = "title" if index == 1 else "closing" if index == len(prs.slides) and "thank" in title.lower() else "content"
        spec: dict[str, Any] = {"type": kind, "title": title, "points": texts[1:6]}
        if len(texts) > 1:
            spec["subtitle"] = texts[1]
        slides.append(spec)
    return {
        "theme": _theme_key(theme),
        "title": _value(prs.core_properties.title) or "Pitch Deck",
        "subtitle": _value(prs.core_properties.subject) or "Designed by THAMBI",
        "author": _value(prs.core_properties.author) or "THAMBI Designer",
        "slides": slides,
    }


def _parse_added_slides(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [_normalize_slide(s if isinstance(s, dict) else {"title": _value(s)}) for s in raw]
    text = _value(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [_normalize_slide(s if isinstance(s, dict) else {"title": _value(s)}) for s in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    result: list[dict[str, Any]] = []
    for line in re.split(r"[;\n]+", text):
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            title, body = line.split("|", 1)
            points = [p.strip() for p in body.split(",") if p.strip()]
        else:
            title, points = line, []
        result.append(_normalize_slide({"type": "content", "title": title.strip(), "points": points}))
    return result


@register("pptx_add_slides", "Insert new slides into an existing deck without rebuilding a JSON spec.",
          {"path": "existing presentation .pptx path",
           "after_slide": "slide number to insert after (default 1)",
           "slides": "new slides as a JSON list or 'Title | point one, point two' lines separated by ;",
           "theme": "theme name or alias (default premium)"},
          ["path", "slides"], category="designer", dangerous=True, read_only=False)
def pptx_add_slides(path: str, slides: Any, after_slide: int | str = 1,
                    theme: str = "premium") -> str:
    source = _read_path(path)
    spec = _deck_spec_from_pptx(str(source), theme)
    try:
        position = int(str(after_slide).strip())
    except (TypeError, ValueError):
        position = 1
    position = min(max(1, position), len(spec["slides"]))
    added = _parse_added_slides(slides)
    if not added:
        raise ValueError("No new slides were provided")
    spec["slides"] = list(spec["slides"][:position]) + added + list(spec["slides"][position:])
    created = pptx_create(
        path=str(source),
        spec=spec,
        theme=str(spec["theme"]),
        title=str(spec["title"]),
        subtitle=str(spec["subtitle"]),
        overwrite=True,
    )
    return (f"INSERTED {len(added)} SLIDES AFTER SLIDE {position}\n" + created)


@register("design_pitch_deck", "Design and create a complete pitch deck from a simple brief. No JSON spec needed.",
          {"topic": "what the pitch is about", "audience": "who will see it (default investors)",
           "theme": "theme name or alias (default premium)",
           "slides": "number of slides, 5-12 (default 8)",
           "tone": "design tone (default confident and concise)",
           "highlights": "comma-separated proof points or JSON list",
           "path": "optional output .pptx path"},
          ["topic"], category="designer", dangerous=True, read_only=False)
def design_pitch_deck(topic: str, audience: str = "investors", theme: str = "premium",
                      slides: int | str = 8, tone: str = "confident and concise",
                      highlights: Any = "", path: str = "") -> str:
    try:
        count = int(slides)
    except (TypeError, ValueError):
        count = 8
    brief = _pitch_spec(topic, audience, theme, count, tone, highlights)
    destination = _value(path).strip() or f"pitch_deck_{uuid.uuid4().hex[:6]}.pptx"
    created = pptx_create(
        path=destination,
        spec=brief,
        theme=str(brief["theme"]),
        overwrite=True,
    )
    return ("DESIGNED FROM BRIEF\n"
            f"Topic: {_value(topic)}\nAudience: {_value(audience) or 'investors'}\n"
            f"Theme: {brief['theme']}\n" + created)


@register("pptx_inspect", "Inspect a PowerPoint file and report slide count, titles, and text.",
          {"path": "presentation .pptx path", "max_chars": "character limit (default 12000)"},
          ["path"], category="designer")
def pptx_inspect(path: str, max_chars: int | str = 12000) -> str:
    p = _read_path(path)
    if p.suffix.lower() != ".pptx":
        raise ValueError("Inspection path must be a .pptx file")
    prs = Presentation(str(p))
    lines = [f"File: {p}", f"Slides: {len(prs.slides)}",
             f"Size: {prs.slide_width / 914400:.2f} x {prs.slide_height / 914400:.2f} inches"]
    for index, slide in enumerate(prs.slides, 1):
        texts = [shape.text.strip().replace("\n", " | ") for shape in slide.shapes
                 if getattr(shape, "has_text_frame", False) and shape.text.strip()]
        lines.append(f"\n[{index:02d}] " + (" || ".join(texts)[:500] if texts else "(no text)"))
    limit = min(50000, max(1000, int(max_chars)))
    return "\n".join(lines)[:limit]


@register("open_design_app", "Open Canva or PowerPoint after confirmation.",
          {"app": "canva or powerpoint"}, ["app"], category="designer",
          dangerous=True, read_only=False)
def open_design_app(app: str) -> str:
    key = str(app or "").strip().lower()
    if key == "canva":
        url = str(get("designer.canva_url", "https://www.canva.com/") or "https://www.canva.com/")
        if not safety.require_external("allow_external_research", "Open Canva?",
                                       f"The default browser will open {url}. No local data is sent."):
            raise PermissionError("External browser access was denied by the user")
        if not webbrowser.open(url, new=2):
            raise RuntimeError("No browser could be opened for Canva")
        return f"Opened Canva: {url}"
    if key in ("powerpoint", "ppt", "powerpnt"):
        target = safety.open_approved_app("powerpoint", confirm=True)
        return f"Opened PowerPoint: {target}"
    raise ValueError("app must be 'canva' or 'powerpoint'")


@register("open_presentation", "Open a generated presentation in its default app after confirmation.",
          {"path": "presentation .pptx path"}, ["path"], category="designer",
          dangerous=True, read_only=False)
def open_presentation(path: str) -> str:
    p = _read_path(path)
    if p.suffix.lower() != ".pptx":
        raise ValueError("Path must be a .pptx presentation")
    if not safety.require_confirmation("Open presentation?",
                                       f"{p}\n\nThis launches the default PowerPoint handler.",
                                       action="open_presentation"):
        raise PermissionError("Open denied by user")
    safety.open_external(str(p))
    return f"Opened presentation: {p}"
