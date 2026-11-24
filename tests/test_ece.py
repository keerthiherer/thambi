from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from aether.tools.ece import CALCS, ece_calculate, bom_analyze


class TestECE(unittest.TestCase):
    def test_ohms_law(self):
        res = json.loads(ece_calculate("ohms_law", {"voltage": 5.0, "resistance": 1000}))
        self.assertAlmostEqual(res["result"]["current_A"], 0.005)

        res2 = json.loads(ece_calculate("ohms_law", {"current": 0.02, "resistance": 220}))
        self.assertAlmostEqual(res2["result"]["voltage_V"], 4.4)

    def test_voltage_divider(self):
        res = json.loads(ece_calculate("voltage_divider", {"vin": 12.0, "r1": 10000, "r2": 2000}))
        self.assertAlmostEqual(res["result"]["vout_V"], 2.0)

    def test_rc_filter(self):
        res = json.loads(ece_calculate("rc_filter", {"r": 1000, "c": 1e-6}))
        # fc = 1 / (2 * pi * 1000 * 1e-6) ~ 159.15 Hz
        self.assertAlmostEqual(res["result"]["cutoff_Hz"], 159.155, places=2)

    def test_lc_resonance(self):
        res = json.loads(ece_calculate("lc_resonance", {"l": 10e-6, "c": 100e-12}))
        # f = 1 / (2 * pi * sqrt(10e-6 * 100e-12)) = 1 / (2 * pi * 3.162e-8) ~ 5.03 MHz
        self.assertAlmostEqual(res["result"]["resonant_Hz"] / 1e6, 5.033, places=2)

    def test_led_resistor(self):
        res = json.loads(ece_calculate("led_resistor", {"supply_v": 5.0, "forward_v": 2.0, "current_ma": 20}))
        # R = (5 - 2) / 0.02 = 150 ohm
        self.assertEqual(res["result"]["resistance_ohm"], 150)
        self.assertAlmostEqual(res["result"]["power_W"], 0.06)

    def test_trace_width(self):
        res = json.loads(ece_calculate("trace_width", {"current_a": 2.0, "temp_rise_c": 10, "copper_oz": 1}))
        self.assertGreater(res["result"]["width_mm"], 0.1)

    def test_microstrip_z(self):
        # FR4: er ~ 4.4, h = 1.6mm, w = 3.0mm
        res = json.loads(ece_calculate("microstrip_z", {"w_mm": 3.0, "h_mm": 1.6, "er": 4.4}))
        self.assertGreater(res["result"]["z0_ohm"], 40.0)
        self.assertLess(res["result"]["z0_ohm"], 60.0)

    def test_antenna_length(self):
        # 300 MHz -> lambda = 1.0 m; dipole = 0.5 m
        res = json.loads(ece_calculate("antenna_length", {"freq_mhz": 300, "antenna_type": "dipole"}))
        self.assertAlmostEqual(res["result"]["length_m"], 0.5)

    def test_friis(self):
        res = json.loads(ece_calculate("friis", {
            "tx_dbm": 14, "tx_gain_dbi": 2.15, "rx_gain_dbi": 2.15,
            "freq_mhz": 2400, "distance_m": 10
        }))
        self.assertIn("rx_dbm", res["result"])
        self.assertLess(res["result"]["rx_dbm"], 0)

    def test_dbm_mw(self):
        res1 = json.loads(ece_calculate("dbm_mw", {"dbm": 20}))
        self.assertAlmostEqual(res1["result"]["mw"], 100.0)

        res2 = json.loads(ece_calculate("dbm_mw", {"mw": 10}))
        self.assertAlmostEqual(res2["result"]["dbm"], 10.0)

    def test_battery_life(self):
        # 2000 mAh, 100 mA load, 0.85 eff, 0.8 derate -> 2000*0.85*0.8/100 = 13.6 h
        res = json.loads(ece_calculate("battery_life", {"capacity_mah": 2000, "avg_current_ma": 100}))
        self.assertAlmostEqual(res["result"]["hours"], 13.6)

    def test_buck_and_boost_duty(self):
        res_buck = json.loads(ece_calculate("buck_duty", {"vin": 12.0, "vout": 3.3}))
        self.assertAlmostEqual(res_buck["result"]["duty"], 3.3 / 12.0)

        res_boost = json.loads(ece_calculate("boost_duty", {"vin": 3.7, "vout": 5.0}))
        self.assertAlmostEqual(res_boost["result"]["duty"], 1 - 3.7 / 5.0)

    def test_bom_analyze(self):
        bom_csv = (
            "Reference,Value,Footprint,Qty,Unit Price\n"
            "R1,10k,Resistor_SMD:R_0603_1608Metric,1,0.01\n"
            "R2,10k,Resistor_SMD:R_0603_1608Metric,1,0.01\n"
            "C1,100nF,Capacitor_SMD:C_0603_1608Metric,1,0.02\n"
            "U1,STM32F401,Package_QFP:LQFP-64,1,3.50\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as f:
            f.write(bom_csv)
            path = Path(f.name)
        try:
            report = json.loads(bom_analyze(str(path)))
            self.assertEqual(report["total_quantity"], 4)
            self.assertEqual(report["unique_designators"], 4)
            self.assertAlmostEqual(report["estimated_cost"], 3.54)
            self.assertEqual(len(report["duplicate_designators"]), 0)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
