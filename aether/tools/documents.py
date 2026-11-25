from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from PIL import Image

from .. import safety
from ..config import ROOT, get, resolve
from ..memory import default as default_memory
from ..retrieval import chunk_text, extract_text as extract_core
from .registry import register

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
_TEXT_EXT = {".txt", ".md", ".rst", ".csv", ".json", ".xml", ".py", ".c", ".h", ".cpp", ".ino", ".log"}
_EPUB_MIME = "application/epub+zip"

_ocr = None


def _ocr_engine():
    global _ocr
    if _ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError("OCR requires rapidocr-onnxruntime. Run: pip install rapidocr-onnxruntime") from e
        _ocr = RapidOCR()
    return _ocr


def _ocr_image_object(image) -> tuple[str, int]:
    result, _ = _ocr_engine()(image)
    if not result:
        return "", 0
    lines = [str(item[1]) for item in result if len(item) >= 2 and item[1]]
    return "\n".join(lines), len(lines)


def _soffice() -> str:
    configured = get("documents.soffice", "")
    candidates = [resolve(configured)] if configured else []
    candidates += [
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ]
    found = shutil.which("soffice")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c and c.exists():
            return str(c)
    raise RuntimeError("LibreOffice not found. Set documents.soffice in config/aether.json.")


def _convert_with_soffice(path: Path, output_ext: str) -> Path:
    target_ext = output_ext.split(":", 1)[0].strip().lower()
    outdir = ROOT / "data" / "drafts" / "converted"
    outdir.mkdir(parents=True, exist_ok=True)
    profile = ROOT / "data" / "drafts" / f"lo_profile_{uuid.uuid4().hex[:8]}"
    cmd = [
        _soffice(),
        f"-env:UserInstallation=file:///{profile.as_posix().lstrip('/')}",
        "--headless", "--norestore", "--convert-to", output_ext,
        "--outdir", str(outdir), str(path),
    ]
    proc = safety.run_fixed(cmd, cwd=ROOT, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"Document conversion failed: {(proc.stderr or proc.stdout or '')[:800]}")
    target = outdir / f"{path.stem}.{target_ext}"
    if not target.exists():
        matches = sorted(outdir.glob(f"{path.stem}*.{target_ext}"), key=lambda p: p.stat().st_mtime)
        if not matches:
            raise RuntimeError("Document conversion produced no output")
        target = matches[-1]
    return target


def _strip_html(text: str) -> str:
    try:
        from lxml import html
        doc = html.fromstring(text)
        for bad in doc.xpath("//script | //style | //noscript"):
            bad.drop_tree()
        text = doc.text_content()
    except (ImportError, ValueError):
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _epub_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
        m = re.search(r'full-path="([^"]+\.opf)"', container, re.I)
        if not m:
            raise ValueError("EPUB container has no OPF manifest")
        opf_path = m.group(1)
        opf = zf.read(opf_path).decode("utf-8", errors="replace")
        base = str(Path(opf_path).parent)
        manifest = dict(re.findall(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf, re.I))
        if not manifest:
            manifest = {i: h for h, i in re.findall(r'<item[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf, re.I)}
        spine = re.search(r"<spine[^>]*>(.*?)</spine>", opf, re.I | re.S)
        ids = re.findall(r'<itemref[^>]*idref="([^"]+)"', spine.group(1)) if spine else list(manifest)
        chunks = []
        for item_id in ids:
            href = manifest.get(item_id)
            if not href:
                continue
            member = str(Path(base) / href).replace("\\", "/")
            if member not in zf.namelist():
                member = href.lstrip("/")
            if member not in zf.namelist():
                continue
            raw = zf.read(member).decode("utf-8", errors="replace")
            text = _strip_html(raw)
            if text:
                chunks.append(text)
        return "\n\n".join(chunks)


def _odt_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("content.xml").decode("utf-8", errors="replace")
    try:
        from lxml import etree
        root = etree.fromstring(raw.encode("utf-8"))
        return "\n".join(x.strip() for x in root.itertext() if x.strip())
    except (ImportError, ValueError):
        return _strip_html(raw)


def _rtf_text(path: Path) -> str:
    raw = path.read_bytes().decode("cp1252", errors="replace")
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw)
    raw = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) & 0xFFFF), raw)
    raw = re.sub(r"\\[a-zA-Z]+\d*\s?", " ", raw)
    raw = raw.replace("{", "").replace("}", "")
    return re.sub(r"[ \t]+", " ", raw).strip()


