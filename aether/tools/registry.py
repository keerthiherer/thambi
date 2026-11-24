from __future__ import annotations

import json
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import safety


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    args: dict[str, str] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    category: str = "general"
    dangerous: bool = False
    read_only: bool = True


REGISTRY: dict[str, Tool] = {}


def register(name: str, description: str, args: dict[str, str] | None = None,
             required: list[str] | None = None, category: str = "general",
             dangerous: bool = False, read_only: bool = True):
    def deco(fn: Callable):
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            handler=fn,
            args=args or {},
            required=required or [],
            category=category,
            dangerous=dangerous,
            read_only=read_only,
        )
        return fn
    return deco


def _normalize_key(key: Any) -> str:
    return str(key).strip().strip(":").strip()


def _error(message: str, error_type: str = "ToolError") -> dict:
    return {"ok": False, "error_type": error_type, "message": message,
            "error": f"{error_type}: {message}" if error_type not in message else message}


def execute(name: str, args: dict[str, Any] | None = None, allow_dangerous: bool = False) -> dict:
    tool = REGISTRY.get(name)
    if not tool:
        return _error(f"Unknown tool '{name}'. Available: {', '.join(sorted(REGISTRY))}", "UnknownTool")
    args = {_normalize_key(k): v for k, v in (args or {}).items() if _normalize_key(k)}
    unknown = sorted(k for k in args if k not in tool.args)
    if unknown:
        return _error(f"Unknown argument(s) for {name}: {', '.join(unknown)}. "
                            f"Allowed arguments: {', '.join(sorted(tool.args)) or 'none'}", "UnknownArgument")
    missing = [r for r in tool.required if r not in args or args[r] is None]
    if missing:
        return _error(f"Missing required arguments: {', '.join(missing)}", "MissingArgument")
    clean = dict(args)
    if tool.dangerous and not allow_dangerous:
        safety.audit("tool_blocked", name, json.dumps(clean, ensure_ascii=False)[:500], "not_approved")
        return _error(f"{name} requires explicit confirmation through the UI.", "ConfirmationRequired")
    try:
        result = tool.handler(**clean)
        safety.audit("tool", name, json.dumps(clean, ensure_ascii=False)[:1000], "ok")
        return {"ok": True, "result": result}
    except Exception as e:
        safety.audit("tool", name, json.dumps(clean, ensure_ascii=False)[:1000], f"error: {e}")
        return {"ok": False, "error_type": type(e).__name__, "message": str(e),
                "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)}


def specs_text(categories: list[str] | None = None, compact: bool = False) -> str:
    lines = []
    for t in sorted(REGISTRY.values(), key=lambda x: (x.category, x.name)):
        if categories and t.category not in categories:
            continue
        req = ", ".join(t.required) or "none"
        mode = "read-only" if t.read_only and not t.dangerous else "needs confirmation"
        if compact:
            desc = t.description
            if len(desc) > 40:
                desc = desc[:37].rstrip() + "..."
            lines.append(f"- {t.name} ({mode}; req: {req}) - {desc}")
            continue
        a = ", ".join(f"{k}: {v}" for k, v in t.args.items())
        lines.append(f"- {t.name} ({t.category}; {mode}; required: {req}; args: {a}) - {t.description}")
    return "\n".join(lines)


_TOOL_BLOCK = re.compile(r"```tool\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_TOOL_TAG = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.DOTALL | re.IGNORECASE)

_PLACEHOLDER_VALUES = {
    "template path", "path_to_deck.pptx", "<path_to_deck>.pptx",
    "slide objects", "your company name", "company name",
}


def _iter_json_blocks(text: str, start_tag: str, end_tag: str):
    seen: set[str] = set()
    for m in re.finditer(re.escape(start_tag), text, re.IGNORECASE):
        i = m.end()
        while i < len(text) and text[i].isspace():
            i += 1
        raw = ""
        if i < len(text) and text[i] == "{":
            depth = 0
            in_string = False
            escaped = False
            for j in range(i, len(text)):
                ch = text[j]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw = text[i:j + 1]
                        break
        if not raw:
            tail = text[m.end():]
            end = re.search(re.escape(end_tag), tail, re.IGNORECASE)
            if end:
                raw = tail[:end.start()].strip()
        key = " ".join(raw.split())
        if raw.startswith("{") and key not in seen:
            seen.add(key)
            yield raw


def _unescape_text(raw: str) -> str:
    out: list[str] = []
    i = 0
    mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
               '"': '"', "\\": "\\", "/": "/"}
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt == "u" and i + 6 <= len(raw):
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        out.append(ch)
        i += 1
    return "".join(out)


def _scan_string(text: str, start: int) -> tuple[str, int]:
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            rest = text[i + 1:].lstrip()
            if not rest or rest[0] in ",}]":
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], len(text)


def _scan_balanced(text: str, start: int) -> tuple[str, int]:
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            _, i = _scan_string(text, i)
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
        i += 1
    return text[start:], len(text)


def _scan_spec_string(text: str, start: int) -> tuple[str, int]:
    boundary = re.search(
        r',\s*"(?:theme|title|subtitle|author|overwrite|slides|points|path)"\s*:',
        text[start:], re.IGNORECASE,
    )
    end = start + boundary.start() if boundary else text.rfind("}")
    if end <= start:
        end = len(text)
    quote = text.rfind('"', start + 1, end)
    if quote <= start:
        quote = end
    return _unescape_text(text[start + 1:quote]), quote + 1


