from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from aether import safety
from aether.config import ROOT
from aether.tools.ceo import create_quotation, create_invoice, draft_email, create_event, add_task, update_task, list_tasks
from aether.tools.office import read_document


class TestCEO(unittest.TestCase):
    def setUp(self):
        self.dir_path = ROOT / "data" / "drafts" / "test_ceo_output"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self.orig_confirm = safety._confirm_fn
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self):
        safety.set_confirm_callback(self.orig_confirm)
        shutil.rmtree(self.dir_path, ignore_errors=True)

    def test_quotation_and_invoice(self):
        p_quote = self.dir_path / "quote.docx"
        items = [
            {"description": "PCB Design & Layout", "qty": 1, "unit_price": 500.00},
            {"description": "Prototype Assembly", "qty": 5, "unit_price": 50.00}
        ]
        # Subtotal: 500 + 250 = 750. Tax 10% = 75. Total = 825.
        msg_q = create_quotation(str(p_quote), "Acme Corp", items, tax_percent=10, number="Q-1001", notes="Net 30 days")
        self.assertTrue(p_quote.exists())
        self.assertIn("Subtotal=750.00", msg_q)
        self.assertIn("tax=75.00", msg_q)
        self.assertIn("total=825.00", msg_q)

        p_inv = self.dir_path / "invoice.docx"
        msg_i = create_invoice(str(p_inv), "Acme Corp", items, tax_percent=10, number="INV-1001", due_date="2026-11-30")
        self.assertTrue(p_inv.exists())
        self.assertIn("total=825.00", msg_i)

        text = read_document(str(p_quote))
        self.assertIn("Melouz Technologies", text)
        self.assertIn("Acme Corp", text)
        self.assertIn("825.00", text)

    def test_draft_email(self):
        res = draft_email("partner@example.com", "Project Status Update", "All milestones for Phase 1 are completed.")
        self.assertIn("Draft #", res)
        self.assertIn("saved to", res)
        self.assertIn("NOT been sent", res)

    def test_create_event(self):
        res = create_event("Executive Sync", "2026-11-05T09:00:00", "2026-11-05T10:00:00",
                           "Review Q4 roadmap", "Conference Room A")
        self.assertIn("Created local event", res)

    def test_tasks_lifecycle(self):
        msg = add_task("Order Stencil", priority="high", due="2026-11-01", notes="From JLCPCB")
        self.assertIn("Task #", msg)
        tid = int(msg.split("#")[1].split()[0])

        tasks_open = list_tasks("open")
        self.assertIn("Order Stencil", tasks_open)

        up_msg = update_task(tid, status="done")
        self.assertIn("updated", up_msg)


if __name__ == "__main__":
    unittest.main()
