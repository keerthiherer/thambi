from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path

from .. import safety
from ..config import ROOT, load, resolve
from .registry import register

CALCS = {}


def calc(name: str, description: str, formula: str, source: str):
    def deco(fn):
        CALCS[name] = {"fn": fn, "description": description, "formula": formula, "source": source}
        return fn
    return deco


def _f(v, name: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a number (SI units), got {v!r}") from e
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite")
    return x


@calc("ohms_law", "Solve V=IR given exactly two of voltage(V), current(A), resistance(ohm).",
      "V = I * R", "Ohm's law")
def _ohms(voltage=None, current=None, resistance=None, v=None, i=None, r=None):
    voltage = voltage if voltage is not None else v
    current = current if current is not None else i
    resistance = resistance if resistance is not None else r
    given = [x is not None for x in (voltage, current, resistance)]
    if sum(given) != 2:
        raise ValueError("Provide exactly two of voltage, current, resistance")
    if voltage is None:
        v_out = _f(current, "current") * _f(resistance, "resistance")
    elif current is None:
        v_out = _f(voltage, "voltage")
        current = v_out / _f(resistance, "resistance")
    else:
        v_out = _f(voltage, "voltage")
        resistance = v_out / _f(current, "current")
    return {"voltage_V": v_out, "current_A": float(current), "resistance_ohm": float(resistance)}


@calc("voltage_divider", "Unloaded resistive divider output.", "Vout = Vin * R2/(R1+R2)", "Kirchhoff/Ohm")
def _divider(vin=None, r1=None, r2=None, v_in=None, v=None):
    vin = vin if vin is not None else (v_in if v_in is not None else v)
    if vin is None or r1 is None or r2 is None:
        raise ValueError("vin, r1, r2 required")
    vin, r1, r2 = _f(vin, "vin"), _f(r1, "r1"), _f(r2, "r2")
    if r1 + r2 == 0:
        raise ValueError("R1+R2 must be > 0")
    return {"vout_V": vin * r2 / (r1 + r2), "current_A": vin / (r1 + r2), "ratio": r2 / (r1 + r2)}


@calc("rc_filter", "First-order RC low-pass cutoff and time constant.", "fc = 1/(2*pi*R*C), tau=R*C", "RC theory")
def _rc(r=None, c=None, resistor=None, capacitor=None):
    r = r if r is not None else resistor
    c = c if c is not None else capacitor
    if r is None or c is None:
        raise ValueError("r and c required")
    r, c = _f(r, "r"), _f(c, "c")
    if r <= 0 or c <= 0:
        raise ValueError("R and C must be > 0")
    return {"cutoff_Hz": 1 / (2 * math.pi * r * c), "tau_s": r * c}


@calc("lc_resonance", "Ideal LC resonant frequency.", "f = 1/(2*pi*sqrt(L*C))", "LC theory")
def _lc(l=None, c=None, inductor=None, capacitor=None):
    l = l if l is not None else inductor
    c = c if c is not None else capacitor
    if l is None or c is None:
        raise ValueError("l and c required")
    l, c = _f(l, "l"), _f(c, "c")
    if l <= 0 or c <= 0:
        raise ValueError("L and C must be > 0")
    return {"resonant_Hz": 1 / (2 * math.pi * math.sqrt(l * c)), "period_s": 2 * math.pi * math.sqrt(l * c)}


@calc("led_resistor", "Series resistor for an LED.", "R = (Vs - Vf)/I; P = I^2*R", "Ohm's law")
def _led(supply_v=None, forward_v=None, current_ma=None, vs=None, vf=None, i_ma=None, current=None):
    supply_v = supply_v if supply_v is not None else vs
    forward_v = forward_v if forward_v is not None else vf
    if current_ma is None:
        if i_ma is not None:
            current_ma = i_ma
        elif current is not None:
            current_ma = current * 1000 if current < 1 else current
    if supply_v is None or forward_v is None or current_ma is None:
        raise ValueError("supply_v, forward_v, current_ma required")
    vs_val, vf_val, i = _f(supply_v, "supply_v"), _f(forward_v, "forward_v"), _f(current_ma, "current_ma") / 1000
    if i <= 0:
        raise ValueError("current_ma must be > 0")
    if vs_val <= vf_val:
        raise ValueError("supply_v must exceed forward_v")
    r = (vs_val - vf_val) / i
    return {"resistance_ohm": r, "power_W": i * i * r, "current_A": i}


@calc("trace_width", "IPC-2221 external/internal copper trace width for a temperature rise.",
      "A=(I/(k*dT^0.44))^(1/0.725), width=A/(oz*1.378)", "IPC-2221")
def _trace(current_a=None, temp_rise_c=10, copper_oz=1, internal=False, i=None, current=None, temp_rise=None, dt=None, oz=None):
    current_a = current_a if current_a is not None else (i if i is not None else current)
    if temp_rise_c is None:
        temp_rise_c = temp_rise if temp_rise is not None else dt
    copper_oz = copper_oz if oz is None else oz
    if current_a is None:
        raise ValueError("current_a required")
    i_val, dt_val, oz_val = _f(current_a, "current_a"), _f(temp_rise_c, "temp_rise_c"), _f(copper_oz, "copper_oz")
    if i_val <= 0 or dt_val <= 0 or oz_val <= 0:
        raise ValueError("current, temperature rise and copper weight must be > 0")
    k = 0.024 if (internal is True or str(internal).lower() in ("1", "true", "yes")) else 0.048
    area_mil2 = (i_val / (k * dt_val ** 0.44)) ** (1 / 0.725)
    width_mil = area_mil2 / (oz_val * 1.378)
    return {
        "width_mm": width_mil * 0.0254,
        "width_mil": width_mil,
        "area_mil2": area_mil2,
        "assumption": "IPC-2221 chart fit; verify against your fab/assembly requirements.",
    }


@calc("microstrip_z", "Approximate microstrip characteristic impedance (Hammerstad-Jensen).",
      "Z0 from W/h and effective permittivity", "Hammerstad & Jensen (1980)")
def _microstrip(w_mm=None, h_mm=None, er=None, t_mm=0.035, w=None, h=None, t=None):
    w_mm = w_mm if w_mm is not None else w
    h_mm = h_mm if h_mm is not None else h
    t_mm = t_mm if t is None else t
    if w_mm is None or h_mm is None or er is None:
        raise ValueError("w_mm, h_mm, er required")
    w_val, h_val, er_val = _f(w_mm, "w_mm"), _f(h_mm, "h_mm"), _f(er, "er")
    if w_val <= 0 or h_val <= 0 or er_val < 1:
        raise ValueError("w,h > 0 and er >= 1 required")
    u = w_val / h_val
    if u < 1:
        ee = (er_val + 1) / 2 + (er_val - 1) / 2 * (1 / math.sqrt(1 + 12 / u) + 0.04 * (1 - u) ** 2)
        z0 = 60 / math.sqrt(ee) * math.log(8 / u + u / 4)
    else:
        ee = (er_val + 1) / 2 + (er_val - 1) / 2 * (1 / math.sqrt(1 + 12 / u))
        z0 = 120 * math.pi / (math.sqrt(ee) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    return {"z0_ohm": z0, "effective_er": ee, "w_over_h": u,
            "assumption": "Zero-thickness approximation; final impedance should be verified with the fab stackup/field solver."}


@calc("antenna_length", "Free-space resonant wire antenna length with velocity factor.",
      "lambda=300/f(MHz); dipole=lambda/2, monopole=lambda/4, loop=lambda", "Maxwell/lumped resonance")
def _antenna(freq_mhz=None, antenna_type="dipole", velocity_factor=1.0, f=None, freq=None, type=None, vf=None):
    freq_mhz = freq_mhz if freq_mhz is not None else (freq if freq is not None else f)
    if type is not None:
        antenna_type = type
    if vf is not None:
        velocity_factor = vf
    if freq_mhz is None:
        raise ValueError("freq_mhz required")
    f_val, vf_val = _f(freq_mhz, "freq_mhz"), _f(velocity_factor, "velocity_factor")
    if f_val <= 0 or not (0 < vf_val <= 1):
        raise ValueError("freq_mhz > 0 and 0 < velocity_factor <= 1")
    lam = 300.0 / f_val
    kind = str(antenna_type).lower().strip()
    factors = {"dipole": 0.5, "halfwave": 0.5, "monopole": 0.25, "quarterwave": 0.25, "loop": 1.0, "fullwave": 1.0}
    if kind not in factors:
        raise ValueError(f"antenna_type must be one of {sorted(factors)}")
    length = lam * factors[kind] * vf_val
    return {"wavelength_m": lam, "length_m": length, "type": kind, "velocity_factor": vf_val,
            "note": "Physical length also depends on wire diameter, height and environment; tune with a VNA."}


@calc("friis", "Received power for free-space path loss.", "Pr = Pt + Gt + Gr + 20log10(lambda/(4*pi*d))", "Friis transmission equation")
def _friis(tx_dbm=None, tx_gain_dbi=None, rx_gain_dbi=None, freq_mhz=None, distance_m=None, pt=None, gt=None, gr=None, f=None, d=None):
    tx_dbm = tx_dbm if tx_dbm is not None else pt
    tx_gain_dbi = tx_gain_dbi if tx_gain_dbi is not None else gt
    rx_gain_dbi = rx_gain_dbi if rx_gain_dbi is not None else gr
    freq_mhz = freq_mhz if freq_mhz is not None else f
    distance_m = distance_m if distance_m is not None else d
    if any(x is None for x in (tx_dbm, tx_gain_dbi, rx_gain_dbi, freq_mhz, distance_m)):
        raise ValueError("tx_dbm, tx_gain_dbi, rx_gain_dbi, freq_mhz, distance_m required")
    pt_val, gt_val, gr_val, f_val, d_val = (_f(tx_dbm, "tx_dbm"), _f(tx_gain_dbi, "tx_gain_dbi"),
                                            _f(rx_gain_dbi, "rx_gain_dbi"), _f(freq_mhz, "freq_mhz"), _f(distance_m, "distance_m"))
    if f_val <= 0 or d_val <= 0:
        raise ValueError("frequency and distance must be > 0")
    lam = 300.0 / f_val
    path_loss_db = 20 * math.log10(4 * math.pi * d_val / lam)
    return {"rx_dbm": pt_val + gt_val + gr_val - path_loss_db, "path_loss_db": path_loss_db,
            "wavelength_m": lam, "assumption": "Far-field, free space, isotropic multipath ignored."}


@calc("dbm_mw", "Convert between dBm and milliwatts.", "P_mW = 10^(dBm/10)", "logarithmic power definition")
def _dbm(dbm=None, mw=None):
    if (dbm is None) == (mw is None):
        raise ValueError("Provide exactly one of dbm or mw")
    if dbm is not None:
        d_val = _f(dbm, "dbm")
        return {"dbm": d_val, "mw": 10 ** (d_val / 10)}
    m = _f(mw, "mw")
    if m <= 0:
        raise ValueError("mw must be > 0")
    return {"dbm": 10 * math.log10(m), "mw": m}


@calc("battery_life", "Runtime from capacity and average load with derating.",
      "hours = capacity_mAh * efficiency * derating / current_mA", "charge conservation")
def _battery(capacity_mah=None, avg_current_ma=None, efficiency=0.85, derating=0.8, cap=None, current=None, i=None):
    capacity_mah = capacity_mah if capacity_mah is not None else cap
    avg_current_ma = avg_current_ma if avg_current_ma is not None else (current if current is not None else i)
    if capacity_mah is None or avg_current_ma is None:
        raise ValueError("capacity_mah and avg_current_ma required")
    cap_val, cur_val, eff_val, der_val = (_f(capacity_mah, "capacity_mah"), _f(avg_current_ma, "avg_current_ma"),
                                          _f(efficiency, "efficiency"), _f(derating, "derating"))
    if cap_val <= 0 or cur_val <= 0:
        raise ValueError("capacity and current must be > 0")
    if not (0 < eff_val <= 1 and 0 < der_val <= 1):
        raise ValueError("efficiency and derating must be in (0,1]")
    h = cap_val * eff_val * der_val / cur_val
    return {"hours": h, "days": h / 24, "assumptions": {"efficiency": eff_val, "derating": der_val}}


@calc("buck_duty", "Ideal buck converter duty cycle.", "D = Vout/Vin", "inductor volt-second balance")
def _buck(vin=None, vout=None, vi=None, vo=None, v_in=None, v_out=None):
    vin = vin if vin is not None else (vi if vi is not None else v_in)
    vout = vout if vout is not None else (vo if vo is not None else v_out)
    if vin is None or vout is None:
        raise ValueError("vin and vout required")
    vi_val, vo_val = _f(vin, "vin"), _f(vout, "vout")
    if vi_val <= 0 or vo_val < 0 or vo_val >= vi_val:
        raise ValueError("Require Vin > Vout >= 0")
    return {"duty": vo_val / vi_val, "duty_percent": 100 * vo_val / vi_val}


@calc("boost_duty", "Ideal boost converter duty cycle.", "D = 1 - Vin/Vout", "inductor volt-second balance")
def _boost(vin=None, vout=None, vi=None, vo=None, v_in=None, v_out=None):
    vin = vin if vin is not None else (vi if vi is not None else v_in)
    vout = vout if vout is not None else (vo if vo is not None else v_out)
    if vin is None or vout is None:
        raise ValueError("vin and vout required")
    vi_val, vo_val = _f(vin, "vin"), _f(vout, "vout")
    if vi_val <= 0 or vo_val <= vi_val:
        raise ValueError("Require Vout > Vin > 0")
    return {"duty": 1 - vi_val / vo_val, "duty_percent": 100 * (1 - vi_val / vo_val)}


@calc("thermal", "Junction temperature from power and thermal resistance.",
      "Tj = Ta + P*theta_JA", "thermal Ohm's law")
def _thermal(power_w=None, theta_ja_c_w=None, ambient_c=25, power=None, p=None, theta_ja=None, theta=None, ambient=None, ta=None):
    power_w = power_w if power_w is not None else (power if power is not None else p)
    theta_ja_c_w = theta_ja_c_w if theta_ja_c_w is not None else (theta_ja if theta_ja is not None else theta)
    ambient_c = ambient_c if ambient is None and ta is None else (ambient or ta)
    if power_w is None or theta_ja_c_w is None:
        raise ValueError("power_w and theta_ja_c_w required")
    p_val, th_val, ta_val = _f(power_w, "power_w"), _f(theta_ja_c_w, "theta_ja_c_w"), _f(ambient_c, "ambient_c")
    if th_val < 0:
        raise ValueError("theta_ja_c_w must be >= 0")
    return {"junction_c": ta_val + p_val * th_val, "rise_c": p_val * th_val}


@register("ece_calculate", "Run a validated electronics calculation. Never invents component specs.",
          {"calculation": f"one of: {', '.join(sorted(CALCS))}", "parameters": "JSON object of SI-unit inputs"},
          ["calculation", "parameters"], category="ece")
def ece_calculate(calculation: str, parameters) -> str:
    key = str(calculation).strip().lower()
    if key not in CALCS:
        return f"Unknown calculation '{calculation}'. Available: {', '.join(sorted(CALCS))}"
    if isinstance(parameters, str):
        try:
            raw_params = json.loads(parameters)
        except json.JSONDecodeError as e:
            raise ValueError("parameters must be a JSON object") from e
    else:
        raw_params = dict(parameters or {})
    params = {str(k).lower().strip(): v for k, v in raw_params.items()}
    meta = CALCS[key]
    result = meta["fn"](**params)
    return json.dumps({
        "calculation": key,
        "inputs": params,
        "result": result,
        "formula": meta["formula"],
        "source": meta["source"],
    }, indent=2)


@register("bom_analyze", "Analyse a BOM CSV: quantities, missing fields, duplicate references, estimated cost.",
          {"path": "BOM CSV path", "currency": "currency label"}, ["path"], category="ece")
def bom_analyze(path: str, currency: str = "USD") -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() not in (".csv", ".tsv"):
        raise ValueError("BOM must be CSV or TSV")
    with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    if not rows:
        return "BOM is empty."
    lower = {k.lower().strip(): k for k in rows[0].keys() if k}
    def col(*names):
        for n in names:
            if n in lower:
                return lower[n]
        return None
    ref_c = col("reference", "references", "ref", "refs", "designator", "designators")
    qty_c = col("qty", "quantity", "count")
    val_c = col("value", "comment")
    fp_c = col("footprint", "package")
    mpn_c = col("mpn", "part number", "manufacturer part number")
    price_c = col("unit price", "price", "unit_cost", "cost")
    refs_seen: dict[str, int] = {}
    total_qty = 0
    total_cost = 0.0
    missing = []
    for i, row in enumerate(rows, 2):
        refs = str(row.get(ref_c, "") if ref_c else "").replace(" ", "")
        for r in [x for x in refs.replace(";", ",").split(",") if x]:
            refs_seen[r] = refs_seen.get(r, 0) + 1
        try:
            qty = int(float(row.get(qty_c, "1") if qty_c else 1))
        except ValueError:
            qty = 1
            missing.append(f"line {i}: invalid quantity")
        total_qty += qty
        if price_c:
            try:
                total_cost += qty * float(str(row.get(price_c, "0")).replace("$", "").replace(",", ""))
            except ValueError:
                pass
        for label, c in (("value", val_c), ("footprint", fp_c), ("MPN", mpn_c)):
            if not c or not str(row.get(c, "")).strip():
                missing.append(f"line {i}: missing {label}")
    dupes = {r: n for r, n in refs_seen.items() if n > 1}
    out = {
        "file": str(p),
        "lines": len(rows),
        "total_quantity": total_qty,
        "unique_designators": len(refs_seen),
        "estimated_cost": round(total_cost, 2),
        "currency": currency,
        "duplicate_designators": dupes,
        "missing_fields": missing[:50],
        "note": "Cost is only as reliable as the unit-price column; verify MPN/availability with suppliers.",
    }
    return json.dumps(out, indent=2)


@register("simulator_status", "Detect ngspice and openEMS availability.",
          {}, category="ece")
def simulator_status() -> str:
    cfg = load()
    found = {}
    for name, key, exe in (("ngspice", "ngspice", "ngspice"), ("openEMS", "openems", "openEMS")):
        p = cfg.get("paths", {}).get(key)
        path = resolve(p) if p else None
        found[name] = str(path) if path and Path(path).exists() else (shutil.which(exe) or None)
    return json.dumps({"simulators": found,
                       "note": "THAMBI does not install simulators automatically. Set paths.ngspice/paths.openems in config."}, indent=2)


@register("ngspice_run", "Run an ngspice netlist in batch mode after confirmation (if installed).",
          {"netlist": "path to .cir/.sp/.net", "timeout": "seconds"}, ["netlist"],
          category="ece", dangerous=True, read_only=False)
def ngspice_run(netlist: str, timeout: int | str = 120) -> str:
    cfg = load()
    exe = cfg.get("paths", {}).get("ngspice") or shutil.which("ngspice")
    if not exe:
        return "ngspice is not installed/configured. Install it or set paths.ngspice; THAMBI will not install software silently."
    p = safety.validate_read(netlist)
    if p.suffix.lower() not in (".cir", ".sp", ".net", ".spice"):
        raise ValueError("Netlist must be .cir/.sp/.net/.spice")
    if not safety.require_confirmation("Run ngspice simulation?", f"{exe}\n{p}", action="ngspice_run"):
        raise PermissionError("Simulation denied by user")
    out = p.with_suffix(".aether.log")
    proc = safety.run_fixed([exe, "-b", "-o", str(out), str(p)], cwd=p.parent, timeout=min(900, max(5, int(timeout))))
    log = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    if out.exists():
        out.unlink()
    return (proc.stdout or "") + (proc.stderr or "") + "\n" + log[-16000:]


@register("openems_note", "Report openEMS integration status and required invocation.",
          {}, category="ece")
def openems_note() -> str:
    return simulator_status()
