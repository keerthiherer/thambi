from __future__ import annotations

import html
import io
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from .. import safety
from ..config import DATA_DIR, ensure_dirs
from ..memory import default as default_memory
from ..retrieval import chunk_text
from .registry import register

@register("research_url", "Fetch a user-authorised datasheet/URL, extract text, and index it locally with a citation. External access is disabled by default.",
          {"url": "http(s) URL", "index": "true to add to local index"}, ["url"],
          category="ece", dangerous=True, read_only=False)
def research_url(url: str, index: bool | str = True) -> str:
    url = safety.validate_http_url(url, "Research URL")
    parsed = urlparse(url)
    if not safety.require_external(
        "allow_external_research", "Fetch external source?",
        f"{url}\n\nThe page/PDF will be downloaded locally and indexed. No credentials or private files are sent."
    ):
        raise PermissionError("External fetch denied")
    try:
        r = requests.get(url, timeout=(10, 60), headers={"User-Agent": "THAMBI-local-research/0.1"},
                         stream=True, allow_redirects=True)
    except requests.RequestException as e:
        raise RuntimeError(f"Fetch failed: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
    ctype = r.headers.get("content-type", "").lower()
    data = b""
    for chunk in r.iter_content(65536):
        data += chunk
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("Response exceeds 20 MB limit")
    if "pdf" in ctype or url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:
            raise RuntimeError(f"PDF extraction failed: {e}") from e
    else:
        raw = data.decode("utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
        raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
        raw = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", raw)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text.strip()) < 40:
        raise RuntimeError("No usable text extracted (may require login/JavaScript).")
    title = parsed.netloc + parsed.path
    ensure_dirs()
    if index is True or str(index).lower() in ("1", "true", "yes"):
        mem = default_memory()
        path = f"url:{url}"
        chunks = chunk_text(text[:2_000_000])
        mem.upsert_document(path, 0, len(data), f"url:{url}", chunks)
        note = f"Indexed {len(chunks)} chunks locally."
    else:
        note = "Not indexed."
    return (f"SOURCE: {url}\nTITLE: {title}\n{note}\n\n"
            f"{text[:30000]}\n\n[Cite this source as {url}]")


def _research_angles(question: str, max_queries: int) -> list[str]:
    base = " ".join(str(question or "").split())
    lower = base.lower()
    angles = [base]
    if " vs " in lower or " versus " in lower:
        angles.extend([f"{base} comparison", f"{base} pros cons", f"{base} benchmark"])
    if lower.startswith(("how", "what", "why", "when", "which")):
        angles.extend([f"{base} explained", f"{base} guide", f"{base} examples"])
    angles.extend([
        f"{base} authoritative source",
        f"{base} best practices 2026",
        f"{base} research evidence",
        f"{base} common mistakes",
    ])
    out: list[str] = []
    seen: set[str] = set()
    for angle in angles:
        key = angle.lower()
        if angle and key not in seen:
            seen.add(key)
            out.append(angle)
        if len(out) >= max_queries:
            break
    return out


def _source_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _source_score(item: dict[str, str]) -> int:
    domain = _source_domain(item.get("url", ""))
    score = 0
    trusted = ("wikipedia.org", ".edu", ".gov", "nist.gov", "ieee.org", "arxiv.org",
               "mozilla.org", "microsoft.com", "developer.mozilla.org", "docs.python.org")
    if any(part in domain for part in trusted):
        score += 5
    if item.get("snippet"):
        score += 2
    if "docs" in domain or "documentation" in domain:
        score += 2
    if "pinterest" in domain or "quora" in domain:
        score -= 3
    return score


def _diverse_sources(items: list[dict[str, str]], max_sources: int) -> list[dict[str, str]]:
    ranked = sorted(enumerate(items), key=lambda pair: (-_source_score(pair[1]), pair[0]))
    chosen: list[dict[str, str]] = []
    domains: set[str] = set()
    for _, item in ranked:
        domain = _source_domain(item.get("url", ""))
        if domain and domain not in domains:
            chosen.append(item)
            domains.add(domain)
            if len(chosen) >= max_sources:
                return chosen
    for _, item in ranked:
        if item in chosen:
            continue
        chosen.append(item)
        if len(chosen) >= max_sources:
            break
    return chosen


def _fetch_research_text(url: str, max_chars: int) -> str:
    try:
        r = requests.get(url, timeout=(10, 45), headers={"User-Agent": "THAMBI-local-research/0.1"},
                         stream=True, allow_redirects=True)
    except requests.RequestException as e:
        raise RuntimeError(f"Fetch failed: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "").lower()
    data = b""
    for chunk in r.iter_content(65536):
        data += chunk
        if len(data) > 10 * 1024 * 1024:
            break
    if "pdf" in ctype or url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages[:20])
        except Exception as e:
            raise RuntimeError(f"PDF extraction failed: {e}") from e
    else:
        raw = data.decode("utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
        raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
        raw = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", raw)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) < 80:
        raise RuntimeError("No usable text extracted")
    return text[:max_chars]


@register("deep_web_research", "Research a question across multiple search angles and diverse domains, then index unique sources for citation.",
          {"question": "research question or topic",
           "queries": "number of search angles (default 4, max 6)",
           "sources": "number of distinct sources to use (default 5, max 8)",
           "index": "true to index sources into local memory (default true)"},
          ["question"], category="opencode", dangerous=True, read_only=False)
def deep_web_research(question: str, queries: int | str = 4, sources: int | str = 5,
                      index: bool | str = True) -> str:
    from .opencode_tools import _web_search_items

    base = " ".join(str(question or "").split())
    if not base:
        raise ValueError("Research question is required")
    try:
        query_count = min(6, max(1, int(queries)))
    except (TypeError, ValueError):
        query_count = 4
    try:
        source_count = min(8, max(2, int(sources)))
    except (TypeError, ValueError):
        source_count = 5
    angles = _research_angles(base, query_count)
    safety.require_external(
        "allow_external_research", "Run multi-source web research?",
        f"Question: {base[:500]}\nSearches: {len(angles)}\n"
        "Several public web searches and page fetches will be performed. No local files or private data are sent.",
    )
    pooled: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    search_lines: list[str] = []
    for angle in angles:
        try:
            items = _web_search_items(angle, 6)
        except RuntimeError as e:
            search_lines.append(f"- {angle}: failed ({e})")
            continue
        added = 0
        for item in items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                pooled.append(item)
                added += 1
        search_lines.append(f"- {angle}: {added} new result(s)")
    if not pooled:
        raise RuntimeError("Web research found no usable results")
    chosen = _diverse_sources(pooled, source_count)
    index_flag = index is True or str(index).lower() in ("1", "true", "yes")
    mem = default_memory() if index_flag else None
    evidence: list[str] = []
    source_lines: list[str] = []
    indexed = 0
    failures: list[str] = []
    for number, item in enumerate(chosen, 1):
        url = item.get("url", "")
        domain = _source_domain(url)
        title = item.get("title") or domain or url
        source_lines.append(f"[R{number}] {title}\n     {domain} | {url}\n     Why: {item.get('snippet', '')[:220]}")
        try:
            text = _fetch_research_text(url, 6000)
            if mem is not None:
                chunks = chunk_text(text[:1_000_000])
                mem.upsert_document(f"url:{url}", 0, len(text), f"url:{url}", chunks)
                indexed += 1
            evidence.append(f"[R{number}] {url}\n{text[:2600]}")
        except Exception as e:
            failures.append(f"[R{number}] {url}: {e}")
    domains = {_source_domain(item.get("url", "")) for item in chosen}
    lines = [
        "MULTI-SOURCE RESEARCH BRIEF",
        f"Question: {base}",
        f"Distinct domains: {len(domains)}",
        "",
        "SEARCH ANGLES:",
        "\n".join(search_lines),
        "",
        "SOURCE SET (diverse, deduplicated):",
        "\n".join(source_lines),
        "",
        "EXTRACTED EVIDENCE:",
        "\n\n".join(evidence) if evidence else "No pages could be fetched.",
    ]
    if failures:
        lines.extend(["", "FETCH FAILURES:", "\n".join("- " + f for f in failures)])
    lines.extend([
        "",
        "REPORTING RULES:",
        "- Use at least three distinct sources when the source set contains them.",
        "- Cite each distinct URL once with its [R#] handle, then list unique URLs under SOURCES.",
        "- Never repeat one URL as [S1] through [S8].",
        "- Separate verified facts from inference and note disagreements between sources.",
        "- If evidence is thin, say what is missing instead of guessing.",
        f"- Indexed {indexed} unique source(s) into local memory." if index_flag else "- Sources were not indexed.",
    ])
    if len(domains) < 3:
        lines.append("- WARNING: fewer than three distinct domains were available; state this limitation.")
    return "\n".join(lines)


_URL_RE = re.compile(r"https?://[^\s<>\"')\]}|,;]+")
_STOPWORDS = {"about", "after", "against", "based", "best", "from", "have", "into", "more", "most",
              "should", "than", "that", "their", "them", "there", "these", "this", "those", "through",
              "using", "what", "when", "where", "which", "while", "with", "would", "your"}
_TRUSTED_DOMAINS = ("wikipedia.org", ".edu", ".gov", "nist.gov", "ieee.org", "arxiv.org",
                    "docs.python.org", "developer.mozilla.org", "microsoft.com")


def _clean_url(url: str) -> str:
    return str(url).rstrip(".,;:)]}>\"'")


def _parse_sources(text: str) -> list[dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    handle_to_url: dict[str, str] = {}
    pending_handle = ""
    current_handle = ""
    in_evidence = False

    def source_for(url: str) -> dict[str, str]:
        if url not in sources:
            sources[url] = {"handle": "", "url": url, "domain": _source_domain(url),
                            "title": "", "snippet": "", "context": ""}
        return sources[url]

    for raw in str(text or "").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("EXTRACTED EVIDENCE"):
            in_evidence = True
            continue
        if upper.startswith(("SEARCH ANGLES", "SOURCE SET", "REPORTING RULES", "FETCH FAILURES")):
            in_evidence = False
        handle_match = re.match(r"^\[([RS]\d+)\]\s*(.*)$", line)
        if handle_match:
            handle, rest = handle_match.group(1), handle_match.group(2).strip()
            current_handle = handle
            url_match = _URL_RE.search(rest)
            if url_match:
                url = _clean_url(url_match.group(0))
                handle_to_url[handle] = url
                source = source_for(url)
                source["handle"] = handle
                if not source["title"]:
                    source["title"] = source["domain"]
            elif rest and not rest.startswith("Why:"):
                pending_handle = handle
            continue
        url_match = _URL_RE.search(line)
        if url_match:
            url = _clean_url(url_match.group(0))
            if pending_handle:
                handle_to_url[pending_handle] = url
                source_for(url)["handle"] = pending_handle
                pending_handle = ""
            source = source_for(url)
            if not source["title"]:
                source["title"] = source["domain"]
            if line.startswith("Why:"):
                source["snippet"] = line[4:].strip()[:500]
            current_handle = source["handle"] or _source_domain(url)
            continue
        if line.startswith("Why:") and current_handle in handle_to_url:
            source_for(handle_to_url[current_handle])["snippet"] = line[4:].strip()[:500]
        elif in_evidence and current_handle in handle_to_url and line and not line.startswith(("#", "-")):
            source = source_for(handle_to_url[current_handle])
            source["context"] = (source["context"] + "\n" + line).strip()[:4000]

    for index, (url, source) in enumerate(sources.items(), 1):
        if not source["handle"]:
            source["handle"] = f"R{index}"
        source["url"] = url
    return list(sources.values())


def _extract_claims(text: str) -> list[str]:
    skip_prefixes = ("MULTI-SOURCE", "Question:", "Distinct domains", "SEARCH ANGLES", "SOURCE SET",
                     "EXTRACTED EVIDENCE", "REPORTING RULES", "FETCH FAILURES", "- USE", "- CITE",
                     "- NEVER", "- SEPARATE", "- IF EVIDENCE", "- INDEXED", "- WARNING")
    claims: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.upper().startswith(skip_prefixes):
            continue
        if line.startswith("|"):
            continue
        line = re.sub(r"^\[[RS]\d+\]\s*", "", line)
        line = re.sub(r"^Why:\s*", "", line)
        line = re.sub(r"https?://\S+", "", line).strip()
        if len(line) < 30 or len(line) > 300:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            clean = " ".join(sentence.split())
            if 30 <= len(clean) <= 300 and clean not in claims:
                claims.append(clean)
    return claims


def _has_contradiction(text: str) -> bool:
    cues = ("however", "contrary", "contradict", "disagree", "but not", "failed", "no evidence",
            "not supported", "unsupported")
    lower = str(text or "").lower()
    return any(cue in lower for cue in cues)


def _dominant_confidence(source: dict[str, str], text: str) -> str:
    if _has_contradiction(text):
        return "low"
    if any(part in source["domain"] for part in _TRUSTED_DOMAINS) and source.get("context"):
        return "high"
    if source.get("context"):
        return "medium"
    return "low" if _has_contradiction(source.get("snippet", "")) else "medium"


@register("research_matrix", "Build an evidence matrix from a research brief or pasted sources.",
          {"brief": "research brief, deep_web_research output, or pasted source notes",
           "max_rows": "maximum claim rows (default 12, max 25)"}, ["brief"], category="research")
def research_matrix(brief: str, max_rows: int | str = 12) -> str:
    text = str(brief or "")
    sources = _parse_sources(text)
    claims = _extract_claims(text)
    if not sources:
        return ("RESEARCH MATRIX\nNo URLs or [R#]/[S#] source handles were found. "
                "Run /web first or paste a research brief containing source URLs.")
    if not claims:
        claims = [f"Source summary: {s['snippet'] or s['title']}" for s in sources if s.get("snippet") or s.get("title")]
    try:
        limit = min(25, max(1, int(max_rows)))
    except (TypeError, ValueError):
        limit = 12
    rows = [
        "| # | Claim / evidence | Handle | Domain | Confidence |",
        "|---|---|---|---|---|",
    ]
    for index, claim in enumerate(claims[:limit], 1):
        source = sources[(index - 1) % len(sources)]
        rows.append(f"| {index} | {claim[:220]} | {source['handle']} | {source['domain']} | "
                    f"{_dominant_confidence(source, text)} |")
    domains = sorted({s["domain"] for s in sources})
    contradictions = "Detected; treat low-confidence rows as disputed." if _has_contradiction(text) else "No contradiction cues detected."
    gaps: list[str] = []
    if len(domains) < 3:
        gaps.append(f"Only {len(domains)} distinct domain(s); add more independent sources.")
    if len(claims) < len(sources):
        gaps.append("Some sources have no extracted claim; fetch fuller evidence or rewrite the brief.")
    source_context = [s for s in sources if s.get("context")]
    if not source_context:
        gaps.append("Evidence is snippet-only; render /web again with page fetches for stronger claims.")
    lines = [
        "RESEARCH MATRIX",
        f"Sources: {len(sources)} | Distinct domains: {len(domains)} | Claims: {min(len(claims), limit)}",
        "",
        "\n".join(rows),
        "",
        "CONTRADICTIONS",
        contradictions,
        "",
        "SOURCE HANDLES",
        "\n".join(f"[{s['handle']}] {s['title']} - {s['url']}" for s in sources),
        "",
        "GAPS",
        "\n".join(f"- {gap}" for gap in gaps) if gaps else "- No obvious coverage gap from the supplied brief.",
    ]
    return "\n".join(lines)


@register("claim_verifier", "Check how well a claim is supported by evidence from distinct sources.",
          {"claim": "claim to verify", "evidence": "research brief or pasted evidence with URLs",
           "min_sources": "distinct supporting domains required (default 2, max 5)"},
          ["claim", "evidence"], category="research")
def claim_verifier(claim: str, evidence: str, min_sources: int | str = 2) -> str:
    statement = " ".join(str(claim or "").split())
    if not statement:
        raise ValueError("Claim is required")
    text = str(evidence or "")
    sources = _parse_sources(text)
    tokens = {word for word in re.findall(r"[a-z0-9]+", statement.lower())
              if len(word) >= 4 and word not in _STOPWORDS}
    if not tokens:
        tokens = {word for word in re.findall(r"[a-z0-9]+", statement.lower()) if len(word) > 2}
    try:
        required = min(5, max(1, int(min_sources)))
    except (TypeError, ValueError):
        required = 2
    scored: list[tuple[float, dict[str, str], bool, str]] = []
    for source in sources:
        haystack = " ".join([source["title"], source["snippet"], source["context"]]).lower()
        overlap = sum(1 for token in tokens if token in haystack) / max(1, len(tokens))
        matched = sorted(token for token in tokens if token in haystack)
        negated = overlap >= 0.25 and _has_contradiction(haystack)
        scored.append((overlap, source, negated, ", ".join(matched[:8])))
    supporting = [item for item in scored if item[0] >= 0.34 and not item[2]]
    partial = [item for item in scored if 0.18 <= item[0] < 0.34 and not item[2]]
    support_domains = {item[1]["domain"] for item in supporting}
    if not sources:
        verdict = "INSUFFICIENT EVIDENCE"
    elif len(support_domains) >= required:
        verdict = "SUPPORTED BY PROVIDED EVIDENCE"
    elif supporting and len(support_domains) >= max(1, required - 1):
        verdict = "PARTIALLY SUPPORTED"
    elif partial:
        verdict = "WEAK OR INDIRECT SUPPORT"
    else:
        verdict = "NOT SUPPORTED BY PROVIDED EVIDENCE"
    lines = [
        "CLAIM VERIFICATION",
        f"Claim: {statement}",
        f"Verdict: {verdict}",
        f"Required distinct supporting domains: {required}",
        f"Supporting domains found: {len(support_domains)}",
        "",
        "SOURCE SCORES",
    ]
    if scored:
        for overlap, source, negated, matched in scored:
            status = "contradiction cue" if negated else ("support" if overlap >= 0.34 else "weak" if overlap >= 0.18 else "no match")
            lines.append(f"- [{source['handle']}] {source['domain']}: overlap={overlap:.0%} | {status} | "
                         f"matched: {matched or 'none'} | {source['url']}")
    else:
        lines.append("- No parseable sources found.")
    lines.extend([
        "",
        "LIMITATIONS",
        "- This is lexical evidence checking, not a truth judgment or external fact-check.",
        "- Verify primary sources and note disagreements before presenting the claim as fact.",
        "- If the verdict is insufficient, run /web or add more independent sources.",
    ])
    return "\n".join(lines)


def _write_report_docx(path: Path, title: str, content: str) -> None:
    try:
        import docx
    except ImportError as e:
        raise RuntimeError("python-docx is not installed") from e
    document = docx.Document()
    document.add_heading(title, level=0)
    for line in content.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:].strip(), level=1)
        elif line.startswith("- "):
            document.add_paragraph(line[2:], style="List Bullet")
        else:
            document.add_paragraph(line)
    document.save(str(path))


def _write_report_pdf(path: Path, title: str, content: str) -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as e:
        raise RuntimeError("reportlab is not installed") from e
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(str(path), pagesize=A4, margins=(18 * mm, 18 * mm, 18 * mm, 18 * mm))
    story = [Paragraph(title, styles["Title"]), Spacer(1, 8)]
    for line in content.splitlines():
        if line.strip():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, styles["BodyText"]))
            story.append(Spacer(1, 4))
    document.build(story)


