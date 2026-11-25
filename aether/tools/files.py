from __future__ import annotations

import ctypes
import fnmatch
import os
import shutil
from ctypes import wintypes
from pathlib import Path

from .. import safety
from ..config import ROOT, load, resolve
from ..hardware import probe
from ..memory import default as default_memory
from ..retrieval import extract_text, index_folders, retrieve
from .registry import register


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _validate_dir(path: str | Path) -> Path:
    p = safety.validate_path(path)
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    return p


def _as_text(value: object, name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return "\n".join(value)
    raise TypeError(f"{name} must be a string or a list of strings")


@register("hardware_report", "Read CPU, RAM, GPU, storage and installed tool information.", {},
          category="files")
def hardware_report() -> str:
    import json
    return json.dumps(probe(), indent=2)


@register("list_dir", "List files and folders in a directory.",
          {"path": "directory path (default .)"}, category="files")
def list_dir(path: str = ".") -> str:
    p = _validate_dir(path)
    entries = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
        try:
            if child.is_dir():
                entries.append(f"[DIR]  {child.name}")
            else:
                entries.append(f"{child.stat().st_size:>10}  {child.name}")
        except OSError:
            entries.append(f"?         {child.name}")
    return f"{p}\n" + "\n".join(entries)


@register("read_file", "Read a text file with line numbers. Office/PDF files should use read_document.",
          {"path": "file path", "start": "first line (1-based)", "limit": "max lines (default 300)"},
          ["path"], category="files")
def read_file(path: str, start: int | str = 1, limit: int | str = 300) -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() in (".pdf", ".docx", ".xlsx", ".pptx"):
        return f"{p.name}: use read_document for this file type."
    start = max(1, int(start))
    limit = min(2000, max(1, int(limit)))
    data = p.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("Binary file; refusing to display")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start - 1:start - 1 + limit]
    out = [f"{i}: {line}" for i, line in enumerate(selected, start)]
    header = f"{p} (lines {start}-{start + len(selected) - 1} of {len(lines)})"
    return header + "\n" + "\n".join(out)


@register("search_files", "Search file names and optionally text contents under a folder.",
          {"query": "text to find", "root": "folder (default .)", "glob": "filename pattern (default *)",
           "content": "true to grep text content", "max_results": "limit (default 30)"},
          ["query"], category="files")
def search_files(query: str, root: str = ".", glob: str = "*", content: bool | str = False,
                 max_results: int | str = 30) -> str:
    base = _validate_dir(root)
    content = content is True or str(content).lower() in ("1", "true", "yes")
    max_results = min(200, max(1, int(max_results)))
    cfg = load()
    exclude = {d.lower() for d in cfg["index"].get("exclude_dirs", [])}
    q_low = query.lower()
    results = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude and not d.startswith(".")]
        for name in filenames:
            if len(results) >= max_results:
                break
            if not fnmatch.fnmatch(name.lower(), glob.lower()):
                continue
            p = Path(dirpath) / name
            rel = _rel(p)
            if q_low in name.lower():
                results.append(f"NAME  {rel}")
                continue
            if content:
                try:
                    if p.stat().st_size > 2 * 1024 * 1024:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                    for ln, line in enumerate(text.splitlines(), 1):
                        if q_low in line.lower():
                            results.append(f"LINE  {rel}:{ln}: {line.strip()[:240]}")
                            break
                except (OSError, UnicodeError):
                    continue
    if not results:
        return f"No matches for '{query}' under {base} (glob={glob}, content={content})."
    return f"Matches under {base}:\n" + "\n".join(results[:max_results])


@register("project_overview", "Summarise a code project: languages, build files, git status, README.",
          {"root": "project folder (default .)"}, category="coding")
def project_overview(root: str = ".") -> str:
    base = _validate_dir(root)
    counts: dict[str, int] = {}
    total = 0
    markers = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".cache"}]
        for name in filenames:
            total += 1
            suf = Path(name).suffix.lower() or "(no ext)"
            counts[suf] = counts.get(suf, 0) + 1
            if name.lower() in {"package.json", "pyproject.toml", "setup.py", "requirements.txt", "cmakelists.txt",
                                "makefile", "cargo.toml", "platformio.ini", "go.mod", ".gitignore"}:
                markers.append(_rel(Path(dirpath) / name))
    top = sorted(counts.items(), key=lambda x: -x[1])[:10]
    readme = ""
    for cand in ("README.md", "README.txt", "readme.md"):
        p = base / cand
        if p.exists():
            readme = p.read_text(encoding="utf-8", errors="replace")[:2500]
            break
    lines = [f"Project: {base}", f"Files: {total}", "Extensions: " + ", ".join(f"{k}={v}" for k, v in top)]
    if markers:
        lines.append("Build/config files: " + ", ".join(sorted(set(markers))[:20]))
    if readme:
        lines.append("README excerpt:\n" + readme)
    return "\n".join(lines)