def _pdf_pages(path: Path, start: int, pages: int, use_ocr: bool) -> tuple[int, str, int]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    first = max(1, start)
    last = min(total, first + max(1, pages) - 1)
    blocks: list[str] = []
    ocr_pages = 0
    for i in range(first - 1, last):
        text = (reader.pages[i].extract_text() or "").strip()
        if not text and use_ocr:
            try:
                import numpy as np
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(str(path))
                bitmap = pdf[i].render(scale=2.0).to_pil()
                text, _ = _ocr_image_object(np.array(bitmap.convert("RGB")))
                ocr_pages += 1
            except (ImportError, RuntimeError, ValueError) as e:
                text = f"[OCR unavailable: {e}]"
        blocks.append(f"[page {i + 1}]" + (" (OCR)" if use_ocr and ocr_pages else "") + "\n" + text)
    return total, "\n\n".join(blocks), ocr_pages


def _read_book(path: Path, start: int, pages: int, use_ocr: bool) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    meta: dict = {"path": str(path), "type": suffix or "text"}
    if suffix == ".pdf":
        total, text, ocr_pages = _pdf_pages(path, start, pages, use_ocr)
        meta.update({"pages": total, "pages_read": pages, "ocr_pages": ocr_pages})
        return text, meta
    if suffix in _IMAGE_EXT:
        with Image.open(path) as img:
            text, lines = _ocr_image_object(img.convert("RGB"))
        meta.update({"ocr_lines": lines})
        return text, meta
    if suffix in (".doc", ".dot"):
        converted = _convert_with_soffice(path, "docx")
        meta["converted_from"] = suffix
        return extract_core(converted), meta
    if suffix == ".epub":
        return _epub_text(path), meta
    if suffix == ".odt":
        return _odt_text(path), meta
    if suffix == ".rtf":
        return _rtf_text(path), meta
    if suffix in _TEXT_EXT:
        data = path.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return data.decode(enc), meta
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace"), meta
    return extract_core(path), meta


@register("ocr_image", "Extract text from an image with offline OCR (RapidOCR).",
          {"path": "image file", "max_chars": "limit (default 20000)"}, ["path"], category="documents")
def ocr_image(path: str, max_chars: int | str = 20000) -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() not in _IMAGE_EXT:
        raise ValueError(f"Not a supported image file: {p}")
    with Image.open(p) as img:
        text, lines = _ocr_image_object(img.convert("RGB"))
    limit = min(100000, max(1000, int(max_chars)))
    return f"OCR of {p} ({lines} lines):\n{text[:limit] or '(no text detected)'}"


@register("pdf_read", "Read PDF pages with embedded text and optional OCR for scanned pages.",
          {"path": "PDF file", "start_page": "first page (default 1)", "pages": "number of pages (default 10)",
           "ocr": "true to OCR pages without text", "max_chars": "limit (default 20000)"},
          ["path"], category="documents")
def pdf_read(path: str, start_page: int | str = 1, pages: int | str = 10,
             ocr: bool | str | None = None, max_chars: int | str = 20000) -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {p}")
    use_ocr = bool(get("documents.ocr_enabled", True)) if ocr is None else _flag(ocr)
    cap_pages = int(get("documents.max_pages", 40))
    total, text, ocr_pages = _pdf_pages(
        p, max(1, int(start_page)), min(cap_pages, max(1, int(pages))), use_ocr
    )
    limit = min(100000, max(1000, int(max_chars)))
    return f"PDF {p} - pages {total}, OCR pages {ocr_pages}:\n{text[:limit]}"