@register("research_report_export", "Export a cited research brief, evidence matrix, and limitations to MD, DOCX, or PDF.",
          {"path": "output path", "title": "report title", "question": "research question",
           "evidence": "research brief or pasted evidence", "format": "md|docx|pdf (default md)"},
          ["path", "title", "question", "evidence"], category="research",
          dangerous=True, read_only=False)
def research_report_export(path: str, title: str, question: str, evidence: str,
                           format: str = "md") -> str:
    fmt = str(format or "md").strip().lower().lstrip(".")
    if fmt not in ("md", "docx", "pdf"):
        raise ValueError("format must be md, docx, or pdf")
    p = safety.validate_write(path)
    if p.suffix.lower() != f".{fmt}":
        p = p.with_suffix(f".{fmt}")
    content = "\n".join([
        f"# {title}",
        "",
        f"Question: {' '.join(str(question or '').split())}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Method",
        "- Evidence was assembled from the supplied research brief and cited URLs.",
        "- Unique URLs are listed once. Claims are separated from inference.",
        "- Contradictions, source gaps, and confidence limits are reported explicitly.",
        "",
        "## Evidence",
        str(evidence or "").strip(),
        "",
        "## Research Matrix",
        research_matrix(evidence),
        "",
        "## Limitations",
        "- Lexical checks are advisory; verify primary and authoritative sources before publication.",
        "- A single-domain source set is a limitation, not a consensus.",
    ])
    if not safety.require_confirmation(
        "Create research report?",
        f"{p}\nFormat: {fmt}\nLength: {len(content)} chars\n\nA local report will be written.",
        action="research_report_export",
    ):
        raise PermissionError("Research report creation denied by user")
    p.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        p.write_text(content, encoding="utf-8")
    elif fmt == "docx":
        _write_report_docx(p, title, content)
    else:
        _write_report_pdf(p, title, content)
    return f"Created research report: {p}\nFormat: {fmt}\nLength: {len(content)} chars"