@register("create_file", "Create a new text/code file after explicit confirmation. Alias of write_file.",
          {"path": "destination path", "content": "full text content", "overwrite": "true to allow overwrite"},
          ["path", "content"], category="files", dangerous=True, read_only=False)
@register("write_code_file", "Create a code file after explicit confirmation. Alias of write_file.",
          {"path": "destination path", "content": "full text content", "overwrite": "true to allow overwrite"},
          ["path", "content"], category="files", dangerous=True, read_only=False)
@register("write_file", "Create or overwrite a text file after explicit confirmation.",
          {"path": "destination path", "content": "full text content", "overwrite": "true to allow overwrite"},
          ["path", "content"], category="files", dangerous=True, read_only=False)
def write_file(path: str, content: str | list[str], overwrite: bool | str = False) -> str:
    text = _as_text(content, "content")
    p = safety.validate_write(path)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(f"Path is a directory: {p}")
    overwrite = overwrite is True or str(overwrite).lower() in ("1", "true", "yes")
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists. Set overwrite=true and confirm to replace it.")
    preview = text[:1200] + ("..." if len(text) > 1200 else "")
    action = "Overwrite" if p.exists() else "Create"
    if not safety.require_confirmation(
        f"{action} file?",
        f"{p}\n\n{preview}",
        action="write_file",
    ):
        raise PermissionError("Write denied by user")
    if p.exists():
        backup = p.with_suffix(p.suffix + ".aether.bak")
        shutil.copy2(p, backup)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".aether.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return f"Wrote {len(text)} chars to {p}"


@register("create_folder", "Create a folder after explicit confirmation.",
          {"path": "folder path"}, ["path"], category="files", dangerous=True, read_only=False)
def create_folder(path: str) -> str:
    p = safety.validate_write(path)
    if p.exists():
        raise FileExistsError(str(p))
    if not safety.require_confirmation("Create folder?", str(p), action="create_folder"):
        raise PermissionError("Folder creation denied by user")
    p.mkdir(parents=True, exist_ok=True)
    return f"Created {p}"


@register("delete_path", "Move a file or folder to the Recycle Bin after explicit confirmation.",
          {"path": "file or folder path"}, ["path"], category="files", dangerous=True, read_only=False)
def delete_path(path: str) -> str:
    p = safety.validate_path(path, must_exist=True)
    if p == ROOT or p.parent == p:
        raise PermissionError("Refusing to delete a root or the THAMBI project")
    if not safety.require_confirmation(
        "Move to Recycle Bin?",
        f"{p}\n\nThis can be restored from the Windows Recycle Bin.",
        action="delete_path",
    ):
        raise PermissionError("Delete denied by user")
    _recycle(p)
    return f"Moved to Recycle Bin: {p}"


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _recycle(path: Path) -> None:
    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400
    src = str(path.resolve()) + "\x00\x00"
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = src
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"Recycle Bin operation failed (code {rc})")


@register("index_folder", "Index a folder into local searchable memory (read-only).",
          {"path": "folder to index"}, ["path"], category="memory", read_only=False)
def index_folder(path: str) -> str:
    p = _validate_dir(path)
    report = index_folders([p], default_memory())
    msg = (f"Indexed {report['indexed']} new/changed, {report['skipped']} unchanged, "
           f"{report['chunks']} chunks, {report['removed']} removed.")
    if report["errors"]:
        msg += "\nErrors:\n" + "\n".join(f"- {e['path']}: {e['error']}" for e in report["errors"][:10])
    return msg


@register("search_documents", "Search indexed documents and long-term memory with citations.",
          {"query": "what to find", "limit": "max chunks (default 6)"}, ["query"], category="memory")
def search_documents(query: str, limit: int | str = 6) -> str:
    ctx, sources = retrieve(query, limit=int(limit))
    if not ctx:
        return "No indexed document or memory matches. Index folders first or state that the information is unavailable."
    cites = "\n".join(f"[{s['id']}] {s['path']} {('- ' + s['heading']) if s.get('heading') else ''}" for s in sources)
    return ctx + "\n\nSources:\n" + cites


@register("add_memory", "Save a durable note/project fact to local memory.",
          {"title": "short title", "content": "fact or note", "kind": "note|project|task|preference",
           "tags": "comma-separated tags", "source": "source (optional)"},
          ["title", "content"], category="memory", read_only=False)
def add_memory(title: str, content: str, kind: str = "note", tags: str = "", source: str = "") -> str:
    mid = default_memory().add_memory(title, content, kind=kind, tags=tags, source=source)
    return f"Saved memory #{mid}: {title}"


_CLEAN_SKIP_DIRS = {".venv", "models", "runtime", ".git", "node_modules", "site-packages"}


