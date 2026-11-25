from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from .. import safety
from .registry import register

_STOPWORDS = {
    "about", "above", "across", "after", "again", "against", "also", "among", "analysis", "approach",
    "around", "based", "because", "been", "before", "being", "below", "between", "both", "could",
    "data", "does", "each", "either", "every", "field", "from", "have", "however", "into", "itself",
    "more", "most", "much", "multiple", "must", "need", "other", "over", "paper", "research", "result",
    "results", "same", "several", "should", "since", "some", "study", "such", "than", "that", "their",
    "them", "then", "there", "these", "they", "this", "those", "through", "under", "using", "very",
    "what", "when", "where", "which", "while", "with", "within", "without", "would", "your",
}

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _flag(value: object) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _first(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return _text(value[0]) if value else ""
    return _text(value)


def _as_list(value: object) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    return [part.strip() for part in text.split(",") if part.strip()]


def _cell(value: object) -> str:
    return " ".join(str(value or "").replace("|", "/").split())


def _tokens(text: object) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", str(text or "").lower())
    return [word for word in words if word not in _STOPWORDS and not re.fullmatch(r"[sr]\d+", word)]


def _themes(text: object, cap: int) -> list[str]:
    words = _tokens(text)
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    chosen = [word for word, _ in ranked[:cap]]
    if not chosen and words:
        chosen = words[:cap]
    return chosen or ["method", "evaluation", "limitations"]


def _source_records(brief: object) -> list[dict]:
    records: list[dict] = []
    current: dict | None = None
    for raw in str(brief or "").splitlines():
        line = raw.strip()
        handle = re.match(r"^\[(S\d+|R\d+)\]\s*(.*)$", line)
        if handle:
            current = {"handle": handle.group(1), "title": handle.group(2).strip(), "url": "", "note": ""}
            records.append(current)
            continue
        if current is None:
            continue
        if not current["url"]:
            found = re.search(r"https?://\S+", line)
            if found:
                current["url"] = found.group(0).rstrip(".,);")
        note = re.match(r"^(?:Why|Evidence|Note):\s*(.*)$", line, re.IGNORECASE)
        if note:
            current["note"] = (current["note"] + " " + note.group(1)).strip()
        elif line and not line.startswith("["):
            current["note"] = (current["note"] + " " + line).strip()
    return records


def _sections(value: object) -> list[tuple[str, str]]:
    raw = _as_list(value)
    if not raw:
        return []
    result: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            heading = _text(item.get("title") or item.get("heading"))
            body = _text(item.get("content") or item.get("body"))
        else:
            line = str(item).strip()
            if "|" in line:
                heading, body = (part.strip() for part in line.split("|", 1))
            else:
                heading, body = line, ""
        if heading:
            result.append((heading, body))
    return result


def _latex_escape(value: object) -> str:
    mapping = {
        "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#",
        "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
    }
    return "".join(mapping.get(char, char) for char in str(value or ""))


def _looks_like_latex(text: str) -> bool:
    return bool(re.search(r"\\[A-Za-z]+|\\[\[(]", str(text or "")))


_BIB_ESCAPES = {"%": r"\%", "&": r"\&", "_": r"\_", "#": r"\#", "$": r"\$"}


def _bib_escape(value: object) -> str:
    return "".join(_BIB_ESCAPES.get(char, char) for char in _text(value))


def _section_body(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "\\emph{Add verified content here.}"
    if _looks_like_latex(text):
        return text
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) > 1:
        items = "\n".join(f"\\item {_latex_escape(part)}" for part in parts)
        return "\\begin{itemize}\n" + items + "\n\\end{itemize}"
    return _latex_escape(text)


def _latex_document(title: str, authors: str = "", abstract: str = "", sections: object = "",
                    style: str = "ieee", keywords: str = "", bib: str = "references") -> str:
    name = _text(title) or "Untitled Research Paper"
    chosen = str(style or "ieee").strip().lower()
    if chosen not in ("ieee", "acm", "springer", "plain"):
        raise ValueError("style must be ieee, acm, springer, or plain")
    if chosen == "ieee":
        document_class = "\\documentclass[journal,twocolumn]{IEEEtran}"
        bib_style = "IEEEtran"
        keyword_command = "IEEEkeywords"
    elif chosen == "acm":
        document_class = "\\documentclass[sigconf]{acmart}"
        bib_style = "ACM-Reference-Format"
        keyword_command = "keywords"
    elif chosen == "springer":
        document_class = "\\documentclass[twocolumn,10pt]{article}"
        bib_style = "plain"
        keyword_command = "keywords"
    else:
        document_class = "\\documentclass[11pt,a4paper]{article}"
        bib_style = "plain"
        keyword_command = "keywords"

    lines = [
        document_class,
        "\\usepackage{amsmath,amssymb}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{url}",
        "\\usepackage{xcolor}",
        "\\usepackage{algorithm}",
        "\\usepackage{algpseudocode}",
    ]
    if chosen in ("plain", "springer"):
        lines.append("\\usepackage[margin=1in]{geometry}")
    if chosen != "acm":
        lines.append("\\usepackage[hidelinks]{hyperref}")
    lines += [
        f"\\bibliographystyle{{{bib_style}}}",
        "",
        f"\\title{{{_latex_escape(name)}}}",
    ]
    author_text = str(authors or "").strip()
    if author_text:
        author_lines = [_latex_escape(part.strip()) for part in
                        re.split(r"\n|;|\s+and\s+", author_text) if part.strip()]
        joined_authors = " \\and ".join(author_lines)
        lines.append(f"\\author{{{joined_authors}}}")
    lines.append("\\maketitle")

    abstract_text = str(abstract or "").strip()
    if abstract_text:
        body = abstract_text if _looks_like_latex(abstract_text) else _latex_escape(abstract_text)
        lines += ["", "\\begin{abstract}", body, "\\end{abstract}"]
    keyword_text = str(keywords or "").strip()
    if keyword_text:
        values = [_latex_escape(part.strip()) for part in keyword_text.split(",") if part.strip()]
        lines += ["", f"\\begin{{{keyword_command}}}", ", ".join(values), f"\\end{{{keyword_command}}}"]

    lines += ["", "\\begin{document}"]
    blocks = _sections(sections)
    if not blocks:
        blocks = [
            ("Introduction", "Context, gap, research question, and contributions."),
            ("Related Work", "Group prior work by theme and state the uncovered gap."),
            ("Method", "Design, data, metrics, baseline, and ethical considerations."),
            ("Results", "Report results per research question with evidence."),
            ("Discussion", "Interpretation, threats to validity, and implications."),
            ("Conclusion", "Answer the question and list bounded future work."),
        ]
    for heading, body in blocks:
        lines += ["", f"\\section{{{_latex_escape(heading)}}}", _section_body(body)]
    bib_stem = _text(bib).strip().replace(" ", "") or "references"
    lines += ["", "\\bibliography{" + bib_stem + "}", "\\end{document}"]
    return "\n".join(lines) + "\n"


@register(
    "paper_lookup",
    "Look up real papers by bibliographic query and return verbatim metadata with DOI and URL; never invent citations.",
    {"query": "title, topic, author, or DOI to look up", "limit": "max results (default 8, max 12)",
     "year_from": "earliest publication year (optional)", "sort": "relevance|cited|date (default relevance)"},
    ["query"],
    category="paper",
    dangerous=True,
    read_only=True,
)
def paper_lookup(query: str, limit: int | str = 8, year_from: int | str = "", sort: str = "relevance") -> str:
    topic = _text(query)
    if len(topic) < 2:
        raise ValueError("query must contain at least 2 characters")
    cap = _bounded_int(limit, 8, 1, 12)
    order = str(sort or "relevance").strip().lower()
    sort_map = {"relevance": "relevance", "cited": "is-referenced-by-count", "date": "published"}
    if order not in sort_map:
        raise ValueError("sort must be relevance, cited, or date")
    if not safety.require_external(
        "allow_external_research",
        "Look up research papers?",
        f"Query: {topic[:500]}\nA public bibliographic API will be queried. No private data is sent.",
    ):
        raise PermissionError("External research was denied by the user")
    params = {
        "query.bibliographic": topic[:400],
        "rows": str(cap),
        "select": "DOI,title,author,published,container-title,URL,is-referenced-by-count",
        "sort": sort_map[order],
        "order": "desc",
    }
    year = _text(year_from)
    if year:
        try:
            parsed_year = int(year)
            if 1500 <= parsed_year <= 2100:
                params["filter"] = f"from-pub-date:{parsed_year}-01-01"
        except ValueError:
            pass
    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=20,
            headers={"User-Agent": "THAMBI-local-research/1.0 (private assistant)"},
        )
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
    except Exception as exc:
        raise RuntimeError(f"Paper lookup failed: {exc}") from exc
    lines = [
        "PAPER LOOKUP",
        f"Query: {topic}",
        f"Source: Crossref REST API | Sort: {order} | Results: {len(items)}",
        "",
    ]
    for number, item in enumerate(items, 1):
        title = html.unescape(re.sub(r"<[^>]+>", "", _first(item.get("title")) or "Untitled"))
        authors = []
        for author in item.get("author") or []:
            given = _text(author.get("given"))
            family = _text(author.get("family"))
            named = _text(author.get("name"))
            full = named or " ".join(part for part in (given, family) if part)
            if full:
                authors.append(full)
        date_parts = (item.get("published") or {}).get("date-parts") or [[]]
        published_year = str(date_parts[0][0]) if date_parts and date_parts[0] else "unknown year"
        venue = _first(item.get("container-title")) or "not supplied"
        doi = _text(item.get("DOI"))
        url = _text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else "")
        citations = item.get("is-referenced-by-count")
        shown = ", ".join(authors[:8]) or "not supplied"
        if len(authors) > 8:
            shown += " et al."
        lines.append(f"[{number}] {title}")
        lines.append(f"    Authors: {shown}")
        lines.append(f"    Venue: {venue} | Year: {published_year} | Cited by: {citations if citations is not None else 'n/a'}")
        if doi:
            lines.append(f"    DOI: {doi}")
        if url:
            lines.append(f"    URL: {url}")
    lines += [
        "",
        "VERBATIM METADATA ONLY: verify each entry against the publisher before citing.",
        "Use bibtex_builder with these exact fields. Never invent titles, authors, DOIs, venues, or results.",
    ]
    return "\n".join(lines)


