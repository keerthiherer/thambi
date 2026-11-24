from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import requests

from . import safety
from .config import get

MANUAL_VERSION = "1.0"


def build_manual(base_url: str | None = None, categories: list[str] | None = None) -> dict:
    from .tools import REGISTRY

    base = (base_url or _local_base()).rstrip("/")
    tools = []
    for tool in sorted(REGISTRY.values(), key=lambda t: t.name):
        if categories and tool.category not in categories:
            continue
        props = {name: {"type": "string", "description": desc} for name, desc in tool.args.items()}
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "inputs": {
                "type": "object",
                "properties": props,
                "required": list(tool.required),
            },
            "outputs": {"type": "string"},
            "tool_provider": {
                "name": "thambi-local",
                "provider_type": "http",
                "url": f"{base}/utcp/call",
                "http_method": "POST",
            },
        })
    return {
        "version": MANUAL_VERSION,
        "name": "thambi",
        "description": "THAMBI local tools exposed over UTCP (Universal Tool Calling Protocol).",
        "tools": tools,
    }


def call_local(name: str, args: dict[str, Any] | None = None,
               allow_dangerous: bool = True) -> dict:
    from .tools import execute

    return execute(name, args or {}, allow_dangerous=allow_dangerous)


def fetch_manual(url: str, timeout: float = 15.0, token: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "THAMBI-utcp/0.1"}
    if token:
        headers["X-UTCP-Token"] = token
    r = requests.get(url, timeout=timeout, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"UTCP manual HTTP {r.status_code} for {url}")
    data = r.json()
    if not isinstance(data, dict) or "tools" not in data:
        raise ValueError("Response is not a UTCP manual (missing 'tools')")
    return data


def call_remote(manual_url: str, tool_name: str, args: dict[str, Any] | None = None,
                token: str = "", timeout: float = 120.0,
                confirm: bool = True) -> dict:
    if confirm and not safety.require_external(
        "allow_external_research", "Call external UTCP tool?",
        f"Manual: {manual_url}\nTool: {tool_name}\nArgs: {json.dumps(args or {}, ensure_ascii=False)[:800]}\n\n"
        "Only the tool arguments are sent; no local files or credentials are included.",
    ):
        raise PermissionError("External UTCP call denied")
    manual = fetch_manual(manual_url, token=token)
    target = None
    for tool in manual.get("tools", []):
        if tool.get("name") == tool_name:
            target = tool
            break
    if target is None:
        names = ", ".join(t.get("name", "?") for t in manual.get("tools", []))
        raise LookupError(f"Tool '{tool_name}' not in manual. Available: {names}")
    provider = target.get("tool_provider") or {}
    if str(provider.get("provider_type", "")).lower() != "http":
        raise ValueError(f"Unsupported UTCP provider type: {provider.get('provider_type')}")
    endpoint = provider.get("url") or manual_url
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-UTCP-Token"] = token
    r = requests.post(endpoint, json={"name": tool_name, "args": args or {}},
                      timeout=timeout, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"UTCP call HTTP {r.status_code}: {r.text[:800]}")
    return r.json()


def _local_base() -> str:
    host = get("utcp.host", "127.0.0.1")
    port = int(get("utcp.port", 8191))
    return f"http://{host}:{port}"


def _auth_ok(headers: Any) -> bool:
    token = str(get("utcp.token", "") or "")
    if not token:
        return True
    return str(headers.get("X-UTCP-Token", "")) == token


class _UtcpHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, utcp: "UtcpServer"):
        super().__init__(address, handler)
        self.utcp = utcp


class _Handler(BaseHTTPRequestHandler):
    server_version = "THAMBI-UTCP/0.1"

    def log_message(self, fmt, *args):  # noqa: A003 - silence default console spam
        return

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/utcp", "/utcp/manual"):
            if not _auth_ok(self.headers):
                self._send(401, {"error": "invalid UTCP token"})
                return
            self._send(200, build_manual(self.server.utcp.url))
            return
        if path == "/health":
            self._send(200, {"status": "ok", "service": "thambi-utcp"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/utcp/call":
            self._send(404, {"error": "not found"})
            return
        if not _auth_ok(self.headers):
            self._send(401, {"error": "invalid UTCP token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4 * 1024 * 1024:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict) or "name" not in payload:
                raise ValueError("expected JSON {name, args}")
            result = call_local(str(payload["name"]), payload.get("args") or {},
                                allow_dangerous=True)
        except Exception as e:
            self._send(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        self._send(200, {"ok": bool(result.get("ok")), "tool": payload["name"],
                         "result": result.get("result"), "error": result.get("error")})


class UtcpServer:
    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or str(get("utcp.host", "127.0.0.1"))
        self.port = int(get("utcp.port", 8191) if port is None else port)
        self._httpd: _UtcpHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        self._httpd = _UtcpHTTPServer((self.host, self.port), _Handler, self)
        actual = int(self._httpd.server_address[1])
        self.url = f"http://{self.host}:{actual}"
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="thambi-utcp", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

    @property
    def manual_url(self) -> str:
        return f"{self.url}/utcp/manual"
