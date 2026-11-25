from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import aether.agent as agent_mod
import aether.tools.papers as papers_mod
from aether import safety
from aether.config import ROOT
from aether.tools import REGISTRY, execute

BRIEF = """MULTI-SOURCE RESEARCH BRIEF
[R1] Asyncio Overview
     docs.python.org | https://docs.python.org/3/howto/asyncio.html
     Why: asyncio coordinates concurrent tasks using an event loop.
[R2] Asyncio Walkthrough
     realpython.com | https://realpython.com/async-io-python/
     Why: however, event loops do not make CPU-bound work parallel.
"""


class TestPaperToolsRegistered(unittest.TestCase):
    def test_paper_category_tools(self) -> None:
        expected = {
            "paper_lookup", "paper_outline", "literature_review", "paper_study_design",
            "latex_paper", "latex_check", "bibtex_builder", "paper_review", "paper_project_export",
        }
        self.assertTrue(expected <= set(REGISTRY))
        for name in expected:
            self.assertEqual(REGISTRY[name].category, "paper")
        self.assertTrue(REGISTRY["paper_project_export"].dangerous)
        self.assertFalse(REGISTRY["paper_outline"].dangerous)

    def test_paper_prompt_wiring(self) -> None:
        self.assertIn("paper", agent_mod.MODULES)
        self.assertIn("rd", agent_mod.MODULES)
        self.assertIn("paper", agent_mod.MODULE_CATEGORIES["research"])
        system = agent_mod.build_system("paper", compact=True)
        for name in ("paper_lookup", "latex_paper", "paper_review"):
            self.assertIn(name, system)


class TestPaperWorkflow(unittest.TestCase):
    def test_outline_is_imrad(self) -> None:
        out = papers_mod.paper_outline(
            "Event Loops in Practice", "How do event loops affect latency?",
            evidence=BRIEF, contributions="a measured latency model",
        )
        self.assertIn("PAPER OUTLINE", out)
        self.assertIn("SECTION PLAN", out)
        self.assertIn("Introduction", out)
        self.assertIn("Related Work", out)
        self.assertIn("Method", out)
        self.assertIn("[R1]", out)

    def test_literature_review_groups_sources(self) -> None:
        out = papers_mod.literature_review(BRIEF, "How do event loops work?")
        self.assertIn("LITERATURE REVIEW MATRIX", out)
        self.assertIn("[R1]", out)
        self.assertIn("[R2]", out)
        self.assertIn("Distinct domains: 2", out)
        self.assertIn("CONTRADICTION CUES", out)

    def test_study_design_has_validity_and_reproducibility(self) -> None:
        out = papers_mod.paper_study_design(
            "Does caching reduce latency?", hypothesis="Caching reduces p95 latency.",
            metric="p95 latency ms", sample_size=120, variants=2,
        )
        self.assertIn("STUDY DESIGN", out)
        self.assertIn("H1:", out)
        self.assertIn("H0:", out)
        self.assertIn("THREATS TO VALIDITY", out)
        self.assertIn("REPRODUCIBILITY", out)
        self.assertIn("N=120", out)

    def test_latex_source_is_compile_ready(self) -> None:
        out = papers_mod.latex_paper(
            "A Study & Its Results",
            authors="A. Author; B. Writer",
            abstract="We study event loops.",
            sections=[{"title": "Method", "content": "We used a 50% sample, 100 & 200 runs."}],
            style="ieee",
            keywords="systems, evaluation",
        )
        self.assertIn("\\documentclass[journal,twocolumn]{IEEEtran}", out)
        self.assertIn("\\begin{abstract}", out)
        self.assertIn("\\begin{IEEEkeywords}", out)
        self.assertIn("\\section{Method}", out)
        self.assertIn("A Study \\& Its Results", out)
        self.assertIn("50\\%", out)
        check = papers_mod.latex_check(out.split("```latex\n", 1)[1].split("```", 1)[0])
        self.assertIn("Status: PASS", check)

    def test_latex_check_detects_broken_source(self) -> None:
        broken = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\section{Broken\n\\begin{figure}\n\\end{table}\n$x = 1$\n\\end{document}\n"
        )
        check = papers_mod.latex_check(broken, strict=True)
        self.assertIn("Status: FAIL", check)
        self.assertIn("Unclosed braces", check)
        self.assertIn("does not match", check)

    def test_bibtex_builder_reports_missing_fields(self) -> None:
        entries = [
            {
                "key": "smith2024", "type": "inproceedings", "title": "Event Loops",
                "author": "Smith, A. and Lee, B.", "year": "2024", "venue": "Conf",
                "doi": "10.1000/example",
            },
            "badentry | misc | Notes",
        ]
        out = papers_mod.bibtex_builder(entries)
        self.assertIn("@inproceedings{smith2024,", out)
        self.assertIn("booktitle = {Conf}", out)
        self.assertIn("doi = {10.1000/example}", out)
        self.assertIn("missing author", out)

    def test_paper_review_flags_unsupported_claims(self) -> None:
        draft = (
            "Abstract\nThis paper shows a significant improvement without measurement.\n\n"
            "Introduction\nPrior work proves the approach always outperforms every baseline.\n\n"
            "Method\nTODO: describe the setup.\n\n"
            "Results\nThe result is better.\n\n"
            "Conclusion\nThis proves generality.\n"
        )
        out = papers_mod.paper_review(draft)
        self.assertIn("PAPER REVIEW", out)
        self.assertIn("without a source handle", out)
        self.assertIn("Placeholder", out)
        self.assertIn("REVIEW LIMITS", out)


