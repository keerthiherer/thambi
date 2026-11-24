from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import requests

from .config import LOG_DIR, ROOT, ensure_dirs, get, load, resolve

LOCAL = {"http": None, "https": None}


class LLMError(RuntimeError):
    pass


def _free_vram_mb() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000,
        ).stdout
        values = [int(float(line.strip())) for line in out.splitlines() if line.strip()]
        return max(values) if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _kv_bytes_per_token(kind: str, model_name: str = "") -> int:
    if "1.5b" in model_name.lower():
        elements = 2 * 28 * 2 * 128
    else:
        elements = 2 * 36 * 8 * 128
    factor = {"q8_0": 1.1, "q4_0": 0.6, "q4_1": 0.65, "q5_0": 0.75, "q5_1": 0.8, "f16": 2.0, "f32": 4.0}
    return max(1, int(elements * factor.get(kind.lower(), 1.1)))


def _safe_context(cfg: dict, model: Path) -> tuple[int, str | None]:
    requested = int(cfg["llama"]["context"])
    if not cfg["llama"].get("adaptive_context", True):
        return requested, None
    free = _free_vram_mb()
    if free is None:
        return requested, None
    try:
        model_mb = model.stat().st_size / 2**20
    except OSError:
        return requested, None
    kv = _kv_bytes_per_token(str(cfg["llama"].get("kv_cache_type", "q8_0")), model.name)
    available_mb = free - model_mb - 800
    if available_mb <= 0:
        reduced = min(requested, 2048)
        return reduced, f"Low free VRAM ({free} MB); context reduced {requested} -> {reduced} tokens"
    cap = max(2048, (int(available_mb * 2**20 / kv) // 512) * 512)
    if cap >= requested:
        return requested, None
    return cap, f"VRAM-aware context reduced {requested} -> {cap} tokens"


class LlamaServer:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.active_model: Path | None = None
        self.context: int | None = None
        self.started_at: float = 0.0
        self._lock = threading.RLock()

    @property
    def host(self) -> str:
        return get("llama.host", "127.0.0.1")

    @property
    def port(self) -> int:
        return int(get("llama.port", 8181))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def health(self, timeout: float = 1.0) -> dict | None:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=timeout, proxies=LOCAL)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            return None
        return None

    def is_running(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        return self.health() is not None

    def _log_tail(self, n: int = 60) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def _running_context(self) -> int | None:
        try:
            r = requests.get(f"{self.base_url}/props", timeout=1.0, proxies=LOCAL)
            if r.status_code != 200:
                return None
            data = r.json()
            settings = data.get("default_generation_settings") or {}
            for key in ("n_ctx", "context"):
                if settings.get(key):
                    return int(settings[key])
        except (requests.RequestException, ValueError, TypeError):
            return None
        return None

    def _start_one(self, model: Path, timeout: int, progress: Callable[[str], None] | None = None) -> None:
        cfg = load()
        server = resolve(cfg["llama"]["server"])
        if not server.exists():
            raise LLMError(f"llama-server not found: {server}. Run scripts\\download_llama.ps1")
        if not model.exists():
            raise LLMError(f"Model not found: {model}. Run scripts\\download_model.ps1")
        ensure_dirs()
        context, note = _safe_context(cfg, model)
        if note and progress:
            progress(note)
        self.log_path = LOG_DIR / "llama-server.log"
        self.log_path.write_text("", encoding="utf-8")
        args = [
            str(server),
            "-m", str(model),
            "--host", self.host,
            "--port", str(self.port),
            "-c", str(context),
            "-ngl", str(cfg["llama"]["gpu_layers"]),
            "-t", str(cfg["llama"]["threads"]),
            "-b", str(cfg["llama"]["batch"]),
            "-ub", str(cfg["llama"]["ubatch"]),
            "-np", "1",
            "-fa", "on" if cfg["llama"].get("flash_attn") else "off",
            "-ctk", str(cfg["llama"].get("kv_cache_type", "q8_0")),
            "-ctv", str(cfg["llama"].get("kv_cache_type", "q8_0")),
        ] + [str(a) for a in cfg["llama"].get("extra_args", [])]
        logf = self.log_path.open("ab", buffering=0)
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            args, cwd=str(server.parent), stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
        self.active_model = model
        self.context = context
        self.started_at = time.time()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise LLMError(f"llama-server exited with code {self.proc.returncode}.\n{self._log_tail()}")
            h = self.health(timeout=1.0)
            if h and h.get("status") in ("ok", "ready", None):
                return
            if progress and int(time.time() - self.started_at) % 5 == 0:
                progress("Loading model into memory ...")
            time.sleep(0.5)
        self.stop()
        raise LLMError(f"llama-server did not become ready in {timeout}s.\n{self._log_tail()}")

    def start(self, progress: Callable[[str], None] | None = None) -> Path:
        with self._lock:
            if self.health():
                self.active_model = resolve(get("model.primary"))
                self.context = self._running_context() or int(get("llama.context", 8192))
                return self.active_model
            cfg = load()
            primary = resolve(cfg["model"]["primary"])
            fallback = resolve(cfg["model"]["fallback"])
            timeout = int(cfg["llama"].get("start_timeout", 150))
            try:
                if progress:
                    progress(f"Starting llama.cpp with {primary.name} ...")
                self._start_one(primary, timeout, progress)
                return primary
            except Exception as e:
                primary_err = e
                if not cfg["model"].get("auto_fallback", True) or not fallback.exists():
                    raise
                if progress:
                    progress(f"Primary model failed. Trying fallback {fallback.name} ...")
                self.stop()
                try:
                    self._start_one(fallback, timeout, progress)
                    return fallback
                except Exception as e2:
                    self.stop()
                    raise LLMError(f"Primary failed: {primary_err}\nFallback failed: {e2}") from e2

    def stop(self) -> None:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        self.proc.kill()
                    except OSError:
                        pass
                except OSError:
                    pass
            self.proc = None
            self.active_model = None
            self.context = None

    def memory_snapshot(self) -> dict:
        pid = self.proc.pid if self.proc and self.proc.poll() is None else None
        rss_mb = None
        if pid:
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-Process -Id {pid}).WorkingSet64"],
                    capture_output=True, text=True, timeout=10, creationflags=0x08000000,
                ).stdout.strip()
                if out.isdigit():
                    rss_mb = round(int(out) / 2**20, 1)
            except (OSError, subprocess.SubprocessError):
                pass
        vram = None
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, creationflags=0x08000000,
            ).stdout
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 2 and pid and parts[0] == str(pid):
                    vram = int(float(parts[1]))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return {"pid": pid, "rss_mb": rss_mb, "vram_mb": vram}


