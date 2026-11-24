from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from aether import safety
from aether.config import ROOT


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.orig_confirm = safety._confirm_fn

    def tearDown(self):
        safety.set_confirm_callback(self.orig_confirm)

    def test_allowed_roots(self):
        roots = safety.allowed_roots()
        self.assertIn(ROOT.resolve(), [r.resolve() for r in roots])

    def test_within(self):
        p = ROOT / "config" / "aether.json"
        self.assertTrue(safety.within(p, [ROOT]))
        outside = Path("C:/Windows/System32/cmd.exe")
        self.assertFalse(safety.within(outside, [ROOT]))

    def test_validate_read(self):
        p = safety.validate_read(ROOT / "config" / "aether.json")
        self.assertTrue(p.exists())

        with self.assertRaises(FileNotFoundError):
            safety.validate_read(ROOT / "non_existent_file_xyz.txt")

    def test_validate_write_containment(self):
        # Valid write inside ROOT
        p = safety.validate_write("data/drafts/test.txt")
        self.assertTrue(str(p).startswith(str(ROOT.resolve())))

        # Invalid write outside ROOT
        with self.assertRaises(PermissionError):
            safety.validate_write("C:/Windows/System32/malicious.dll")

    def test_confirmation_callback(self):
        safety.set_confirm_callback(lambda title, detail: False)
        self.assertFalse(safety.require_confirmation("Test", "Detail"))

        safety.set_confirm_callback(lambda title, detail: True)
        self.assertTrue(safety.require_confirmation("Test", "Detail"))

    def test_app_allowlist(self):
        notepad = safety.app_path("notepad")
        self.assertTrue(notepad.lower().endswith("notepad.exe"))

        with self.assertRaises(PermissionError):
            safety.app_path("unapproved_hack_tool")

    def test_run_fixed_safety(self):
        # Must be list of strings
        with self.assertRaises(ValueError):
            safety.run_fixed("echo dangerous string")

        # Null bytes forbidden
        with self.assertRaises(ValueError):
            safety.run_fixed(["echo", "bad\x00arg"])


if __name__ == "__main__":
    unittest.main()
