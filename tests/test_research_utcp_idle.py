from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import requests

from aether.agent import MODULE_CATEGORIES, ChatEngine, build_system
from aether.config import get
from aether.idle import IdleManager
from aether.memory import Memory
from aether.tools import execute
from aether.utcp import UtcpServer, build_manual


class TestResearchMode(unittest.TestCase):
    def test_research_prompt(self):
        prompt = build_system("research")
        self.assertIn("THAMBI", prompt)
        self.assertIn("tradeoffs", prompt.lower())
        self.assertIn("hackathon", prompt.lower())
        self.assertEqual(MODULE_CATEGORIES["research"][0], "research")

    def test_research_command_usage(self):
        with tempfile.TemporaryDirectory() as td:
            mem = Memory(Path(td) / "research.db")
            try:
                engine = ChatEngine(server=None, mem=mem)
                res = engine.reply("/research")
                self.assertTrue(res["command"])
                self.assertIn("Usage: /research", res["text"])
            finally:
                mem.close()

    def test_research_reply_uses_deep_profile(self):
        import aether.agent as agent_mod

        captured = {}
        original = agent_mod.stream

        def fake_stream(messages, on_token, server=None, stop_event=None, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            on_token("answer")
            return {"text": "A rigorous research answer", "usage": {}}

        agent_mod.stream = fake_stream
        with tempfile.TemporaryDirectory() as td:
            mem = Memory(Path(td) / "research2.db")
            try:
                engine = ChatEngine(server=None, mem=mem)
                res = engine.reply("/research How should I scope my hackathon project?")
                self.assertEqual(res["text"], "A rigorous research answer")
                self.assertEqual(captured["kwargs"]["max_tokens"], get("generation.research_max_tokens"))
                self.assertIn("Deep research", captured["messages"][0]["content"])
            finally:
                mem.close()
                agent_mod.stream = original


class TestUtcp(unittest.TestCase):
    def test_manual_has_tools(self):
        manual = build_manual("http://127.0.0.1:9999")
        self.assertEqual(manual["version"], "1.0")
        names = {t["name"] for t in manual["tools"]}
        self.assertIn("read_file", names)
        self.assertIn("ece_calculate", names)
        for tool in manual["tools"]:
            self.assertIn("inputs", tool)
            self.assertEqual(tool["tool_provider"]["provider_type"], "http")

    def test_local_gateway_roundtrip(self):
        srv = UtcpServer(port=0)
        try:
            url = srv.start()
            manual = requests.get(f"{url}/utcp/manual", timeout=5).json()
            self.assertGreater(len(manual["tools"]), 10)
            res = requests.post(f"{url}/utcp/call",
                                json={"name": "list_dir", "args": {"path": "config"}},
                                timeout=10).json()
            self.assertTrue(res["ok"])
            self.assertIn("aether.json", res["result"])
        finally:
            srv.stop()

    def test_local_manual_tool(self):
        res = execute("utcp_manual", {}, allow_dangerous=True)
        self.assertTrue(res["ok"])
        self.assertIn("tools", json.loads(res["result"]))


class TestIdleManager(unittest.TestCase):
    def test_touch_and_tick(self):
        manager = IdleManager()
        manager.touch()
        self.assertFalse(manager.is_idle())
        manager.tick()
        self.assertGreaterEqual(manager.idle_seconds(), 0.0)
        manager.stop()


if __name__ == "__main__":
    unittest.main()