@register(
    "paper_outline",
    "Build an IMRaD research-paper outline with research questions, contributions, evidence mapping, and per-section targets.",
    {"title": "working paper title", "question": "primary research question",
     "field": "field or target venue (optional)", "evidence": "research brief or source notes with [S#] handles (optional)",
     "contributions": "comma or newline separated claimed contributions (optional)",
     "style": "ieee|acm|apa|generic (default ieee)"},
    ["title", "question"],
    category="paper",
)
def paper_outline(title: str, question: str, field: str = "", evidence: str = "",
                  contributions: object = "", style: str = "ieee") -> str:
    name = _text(title) or "Untitled Research Paper"
    question_text = _text(question)
    if not question_text:
        raise ValueError("question is required")
    venue = str(style or "ieee").strip().lower()
    if venue not in ("ieee", "acm", "apa", "generic"):
        raise ValueError("style must be ieee, acm, apa, or generic")
    handles = sorted(set(re.findall(r"\[(?:S|R)\d+\]", str(evidence or ""))))
    themes = _themes(evidence, 5)
    claims = [item for item in _as_list(contributions) if _text(item)]
    lines = [
        "PAPER OUTLINE",
        f"Title: {name}",
        f"Field / venue: {_text(field) or venue.upper()} | Style: {venue.upper()}",
        f"Primary research question: {question_text}",
        "",
        "CLAIM-EVIDENCE LEDGER",
        f"- RQ1: {question_text}",
        "- Sub-questions: split into context, mechanism, and boundary-condition questions; answer each with cited evidence.",
        f"- Available source handles: {', '.join(handles) if handles else 'none supplied; use paper_lookup before writing claims'}",
        f"- Contributions: {'; '.join(_text(item) for item in claims) if claims else 'state only what this paper demonstrably adds'}",
        "",
        "SECTION PLAN",
    ]
    sections = [
        ("1. Introduction", [
            f"Motivate the gap for: {question_text}",
            "State RQ1 and 2-3 sub-questions; define scope and assumptions.",
            "List contributions as verifiable claims, not promises.",
        ]),
        ("2. Related Work", [
            f"Theme clusters: {', '.join(themes)}.",
            "Compare each theme by method, dataset, metric, and finding; cite one handle per claim.",
            "End with an explicit, evidence-backed gap statement.",
        ]),
        ("3. Method", [
            "Specify design, units, sample size, instruments, and assignment procedure.",
            "Define independent, dependent, and control variables; name the primary metric and baselines.",
            "State statistical plan, significance threshold, and ethical or data-handling constraints.",
        ]),
        ("4. Results", [
            "Report one subsection per research question in the same order as the Introduction.",
            "Give effect sizes and uncertainty, not only p-values; label descriptive vs inferential results.",
            "Include failed or null results and reference every table and figure in the text.",
        ]),
        ("5. Discussion", [
            "Interpret results against prior work and the stated gap.",
            "Cover internal, external, construct, and conclusion threats to validity.",
            "Separate observed findings from inference and speculation.",
        ]),
        ("6. Conclusion", [
            f"Answer {question_text} directly and boundedly.",
            "List limitations and concrete future work; do not overclaim generality.",
        ]),
        ("References", [
            "Include only verified sources with DOI or stable URL.",
            "Use bibtex_builder for entries; do not fabricate authors, years, or venues.",
        ]),
    ]
    if venue == "apa":
        sections[0] = ("1. Introduction", sections[0][1])
    for heading, bullets in sections:
        lines.append(heading)
        lines.extend(f"  - {bullet}" for bullet in bullets)
        lines.append("")
    lines += [
        "WRITING RULES",
        "- One claim per sentence with an attached source handle or a result produced by your own method.",
        "- Keep a claim-evidence ledger so every number can be traced to a table, artifact, or citation.",
        "- Mark placeholders explicitly; never present an unverified number as a result.",
    ]
    return "\n".join(lines)


