# THAMBI

**THAMBI** is a lightweight, private, offline-first AI engineering and CEO assistant running 100% locally on your machine (**ASUS TUF F15, Windows 11, 8GB RAM, NVIDIA RTX 2050**).

It combines local quantized LLM inference via `llama.cpp`, offline wake-word speech recognition and TTS, persistent SQLite full-text memory, safe Windows desktop automation, ECE/PCB/RF engineering tools, Office generation, CEO productivity modules, a deep **Research mode**, and a local **UTCP** tool gateway.

### Always-on hot word + ultra-low idle

- On launch THAMBI automatically starts the offline wake-word listener (**"Hey Thambi"**); no button press needed.
- After the wake word the conversation is fully handsfree: THAMBI records, transcribes, answers aloud, and keeps listening for follow-up questions until you go silent or say "stop" / "that's all".
- The LLM is **lazy-loaded** on first use, so an idle THAMBI costs almost no RAM/VRAM.
- While idle, audio is energy-gated, the keyword spotter runs on one thread, the process drops to Windows `IDLE_PRIORITY_CLASS`, and the working set is trimmed. After a configurable idle period the model is unloaded automatically.
- The first interaction (hot word, mic, or typed message) instantly restores normal priority and reloads the model.
- With voice enabled, **live speech** speaks complete sentences as they are generated instead of waiting for the whole answer. Disable it with **Settings → Voice → Stream speech live**.

### UTCP instead of MCP

THAMBI exposes every local tool over the **Universal Tool Calling Protocol** at `http://127.0.0.1:8191/utcp/manual` (JSON manual) and `POST /utcp/call` (tool execution). It can also consume external UTCP manuals through `utcp_list` / `utcp_call` with explicit approval. Run it standalone with `python -m aether --utcp`.

---

## Architecture & System Overview

- **Local LLM Engine**: `llama.cpp` (b12000, CUDA 12.4 with CPU fallback) running `qwen2.5-3b-instruct-q4_k_m.gguf` (2.1 GB) on local port `8181`.
- **Speech & Audio**:
  - Wake-word spotter: `sherpa-onnx` Zipformer (detects *"Hey Thambi"* offline).
  - Speech-to-Text: `whisper.cpp` (`whisper-cli.exe` + `ggml-base.en.bin`).
  - Text-to-Speech: `piper-tts` (`en_US-lessac-medium.onnx`).
- **Memory & Search**: SQLite WAL database with FTS5 (`data/aether.db`) indexing chat messages, long-term memory, document chunks, tasks, imported emails, calendar events, and drafts.
- **Native Windows Acceleration**: Lightweight C++ helper (`aether/native/aether_native.exe`) for fast recursive directory traversal, disk telemetry, SHA256 hashing, and safe `ShellExecute`.
- **Engineering Tools (ECE/RF/KiCad)**:
  - Validated mathematical formulas for Ohm's law, voltage dividers, RC filters, LC resonance, LED resistors, IPC-2221 trace width, Hammerstad-Jensen microstrip impedance, wire antenna lengths, Friis free-space path loss, dBm/mW conversions, battery runtime, buck/boost duty cycles, and thermal junction temperatures.
  - S-expression parser for KiCad schematics (`.kicad_sch`) and PCBs (`.kicad_pcb`).
  - BOM analyzer with duplicate detection and cost estimation.
- **Adaptive Learning**: durable user preferences are extracted from chat, stored in SQLite, confidence-weighted by feedback, and injected into future system prompts. Use `/remember`, `/forget`, and `/learned` to control them. This is prompt-level learning, not weight fine-tuning.
- **Opencode-style Developer Tools**: glob and regex search, allowlisted read-only commands, delegated subtasks, todo tracking, clarification prompts, web search/fetch, and UTCP tool manuals. Arbitrary model-invented shell commands remain blocked.
- **Psychology & HR Toolkit**: emotion reflection, tentative behaviour profiles, GROW coaching, wellbeing check-ins, empathy drafts, conflict mediation, structured interviews, performance-review drafts, and SBI feedback. Non-clinical and bias-aware; no diagnosis, manipulation, or automated employment decisions.
- **Document Intelligence**: offline OCR (RapidOCR), page-aware PDF reading with scanned-page OCR, EPUB/ODT/RTF/Office/text extraction, book indexing into local FTS memory, and book writing to DOCX/DOC/PDF/TXT/MD/EPUB.
- **CEO & Company Productivity**:
  - Quotation and invoice generation (`.docx`) with verified `Decimal` arithmetic.
  - Safe `.eml` email draft creation (never sends or deletes emails).
  - Local `.ics` calendar management and task tracking.
  - Multi-format document parser: PDF, Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), Email (`.eml`, `.mbox`).
