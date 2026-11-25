from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .. import safety
from ..config import ROOT, load
from ..memory import default as default_memory
from .registry import register

_ALLOWED_COMMANDS = {
    "git_status": ["git", "status", "--short", "--branch"],
    "git_diff_stat": ["git", "diff", "--stat"],
    "git_log": ["git", "log", "--oneline", "-n", "10"],
    "python_version": [sys.executable, "--version"],
    "nvidia_smi": ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"],
    "disk_usage": [sys.executable, "-c", "import shutil; p=shutil.disk_usage('.'); print(p.free // 2**30, 'GB free')"],
}


def _root(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    return p


def _excluded() -> set[str]:
    return {d.lower() for d in load()["index"].get("exclude_dirs", [])}


def _flag(value: bool | str) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _http_url(value: str, label: str = "URL") -> str:
    return safety.validate_http_url(value, label)


@register("glob_files", "Find files by glob pattern (opencode-style, e.g. **/*.py).",
          {"pattern": "glob pattern", "root": "folder (default .)", "max_results": "limit (default 200)"},
          ["pattern"], category="opencode")
def glob_files(pattern: str, root: str = ".", max_results: int | str = 200) -> str:
    base = _root(root)
    limit = min(500, max(1, int(max_results)))
    excluded = _excluded()
    out: list[str] = []
    for p in sorted(base.rglob(pattern)):
        if len(out) >= limit:
            break
        if not p.is_file() or any(part.lower() in excluded or part.startswith(".") for part in p.parts):
            continue
        try:
            out.append(f"{p.relative_to(base)}  ({p.stat().st_size} bytes)")
        except OSError:
            out.append(str(p.relative_to(base)))
    if not out:
        return f"No files matching '{pattern}' under {base}."
    return f"Matches under {base}:\n" + "\n".join(out)


@register("grep_content", "Regex or literal content search across files (opencode-style grep).",
          {"pattern": "regex or literal text", "root": "folder (default .)", "glob": "filename pattern (default *)",
           "regex": "true for regex (default false)", "max_results": "limit (default 100)"},
          ["pattern"], category="opencode")
def grep_content(pattern: str, root: str = ".", glob: str = "*", regex: bool | str = False,
                 max_results: int | str = 100) -> str:
    import fnmatch
    base = _root(root)
    use_regex = _flag(regex)
    try:
        rx = re.compile(pattern, re.IGNORECASE) if use_regex else None
    except re.error as e:
        raise ValueError(f"Invalid regular expression: {e}") from e
    limit = min(300, max(1, int(max_results)))
    excluded = _excluded()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d.lower() not in excluded and not d.startswith(".")]
        for name in filenames:
            if len(out) >= limit:
                break
            if not fnmatch.fnmatch(name.lower(), glob.lower()):
                continue
            p = Path(dirpath) / name
            try:
                if p.stat().st_size > 2 * 1024 * 1024:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), 1):
                if (rx.search(line) if rx else pattern.casefold() in line.casefold()):
                    out.append(f"{p.relative_to(base)}:{idx}: {line.strip()[:240]}")
                    if len(out) >= limit:
                        break
    if not out:
        return f"No matches for '{pattern}' under {base}."
    return "\n".join(out)


@register("run_allowed_command", "Run a fixed, allowlisted read-only command after confirmation.",
          {"command": ", ".join(sorted(_ALLOWED_COMMANDS)), "root": "working folder (default .)"},
          ["command"], category="opencode", dangerous=True, read_only=False)
def run_allowed_command(command: str, root: str = ".") -> str:
    key = command.strip().lower()
    if key not in _ALLOWED_COMMANDS:
        raise ValueError(f"Command not allowed. Choose one of: {', '.join(sorted(_ALLOWED_COMMANDS))}")
    argv = list(_ALLOWED_COMMANDS[key])
    if argv[0] == "git" and not shutil.which("git"):
        raise RuntimeError("git is not installed")
    if not safety.require_confirmation(
        "Run allowlisted command?", f"{' '.join(argv)}\nFolder: {_root(root)}", action="run_allowed_command"
    ):
        raise PermissionError("Command denied by user")
    proc = safety.run_fixed(argv, cwd=_root(root), timeout=60)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"
    return f"$ {' '.join(argv)}\nexit={proc.returncode}\n{text[:16000]}"


