from __future__ import annotations

import email
import hashlib
import json
import mimetypes
import os
import re
import subprocess
from email import policy
from pathlib import Path
from typing import Callable, Iterable

from .config import ROOT, get, load, resolve
from .memory import Memory, default as default_memory


class ExtractionError(RuntimeError):
    pass


def _read_bytes(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as f:
        return f.read(max_bytes)


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "utf-16"):
        try:
            text = data.decode(enc)
            if text.count("\x00") > len(text) * 0.02:
                continue
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ExtractionError("pypdf not installed; run scripts\\install.ps1") from e
    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as e:
            raise ExtractionError(f"Encrypted PDF: {path}") from e
    parts = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(f"[page {i + 1}]\n{t}")
    return "\n\n".join(parts)


def extract_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as e:
        raise ExtractionError("python-docx not installed; run scripts\\install.ps1") from e
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for ti, table in enumerate(d.tables):
        rows = []
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"[table {ti + 1}]\n" + "\n".join(rows))
    return "\n".join(parts)


def extract_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError as e:
        raise ExtractionError("openpyxl not installed; run scripts\\install.ps1") from e
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"[sheet: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(vals):
                parts.append(" | ".join(vals))
    wb.close()
    return "\n".join(parts)


def extract_pptx(path: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:
        raise ExtractionError("python-pptx not installed; run scripts\\install.ps1") from e
    prs = Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"[slide {i}]")
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                parts.append(shape.text)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
    return "\n".join(parts)


def parse_eml(path: Path) -> dict:
    with path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain":
                try:
                    body_parts.append(str(part.get_content()))
                except Exception:
                    pass
        if not body_parts:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        html = str(part.get_content())
                        body_parts.append(re.sub(r"<[^>]+>", " ", html))
                    except Exception:
                        pass
    else:
        try:
            body_parts.append(str(msg.get_content()))
        except Exception:
            payload = msg.get_payload(decode=True) or b""
            body_parts.append(_decode(payload))
    text = "\n".join(body_parts)
    return {
        "uid": msg.get("Message-ID") or f"file:{path.resolve()}",
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "subject": str(msg.get("Subject", path.stem)),
        "date": str(msg.get("Date", "")),
        "body": text,
        "thread_id": str(msg.get("Thread-Index", "") or msg.get("Message-ID", "") or path.stem),
        "path": str(path),
    }


def extract_text(path: str | Path, max_file_mb: float | None = None) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise IsADirectoryError(str(p))
    cfg = load()
    max_mb = max_file_mb if max_file_mb is not None else cfg["index"]["max_file_mb"]
    if p.stat().st_size > max_mb * 1024 * 1024:
        raise ExtractionError(f"File exceeds {max_mb} MB indexing limit: {p}")
    suf = p.suffix.lower()
    if suf == ".pdf":
        return extract_pdf(p)
    if suf == ".docx":
        return extract_docx(p)
    if suf in (".xlsx", ".xlsm"):
        return extract_xlsx(p)
    if suf == ".pptx":
        return extract_pptx(p)
    if suf == ".eml":
        e = parse_eml(p)
        return f"From: {e['from']}\nTo: {e['to']}\nDate: {e['date']}\nSubject: {e['subject']}\n\n{e['body']}"
    if suf in (".xls", ".doc", ".ppt"):
        raise ExtractionError(f"Legacy binary format not supported: {p}. Convert to modern Office format.")
    return _decode(_read_bytes(p, int(max_mb * 1024 * 1024)))


