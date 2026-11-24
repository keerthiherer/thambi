from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import aether.agent as agent_mod
import aether.tools.documents as documents_mod
import aether.tools.opencode_tools as opencode_mod
from aether import safety
from aether.config import ROOT
from aether.learning import observe, rules_block
from aether.memory import Memory
from aether.tools import REGISTRY, execute, specs_text


class TestAdaptiveLearning(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "learning.db")

    def tearDown(self) -> None:
        self.mem.close()
        self.tmp.cleanup()

    def test_extract_and_inject_rules(self) -> None:
        learned = observe(
            "Remember that I prefer metric units.\nAlways answer with exact file paths.\nMy name is Arun.",
            mem=self.mem,
        )
        rules = " | ".join(item["rule"] for item in learned)
        self.assertIn("metric units", rules.lower())
        self.assertIn("exact file paths", rules.lower())
        self.assertIn("Arun", rules)
        block = rules_block(self.mem, "chat")
        self.assertIn("ADAPTIVE LEARNING", block)
        self.assertIn("exact file paths", block)

    def test_slash_commands(self) -> None:
        engine = agent_mod.ChatEngine(server=None, mem=self.mem)
        added = engine.command("/remember Use Python 3.10 for this project")
        self.assertIn("rule #1", added["text"])
        listed = engine.command("/learned")
        self.assertIn("Use Python 3.10", listed["text"])
        removed = engine.command("/forget Python 3.10")
        self.assertIn("Removed 1", removed["text"])

    def test_reply_includes_learned_rules(self) -> None:
        self.mem.upsert_learned_rule("Always answer with exact file paths.", confidence=0.9)
        engine = agent_mod.ChatEngine(server=None, mem=self.mem)
        captured: list[list[dict]] = []

        def fake_stream(messages, on_token, server=None, stop_event=None, **kwargs):
            captured.append(messages)
            return {"text": "done", "usage": {}, "finish_reason": "stop"}

        with patch.object(agent_mod, "stream", side_effect=fake_stream):
            result = engine.reply("hello")
        self.assertEqual(result["text"], "done")
        self.assertTrue(captured)
        self.assertTrue(any("ADAPTIVE LEARNING" in m["content"] for m in captured[0]))