- **Safety First**:
  - Tool failures are returned with an exact error type and message, and are appended to the final answer if the model tries to hide or misstate them.
  - Unknown tool arguments and malformed tool JSON are rejected with explicit validation errors instead of being silently ignored.
  - Never executes model-generated arbitrary shell strings.
  - Destructive actions, app launches, and file writes require explicit user confirmation.
  - Path confinement enforced within allowed project roots.

---

## Quick Start & How to Launch

> The Python package, config filename (`config/aether.json`), database, and native helper keep the internal `aether` name for compatibility. The assistant you talk to is **THAMBI**.

### 1. Graphical User Interface (Default)
Run the launcher batch file:
```cmd
run_aether.bat
```
Or run directly via Python:
```powershell
.\.venv\Scripts\python.exe -m aether
```

### 2. Command Line Interface (CLI mode)
For a terminal-only conversational interface:
```powershell
.\.venv\Scripts\python.exe -m aether --cli
```
Research mode with always-on hot word in the terminal:
```powershell
.\.venv\Scripts\python.exe -m aether --cli --module research --listen
```
Run the UTCP tool gateway standalone:
```powershell
.\.venv\Scripts\python.exe -m aether --utcp
```

### 3. Hardware Telemetry & Probe
Check available RAM, VRAM, storage, and tool status:
```powershell
.\.venv\Scripts\python.exe -m aether --probe
```

### 4. Voice Verification & Microphone Test
Verify offline Piper TTS, Whisper STT, and wake-word model:
```powershell
.\.venv\Scripts\python.exe -m aether.voice --check
```
Listen for the wake word (*"Hey Thambi"*):
```powershell
.\.venv\Scripts\python.exe -m aether.voice --listen
```

### 5. Running the Automated Test Suite
Run all automated unit and integration tests:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

---

## Designer Mode (High-Quality PPT)

Switch to the **Designer** mode in the GUI to create premium 16:9 presentations. The designer persona applies narrative hierarchy, one takeaway per slide, strong contrast, consistent margins, cards, stats, timelines, charts, quotes and comparisons.

| Tool / command | Purpose |
|---|---|
| `/ppt [theme]` or `pptx_template` | Get the design spec template, themes, and slide types |
| `design_pitch_deck` | Create a full deck from a simple brief: topic, audience, theme, slide count |
| `pptx_create` | Generate a real `.pptx` deck from a JSON spec after confirmation |
| `pptx_add_slides` | Insert new pages into an existing deck with plain `Title \| point, point` lines |
| `pptx_inspect` | Verify slide count, titles, and text after creation |
| `open_design_app {"app":"canva"}` | Open Canva in the browser |
| `open_design_app {"app":"powerpoint"}` | Open PowerPoint |
| `open_presentation` | Open a generated deck in the default app |

`pptx_create` returns a `CREATED PPTX PATH:` line; copy that exact path into `pptx_inspect` or `open_presentation`. A placeholder such as `<path_to_deck>.pptx` automatically resolves to the newest generated deck.
Themes: `midnight`, `aurora`, `corporate`, `creative`, `pitch`, `premium`. Aliases such as `luxury`, `executive`, `light`, `dark`, and `startup` are accepted; unknown names safely fall back to `midnight`.
Slide types: `title`, `agenda`, `section`, `content`, `cards`, `stats`, `timeline`, `chart`, `quote`, `comparison`, `table`, `image`, `closing`.

Example:
```text
/tool pptx_create {"path":"data/drafts/melouz_pitch.pptx","theme":"pitch","title":"Melouz Growth Deck","slides":[{"type":"title","title":"Designing What Matters","subtitle":"2026 pitch"},{"type":"stats","title":"Traction","stats":{"42%":"Faster delivery","3.1x":"Higher conversion"}},{"type":"closing","title":"Let's build it"}]}
```

