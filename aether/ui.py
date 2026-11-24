from __future__ import annotations

import ctypes
import json
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import safety
from .agent import ChatEngine
from .config import ROOT, get, load, resolve, save
from .hardware import probe
from .idle import IdleManager
from .llm import LlamaServer
from .memory import default as default_memory
from .tools import execute

BG = "#0b0f14"
PANEL = "#11161d"
PANEL2 = "#171e27"
PANEL3 = "#1f2933"
BORDER = "#243040"
FG = "#e8eef5"
MUTED = "#8b98a9"
ACCENT = "#4f8cff"
ACCENT2 = "#22d3ee"
GREEN = "#34d399"
YELLOW = "#fbbf24"
RED = "#f87171"

_SUPERSCRIPT = str.maketrans("0123456789+-=()abnop", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇⁿᵒᵖ")
_SUBSCRIPT = str.maketrans("0123456789+-=()aehkop", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕₖₒₚ")
_MATH_COMMANDS = {
    "times": "×", "cdot": "·", "pm": "±", "mp": "∓", "div": "÷",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "infty": "∞", "in": "∈", "notin": "∉", "subset": "⊂",
    "cup": "∪", "cap": "∩", "sum": "∑", "prod": "∏", "int": "∫",
    "partial": "∂", "nabla": "∇", "alpha": "α", "beta": "β", "gamma": "γ",
    "delta": "δ", "Delta": "Δ", "epsilon": "ε", "theta": "θ", "lambda": "λ",
    "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "Sigma": "Σ", "tau": "τ",
    "phi": "φ", "omega": "ω", "Omega": "Ω", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "leftrightarrow": "↔", "ldots": "…",
    "sin": "sin", "cos": "cos", "tan": "tan", "log": "log", "ln": "ln",
    "exp": "exp", "lim": "lim", "max": "max", "min": "min",
}


def _readable_math(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\dfrac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", text)
    text = text.replace(r"\left", "").replace(r"\right", "")
    text = re.sub(r"\^\{([^{}]+)\}", lambda m: m.group(1).translate(_SUPERSCRIPT), text)
    text = re.sub(r"_\{([^{}]+)\}", lambda m: m.group(1).translate(_SUBSCRIPT), text)
    text = re.sub(r"\^([A-Za-z0-9])", lambda m: m.group(1).translate(_SUPERSCRIPT), text)
    text = re.sub(r"_([A-Za-z0-9])", lambda m: m.group(1).translate(_SUBSCRIPT), text)

    def command(match: re.Match[str]) -> str:
        name = match.group(1)
        return _MATH_COMMANDS.get(name, name)

    text = re.sub(r"\\([A-Za-z]+)", command, text)
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    text = re.sub(r"\s+([,.;:)\]])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _format_markdown(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            if out and out[-1].strip():
                out.append("")
            out.append(heading.group(1).strip())
            out.append("")
            continue
        if re.fullmatch(r"(?:---+|\*\*\*+|___+)", stripped):
            out.append("────────────────────")
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", stripped) and "|" in stripped:
            out.append("  " + "─" * min(48, max(12, len(stripped))))
            continue
        line = re.sub(r"^\s*[-*+]\s+", "• ", line)
        line = re.sub(r"^\s*>\s?", "  ", line)
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = "  " + " | ".join(cells)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_]+)__", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", line)
        line = re.sub(r"(?<![A-Za-z0-9_])_([^_]+)_(?![A-Za-z0-9_])", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        out.append(line)
    return "\n".join(out)


def _dedupe_sources(text: str) -> str:
    match = re.search(r"(?ms)^SOURCES\s*$", text)
    if not match:
        return text
    head = text[:match.start()].rstrip()
    body = text[match.end():]
    seen: set[str] = set()
    kept: list[str] = []
    for line in body.splitlines():
        key = re.sub(r"^\[[SR]\d+\]\s*", "", " ".join(line.split()))
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(line)
    return head + "\n\nSOURCES\n" + "\n".join(kept)


def _dedupe_source_items(sources: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept: list[dict] = []
    for source in sources:
        path = str(source.get("path") or "").strip()
        key = re.sub(r"^url:\s*", "", path).lower().rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(source)
    return [{**source, "id": f"S{index}"} for index, source in enumerate(kept, 1)]


def format_response(text: str) -> str:
    if not text:
        return ""
    result = str(text).replace("\r\n", "\n").replace("\r", "\n")
    result = re.sub(r"```tool\s*.*?```", "", result, flags=re.DOTALL | re.IGNORECASE)
    blocks: list[str] = []

    def hold_block(match: re.Match[str]) -> str:
        code = match.group(1).strip("\n")
        blocks.append(code)
        return f"\ue000{len(blocks) - 1}\ue001"

    result = re.sub(r"```[A-Za-z0-9_+-]*\n(.*?)```", hold_block, result, flags=re.DOTALL)
    result = re.sub(r"\\\((.+?)\\\)", lambda m: _readable_math(m.group(1)), result, flags=re.DOTALL)
    result = re.sub(r"\\\[(.+?)\\\]", lambda m: "\n" + _readable_math(m.group(1)) + "\n",
                    result, flags=re.DOTALL)
    result = re.sub(r"\$\$(.+?)\$\$", lambda m: "\n" + _readable_math(m.group(1)) + "\n",
                    result, flags=re.DOTALL)
    result = re.sub(r"(?<!\w)\$([^$\n]+)\$(?!\w)", lambda m: _readable_math(m.group(1)), result)
    result = _format_markdown(result)
    for index, code in enumerate(blocks):
        body = "\n".join("    " + line for line in code.splitlines())
        result = result.replace(f"\ue000{index}\ue001", f"Code:\n{body}")
    return _dedupe_sources(result).strip()


class AetherUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("THAMBI - private local assistant")
        self.root.geometry("1180x760")
        self.root.minsize(940, 600)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.server = LlamaServer()
        self.mem = default_memory()
        self.engine = ChatEngine(self.server, self.mem)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.voice = None
        self.voice_active = False
        self.utcp = None
        self.module = tk.StringVar(value="chat")
        self.status_var = tk.StringVar(value="Starting ...")
        self.model_var = tk.StringVar(value="Model: idle (starts on first use)")
        self.reply_start = None
        self.last_reply_text = ""
        self.live_var = tk.StringVar(value="ready")
        self._last_hw = 0.0
        self._starting_server = False
        self._confirm_parent = self.root
        self.idle = IdleManager(self.server, on_state=lambda s: self.q.put(("status", s)))

        safety.set_confirm_callback(self.confirm)

        self._build()
        self._poll()
        self.idle.start()
        self._start_utcp()
        self._auto_start_voice()
        if not bool(get("idle.lazy_model", True)):
            self._load_engine()
        else:
            self._detect_server()
        self.root.after(5000, self._update_health)

    # ---------------- UI ----------------
    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG,
                        arrowcolor=MUTED, bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
                        padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL2)],
                  foreground=[("readonly", FG)],
                  selectbackground=[("readonly", PANEL2)], selectforeground=[("readonly", FG)])
        style.configure("Vertical.TScrollbar", background=PANEL3, troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=MUTED, relief="flat", width=10)
        style.map("Vertical.TScrollbar", background=[("active", ACCENT)])

        self._build_menu()

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 10))

        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        logo = tk.Canvas(brand, width=40, height=40, bg=BG, highlightthickness=0)
        logo.create_rectangle(4, 4, 36, 36, fill=ACCENT, outline="")
        logo.create_text(20, 21, text="T", fill="#07101f", font=("Segoe UI Semibold", 18))
        logo.pack(side="left", padx=(0, 12))
        words = tk.Frame(brand, bg=BG)
        words.pack(side="left")
        tk.Label(words, text="THAMBI", bg=BG, fg=FG, font=("Segoe UI Semibold", 19)).pack(anchor="w")
        tk.Label(words, text="private  |  offline-first  |  local", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")

        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        self.settings_btn = self._ghost_button(right, "Settings", self.settings_dialog)
        self.settings_btn.pack(side="right", padx=(6, 0))
        self.clean_btn = self._ghost_button(right, "Auto Clean", self.auto_clean)
        self.clean_btn.pack(side="right", padx=6)
        self.voice_btn = tk.Button(right, text="Voice: off", command=self.toggle_voice, bg=PANEL2, fg=FG,
                                   activebackground=PANEL3, activeforeground=FG, relief="flat",
                                   padx=14, pady=7, font=("Segoe UI Semibold", 9), cursor="hand2")
        self.voice_btn.pack(side="right", padx=6)

        chip = tk.Frame(right, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        chip.pack(side="right", padx=6)
        self.status_dot = tk.Canvas(chip, width=12, height=12, bg=PANEL2, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(8, 2), pady=6)
        self._dot = self.status_dot.create_oval(3, 3, 9, 9, fill=MUTED, outline="")
        tk.Label(chip, textvariable=self.model_var, bg=PANEL2, fg=MUTED,
                 font=("Segoe UI", 8), padx=4).pack(side="left", padx=(0, 8), pady=6)

        mode = tk.Frame(header, bg=BG)
        mode.pack(side="left", padx=(28, 0))
        tk.Label(mode, text="MODE", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.module_box = ttk.Combobox(mode, textvariable=self.module, state="readonly", width=12,
                                       values=["chat", "research", "coding", "designer", "ece", "desktop", "ceo",
                                               "psychology", "hr", "voice"])
        self.module_box.pack(pady=(2, 0))
        self.module_box.bind("<<ComboboxSelected>>", lambda e: self._module_changed())

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        side = tk.Frame(body, bg=PANEL, width=238, highlightbackground=BORDER, highlightthickness=1)
        side.pack(side="left", fill="y", padx=(0, 12))
        side.pack_propagate(False)

        self._section_label(side, "WORKSPACE")
        self.module_buttons: dict[str, tk.Button] = {}
        for text, mod in (("Chat", "chat"), ("Research", "research"), ("Coding", "coding"),
                          ("Designer", "designer"), ("ECE / Hardware", "ece"),
                          ("Desktop", "desktop"), ("CEO / Melouz", "ceo"),
                          ("Psychology", "psychology"), ("HR / People", "hr")):
            self._module_button(side, text, mod)

        self._divider(side)
        self._section_label(side, "TOOLS")
        for text, cmd in (
            ("Index folder...", self.index_folder_dialog),
            ("Search docs...", self.search_dialog),
            ("Research question...", self.research_question_dialog),
            ("Read document...", self.document_dialog),
            ("KiCad inspect...", self.kicad_dialog),
            ("BOM analysis...", self.bom_dialog),
            ("Email / calendar...", self.email_dialog),
            ("Tasks...", self.tasks_dialog),
            ("Hardware report...", self.hardware_dialog),
        ):
            self._side_button(side, text, cmd)

        card = tk.Frame(side, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        card.pack(side="bottom", fill="x", padx=10, pady=10)
        tk.Label(card, text="SYSTEM", bg=PANEL2, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.stats_label = tk.Label(card, text="", bg=PANEL2, fg=MUTED, font=("Segoe UI", 8),
                                    justify="left", anchor="w")
        self.stats_label.pack(anchor="w", padx=10, pady=(0, 8))

        main = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        main.pack(side="left", fill="both", expand=True)

        chat_head = tk.Frame(main, bg=PANEL)
        chat_head.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(chat_head, text="CONVERSATION", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.module_hint_var = tk.StringVar(value="General assistant")
        tk.Label(chat_head, textvariable=self.module_hint_var, bg=PANEL, fg=ACCENT2,
                 font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))
        tk.Label(chat_head, textvariable=self.live_var, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="right", padx=(0, 10))
        self._ghost_button(chat_head, "Copy", self.copy_last_reply).pack(side="right", padx=(6, 0))
        self._ghost_button(chat_head, "New", self.new_conversation).pack(side="right", padx=(6, 0))
        self._ghost_button(chat_head, "Clear", self.clear_chat).pack(side="right")

        chat_wrap = tk.Frame(main, bg=PANEL)
        chat_wrap.pack(fill="both", expand=True)
        self.chat = tk.Text(chat_wrap, bg=PANEL, fg=FG, insertbackground=FG, wrap="word",
                            relief="flat", borderwidth=0, highlightthickness=0,
                            font=("Segoe UI", 10), padx=16, pady=8, state="disabled",
                            spacing1=2, spacing3=4, undo=False, cursor="arrow",
                            selectbackground=ACCENT, selectforeground="#07101f")
        scroll = ttk.Scrollbar(chat_wrap, orient="vertical", command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("user_role", foreground=ACCENT2, font=("Segoe UI Semibold", 8),
                                spacing1=12, spacing3=2)
        self.chat.tag_configure("user_text", foreground=FG, font=("Segoe UI", 10),
                                lmargin1=10, lmargin2=10, spacing3=6)
        self.chat.tag_configure("ai_role", foreground=ACCENT, font=("Segoe UI Semibold", 8),
                                spacing1=12, spacing3=2)
        self.chat.tag_configure("assistant", foreground=FG, font=("Segoe UI", 10),
                                lmargin1=10, lmargin2=10, spacing3=8)
        self.chat.tag_configure("source", foreground=MUTED, font=("Segoe UI", 8),
                                lmargin1=14, lmargin2=14, spacing1=4)
        self.chat.tag_configure("tool", foreground=YELLOW, font=("Segoe UI", 8),
                                 lmargin1=14, lmargin2=14)
        self.chat.tag_configure("tool_error", foreground=RED, font=("Segoe UI", 8),
                                 lmargin1=14, lmargin2=14)

        self.chat.tag_configure("error", foreground=RED, font=("Segoe UI", 9),
                                lmargin1=10, lmargin2=10, spacing1=4)
        self._chat_menu()

        input_area = tk.Frame(main, bg=PANEL)
        input_area.pack(fill="x", padx=12, pady=(6, 12))
        border = tk.Frame(input_area, bg=BORDER)
        border.pack(fill="x")
        inner = tk.Frame(border, bg=PANEL2)
        inner.pack(fill="x", padx=1, pady=1)
        self.input = tk.Text(inner, height=3, bg=PANEL2, fg=FG, insertbackground=ACCENT,
                             relief="flat", borderwidth=0, highlightthickness=0,
                             font=("Segoe UI", 10), padx=12, pady=9, wrap="word", undo=True)
        self.input.pack(side="left", fill="both", expand=True)
        self._placeholder = "Ask THAMBI anything...  (Enter to send, Shift+Enter for newline)"
        self._placeholder_on = False
        self._install_placeholder()
        self.input.bind("<Return>", self._enter)
        self.input.bind("<Shift-Return>", lambda e: None)
        self.input.bind("<Key>", lambda e: self.idle.touch(), add="+")
        self.root.bind("<Button-1>", lambda e: self.idle.touch(), add="+")

        btns = tk.Frame(inner, bg=PANEL2)
        btns.pack(side="right", fill="y", padx=(6, 8), pady=8)
        self.send_btn = tk.Button(btns, text="Send", command=self.submit, bg=ACCENT, fg="#07101f",
                                  activebackground="#76a9ff", activeforeground="#07101f",
                                  disabledforeground="#5b6b7f", relief="flat", width=9, pady=7,
                                  cursor="hand2", font=("Segoe UI Semibold", 9))
        self.send_btn.pack(fill="x", pady=(0, 4))
        self.stop_btn = tk.Button(btns, text="Stop", command=self.stop_generation, bg=PANEL3, fg=FG,
                                  activebackground=RED, activeforeground="#07101f",
                                  disabledforeground="#5b6b7f", relief="flat", width=9, pady=5,
                                  state="disabled", cursor="hand2")
        self.stop_btn.pack(fill="x", pady=(0, 4))
        self.mic_btn = tk.Button(btns, text="Mic", command=self.push_to_talk, bg=PANEL3, fg=FG,
                                 activebackground=GREEN, activeforeground="#07101f", relief="flat",
                                 width=9, pady=5, cursor="hand2")
        self.mic_btn.pack(fill="x")

        tk.Label(input_area, text="Enter send   Shift+Enter newline   Ctrl+L clear   Ctrl+M mic   Esc stop",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=2, pady=(5, 0))

        statusbar = tk.Frame(self.root, bg=PANEL2, height=26, highlightbackground=BORDER,
                             highlightthickness=1)
        statusbar.pack(fill="x", side="bottom")
        tk.Label(statusbar, textvariable=self.status_var, bg=PANEL2, fg=MUTED, anchor="w",
                 font=("Segoe UI", 8), padx=12).pack(side="left", fill="x", expand=True)
        self.voice_var = tk.StringVar(value="Voice: off")
        self.utcp_var = tk.StringVar(value="UTCP: starting")
        self.power_var = tk.StringVar(value="Power: normal")
        for var in (self.power_var, self.utcp_var, self.voice_var):
            tk.Label(statusbar, textvariable=var, bg=PANEL2, fg=MUTED, font=("Segoe UI", 8),
                     padx=10).pack(side="right")

        self._bind_shortcuts()
        self._set_module_active()

    # ---------------- UI helpers ----------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root, bg=PANEL, fg=FG, activebackground=ACCENT,
                          activeforeground="#07101f", borderwidth=0)
        filem = tk.Menu(menubar, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                        activeforeground="#07101f")
        filem.add_command(label="New conversation", command=self.new_conversation, accelerator="Ctrl+N")
        filem.add_command(label="Clear display", command=self.clear_chat, accelerator="Ctrl+L")
        filem.add_command(label="Index folder ...", command=self.index_folder_dialog)
        filem.add_command(label="Re-index configured folders", command=self.reindex_configured)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=filem)

        toolsm = tk.Menu(menubar, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                         activeforeground="#07101f")
        toolsm.add_command(label="Read document ...", command=self.document_dialog)
        toolsm.add_command(label="Inspect KiCad file ...", command=self.kicad_dialog)
        toolsm.add_command(label="Analyse BOM ...", command=self.bom_dialog)
        toolsm.add_command(label="Simulator status", command=lambda: self.run_tool("simulator_status", {}))
        toolsm.add_separator()
        toolsm.add_command(label="Import email ...", command=self.email_dialog)
        toolsm.add_command(label="Import calendar ...", command=self.calendar_import_dialog)
        toolsm.add_command(label="List calendar", command=lambda: self.run_tool("list_calendar", {}))
        toolsm.add_command(label="Tasks", command=self.tasks_dialog)
        toolsm.add_command(label="Create quotation ...", command=self.quotation_dialog)
        toolsm.add_command(label="Create invoice ...", command=self.invoice_dialog)
        toolsm.add_command(label="Analyse resume ...", command=self.resume_dialog)
        toolsm.add_separator()
        toolsm.add_command(label="Fetch datasheet / URL ...", command=self.research_dialog)
        toolsm.add_command(label="Hardware report", command=self.hardware_dialog)
        menubar.add_cascade(label="Tools", menu=toolsm)

        settingsm = tk.Menu(menubar, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                            activeforeground="#07101f")
        settingsm.add_command(label="Settings ...", command=self.settings_dialog)
        settingsm.add_command(label="Restart model server", command=self.restart_server)
        settingsm.add_command(label="Show model log", command=self.show_model_log)
        menubar.add_cascade(label="Settings", menu=settingsm)

        helpm = tk.Menu(menubar, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                        activeforeground="#07101f")
        helpm.add_command(label="Commands", command=self.show_help)
        helpm.add_command(label="UTCP manual", command=self.show_utcp_manual)
        helpm.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpm)
        self.root.config(menu=menubar)

    def _ghost_button(self, parent, text, command) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=PANEL, fg=MUTED,
                         activebackground=PANEL2, activeforeground=FG, relief="flat",
                         padx=10, pady=5, font=("Segoe UI", 9), cursor="hand2")

    def _section_label(self, parent, text) -> None:
        tk.Label(parent, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(
            anchor="w", padx=14, pady=(14, 6))

    def _divider(self, parent) -> None:
        wrap = tk.Frame(parent, bg=PANEL)
        wrap.pack(fill="x", padx=12, pady=8)
        tk.Frame(wrap, bg=BORDER, height=1).pack(fill="x")

    def _module_button(self, parent, text, module) -> None:
        b = tk.Button(parent, text=text, command=lambda m=module: self.set_module(m), bg=PANEL,
                      fg=FG, activebackground=PANEL2, activeforeground=FG, relief="flat",
                      anchor="w", padx=14, pady=7, font=("Segoe UI", 9), cursor="hand2")
        b.pack(fill="x", padx=6, pady=1)
        self.module_buttons[module] = b

    def _side_button(self, parent, text, cmd) -> None:
        b = tk.Button(parent, text=text, command=cmd, bg=PANEL, fg=MUTED,
                      activebackground=PANEL2, activeforeground=FG, relief="flat",
                      anchor="w", padx=14, pady=4, font=("Segoe UI", 9), cursor="hand2")
        b.pack(fill="x", padx=6)

    def _set_module_active(self) -> None:
        current = self.module.get()
        for mod, btn in getattr(self, "module_buttons", {}).items():
            active = mod == current
            btn.configure(bg=PANEL2 if active else PANEL,
                          fg=ACCENT2 if active else FG,
                          font=("Segoe UI Semibold" if active else "Segoe UI", 9))

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda e: self.new_conversation())
        self.root.bind("<Control-l>", lambda e: self.clear_chat())
        self.root.bind("<Control-m>", lambda e: self.push_to_talk())
        self.root.bind("<Escape>", lambda e: self.stop_generation())

    def _chat_menu(self) -> None:
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT,
                    activeforeground="#07101f")
        m.add_command(label="Copy", command=lambda: self.chat.event_generate("<<Copy>>"))
        m.add_command(label="Select all", command=self._select_all_chat)
        self.chat_menu = m
        self.chat.bind("<Button-3>", self._show_chat_menu)

    def _show_chat_menu(self, event) -> None:
        try:
            self.chat_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.chat_menu.grab_release()

    def _select_all_chat(self) -> None:
        self.chat.tag_add("sel", "1.0", "end-1c")
        self.chat.mark_set("insert", "1.0")

    def _set_dot(self, color: str) -> None:
        if hasattr(self, "status_dot") and hasattr(self, "_dot"):
            self.status_dot.itemconfigure(self._dot, fill=color)

    def _install_placeholder(self) -> None:
        self.input.insert("1.0", self._placeholder)
        self.input.configure(fg=MUTED)
        self._placeholder_on = True
        self.input.bind("<FocusIn>", self._clear_placeholder)
        self.input.bind("<FocusOut>", self._restore_placeholder)

    def _clear_placeholder(self, _event=None) -> None:
        if self._placeholder_on:
            self.input.delete("1.0", "end")
            self.input.configure(fg=FG)
            self._placeholder_on = False

    def _restore_placeholder(self, _event=None) -> None:
        if not self.input.get("1.0", "end-1c").strip():
            self.input.delete("1.0", "end")
            self.input.insert("1.0", self._placeholder)
            self.input.configure(fg=MUTED)
            self._placeholder_on = True

    def _at_bottom(self) -> bool:
        try:
            return self.chat.yview()[1] >= 0.985
        except tk.TclError:
            return True

    def clear_chat(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def new_conversation(self) -> None:
        import uuid
        self.engine.session_id = uuid.uuid4().hex[:12]
        self.clear_chat()
        self.status_var.set("New conversation")
        self.idle.touch()

    # ---------------- engine ----------------
    def _detect_server(self) -> None:
        if self.server.health(0.4):
            try:
                self.server.active_model = resolve(get("model.primary"))
                self.q.put(("model", self.server.active_model))
            except Exception:
                pass
            self.q.put(("status", "Ready (model already running)"))

    def _ensure_server_async(self) -> None:
        if self.server.health(0.4) or self._starting_server:
            return
        self._starting_server = True

        def work():
            try:
                self.q.put(("status", "Loading model ..."))
                model = self.server.start(lambda s: self.q.put(("status", s)))
                self.q.put(("model", model))
                self.q.put(("status", "Model ready"))
            except Exception as e:
                self.q.put(("error", f"Model server failed: {e}"))
            finally:
                self._starting_server = False
                self.idle.touch()
        threading.Thread(target=work, daemon=True).start()

    def _load_engine(self) -> None:
        self._ensure_server_async()

    def _start_utcp(self) -> None:
        if not bool(get("utcp.enabled", True)):
            if hasattr(self, "utcp_var"):
                self.utcp_var.set("UTCP: disabled")
            return
        try:
            from .utcp import UtcpServer
            self.utcp = UtcpServer()
            url = self.utcp.start()
            self.utcp_var.set("UTCP: online")
            self.q.put(("status", f"UTCP gateway: {url}/utcp/manual"))
        except Exception as e:
            self.utcp = None
            self.utcp_var.set("UTCP: error")
            self.q.put(("status", f"UTCP unavailable: {e}"))

    def _make_voice(self) -> None:
        from .voice import VoiceAssistant
        self.voice = VoiceAssistant(
            on_transcript=lambda t: self.q.put(("voice_text", t)),
            on_status=lambda s: self.q.put(("status", s)),
            on_state=lambda s: self.q.put(("voice_state", s)),
            on_wake=lambda: self.q.put(("wake", None)),
            speak_lock=self.server,
        )

    def _auto_start_voice(self) -> None:
        cfg = load()["voice"]
        if not cfg.get("enabled", True):
            self.status_var.set("Voice: off (disabled in config)")
            self.voice_var.set("Voice: disabled")
            return
        if not cfg.get("auto_start", True):
            return

        def work():
            try:
                if self.voice is None:
                    self._make_voice()
                if not self.voice.available():
                    self.q.put(("status", "Voice: models missing - run scripts\\setup_voice.ps1"))
                    self.q.put(("voice_state", "Voice: setup needed"))
                    return
                self.voice.start()
                self.voice_active = True
                self.q.put(("voice_state", "Voice: on"))
                self.q.put(("status", "Always listening - say 'Hey Thambi'"))
            except Exception as e:
                self.q.put(("status", f"Voice auto-start failed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def restart_server(self) -> None:
        def work():
            self.q.put(("status", "Restarting model ..."))
            self.server.stop()
            try:
                model = self.server.start(lambda s: self.q.put(("status", s)))
                self.q.put(("model", model))
                self.q.put(("status", "Ready"))
            except Exception as e:
                self.q.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _module_changed(self) -> None:
        m = self.module.get()
        hints = {
            "chat": "General assistant",
            "research": "Deep reasoning and sources",
            "coding": "Software engineering",
            "designer": "High-impact presentations",
            "ece": "Electronics and hardware",
            "desktop": "Windows actions",
            "ceo": "Company productivity",
            "psychology": "Emotion, behaviour and wellbeing",
            "hr": "People operations and HR",
            "voice": "Voice-first conversation",
        }
        if hasattr(self, "module_hint_var"):
            self.module_hint_var.set(hints.get(m, m))
        self._set_module_active()
        self.status_var.set(f"Module: {m}")

    def set_module(self, module: str) -> None:
        self.module.set(module)
        self._module_changed()

    # ---------------- chat ----------------
    def _enter(self, event):
        if event.state & 0x0001:  # Shift
            return None
        self.submit()
        return "break"

    def append(self, text: str, tag: str = "assistant", follow: bool = True) -> None:
        stick = self._at_bottom()
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        if follow or stick:
            self.chat.see("end")

    def copy_last_reply(self) -> None:
        if not self.last_reply_text:
            self.status_var.set("Nothing to copy yet")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_reply_text)
        self.status_var.set("Last reply copied")

    def submit(self, text: str | None = None) -> None:
        if self.busy:
            if self.voice_active and self.voice:
                self.voice.turn_finished()
            return
        if text is None:
            if getattr(self, "_placeholder_on", False):
                text = ""
            else:
                text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self.input.configure(fg=FG)
        self._placeholder_on = False
        if text.strip() in ("/clear", "/new"):
            self.new_conversation()
            return
        self.idle.touch()
        self.append("YOU", "user_role")
        self.append(f"\n{text}\n", "user_text")
        self.reply_start = self.chat.index("end-1c")
        self.append("THAMBI\n", "ai_role")
        self.busy = True
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.live_var.set("generating")
        self.stop_event = threading.Event()
        module = self.module.get()
        if module == "voice":
            module = "chat"
        lowered = text.strip().lower()
        mode_commands = (
            ("/research", "research"), ("/psychology", "psychology"), ("/psych", "psychology"),
            ("/coach", "psychology"), ("/hr", "hr"), ("/people", "hr"),
        )
        for prefix, target in mode_commands:
            if lowered.startswith(prefix):
                module = target
                break
        needs_model = not text.strip().startswith("/") or any(lowered.startswith(p) for p, _ in mode_commands)

        def work():
            streaming = False
            try:
                if needs_model and not self.server.health(0.5):
                    self.q.put(("status", "Loading model ..."))
                    model = self.server.start(lambda s: self.q.put(("status", s)))
                    self.q.put(("model", model))
                self.idle.touch()
                if needs_model and self.voice_active and self.voice:
                    streaming = self.voice.begin_stream()
                    if streaming:
                        self.live_var.set("speaking live")

                def emit_token(token: str) -> None:
                    self.q.put(("token", token))
                    if streaming:
                        self.voice.feed_stream(token)

                res = self.engine.reply(
                    text, module,
                    on_token=emit_token,
                    on_status=lambda s: self.q.put(("status", s)),
                    stop_event=self.stop_event,
                )
                if streaming:
                    self.voice.end_stream()
                    res["voice_streamed"] = True
                self.q.put(("done", res))
            except Exception as e:
                if streaming:
                    self.voice.end_stream()
                self.q.put(("error", f"{type(e).__name__}: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _replace_reply(self, text: str) -> None:
        if self.reply_start is None:
            self.append(text)
            return
        self.chat.configure(state="normal")
        self.chat.delete(self.reply_start, "end")
        self.chat.insert("end", "THAMBI\n", "ai_role")
        text = format_response(text)
        if text and not text.endswith("\n"):
            text += "\n"
        self.chat.insert("end", text, "assistant")
        self.chat.configure(state="disabled")
        self.chat.see("end")
        self.reply_start = None

    def _done(self, res: dict) -> None:
        self.busy = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.live_var.set("ready")
        text = res.get("text") or "(empty response)"
        self.last_reply_text = format_response(text)
        self._replace_reply(text)
        if res.get("tool_results"):
            lines = []
            has_error = False
            for t in res["tool_results"]:
                ok = bool(t.get("ok"))
                has_error = has_error or not ok
                label = f"[tool {t['name']}: {'ok' if ok else 'error'}]"
                if not ok and t.get("message"):
                    label += f" {str(t['message'])[:160]}"
                lines.append(label)
            self.append("\n" + " ".join(lines) + "\n", "tool_error" if has_error else "tool")
        if res.get("sources"):
            src = "\n".join(f"[{s['id']}] {s['path']}" + (f" - {s['heading']}" if s.get("heading") else "")
                            for s in _dedupe_source_items(res["sources"]))
            self.append("\nSOURCES\n" + src + "\n", "source")
        if res.get("elapsed"):
            self.status_var.set(f"Ready - {res['elapsed']}s")
        else:
            self.status_var.set("Ready")
        if (self.voice_active and self.voice and text and not res.get("command")
                and not res.get("voice_streamed")):
            self.voice.speak(text)
        if self.voice_active and self.voice:
            self.voice.turn_finished()

    def stop_generation(self) -> None:
        if not self.busy:
            return
        if hasattr(self, "stop_event"):
            self.stop_event.set()
        if self.voice_active and self.voice:
            self.voice.interrupt()
        self.live_var.set("stopping")
        self.status_var.set("Stopping ...")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "token":
                    self.idle.touch()
                    self.append(payload, "assistant", follow=False)
                elif kind == "status":
                    self.status_var.set(payload)
                    if "unloaded" in str(payload).lower():
                        self.model_var.set("Model: idle (starts on first use)")
                elif kind == "model":
                    self.model_var.set(f"Model: {Path(payload).stem}")
                    self._set_dot(GREEN)
                elif kind == "done":
                    self.idle.touch()
                    self._done(payload)
                elif kind == "wake":
                    self.idle.touch()
                    self.status_var.set("Wake word detected - listening ...")
                    self._ensure_server_async()
                elif kind == "error":
                    self.busy = False
                    self.send_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.live_var.set("ready")
                    self.append(f"\nERROR: {payload}\n", "error")
                    self.status_var.set("Error - see log")

                    if self.voice_active and self.voice:
                        self.voice.turn_finished()
                elif kind == "tooltext":
                    self.append(payload, "assistant")
                elif kind == "voice_state":
                    state = payload.lower()
                    self.voice_active = not any(x in state for x in ("off", "error", "setup needed", "disabled"))
                    self.voice_btn.configure(text=payload, bg=GREEN if self.voice_active else PANEL2,
                                             fg="#07101f" if self.voice_active else FG)
                    if hasattr(self, "voice_var"):
                        self.voice_var.set(payload)
                elif kind == "voice_text":
                    self.idle.touch()
                    self.submit(payload)
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _update_health(self) -> None:
        idle = self.idle.is_idle()
        if hasattr(self, "power_var"):
            self.power_var.set("Power: ultra-low" if idle else "Power: normal")
        if idle:
            self._set_dot(YELLOW)
            self.root.after(30000, self._update_health)
            return
        try:
            h = probe()
            st = self.mem.index_stats()
            gpu = h["gpus"][0] if h["gpus"] else {}
            server_up = self.server.health(0.4)
            server_state = "up" if server_up else "not loaded"
            self.stats_label.configure(
                text=(f"RAM   {h['ram']['available_gb']:.1f} / {h['ram']['total_gb']:.1f} GB\n"
                      f"VRAM  {gpu.get('vram_free_mb', '?')} MB free\n"
                      f"Docs  {st['documents']}  |  chunks {st['chunks']}\n"
                      f"Memories  {st['memories']}\n"
                      f"Server  {server_state}")
            )
            self._set_dot(GREEN if server_up else MUTED)
        except Exception:
            self._set_dot(MUTED)
        self.root.after(10000, self._update_health)

    # ---------------- confirmation / tools ----------------
    def confirm(self, title: str, detail: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(title, detail[:3500], parent=self.root)
        ev = threading.Event()
        result = {"ok": False}
        def ask():
            result["ok"] = messagebox.askyesno(title, detail[:3500], parent=self.root)
            ev.set()
        self.root.after(0, ask)
        ev.wait(timeout=600)
        return result["ok"]

    def run_tool(self, name: str, args: dict, show: bool = True) -> None:
        self.status_var.set(f"Running {name} ...")
        def work():
            res = execute(name, args, allow_dangerous=True)
            body = res.get("result") if res.get("ok") else f"ERROR: {res.get('error')}"
            if not isinstance(body, str):
                body = json.dumps(body, indent=2, ensure_ascii=False)
            self.q.put(("tooltext", f"\nTool: {name}\n{body}\n"))
            self.status_var.set("Ready")
        threading.Thread(target=work, daemon=True).start()

    def search_dialog(self) -> None:
        q = simpledialog.askstring("Search documents", "Query:", parent=self.root)
        if q:
            self.run_tool("search_documents", {"query": q})

    def index_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(title="Select folder to index", parent=self.root)
        if folder:
            self.status_var.set(f"Indexing {folder} ...")
            def work():
                res = execute("index_folder", {"path": folder})
                body = res.get("result") if res.get("ok") else f"ERROR: {res.get('error')}"
                self.q.put(("tooltext", f"\nIndex: {folder}\n{body}\n"))
                self.status_var.set("Indexing complete")
            threading.Thread(target=work, daemon=True).start()

    def reindex_configured(self) -> None:
        self.run_tool("index_folder", {"path": str(ROOT)})

    def document_dialog(self) -> None:
        f = filedialog.askopenfilename(title="Open document",
                                       filetypes=[("Documents", "*.pdf *.docx *.xlsx *.pptx"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("read_document", {"path": f})

    def kicad_dialog(self) -> None:
        f = filedialog.askopenfilename(title="KiCad file",
                                       filetypes=[("KiCad", "*.kicad_sch *.kicad_pcb"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("kicad_inspect", {"path": f})

    def bom_dialog(self) -> None:
        f = filedialog.askopenfilename(title="BOM CSV", filetypes=[("CSV", "*.csv *.tsv"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("bom_analyze", {"path": f})

    def email_dialog(self) -> None:
        f = filedialog.askopenfilename(title="Email file (.eml/.mbox)",
                                       filetypes=[("Email", "*.eml *.mbox"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("import_email", {"path": f})

    def calendar_import_dialog(self) -> None:
        f = filedialog.askopenfilename(title="Calendar ICS", filetypes=[("Calendar", "*.ics"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("import_calendar", {"path": f})

    def tasks_dialog(self) -> None:
        rows = self.mem.list_tasks()
        text = "\n".join(f"#{r['id']} [{r['status']}] {r['title']} ({r['due'] or 'no due date'})" for r in rows) or "No tasks."
        ans = simpledialog.askstring("Tasks", text + "\n\nEnter a new task title (Cancel to close):", parent=self.root)
        if ans:
            self.run_tool("add_task", {"title": ans})

    def quotation_dialog(self) -> None:
        self._invoice_dialog("create_quotation")
    def invoice_dialog(self) -> None:
        self._invoice_dialog("create_invoice")

    def _invoice_dialog(self, tool: str) -> None:
        customer = simpledialog.askstring("Customer", "Customer name:", parent=self.root)
        if not customer:
            return
        items = simpledialog.askstring(
            "Items", 'JSON list, e.g.\n[{"description":"PCB assembly","qty":10,"unit_price":25.5}]',
            parent=self.root)
        if not items:
            return
        tax = simpledialog.askstring("Tax", "Tax percent:", initialvalue="0", parent=self.root) or "0"
        out = filedialog.asksaveasfilename(title="Save document", defaultextension=".docx",
                                           filetypes=[("Word", "*.docx")], parent=self.root)
        if not out:
            return
        self.run_tool(tool, {"path": out, "customer": customer, "items": items, "tax_percent": tax})

    def resume_dialog(self) -> None:
        f = filedialog.askopenfilename(title="Resume", filetypes=[("Documents", "*.pdf *.docx"), ("All", "*.*")],
                                       parent=self.root)
        if f:
            self.run_tool("read_resume", {"path": f})

    def research_dialog(self) -> None:
        url = simpledialog.askstring("Fetch source", "Datasheet / article URL (external access, confirmation required):",
                                     parent=self.root)
        if url:
            self.run_tool("research_url", {"url": url})

    def research_question_dialog(self) -> None:
        q = simpledialog.askstring(
            "Research mode",
            "Ask a reasoning question about your research, project, or hackathon:",
            parent=self.root,
        )
        if q:
            self.set_module("research")
            self.submit(f"/research {q}")

    def hardware_dialog(self) -> None:
        self.run_tool("hardware_report", {})

    def show_help(self) -> None:
        messagebox.showinfo("THAMBI commands", self.engine_help(), parent=self.root)

    def show_utcp_manual(self) -> None:
        try:
            from .utcp import build_manual
            text = json.dumps(build_manual(), indent=2, ensure_ascii=False)
        except Exception as e:
            text = f"UTCP manual unavailable: {e}"
        win = tk.Toplevel(self.root)
        win.title("UTCP manual")
        win.geometry("820x680")
        win.configure(bg=BG)
        t = tk.Text(win, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                    font=("Consolas", 9), padx=12, pady=10)
        scroll = ttk.Scrollbar(win, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.configure(state="disabled")

    def engine_help(self) -> str:
        from .agent import HELP
        return HELP

    def show_about(self) -> None:
        messagebox.showinfo("About THAMBI",
                            "THAMBI 0.1 - private, offline-first assistant.\n"
                            "llama.cpp + Qwen2.5. SQLite memory. No cloud dependency.\n"
                            "Email is draft-only; destructive actions require confirmation.",
                            parent=self.root)

    def show_model_log(self) -> None:
        log = ROOT / "data" / "logs" / "llama-server.log"
        if not log.exists():
            messagebox.showinfo("Model log", "No log yet.", parent=self.root)
            return
        text = log.read_text(encoding="utf-8", errors="replace")[-12000:]
        win = tk.Toplevel(self.root)
        win.title("llama.cpp log")
        win.geometry("900x600")
        t = tk.Text(win, bg=BG, fg=FG, font=("Consolas", 9))
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.configure(state="disabled")

    def auto_clean(self) -> None:
        preview = execute("cleanup_cache", {"dry_run": True}, allow_dangerous=True)
        if not preview.get("ok"):
            messagebox.showerror("Auto Cleaner", preview.get("message") or "Preview failed.", parent=self.root)
            return
        detail = preview.get("result") or "No details available."
        if not messagebox.askyesno("THAMBI Auto Cleaner", detail + "\n\nRemove these items now?",
                                   parent=self.root):
            self.status_var.set("Auto Cleaner cancelled")
            return
        result = execute("cleanup_cache", {"dry_run": False, "confirmed": True}, allow_dangerous=True)
        if result.get("ok"):
            self.status_var.set("Auto Cleaner finished")
            messagebox.showinfo("Auto Cleaner", result.get("result") or "Done.", parent=self.root)
        else:
            self.status_var.set("Auto Cleaner failed")
            messagebox.showerror("Auto Cleaner", result.get("message") or "Cleanup failed.", parent=self.root)

    # ---------------- settings ----------------
    def settings_dialog(self) -> None:
        cfg = load()
        win = tk.Toplevel(self.root)
        win.title("THAMBI settings")
        win.configure(bg=BG)
        win.geometry("780x1120")
        fields = {}

        def add(parent, label, key, value, width=46):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, bg=BG, fg=MUTED, width=24, anchor="w").pack(side="left")
            if isinstance(value, bool):
                var = tk.BooleanVar(value=value)
                tk.Checkbutton(row, variable=var, bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                               activeforeground=FG).pack(side="left")
            else:
                var = tk.StringVar(value=str(value))
                tk.Entry(row, textvariable=var, bg=PANEL2, fg=FG, insertbackground=FG, relief="flat",
                         width=width).pack(side="left", fill="x", expand=True)
            fields[key] = var

        tk.Label(win, text="Model and inference", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f1 = tk.Frame(win, bg=BG); f1.pack(fill="x", padx=16)
        add(f1, "Context (tokens)", "context", cfg["llama"]["context"])
        add(f1, "GPU layers (99 = all)", "gpu_layers", cfg["llama"]["gpu_layers"])
        add(f1, "CPU threads", "threads", cfg["llama"]["threads"])
        add(f1, "Batch / ubatch", "batch", f"{cfg['llama']['batch']} / {cfg['llama']['ubatch']}")
        add(f1, "Max output tokens", "max_tokens", cfg["generation"]["max_tokens"])
        add(f1, "Coding max tokens", "coding_max_tokens", cfg["generation"].get("coding_max_tokens", 3072))
        add(f1, "Coding temperature", "coding_temperature", cfg["generation"].get("coding_temperature", 0.1))
        add(f1, "Max continuations", "continuations", cfg["generation"].get("max_continuations", 1))
        add(f1, "Temperature", "temperature", cfg["generation"]["temperature"])
        add(f1, "KV cache type", "kv_cache", cfg["llama"].get("kv_cache_type", "q8_0"))
        add(f1, "Adaptive VRAM context", "adaptive_context", bool(cfg["llama"].get("adaptive_context", True)))

        tk.Label(win, text="Memory and retrieval", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f5 = tk.Frame(win, bg=BG); f5.pack(fill="x", padx=16)
        add(f5, "History turns", "history_turns", cfg["memory"].get("history_turns", 10))
        add(f5, "Retrieval chunks", "retrieval_chunks", cfg["memory"].get("retrieval_chunks", 8))
        add(f5, "Max source chars", "max_context_chars", cfg["memory"].get("max_context_chars", 9000))

        tk.Label(win, text="Adaptive learning and documents", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f6 = tk.Frame(win, bg=BG); f6.pack(fill="x", padx=16)
        add(f6, "Learning enabled", "learning_enabled", bool(cfg["learning"].get("enabled", True)))
        add(f6, "Auto-learn from chat", "auto_learn", bool(cfg["learning"].get("auto_learn", True)))
        add(f6, "Max learned rules", "max_rules", cfg["learning"].get("max_rules", 12))
        add(f6, "Feedback learning", "feedback_learning", bool(cfg["learning"].get("feedback_enabled", True)))
        add(f6, "OCR enabled", "ocr_enabled", bool(cfg["documents"].get("ocr_enabled", True)))
        add(f6, "Max book pages", "max_pages", cfg["documents"].get("max_pages", 40))
        add(f6, "LibreOffice path", "soffice", cfg["documents"].get("soffice", ""))

        tk.Label(win, text="Privacy and confirmation", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f2 = tk.Frame(win, bg=BG); f2.pack(fill="x", padx=16)
        add(f2, "Allow internet access", "allow_research", bool(cfg["safety"].get("allow_external_research")))
        add(f2, "Allow read-only IMAP", "allow_imap", bool(cfg["safety"].get("allow_imap_read")))
        add(f2, "Confirm writes", "confirm_writes", bool(cfg["safety"].get("confirm_writes", True)))

        tk.Label(win, text="Index folders (one path per line)", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        folders = tk.Text(win, height=5, bg=PANEL2, fg=FG, insertbackground=FG, relief="flat", font=("Consolas", 9))
        folders.pack(fill="x", padx=16)
        folders.insert("1.0", "\n".join(cfg["index"].get("folders", [])))

        tk.Label(win, text="Voice and hot word", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f3 = tk.Frame(win, bg=BG); f3.pack(fill="x", padx=16)
        add(f3, "Wake word", "wake_word", cfg["voice"].get("wake_word", "hey thambi"))
        add(f3, "Microphone device index", "mic_device", cfg["voice"].get("mic_device", "") or "")
        add(f3, "Text to speech", "tts_enabled", bool(cfg["voice"].get("tts_enabled", True)))
        add(f3, "Stream speech live", "stream_tts", bool(cfg["voice"].get("stream_tts", True)))
        add(f3, "Auto-start hot word", "voice_auto", bool(cfg["voice"].get("auto_start", True)))
        add(f3, "Always listen", "always_listen", bool(cfg["voice"].get("always_listen", True)))
        add(f3, "Handsfree follow-ups", "continuous", bool(cfg["voice"].get("continuous", True)))
        add(f3, "Follow-up wait (s)", "followup_timeout", cfg["voice"].get("followup_timeout_s", 8))

        tk.Label(win, text="Idle / power / UTCP", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(14, 4))
        f4 = tk.Frame(win, bg=BG); f4.pack(fill="x", padx=16)
        add(f4, "Idle timeout (s)", "idle_timeout", cfg["idle"].get("timeout_s", 45))
        add(f4, "Lazy model load", "lazy_model", bool(cfg["idle"].get("lazy_model", True)))
        add(f4, "Unload model after (s)", "unload_after", cfg["idle"].get("unload_model_after_s", 900))
        add(f4, "UTCP gateway", "utcp_enabled", bool(cfg["utcp"].get("enabled", True)))
        add(f4, "Research max tokens", "research_max", cfg["generation"].get("research_max_tokens", 1600))

        tk.Label(win, text="Model/context changes take effect after Restart model server.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(12, 0))

        def do_save():
            try:
                cfg["llama"]["context"] = int(fields["context"].get())
                cfg["llama"]["gpu_layers"] = int(fields["gpu_layers"].get())
                cfg["llama"]["threads"] = int(fields["threads"].get())
                batch_parts = [x.strip() for x in fields["batch"].get().split("/")]
                cfg["llama"]["batch"] = int(batch_parts[0])
                cfg["llama"]["ubatch"] = int(batch_parts[1]) if len(batch_parts) > 1 else cfg["llama"]["ubatch"]
                cfg["llama"]["kv_cache_type"] = fields["kv_cache"].get().strip()
                cfg["llama"]["adaptive_context"] = bool(fields["adaptive_context"].get())
                cfg["generation"]["max_tokens"] = int(fields["max_tokens"].get())
                cfg["generation"]["coding_max_tokens"] = int(fields["coding_max_tokens"].get())
                cfg["generation"]["coding_temperature"] = float(fields["coding_temperature"].get())
                cfg["generation"]["max_continuations"] = max(0, int(fields["continuations"].get()))
                cfg["generation"]["temperature"] = float(fields["temperature"].get())
                cfg["memory"]["history_turns"] = max(1, int(fields["history_turns"].get()))
                cfg["memory"]["retrieval_chunks"] = max(1, int(fields["retrieval_chunks"].get()))
                cfg["memory"]["max_context_chars"] = max(1000, int(fields["max_context_chars"].get()))
                cfg["learning"]["enabled"] = bool(fields["learning_enabled"].get())
                cfg["learning"]["auto_learn"] = bool(fields["auto_learn"].get())
                cfg["learning"]["max_rules"] = max(1, int(fields["max_rules"].get()))
                cfg["learning"]["feedback_enabled"] = bool(fields["feedback_learning"].get())
                cfg["documents"]["ocr_enabled"] = bool(fields["ocr_enabled"].get())
                cfg["documents"]["max_pages"] = max(1, int(fields["max_pages"].get()))
                cfg["documents"]["soffice"] = fields["soffice"].get().strip()
                cfg["safety"]["allow_external_research"] = bool(fields["allow_research"].get())
                cfg["safety"]["allow_imap_read"] = bool(fields["allow_imap"].get())
                cfg["safety"]["confirm_writes"] = bool(fields["confirm_writes"].get())
                cfg["index"]["folders"] = [x.strip() for x in folders.get("1.0", "end").splitlines() if x.strip()]
                cfg["voice"]["wake_word"] = fields["wake_word"].get().strip().lower()
                md = fields["mic_device"].get().strip()
                cfg["voice"]["mic_device"] = int(md) if md.isdigit() else None
                cfg["voice"]["tts_enabled"] = bool(fields["tts_enabled"].get())
                cfg["voice"]["stream_tts"] = bool(fields["stream_tts"].get())
                cfg["voice"]["auto_start"] = bool(fields["voice_auto"].get())
                cfg["voice"]["always_listen"] = bool(fields["always_listen"].get())
                cfg["voice"]["continuous"] = bool(fields["continuous"].get())
                cfg["voice"]["followup_timeout_s"] = float(fields["followup_timeout"].get())
                cfg["idle"]["timeout_s"] = float(fields["idle_timeout"].get())
                cfg["idle"]["lazy_model"] = bool(fields["lazy_model"].get())
                cfg["idle"]["unload_model_after_s"] = float(fields["unload_after"].get())
                cfg["utcp"]["enabled"] = bool(fields["utcp_enabled"].get())
                cfg["generation"]["research_max_tokens"] = int(fields["research_max"].get())
                save(cfg)
                win.destroy()
                messagebox.showinfo("Settings", "Saved. Settings for the running model require a restart.", parent=self.root)
            except ValueError as e:
                messagebox.showerror("Settings", f"Invalid value: {e}", parent=win)

        tk.Button(win, text="Save", command=do_save, bg=ACCENT, fg="#07101f", relief="flat",
                  padx=18, pady=6).pack(anchor="e", padx=16, pady=12)

    # ---------------- voice ----------------
    def toggle_voice(self) -> None:
        try:
            if self.voice is None:
                self._make_voice()
        except Exception as e:
            messagebox.showerror("Voice", f"Voice module unavailable: {e}", parent=self.root)
            return
        if not self.voice_active:
            if not self.voice.available():
                messagebox.showerror("Voice setup",
                                     "Voice models are missing. Run:\n\nscripts\\setup_voice.ps1\n\n"
                                     "This downloads whisper.cpp, the STT model, Piper voice, and wake-word model.",
                                     parent=self.root)
                return
            try:
                self.voice.start()
            except Exception as e:
                messagebox.showerror("Voice", f"Could not start voice: {e}", parent=self.root)
                return
            self.voice_active = True
            self.voice_btn.configure(text="Voice: on", bg=GREEN, fg="#07101f")
            self.voice_var.set("Voice: on")
        else:
            self.voice.stop()
            self.voice_active = False
            self.voice_btn.configure(text="Voice: off", bg=PANEL2, fg=FG)
            self.voice_var.set("Voice: off")

    def push_to_talk(self) -> None:
        if self.voice is None:
            self.toggle_voice()
            if not self.voice_active:
                return
        self.idle.touch()
        self.voice.listen_once()

    def close(self) -> None:
        try:
            if self.voice:
                self.voice.stop()
        except Exception:
            pass
        try:
            self.idle.stop()
        except Exception:
            pass
        try:
            if self.utcp:
                self.utcp.stop()
        except Exception:
            pass
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.mem.close()
        except Exception:
            pass
        self.root.destroy()


def run() -> None:
    ui = AetherUI()
    ui.root.mainloop()