def _loose_array(raw: str) -> list:
    text = raw.strip()
    if not text.startswith("["):
        return []
    inner = text[1:text.rfind("]")] if "]" in text else text[1:]
    items: list[Any] = []
    i = 0
    while i < len(inner):
        if inner[i] in ", \t\r\n":
            i += 1
            continue
        if inner[i] in "{[":
            value_raw, i = _scan_balanced(inner, i)
            try:
                items.append(json.loads(_repair_json(value_raw)))
            except (json.JSONDecodeError, ValueError):
                items.append(_loose_object(value_raw) if value_raw.startswith("{") else _loose_array(value_raw))
        elif inner[i] == '"':
            value, i = _scan_string(inner, i)
            items.append(_unescape_text(value))
        else:
            end = i
            while end < len(inner) and inner[end] != ",":
                end += 1
            token = inner[i:end].strip()
            items.append(True if token.lower() == "true" else False if token.lower() == "false" else token)
            i = end
    return items


def _loose_object(raw: str) -> dict:
    text = raw.strip()
    result: dict[str, Any] = {}
    key_pattern = re.compile(
        r'"([A-Za-z_][A-Za-z0-9_.-]*)"\s*:\s*|(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*'
    )
    for m in key_pattern.finditer(text):
        key = m.group(1) or m.group(2)
        i = m.end()
        if i >= len(text):
            break
        ch = text[i]
        if key == "args" and ch == "{":
            close = text.rfind("}")
            inner = text[i + 1:close if close > i else len(text)]
            result[key] = _loose_object(inner)
            i = close + 1 if close > i else len(text)
        elif key == "spec" and ch == '"':
            value, i = _scan_spec_string(text, i)
            result[key] = value
        elif ch == '"':
            value, i = _scan_string(text, i)
            result[key] = _unescape_text(value)
        elif ch in "{[":
            value_raw, end = _scan_balanced(text, i)
            try:
                result[key] = json.loads(_repair_json(value_raw))
            except (json.JSONDecodeError, ValueError):
                result[key] = _loose_object(value_raw) if ch == "{" else _loose_array(value_raw)
            i = end
        else:
            end = i
            while end < len(text) and text[end] not in ",}]":
                end += 1
            value = text[i:end].strip().strip('"')
            result[key] = True if value.lower() == "true" else False if value.lower() == "false" else value
            i = end
    return result


def _repair_json(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r'"([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*"\s*:', r'"\1":', text)
    text = re.sub(r'"([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*"', r'"\1": "', text)
    text = re.sub(r'(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*:', r' "\1":', text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _PLACEHOLDER_VALUES or re.fullmatch(r"<[^>]+>", text):
            return ""
        if re.fullmatch(r"\[[A-Za-z_ ]+\]", text):
            return []
        return value
    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_value(v) for v in value]
    return value


def _parse_block(raw: str) -> dict:
    loose = False
    try:
        obj = json.loads(_repair_json(raw))
    except (json.JSONDecodeError, ValueError):
        loose = True
        obj = _loose_object(raw)
    if loose and re.search(r":\s*[,}]", _repair_json(raw)):
        raise ValueError("Malformed tool JSON: empty value")
    if not isinstance(obj, dict):
        raise ValueError("tool call must be a JSON object")
    args = obj.get("args")
    if isinstance(args, dict) and isinstance(args.get("spec"), str):
        spec = args["spec"].strip()
        try:
            args["spec"] = json.loads(spec)
        except json.JSONDecodeError:
            try:
                args["spec"] = json.loads(re.sub(r"\s+", " ", spec))
            except json.JSONDecodeError:
                if spec.lstrip().startswith(("{", "[")):
                    recovered = _loose_object(spec) if spec.lstrip().startswith("{") else _loose_array(spec)
                    if recovered:
                        args["spec"] = recovered
    return _clean_value(obj)


def parse_tool_calls(text: str) -> list[dict]:
    calls: list[dict] = []
    blocks = (list(_iter_json_blocks(text, "```tool", "```")) +
              list(_iter_json_blocks(text, "<tool>", "</tool>")))
    for raw in blocks:
        try:
            obj = _parse_block(raw)
            if "name" in obj and isinstance(obj.get("args", {}), dict):
                calls.append({"name": str(obj["name"]), "args": obj.get("args") or {}})
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return calls


def tool_parse_errors(text: str) -> list[str]:
    errors: list[str] = []
    blocks = (list(_iter_json_blocks(text, "```tool", "```")) +
              list(_iter_json_blocks(text, "<tool>", "</tool>")))
    for raw in blocks:
        try:
            obj = _parse_block(raw)
        except json.JSONDecodeError as e:
            errors.append(f"Malformed tool JSON: {e.msg} at line {e.lineno} column {e.colno}. Block: {raw[:500]}")
            continue
        except ValueError as e:
            errors.append(f"Malformed tool call: {e}. Block: {raw[:500]}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"Tool call must be a JSON object. Block: {raw[:500]}")
        elif "name" not in obj:
            errors.append(f"Tool call is missing 'name'. Block: {raw[:500]}")
        elif "args" in obj and not isinstance(obj["args"], dict):
            errors.append(f"Tool call 'args' must be a JSON object. Block: {raw[:500]}")
    return errors


# Import modules so their decorators run.
from . import devtools, files, coding, desktop, office, ece, kicad, ceo, research, utcp_tools, learning_tools, opencode_tools, documents, designer, psychology, innovation  # noqa: E402,F401