---

## Code Files and Environments

THAMBI can create project files and Python environments through registered tools. Every write/install action requires confirmation and stays inside the configured allowed roots.

| Tool / command | Purpose |
|---|---|
| `create_file` / `write_code_file` / `write_file` | Create a text or code file; accepts a string or list of lines |
| `create_folder` | Create a directory |
| `patch_file` | Replace exact text with a backup |
| `create_venv` / `/venv [path]` | Create a Python virtual environment with the current interpreter |
| `venv_pip_install` | Install a `requirements.txt` into that environment using its own pip |
| `code_review` | Deterministic review for bare excepts, mutable defaults, long functions, dynamic code, and secret-like assignments |
| `security_scan` | Focused scan for secrets, private keys, shell injection, TLS bypass, unsafe deserialization, and unsafe YAML |
| `test_generator` | Generate pytest stub tests for public functions/classes in a Python file |
| `tool_spec_builder` | Create a reviewed tool scaffold draft with tool code, test file, and documentation |

Example tool calls:
```text
/tool create_file {"path":"data/drafts/demo_app/main.py","content":"print('hello')\n"}
/tool create_venv {"path":"data/drafts/demo_app/.venv"}
/tool venv_pip_install {"venv":"data/drafts/demo_app/.venv","requirements":"data/drafts/demo_app/requirements.txt"}
```

---

## Quality Loop

For coding work, THAMBI is instructed to run `code_review` and `security_scan` on changed files, generate missing tests with `test_generator`, syntax-check with `python_syntax_check`, and finish with `run_tests`. Findings include severity, file/line, and a fix direction; deterministic checks are advisory, not a proof of correctness.

For new tools, `tool_spec_builder` writes a non-registered draft under `data/drafts/tool_specs` with a `@register` skeleton, placeholder test, and spec README. The draft must be implemented, added to `aether/tools/registry.py`, syntax-checked, and tested before it becomes a live tool.

---

## Innovation Lab

THAMBI now has an explicit innovation loop: check the landscape, generate options, design a test, then compare tradeoffs.

| Tool | Purpose |
|---|---|
| `prior_art_search` | Multi-angle web search for existing products, papers, open-source work, and similar solutions |
| `patent_search` | Patent-landscape search with overlap signals and claim-chart next actions |
| `idea_lab` | Structured SCAMPER, TRIZ, and jobs-to-be-done idea prompts with transparent heuristic scoring |
| `experiment_designer` | Hypotheses, primary/guardrail metrics, sample-size heuristic, A/B design, stopping rule, and risk controls |
| `decision_matrix` | Weighted criteria, evidence-linked scores, sensitivity analysis, and evidence gaps |

Novelty and patent outputs are technical landscape scans only. They are not legal advice, patentability opinions, or freedom-to-operate clearance. Heuristic scores are advisory until backed by real evidence.

---

## Auto Cleaner

Click **Auto Clean** in the top-right of the GUI, or call `cleanup_cache`, to preview and remove cache/temporary artifacts. It clears `__pycache__`, `.pyc`, `.pytest_cache`, `.aether.tmp`/`.aether.bak`/`.aether.log` files, stale LibreOffice profiles, temporary voice recordings, and tool scratch files.

It never deletes source code, config, models, SQLite memory, documents, or delivered drafts. The button first shows a dry-run preview and asks for confirmation.

---

## Internet Access

Internet access is enabled for this install via `safety.allow_external_research: true` (also visible as **Settings → Safety → Allow internet access**). Available tools:

| Tool | Purpose |
|---|---|
| `internet_status` | Test outbound connectivity and show whether proxy environment variables are present |
| `deep_web_research` | Run several search angles, select domain-diverse sources, fetch evidence, and index each unique URL once |
| `research_matrix` | Turn a research brief into a claim/source/domain/confidence matrix with gaps and contradiction cues |
| `claim_verifier` | Check a claim against supplied evidence and report domain-diverse lexical support without claiming external truth |
| `research_report_export` | Export the brief, evidence matrix, method, and limitations to MD, DOCX, or PDF |
| `web_search` | Quick single-angle DuckDuckGo search with titles, URLs, and snippets |
| `web_fetch` | Download an http(s) page and return readable text |
| `research_url` | Fetch a page/PDF, extract text, and index it locally with a citation |
| `browser_open` | Open an http(s) URL in the system default browser |
| `utcp_list` / `utcp_call` | Discover and call tools from external UTCP providers |

