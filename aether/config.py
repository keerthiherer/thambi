from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "aether.json"
DATA_DIR = ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
DRAFT_DIR = DATA_DIR / "drafts"

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "model": {
        "primary": "models/qwen2.5-3b-instruct-q4_k_m.gguf",
        "fallback": "models/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "auto_fallback": True,
    },
    "llama": {
        "server": "runtime/llama/llama-server.exe",
        "host": "127.0.0.1",
        "port": 8181,
        "gpu_layers": 99,
        "context": 8192,
        "adaptive_context": True,
        "threads": 6,
        "batch": 512,
        "ubatch": 256,
        "flash_attn": True,
        "kv_cache_type": "q8_0",
        "start_timeout": 150,
        "extra_args": ["--jinja", "--no-warmup"],
    },
    "generation": {
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 2048,
        "coding_max_tokens": 3072,
        "coding_temperature": 0.1,
        "repeat_penalty": 1.08,
        "max_continuations": 1,
        "research_max_tokens": 3000,
        "research_temperature": 0.3,
    },
    "memory": {
        "db": "data/aether.db",
        "retrieval_chunks": 8,
        "max_context_chars": 9000,
        "max_message_chars": 6000,
        "history_turns": 10,
    },
    "index": {
        "folders": [],
        "extensions": [".txt", ".md", ".py", ".c", ".h", ".cpp", ".hpp", ".json", ".csv", ".pdf", ".docx", ".xlsx", ".pptx", ".eml"],
        "exclude_dirs": [".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".cache", "AppData", "Windows", "Program Files"],
        "max_file_mb": 10,
    },
    "voice": {
        "enabled": True,
        "auto_start": True,
        "always_listen": True,
        "continuous": True,
        "followup_timeout_s": 8,
        "turn_timeout_s": 120,
        "wake_word": "hey thambi",
        "mic_device": None,
        "sample_rate": 16000,
        "whisper_binary": "runtime/whisper/whisper-cli.exe",
        "whisper_model": "models/ggml-base.en.bin",
        "piper_model": "models/piper/en_US-lessac-medium.onnx",
        "kws_model_dir": "models/kws",
        "vad_silence_ms": 900,
        "vad_threshold": 0.012,
        "vad_start_timeout_s": 6,
        "max_record_seconds": 15,
        "wake_threshold": 0.25,
        "whisper_threads": 6,
        "tts_enabled": True,
        "stream_tts": True,
    },
    "idle": {
        "enabled": True,
        "timeout_s": 45,
        "lazy_model": True,
        "unload_model_after_s": 900,
        "priority": True,
        "trim_memory": True,
        "kws_threads": 1,
        "energy_gate": True,
        "energy_threshold": 0.006,
        "pre_roll_blocks": 3,
    },
    "utcp": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8191,
        "token": "",
        "allow_remote": False,
    },
    "safety": {
        "allow_external_research": False,
        "allow_imap_read": False,
        "confirm_writes": True,
        "allowed_roots": [],
        "app_allowlist": {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "explorer": "explorer.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
            "code": "code.cmd",
            "kicad": "C:/Program Files/KiCad/10.0/bin/kicad.exe",
            "paint": "mspaint.exe",
            "edge": "msedge.exe",
            "chrome": "chrome.exe",
        },
    },
    "email": {
        "imap_host": "",
        "imap_port": 993,
        "imap_folder": "INBOX",
        "imap_user_env": "AETHER_IMAP_USER",
        "imap_password_env": "AETHER_IMAP_PASSWORD",
    },
    "learning": {
        "enabled": True,
        "auto_learn": True,
        "max_rules": 12,
        "min_confidence": 0.35,
        "feedback_enabled": True,
    },
    "documents": {
        "ocr_language": "eng",
        "ocr_enabled": True,
        "max_pages": 40,
        "max_chars": 30000,
        "soffice": "C:/Program Files/LibreOffice/program/soffice.exe",
    },
    "designer": {
        "canva_url": "https://www.canva.com/",
        "default_theme": "midnight",
        "default_ratio": "16:9",
    },
    "paths": {"ngspice": "", "openems": "", "kicad_cli": "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"},
    "ui": {"theme": "dark", "max_chat_chars": 12000},
}

_lock = threading.RLock()
_config: dict[str, Any] | None = None
_config_fingerprint: tuple[int, int, int] = (0, 0, 0)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _fingerprint() -> tuple[int, int, int]:
    try:
        st = CONFIG_PATH.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return (0, 0, 0)


def load() -> dict[str, Any]:
    global _config, _config_fingerprint
    with _lock:
        current = _fingerprint()
        if _config is None or current != _config_fingerprint:
            if CONFIG_PATH.exists():
                with CONFIG_PATH.open("r", encoding="utf-8") as f:
                    user = json.load(f)
            else:
                user = {}
            fresh = _merge(DEFAULTS, user)
            if _config is None:
                _config = fresh
            else:
                _config.clear()
                _config.update(fresh)
            _config_fingerprint = current
        return _config


def save(cfg: dict[str, Any]) -> None:
    global _config, _config_fingerprint
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
        snapshot = copy.deepcopy(cfg)
        if _config is None:
            _config = snapshot
        else:
            _config.clear()
            _config.update(snapshot)
        _config_fingerprint = _fingerprint()


def get(path: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, DRAFT_DIR, DATA_DIR / "voice", DATA_DIR / "attachments", DATA_DIR / "index"):
        d.mkdir(parents=True, exist_ok=True)
