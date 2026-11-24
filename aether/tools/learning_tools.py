from __future__ import annotations

import json

from .. import learning
from ..config import get
from ..memory import default as default_memory
from .registry import register


@register("remember_rule", "Learn a durable user preference or instruction for future chats.",
          {"rule": "the rule to remember", "scope": "global or a module name (default global)"},
          ["rule"], category="learning", read_only=False)
def remember_rule(rule: str, scope: str = "global") -> str:
    rule_id, created = learning.teach(rule, scope=scope)
    return f"{'Learned' if created else 'Updated'} rule #{rule_id}: {rule}"


@register("forget_rule", "Remove a learned rule by id or matching text.",
          {"query": "rule id or text to match"}, ["query"], category="learning", read_only=False)
def forget_rule(query: str) -> str:
    count = learning.forget(query)
    return f"Removed {count} learned rule(s)."


@register("list_learned", "List rules THAMBI has learned about the user.",
          {"limit": "maximum rules (default 20)"}, category="learning")
def list_learned(limit: int | str = 20) -> str:
    rules = default_memory().list_learned_rules(limit=min(100, max(1, int(limit))))
    if not rules:
        return "No learned rules yet. Preferences stated in chat are learned automatically."
    lines = [f"[{r['id']}] ({r['scope']}, confidence {r['confidence']:.2f}, {r['source']}) {r['rule']}"
             for r in rules]
    return "\n".join(lines)


@register("record_feedback", "Record explicit satisfaction or dissatisfaction with the last answer.",
          {"signal": "positive|negative", "detail": "optional reason"}, ["signal"],
          category="learning", read_only=False)
def record_feedback(signal: str, detail: str = "") -> str:
    signal = signal.strip().lower()
    if signal not in ("positive", "negative"):
        raise ValueError("signal must be positive or negative")
    mem = default_memory()
    mem.add_feedback("", signal, detail)
    delta = 0.05 if signal == "positive" else -0.08
    for item in mem.top_learned_rules(limit=3):
        mem.adjust_learned_rule(int(item["id"]), delta)
    return f"Recorded {signal} feedback; learned-rule confidence adjusted."


@register("learning_status", "Show adaptive-learning configuration and learned-rule count.",
          {}, category="learning")
def learning_status() -> str:
    enabled = bool(get("learning.enabled", True))
    auto = bool(get("learning.auto_learn", True))
    count = len(default_memory().list_learned_rules(limit=500))
    return json.dumps({"enabled": enabled, "auto_learn": auto, "learned_rules": count}, indent=2)
