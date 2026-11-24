from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from .. import safety
from ..config import DATA_DIR, load, resolve
from .registry import register


def _tokenize(text: str) -> list[str]:
    toks: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            toks.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                elif ch == '"':
                    i += 1
                    break
                else:
                    buf.append(ch)
                    i += 1
            toks.append('"' + "".join(buf))
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            toks.append(text[i:j])
            i = j
    return toks


def parse_sexpr(text: str):
    toks = _tokenize(text)
    stack: list[list] = [[]]
    for t in toks:
        if t == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            if t.startswith('"'):
                stack[-1].append(t[1:])
            else:
                stack[-1].append(t)
    return stack[0][0] if stack[0] else []


def _children(node: list, name: str) -> list[list]:
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def _first(node: list, name: str):
    for x in node:
        if isinstance(x, list) and x and x[0] == name:
            return x
    return None


def _prop(node: list, key: str) -> str:
    for p in _children(node, "property"):
        if len(p) >= 3 and str(p[1]).lower() == key.lower():
            return str(p[2])
    for p in _children(node, "fp_text"):
        if len(p) >= 3 and str(p[1]).lower() == key.lower():
            return str(p[2])
    return ""


def _load(path: str) -> tuple[Path, list]:
    p = safety.validate_read(path)
    if p.suffix.lower() not in (".kicad_sch", ".kicad_pcb"):
        raise ValueError("Expected a .kicad_sch or .kicad_pcb file")
    if p.stat().st_size > 80 * 1024 * 1024:
        raise ValueError("File too large for in-process inspection (80 MB limit)")
    text = p.read_text(encoding="utf-8", errors="replace")
    return p, parse_sexpr(text)


def _inspect_sch(p: Path, root: list) -> dict:
    comps = []
    labels = []
    power = []
    for node in _children(root, "symbol"):
        lib = _first(node, "lib_id")
        ref = _prop(node, "Reference")
        val = _prop(node, "Value")
        fp = _prop(node, "Footprint")
        dnp = _prop(node, "DNP")
        if not ref:
            continue
        item = {"reference": ref, "value": val, "footprint": fp, "lib_id": lib[1] if lib and len(lib) > 1 else "", "dnp": dnp}
        if ref.startswith("#"):
            power.append(item)
        else:
            comps.append(item)
    for kind in ("label", "global_label", "hierarchical_label"):
        for node in _children(root, kind):
            if len(node) > 1 and isinstance(node[1], str):
                labels.append({"name": node[1], "type": kind})
    tb = _first(root, "title_block")
    title = {}
    if tb:
        for key in ("title", "date", "rev", "company", "comment"):
            vals = _children(tb, key)
            if vals:
                title[key] = " ".join(str(x) for x in vals[0][1:] if isinstance(x, str))
    counts = Counter(c["value"] for c in comps)
    return {
        "file": str(p),
        "type": "schematic",
        "title_block": title,
        "components": len(comps),
        "power_symbols": len(power),
        "unique_values": len(counts),
        "labels": len(labels),
        "wires": len(_children(root, "wire")),
        "junctions": len(_children(root, "junction")),
        "sheets": len(_children(root, "sheet")),
        "bom": comps,
        "label_names": labels[:200],
        "duplicate_refs": [r for r, n in Counter(c["reference"] for c in comps).items() if n > 1],
        "missing_footprints": [c["reference"] for c in comps if not c["footprint"]],
    }


def _edge_bbox(root: list) -> dict | None:
    xs: list[float] = []
    ys: list[float] = []
    for kind in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
        for node in _children(root, kind):
            layer = _first(node, "layer")
            if not layer or len(layer) < 2 or str(layer[1]) != "Edge.Cuts":
                continue
            for key in ("start", "end", "mid", "center", "at"):
                pt = _first(node, key)
                if pt and len(pt) >= 3:
                    try:
                        xs.append(float(pt[1]))
                        ys.append(float(pt[2]))
                    except (ValueError, TypeError):
                        pass
            for xy in _children(node, "xy"):
                if len(xy) >= 3:
                    try:
                        xs.append(float(xy[1]))
                        ys.append(float(xy[2]))
                    except (ValueError, TypeError):
                        pass
    if not xs or not ys:
        return None
    return {"x_min_mm": min(xs), "x_max_mm": max(xs), "y_min_mm": min(ys), "y_max_mm": max(ys),
            "width_mm": max(xs) - min(xs), "height_mm": max(ys) - min(ys)}


