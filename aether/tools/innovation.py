from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .. import safety
from ..config import ensure_dirs
from ..memory import default as default_memory
from ..retrieval import chunk_text
from .opencode_tools import _web_search_items
from .registry import register
from .research import _diverse_sources, _fetch_research_text, _parse_sources, _source_domain

_STOPWORDS = {"about", "after", "against", "based", "best", "from", "have", "into", "more", "most",
              "should", "than", "that", "their", "them", "there", "these", "this", "those", "through",
              "using", "what", "when", "where", "which", "while", "with", "would", "your"}
_DISCLAIMER = "This is a technical landscape scan, not legal advice, a patentability opinion, or freedom-to-operate clearance."

_FRAMEWORKS = {
    "scamper": [
        ("Substitute", "Replace one component, material, channel, or assumption with a lower-cost alternative."),
        ("Combine", "Merge two existing functions or audiences into one product or workflow."),
        ("Adapt", "Borrow a mechanism from another domain and translate it to this challenge."),
        ("Modify", "Change scale, timing, format, or interaction to remove the main friction."),
        ("Put to other use", "Find a new user or context for the existing solution."),
        ("Eliminate", "Remove a step, dependency, or cost that users do not value."),
        ("Reverse", "Invert the sequence, ownership, pricing, or control model."),
    ],
    "triz": [
        ("Segmentation", "Break the system into independent modules that can improve separately."),
        ("Extraction", "Pull the most valuable function out into a focused standalone offer."),
        ("Local quality", "Make each component adapt to its own local conditions."),
        ("Asymmetry", "Change the shape, allocation, or rules to favour the useful side."),
        ("Consolidation", "Combine similar steps to reduce handoffs and delay."),
        ("Dynamics", "Make the solution adapt over time instead of staying fixed."),
        ("Feedback", "Add a short feedback loop that converts use into improvement."),
        ("Self-service", "Let users perform the key step without an intermediary."),
        ("Preliminary action", "Move detection or preparation before the failure point."),
        ("Nesting", "Place one useful component inside another to reduce total parts."),
    ],
    "jtbd": [
        ("Functional job", "Define the concrete task the user is hiring the solution to complete."),
        ("Social job", "Define how the solution helps the user look competent or trusted."),
        ("Emotional job", "Define the fear, frustration, or confidence the solution changes."),
        ("Struggle reduction", "Remove the biggest delay, risk, or effort in the current workaround."),
        ("Desired outcome", "State the measurable outcome the user wants instead of features."),
    ],
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _flag(value: bool | str) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
                parsed = decoded if isinstance(decoded, list) else [text]
            except json.JSONDecodeError:
                parsed = [text]
        else:
            parsed = re.split(r"[,\n]", text)
    return [str(item).strip() for item in parsed if str(item).strip()]


def _tokens(value: object) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) >= 3 and word not in _STOPWORDS}


def _overlap(left: object, right: object) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / max(1, len(a))


def _search_pool(angles: list[str], per_angle: int) -> tuple[list[dict[str, str]], list[str]]:
    pooled: list[dict[str, str]] = []
    seen: set[str] = set()
    log: list[str] = []
    for angle in angles:
        try:
            items = _web_search_items(angle, per_angle)
        except RuntimeError as e:
            log.append(f"- {angle}: failed ({e})")
            continue
        added = 0
        for item in items:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                pooled.append(item)
                added += 1
        log.append(f"- {angle}: {added} new result(s)")
    return pooled, log


def _novelty_rows(items: list[dict[str, str]], idea: str, prefix: str) -> list[str]:
    scored = sorted(items, key=lambda item: -_overlap(idea, f"{item.get('title', '')} {item.get('snippet', '')}"))
    rows: list[str] = []
    for index, item in enumerate(scored, 1):
        score = _overlap(idea, f"{item.get('title', '')} {item.get('snippet', '')}")
        relation = "close" if score >= 0.5 else "adjacent" if score >= 0.25 else "background"
        rows.append(
            f"[{prefix}{index}] {relation.upper()} | overlap={score:.0%} | {item.get('title', _source_domain(item.get('url', '')))}\n"
            f"     {item.get('url', '')}\n     {item.get('snippet', '')[:350]}"
        )
    return rows


