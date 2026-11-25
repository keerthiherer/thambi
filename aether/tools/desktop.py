from __future__ import annotations

from pathlib import Path

from .. import safety
from ..config import ROOT, load
from .registry import register

_APPROVED_PROCS = {
    "winword.exe", "excel.exe", "powerpnt.exe", "notepad.exe", "mspaint.exe",
    "code.exe", "kicad.exe", "eeschema.exe", "pcbnew.exe", "explorer.exe",
}

_TYPE_KEYS_SPECIAL = {
    "{": "{{}", "}": "{}}", "+": "{+}", "^": "{^}", "%": "{%}", "~": "{~}",
    "(": "{(}", ")": "{)}", "[": "{[}", "]": "{]}",
}


def _escape_type_keys(value: str) -> str:
    flat = str(value or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return "".join(_TYPE_KEYS_SPECIAL.get(ch, ch) for ch in flat)


@register("open_app", "Open an application from the approved allowlist after confirmation.",
          {"name": "approved app key, e.g. notepad, word, excel, kicad"}, ["name"],
          category="desktop", dangerous=True, read_only=False)
def open_app(name: str) -> str:
    target = safety.open_approved_app(name, confirm=True)
    return f"Opened approved application: {target}"


@register("open_path", "Open a file or folder in its default application after confirmation.",
          {"path": "file or folder path"}, ["path"], category="desktop", dangerous=True, read_only=False)
def open_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(str(p))
    if not safety.require_confirmation(
        "Open path?",
        f"{p}\n\nThis launches the default application for this item.",
        action="open_path",
    ):
        raise PermissionError("Open denied by user")
    safety.open_external(str(p))
    return f"Opened {p}"


def _desktop():
    try:
        from pywinauto import Desktop
    except ImportError as e:
        raise RuntimeError("pywinauto is not installed; run scripts\\install.ps1") from e
    return Desktop(backend="uia")


@register("list_windows", "List visible desktop windows.", {}, category="desktop")
def list_windows() -> str:
    desk = _desktop()
    rows = []
    for w in desk.windows():
        try:
            title = w.window_text().strip()
            proc = (w.element_info.process_id, w.element_info.control_type)
            if title:
                rows.append(f"{title[:100]}  [{proc[1]}]")
        except Exception:
            continue
    return "\n".join(rows[:80]) or "(no visible windows)"


@register("focus_window", "Focus an approved application window by title fragment.",
          {"title": "window title fragment"}, ["title"], category="desktop", dangerous=True, read_only=False)
def focus_window(title: str) -> str:
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None
    desk = _desktop()
    matches = []
    for w in desk.windows():
        try:
            t = w.window_text()
            if title.lower() in t.lower():
                matches.append(w)
        except Exception:
            continue
    if not matches:
        raise LookupError(f"No window matching '{title}'")
    w = matches[0]
    if psutil:
        try:
            proc = psutil.Process(w.element_info.process_id).name().lower()
        except Exception as e:
            raise PermissionError(f"Could not verify window process: {e}") from e
        if proc not in _APPROVED_PROCS:
            raise PermissionError(f"Window process '{proc}' is not in the approved application set")
    if not safety.require_confirmation("Focus window?", f"{w.window_text()}", action="focus_window"):
        raise PermissionError("Focus denied by user")
    w.set_focus()
    return f"Focused: {w.window_text()}"


@register("type_text", "Type text into an approved application window after confirmation. Does not press Enter.",
          {"title": "window title fragment", "text": "text to type"}, ["title", "text"],
          category="desktop", dangerous=True, read_only=False)
def type_text(title: str, text: str) -> str:
    try:
        import psutil  # type: ignore
    except ImportError as e:
        raise RuntimeError("psutil is not installed; run scripts\\install.ps1") from e
    desk = _desktop()
    for w in desk.windows():
        try:
            t = w.window_text()
            if title.lower() not in t.lower():
                continue
            proc = psutil.Process(w.element_info.process_id).name().lower()
            if proc not in _APPROVED_PROCS:
                raise PermissionError(f"Refusing to type into unapproved process: {proc}")
            if not safety.require_confirmation(
                "Type into application?",
                f"Window: {t}\nProcess: {proc}\n\n{text[:1500]}\n\nTHAMBI will not press Enter.",
                action="type_text",
            ):
                raise PermissionError("Typing denied by user")
            w.set_focus()
            w.type_keys(_escape_type_keys(text), with_spaces=True, set_foreground=True)
            return f"Typed {len(text)} characters into '{t}' (newlines converted to spaces; no Enter sent)"
        except PermissionError:
            raise
        except Exception:
            continue
    raise LookupError(f"No approved window matching '{title}'")
