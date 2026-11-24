from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Callable

from . import learning
from .config import get, load
from .hardware import probe
from .llm import LlamaServer, LLMError, _clip_text, prepare_context, stream
from .memory import Memory, default as default_memory
from .retrieval import retrieve
from .tools import execute, parse_tool_calls, specs_text, tool_parse_errors

BASE_SYSTEM = """You are THAMBI (Tamil for "little brother"), a private offline AI assistant running locally on the user's ASUS TUF F15.
You serve the user personally and as CEO of Melouz Technologies.

Working method (mandatory):
1. Understand the request and separate what is known from what must be checked.
2. Inspect real local evidence with tools before answering about files, code, data, hardware, documents, or company facts.
3. Act through registered tools only. Make the smallest safe change that solves the problem.
4. Verify: syntax-check or test changed code, re-read edited files, and inspect TOOL RESULT before claiming success.
5. Report what you actually observed, the verification outcome, and any remaining uncertainty.

Conversation continuity (mandatory):
- CONVERSATION HISTORY contains earlier turns of this same chat. Use it when the user refers to anything said before ("he", "that", "my name", "above", "continue"). Never start a fresh answer when history contains the needed detail.
- If the user asks to clear or start a new chat, that history is reset and no earlier detail applies.
- Do not call ask_user for information already present in CONVERSATION HISTORY or LOCAL SOURCES; answer from that evidence instead.

Accuracy rules (mandatory):
- Prefer the provided local sources. Cite them as [S1], [S2] exactly when used.
- If sources do not contain the answer, state what is missing. Never invent datasheet values, part specifications, pinouts, prices, test results, citations, file paths, line numbers, or company facts.
- Never claim that a file was read, code was applied, a tool succeeded, or tests passed unless a TOOL RESULT confirms it.
- Distinguish measured fact, vendor specification, estimate, and assumption. Label uncertainty explicitly.
- When evidence conflicts, say so and give the quickest way to resolve it. Do not guess to fill gaps.

Error handling (mandatory):
- When a TOOL ERROR appears, stop and report it to the user with the exact tool name, ERROR TYPE, and ERROR MESSAGE from that result.
- Report each distinct error only once; do not repeat the same tool name, error type, and message in the answer.
- Do not paraphrase, guess, hide, retry blindly, or claim a different cause. Only say "file not found" if the error actually says FileNotFoundError.
- If a tool call is malformed or has unknown arguments, report the validation message verbatim, then correct it only if the fix is obvious from that message.
- Do not start dependent actions after a failed tool until the error has been reported or resolved from evidence.

Safety rules (mandatory):
- Never send or delete email.
- Never submit forms, make payments, install software, flash hardware, delete/overwrite files, or run destructive/administrative commands without the user's explicit confirmation.
- Never execute shell commands invented by the model. Only call the registered tools.
- Keep private data local. External access must be explicitly enabled and confirmed by the user.

Web research discipline (mandatory):
- For current, comparative, or open-ended questions, call deep_web_research instead of relying on a single web_search call.
- Use several search angles and prefer at least three distinct domains. Cite each distinct URL once with its [R#] handle, not the same URL repeatedly.
- Calls to deep_web_research and research_url index unique URLs into local memory; retrieve groups each unique URL under one [S#] handle. Cite [S#] for indexed evidence and [R#] for handles printed inside the research brief.
- Do not reuse a citation handle for a different URL. Never repeat one URL as [S1] through [S8]. Do not list the same URL more than once under SOURCES.
- If only one domain is available, say so explicitly and limit conclusions. If web sources disagree, name the disagreement and prefer primary/authoritative sources.
- For a single narrow lookup, web_search is allowed. Use only exact http(s) URLs copied verbatim from tool results or provided by the user; never guess or complete URLs.
- Never use example.com, example.org, example.net, localhost, or placeholder URLs. If a URL fails, report the exact error once and answer from remaining evidence.

Quality loop (mandatory when relevant):
- After deep_web_research, use research_matrix to structure evidence and claim_verifier to test important claims. Use research_report_export only when the user wants a saved report.
- After code changes, run code_review and security_scan on changed files, then use test_generator and run_tests when practical.
- When designing a new tool, use tool_spec_builder to create a reviewed draft; never claim it is registered until the registry import and tests are confirmed.

Innovation loop (mandatory when relevant):
- For new ideas or products, run prior_art_search or patent_search before claiming novelty. Never claim patentability or freedom to operate.
- Use idea_lab for structured divergent options, experiment_designer to turn the best option into a test, and decision_matrix for weighted tradeoffs.
- Mark heuristic scores as advisory and validate them with real evidence before recommending a commitment.

Tool interoperability: THAMBI uses UTCP (Universal Tool Calling Protocol), not MCP. Local tools are
exposed at the UTCP gateway; external tool providers are reached through utcp_list/utcp_call in research mode.
When internet access is enabled, use internet_status, deep_web_research, web_search, web_fetch, research_url, and browser_open for
current information; every external action still requires the user's confirmation. If a tool says external access
is disabled, report that exact error instead of pretending the lookup succeeded.

Adaptive learning: a separate system message may contain rules learned from this user. Apply them when relevant,
but safety rules, UTCP/tool results, and the user's latest explicit request take priority.

Tool protocol:
If an action/tool is needed, respond with ONLY one fenced block in this exact form and no other text:
```tool
{{"name": "tool_name", "args": {{"arg": "value"}}}}
```
Use exact tool names and argument names from the list. Never invent a tool or argument. Tools marked
"needs confirmation" require the user to approve before they run; if denied, report that plainly.
Available tools:
{tools}

After a TOOL RESULT is supplied, answer the user using it. If no tool is needed, answer directly. Be concise unless the task requires detail.
Active module: {module}. Local date: {date}."""