def _messages_payload(messages: list[dict], stream: bool,
                      max_tokens: int | None = None,
                      temperature: float | None = None) -> dict:
    cfg = load()
    gen = cfg["generation"]
    return {
        "messages": messages,
        "temperature": gen["temperature"] if temperature is None else temperature,
        "top_p": gen["top_p"],
        "max_tokens": gen["max_tokens"] if max_tokens is None else max_tokens,
        "repeat_penalty": gen["repeat_penalty"],
        "stream": stream,
        "cache_prompt": True,
    }


def chat(messages: list[dict], server: LlamaServer | None = None,
         stop_event: threading.Event | None = None,
         max_tokens: int | None = None,
         temperature: float | None = None) -> dict:
    server = server or LlamaServer()
    url = f"{server.base_url}/v1/chat/completions"
    try:
        r = requests.post(url, json=_messages_payload(messages, False, max_tokens, temperature),
                          timeout=(10, 300), proxies=LOCAL)
    except requests.RequestException as e:
        raise LLMError(f"llama-server request failed: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"llama-server HTTP {r.status_code}: {r.text[:800]}")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected llama-server response: {str(data)[:500]}") from e
    finish = None
    choices = data.get("choices") or []
    if choices:
        finish = choices[0].get("finish_reason")
    return {"text": text, "usage": data.get("usage", {}), "finish_reason": finish, "raw": data}


