from __future__ import annotations

import unittest
from aether.hardware import probe, _ram, _storage, _tools


class TestHardware(unittest.TestCase):
    def test_ram(self):
        ram = _ram()
        self.assertIn("total_gb", ram)
        self.assertIn("available_gb", ram)
        self.assertIn("load_percent", ram)
        self.assertGreater(ram["total_gb"], 0)

    def test_storage(self):
        disks = _storage()
        self.assertIsInstance(disks, list)
        self.assertGreater(len(disks), 0)
        self.assertIn("free_gb", disks[0])

    def test_tools(self):
        t = _tools()
        self.assertIn("python", t)
        self.assertIn("llama_server", t)
        self.assertTrue(t["llama_server"])

    def test_probe(self):
        info = probe()
        self.assertIn("os", info)
        self.assertIn("cpu", info)
        self.assertIn("ram", info)
        self.assertIn("models", info)
        self.assertTrue(info["models"]["primary_present"])
        self.assertIsNotNone(info["models"]["recommendation"])


if __name__ == "__main__":
    unittest.main()