MODULES = {
    "chat": "General assistant. Be practical and concise.",
    "coding": ("Senior software engineer. Follow plan -> inspect -> change -> verify. Use create_file/write_file to "
               "create code files, create_folder for directories, create_venv for Python environments, and "
               "venv_pip_install for requirements. Use search_in_file and read_many_files to inspect real code, "
               "patch_file for targeted edits, code_review and security_scan after changes, test_generator for missing coverage, "
               "python_syntax_check for quick validation, and run_tests for final verification. Use tool_spec_builder for new tool drafts. Cite file:line for findings. Never claim code was "
               "applied or tests passed unless a TOOL RESULT confirms it. Return complete, runnable code or diffs, "
               "and finish with the exact verification result."),
    "ece": ("Electronics/embedded engineer. Use ece_calculate for math, kicad_inspect for boards, "
            "bom_analyze for BOMs, and ngspice_run only when installed. Cite datasheets/sources. "
            "Never guess component ratings, pinouts, or RF results."),
    "desktop": ("Windows desktop agent. Use approved tools only. Read-only inspection is automatic; "
                "all launches, writes, typing, and deletions require confirmation."),
    "ceo": ("Executive assistant for Melouz Technologies. Draft professional emails/reports, summarise "
            "local email, manage local calendar/tasks, analyse resumes against a job description, and prepare "
            "quotations/invoices with validated arithmetic. Email is draft-only: never send or delete."),
    "designer": (
        "Executive presentation designer and visual art director. Create high-impact, polished decks that look "
        "premium and stay error-free. Lead with one takeaway per slide; write short, confident headlines; use "
        "cards, stats, timelines, charts, quotes and comparisons instead of dense paragraphs. Apply strong "
        "hierarchy, consistent 0.7-inch margins, generous whitespace, contrast, repetition and alignment. "
        "Use premium for luxury/high-end decks. For any normal deck request, call design_pitch_deck ONCE with "
        "a simple brief: topic, audience, theme, and slide count. It builds the full spec and calls pptx_create "
        "for you; never hand-build a JSON spec unless the user explicitly supplies one. "
        "If pptx_create fails with JSONDecodeError, ToolParseError, or RepeatedToolFailure, recover immediately "
        "by calling design_pitch_deck with a simple brief instead of repeating the failed call. "
        "Do not ask the user to confirm file creation or paste a path; the tool confirmation prompt is the "
        "confirmation. Copy the full path from the CREATED PPTX PATH line into pptx_inspect and open_presentation. "
        "Never invent placeholders such as <path_to_deck>.pptx. If no path is available, pptx_inspect resolves "
        "a placeholder to the newest generated deck. Call pptx_inspect to verify the deck after creation. "
        "Use open_design_app for Canva or PowerPoint and open_presentation to show the file only when asked. "
        "For edits such as 'add new pages in between', call pptx_add_slides with the existing deck path, "
        "after_slide, and plain 'Title | point one, point two' lines. Never hand-build a full JSON spec for edits. "
        "If a tool block has malformed JSON, a huge escaped spec string, or a RepeatedToolFailure, recover by "
        "calling design_pitch_deck for a new deck or pptx_add_slides for an edit; the parser also salvages "
        "malformed spec strings automatically. Never invent metrics; label placeholders clearly. Do not claim "
        "a deck is created or verified unless a TOOL RESULT confirms it."
    ),
    "voice": "Spoken assistant. Keep answers short and clear; avoid markdown tables and long lists.",
    "psychology": (
        "Emotion-aware behaviour and wellbeing coach. Use emotion_analyze, psychology_profile, behavior_profile, "
        "coaching_plan, wellbeing_checkin, empathy_reply, and conflict_mediation. Reflect emotions without diagnosing, "
        "separate observation from interpretation, label profiles as tentative hypotheses, respect privacy and "
        "boundaries, and support professional help when risk or clinical needs appear. Never manipulate, guilt-trip, "
        "or infer protected traits. Ask one useful question at a time."
    ),
    "hr": (
        "HR business partner and people-operations assistant. Use hr_interview_kit, hr_performance_review, "
        "hr_feedback_message, and the psychology tools for structured, bias-aware people work. Base every statement "
        "on supplied evidence, use STAR and SBI frameworks, avoid protected or irrelevant traits, and never make "
        "automated hiring, rating, pay, promotion, or termination decisions. Always leave the final judgement to "
        "accountable humans and local policy."
    ),
    "research": (
        "Deep research and reasoning partner for the user's research, engineering projects, startups, "
        "and hackathons. Treat every question as a reasoning task: restate the real question, surface "
        "assumptions, decompose it into sub-problems, compare options with explicit tradeoffs, and then give "
        "a clear recommendation. Use deep_web_research, research_matrix, and claim_verifier for evidence-heavy work; "
        "use prior_art_search, patent_search, idea_lab, experiment_designer, and decision_matrix for innovation work; "
        "export a report only when asked. Separate local evidence [S#] from inference and speculation. Design concrete "
        "experiments, validation steps, milestones, risks, and next actions. For hackathons produce a scoped "
        "build plan with a demo narrative, tech stack, timeboxes, and judging-criteria alignment. Be rigorous, "
        "specific, and honest about uncertainty. Never invent papers, citations, results, or company facts."
    ),
}