def stream(messages: list[dict], on_token: Callable[[str], None],
           server: LlamaServer | None = None, stop_event: threading.Event | None = None,
           max_tokens: int | None = None, temperature: float | None = None) -> dict:
    server = server or LlamaServer()
    url = f"{server.base_url}/v1/chat/completions"
    try:
        r = requests.post(url, json=_messages_payload(messages, True, max_tokens, temperature), stream=True,
                          timeout=(10, 300), proxies=LOCAL)
    except requests.RequestException as e:
        raise LLMError(f"llama-server request failed: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"llama-server HTTP {r.status_code}: {r.text[:800]}")
    parts: list[str] = []
    usage: dict = {}
    finish_reason: str | None = None
    stopped = False
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if data.get("usage"):
                usage = data["usage"]
            choices = data.get("choices") or []
            if choices:
                finish = choices[0].get("finish_reason")
                if finish:
                    finish_reason = finish
                delta = choices[0].get("delta") or {}
                token = delta.get("content") or ""
                if token:
                    parts.append(token)
                    on_token(token)
    finally:
        r.close()
    return {"text": "".join(parts), "usage": usage, "finish_reason": finish_reason, "stopped": stopped}


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head)
    return text[:head] + f"\n... [{len(text) - limit} chars omitted] ...\n" + text[-tail:]


def prepare_context(messages: list[dict], max_tokens: int | None = None,
                    context: int | None = None) -> list[dict]:
    """Keep system rules, reserve room for chat history, and drop only whole old turns."""
    cfg = load()
    ctx = int(context or cfg["llama"]["context"])
    reserve = int(max_tokens or cfg["generation"]["max_tokens"])
    reserve = min(reserve, max(256, ctx - 1024))
    per_msg = max(1000, int(cfg["memory"].get("max_message_chars", 6000)))
    budget_chars = max(2000, int((ctx - reserve - 256) * 3.0))
    head: list[dict] = []
    body = list(messages)
    while body and body[0].get("role") == "system":
        head.append(body.pop(0))

    head_budget = max(1200, int(budget_chars * 0.72)) if body else budget_chars
    if head:
        first = str(head[0].get("content", ""))
        first_cap = max(600, head_budget - 300 * (len(head) - 1))
        if len(first) > first_cap:
            head[0] = dict(head[0])
            head[0]["content"] = _clip_text(first, first_cap)
        remaining_head = max(300, head_budget - len(str(head[0].get("content", ""))))
        for i in range(1, len(head)):
            content = str(head[i].get("content", ""))
            cap = max(300, remaining_head // (len(head) - i))
            if len(content) > cap:
                head[i] = dict(head[i])
                head[i]["content"] = _clip_text(content, cap)
                content = head[i]["content"]
            remaining_head = max(0, remaining_head - len(content))

    total = sum(len(str(m.get("content", ""))) for m in head)
    kept: list[dict] = []
    for m in reversed(body):
        content = str(m.get("content", ""))
        if len(content) > per_msg:
            m = dict(m)
            m["content"] = _clip_text(content, per_msg)
            content = m["content"]
        remaining = budget_chars - total
        if remaining < 400 and kept:
            break
        if len(content) > remaining:
            target = per_msg if not kept else max(800, remaining)
            if len(content) > target:
                m = dict(m)
                m["content"] = _clip_text(content, target)
                content = m["content"]
        kept.append(m)
        total += len(content)
    kept.reverse()
    while kept and kept[0].get("role") != "user":
        kept.pop(0)
    return head + kept