def _novelty_readout(idea: str, rows: list[str], items: list[dict[str, str]]) -> list[str]:
    close = [row for row in rows if "CLOSE" in row]
    adjacent = [row for row in rows if "ADJACENT" in row]
    idea_tokens = _tokens(idea)
    covered: set[str] = set()
    for item in items:
        covered |= _tokens(f"{item.get('title', '')} {item.get('snippet', '')}")
    differentiators = sorted(idea_tokens - covered)
    risk = "high" if len(close) >= 2 else "medium" if close or len(adjacent) >= 2 else "low"
    lines = [
        "NOVELTY READOUT",
        f"- Overlap risk: {risk} based on snippet similarity only.",
        f"- Close matches: {len(close)} | Adjacent matches: {len(adjacent)} | Sources scanned: {len(items)}",
    ]
    if differentiators:
        lines.append("- Terms in the idea not found in the collected snippets: " + ", ".join(differentiators[:12]))
    else:
        lines.append("- No unique idea terms were found in the snippets; refine the concept before claiming novelty.")
    lines.extend([
        "- Next: build a claims/feature chart against the closest matches, then ask a qualified reviewer for legal clearance.",
        f"- {_DISCLAIMER}",
    ])
    return lines


@register("prior_art_search", "Search web, academic, product, and open-source sources for prior art before building an idea.",
          {"idea": "idea or concept to check", "field": "field or domain filter (optional)",
           "years": "date filter text such as 2022-2026 (optional)", "sources": "number of sources (default 6, max 10)",
           "index": "true to index fetched sources into local memory (default true)"},
          ["idea"], category="innovation", dangerous=True, read_only=False)
def prior_art_search(idea: str, field: str = "", years: str = "", sources: int | str = 6,
                     index: bool | str = True) -> str:
    concept = _clean(idea)
    if not concept:
        raise ValueError("Idea is required")
    field_text = _clean(field)
    years_text = _clean(years)
    base = concept + (f" in {field_text}" if field_text else "")
    angles = [
        f"{base} prior art",
        f"{base} existing product",
        f"{base} research paper",
        f"{base} open source project",
        f"{base} similar solution",
        f"{base} {years_text}" if years_text else f"{base} recent developments",
    ]
    try:
        source_count = min(10, max(2, int(sources)))
    except (TypeError, ValueError):
        source_count = 6
    if not safety.require_external(
        "allow_external_research", "Run prior-art search?",
        f"Idea: {concept[:500]}\nSearches: {len(angles)}\n"
        "Public web searches and page fetches will be performed. No private data is sent.",
    ):
        raise PermissionError("External research was denied by the user")
    pooled, log = _search_pool(angles, 5)
    if not pooled:
        raise RuntimeError("Prior-art search found no usable results")
    chosen = _diverse_sources(pooled, source_count)
    rows = _novelty_rows(chosen, concept, "PA")
    index_flag = _flag(index)
    fetch_notes: list[str] = []
    fetched = 0
    if index_flag:
        mem = default_memory()
        ensure_dirs()
        for number, item in enumerate(chosen, 1):
            url = item.get("url", "")
            try:
                text = _fetch_research_text(url, 3000)
                chunks = chunk_text(text[:500_000])
                mem.upsert_document(f"url:{url}", 0, len(text), f"url:{url}", chunks)
                fetched += 1
            except Exception as e:
                fetch_notes.append(f"- PA{number} {url}: {e}")
    lines = [
        "PRIOR ART SCAN",
        f"Idea: {concept}",
        f"Field: {field_text or 'unspecified'} | Date filter: {years_text or 'none'}",
        "",
        "SEARCH ANGLES",
        "\n".join(log),
        "",
        "CLOSEST MATCHES",
        "\n".join(rows),
        "",
    ]
    lines.extend(_novelty_readout(concept, rows, chosen))
    if index_flag:
        lines.append(f"Indexed {fetched} unique source(s) into local memory.")
    if fetch_notes:
        lines.extend(["FETCH FAILURES", "\n".join(fetch_notes)])
    return "\n".join(lines)