@register("extract_text_file", "Extract plain text from PDF, Word, EPUB, ODT, RTF, images, Office files and text files.",
          {"path": "file path", "ocr": "true to OCR scanned pages/images", "max_chars": "limit (default 20000)"},
          ["path"], category="documents")
def extract_text_file(path: str, ocr: bool | str | None = None,
                      max_chars: int | str = 20000) -> str:
    p = safety.validate_read(path)
    use_ocr = bool(get("documents.ocr_enabled", True)) if ocr is None else _flag(ocr)
    text, meta = _read_book(p, 1, int(get("documents.max_pages", 40)), use_ocr)
    limit = min(100000, max(1000, int(max_chars)))
    return json.dumps(meta, ensure_ascii=False) + "\n" + text[:limit]


@register("book_read", "Read a book or long document by page/chapter range with OCR fallback.",
          {"path": "book file (pdf, docx, doc, epub, odt, rtf, txt, image)",
           "start": "first page or section (default 1)", "pages": "number of pages (default 10)",
           "ocr": "true for OCR on scanned content", "max_chars": "limit (default 20000)"},
          ["path"], category="documents")
def book_read(path: str, start: int | str = 1, pages: int | str = 10,
              ocr: bool | str | None = None, max_chars: int | str = 20000) -> str:
    p = safety.validate_read(path)
    use_ocr = bool(get("documents.ocr_enabled", True)) if ocr is None else _flag(ocr)
    cap_pages = int(get("documents.max_pages", 40))
    text, meta = _read_book(p, max(1, int(start)), min(cap_pages, max(1, int(pages))), use_ocr)
    limit = min(100000, max(1000, int(max_chars)))
    return json.dumps(meta, ensure_ascii=False) + "\n" + text[:limit]


@register("book_index", "Extract and index a book/document into THAMBI's searchable local memory.",
          {"path": "book or document path", "ocr": "true to OCR scanned pages"}, ["path"],
          category="documents", read_only=False)
def book_index(path: str, ocr: bool | str | None = None) -> str:
    p = safety.validate_read(path)
    use_ocr = bool(get("documents.ocr_enabled", True)) if ocr is None else _flag(ocr)
    cap_pages = int(get("documents.max_pages", 40))
    text, _ = _read_book(p, 1, cap_pages, use_ocr)
    chunks = chunk_text(text, str(p))
    if not chunks:
        raise ValueError("No extractable text found in document")
    import hashlib
    stat = p.stat()
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    default_memory().upsert_document(str(p), stat.st_mtime, stat.st_size, digest, chunks)
    return f"Indexed {len(chunks)} chunks from {p} into local memory."


def _flag(value: bool | str | None) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _chapters(value: list | str) -> list[dict]:
    if isinstance(value, list):
        items = value
    else:
        try:
            items = json.loads(str(value))
        except json.JSONDecodeError as e:
            raise ValueError("chapters must be a JSON list") from e
    if not isinstance(items, list):
        raise ValueError("chapters must be a JSON list")
    result = []
    for i, item in enumerate(items, 1):
        if isinstance(item, str):
            result.append({"title": f"Chapter {i}", "content": item})
        elif isinstance(item, dict):
            result.append({
                "title": str(item.get("title") or f"Chapter {i}"),
                "content": str(item.get("content") or ""),
            })
        else:
            result.append({"title": f"Chapter {i}", "content": str(item)})
    return result


def _write_docx(path: Path, title: str, chapters: list[dict]) -> None:
    from docx import Document
    doc = Document()
    doc.add_heading(title, level=0)
    for chapter in chapters:
        doc.add_heading(chapter["title"], level=1)
        for line in chapter["content"].splitlines():
            if line.strip().startswith("## "):
                doc.add_heading(line.strip()[3:], level=2)
            elif line.strip().startswith("# "):
                doc.add_heading(line.strip()[2:], level=2)
            elif line.strip().startswith("- "):
                doc.add_paragraph(line.strip()[2:], style="List Bullet")
            elif line.strip():
                doc.add_paragraph(line)
    doc.save(str(path))


