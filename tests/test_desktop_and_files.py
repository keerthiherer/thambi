from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aether import safety
from aether.config import ROOT
from aether.tools.files import list_dir, read_file, search_files, project_overview, write_file, create_folder, cleanup_cache
from aether.tools.desktop import list_windows, _APPROVED_PROCS


class TestDesktopAndFiles(unittest.TestCase):
    def setUp(self):
        self.test_dir = ROOT / "data" / "drafts" / "test_files_temp"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.orig_confirm = safety._confirm_fn
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self):
        safety.set_confirm_callback(self.orig_confirm)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_list_dir(self):
        res = list_dir(str(ROOT / "config"))
        self.assertIn("aether.json", res)

    def test_read_file(self):
        res = read_file("config/aether.json", start=1, limit=5)
        self.assertIn("1: {", res)

    def test_search_files(self):
        res = search_files(query="aether.json", root="config")
        self.assertIn("aether.json", res)

        res_content = search_files(query="llama", root="config", content=True)
        self.assertIn("aether.json", res_content)

    def test_project_overview(self):
        res = project_overview(".")
        self.assertIn("Project:", res)
        self.assertIn(".py", res)

    def test_cleanup_cache_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "aether" / "__pycache__").mkdir(parents=True)
            (root / "aether" / "module.pyc").write_text("cache", encoding="utf-8")
            (root / "data" / "drafts" / "lo_profile_test").mkdir(parents=True)
            (root / "data" / "drafts" / "pitch_deck.aether.bak").write_text("bak", encoding="utf-8")
            (root / "data" / "drafts" / "keep.pptx").write_text("keep", encoding="utf-8")
            (root / "data" / "aether.db").write_text("memory", encoding="utf-8")
            with patch("aether.tools.files.ROOT", root):
                preview = cleanup_cache(dry_run=True)
                self.assertIn("Would remove", preview)
                result = cleanup_cache(dry_run=False, confirmed=True)
                self.assertIn("CLEANED", result)
            self.assertFalse((root / "aether" / "__pycache__").exists())
            self.assertFalse((root / "data" / "drafts" / "lo_profile_test").exists())
            self.assertFalse((root / "data" / "drafts" / "pitch_deck.aether.bak").exists())
            self.assertTrue((root / "data" / "drafts" / "keep.pptx").exists())
            self.assertTrue((root / "data" / "aether.db").exists())

    def test_write_file_and_create_folder(self):
        folder = self.test_dir / "sub_test"
        msg_f = create_folder(str(folder))
        self.assertTrue(folder.is_dir())
        self.assertIn("Created", msg_f)

        f_path = folder / "hello.txt"
        msg_w = write_file(str(f_path), "Hello world from AETHER\nSecond line", overwrite=False)
        self.assertTrue(f_path.is_file())
        self.assertIn("Wrote", msg_w)

        # Overwrite test with backup
        msg_w2 = write_file(str(f_path), "Overwritten content", overwrite=True)
        self.assertIn("Wrote", msg_w2)
        self.assertEqual(f_path.read_text(encoding="utf-8"), "Overwritten content")
        self.assertTrue((folder / "hello.txt.aether.bak").is_file())

    def test_list_windows(self):
        windows = list_windows()
        # Should return a string with windows or "(no visible windows)"
        self.assertIsInstance(windows, str)
        self.assertIn("notepad.exe", _APPROVED_PROCS)


if __name__ == "__main__":
    unittest.main()