@register("patent_search", "Search patent publications for potential overlap with an idea using patent-site queries.",
          {"idea": "idea or concept to check", "jurisdiction": "US|EP|WO|IN|CN|JP|all (default US)",
           "status": "all|granted|pending (default all)", "limit": "result limit (default 8, max 12)"},
          ["idea"], category="innovation", dangerous=True, read_only=True)
def patent_search(idea: str, jurisdiction: str = "US", status: str = "all", limit: int | str = 8) -> str:
    concept = _clean(idea)
    if not concept:
        raise ValueError("Idea is required")
    jur = str(jurisdiction or "US").strip().upper()
    site = "site:patents.google.com/patent" + (f"/{jur}" if jur and jur != "ALL" else "")
    status_word = " patent application" if str(status).lower() == "pending" else \
        " granted patent" if str(status).lower() == "granted" else ""
    angles = [
        f"{site} {concept}{status_word}",
        f"{site} {concept} claims",
        f"{site} {concept} abstract",
        f"{concept} {jur} patent {status}",
    ]
    try:
        result_limit = min(12, max(2, int(limit)))
    except (TypeError, ValueError):
        result_limit = 8
    if not safety.require_external(
        "allow_external_research", "Run patent search?",
        f"Idea: {concept[:500]}\nJurisdiction: {jur}\nStatus: {status}\n"
        "Public patent search results will be opened. No private data is sent.",
    ):
        raise PermissionError("External research was denied by the user")
    pooled, log = _search_pool(angles, 6)
    if not pooled:
        raise RuntimeError("Patent search found no usable results")
    chosen = _diverse_sources(pooled, result_limit)
    rows = _novelty_rows(chosen, concept, "PT")
    lines = [
        "PATENT LANDSCAPE SCAN",
        f"Idea: {concept}",
        f"Jurisdiction: {jur} | Status filter: {status} | Source: Google Patents search results",
        "",
        "SEARCH ANGLES",
        "\n".join(log),
        "",
        "POTENTIAL OVERLAP",
        "\n".join(rows),
        "",
    ]
    lines.extend(_novelty_readout(concept, rows, chosen))
    lines.extend([
        "",
        "PATENT-SPECIFIC NEXT ACTIONS",
        "- Read the independent claims, not only titles and abstracts.",
        "- Build a feature-to-claim chart for the closest publications.",
        "- Check family status, continuations, assignments, and expiry dates.",
        "- Confirm freedom to operate with a qualified patent professional.",
    ])
    return "\n".join(lines)