def _write_pdf(path: Path, title: str, chapters: list[dict]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    styles = getSampleStyleSheet()
    story = [Paragraph(xml_escape(title), styles["Title"]), Spacer(1, 12)]
    for chapter in chapters:
        story.append(Paragraph(xml_escape(chapter["title"]), styles["Heading1"]))
        for line in chapter["content"].splitlines():
            safe = xml_escape(line.strip()).replace("\t", "    ")
            if safe:
                story.append(Paragraph(safe, styles["BodyText"]))
                story.append(Spacer(1, 4))
        story.append(Spacer(1, 10))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def _write_epub(path: Path, title: str, chapters: list[dict]) -> None:
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    names = [f"chapter{i}.xhtml" for i in range(1, len(chapters) + 1)]
    manifest = "".join(
        f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(names, 1)
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(chapters) + 1))
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id1">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{xml_escape(title)}</dc:title>'
           '<dc:language>en</dc:language></metadata>'
           f'<manifest>{manifest}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
           f'<spine toc="ncx">{spine}</spine></package>')
    nav = "".join(
        f'<navPoint id="n{i}"><navLabel><text>{xml_escape(c["title"])}</text></navLabel>'
        f'<content src="{name}"/></navPoint>'
        for i, (c, name) in enumerate(zip(chapters, names), 1)
    )
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>'
           f'<docTitle><text>{xml_escape(title)}</text></docTitle><navMap>{nav}</navMap></ncx>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", _EPUB_MIME, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        for chapter, name in zip(chapters, names):
            body = "<br/>".join(xml_escape(line) for line in chapter["content"].splitlines())
            page = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                    f'<title>{xml_escape(chapter["title"])}</title></head><body>'
                    f'<h1>{xml_escape(chapter["title"])}</h1><p>{body}</p></body></html>')
            zf.writestr(f"OEBPS/{name}", page, compress_type=zipfile.ZIP_DEFLATED)


@register("book_write", "Write a book/document in DOCX, PDF, DOC, TXT, MD or EPUB format.",
          {"path": "destination path", "title": "book title", "chapters": "JSON list of {title, content}",
           "format": "docx|pdf|doc|txt|md|epub (default from extension)"},
          ["path", "title", "chapters"], category="documents", dangerous=True, read_only=False)
def book_write(path: str, title: str, chapters: list | str, format: str = "") -> str:
    p = safety.validate_write(path)
    fmt = (format or p.suffix.lower().lstrip(".")).strip().lower()
    if fmt not in ("docx", "pdf", "doc", "txt", "md", "epub"):
        raise ValueError("Unsupported format. Use docx, pdf, doc, txt, md or epub.")
    if p.suffix.lower().lstrip(".") != fmt:
        p = p.with_suffix("." + fmt)
    items = _chapters(chapters)
    if not items:
        raise ValueError("At least one chapter is required")
    preview = f"{title}\n" + "\n".join(f"- {c['title']}" for c in items[:20])
    if not safety.require_confirmation("Write book/document?", f"{p}\nFormat: {fmt}\n\n{preview[:2000]}",
                                       action="book_write"):
        raise PermissionError("Book write denied by user")
    p.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "docx":
        _write_docx(p, title, items)
    elif fmt == "pdf":
        _write_pdf(p, title, items)
    elif fmt == "epub":
        _write_epub(p, title, items)
    elif fmt in ("txt", "md"):
        body = "\n\n".join(f"# {c['title']}\n\n{c['content']}" for c in items)
        p.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    else:
        temp = p.with_name(f"{p.stem}_source.docx")
        _write_docx(temp, title, items)
        converted = _convert_with_soffice(temp, "doc:MS Word 97")
        if p.exists():
            p.unlink()
        os.replace(converted, p)
        temp.unlink(missing_ok=True)
    return f"Wrote {fmt} book '{title}' with {len(items)} chapters to {p}"
