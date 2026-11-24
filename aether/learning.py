from __future__ import annotations

import re

from .config import get
from .memory import Memory, default as default_memory

_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"^\s*(?:please\s+)?remember\s+(?:that\s+)?(.+?)[.!]?\s*$", re.I), "remember", 0.85),
    (re.compile(r"^\s*from now on[, ]+(.+?)[.!]?\s*$", re.I), "from_now", 0.85),
    (re.compile(r"^\s*always\s+(.+?)[.!]?\s*$", re.I), "always", 0.8),
    (re.compile(r"^\s*never\s+(.+?)[.!]?\s*$", re.I), "never", 0.8),
    (re.compile(r"^\s*(?:don't|do not|stop)\s+(.+?)[.!]?\s*$", re.I), "avoid", 0.8),
    (re.compile(r"^\s*i (?:prefer|like|want|need|use)\s+(.+?)[.!]?\s*$", re.I), "prefer", 0.75),
    (re.compile(r"^\s*(?:my name is|call me)\s+(.+?)[.!]?\s*$", re.I), "identity", 0.9),
    (re.compile(r"^\s*we (?:use|prefer|follow)\s+(.+?)[.!]?\s*$", re.I), "project", 0.75),
]

_CORRECTION = re.compile(
    r"^\s*(?:no[, ]|wrong[, ]|incorrect[, ]|actually[, ]|you (?:should|must|were supposed to) have)\s*(.+)$",
    re.I,
)
_POSITIVE = re.compile(r"^\s*(?:thanks|thank you|perfect|great|good|exactly|correct|nice|well done)\b", re.I)
_NEGATIVE = re.compile(r"^\s*(?:wrong|incorrect|bad|not what i (?:asked|wanted)|you missed|stop doing that)\b", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .!,")[:500]


def _normalize(kind: str, body: str) -> str:
    body = _clean(body)
    if not body:
        return ""
    if kind == "always":
        return f"Always {body[0].lower() + body[1:]}"
    if kind == "never":
        return f"Never {body[0].lower() + body[1:]}"
    if kind == "avoid":
        return f"Do not {body[0].lower() + body[1:]}"
    if kind == "prefer":
        return f"User prefers {body[0].lower() + body[1:]}"
    if kind == "identity":
        return f"User's name is {body}."
    if kind == "project":
        return f"Project uses {body[0].lower() + body[1:]}"
    return body[0].upper() + body[1:]


def extract_rules(text: str) -> list[tuple[str, float, str]]:
    """Return (rule, confidence, source) triples inferred from a user message."""
    out: list[tuple[str, float, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or len(line) > 500:
            continue
        for pattern, kind, confidence in _PATTERNS:
            m = pattern.match(line)
            if m:
                rule = _normalize(kind, m.group(1))
                if rule:
                    out.append((rule, confidence, "chat"))
                break
        else:
            m = _CORRECTION.match(line)
            if m:
                rule = _clean(m.group(1))
                if rule:
                    out.append((f"Correction: {rule[0].upper() + rule[1:]}", 0.6, "correction"))
    deduped: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    for rule, confidence, source in out:
        key = rule.lower()
        if key not in seen:
            seen.add(key)
            deduped.append((rule, confidence, source))
    return deduped[:5]


def detect_feedback(text: str) -> str | None:
    line = _clean(text)
    if _POSITIVE.match(line):
        return "positive"
    if _NEGATIVE.match(line) or _CORRECTION.match(line):
        return "negative"
    return None


def observe(text: str, mem: Memory | None = None, module: str = "chat",
            session_id: str = "") -> list[dict]:
    """Learn durable preferences and feedback from a conversation turn."""
    if not get("learning.enabled", True) or not get("learning.auto_learn", True):
        return []
    mem = mem or default_memory()
    minimum = float(get("learning.min_confidence", 0.35))
    learned: list[dict] = []
    for rule, confidence, source in extract_rules(text):
        if confidence < minimum:
            continue
        rule_id, created = mem.upsert_learned_rule(rule, scope="global", source=source, confidence=confidence)
        learned.append({"id": rule_id, "rule": rule, "created": created, "source": source})
    if get("learning.feedback_enabled", True):
        signal = detect_feedback(text)
        if signal:
            mem.add_feedback(session_id, signal, _clean(text)[:300])
            recent = mem.top_learned_rules(module, limit=3, min_confidence=minimum)
            delta = 0.05 if signal == "positive" else -0.08
            for item in recent:
                mem.adjust_learned_rule(int(item["id"]), delta)
    return learned


def rules_block(mem: Memory | None = None, module: str = "chat") -> str:
    if not get("learning.enabled", True):
        return ""
    mem = mem or default_memory()
    limit = max(1, int(get("learning.max_rules", 12)))
    minimum = float(get("learning.min_confidence", 0.35))
    rules = mem.top_learned_rules(module, limit=limit, min_confidence=minimum)
    if not rules:
        return ""
    lines = [f"- [#{r['id']}] {r['rule']}" for r in rules]
    return ("ADAPTIVE LEARNING - rules learned from this user (apply them unless safety rules or the user's "
            "latest explicit request override them):\n" + "\n".join(lines) +
            "\nIf a rule is outdated, ask the user or follow their newest instruction.")


def teach(rule: str, mem: Memory | None = None, scope: str = "global",
          confidence: float = 0.9) -> tuple[int, bool]:
    mem = mem or default_memory()
    return mem.upsert_learned_rule(_clean(rule), scope=scope, source="manual", confidence=confidence)


def forget(query: str, mem: Memory | None = None) -> int:
    mem = mem or default_memory()
    target = query.strip()
    if target.isdigit():
        return 1 if mem.delete_learned_rule(int(target)) else 0
    needle = target.lower()
    removed = 0
    for item in mem.list_learned_rules(limit=500):
        if needle in item["rule"].lower():
            removed += int(mem.delete_learned_rule(int(item["id"])))
    return removed