MODULE_CATEGORIES = {
    "chat": ["files", "memory", "coding", "research", "innovation", "documents", "opencode", "learning"],
    "coding": ["coding", "research", "innovation", "files", "memory", "opencode", "learning"],
    "ece": ["ece", "research", "innovation", "files", "memory", "documents", "opencode", "learning"],
    "desktop": ["desktop", "research", "innovation", "files", "memory", "documents", "opencode", "learning"],
    "ceo": ["ceo", "research", "innovation", "office", "files", "memory", "documents", "opencode", "learning"],
    "designer": ["designer", "research", "innovation", "documents", "files", "opencode", "memory", "learning"],
    "voice": ["research", "innovation", "files", "memory", "ece", "ceo", "desktop", "opencode", "learning"],
    "psychology": ["psychology", "research", "innovation", "documents", "memory", "opencode", "learning"],
    "hr": ["hr", "psychology", "research", "innovation", "office", "documents", "memory", "opencode", "learning"],
    "research": ["research", "innovation", "files", "memory", "coding", "ece", "opencode", "documents", "learning"],
}


def build_system(module: str = "chat", compact: bool = False) -> str:
    cats = MODULE_CATEGORIES.get(module)
    tools = specs_text(cats, compact=compact)
    return BASE_SYSTEM.format(
        tools=tools,
        module=MODULES.get(module, MODULES["chat"]),
        date=datetime.now().strftime("%Y-%m-%d"),
    )


