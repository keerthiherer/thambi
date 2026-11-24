from __future__ import annotations

import re
from typing import Any

from .registry import register

_GUARDRAIL = (
    "This is a reflective, non-clinical aid. It is not a diagnosis, a validated personality test, "
    "or a substitute for a qualified professional. Treat every profile as a tentative hypothesis "
    "based only on the text supplied. Do not use it to manipulate, label, or make automated "
    "employment or healthcare decisions."
)

_EMOTIONS: dict[str, tuple[str, ...]] = {
    "anxiety / worry": ("worried", "anxious", "nervous", "scared", "afraid", "uncertain", "panic", "stressed", "tension"),
    "anger / frustration": ("angry", "frustrated", "annoyed", "irritated", "upset", "furious", "unfair", "fed up"),
    "sadness / disappointment": ("sad", "down", "disappointed", "hurt", "lonely", "grief", "miss", "cry"),
    "hope / optimism": ("hope", "excited", "optimistic", "looking forward", "can't wait", "confident", "proud"),
    "guilt / shame": ("guilty", "ashamed", "sorry", "regret", "fault", "embarrassed"),
    "curiosity / interest": ("interesting", "curious", "wonder", "why", "how", "explore", "learn"),
    "gratitude / warmth": ("thank", "grateful", "appreciate", "care", "support", "kind"),
    "overload / fatigue": ("tired", "exhausted", "overwhelmed", "burned out", "drained", "too much", "no energy"),
}