Every external action still asks for confirmation. No local files, credentials, or private memory are sent automatically.

Research answers now use a ChatGPT-style citation flow: multiple search angles, domain-diverse source selection, one `[S#]` handle per unique URL, deduplicated `SOURCES` lines, and an explicit warning when fewer than three domains are available. The same URL is never repeated as many separate references.

---

## Psychology & HR Mode

Switch to **Psychology** or **HR / People** in the GUI, or use `/psych`, `/coach`, `/hr`, and `/people`. These modes add emotion-aware and people-operations tools while keeping strict guardrails.

| Tool / command | Purpose |
|---|---|
| `emotion_analyze` | Reflect likely emotions, intensity, cues, needs, and supportive response moves |
| `psychology_profile` | Tentative communication/motivation hypotheses from supplied notes |
| `behavior_profile` | Antecedent-behavior-consequence pattern reflection |
| `coaching_plan` | GROW coaching plan with SMART next steps |
| `wellbeing_checkin` | Supportive check-in with crisis escalation guidance |
| `empathy_reply` | Draft a warm, non-judgemental reply |
| `conflict_mediation` | Neutral conflict frame, interests, options, and agreement draft |
| `hr_interview_kit` | Structured STAR questions, evidence rubric, and bias controls |
| `hr_performance_review` | Fair review draft from supplied evidence only |
| `hr_feedback_message` | Situation-Behavior-Impact feedback draft |

All outputs are labelled as non-clinical and evidence-limited. They do not diagnose, infer protected traits, manipulate people, or make hiring/pay/promotion/termination decisions.

---

## Slash Commands in Chat

| Command | Action |
|---|---|
| `/help` | Show command reference |
| `/research <question>` | Deep research mode: reasoning, tradeoffs, experiments, project and hackathon plans |
| `/psych <message>` | Emotion-aware reflection and coaching mode |
| `/coach <goal>` | GROW coaching-plan mode |
| `/hr <situation>` or `/people <situation>` | HR and people-operations mode |
| `/ppt [theme]` | Presentation template, rules, and slide types |
| `/remember <rule>` | Teach THAMBI a durable preference (or say "remember that ..." in chat) |
| `/forget <id|text>` | Remove a learned rule |
| `/learned` | List learned rules |
| `/status` | Model, RAM, GPU, and memory index telemetry |
| `/internet` | Check outbound internet connectivity |
| `/web <question>` | Multi-source web research with diverse citations |
| `/websearch <query>` | Quick single web search |
| `/openurl <url>` | Open a URL in the default browser |
| `/hw` | Full hardware and system capability probe |
| `/index [folder]` | Index a folder into searchable local memory |
| `/search <query>` | Search indexed documents with source citations |
| `/read <path>` | Read local text file with line numbers |
| `/doc <path>` | Extract text and tables from PDF, DOCX, XLSX, PPTX |
| `/tool ocr_image {"path":"..."}` | Offline OCR on an image file |
| `/tool book_read {"path":"..."}` | Read a PDF/book/scanned document with OCR fallback |
| `/tool book_write {"path":"...","title":"...","chapters":[...]}` | Write DOCX/DOC/PDF/TXT/MD/EPUB |
| `/calc <name> <json>` | Run verified ECE calculation (`rc_filter`, `trace_width`, etc.) |
| `/kicad <path>` | Inspect `.kicad_sch` or `.kicad_pcb` |
| `/bom <path>` | Analyze BOM CSV |
| `/venv [path]` | Create a Python virtual environment (default `.venv`) |
| `/tasks` | List tracked company tasks |
| `/task <title>` | Add a task |
| `/emails <query>` | Search locally imported email |
| `/calendar` | List local calendar events |
| `/open <app>` | Launch allowlisted application (requires confirmation) |
| `/clear` | Clear the chat display |