def _idea_score(challenge: str, frame_prompt: str, constraints: list[str], evidence: str) -> tuple[int, list[str]]:
    novelty = 4 - min(4, int(round(_overlap(frame_prompt + " " + challenge, evidence) * 6))) if evidence else 4
    feasibility = 5 - min(4, len(constraints) // 2)
    impact = 3 + min(2, len(_tokens(challenge)) // 4)
    evidence_score = 4 if _parse_sources(evidence) else 2
    total = round((novelty * 0.35 + feasibility * 0.25 + impact * 0.25 + evidence_score * 0.15) * 20)
    reasons = [
        f"novelty={novelty}/5",
        f"feasibility={feasibility}/5",
        f"impact={impact}/5",
        f"evidence={evidence_score}/5",
    ]
    return max(0, min(100, total)), reasons


@register("idea_lab", "Generate structured SCAMPER, TRIZ, and jobs-to-be-done idea prompts with transparent scoring.",
          {"challenge": "problem or opportunity", "framework": "scamper|triz|jtbd|all (default all)",
           "constraints": "comma or newline separated constraints (optional)",
           "evidence": "prior-art or research brief used for novelty scoring (optional)",
           "count": "number of ideas (default 8, max 20)"},
          ["challenge"], category="innovation")
def idea_lab(challenge: str, framework: str = "all", constraints: str = "", evidence: str = "",
             count: int | str = 8) -> str:
    problem = _clean(challenge)
    if not problem:
        raise ValueError("Challenge is required")
    selected = str(framework or "all").strip().lower()
    if selected not in _FRAMEWORKS and selected != "all":
        raise ValueError("framework must be scamper, triz, jtbd, or all")
    names = list(_FRAMEWORKS) if selected == "all" else [selected]
    constraint_list = _as_list(constraints)
    try:
        wanted = min(20, max(1, int(count)))
    except (TypeError, ValueError):
        wanted = 8
    frames = [(name, verb, cue) for name in names for verb, cue in _FRAMEWORKS[name]]
    core = " ".join(problem.split()[:14])
    ideas: list[dict[str, object]] = []
    for name, verb, cue in frames[:wanted]:
        prompt = f"{cue} Apply it to: {problem}. Constraints: {', '.join(constraint_list) or 'none supplied'}."
        score, reasons = _idea_score(problem, prompt, constraint_list, evidence)
        ideas.append({
            "framework": name,
            "name": f"{verb}: {core}",
            "prompt": prompt,
            "score": score,
            "reasons": reasons,
        })
    ranked = sorted(ideas, key=lambda item: int(item["score"]), reverse=True)
    lines = [
        "IDEA LAB",
        f"Challenge: {problem}",
        f"Frameworks: {', '.join(names)} | Constraints: {', '.join(constraint_list) or 'none'}",
        "",
        "IDEAS",
    ]
    for index, idea in enumerate(ideas, 1):
        lines.append(
            f"[{index}] {idea['name']} | score {idea['score']}/100 | framework {idea['framework']}\n"
            f"     Prompt: {idea['prompt']}\n"
            f"     Scoring: {', '.join(idea['reasons'])}"
        )
    lines.extend([
        "",
        "TOP PICKS",
        "\n".join(f"- {item['name']} ({item['score']}/100)" for item in ranked[:3]),
        "",
        "CAUTIONS",
        "- Scores are transparent heuristics, not validated market or technical evidence.",
        "- Run prior_art_search or patent_search before calling any idea novel.",
        "- Convert the top prompts into experiment_designer tests before committing resources.",
    ])
    return "\n".join(lines)


@register("experiment_designer", "Design a practical A/B or controlled experiment with metrics, sample size, stopping rules, and risks.",
          {"question": "decision or question to test", "hypothesis": "testable hypothesis (optional)",
           "metric": "primary metric (optional)", "sample_size": "total sample size (default 100)",
           "variants": "number of variants including control (default 2)",
           "duration_days": "planned duration in days (default 14)",
           "risks": "comma or newline separated risks (optional)",
           "evidence": "supporting research brief (optional)"},
          ["question"], category="innovation")
def experiment_designer(question: str, hypothesis: str = "", metric: str = "", sample_size: int | str = 100,
                        variants: int | str = 2, duration_days: int | str = 14, risks: str = "",
                        evidence: str = "") -> str:
    topic = _clean(question)
    if not topic:
        raise ValueError("Experiment question is required")
    primary = _clean(metric) or f"success rate for {topic}"
    hypothesis_text = _clean(hypothesis) or f"{topic} will improve {primary} compared with the control."
    try:
        total = min(1_000_000, max(4, int(sample_size)))
    except (TypeError, ValueError):
        total = 100
    try:
        variant_count = min(6, max(2, int(variants)))
    except (TypeError, ValueError):
        variant_count = 2
    try:
        days = min(365, max(1, int(duration_days)))
    except (TypeError, ValueError):
        days = 14
    per_variant = total // variant_count
    mde = 1.96 * math.sqrt(2 * 0.5 * 0.5 / max(2, per_variant))
    risk_list = _as_list(risks)
    default_risks = ["novelty effect", "sample contamination", "metric gaming", "privacy or safety regression"]
    all_risks = risk_list + [item for item in default_risks if item not in risk_list]
    controls = [f"- {item}: predefine guardrails, monitor daily, and rollback on breach." for item in all_risks]
    source_count = len(_parse_sources(evidence))
    lines = [
        "EXPERIMENT DESIGN",
        f"Question: {topic}",
        f"Hypothesis: {hypothesis_text}",
        f"Null hypothesis: {topic} produces no change in {primary} versus control.",
        "",
        "MEASUREMENT",
        f"- Primary metric: {primary}",
        "- Guardrail metrics: error rate, latency, cost per user, complaints, privacy/safety incidents.",
        "- Segment by acquisition source, device, and new versus returning user if available.",
        "",
        "DESIGN",
        f"- Variants: {variant_count} including control",
        f"- Total sample: {total} | Per variant: {per_variant}",
        f"- Duration: {days} days or until the pre-registered sample is reached, whichever is later",
        "- Assignment: random, stable per user, with allocation logged before exposure.",
        f"- Approximate binary MDE at 95% confidence: {mde:.1%} absolute (heuristic p=0.5, equal split).",
        "- Do not stop early for a favourable result; use a pre-registered sequential rule if early stopping is needed.",
        "",
        "DECISION RULE",
        f"- Ship only if {primary} improves beyond the MDE with guardrails unchanged.",
        "- If results conflict across segments, keep the control and run a targeted follow-up.",
        "- Record the exact metric definition, exclusions, and data snapshot before launch.",
        "",
        "RISK CONTROLS",
        "\n".join(controls),
        "",
        "EVIDENCE",
        f"- Parsed source handles from supplied evidence: {source_count}",
        "- Treat the MDE as a planning heuristic; use a statistician for regulated, safety-critical, or high-cost decisions.",
    ]
    return "\n".join(lines)


def _parse_weights(value: object, criteria: list[str]) -> dict[str, float]:
    result = {criterion: 1.0 for criterion in criteria}
    if isinstance(value, dict):
        raw = {str(k).strip(): v for k, v in value.items() if str(k).strip()}
    else:
        text = str(value or "").strip()
        if not text:
            return result
        if text.startswith("{"):
            try:
                decoded = json.loads(text)
                raw = decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError as e:
                raise ValueError(f"weights must be a JSON object or key=value list: {e}") from e
        else:
            raw = {}
            for item in re.split(r"[,\n]", text):
                key, sep, number = item.partition("=")
                if sep:
                    raw[key.strip()] = number.strip()
    for key, number in raw.items():
        if key not in result:
            continue
        try:
            result[key] = max(0.0, min(10.0, float(number)))
        except (TypeError, ValueError):
            raise ValueError(f"Invalid weight for '{key}': {number}") from None
    return result


def _decision_scores(options: list[str], criteria: list[str], evidence: str) -> dict[tuple[str, str], int | None]:
    lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    scores: dict[tuple[str, str], int | None] = {}
    for option in options:
        option_tokens = _tokens(option)
        for criterion in criteria:
            criterion_tokens = _tokens(criterion)
            matches = [line for line in lines
                       if option_tokens & _tokens(line) and criterion_tokens & _tokens(line)]
            scores[(option, criterion)] = min(5, 1 + len(matches)) if matches else None
    return scores


def _weighted_totals(options: list[str], criteria: list[str], weights: dict[str, float],
                     scores: dict[tuple[str, str], int | None]) -> dict[str, float | None]:
    totals: dict[str, float | None] = {}
    for option in options:
        numerator = 0.0
        denominator = 0.0
        for criterion in criteria:
            score = scores.get((option, criterion))
            if score is None:
                continue
            numerator += score * weights[criterion]
            denominator += weights[criterion]
        totals[option] = numerator / denominator if denominator > 0 else None
    return totals


@register("decision_matrix", "Compare options against weighted criteria with evidence-linked scores and sensitivity analysis.",
          {"options": "JSON list or comma-separated option names",
           "criteria": "JSON list or comma-separated criteria",
           "weights": "JSON object or comma-separated criterion=weight list (default equal weights)",
           "evidence": "research brief or notes containing option and criterion evidence (optional)"},
          ["options", "criteria"], category="innovation")
def decision_matrix(options: object, criteria: object, weights: object = "", evidence: str = "") -> str:
    option_list = _as_list(options)
    criteria_list = _as_list(criteria)
    if len(option_list) < 2:
        raise ValueError("Provide at least two options")
    if len(criteria_list) < 2:
        raise ValueError("Provide at least two criteria")
    weight_map = _parse_weights(weights, criteria_list)
    evidence_text = str(evidence or "")
    scores = _decision_scores(option_list, criteria_list, evidence_text)
    totals = _weighted_totals(option_list, criteria_list, weight_map, scores)
    lines = [
        "DECISION MATRIX",
        f"Options: {len(option_list)} | Criteria: {len(criteria_list)}",
        "",
        "WEIGHTS",
        "\n".join(f"- {criterion}: {weight_map[criterion]:g}" for criterion in criteria_list),
        "",
        "SCORES (0-5 from direct evidence mentions; N/A means no direct evidence)",
        "| Option | " + " | ".join(criteria_list) + " | Weighted |",
        "|---|" + "---|" * (len(criteria_list) + 1),
    ]
    for option in option_list:
        cells = []
        for criterion in criteria_list:
            score = scores[(option, criterion)]
            cells.append("N/A" if score is None else str(score))
        total = totals[option]
        cells.append("N/A" if total is None else f"{total:.2f}")
        lines.append(f"| {option} | " + " | ".join(cells) + " |")
    ranked = sorted((item for item in totals.items() if item[1] is not None),
                    key=lambda item: item[1], reverse=True)
    lines.extend(["", "WEIGHTED RANKING"])
    if ranked:
        lines.extend(f"{index}. {name} ({score:.2f})" for index, (name, score) in enumerate(ranked, 1))
    else:
        lines.append("- No option has direct evidence; supply evidence or expert scores before ranking.")
    base_leader = ranked[0][0] if ranked else None
    sensitive: list[str] = []
    for criterion in criteria_list:
        altered = dict(weight_map)
        altered[criterion] = min(10.0, altered[criterion] * 1.5 + 0.5)
        altered_totals = _weighted_totals(option_list, criteria_list, altered, scores)
        altered_ranked = sorted((item for item in altered_totals.items() if item[1] is not None),
                                key=lambda item: item[1], reverse=True)
        if altered_ranked and altered_ranked[0][0] != base_leader:
            sensitive.append(criterion)
    lines.extend(["", "SENSITIVITY",
                  "- Ranking changed when increasing: " + ", ".join(sensitive) if sensitive
                  else "- Ranking was stable under a +50% weight stress test." if base_leader
                  else "- Sensitivity not run because no option has direct evidence."])
    gaps = [criterion for criterion in criteria_list
            if all(scores[(option, criterion)] is None for option in option_list)]
    lines.extend(["", "EVIDENCE GAPS",
                  "- No direct evidence for: " + ", ".join(gaps) if gaps
                  else "- Every criterion has at least one evidence-linked score."])
    if base_leader and not sensitive:
        lines.extend(["", "PRELIMINARY READ", f"- {base_leader} leads under current weights; validate the evidence before deciding.", _DISCLAIMER])
    else:
        lines.extend(["", "PRELIMINARY READ", "- Do not decide yet; collect evidence or resolve weight sensitivity first.", _DISCLAIMER])
    return "\n".join(lines)
