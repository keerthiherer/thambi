from __future__ import annotations

import json

from .. import safety
from ..config import get
from .. import utcp
from .registry import register


@register("utcp_manual", "Return this THAMBI instance's UTCP manual so other agents can discover its tools.",
          {}, category="research")
def utcp_manual() -> str:
    return json.dumps(utcp.build_manual(), indent=2)


@register("utcp_list", "List tools exposed by an external UTCP manual URL. Requires external access approval.",
          {"url": "UTCP manual URL"}, ["url"], category="research",
          dangerous=True, read_only=False)
def utcp_list(url: str) -> str:
    if not safety.require_external(
        "allow_external_research", "Read external UTCP manual?",
        f"{url}\n\nOnly the manual JSON is downloaded. No local data is sent.",
    ):
        raise PermissionError("External UTCP manual denied")
    manual = utcp.fetch_manual(url, token=str(get("utcp.token", "") or ""))
    rows = []
    for t in manual.get("tools", []):
        rows.append(f"- {t.get('name')}: {t.get('description', '')}")
    return (f"UTCP manual {url} ({len(rows)} tools)\n" + "\n".join(rows)) if rows else "Manual has no tools."


@register("utcp_call", "Call a tool on an external UTCP provider. Requires external access approval and confirmation.",
          {"url": "UTCP manual URL", "tool": "tool name", "args": "JSON arguments"},
          ["url", "tool"], category="research", dangerous=True, read_only=False)
def utcp_call(url: str, tool: str, args: str | dict = "") -> str:
    if isinstance(args, str):
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            raise ValueError(f"args must be JSON: {e}") from e
    else:
        parsed = args or {}
    if not isinstance(parsed, dict):
        raise ValueError("args must be a JSON object")
    result = utcp.call_remote(url, tool, parsed, token=str(get("utcp.token", "") or ""))
    return json.dumps(result, indent=2, ensure_ascii=False)
