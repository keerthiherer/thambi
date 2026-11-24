from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from aether import safety
from aether.config import ROOT
from aether.tools.office import create_docx, create_xlsx, create_pptx, create_pdf, read_document


class TestOffice(unittest.TestCase):
    def setUp(self):
        self.dir_path = ROOT / "data" / "drafts" / "test_office_output"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self.orig_confirm = safety._confirm_fn
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self):
        safety.set_confirm_callback(self.orig_confirm)
        shutil.rmtree(self.dir_path, ignore_errors=True)

    def test_docx(self):
        p = self.dir_path / "test.docx"
        msg = create_docx(str(p), "Test Document", "## Heading 2\nParagraph content.\n- Bullet point")
        self.assertTrue(p.exists())
        self.assertIn("Created", msg)

        doc_text = read_document(str(p))
        self.assertIn("Test Document", doc_text)
        self.assertIn("Heading 2", doc_text)
        self.assertIn("Paragraph content", doc_text)

    def test_xlsx(self):
        p = self.dir_path / "test.xlsx"
        rows = [["Item", "Qty", "Cost"], ["Resistor 10k", 100, 0.01], ["Capacitor 100n", 50, 0.02]]
        msg = create_xlsx(str(p), rows, sheet="BOM")
        self.assertTrue(p.exists())
        self.assertIn("Created", msg)

        text = read_document(str(p))
        self.assertIn("Resistor 10k", text)
        self.assertIn("Capacitor 100n", text)

    def test_pptx(self):
        p = self.dir_path / "test.pptx"
        slides = [
            {"title": "Slide 1: Overview", "bullets": ["AETHER architecture", "Hardware constraints"]},
            {"title": "Slide 2: Results", "bullets": ["Offline voice verified", "ECE tools complete"]}
        ]
        msg = create_pptx(str(p), "Melouz Tech Update", slides)
        self.assertTrue(p.exists())
        self.assertIn("Created", msg)

        text = read_document(str(p))
        self.assertIn("Melouz Tech Update", text)
        self.assertIn("AETHER architecture", text)

    def test_pdf(self):
        p = self.dir_path / "test.pdf"
        msg = create_pdf(str(p), "Melouz Engineering Memo", "This is an internal memorandum regarding RF layout guidelines.")
        self.assertTrue(p.exists())
        self.assertIn("Created", msg)

        text = read_document(str(p))
        self.assertIn("Melouz Engineering Memo", text)
        self.assertIn("RF layout guidelines", text)


if __name__ == "__main__":
    unittest.main()