@register("delegate_task", "Delegate a focused subtask to THAMBI's own reasoning loop and return its answer.",
          {"prompt": "clear subtask description", "module": "module persona (default chat)"},
          ["prompt"], category="opencode", dangerous=True, read_only=False)
def delegate_task(prompt: str, module: str = "chat") -> str:
    if not safety.require_confirmation(
        "Run delegated subtask?", f"Module: {module}\n\n{prompt[:1200]}", action="delegate_task"
    ):
        raise PermissionError("Delegation denied by user")
    from ..agent import ChatEngine

    engine = ChatEngine()
    res = engine.reply(prompt, module=module)
    text = res.get("text") or "(no answer)"
    return f"SUBTASK RESULT ({module}):\n{text[:8000]}"


def _parse_items(value: list | str) -> list[dict]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    text = str(value or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("items must be a JSON list of objects") from e
    if not isinstance(parsed, list):
        raise ValueError("items must be a JSON list of objects")
    return [x for x in parsed if isinstance(x, dict)]


@register("todo_write", "Create or update tracked todos (opencode-style todowrite).",
          {"items": "JSON list of {id?, title, status?, priority?}"}, ["items"],
          category="opencode", read_only=False)
def todo_write(items: list | str) -> str:
    mem = default_memory()
    out: list[str] = []
    for item in _parse_items(items):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        item_id = item.get("id")
        status = str(item.get("status") or "open")
        priority = str(item.get("priority") or "normal")
        if item_id and mem.update_task(int(item_id), status=status, priority=priority):
            out.append(f"updated #{item_id}: {title}")
        else:
            new_id = mem.add_task(title, priority=priority)
            if status != "open":
                mem.update_task(new_id, status=status)
            out.append(f"created #{new_id}: {title}")
    return "\n".join(out) or "No valid todo items supplied."


@register("todo_read", "List tracked todos (opencode-style todoread).",
          {"status": "open|done|all (default open)", "limit": "limit (default 50)"}, category="opencode")
def todo_read(status: str = "open", limit: int | str = 50) -> str:
    status = status.strip().lower()
    rows = default_memory().list_tasks(status=None if status == "all" else status,
                                       limit=min(200, max(1, int(limit))))
    if not rows:
        return "No matching todos."
    return "\n".join(f"#{r['id']} [{r['status']}] {r['title']}" for r in rows)


@register("ask_user", "Ask the user a clarifying question and record their approval or rejection.",
          {"question": "question to show the user"}, ["question"], category="opencode")
def ask_user(question: str) -> str:
    ok = safety.require_confirmation("THAMBI has a question", question[:3000], action="ask_user")
    return (f"Question shown to user: {question}\nUser response: " +
            ("approved/yes" if ok else "declined or chose another approach"))


def _web_search_items(query: str, limit: int = 5) -> list[dict[str, str]]:
    limit = min(10, max(1, int(limit)))
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (THAMBI local assistant)"},
            timeout=20,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Web search failed: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"Web search HTTP {r.status_code}")
    items: list[dict[str, str]] = []
    try:
        from lxml import html
        doc = html.fromstring(r.text)
        nodes = doc.xpath('//div[contains(@class, "result")]')
        seen: set[str] = set()
        for node in nodes:
            links = node.xpath('.//a[contains(@class, "result__a")]')
            snippets = node.xpath('.//a[contains(@class, "result__snippet")] | .//div[contains(@class, "result__snippet")]')
            if not links:
                continue
            title = " ".join(links[0].text_content().split())
            url = links[0].get("href", "")
            if url.startswith("//"):
                url = "https:" + url
            if "uddg=" in url:
                url = unquote(parse_qs(urlparse(url).query).get("uddg", [url])[0])
            try:
                url = safety.validate_http_url(url, "Result URL")
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            snippet = " ".join(snippets[0].text_content().split()) if snippets else ""
            items.append({"title": title, "url": url, "snippet": snippet[:500]})
            if len(items) >= limit:
                break
    except ImportError:
        return []
    return items