class TestPaperLookup(unittest.TestCase):
    def test_lookup_uses_verbatim_metadata(self) -> None:
        payload = {
            "message": {
                "items": [{
                    "DOI": "10.1000/example",
                    "title": ["Real <b>Paper</b>"],
                    "author": [{"given": "A.", "family": "Smith"}],
                    "published": {"date-parts": [[2024]]},
                    "container-title": ["Journal of Tests"],
                    "URL": "https://doi.org/10.1000/example",
                    "is-referenced-by-count": 12,
                }],
            },
        }
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        with patch.object(papers_mod.safety, "require_external", return_value=True), \
             patch.object(papers_mod.requests, "get", return_value=response) as get:
            out = papers_mod.paper_lookup("event loops", limit=4)
        self.assertIn("PAPER LOOKUP", out)
        self.assertIn("Real Paper", out)
        self.assertIn("10.1000/example", out)
        self.assertIn("VERBATIM METADATA ONLY", out)
        self.assertEqual(get.call_args.kwargs["params"]["rows"], "4")

    def test_lookup_rejects_bad_sort(self) -> None:
        with self.assertRaises(ValueError):
            papers_mod.paper_lookup("topic", sort="random")


class TestPaperProjectExport(unittest.TestCase):
    def setUp(self) -> None:
        self.dir_path = ROOT / "data" / "drafts" / f"test_papers_{uuid.uuid4().hex[:8]}"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self.orig_confirm = safety._confirm_fn
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(self.orig_confirm)
        shutil.rmtree(self.dir_path, ignore_errors=True)

    def test_export_writes_overleaf_project(self) -> None:
        target = self.dir_path / "paper_project"
        out = papers_mod.paper_project_export(
            str(target), "Test Paper", "What works?",
            sections=[{"title": "Introduction", "content": "State the gap."}],
            evidence=BRIEF,
        )
        self.assertIn("Created Overleaf paper project", out)
        self.assertTrue((target / "main.tex").exists())
        self.assertTrue((target / "references.bib").exists())
        self.assertTrue((target / "outline.md").exists())
        self.assertTrue((target / "study_design.md").exists())
        self.assertTrue((target / "literature_review.md").exists())
        self.assertTrue((target / "latex_check.txt").exists())
        self.assertIn("\\documentclass", (target / "main.tex").read_text(encoding="utf-8"))

    def test_export_blocks_overwrite_without_flag(self) -> None:
        target = self.dir_path / "paper_project"
        papers_mod.paper_project_export(str(target), "Test Paper", "What works?")
        with self.assertRaises(FileExistsError):
            papers_mod.paper_project_export(str(target), "Test Paper", "What works?")

    def test_registry_dangerous_gate(self) -> None:
        res = execute("paper_project_export", {"path": str(self.dir_path / "blocked"),
                                               "title": "T", "question": "Q"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "ConfirmationRequired")


class TestPaperCommands(unittest.TestCase):
    def test_paper_and_rd_usage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            from aether.memory import Memory

            mem = Memory(Path(td) / "papers.db")
            try:
                engine = agent_mod.ChatEngine(server=None, mem=mem)
                for command in ("/paper", "/rd"):
                    res = engine.reply(command)
                    self.assertTrue(res["command"])
                    self.assertIn(f"Usage: {command}", res["text"])
            finally:
                mem.close()


if __name__ == "__main__":
    unittest.main()