_INTENSITY_WORDS = ("very", "extremely", "really", "so", "deeply", "incredibly", "constantly", "always", "never")
_STRESS_WORDS = ("deadline", "pressure", "stressed", "overwhelmed", "conflict", "late", "failed", "risk", "urgent")
_ACHIEVEMENT_WORDS = ("target", "goal", "result", "achieve", "improve", "growth", "win", "performance", "metric")
_AFFILIATION_WORDS = ("team", "we", "together", "help", "support", "relationship", "trust", "belong")
_ANALYTICAL_WORDS = ("why", "how", "data", "evidence", "analyse", "analyze", "compare", "reason", "logic", "fact")
_STRUCTURE_WORDS = ("process", "rule", "plan", "detail", "correct", "standard", "checklist", "deadline", "order")
_NOVELTY_WORDS = ("new", "idea", "creative", "change", "innovate", "experiment", "different", "future")
_BOUNDARY_WORDS = ("no", "boundary", "space", "need", "must", "cannot", "can't", "stop", "independent")
_RISK_PATTERNS = re.compile(
    r"\b(suicide|self[- ]?harm|kill myself|end my life|hurt myself|no reason to live|"
    r"don'?t want to live|do not want to live|don'?t want to be here|can'?t go on|"
    r"hopeless and alone|want to die)\b", re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    return [p.strip() for p in parts if p.strip()]


def _lower(text: Any) -> str:
    return str(text or "").lower()


def _matches(text: str, terms: tuple[str, ...]) -> list[str]:
    low = _lower(text)
    return [term for term in terms if term in low]


def _confidence(count: int) -> str:
    if count >= 3:
        return "medium"
    if count >= 1:
        return "low to medium"
    return "low"


def _bullet_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- Not enough evidence in the supplied text."


def _emotion_scores(text: str) -> list[tuple[str, int, list[str]]]:
    scores: list[tuple[str, int, list[str]]] = []
    for label, terms in _EMOTIONS.items():
        hits = _matches(text, terms)
        if hits:
            scores.append((label, len(hits), hits))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def _signal_summary(text: str) -> list[str]:
    low = _lower(text)
    signals: list[str] = []
    if "!" in str(text):
        signals.append("Exclamation marks suggest heightened energy.")
    if len(re.findall(r"\?\s*$", str(text), re.MULTILINE)) >= 2:
        signals.append("Repeated questions suggest uncertainty or a search for clarity.")
    if len(re.findall(r"\b[A-Z]{3,}\b", str(text))) >= 2:
        signals.append("Capitalised words suggest emphasis or strong feeling.")
    if len(_matches(text, _INTENSITY_WORDS)) >= 2:
        signals.append("Intensifiers suggest a stronger emotional state.")
    if " i " in f" {low} " or low.startswith("i "):
        signals.append("First-person wording suggests personal experience is being shared.")
    if "we" in low or "us" in low:
        signals.append("Group wording suggests a relational or team context.")
    return signals


def _needs_for(labels: list[str]) -> list[str]:
    needs: list[str] = []
    mapping = {
        "anxiety / worry": "clarity, safety, and predictable next steps",
        "anger / frustration": "fairness, acknowledgement, and agency",
        "sadness / disappointment": "support, patience, and validation",
        "hope / optimism": "specific goals and momentum",
        "guilt / shame": "compassion, perspective, and repair options",
        "overload / fatigue": "rest, reduced load, and prioritisation",
        "gratitude / warmth": "connection and continued support",
        "curiosity / interest": "space to explore and learn",
    }
    for label in labels:
        need = mapping.get(label)
        if need and need not in needs:
            needs.append(need)
    return needs or ["clarity about what happened and what matters next"]


def _risk_note(text: str) -> str:
    if not _RISK_PATTERNS.search(str(text or "")):
        return ""
    return ("SAFETY: The text contains possible self-harm or crisis language. If there is immediate danger, "
            "contact local emergency services or a trusted person now. I cannot provide crisis care; "
            "encourage professional support and stay with the person if you can.")


@register("emotion_analyze", "Reflect likely emotions, intensity, cues, and supportive needs from written text. Non-clinical.",
          {"text": "the message or note to reflect on", "context": "optional situation or relationship context"},
          ["text"], category="psychology")
def emotion_analyze(text: str, context: str = "") -> str:
    scores = _emotion_scores(text)
    labels = [label for label, _, _ in scores]
    intensity_hits = len(_matches(text, _INTENSITY_WORDS)) + (1 if "!" in str(text) else 0)
    intensity = "high" if intensity_hits >= 3 else "medium" if intensity_hits >= 1 else "low to medium"
    evidence = [f"{label}: {', '.join(hits)}" for label, _, hits in scores[:5]]
    lines = [
        "EMOTION REFLECTION",
        f"Context: {str(context).strip() or 'not supplied'}",
        f"Likely emotions: {', '.join(labels[:4]) if labels else 'unclear from the text'}",
        f"Confidence: {_confidence(sum(n for _, n, _ in scores))}",
        f"Estimated intensity: {intensity}",
        "",
        "Observed cues:",
        _bullet_lines(_signal_summary(text) + evidence),
        "",
        "Likely needs:",
        _bullet_lines(_needs_for(labels[:3])),
        "",
        "Supportive response moves:",
        _bullet_lines([
            "Name the emotion gently: 'It sounds like this has been heavy.'",
            "Ask one open question: 'What part is hardest right now?'",
            "Offer one concrete next step instead of giving a long lecture.",
        ]),
        "",
        _GUARDRAIL,
    ]
    risk = _risk_note(text)
    if risk:
        lines.append("")
        lines.append(risk)
    return "\n".join(lines)


@register("psychology_profile", "Build a tentative, evidence-based communication and motivation profile from notes. Non-clinical.",
          {"notes": "observations, messages, or notes about the person",
           "purpose": "optional purpose such as coaching, communication, or teamwork"},
          ["notes"], category="psychology")
def psychology_profile(notes: str, purpose: str = "communication") -> str:
    dimensions = [
        ("Achievement orientation", _ACHIEVEMENT_WORDS, "focus on goals, results, and measurable progress"),
        ("Affiliation and teamwork", _AFFILIATION_WORDS, "focus on relationships, trust, and shared work"),
        ("Analytical style", _ANALYTICAL_WORDS, "focus on evidence, causes, and structured reasoning"),
        ("Structure and reliability", _STRUCTURE_WORDS, "focus on plans, rules, detail, and follow-through"),
        ("Novelty and change", _NOVELTY_WORDS, "focus on new ideas, experimentation, and change"),
        ("Autonomy and boundaries", _BOUNDARY_WORDS, "focus on independence, limits, and control"),
    ]
    ranked: list[tuple[str, int, str, list[str]]] = []
    for name, terms, description in dimensions:
        hits = _matches(notes, terms)
        if hits:
            ranked.append((name, len(hits), description, hits))
    ranked.sort(key=lambda item: item[1], reverse=True)
    top = ranked[:4]
    evidence = [f"{name}: {', '.join(hits)}" for name, _, _, hits in top]
    stress_hits = _matches(notes, _STRESS_WORDS)
    lines = [
        "TENTATIVE PSYCHOLOGY PROFILE",
        f"Purpose: {str(purpose).strip() or 'communication'}",
        f"Evidence strength: {_confidence(sum(n for _, n, _, _ in ranked))}",
        "",
        "Likely working style:",
        _bullet_lines([f"{name} - {desc} (evidence: {', '.join(hits)})" for name, _, desc, hits in top]),
        "",
        "Possible stress signals:",
        _bullet_lines([f"Observed: {', '.join(stress_hits)}"] if stress_hits else []),
        "",
        "Suggested support approach:",
        _bullet_lines([
            f"Lead with {top[0][0].lower()} when speaking with this person." if top else "Ask about preferences before adapting your approach.",
            "State expectations, decision rights, and timelines explicitly.",
            "Ask for confirmation: 'Does that match how you like to work?'",
        ]),
        "",
        "Observed evidence:",
        _bullet_lines(evidence),
        "",
        _GUARDRAIL,
    ]
    risk = _risk_note(notes)
    if risk:
        lines.extend(["", risk])
    return "\n".join(lines)


@register("behavior_profile", "Profile behavior patterns using an antecedent-behavior-consequence framework. Non-clinical.",
          {"notes": "specific observed behaviors and situations", "timeframe": "optional period such as last 30 days"},
          ["notes"], category="psychology")
def behavior_profile(notes: str, timeframe: str = "") -> str:
    sentences = _sentences(notes)
    triggers = [s for s in sentences if any(t in _lower(s) for t in ("when", "after", "before", "during", "because"))]
    actions = [s for s in sentences if any(t in _lower(s) for t in ("said", "did", "sent", "missed", "completed", "avoided", "asked", "helped", "replied"))]
    consequences = [s for s in sentences if any(t in _lower(s) for t in ("result", "led to", "impact", "affected", "because", "so ", "then"))]
    lines = [
        "BEHAVIOR PATTERN REFLECTION",
        f"Timeframe: {str(timeframe).strip() or 'not supplied'}",
        "",
        "Antecedents - what happens before:",
        _bullet_lines(triggers[:5]),
        "",
        "Behaviors - what was observed:",
        _bullet_lines(actions[:5]),
        "",
        "Consequences - what happens after:",
        _bullet_lines(consequences[:5]),
        "",
        "Working hypothesis:",
        _bullet_lines([
            "Look for repeated trigger -> action -> outcome loops before assigning motives.",
            "Separate observable behavior from interpretation of intent.",
            "Choose one small environmental change and test it for two weeks.",
        ]),
        "",
        "Change levers:",
        _bullet_lines([
            "Make expectations explicit and reduce ambiguity.",
            "Replace vague feedback with specific, timely observations.",
            "Agree on one measurable behavior and one support action.",
        ]),
        "",
        _GUARDRAIL,
    ]
    return "\n".join(lines)


@register("coaching_plan", "Create a practical GROW coaching plan for a goal, obstacle, or development need.",
          {"goal": "what the person wants to achieve", "obstacles": "optional current obstacles",
           "timeframe": "optional timeframe such as 30 days"},
          ["goal"], category="psychology")
def coaching_plan(goal: str, obstacles: str = "", timeframe: str = "30 days") -> str:
    return "\n".join([
        "COACHING PLAN - GROW",
        f"Goal: {str(goal).strip()}",
        f"Timeframe: {str(timeframe).strip() or '30 days'}",
        "",
        "Reality:",
        _bullet_lines([
            f"Current obstacles: {str(obstacles).strip() or 'not supplied'}",
            "Describe what has already been tried and what evidence exists.",
        ]),
        "",
        "Options:",
        _bullet_lines([
            "Choose the smallest action that produces visible progress.",
            "Remove one friction point instead of adding more motivation.",
            "Ask for one specific form of help or accountability.",
        ]),
        "",
        "Will - next steps:",
        _bullet_lines([
            "Week 1: define the target behaviour and baseline.",
            "Week 2: run one experiment and record what changed.",
            "Week 3: review evidence and adjust the plan.",
            "Week 4: decide what to keep, stop, or scale.",
        ]),
        "",
        "Reflection questions:",
        _bullet_lines([
            "What would good look like in concrete, observable terms?",
            "What is within your control this week?",
            "How will you know the plan is working?",
        ]),
        "",
        _GUARDRAIL,
    ])


@register("wellbeing_checkin", "Write a supportive, non-clinical wellbeing check-in with clear escalation guidance.",
          {"message": "what the person said", "style": "gentle|direct|practical"},
          ["message"], category="psychology")
def wellbeing_checkin(message: str, style: str = "gentle") -> str:
    scores = _emotion_scores(message)
    labels = [label for label, _, _ in scores[:3]]
    tone = str(style).strip().lower() or "gentle"
    opening = {
        "gentle": "That sounds difficult, and it makes sense that you would notice it.",
        "direct": "Thank you for saying that clearly. Let's look at what is happening and what helps.",
        "practical": "Thanks for sharing. Let's turn this into one calm, doable next step.",
    }.get(tone, "Thank you for sharing this with me.")
    lines = [
        "WELLBEING CHECK-IN",
        opening,
        "",
        "What I hear:",
        _bullet_lines([f"Possible feelings: {', '.join(labels)}"] if labels else ["The message is brief; more context would help."]),
        "",
        "Questions you can ask:",
        _bullet_lines([
            "How intense is this from 0 to 10 right now?",
            "What has helped even a little in the past?",
            "Would listening, distraction, or practical help feel best today?",
        ]),
        "",
        "Small grounding steps:",
        _bullet_lines([
            "Slow the exhale for six breaths and notice five things you can see.",
            "Drink water, unclench your jaw, and step away from the screen for two minutes.",
            "Choose one tiny action you can finish in ten minutes.",
        ]),
        "",
        "Support:",
        _bullet_lines([
            "Encourage contact with a trusted friend, family member, counsellor, or clinician.",
            "If this is work-related, suggest a manager or HR conversation focused on workload and support.",
        ]),
        "",
        _GUARDRAIL,
    ]
    risk = _risk_note(message)
    if risk:
        lines.extend(["", risk])
    return "\n".join(lines)


@register("empathy_reply", "Draft a warm, non-judgemental reply to an emotional message.",
          {"message": "the message to respond to", "relationship": "optional, e.g. colleague, friend, manager",
           "tone": "gentle|direct|practical"},
          ["message"], category="psychology")
def empathy_reply(message: str, relationship: str = "", tone: str = "gentle") -> str:
    scores = _emotion_scores(message)
    feeling = scores[0][0] if scores else "this"
    who = str(relationship).strip() or "there"
    style = str(tone).strip().lower() or "gentle"
    opener = {
        "gentle": f"Hi {who}, thank you for telling me this.",
        "direct": f"{who}, I appreciate you being direct with me.",
        "practical": f"Thanks {who} - let's make this concrete.",
    }.get(style, f"Hi {who}, thank you for telling me this.")
    draft = (
        f"{opener}\n\n"
        f"It sounds like there may be {feeling} here, and I do not want to minimise it. "
        "I am interested in understanding what has been hardest about it.\n\n"
        "What part feels most important for me to understand right now? If it would help, I can listen, "
        "help you think through options, or take one practical task off your plate.\n\n"
        "Thank you for trusting me with it."
    )
    risk = _risk_note(message)
    return "EMPATHY REPLY DRAFT\n\n" + draft + ("\n\n" + risk if risk else "")


@register("conflict_mediation", "Structure a conflict into neutral summaries, interests, options, and an agreement draft.",
          {"issue": "short neutral description of the conflict",
           "party_a_notes": "what party A says they need or observed",
           "party_b_notes": "what party B says they need or observed"},
          ["issue"], category="psychology")
def conflict_mediation(issue: str, party_a_notes: str = "", party_b_notes: str = "") -> str:
    return "\n".join([
        "CONFLICT MEDIATION FRAME",
        f"Issue: {str(issue).strip()}",
        "",
        "Neutral summary:",
        _bullet_lines([
            f"Party A: {str(party_a_notes).strip() or 'not supplied'}",
            f"Party B: {str(party_b_notes).strip() or 'not supplied'}",
            "Focus on observable behaviour and impact, not character judgements.",
        ]),
        "",
        "Interests behind positions:",
        _bullet_lines([
            "Ask each person what they need, fear losing, and can accept.",
            "Separate the desired outcome from the proposed solution.",
            "Name shared goals before debating solutions.",
        ]),
        "",
        "Questions to ask:",
        _bullet_lines([
            "What do you need the other person to understand?",
            "What is one thing you might have contributed to the pattern?",
            "What would a fair next step look like for the next two weeks?",
        ]),
        "",
        "Options:",
        _bullet_lines([
            "Agree on one specific behaviour change for each person.",
            "Set a short review date and a clear escalation path.",
            "Document decisions, owners, and deadlines.",
        ]),
        "",
        "Draft agreement:",
        _bullet_lines([
            "We agree the issue is: ...",
            "Party A will: ... by ...",
            "Party B will: ... by ...",
            "We will review on: ...",
        ]),
        "",
        _GUARDRAIL,
    ])


def _competency_list(raw: str) -> list[str]:
    items = [part.strip() for part in re.split(r"[,;\n]+", str(raw or "")) if part.strip()]
    return items[:8] or ["Ownership", "Problem solving", "Collaboration", "Communication", "Learning agility"]


@register("hr_interview_kit", "Create a structured, bias-aware interview kit with STAR questions and an evidence rubric.",
          {"role": "role title", "level": "optional level such as junior or senior",
           "competencies": "optional comma-separated competencies", "count": "questions per competency (default 2)"},
          ["role"], category="hr")
def hr_interview_kit(role: str, level: str = "", competencies: str = "", count: int | str = 2) -> str:
    try:
        per = min(3, max(1, int(count)))
    except (TypeError, ValueError):
        per = 2
    comps = _competency_list(competencies)
    lines = [
        "STRUCTURED INTERVIEW KIT",
        f"Role: {str(role).strip()}",
        f"Level: {str(level).strip() or 'not specified'}",
        f"Competencies: {', '.join(comps)}",
        "",
    ]
    for comp in comps:
        lines.append(f"{comp}:")
        for n in range(1, per + 1):
            lines.append(f"{n}. Tell me about a time you demonstrated {comp.lower()} in a situation similar to this role. "
                         "What was the situation, what did you do, and what was the result?")
            lines.append(f"   Follow-up: What evidence shows your action made the difference? (STAR probe {n})")
        lines.append("")
    lines.extend([
        "Scoring rubric - use evidence only:",
        _bullet_lines([
            "1 = no relevant example or unclear personal contribution.",
            "2 = generic answer with limited evidence.",
            "3 = specific example with clear action and plausible result.",
            "4 = specific example, measurable result, and reflection on tradeoffs.",
            "5 = repeated pattern, strong evidence, and learning that transfers to this role.",
        ]),
        "",
        "Bias controls:",
        _bullet_lines([
            "Ask the same core questions in the same order for every candidate.",
            "Score before discussing other candidates or external opinions.",
            "Ignore protected or irrelevant traits such as age, accent, family status, or school prestige.",
            "Write down evidence for each score; do not rely on gut feeling alone.",
        ]),
        "",
        "Decision note:",
        "This kit supports structured evidence collection. It does not make hiring decisions and should not replace "
        "human review, local law, or organisational policy.",
    ])
    return "\n".join(lines)


@register("hr_performance_review", "Draft a fair, evidence-based performance review from notes without inventing metrics.",
          {"notes": "manager, peer, or self-review notes", "role": "optional role title",
           "period": "optional review period"},
          ["notes"], category="hr")
def hr_performance_review(notes: str, role: str = "", period: str = "") -> str:
    sentences = _sentences(notes)
    achievements = [s for s in sentences if any(t in _lower(s) for t in ("achieved", "completed", "delivered", "improved", "led", "built", "launched"))]
    strengths = [s for s in sentences if any(t in _lower(s) for t in ("team", "help", "support", "quality", "reliable", "focused", "solve"))]
    development = [s for s in sentences if any(t in _lower(s) for t in ("missed", "late", "needs", "improve", "develop", "slow", "feedback", "error"))]
    return "\n".join([
        "PERFORMANCE REVIEW DRAFT",
        f"Role: {str(role).strip() or 'not specified'}",
        f"Period: {str(period).strip() or 'not specified'}",
        "",
        "Evidence of impact:",
        _bullet_lines(achievements[:6]),
        "",
        "Observed strengths:",
        _bullet_lines(strengths[:6]),
        "",
        "Development areas:",
        _bullet_lines(development[:6]),
        "",
        "SMART goals for next period:",
        _bullet_lines([
            "Specific: name the behaviour or outcome to change.",
            "Measurable: define what evidence will show progress.",
            "Achievable: agree on support and remove one blocker.",
            "Relevant: connect the goal to the role and team outcome.",
            "Time-bound: set a review date and interim checkpoint.",
        ]),
        "",
        "Support plan:",
        _bullet_lines([
            "Agree one manager action and one employee action.",
            "Set a two-week checkpoint before the formal review.",
            "Record evidence and avoid trait-based labels.",
        ]),
        "",
        "Fairness check:",
        "Base every statement on the supplied notes. If evidence is missing, write 'not enough evidence' rather than "
        "guessing. This draft does not make rating, pay, promotion, or termination decisions.",
    ])


@register("hr_feedback_message", "Draft respectful Situation-Behavior-Impact feedback for a workplace conversation.",
          {"recipient": "optional name or role", "situation": "when and where it happened",
           "behavior": "specific observable behaviour", "impact": "observed effect on people or work",
           "next_step": "optional desired next step", "tone": "direct|supportive|coaching"},
          ["situation", "behavior", "impact"], category="hr")
def hr_feedback_message(situation: str, behavior: str, impact: str, recipient: str = "",
                        next_step: str = "", tone: str = "direct") -> str:
    who = str(recipient).strip() or "there"
    style = str(tone).strip().lower()
    st = {
        "direct": f"{who}, I want to give you clear feedback about something specific.",
        "supportive": f"{who}, I want to share an observation because I value our work together.",
        "coaching": f"{who}, I want to pause on one moment and think with you about a better pattern.",
    }.get(style, f"{who}, I want to give you clear feedback about something specific.")
    draft = (
        f"{st}\n\n"
        f"Situation: {str(situation).strip()}\n"
        f"Behavior I observed: {str(behavior).strip()}\n"
        f"Impact: {str(impact).strip()}\n\n"
        f"Next step: {str(next_step).strip() or 'What do you think would help next time?'}\n\n"
        "I am describing the behaviour and its effect, not judging your character. "
        "What is your view of what happened?"
    )
    return "FEEDBACK DRAFT - SBI\n\n" + draft
