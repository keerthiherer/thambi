from __future__ import annotations

import json
from pathlib import Path

from .. import safety
from ..config import ROOT
from ..retrieval import extract_text
from .registry import register


@register("read_document", "Extract text/tables from PDF, Word, Excel or PowerPoint files.",
          {"path": "document path", "max_chars": "max characters (default 20000)"},
          ["path"], category="office")
def read_document(path: str, max_chars: int | str = 20000) -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() not in (".pdf", ".docx", ".xlsx", ".pptx"):
        return f"{p.name} is not an Office/PDF document; use read_file."
    text = extract_text(p)
    limit = min(100000, max(1000, int(max_chars)))
    extra = "" if len(text) <= limit else f"\n\n[truncated {len(text) - limit} chars]"
    return f"{p}\n\n{text[:limit]}{extra}"


def _confirm_write(p: Path, kind: str) -> None:
    action = "Overwrite" if p.exists() else "Create"
    if not safety.require_confirmation(
        f"{action} {kind}?",
        f"{p}\n\nTHAMBI will create this file locally.",
        action=f"create_{kind}",
    ):
        raise PermissionError("File creation denied by user")


@register("create_docx", "Create a Word document after confirmation.",
          {"path": "output .docx", "title": "document title", "content": "text; lines starting with ## are headings"},
          ["path", "title", "content"], category="office", dangerous=True, read_only=False)
def create_docx(path: str, title: str, content: str) -> str:
    try:
        import docx
    except ImportError as e:
        raise RuntimeError("python-docx is not installed") from e
    p = safety.validate_write(path)
    if p.suffix.lower() != ".docx":
        p = p.with_suffix(".docx")
    _confirm_write(p, "Word document")
    p.parent.mkdir(parents=True, exist_ok=True)
    d = docx.Document()
    d.add_heading(title, level=0)
    for line in content.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            d.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            d.add_heading(line[2:].strip(), level=1)
        elif line.startswith("- "):
            d.add_paragraph(line[2:], style="List Bullet")
        else:
            d.add_paragraph(line)
    d.save(str(p))
    return f"Created {p} ({len(content)} chars)"


@register("create_xlsx", "Create an Excel workbook from rows after confirmation.",
          {"path": "output .xlsx", "sheet": "sheet name", "rows": "list of row lists (or JSON string)"},
          ["path", "rows"], category="office", dangerous=True, read_only=False)
def create_xlsx(path: str, rows, sheet: str = "Sheet1") -> str:
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError("openpyxl is not installed") from e
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except json.JSONDecodeError as e:
            raise ValueError("rows must be a JSON array of arrays") from e
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    p = safety.validate_write(path)
    if p.suffix.lower() != ".xlsx":
        p = p.with_suffix(".xlsx")
    _confirm_write(p, "Excel workbook")
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = str(sheet)[:31]
    for row in rows:
        if isinstance(row, (list, tuple)):
            ws.append(list(row))
        else:
            ws.append([row])
    wb.save(str(p))
    return f"Created {p} ({len(rows)} rows)"


@register("create_pptx", "Create a PowerPoint deck after confirmation.",
          {"path": "output .pptx", "title": "deck title", "slides": "list of {title, bullets} (or JSON string)"},
          ["path", "title", "slides"], category="office", dangerous=True, read_only=False)
def create_pptx(path: str, title: str, slides) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:
        raise RuntimeError("python-pptx is not installed") from e
    if isinstance(slides, str):
        try:
            slides = json.loads(slides)
        except json.JSONDecodeError as e:
            raise ValueError("slides must be JSON [{title, bullets:[...]}]") from e
    p = safety.validate_write(path)
    if p.suffix.lower() != ".pptx":
        p = p.with_suffix(".pptx")
    _confirm_write(p, "PowerPoint deck")
    p.parent.mkdir(parents=True, exist_ok=True)
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = title
    if len(s.placeholders) > 1:
        s.placeholders[1].text = "Prepared with THAMBI"
    for item in slides or []:
        if isinstance(item, str):
            item = {"title": item, "bullets": []}
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = str(item.get("title", ""))
        body = sl.placeholders[1].text_frame
        body.clear()
        for i, b in enumerate(item.get("bullets") or []):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            para.text = str(b)
            para.level = 0
    prs.save(str(p))
    return f"Created {p} ({len(slides or []) + 1} slides)"


@register("create_pdf", "Create a PDF from plain text after confirmation.",
          {"path": "output .pdf", "title": "title", "content": "text"}, ["path", "title", "content"],
          category="office", dangerous=True, read_only=False)
def create_pdf(path: str, title: str, content: str) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as e:
        raise RuntimeError("reportlab is not installed") from e
    p = safety.validate_write(path)
    if p.suffix.lower() != ".pdf":
        p = p.with_suffix(".pdf")
    _confirm_write(p, "PDF")
    p.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(p), pagesize=A4, margins=(18 * mm, 18 * mm, 18 * mm, 18 * mm))
    story = [Paragraph(title, styles["Title"]), Spacer(1, 8)]
    for line in content.splitlines():
        if line.strip():
            story.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), styles["BodyText"]))
            story.append(Spacer(1, 4))
    doc.build(story)
    return f"Created {p}"