def chunk_text(text: str, path: str = "", max_chars: int = 1400, overlap: int = 180) -> list[dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    lines = text.split("\n")
    chunks: list[dict] = []
    buf: list[str] = []
    size = 0
    heading = ""

    def emit() -> None:
        nonlocal buf, size
        body = "\n".join(buf).strip()
        if body:
            chunks.append({"text": body, "heading": heading})
        if len(body) > overlap:
            tail = body[-overlap:]
            buf = [tail]
            size = len(tail)
        else:
            buf = []
            size = 0

    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            if size > max_chars * 0.5:
                emit()
            heading = re.sub(r"^#+\s*", "", stripped)[:160]
        if size + len(line) + 1 > max_chars and buf:
            emit()
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append({"text": "\n".join(buf).strip(), "heading": heading})
    return [c for c in chunks if c["text"]]


def _native_scan(folder: Path, max_depth: int = 14, max_files: int = 200000) -> list[dict]:
    native = ROOT / "aether" / "native" / "aether_native.exe"
    if not native.exists():
        return []
    try:
        p = subprocess.run(
            [str(native), "scan", str(folder), str(max_depth), str(max_files)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in p.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _walk(folder: Path, exclude: set[str]) -> list[dict]:
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"p": str(p), "s": st.st_size, "m": int(st.st_mtime * 1000)})
    return out


def discover_files(folders: Iterable[str | Path], cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load()
    exts = {e.lower() for e in cfg["index"]["extensions"]}
    exclude = {d.lower() for d in cfg["index"].get("exclude_dirs", [])}
    seen: set[str] = set()
    files: list[dict] = []
    for folder in folders:
        fp = Path(folder).expanduser().resolve()
        if not fp.exists() or not fp.is_dir():
            continue
        rows = _native_scan(fp)
        if not rows:
            rows = _walk(fp, exclude)
        for r in rows:
            p = str(r.get("p", ""))
            if not p:
                continue
            if any(part.lower() in exclude for part in Path(p).parts):
                continue
            if Path(p).suffix.lower() not in exts:
                continue
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                continue
            seen.add(key)
            files.append({"path": os.path.abspath(p), "size": int(r.get("s", 0)), "mtime": int(r.get("m", 0)) / 1000.0})
    return files


def index_folders(folders: Iterable[str | Path], mem: Memory | None = None,
                  progress: Callable[[str], None] | None = None) -> dict:
    mem = mem or default_memory()
    cfg = load()
    files = discover_files(folders, cfg)
    state = mem.document_state()
    report = {"scanned": len(files), "indexed": 0, "skipped": 0, "errors": [], "chunks": 0, "removed": 0}
    present = set()
    max_mb = cfg["index"]["max_file_mb"]
    for i, f in enumerate(files, 1):
        path = f["path"]
        present.add(path)
        if progress and i % 5 == 0:
            progress(f"Indexing {i}/{len(files)}: {Path(path).name}")
        try:
            st = os.stat(path)
            old = state.get(path)
            if old and abs(old["mtime"] - st.st_mtime) < 0.001 and old["size"] == st.st_size:
                report["skipped"] += 1
                continue
            if st.st_size > max_mb * 1024 * 1024:
                report["skipped"] += 1
                continue
            text = extract_text(path, max_mb)
            chunks = chunk_text(text, path)
            if not chunks:
                report["skipped"] += 1
                continue
            h = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
            if old and old["sha256"] == h:
                mem.upsert_document(path, st.st_mtime, st.st_size, h, chunks)
                report["skipped"] += 1
                continue
            mem.upsert_document(path, st.st_mtime, st.st_size, h, chunks)
            report["indexed"] += 1
            report["chunks"] += len(chunks)
        except Exception as e:
            report["errors"].append({"path": path, "error": str(e)})
    report["removed"] = mem.remove_missing_documents(present)
    return report


def index_configured(mem: Memory | None = None, progress: Callable[[str], None] | None = None) -> dict:
    cfg = load()
    folders = [resolve(f) for f in cfg["index"].get("folders", [])]
    if not folders:
        return {"scanned": 0, "indexed": 0, "skipped": 0, "errors": [], "chunks": 0, "removed": 0,
                "note": "No folders configured. Add index.folders in config/aether.json or use the Memory tab."}
    return index_folders(folders, mem, progress)


def retrieve(query: str, mem: Memory | None = None, limit: int | None = None) -> tuple[str, list[dict]]:
    mem = mem or default_memory()
    cfg = load()
    limit = limit or cfg["memory"]["retrieval_chunks"]
    hits = mem.search_chunks(query, limit)
    memories = mem.search_memories(query, 4)
    max_chars = cfg["memory"]["max_context_chars"]
    used: list[dict] = []
    blocks: list[str] = []
    total = 0
    grouped: dict[str, dict] = {}
    for h in hits:
        key = str(h["path"])
        group = grouped.setdefault(key, {"path": key, "texts": [], "headings": [], "score": h.get("score")})
        if h["text"] not in group["texts"]:
            group["texts"].append(h["text"])
        if h.get("heading") and h["heading"] not in group["headings"]:
            group["headings"].append(h["heading"])
    for group in grouped.values():
        text = "\n\n".join(group["texts"])
        if total + len(text) > max_chars:
            text = text[: max(0, max_chars - total)]
        if not text.strip():
            break
        idx = len(used) + 1
        heading = " / ".join(group["headings"])
        heading = f" / {heading}" if heading else ""
        blocks.append(f"[S{idx}] {group['path']}{heading}\n{text}")
        used.append({"id": f"S{idx}", "path": group["path"], "heading": " / ".join(group["headings"]),
                     "score": group["score"], "type": "document"})
        total += len(text)
        if total >= max_chars:
            break
    for m in memories:
        idx = len(used) + 1
        text = m["content"][:1200]
        blocks.append(f"[S{idx}] memory: {m['title']} ({m.get('kind', 'note')})\n{text}")
        used.append({"id": f"S{idx}", "path": f"memory:{m['id']}", "heading": m["title"],
                     "score": m.get("score"), "type": "memory"})
        total += len(text)
        if total >= max_chars:
            break
    return "\n\n".join(blocks), used