def _cleanup_candidates(include_voice: bool = True) -> tuple[list[Path], list[Path], dict[str, int]]:
    files: list[Path] = []
    dirs: list[Path] = []
    counts = {"Python cache": 0, "Temp/backup": 0, "LibreOffice profile": 0,
              "Voice recording": 0, "Test cache": 0, "Tool scratch": 0}

    def inside(p: Path, *parts: str) -> bool:
        rel = p.relative_to(ROOT)
        return rel.parts[:len(parts)] == parts

    for current, dirnames, filenames in os.walk(ROOT):
        base = Path(current)
        rel_parts = base.relative_to(ROOT).parts
        if rel_parts and rel_parts[0] in _CLEAN_SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _CLEAN_SKIP_DIRS]

        if "__pycache__" in dirnames:
            dirs.append(base / "__pycache__")
            dirnames.remove("__pycache__")
            counts["Python cache"] += 1
        if not rel_parts and ".pytest_cache" in dirnames:
            dirs.append(base / ".pytest_cache")
            dirnames.remove(".pytest_cache")
            counts["Test cache"] += 1
        if inside(base, "data", "drafts"):
            for name in list(dirnames):
                if name.startswith("lo_profile_"):
                    dirs.append(base / name)
                    dirnames.remove(name)
                    counts["LibreOffice profile"] += 1

        for name in filenames:
            path = base / name
            if name.endswith(".pyc"):
                category = "Python cache"
            elif name.endswith((".aether.tmp", ".aether.bak", ".aether.log")) or name == "aether.json.tmp":
                category = "Temp/backup"
            elif inside(base, "data", "index") and name.startswith("drc_") and name.endswith(".txt"):
                category = "Tool scratch"
            elif include_voice and inside(base, "data", "voice") and name.startswith("utterance_") and name.endswith(".wav"):
                category = "Voice recording"
            else:
                continue
            files.append(path)
            counts[category] += 1
    return files, dirs, counts


def _cleanup_size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            if p.is_dir():
                total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            elif p.exists():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _cleanup_summary(files: list[Path], dirs: list[Path], counts: dict[str, int],
                     removed: bool, freed: int = 0, errors: list[str] | None = None) -> str:
    label = "CLEANED" if removed else "FOUND"
    verb = "Removed" if removed else "Would remove"
    active = ", ".join(f"{name}: {n}" for name, n in counts.items() if n) or "none"
    preview = [(_rel(p) + "/") if p.is_dir() else _rel(p) for p in (dirs + files)[:12]
               if not removed or True]
    lines = [f"THAMBI Auto Cleaner - {label}",
             f"{verb}: {len(files) + len(dirs)} cache/temporary items ({freed / 1024 / 1024:.2f} MB)",
             f"Categories: {active}"]
    if preview:
        lines.append("Sample: " + ", ".join(preview))
    if removed and errors:
        lines.append(f"Could not remove {len(errors)} item(s): " + ", ".join(errors[:5]))
    return "\n".join(lines)


@register("cleanup_cache", "Safely remove THAMBI cache, temporary, backup, and stale tool artifacts. Never deletes memory, config, models, or source files.",
          {"dry_run": "true to preview only (default true)",
           "include_voice": "true to remove temporary utterance recordings (default true)",
           "confirmed": "internal: UI already confirmed"}, category="files",
          dangerous=True, read_only=False)
def cleanup_cache(dry_run: bool | str = True, include_voice: bool | str = True,
                  confirmed: bool | str = False) -> str:
    preview_mode = dry_run is True or str(dry_run).lower() in ("1", "true", "yes")
    include_voice_flag = include_voice is True or str(include_voice).lower() in ("1", "true", "yes")
    confirmed_flag = confirmed is True or str(confirmed).lower() in ("1", "true", "yes")

    files, dirs, counts = _cleanup_candidates(include_voice_flag)
    total = len(files) + len(dirs)
    if not total:
        return "THAMBI Auto Cleaner - nothing to clean. Cache and temporary folders are already clear."
    freed = _cleanup_size(files + dirs)
    if preview_mode:
        return _cleanup_summary(files, dirs, counts, removed=False, freed=freed)

    detail = _cleanup_summary(files, dirs, counts, removed=False, freed=freed)
    if not confirmed_flag and not safety.require_confirmation(
        "Run THAMBI Auto Cleaner?",
        detail + "\n\nMemory, config, models, documents, and delivered drafts are never removed.",
        action="cleanup_cache",
    ):
        raise PermissionError("Auto Cleaner cancelled by user")

    removed = 0
    errors: list[str] = []
    for p in dirs:
        try:
            if p.exists():
                shutil.rmtree(p)
                removed += 1
        except OSError as e:
            errors.append(f"{_rel(p)}: {e}")
    for p in files:
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except OSError as e:
            errors.append(f"{_rel(p)}: {e}")
    return _cleanup_summary(files, dirs, counts, removed=True, freed=freed, errors=errors)
