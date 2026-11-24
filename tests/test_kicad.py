from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from aether.tools.kicad import parse_sexpr, kicad_inspect


class TestKiCad(unittest.TestCase):
    def test_parse_sexpr(self):
        sexpr = '(kicad_sch (version 20231120) (generator "eeschema") (symbol (lib_id "Device:R") (property "Reference" "R1") (property "Value" "10k")))'
        tree = parse_sexpr(sexpr)
        self.assertEqual(tree[0], "kicad_sch")
        self.assertEqual(len(tree), 4)

    def test_kicad_inspect_sch(self):
        sch_text = """(kicad_sch (version 20231120) (generator "eeschema")
  (symbol (lib_id "Device:R") (property "Reference" "R1") (property "Value" "10k") (property "Footprint" "Resistor_SMD:R_0603_1608Metric"))
  (symbol (lib_id "Device:C") (property "Reference" "C1") (property "Value" "100nF") (property "Footprint" "Capacitor_SMD:C_0603_1608Metric"))
  (wire (pts (xy 100 100) (xy 120 100)))
)"""
        with tempfile.NamedTemporaryFile(suffix=".kicad_sch", delete=False, mode="w", encoding="utf-8") as f:
            f.write(sch_text)
            p = Path(f.name)
        try:
            res = json.loads(kicad_inspect(str(p)))
            self.assertEqual(res["type"], "schematic")
            self.assertEqual(res["components"], 2)
            self.assertEqual(res["wires"], 1)
            self.assertEqual(len(res["missing_footprints"]), 0)
        finally:
            p.unlink()

    def test_kicad_inspect_pcb(self):
        pcb_text = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 50 0) (end 50 40) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 50 40) (end 0 40) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 0 40) (end 0 0) (layer "Edge.Cuts") (width 0.1))
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
  (net 0 "")
  (net 1 "+3V3")
)"""
        with tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False, mode="w", encoding="utf-8") as f:
            f.write(pcb_text)
            p = Path(f.name)
        try:
            res = json.loads(kicad_inspect(str(p)))
            self.assertEqual(res["type"], "pcb")
            self.assertEqual(res["nets"], 2)
            self.assertEqual(res["track_segments"], 1)
            self.assertIsNotNone(res["board_bbox"])
            self.assertAlmostEqual(res["board_bbox"]["width_mm"], 50.0)
            self.assertAlmostEqual(res["board_bbox"]["height_mm"], 40.0)
        finally:
            p.unlink()


if __name__ == "__main__":
    unittest.main()