@register("web_search", "Search the web with DuckDuckGo HTML (requires external research approval).",
          {"query": "search query", "max_results": "limit (default 5)"}, ["query"],
          category="opencode")
def web_search(query: str, max_results: int | str = 5) -> str:
    if not safety.require_external("allow_external_research", "Web search",
                                   f"Search the web for: {query[:500]}"):
        raise PermissionError("Web search was denied by the user")
    items = _web_search_items(query, max_results)
    if not items:
        return f"Web results for '{query}':\n- No usable results returned. Try a broader query."
    out = [f"- {item['title']}\n  {item['url']}\n  {item['snippet']}" for item in items]
    return f"Web results for '{query}':\n" + "\n".join(out)


@register("web_fetch", "Fetch a URL and return readable text (requires external research approval).",
          {"url": "http(s) URL", "max_chars": "limit (default 12000)"}, ["url"], category="opencode")
def web_fetch(url: str, max_chars: int | str = 12000) -> str:
    url = _http_url(url)
    if not safety.require_external("allow_external_research", "Web fetch", f"Fetch URL: {url[:500]}"):
        raise PermissionError("Web fetch was denied by the user")
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (THAMBI local assistant)"})
    except requests.RequestException as e:
        raise RuntimeError(f"Web fetch failed: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"Web fetch HTTP {r.status_code}")
    text = r.text
    if "html" in r.headers.get("content-type", "").lower():
        try:
            from lxml import html
            doc = html.fromstring(text)
            for bad in doc.xpath("//script | //style | //noscript"):
                bad.drop_tree()
            text = doc.text_content()
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    limit = min(50000, max(1000, int(max_chars)))
    return f"Fetched {url}:\n{text[:limit]}"


@register("internet_status", "Check outbound internet connectivity and proxy presence (requires internet access approval).",
          {}, category="opencode")
def internet_status() -> str:
    if not safety.require_external("allow_external_research", "Check internet connectivity?",
                                   "A harmless request will be sent to DuckDuckGo. No local data is sent."):
        raise PermissionError("Internet access was denied by the user")
    started = time.perf_counter()
    try:
        r = requests.get("https://duckduckgo.com/", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0 (THAMBI local assistant)"})
    except requests.RequestException as e:
        return f"Internet: unreachable\nError: {type(e).__name__}: {e}"
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy")
    proxies = ", ".join(k for k in keys if os.environ.get(k)) or "none"
    return (f"Internet: online\nHTTP {r.status_code} from {r.url} in {elapsed_ms} ms\n"
            f"Proxy environment variables present: {proxies}")


@register("browser_open", "Open an http(s) URL in the default browser (requires internet access approval).",
          {"url": "http(s) URL to open"}, ["url"], category="opencode",
          dangerous=True, read_only=False)
def browser_open(url: str) -> str:
    url = _http_url(url, "Browser URL")
    if not safety.require_external("allow_external_research", "Open URL in browser?",
                                   f"The system default browser will open: {url[:500]}"):
        raise PermissionError("Browser access was denied by the user")
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as e:
        raise RuntimeError(f"Could not open browser: {e}") from e
    if not opened:
        raise RuntimeError("No browser could be opened on this system")
    return f"Opened in default browser: {url}"


@register("skill_list", "List THAMBI's built-in skills (modules) and slash commands.",
          {}, category="opencode")
def skill_list() -> str:
    from ..agent import HELP, MODULES
    lines = ["Modules: " + ", ".join(sorted(MODULES))]
    lines.extend(line for line in HELP.splitlines() if line.strip().startswith("/"))
    return "\n".join(lines)