@register(
    "literature_review",
    "Turn source notes with [S#] handles into a themed literature-review matrix with supports, gaps, and contradiction cues.",
    {"evidence": "research brief or pasted source notes with [S#] handles and URLs",
     "question": "review question or scope (optional)", "max_themes": "maximum themes (default 6, max 12)"},
    ["evidence"],
    category="paper",
)
def literature_review(evidence: str, question: str = "", max_themes: int | str = 6) -> str:
    source_text = _text(evidence)
    if not source_text:
        raise ValueError("evidence is required")
    cap = _bounded_int(max_themes, 6, 1, 12)
    records = _source_records(source_text)
    topics = _themes(source_text, cap)
    domains = sorted({urlparse(record["url"]).netloc.lower() for record in records if record["url"]})
    lines = [
        "LITERATURE REVIEW MATRIX",
        f"Review question: {_text(question) or 'not supplied'}",
        f"Sources parsed: {len(records)} | Distinct domains: {len(domains)}",
        "",
        "| Theme | Handles | Supported point | Gap / contradiction cue |",
        "| --- | --- | --- | --- |",
    ]
    for theme in topics:
        handles = []
        for record in records:
            blob = (record["title"] + " " + record["note"]).lower()
            if theme in blob:
                handles.append(record["handle"])
        support = []
        for handle in handles[:3]:
            match = next((record["note"] for record in records if record["handle"] == handle), "")
            if match:
                support.append(match[:180])
        cue = []
        if any(re.search(r"however|whereas|unlike|but |contradict", record["note"], re.IGNORECASE) for record in records):
            cue.append("contradiction language present; verify the conflicting claims")
        if not handles:
            cue.append("theme not found in supplied evidence; no source can be attributed")
        elif len(handles) < 2:
            cue.append("single-source theme; seek independent support")
        lines.append(
            f"| {_cell(theme.title())} | {_cell(', '.join(f'[{handle}]' for handle in handles)) or 'none'} | "
            f"{_cell('; '.join(support)) or 'no evidence line supplied'} | {_cell('; '.join(cue)) or 'none noted'} |"
        )

    lower = source_text.lower()
    gaps = []
    if not re.search(r"sample|participants|dataset|cohort|\bn\s*=", lower):
        gaps.append("No sample size or dataset is described; methods cannot be judged for adequacy.")
    if not re.search(r"baseline|comparator|control|state of the art", lower):
        gaps.append("No comparator or baseline is stated; relative improvement is unestablished.")
    if not re.search(r"limitation|threat to validity|future work", lower):
        gaps.append("Sources do not state limitations; boundary conditions are unclear.")
    for record in records:
        if not record["note"]:
            gaps.append(f"[{record['handle']}] has no evidence line; extract or drop it.")
    if not records:
        gaps.append("No [S#] handles found; supply source notes or run deep_web_research first.")
    lines += ["", "EVIDENCE GAPS"]
    lines.extend(f"- {gap}" for gap in gaps)

    contradictions = []
    for record in records:
        if re.search(r"however|whereas|unlike|contradict|but ", record["note"], re.IGNORECASE):
            contradictions.append(f"- [{record['handle']}]: {record['note'][:220]}")
    lines += ["", "CONTRADICTION CUES"]
    lines.extend(contradictions or ["- none detected by keyword scan; check claims manually"])

    lines += [
        "",
        "CITATION RULES",
        "- Cite one source handle per claim; do not merge distinct sources into a single invented reference.",
        "- Use only domains and URLs present above; verify metadata with paper_lookup before formatting with bibtex_builder.",
    ]
    return "\n".join(lines)