class TestOpencodeTools(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_opencode_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        self.rel = str(self.folder.relative_to(ROOT))

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_registered_categories(self) -> None:
        for name in ("glob_files", "grep_content", "delegate_task", "web_search"):
            self.assertIn(name, REGISTRY)
        specs = specs_text(["opencode", "documents", "learning"])
        self.assertIn("glob_files (opencode; read-only", specs)
        self.assertIn("book_write (documents; needs confirmation", specs)
        self.assertIn("remember_rule (learning;", specs)

    def test_glob_and_grep(self) -> None:
        found = execute("glob_files", {"pattern": "*.py", "root": self.rel})
        self.assertTrue(found["ok"], found)
        self.assertIn("main.py", found["result"])
        hits = execute("grep_content", {"pattern": "def alpha", "root": self.rel, "glob": "*.py"})
        self.assertTrue(hits["ok"], hits)
        self.assertIn("main.py:1", hits["result"])

    def test_run_allowed_command_requires_confirmation(self) -> None:
        safety.set_confirm_callback(lambda title, detail: False)
        try:
            res = execute("run_allowed_command", {"command": "python_version"}, allow_dangerous=True)
        finally:
            safety.set_confirm_callback(None)
        self.assertFalse(res["ok"])
        self.assertIn("denied", res["error"].lower())


class TestDocumentTools(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_documents_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def rel(self, p: Path) -> str:
        return str(p.relative_to(ROOT))

    def test_write_and_read_formats(self) -> None:
        chapters = [{"title": "Chapter 1", "content": "Hello book from THAMBI."}]
        for fmt in ("txt", "docx", "pdf", "epub"):
            target = self.folder / f"book.{fmt}"
            res = execute(
                "book_write",
                {"path": self.rel(target), "title": "Test Book", "chapters": chapters},
                allow_dangerous=True,
            )
            self.assertTrue(res["ok"], res)
            self.assertTrue(target.exists(), target)
        read = execute("book_read", {"path": self.rel(self.folder / "book.txt")})
        self.assertTrue(read["ok"], read)
        self.assertIn("Hello book from THAMBI.", read["result"])
        extracted = execute("extract_text_file", {"path": self.rel(self.folder / "book.epub")})
        self.assertTrue(extracted["ok"], extracted)
        self.assertIn("Chapter 1", extracted["result"])

    def test_book_index_uses_supplied_memory(self) -> None:
        target = self.folder / "notes.txt"
        target.write_text("# Notes\n\nPersistent offline memory test.\n", encoding="utf-8")
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "docs.db")
        try:
            with patch.object(documents_mod, "default_memory", return_value=mem):
                res = execute("book_index", {"path": self.rel(target)})
            self.assertTrue(res["ok"], res)
            self.assertIn("Indexed", res["result"])
            self.assertTrue(mem.index_stats()["documents"] >= 1)
        finally:
            mem.close()
            tmp.cleanup()


class TestInternetTools(unittest.TestCase):
    def setUp(self) -> None:
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)

    def test_internet_tools_registered(self) -> None:
        specs = specs_text(["opencode"])
        for name in ("internet_status", "web_search", "web_fetch", "browser_open"):
            self.assertIn(name, specs)

    def test_internet_status_reports_online(self) -> None:
        response = Mock(status_code=200, url="https://duckduckgo.com/")
        with patch.object(opencode_mod.requests, "get", return_value=response) as get:
            res = execute("internet_status", {})
        self.assertTrue(res["ok"], res)
        self.assertIn("Internet: online", res["result"])
        self.assertNotIn("proxies", get.call_args.kwargs)

    def test_web_search_filters_placeholder_and_duplicate_urls(self) -> None:
        page = """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F">Docs</a>
          <a class="result__snippet">Python docs</a>
        </div>
        <div class="result">
          <a class="result__a" href="https://www.example.com/">Bad</a>
          <a class="result__snippet">Placeholder</a>
        </div>
        <div class="result">
          <a class="result__a" href="https://docs.python.org/3/">Duplicate</a>
          <a class="result__snippet">Again</a>
        </div>
        """
        response = Mock(status_code=200, text=page)
        with patch.object(opencode_mod.requests, "get", return_value=response):
            result = opencode_mod.web_search("python docs", 5)
        self.assertIn("https://docs.python.org/3/", result)
        self.assertNotIn("example.com", result)
        self.assertEqual(result.count("https://docs.python.org/3/"), 1)

    def test_web_fetch_uses_system_proxies(self) -> None:
        response = Mock(status_code=200, text="Hello web page", headers={"content-type": "text/plain"})
        with patch.object(opencode_mod.requests, "get", return_value=response) as get:
            res = execute("web_fetch", {"url": "https://docs.python.org/3/", "max_chars": 2000})
        self.assertTrue(res["ok"], res)
        self.assertIn("Hello web page", res["result"])
        self.assertNotIn("proxies", get.call_args.kwargs)

    def test_browser_open_validates_and_opens(self) -> None:
        bad = execute("browser_open", {"url": "file:///C:/Windows"}, allow_dangerous=True)
        self.assertFalse(bad["ok"])
        self.assertIn("http(s)", bad["message"])
        with patch.object(opencode_mod.webbrowser, "open", return_value=True) as opened:
            res = execute("browser_open", {"url": "https://docs.python.org/3/"}, allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        opened.assert_called_once_with("https://docs.python.org/3/", new=2)

    def test_placeholder_urls_rejected_everywhere(self) -> None:
        cases = (
            ("research_url", {"url": "https://www.example.com/"}),
            ("web_fetch", {"url": "https://www.example.com/"}),
            ("browser_open", {"url": "https://www.example.com/"}),
        )
        for name, args in cases:
            with self.subTest(tool=name):
                res = execute(name, args, allow_dangerous=True)
                self.assertFalse(res["ok"])
                self.assertIn("placeholder", res["message"].lower())


if __name__ == "__main__":
    unittest.main()
