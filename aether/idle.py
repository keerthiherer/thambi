from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Callable

from .config import get

IDLE_PRIORITY_CLASS = 0x00000040
NORMAL_PRIORITY_CLASS = 0x00000020


class IdleManager:
    """Ultra-low-power idle governor.

    When there is no interaction for a while it:
      - drops the process to Windows IDLE_PRIORITY_CLASS;
      - trims the working set (EmptyWorkingSet);
      - optionally unloads the llama.cpp model to free RAM/VRAM.
    The hot-word listener keeps running the whole time at one thread.
    """

    def __init__(self, server: Any | None = None,
                 on_state: Callable[[str], None] | None = None) -> None:
        self.server = server
        self.on_state = on_state or (lambda _s: None)
        self._lock = threading.RLock()
        self._last = time.monotonic()
        self._idle = False
        self._unloaded = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_priority = False

    def touch(self) -> None:
        with self._lock:
            self._last = time.monotonic()
            if self._idle:
                self._idle = False
                self._unloaded = False
                self._apply_priority(False)
                self.on_state("Idle: off - normal power")

    def is_idle(self) -> bool:
        with self._lock:
            return self._idle

    def idle_seconds(self) -> float:
        with self._lock:
            return max(0.0, time.monotonic() - self._last)

    def start(self) -> None:
        if not get("idle.enabled", True):
            return
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._loop, name="aether-idle", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._apply_priority(False)

    def tick(self) -> None:
        if not get("idle.enabled", True):
            return
        idle_for = self.idle_seconds()
        timeout = float(get("idle.timeout_s", 45))
        if idle_for >= timeout:
            with self._lock:
                if not self._idle:
                    self._idle = True
                    self._apply_priority(True)
                    self._trim_memory()
                    self.on_state("Idle: ultra-low power")
            unload_after = float(get("idle.unload_model_after_s", 900))
            if unload_after > 0 and idle_for >= unload_after:
                self._maybe_unload()

    def _maybe_unload(self) -> None:
        if self.server is None or self._unloaded:
            return
        try:
            running = bool(self.server.is_running())
        except Exception:
            running = False
        if not running:
            return
        try:
            self.server.stop()
            self._unloaded = True
            self.on_state("Model unloaded (idle) - will reload on next request")
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stopped.wait(2.0)

    def _apply_priority(self, idle: bool) -> None:
        if not get("idle.priority", True):
            return
        if self._active_priority == idle:
            return
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            value = IDLE_PRIORITY_CLASS if idle else NORMAL_PRIORITY_CLASS
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), value)
            self._active_priority = idle
        except Exception:
            pass

    def _trim_memory(self) -> None:
        if not get("idle.trim_memory", True):
            return
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
            psapi.EmptyWorkingSet(kernel32.GetCurrentProcess())
        except Exception:
            pass