@register(
    "paper_study_design",
    "Design a publication-ready study with hypotheses, variables, metrics, sample size, statistical plan, validity threats, and reproducibility controls.",
    {"question": "research question to test", "hypothesis": "primary testable hypothesis (optional)",
     "variables": "comma or newline separated independent, dependent, and control variables (optional)",
     "metric": "primary outcome metric (optional)", "sample_size": "planned sample size (default 100)",
     "variants": "number of conditions including control (default 2)",
     "threats": "known threats to validity (optional)", "evidence": "supporting research brief (optional)"},
    ["question"],
    category="paper",
)
def paper_study_design(question: str, hypothesis: str = "", variables: object = "", metric: str = "",
                       sample_size: int | str = 100, variants: int | str = 2, threats: object = "",
                       evidence: str = "") -> str:
    prompt = _text(question)
    if not prompt:
        raise ValueError("question is required")
    total = _bounded_int(sample_size, 100, 2, 1000000)
    arms = _bounded_int(variants, 2, 1, 8)
    per_arm = max(1, total // arms)
    primary = _text(metric) or "define one primary outcome metric before data collection"
    declared = [item for item in _as_list(variables) if _text(item)]
    stated_threats = [item for item in _as_list(threats) if _text(item)]
    h1 = _text(hypothesis) or f"The proposed intervention changes {primary} relative to the control condition."
    lower_metric = primary.lower()
    if re.search(r"%|rate|accuracy|proportion|conversion|error", lower_metric):
        test_plan = "Two-proportion z-test or chi-square; report absolute difference, ratio, and 95% CI."
    elif re.search(r"time|latency|ms|second|duration|throughput", lower_metric):
        test_plan = "Welch t-test or Mann-Whitney U; report median, IQR, and a standardised effect size."
    else:
        test_plan = "Welch t-test or ANOVA (non-parametric equivalent if assumptions fail); report effect size and 95% CI."
    guardrail = "Define at least one guardrail metric to detect harm or hidden cost."
    lines = [
        "STUDY DESIGN",
        f"Research question: {prompt}",
        f"H1: {h1}",
        f"H0: no difference in {primary} across conditions.",
        "",
        "VARIABLES",
        f"- Independent: {_cell(', '.join(declared[:1])) or 'assign the manipulated condition explicitly'}",
        f"- Dependent: {_cell(primary)}",
        f"- Controls: {_cell(', '.join(declared[1:])) or 'list fixed confounders and how they are held constant'}",
        f"- Guardrail: {guardrail}",
        "",
        "MEASUREMENT",
        f"- Primary metric: {primary}; define numerator, denominator, and collection window.",
        f"- Baseline and comparator: control condition plus at least one relevant prior method from the evidence brief.",
        f"- Instrument: state the exact tool/version and calibration used to record {primary}.",
        "",
        "DESIGN",
        f"- Conditions: {arms} including control; planned total N={total}, about {per_arm} per condition.",
        "- Assignment: randomise where ethical and feasible; document any deviation.",
        "- Blinding: specify who is blinded during measurement and analysis.",
        "- Duration: fix the observation window before starting; avoid optional stopping.",
        "",
        "STATISTICAL PLAN",
        f"- Primary test: {test_plan}",
        f"- Multiplicity: {'apply Holm or Bonferroni correction across pairwise comparisons' if arms > 2 else 'one primary comparison; treat secondary analyses as exploratory'}.",
        "- Report effect sizes and confidence intervals, not only p-values; state alpha before analysis.",
        "- Run an a priori power analysis; the sample size above is a planning input, not a proof of sufficient power.",
        "",
        "THREATS TO VALIDITY",
        "- Internal: selection bias, learning effects, instrumentation drift.",
        "- External: population, task, and setting generalisability.",
        "- Construct: whether the metric truly captures the intended outcome.",
        "- Conclusion: statistical power, multiple comparisons, and effect stability.",
    ]
    lines.extend(f"- Supplied: {_cell(item)}" for item in stated_threats)
    lines += [
        "",
        "REPRODUCIBILITY",
        "- Pre-register the question, hypothesis, metric, exclusions, and stopping rule before data collection.",
        "- Publish code, data or a data-access statement, environment versions, and random seeds.",
        "- Keep a run log linking each result table to the exact artifact that produced it.",
        "",
        "CAUTION",
        "- These are heuristic planning prompts, not a power calculation, ethical approval, or a validity proof.",
        "- If evidence was supplied, reconcile it with the design before claiming novelty.",
    ]
    return "\n".join(lines)


@register(
    "latex_paper",
    "Generate compile-ready Overleaf LaTeX source for a research paper from a title, abstract, and section spec; supports IEEEtran, acmart, and article styles.",
    {"title": "paper title", "authors": "author names separated by newline or semicolon (optional)",
     "abstract": "abstract text (optional)", "sections": "JSON list of {title, content} or 'Title | point, point' lines",
     "style": "ieee|acm|springer|plain (default ieee)", "keywords": "comma separated keywords (optional)",
     "bib": "bibliography file stem (default references)"},
    ["title"],
    category="paper",
)
def latex_paper(title: str, authors: str = "", abstract: str = "", sections: object = "",
                style: str = "ieee", keywords: str = "", bib: str = "references") -> str:
    source = _latex_document(title, authors, abstract, sections, style, keywords, bib)
    return (
        "OVERLEAF LATEX SOURCE (save as main.tex)\n"
        "```latex\n" + source + "```\n"
        "Add verified entries to references.bib before compiling. For a venue-specific Springer class, "
        "replace the documentclass line with the class required by the publisher. Validate with latex_check."
    )


@register(
    "latex_check",
    "Statically validate LaTeX source for balanced braces, environments, math delimiters, labels, references, and Overleaf compile readiness.",
    {"source": "full LaTeX source text", "strict": "true to treat missing bibliography and undefined refs as errors (default false)"},
    ["source"],
    category="paper",
)
def latex_check(source: str, strict: bool | str = False) -> str:
    raw = str(source or "")
    if not raw.strip():
        raise ValueError("source is required")
    strict_mode = _flag(strict)
    visible_lines = []
    for line in raw.splitlines():
        output = []
        escaped = False
        for char in line:
            if char == "%" and not escaped:
                break
            output.append(char)
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
        visible_lines.append("".join(output))
    code = "\n".join(visible_lines)

    errors: list[str] = []
    warnings: list[str] = []
    depth = 0
    escaped = False
    for char in code:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                errors.append("Unmatched closing brace")
                depth = 0
    if depth > 0:
        errors.append(f"Unclosed braces: {depth} open brace(s) remain")

    stack: list[str] = []
    for match in re.finditer(r"\\(begin|end)\{([^}]+)\}", code):
        kind, env = match.group(1), match.group(2).strip()
        if kind == "begin":
            stack.append(env)
        elif not stack:
            errors.append("\\end{" + env + "} without matching \\begin")
        elif stack[-1] != env:
            errors.append("\\end{" + env + "} does not match \\begin{" + stack[-1] + "}")
            stack.pop()
        else:
            stack.pop()
    for env in stack:
        errors.append("Unclosed environment: " + env)

    dollars = len(re.findall(r"(?<!\\)\$", code))
    if dollars % 2:
        errors.append("Unbalanced inline math delimiters ($ count is odd)")
    for opener, closer in (("\\[", "\\]"), ("\\(", "\\)")):
        if code.count(opener) != code.count(closer):
            errors.append(f"Unbalanced display/inline math pair {opener} ... {closer}")
    for env in ("equation", "align", "gather", "multline"):
        if code.count("\\begin{" + env + "}") != code.count("\\end{" + env + "}"):
            errors.append(f"Unbalanced {env} environment")

    if not re.search(r"\\documentclass\b", code):
        errors.append("Missing \\documentclass")
    if "\\begin{document}" not in code or "\\end{document}" not in code:
        errors.append("Missing \\begin{document} or \\end{document}")

    labels = re.findall(r"\\label\{([^}]+)\}", code)
    refs = re.findall(r"\\(?:ref|eqref|cref|Cref|autoref)\{([^}]+)\}", code)
    cites = re.findall(r"\\(?:cite|citep|citet|parencite|textcite)(?:\[[^\]]*\])*\{([^}]+)\}", code)
    undefined = sorted(set(refs) - set(labels))
    duplicates = sorted({key for key in labels if labels.count(key) > 1})
    if undefined:
        target = errors if strict_mode else warnings
        target.append("Undefined references: " + ", ".join(undefined))
    if duplicates:
        warnings.append("Duplicate labels: " + ", ".join(duplicates))
    if not re.search(r"\\bibliography\s*\{", code) and not re.search(r"\\begin\{thebibliography\}", code):
        target = errors if strict_mode else warnings
        target.append("No \\bibliography or thebibliography section found")
    if not re.search(r"\\bibliographystyle\s*\{", code) and not re.search(r"\\begin\{thebibliography\}", code):
        warnings.append("No \\bibliographystyle specified")
    if "\\begin{table}" in code and "\\caption" not in code:
        warnings.append("Table environment without a caption")
    if "\\begin{figure}" in code and "\\caption" not in code:
        warnings.append("Figure environment without a caption")
    if "\\begin{itemize}" in code and "\\end{itemize}" not in code:
        warnings.append("Unclosed itemize environment")

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    lines = [
        "LATEX CHECK",
        f"Status: {status} | Lines: {len(raw.splitlines())} | Labels: {len(labels)} | Refs: {len(refs)} | Cite keys: {len(set(cites))}",
        "",
        "ERRORS",
    ]
    lines.extend(f"- {item}" for item in errors) if errors else lines.append("- none")
    lines += ["", "WARNINGS"]
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- none")
    if status == "PASS":
        lines += ["", "Static structure passed. Compile on Overleaf with pdfLaTeX to verify packages and citations."]
    return "\n".join(lines)


def _bib_entry(raw: object, used: dict[str, int]) -> tuple[str, list[str], str]:
    warnings: list[str] = []
    if isinstance(raw, dict):
        data = {str(key): value for key, value in raw.items()}
    else:
        pieces = [part.strip() for part in str(raw or "").split("|")]
        pieces += [""] * (8 - len(pieces))
        keys = ["key", "type", "title", "author", "year", "venue", "doi", "url"]
        data = dict(zip(keys, pieces[:8]))
    kind = str(data.get("type") or data.get("entry_type") or "misc").strip().lower()
    if kind not in ("article", "inproceedings", "book", "techreport", "mastersthesis", "phdthesis", "misc", "unpublished"):
        warnings.append(f"unsupported type '{kind}' treated as misc")
        kind = "misc"
    title = _text(data.get("title"))
    author = _text(data.get("author") or data.get("authors"))
    year = _text(data.get("year"))
    venue = _text(data.get("venue"))
    doi = _text(data.get("doi"))
    url = _text(data.get("url"))
    key = _text(data.get("key") or data.get("id"))
    if not key:
        seed = re.sub(r"[^a-z0-9]+", "", (author.split()[0] if author else "ref").lower()) or "ref"
        key = seed + (year or "") + (re.sub(r"[^a-z0-9]+", "", title[:24].lower()) or "entry")
    base = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    key = base
    counter = 0
    while key in used:
        counter += 1
        suffix = ""
        number = counter
        while number:
            number, remainder = divmod(number - 1, 26)
            suffix = chr(ord("a") + remainder) + suffix
        key = f"{base}_{suffix}"
    used[key] = 1
    if not title:
        warnings.append(f"{key}: missing title")
    if not author:
        warnings.append(f"{key}: missing author")
    if not year:
        warnings.append(f"{key}: missing year")
    venue_field = {
        "article": "journal", "inproceedings": "booktitle", "book": "publisher",
        "techreport": "institution", "mastersthesis": "school", "phdthesis": "school",
    }.get(kind)
    if venue_field and not venue:
        warnings.append(f"{key}: missing {venue_field}")
    fields = [("title", title), ("author", author), ("year", year)]
    if venue and venue_field:
        fields.append((venue_field, venue))
    for name, value in data.items():
        if name in ("key", "id", "type", "entry_type", "title", "author", "authors", "year", "venue", "doi", "url"):
            continue
        if name in ("journal", "booktitle", "publisher", "institution", "school", "volume", "number", "pages", "edition", "note"):
            fields.append((name, _text(value)))
    if doi:
        fields.append(("doi", doi))
    if url:
        fields.append(("url", url))
    for name, value in fields:
        raw = _text(value)
        if raw.count("{") != raw.count("}"):
            warnings.append(f"{key}: unbalanced braces in {name}")
    body = ",\n".join(f"  {name} = {{{_bib_escape(value)}}}" for name, value in fields if _text(value))
    entry = "@" + kind + "{" + key + (",\n" + body + "\n" if body else "") + "}"
    return entry, warnings, key


@register(
    "bibtex_builder",
    "Build valid BibTeX from verified metadata only; missing fields are reported and never invented.",
    {"entries": "JSON list of entry dicts or 'key | type | title | authors | year | venue | doi | url' lines",
     "style": "informational target style: ieee|acm|apa|plain (default ieee)"},
    ["entries"],
    category="paper",
)
def bibtex_builder(entries: object, style: str = "ieee") -> str:
    raw_entries = [item for item in _as_list(entries) if _text(item) or isinstance(item, dict)]
    if not raw_entries:
        raise ValueError("entries must contain at least one bibliographic record")
    chosen = str(style or "ieee").strip().lower()
    if chosen not in ("ieee", "acm", "apa", "plain"):
        raise ValueError("style must be ieee, acm, apa, or plain")
    used: dict[str, int] = {}
    built = [_bib_entry(item, used) for item in raw_entries]
    warnings = [warning for _, warns, _ in built for warning in warns]
    lines = ["BIBTEX", f"Entries: {len(built)} | Target style: {chosen.upper()}", "", "```bibtex"]
    lines.extend(entry for entry, _, _ in built)
    lines += ["```", "", "FIELD WARNINGS"]
    lines.extend(f"- {warning}" for warning in warnings) if warnings else lines.append("- none")
    lines += [
        "",
        "USAGE",
        "- Paste the entries into references.bib and cite with \\cite{key} or the venue-specific command.",
        "- Verify each DOI and URL against the publisher; this tool never invents bibliographic facts.",
    ]
    return "\n".join(lines)


@register(
    "paper_review",
    "Run a deterministic reviewer-style critique of a draft for structure, evidence, methods, reproducibility, and writing quality.",
    {"draft": "full paper draft or substantive excerpt", "focus": "all|structure|evidence|methods|writing (default all)",
     "max_findings": "maximum findings (default 12, max 25)"},
    ["draft"],
    category="paper",
)
def paper_review(draft: str, focus: str = "all", max_findings: int | str = 12) -> str:
    text = str(draft or "")
    if len(text.strip()) < 100:
        raise ValueError("draft must contain at least 100 characters")
    area_focus = str(focus or "all").strip().lower()
    if area_focus not in ("all", "structure", "evidence", "methods", "writing"):
        raise ValueError("focus must be all, structure, evidence, methods, or writing")
    cap = _bounded_int(max_findings, 12, 1, 25)
    lower = text.lower()
    findings: list[tuple[str, str, str, str]] = []
    headings = set()
    for match in re.finditer(r"(?im)^\s*(?:\d+(?:\.\d+)*\.?\s*)?(abstract|introduction|related work|method(?:ology)?|results|discussion|conclusion)\b", text):
        headings.add(match.group(1).lower().replace("methodology", "method"))
    for section in ("abstract", "introduction", "related work", "method", "results", "discussion", "conclusion"):
        if section not in headings and area_focus in ("all", "structure"):
            findings.append(("MEDIUM", "structure", f"Missing {section} section", f"Add a {section} heading with traceable content."))

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    unsupported = 0
    for sentence in sentences:
        if re.search(r"\b(shows?|proves?|improves?|outperforms?|significant(?:ly)?)\b", sentence, re.IGNORECASE):
            if not re.search(r"\[(?:S|R)\d+\]|\\cite|https?://", sentence):
                unsupported += 1
    if unsupported and area_focus in ("all", "evidence"):
        findings.append(("HIGH", "evidence", f"{unsupported} claim sentence(s) without a source handle or result reference",
                         r"Attach a [S#] handle, a \cite key, or a pointer to your own result table."))
    if re.search(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b|<[^>]+>", text, re.IGNORECASE):
        findings.append(("HIGH", "writing", "Placeholder text remains in the draft",
                         "Replace every placeholder with verified content before submission."))
    if not re.search(r"sample|participants|dataset|cohort|\bn\s*=", lower) and area_focus in ("all", "methods"):
        findings.append(("MEDIUM", "methods", "No sample size or dataset description found",
                         "State the unit of analysis, N, and inclusion or exclusion criteria."))
    if not re.search(r"metric|measure|accuracy|latency|precision|recall|f1|error", lower) and area_focus in ("all", "methods"):
        findings.append(("MEDIUM", "methods", "No explicit outcome metric found",
                         "Define one primary metric and any guardrail metrics."))
    if not re.search(r"baseline|compare|control|state of the art", lower) and area_focus in ("all", "methods"):
        findings.append(("MEDIUM", "methods", "No comparator or baseline found",
                         "Compare against a justified baseline and cite its source."))
    if re.search(r"significant", lower) and not re.search(r"p\s*[=<>]|confidence interval|\bci\b", lower) and area_focus in ("all", "methods"):
        findings.append(("MEDIUM", "methods", "Significance claimed without a test statistic or interval",
                         "Report the test, effect size, and 95% CI; treat exploratory results as such."))
    if not re.search(r"limitation|threat to validity|future work", lower) and area_focus in ("all", "structure"):
        findings.append(("MEDIUM", "structure", "No limitations or threats-to-validity statement found",
                         "Add a bounded limitations paragraph with concrete validity threats."))
    if not re.search(r"github|zenodo|artifact|\bcode\b|\bseed\b|reproduc|data availability", lower) and area_focus in ("all", "methods"):
        findings.append(("MEDIUM", "methods", "No reproducibility or data-availability statement found",
                         "Name the artifact, environment, seeds, and access route."))
    long_sentences = [sentence for sentence in sentences if len(sentence.split()) > 40]
    if long_sentences and area_focus in ("all", "writing"):
        findings.append(("LOW", "writing", f"{len(long_sentences)} sentence(s) exceed 40 words",
                         "Split long sentences so each carries one claim."))
    seen: set[str] = set()
    duplicates = 0
    for sentence in sentences:
        key = re.sub(r"\s+", " ", sentence.lower())
        if len(key) > 40 and key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates and area_focus in ("all", "writing"):
        findings.append(("LOW", "writing", f"{duplicates} repeated sentence(s) detected",
                         "Remove duplicated text and keep one authoritative statement."))
    findings.sort(key=lambda item: (_SEVERITY_ORDER.get(item[0], 3), item[1]))
    strengths = []
    if headings:
        strengths.append(f"Sections present: {', '.join(sorted(headings))}")
    evidence_marks = len(re.findall(r"\[(?:S|R)\d+\]|\\cite", text))
    strengths.append(f"Evidence markers found: {evidence_marks}")
    if re.search(r"algorithm|procedure|pseudocode", lower):
        strengths.append("Method is stated procedurally")
    lines = [
        "PAPER REVIEW",
        f"Focus: {area_focus} | Findings: {min(len(findings), cap)} of {len(findings)}",
        "",
        "STRENGTHS",
    ]
    lines.extend(f"- {item}" for item in strengths)
    lines += ["", "FINDINGS", "| Severity | Area | Finding | Fix |", "| --- | --- | --- | --- |"]
    for severity, area, finding, fix in findings[:cap]:
        lines.append(f"| {severity} | {area} | {_cell(finding)} | {_cell(fix)} |")
    if not findings:
        lines.append("| - | - | No deterministic findings | - |")
    lines += [
        "",
        "REVIEW LIMITS",
        "- Heuristic checks only; they do not verify truth, novelty, statistics, or venue policy.",
        "- A human reviewer and a real compile step are still required before submission.",
    ]
    return "\n".join(lines)


@register(
    "paper_project_export",
    "Write a complete Overleaf-ready paper folder with main.tex, references.bib, outline, study design, literature matrix, and LaTeX check report.",
    {"path": "destination folder", "title": "paper title", "question": "primary research question",
     "authors": "authors (optional)", "abstract": "abstract (optional)",
     "sections": "JSON list of {title, content} or 'Title | point, point' lines",
     "style": "ieee|acm|springer|plain (default ieee)", "keywords": "comma separated keywords (optional)",
     "evidence": "research brief for the literature matrix (optional)",
     "hypothesis": "primary hypothesis for the study design (optional)",
     "metric": "primary outcome metric (optional)", "bib": "BibTeX text for references.bib (optional)",
     "overwrite": "true to replace existing generated files (default false)"},
    ["path", "title", "question"],
    category="paper",
    dangerous=True,
    read_only=False,
)
def paper_project_export(path: str, title: str, question: str, authors: str = "", abstract: str = "",
                         sections: object = "", style: str = "ieee", keywords: str = "", evidence: str = "",
                         hypothesis: str = "", metric: str = "", bib: str = "", overwrite: bool | str = False) -> str:
    target = safety.validate_path(path)
    if target.exists() and not target.is_dir():
        raise ValueError("path must be a folder, not a file")
    source = _latex_document(title, authors, abstract, sections, style, keywords, "references")
    files: dict[str, str] = {
        "main.tex": source,
        "references.bib": str(bib or "").strip() or "@comment{Add verified BibTeX entries here before citing.}\n",
        "outline.md": paper_outline(title, question, evidence=evidence) + "\n",
        "study_design.md": paper_study_design(question, hypothesis=hypothesis, metric=metric, evidence=evidence) + "\n",
        "latex_check.txt": latex_check(source) + "\n",
        "README.md": (
            f"# {_text(title) or 'Research paper'}\n\n"
            "Overleaf setup:\n"
            "1. Upload this folder to a new Overleaf project.\n"
            "2. Compile main.tex with pdfLaTeX.\n"
            "3. Add verified BibTeX entries to references.bib, then cite them from the text.\n"
            "4. Replace the guide text in each section with evidence-backed content.\n"
            "5. Run latex_check again after editing.\n\n"
            f"Research question: {_text(question)}\n"
            f"Style: {str(style or 'ieee').strip().lower()}\n"
        ),
    }
    if _text(evidence):
        files["literature_review.md"] = literature_review(evidence, question) + "\n"
    targets = [(target / name, content) for name, content in files.items()]
    existing = [str(item) for item, _ in targets if item.exists()]
    if existing and not _flag(overwrite):
        raise FileExistsError("Existing generated file(s): " + ", ".join(existing) + ". Set overwrite=true to replace them.")
    preview = "\n".join(f"- {item.name}: {len(content)} chars" for item, content in targets)
    if not safety.require_confirmation(
        "Create Overleaf paper project?",
        f"{target}\n{preview}\n\nLocal text files will be written.",
        action="paper_project_export",
    ):
        raise PermissionError("Paper project creation denied by user")
    target.mkdir(parents=True, exist_ok=True)
    for item, content in targets:
        item.write_text(content, encoding="utf-8")
    listing = "\n".join(f"- {item}" for item, _ in targets)
    return (
        f"Created Overleaf paper project: {target}\nFiles:\n{listing}\n"
        "Next: upload the folder to Overleaf, add verified BibTeX entries, and replace guide text with evidence-backed writing."
    )