def _source_block(sources: list[dict], context: str) -> str:
    if not context:
        return ("LOCAL SOURCES: none matched. If the user asks about their documents, project, or company, "
                "state that no local source was found instead of inventing details.")
    return ("LOCAL RETRIEVAL RESULTS (cite with [S#]):\n\n" + context)


def _clean_tool_text(text: str) -> str:
    text = re.sub(r"```tool\s*\{.*?\}\s*```", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<tool>\s*\{.*?\}\s*</tool>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _format_tool_result(name: str, result: dict) -> str:
    limit = max(3000, int(get("memory.max_message_chars", 6000)) * 2)
    if result.get("ok"):
        r = result.get("result")
        body = r if isinstance(r, str) else json.dumps(r, indent=2, ensure_ascii=False)
        return f"TOOL RESULT ({name}):\n{_clip_text(body, limit)}"
    error_type = str(result.get("error_type") or "ToolError")
    message = str(result.get("message") or result.get("error") or "unknown error")
    return (f"TOOL ERROR ({name}):\n"
            f"ERROR TYPE: {error_type}\n"
            f"ERROR MESSAGE: {_clip_text(message, limit)}\n"
            "REQUIRED RESPONSE: report this exact tool name, error type and error message to the user. "
            "Do not guess or continue dependent actions until it is resolved.")


def _append_tool_errors(text: str, tool_results: list[dict]) -> str:
    failed = [t for t in tool_results if not t.get("ok")]
    if not failed:
        return text
    counts: dict[tuple[str, str, str], int] = {}
    for item in failed:
        key = (
            str(item.get("name") or "tool"),
            str(item.get("error_type") or "ToolError"),
            str(item.get("message") or item.get("error") or "unknown error"),
        )
        counts[key] = counts.get(key, 0) + 1
    lines = []
    for (name, error_type, message), count in counts.items():
        line = f"- {name}: {error_type}: {message}"
        if count > 1:
            line += f" (repeated {count} times)"
        if message not in text:
            lines.append(line)
    if not lines:
        return text
    return text.rstrip() + "\n\nTool errors encountered:\n" + "\n".join(lines)


def _stream_answer(messages: list[dict], on_token: Callable[[str], None] | None,
                   server: LlamaServer, stop_event: threading.Event | None,
                   gen_kwargs: dict, max_continuations: int, context: int) -> dict:
    work = list(messages)
    parts: list[str] = []
    usage: dict = {}
    finish = None
    kwargs = dict(gen_kwargs)
    def cb(tok: str) -> None:
        if on_token:
            on_token(tok)
    for attempt in range(max_continuations + 1):
        resp = stream(work, cb, server, stop_event, **kwargs)
        piece = resp.get("text") or ""
        parts.append(piece)
        usage = resp.get("usage") or usage
        finish = resp.get("finish_reason")
        if resp.get("stopped") or (stop_event is not None and stop_event.is_set()):
            break
        if finish != "length" or attempt >= max_continuations or len(piece.strip()) < 120:
            break
        used = int((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
        remaining = context - used - 256
        if remaining < 300:
            break
        kwargs = dict(kwargs)
        kwargs["max_tokens"] = min(int(kwargs.get("max_tokens", 2048)), remaining)
        work = work + [
            {"role": "assistant", "content": piece},
            {"role": "user", "content": ("Continue the previous answer from exactly where it stopped. Do not repeat "
                                          "any text. If a tool JSON block was unfinished, complete it.")},
        ]
    return {"text": "".join(parts), "usage": usage, "finish_reason": finish}


class ChatEngine:
    def __init__(self, server: LlamaServer | None = None, mem: Memory | None = None):
        self.server = server or LlamaServer()
        self.mem = mem or default_memory()
        self.session_id = uuid.uuid4().hex[:12]

    def command(self, text: str) -> dict | None:
        if not text.startswith("/"):
            return None
        parts = text[1:].strip().split(maxsplit=1)
        if not parts:
            return None
        cmd = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "help":
            return {"text": HELP, "sources": [], "command": True}
        if cmd == "remember":
            if not rest:
                return {"text": "Usage: /remember <rule>", "sources": [], "command": True}
            rule_id, created = learning.teach(rest, mem=self.mem)
            return {"text": f"{'Learned' if created else 'Updated'} rule #{rule_id}: {rest}",
                    "sources": [], "command": True}
        if cmd == "forget":
            if not rest:
                return {"text": "Usage: /forget <id or matching text>", "sources": [], "command": True}
            count = learning.forget(rest, mem=self.mem)
            return {"text": f"Removed {count} learned rule(s).", "sources": [], "command": True}
        if cmd == "learned":
            rules = self.mem.list_learned_rules(limit=50)
            body = "\n".join(f"[{r['id']}] ({r['scope']}, {r['confidence']:.2f}) {r['rule']}" for r in rules)
            return {"text": body or "No learned rules yet.", "sources": [], "command": True}
        if cmd == "status":
            h = probe()
            st = self.mem.index_stats()
            srv = self.server.memory_snapshot()
            active = str(self.server.active_model.name) if self.server.active_model else "not loaded"
            return {"text": (f"Model: {active}\nllama.cpp: {self.server.base_url} "
                             f"({self.server.health() or 'down'})\nProcess: {srv}\n"
                             f"RAM: {h['ram']['available_gb']} GB free / {h['ram']['total_gb']} GB\n"
                             f"GPU: " + ", ".join(f"{g['name']} {g.get('vram_free_mb')} MB free" for g in h["gpus"]) +
                             f"\nIndex: {st['documents']} docs / {st['chunks']} chunks / {st['memories']} memories"),
                    "sources": [], "command": True}
        if cmd == "hw":
            return {"text": json.dumps(probe(), indent=2), "sources": [], "command": True}
        if cmd == "search":
            if not rest:
                return {"text": "Usage: /search <query>", "sources": [], "command": True}
            ctx, src = retrieve(rest, self.mem)
            if not ctx:
                return {"text": "No local document or memory match.", "sources": [], "command": True}
            return {"text": ctx, "sources": src, "command": True}
        if cmd == "index":
            args = {"path": rest} if rest else {}
            if not rest:
                from .retrieval import index_configured
                report = index_configured(self.mem)
                return {"text": json.dumps(report, indent=2), "sources": [], "command": True}
            return self._command_tool("index_folder", args)
        if cmd == "read":
            return self._command_tool("read_file", {"path": rest})
        if cmd == "doc":
            return self._command_tool("read_document", {"path": rest})
        if cmd == "calc":
            try:
                name, _, params = rest.partition(" ")
                args = {"calculation": name, "parameters": json.loads(params) if params else {}}
            except json.JSONDecodeError:
                return {"text": "Usage: /calc rc_filter {\"r\":10000,\"c\":1e-7}", "sources": [], "command": True}
            return self._command_tool("ece_calculate", args)
        if cmd == "kicad":
            return self._command_tool("kicad_inspect", {"path": rest})
        if cmd == "bom":
            return self._command_tool("bom_analyze", {"path": rest})
        if cmd == "sim":
            return self._command_tool("simulator_status", {})
        if cmd == "tasks":
            return self._command_tool("list_tasks", {})
        if cmd == "task":
            return self._command_tool("add_task", {"title": rest}) if rest else \
                {"text": "Usage: /task <title>", "sources": [], "command": True}
        if cmd == "emails":
            return self._command_tool("search_email", {"query": rest}) if rest else \
                {"text": "Usage: /emails <query>", "sources": [], "command": True}
        if cmd == "email-import":
            return self._command_tool("import_email", {"path": rest})
        if cmd == "calendar":
            return self._command_tool("list_calendar", {})
        if cmd == "ppt":
            return self._command_tool("pptx_template", {"theme": rest or "midnight"})
        if cmd == "venv":
            return self._command_tool("create_venv", {"path": rest or ".venv"})
        if cmd == "open":
            return self._command_tool("open_app", {"name": rest})
        if cmd == "internet":
            return self._command_tool("internet_status", {})
        if cmd == "web":
            return self._command_tool("deep_web_research", {"question": rest}) if rest else \
                {"text": "Usage: /web <research question>", "sources": [], "command": True}
        if cmd == "websearch":
            return self._command_tool("web_search", {"query": rest}) if rest else \
                {"text": "Usage: /websearch <quick query>", "sources": [], "command": True}
        if cmd == "openurl":
            return self._command_tool("browser_open", {"url": rest}) if rest else \
                {"text": "Usage: /openurl <http(s) URL>", "sources": [], "command": True}
        if cmd == "utcp":
            from .utcp import build_manual
            return {"text": json.dumps(build_manual(), indent=2), "sources": [], "command": True}
        if cmd == "tool":
            name, _, payload = rest.partition(" ")
            try:
                args = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return {"text": "Usage: /tool <name> {\"arg\":\"value\"}", "sources": [], "command": True}
            return self._command_tool(name, args)
        if cmd == "clear":
            self.session_id = uuid.uuid4().hex[:12]
            return {"text": "", "sources": [], "command": True, "clear": True,
                    "session_id": self.session_id}
        return {"text": f"Unknown command /{cmd}. Type /help.", "sources": [], "command": True}

    def _command_tool(self, name: str, args: dict) -> dict:
        res = execute(name, args, allow_dangerous=True)
        body = res.get("result") if res.get("ok") else res.get("error")
        if not isinstance(body, str):
            body = json.dumps(body, indent=2, ensure_ascii=False)
        self.mem.add_message(self.session_id, "user", f"/{name} {json.dumps(args, ensure_ascii=False)}")
        self.mem.add_message(self.session_id, "assistant", body)
        return {"text": body, "sources": [], "command": True}

    def reply(self, user_text: str, module: str = "chat",
              on_token: Callable[[str], None] | None = None,
              on_status: Callable[[str], None] | None = None,
              stop_event: threading.Event | None = None) -> dict:
        research = user_text.strip()
        if research.lower().startswith("/research"):
            research = research[len("/research"):].strip()
            if not research:
                return {"text": "Usage: /research <question about your research, project, or hackathon>",
                        "sources": [], "command": True}
            user_text = research
            module = "research"
        else:
            mode_switches = (
                ("/psychology", "psychology"), ("/psych", "psychology"), ("/coach", "psychology"),
                ("/hr", "hr"), ("/people", "hr"),
            )
            for prefix, target in mode_switches:
                if research.lower().startswith(prefix):
                    remainder = research[len(prefix):].strip()
                    if not remainder:
                        return {"text": f"Usage: {prefix} <question, message, or situation>",
                                "sources": [], "command": True}
                    user_text = remainder
                    module = target
                    break
        cmd = self.command(user_text)
        if cmd is not None:
            return cmd
        learning.observe(user_text, self.mem, module, self.session_id)
        if on_status:
            on_status("Retrieving local context ...")
        ctx_text, sources = retrieve(user_text, self.mem)
        context = int(self.server.context or get("llama.context", 8192))
        system = build_system(module, compact=context <= 8192)
        learned = learning.rules_block(self.mem, module)
        if learned:
            system += "\n\n" + learned
        history_turns = int(get("memory.history_turns", 10))
        history = self.mem.recent_messages(self.session_id, history_turns * 2)
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": _source_block(sources, ctx_text)},
        ]
        if history:
            messages.append({"role": "system",
                             "content": "CONVERSATION HISTORY (earlier turns of this chat; use it for follow-ups):"})
        messages.extend({"role": h["role"], "content": h["content"]} for h in history)
        messages.append({"role": "user", "content": user_text})
        gen_kwargs: dict = {}
        if module == "coding":
            gen_kwargs["max_tokens"] = int(get("generation.coding_max_tokens", 3072))
            gen_kwargs["temperature"] = float(get("generation.coding_temperature", 0.1))
        elif module == "research":
            gen_kwargs["max_tokens"] = int(get("generation.research_max_tokens", 3000))
            gen_kwargs["temperature"] = float(get("generation.research_temperature", 0.3))
        elif module in ("psychology", "hr"):
            gen_kwargs["max_tokens"] = int(get("generation.research_max_tokens", 3000))
            gen_kwargs["temperature"] = 0.35
        messages = prepare_context(messages, max_tokens=gen_kwargs.get("max_tokens"), context=context)

        self.mem.add_message(self.session_id, "user", user_text)
        started = time.time()
        tool_results: list[dict] = []
        repeated_failures: dict[tuple, int] = {}
        final_text = ""
        usage: dict = {}
        finish = None
        max_continuations = max(0, int(get("generation.max_continuations", 1)))
        max_rounds = 5 if module in ("coding", "research", "designer", "psychology", "hr") else 3
        for rnd in range(max_rounds):
            if stop_event is not None and stop_event.is_set():
                break
            if on_status:
                on_status("Thinking locally ..." if rnd == 0 else "Using tool result ...")
            try:
                resp = _stream_answer(messages, on_token, self.server, stop_event,
                                      gen_kwargs, max_continuations, context)
            except LLMError:
                raise
            text = resp["text"]
            usage = resp.get("usage") or usage
            finish = resp.get("finish_reason")
            parse_errors = tool_parse_errors(text)
            calls = parse_tool_calls(text)
            if not calls and not parse_errors:
                final_text = text
                break
            if on_status:
                on_status("Tool call error - report it exactly" if parse_errors else
                          f"Requesting tool: {calls[0]['name']}")
            messages.append({"role": "assistant", "content": _clip_text(text, 12000)})
            for err in parse_errors:
                tr = {"name": "tool_parser", "ok": False, "error_type": "ToolParseError",
                      "message": err, "error": f"ToolParseError: {err}"}
                tool_results.append(tr)
                messages.append({"role": "user", "content": _format_tool_result("tool_parser", tr)})
            for call in calls[:3]:
                args = call.get("args") or {}
                args_json = json.dumps(args, sort_keys=True, ensure_ascii=False)
                prefix = (call["name"], args_json)
                if any(key[:2] == prefix and count >= 2 for key, count in repeated_failures.items()):
                    res = {
                        "ok": False,
                        "error_type": "RepeatedToolFailure",
                        "message": (f"Repeated call to {call['name']} with identical arguments was suppressed "
                                    "after 2 failures. Recover with a different tool or answer from available "
                                    "evidence; do not ask the user to re-confirm."),
                    }
                else:
                    res = execute(call["name"], args, allow_dangerous=True)
                tr = {"name": call["name"], "ok": bool(res.get("ok")),
                      "result": res.get("result"), "error": res.get("error"),
                      "error_type": res.get("error_type"), "message": res.get("message")}
                tool_results.append(tr)
                messages.append({"role": "user", "content": _format_tool_result(call["name"], res)})
                if not res.get("ok"):
                    signature = (
                        call["name"],
                        args_json,
                        str(res.get("error_type") or "ToolError"),
                        str(res.get("message") or res.get("error") or ""),
                    )
                    repeated_failures[signature] = repeated_failures.get(signature, 0) + 1
            if any(count >= 2 for count in repeated_failures.values()):
                failed_names = {key[0] for key in repeated_failures}
                designer_failure = bool(failed_names & {
                    "pptx_create", "pptx_template", "pptx_inspect", "design_pitch_deck",
                })
                if on_status:
                    on_status("Repeated tool failure - recovering" if designer_failure else
                              "Repeated tool failure - stop retrying")
                recovery = (
                    "STOP RETRYING the same failed tool call. Do not ask the user to confirm or provide a path; "
                    "the tool confirmation prompt is the confirmation. Recover by calling design_pitch_deck ONCE "
                    "with a simple brief such as topic, audience, theme, and slides. Then use the CREATED PPTX "
                    "PATH from its result with pptx_inspect and open_presentation. Never retry pptx_create with "
                    "the same arguments or a hand-built JSON spec."
                ) if designer_failure else (
                    "STOP RETRYING the same failed tool call. Report the exact tool name, ERROR TYPE, and "
                    "ERROR MESSAGE once, then answer using the evidence already available. Do not call "
                    "research_url, web_fetch, web_search, or any other tool again."
                )
                messages.append({"role": "user", "content": recovery})
            messages = prepare_context(messages, max_tokens=gen_kwargs.get("max_tokens"), context=context)
            if rnd == max_rounds - 1:
                final_text = _clean_tool_text(text)
        if not _clean_tool_text(final_text) and tool_results and not (stop_event and stop_event.is_set()):
            if on_status:
                on_status("Composing final answer ...")
            messages.append({"role": "user", "content": ("Your previous message contained only tool calls. "
                                                          "Provide the final answer now using the TOOL RESULT(s). "
                                                          "Do not call another tool.")})
            try:
                resp = _stream_answer(messages, on_token, self.server, stop_event,
                                      gen_kwargs, max_continuations, context)
                final_text = resp["text"]
                usage = resp.get("usage") or usage
                finish = resp.get("finish_reason")
            except LLMError:
                pass
        if not final_text:
            final_text = "No final answer was produced. Check data/logs/llama-server.log for runtime errors."
        clean = _append_tool_errors(_clean_tool_text(final_text), tool_results)
        self.mem.add_message(self.session_id, "assistant", clean)
        return {
            "text": clean,
            "raw": final_text,
            "sources": sources,
            "tool_results": tool_results,
            "usage": usage,
            "finish_reason": finish,
            "elapsed": round(time.time() - started, 2),
            "session_id": self.session_id,
        }


HELP = """/help            this help
/research <q>    deep research mode for research, projects, hackathons
/psych <message> emotion-aware reflection and coaching mode
/hr <situation>  HR and people-operations mode
/coach <goal>    GROW coaching-plan mode
/ppt [theme]     presentation design template and rules
/remember <rule> teach THAMBI a durable preference
/forget <id|text> remove a learned rule
/learned          list learned rules
/status          model, RAM, GPU, index status
/internet        check outbound internet connectivity
/web <question>  multi-source web research with diverse citations
/websearch <q>   quick single web search
/openurl <url>   open a URL in the default browser
/hw              full hardware report
/index [folder]  index a folder (or configured folders)
/search <query>  search indexed documents with citations
/read <path>     read a text file
/doc <path>      read PDF/DOCX/XLSX/PPTX
                 books/OCR: /tool book_read {"path":"..."} or /tool ocr_image {"path":"..."}
/calc <name> {json}  ECE calculation (rc_filter, trace_width, friis, ...)
/kicad <path>    inspect .kicad_sch / .kicad_pcb
/bom <path>      analyse BOM CSV
/sim             detect ngspice/openEMS
/venv [path]     create a Python virtual environment (default .venv)
/tasks           list tasks
/task <title>    add a task
/emails <query>  search locally imported email
/email-import <path>  import .eml/.mbox
/calendar        list local events
/utcp            print this instance's UTCP tool manual
/open <app>      open allowlisted app
/tool <name> {json}   call any registered tool
/clear           clear the chat display

Natural language tool actions also work: THAMBI emits a validated tool call and asks for confirmation when required.
Voice: say "Hey Thambi" to start a handsfree conversation."""
