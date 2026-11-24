from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

import aether.agent as agent_mod
import aether.tools.coding as coding_mod
import aether.tools.research as research_mod
from aether import safety
from aether.config import ROOT
from aether.tools import REGISTRY, execute

BRIEF = """MULTI-SOURCE RESEARCH BRIEF
SOURCE SET (diverse, deduplicated):
[R1] Asyncio Overview
     docs.python.org | https://docs.python.org/3/howto/asyncio.html
     Why: asyncio coordinates concurrent tasks using an event loop in modern Python applications.
[R2] Asyncio Walkthrough
     realpython.com | https://realpython.com/async-io-python/
     Why: asyncio uses an event loop to schedule concurrent tasks and awaitable operations.

EXTRACTED EVIDENCE:
[R1] https://docs.python.org/3/howto/asyncio.html
asyncio coordinates concurrent tasks using an event loop in modern Python applications.
[R2] https://realpython.com/async-io-python/
asyncio uses an event loop to schedule concurrent tasks and awaitable operations.
"""


class TestResearchSynthesis(unittest.TestCase):
    def test_research_matrix_groups_sources_and_claims(self) -> None:
        out = research_mod.research_matrix(BRIEF)
        self.assertIn("RESEARCH MATRIX", out)
        self.assertIn("[R1]", out)
        self.assertIn("[R2]", out)
        self.assertIn("Distinct domains: 2", out)
        self.assertIn("| Claim / evidence", out)

    def test_claim_verifier_reports_domain_support(self) -> None:
        out = research_mod.claim_verifier(
            "asyncio coordinates concurrent tasks using an event loop", BRIEF, min_sources=1
        )
        self.assertIn("CLAIM VERIFICATION", out)
        self.assertIn("SUPPORTED BY PROVIDED EVIDENCE", out)
        self.assertIn("docs.python.org", out)

    def test_research_report_export_markdown(self) -> None:
        folder = ROOT / "data" / "drafts" / f"test_report_{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True, exist_ok=True)
        safety.set_confirm_callback(lambda title, detail: True)
        try:
            target = folder / "report.md"
            result = research_mod.research_report_export(
                str(target), "Asyncio Research", "How does asyncio work?", BRIEF, "md"
            )
            self.assertIn("Created research report", result)
            body = target.read_text(encoding="utf-8")
            self.assertIn("# Asyncio Research", body)
            self.assertIn("## Research Matrix", body)
            self.assertIn("docs.python.org", body)
        finally:
            safety.set_confirm_callback(None)
            shutil.rmtree(folder, ignore_errors=True)


class TestCodeQualityTools(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_quality_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.source = self.folder / "sample_module.py"
        self.source.write_text(
            "import os\n\n\ndef alpha():\n    return 1\n\n\ndef beta(items=[]):\n    token = 'abcdef123456'\n    os.system('echo hi')\n    return items\n",
            encoding="utf-8",
        )
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_code_review_and_security_scan(self) -> None:
        review = coding_mod.code_review(str(self.source))
        self.assertIn("CODE REVIEW", review)
        self.assertIn("mutable_default", review)
        scan = coding_mod.security_scan(str(self.source))
        self.assertIn("SECURITY SCAN", scan)
        self.assertIn("hardcoded_secret", scan)
        self.assertIn("shell_injection", scan)

    def test_test_generator_writes_pytest_stubs(self) -> None:
        test_dir = self.folder / "generated_tests"
        out = coding_mod.test_generator(str(self.source), str(test_dir))
        self.assertIn("Generated pytest stubs", out)
        generated = test_dir / "test_sample_module.py"
        body = generated.read_text(encoding="utf-8")
        self.assertIn("def test_alpha_stub", body)
        self.assertIn("pytest.skip", body)

    def test_tool_spec_builder_creates_safe_draft(self) -> None:
        name = f"draft_tool_{uuid.uuid4().hex[:6]}"
        out = execute("tool_spec_builder", {
            "name": name,
            "description": "A generated draft tool for verification.",
            "category": "coding",
            "args_json": {"query": "text to inspect", "limit": "result limit"},
            "required_json": ["query"],
            "draft_dir": str(self.folder / "tool_specs"),
        }, allow_dangerous=True)
        self.assertTrue(out["ok"], out)
        base = self.folder / "tool_specs"
        self.assertTrue((base / f"{name}_tool.py").exists())
        self.assertTrue((base / f"test_{name}.py").exists())
        self.assertTrue((base / f"{name}_README.md").exists())
        compile((base / f"{name}_tool.py").read_text(encoding="utf-8"), f"{name}.py", "exec")
        self.assertNotIn(name, REGISTRY)


class TestQualityPrompt(unittest.TestCase):
    def test_quality_loop_is_in_system_prompt(self) -> None:
        system = agent_mod.build_system("coding")
        self.assertIn("code_review", system)
        self.assertIn("security_scan", system)
        self.assertIn("tool_spec_builder", system)
        for name in ("research_matrix", "claim_verifier", "research_report_export",
                     "code_review", "security_scan", "test_generator", "tool_spec_builder"):
            self.assertIn(name, REGISTRY)


if __name__ == "__main__":
    unittest.main()
