from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from aether.memory import Memory


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_aether.db"
        self.mem = Memory(self.db_path)

    def tearDown(self):
        self.mem.close()
        self.tmp_dir.cleanup()

    def test_messages(self):
        mid = self.mem.add_message("session_1", "user", "Hello Aether")
        self.assertGreater(mid, 0)
        self.mem.add_message("session_1", "assistant", "Hello! How can I help?")

        recents = self.mem.recent_messages("session_1", 10)
        self.assertEqual(len(recents), 2)
        self.assertEqual(recents[0]["role"], "user")
        self.assertEqual(recents[1]["role"], "assistant")

        sessions = self.mem.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "session_1")

    def test_memories_fts(self):
        m_id = self.mem.add_memory("KiCad Footprint", "Use 0603 SMD resistors for R1 and R2", kind="note", tags="pcb,hardware")
        self.assertGreater(m_id, 0)

        results = self.mem.search_memories("resistors")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], m_id)

        all_m = self.mem.list_memories()
        self.assertEqual(len(all_m), 1)

    def test_documents_and_chunks(self):
        chunks = [
            {"heading": "Introduction", "text": "AETHER is a local offline assistant for engineering and CEO work."},
            {"heading": "Hardware", "text": "It runs on ASUS TUF F15 with RTX 2050 GPU."}
        ]
        doc_id = self.mem.upsert_document("docs/readme.md", 12345.0, 1024, "dummy_hash", chunks)
        self.assertGreater(doc_id, 0)

        found = self.mem.search_chunks("ASUS TUF")
        self.assertGreater(len(found), 0)
        self.assertIn("RTX 2050", found[0]["text"])

        stats = self.mem.index_stats()
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["chunks"], 2)

    def test_tasks(self):
        t_id = self.mem.add_task("Review schematic design", priority="high", due="2026-11-01", notes="Check power rail decoupling")
        self.assertGreater(t_id, 0)

        tasks = self.mem.list_tasks(status="open")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Review schematic design")

        updated = self.mem.update_task(t_id, status="done")
        self.assertTrue(updated)
        self.assertEqual(len(self.mem.list_tasks(status="open")), 0)
        self.assertEqual(len(self.mem.list_tasks(status="done")), 1)

    def test_emails(self):
        e_id = self.mem.add_email("uid_123", "INBOX", "alice@example.com", "ceo@melouz.com",
                                  "Quotation Request", "2026-10-30", "Please send quote for 500 PCB units.")
        self.assertGreater(e_id, 0)

        results = self.mem.search_emails("PCB units")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["subject"], "Quotation Request")

    def test_calendar_and_drafts(self):
        ev_id = self.mem.add_event("cal_01", "Board Review Meeting", "2026-11-02T14:00:00",
                                   "2026-11-02T15:00:00", "Review rev B layout", "Office")
        self.assertGreater(ev_id, 0)
        events = self.mem.list_events()
        self.assertEqual(len(events), 1)

        d_id = self.mem.add_draft("email", "Re: Quotation Request", "alice@example.com", "Attached is the quote.")
        self.assertGreater(d_id, 0)


if __name__ == "__main__":
    unittest.main()
