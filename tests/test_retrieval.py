from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from aether.retrieval import chunk_text, _decode, parse_eml
from aether.memory import Memory


class TestRetrieval(unittest.TestCase):
    def test_chunk_text(self):
        sample = """# Section 1
This is the first paragraph describing the power supply.

## Section 1.1
This paragraph describes the buck converter component selection.

# Section 2
RF section explaining 2.4 GHz antenna matching.
"""
        chunks = chunk_text(sample, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(any("Section 1" in c.get("heading", "") or "buck" in c["text"] for c in chunks))

    def test_decode(self):
        data_utf8 = "AETHER electronics helper".encode("utf-8")
        self.assertEqual(_decode(data_utf8), "AETHER electronics helper")

        data_latin1 = "Capacitance 10µF".encode("latin-1")
        self.assertIn("10", _decode(data_latin1))

    def test_parse_eml(self):
        eml_content = (
            "From: client@example.com\r\n"
            "To: info@melouz.com\r\n"
            "Subject: RF matching circuit inquiry\r\n"
            "Date: Fri, 30 Oct 2026 10:00:00 +0000\r\n"
            "Message-ID: <msg123@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Hello, we need a 50 ohm trace impedance calculation.\r\n"
        ).encode("utf-8")

        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as f:
            f.write(eml_content)
            temp_path = Path(f.name)

        try:
            d = parse_eml(temp_path)
            self.assertEqual(d["subject"], "RF matching circuit inquiry")
            self.assertEqual(d["from"], "client@example.com")
            self.assertIn("50 ohm", d["body"])
        finally:
            temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
