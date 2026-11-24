from __future__ import annotations

import getpass
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

from .config import LOG_DIR, ROOT, ensure_dirs, load, resolve

AuditFn = Callable[[dict], None]
ConfirmFn = Callable[[str, str], bool]

_lock = threading.RLock()
_confirm_fn: ConfirmFn | None = None
_audit_fn: AuditFn | None = None


def set_confirm_callback(fn: ConfirmFn | None) -> None:
    global _confirm_fn
    _confirm_fn = fn


def set_audit_callback(fn: AuditFn | None) -> None:
    global _audit_fn
    _audit_fn = fn


def audit(event: str, action: str, detail: str, result: str) -> None:
    ensure_dirs()
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "action": action,
        "detail": detail[:2000],
        "result": result,
        "user": getpass.getuser(),
    }
    with _lock:
        try:
            with (LOG_DIR / "audit.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
    if _audit_fn:
        try:
            _audit_fn(rec)
        except Exception:
            pass


def _console_confirm(title: str, detail: str) -> bool:
    print(f"\n[CONFIRMATION REQUIRED] {title}\n{detail}")
    try:
        return input("Type 'yes' to allow: ").strip().lower() == "yes"
    except EOFError:
        return False


def require_confirmation(title: str, detail: str, action: str = "") -> bool:
    fn = _confirm_fn or _console_confirm
    audit("confirmation_requested", action or title, detail, "pending")
    try:
        ok = bool(fn(title, detail))
    except Exception as e:
        audit("confirmation_error", action or title, str(e), "denied")
        return False
    audit("confirmation", action or title, detail, "approved" if ok else "denied")
    return ok


def allowed_roots() -> list[Path]:
    cfg = load()
    roots = [ROOT]
    for r in cfg["safety"].get("allowed_roots") or []:
        p = resolve(r).resolve()
        if p.exists() and p.is_dir():
            roots.append(p)
    return _unique_paths(roots)


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p not in out:
            out.append(p)
    return out


def within(path: Path, roots: Iterable[Path]) -> bool:
    p = path.resolve()
    for r in roots:
        try:
            p.relative_to(r.resolve())
            return True
        except ValueError:
            continue
    return False


def validate_path(path: str | Path, must_exist: bool = False) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not within(p, allowed_roots()):
        raise PermissionError(
            f"Path is outside allowed roots: {p}. Add it to safety.allowed_roots in config/aether.json."
        )
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


def validate_write(path: str | Path, must_exist: bool = False) -> Path:
    p = validate_path(path, must_exist=must_exist)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(str(p))
    return p


def validate_read(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (ROOT / p)
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise IsADirectoryError(str(p))
    return p


_PLACEHOLDER_HOSTS = {
    "example.com", "www.example.com", "example.org", "www.example.org",
    "example.net", "www.example.net", "localhost", "127.0.0.1", "::1",
}


def validate_http_url(value: str, label: str = "URL") -> str:
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{label} must be a valid http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not contain embedded credentials")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in _PLACEHOLDER_HOSTS or host.endswith((".example", ".invalid", ".localhost", ".test")):
        raise ValueError(f"{label} is a placeholder or local URL, not a public web address")
    return url


def require_external(flag: str, title: str, detail: str) -> bool:
    cfg = load()
    if not cfg["safety"].get(flag, False):
        raise PermissionError(
            f"External integration '{flag}' is disabled. Enable it in config/aether.json only if you authorise external access."
        )
    return require_confirmation(title, detail, action=flag)


def run_fixed(cmd: list[str], cwd: str | Path | None = None, timeout: int = 60,
              env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a fixed argv list. No shell, no model-generated command strings."""
    if not isinstance(cmd, list) or not all(isinstance(x, str) and "\x00" not in x for x in cmd):
        raise ValueError("Command must be a list of strings")
    cwd_p = None
    if cwd:
        cwd_p = Path(cwd).resolve()
        if not within(cwd_p, allowed_roots()) and not within(cwd_p, [Path.home()]):
            raise PermissionError(f"Working directory not allowed: {cwd_p}")
    flags = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.run(
        cmd,
        cwd=str(cwd_p) if cwd_p else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        creationflags=flags,
    )
    return p


def open_external(path: str, args: str = "") -> None:
    p = Path(path)
    if not p.exists() and not path.lower().endswith((".exe", ".cmd", ".bat")):
        raise FileNotFoundError(path)
    native = ROOT / "aether" / "native" / "aether_native.exe"
    cmd = [str(native), "launch", str(p)]
    if args:
        cmd.append(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ShellExecute failed")


def app_path(name: str) -> str:
    cfg = load()
    allow = cfg["safety"].get("app_allowlist") or {}
    key = name.strip().lower()
    if key not in allow:
        raise PermissionError(f"Application '{name}' is not in the approved allowlist: {', '.join(sorted(allow))}")
    target = allow[key]
    p = Path(target)
    if p.is_absolute() and p.exists():
        return str(p)
    found = None
    for base in (os.environ.get("PATH", "").split(os.pathsep) + [os.environ.get("WINDIR", r"C:\Windows")]):
        cand = Path(base) / target if base else p
        if cand.exists():
            found = cand
            break
    if found:
        return str(found)
    return target


def open_approved_app(name: str, confirm: bool = True) -> str:
    target = app_path(name)
    if confirm and not require_confirmation(
        f"Open {name}", f"Launch approved application: {target}", action="open_app"
    ):
        raise PermissionError("Launch denied by user")
    open_external(target)
    audit("action", "open_app", target, "opened")
    return target


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