def _inspect_pcb(p: Path, root: list) -> dict:
    fps = []
    for fp in _children(root, "footprint"):
        ref = _prop(fp, "Reference")
        val = _prop(fp, "Value")
        layer = _first(fp, "layer")
        at = _first(fp, "at")
        fps.append({
            "reference": ref,
            "value": val,
            "footprint": fp[1] if len(fp) > 1 and isinstance(fp[1], str) else "",
            "layer": layer[1] if layer and len(layer) > 1 else "",
            "x_mm": float(at[1]) if at and len(at) > 2 else None,
            "y_mm": float(at[2]) if at and len(at) > 2 else None,
        })
    tracks = []
    for seg in _children(root, "segment"):
        w = _first(seg, "width")
        s = _first(seg, "start")
        e = _first(seg, "end")
        tracks.append({
            "width_mm": float(w[1]) if w and len(w) > 1 else None,
            "length_mm": ((float(e[1]) - float(s[1])) ** 2 + (float(e[2]) - float(s[2])) ** 2) ** 0.5 if s and e else None,
        })
    nets = []
    for n in _children(root, "net"):
        if len(n) >= 2:
            nets.append({"code": n[1], "name": n[2] if len(n) > 2 else ""})
    layers = [str(x[1]) for x in _children(root, "layer") if len(x) > 1]
    setup = _first(root, "setup")
    stackup = _first(setup, "stackup") if setup else None
    stack_layers = []
    if stackup:
        for lay in _children(stackup, "layer"):
            if len(lay) > 1:
                stack_layers.append(str(lay[1]))
    widths = [t["width_mm"] for t in tracks if t["width_mm"]]
    lengths = [t["length_mm"] for t in tracks if t["length_mm"]]
    return {
        "file": str(p),
        "type": "pcb",
        "footprints": len(fps),
        "nets": len(nets),
        "track_segments": len(tracks),
        "vias": len(_children(root, "via")),
        "zones": len(_children(root, "zone")),
        "copper_layers": layers,
        "stackup_layers": stack_layers,
        "board_bbox": _edge_bbox(root),
        "min_track_width_mm": min(widths) if widths else None,
        "max_track_width_mm": max(widths) if widths else None,
        "total_track_length_mm": round(sum(lengths), 2) if lengths else None,
        "net_names": [n["name"] for n in nets if n["name"]][:200],
        "bom": fps,
        "unreferenced_footprints": [f for f in fps if not f["reference"]],
        "notes": ["Parsed directly from KiCad S-expressions. Run kicad_drc for ERC/DRC verification."],
    }


@register("kicad_inspect", "Inspect a KiCad schematic or PCB: components, nets, footprints, board size, tracks.",
          {"path": "path to .kicad_sch or .kicad_pcb"}, ["path"], category="ece")
def kicad_inspect(path: str) -> str:
    p, root = _load(path)
    if not root:
        raise ValueError("Could not parse file")
    head = root[0] if isinstance(root[0], str) else ""
    if p.suffix.lower() == ".kicad_sch" or head == "kicad_sch":
        info = _inspect_sch(p, root)
    else:
        info = _inspect_pcb(p, root)
    return json.dumps(info, indent=2, ensure_ascii=False)[:24000]


def _kicad_cli() -> str | None:
    cfg = load()
    exe = cfg.get("paths", {}).get("kicad_cli")
    if exe:
        p = resolve(exe)
        if p.exists():
            return str(p)
    import shutil
    return shutil.which("kicad-cli")


@register("kicad_drc", "Run KiCad ERC/DRC and return the report after confirmation (read-only analysis).",
          {"path": "schematic or PCB path", "kind": "pcb or sch"}, ["path"],
          category="ece", dangerous=True, read_only=False)
def kicad_drc(path: str, kind: str = "pcb") -> str:
    p = safety.validate_read(path)
    exe = _kicad_cli()
    if not exe:
        return "kicad-cli not found. Set paths.kicad_cli in config/aether.json."
    if not safety.require_confirmation(
        "Run KiCad design-rule check?",
        f"{exe}\n{p}\n\nA temporary report is written under data/index and deleted afterwards.",
        action="kicad_drc",
    ):
        raise PermissionError("DRC denied by user")
    cfgdir = DATA_DIR / "kicad_config"
    cfgdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["KICAD_CONFIG_HOME"] = str(cfgdir)
    out = DATA_DIR / "index" / f"drc_{p.stem}.txt"
    if kind.lower() == "sch" or p.suffix.lower() == ".kicad_sch":
        cmd = [exe, "sch", "erc", "--output", str(out), str(p)]
    else:
        cmd = [exe, "pcb", "drc", "--output", str(out), str(p)]
    proc = safety.run_fixed(cmd, cwd=p.parent, timeout=300, env=env)
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    if out.exists():
        out.unlink()
    return f"exit={proc.returncode}\n{(proc.stdout or '') + (proc.stderr or '')}\n{text[-20000:]}"
