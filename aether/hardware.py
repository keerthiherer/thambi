from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ROOT, load, resolve


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram() -> dict:
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return {
        "total_gb": round(st.ullTotalPhys / 2**30, 2),
        "available_gb": round(st.ullAvailPhys / 2**30, 2),
        "load_percent": st.dwMemoryLoad,
    }


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _gpu() -> list[dict]:
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            try:
                gpus.append({
                    "name": parts[0],
                    "vram_total_mb": int(float(parts[1])),
                    "vram_free_mb": int(float(parts[2])),
                    "driver": parts[3],
                    "compute_capability": parts[4],
                })
            except ValueError:
                pass
    if not gpus:
        wmic = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + '|' + $_.AdapterRAM })"])
        for line in wmic.strip().splitlines():
            if "|" in line:
                name, ram = line.rsplit("|", 1)
                try:
                    gpus.append({"name": name, "vram_total_mb": int(int(ram) / 2**20), "vram_free_mb": None})
                except ValueError:
                    gpus.append({"name": name, "vram_total_mb": None, "vram_free_mb": None})
    return gpus


def _cpu() -> dict:
    name = platform.processor()
    out = _run(["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:csv"])
    cores = logical = None
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[-1]:
            try:
                name = parts[1]
                cores = int(parts[2])
                logical = int(parts[3])
            except (ValueError, IndexError):
                pass
    if not logical:
        logical = os.cpu_count()
    return {"name": name, "cores": cores, "logical": logical}


def _storage() -> list[dict]:
    native = ROOT / "aether" / "native" / "aether_native.exe"
    disks = []
    if native.exists():
        out = _run([str(native), "disk"])
        for line in out.splitlines():
            try:
                d = json.loads(line)
                disks.append({
                    "drive": d["drive"],
                    "free_gb": round(d["free"] / 2**30, 2),
                    "total_gb": round(d["total"] / 2**30, 2),
                })
            except (ValueError, KeyError):
                pass
    if not disks:
        total, used, free = shutil.disk_usage(ROOT)
        disks.append({"drive": str(ROOT.drive), "free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2)})
    return disks


def _tools() -> dict:
    exe = shutil.which
    cfg = load()
    kicad_cli = resolve(cfg["paths"].get("kicad_cli", ""))
    native = ROOT / "aether" / "native" / "aether_native.exe"
    return {
        "native_helper": native.exists(),
        "llama_server": resolve(cfg["llama"]["server"]).exists(),
        "python": sys.version.split()[0],
        "git": exe("git"),
        "gcc": exe("gcc"),
        "cmake": exe("cmake"),
        "kicad_cli": str(kicad_cli) if kicad_cli.exists() else exe("kicad-cli"),
        "ngspice": exe("ngspice") or (cfg["paths"].get("ngspice") or None),
        "openems": exe("openEMS") or (cfg["paths"].get("openems") or None),
        "ffmpeg": exe("ffmpeg"),
    }


def probe() -> dict:
    cfg = load()
    ram = _ram()
    gpus = _gpu()
    storage = _storage()
    tools = _tools()
    primary = resolve(cfg["model"]["primary"])
    fallback = resolve(cfg["model"]["fallback"])
    vram_free = max([g.get("vram_free_mb") or 0 for g in gpus], default=0)
    vram_total = max([g.get("vram_total_mb") or 0 for g in gpus], default=0)
    can_3b = primary.exists() and ram["total_gb"] >= 7 and (vram_free >= 1500 or vram_total >= 3800)
    can_1_5b = fallback.exists() and ram["total_gb"] >= 6
    if primary.exists() and (can_3b or vram_free >= 1500):
        recommendation = str(primary)
    elif fallback.exists() and (can_1_5b or vram_free >= 1000):
        recommendation = str(fallback)
    elif primary.exists():
        recommendation = str(primary)
    else:
        recommendation = None
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu(),
        "ram": ram,
        "gpus": gpus,
        "storage": storage,
        "tools": tools,
        "models": {
            "primary_present": primary.exists(),
            "fallback_present": fallback.exists(),
            "recommendation": recommendation,
        },
        "notes": [
            "llama.cpp uses CUDA with CPU fallback; one model is loaded at a time.",
            "Voice models load only while voice mode is active.",
            "KiCad CLI is used only for read-only inspection unless you confirm an export.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(probe(), indent=2))
