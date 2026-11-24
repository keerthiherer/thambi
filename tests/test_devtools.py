from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import aether.tools.devtools as devtools_mod
from aether import safety
from aether.config import ROOT
from aether.llm import prepare_context
from aether.tools import execute, specs_text


class TestDevTools(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_devtools_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.a = self.folder / "a.py"
        self.b = self.folder / "b.py"
        self.a.write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n", encoding="utf-8")
        self.b.write_text("def alpha():\n    return 1\n\ndef beta():\n    return 3\n", encoding="utf-8")
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def rel(self, p: Path) -> str:
        return str(p.relative_to(ROOT))

    def test_search_in_file(self) -> None:
        res = execute("search_in_file", {"path": self.rel(self.a), "pattern": "def beta", "context_lines": 0})
        self.assertTrue(res["ok"], res)
        self.assertIn(f"{self.a}:4", res["result"])
        res = execute("search_in_file", {"path": self.rel(self.a), "pattern": r"def \w+\(\)", "regex": True})
        self.assertTrue(res["ok"], res)
        self.assertIn("match 1", res["result"])
        res = execute("search_in_file", {"path": self.rel(self.a), "pattern": "not_present"})
        self.assertIn("No matches", res["result"])

    def test_read_many_files(self) -> None:
        res = execute("read_many_files", {"paths": [self.rel(self.a), self.rel(self.b)]})
        self.assertTrue(res["ok"], res)
        self.assertIn("def alpha()", res["result"])
        self.assertIn("def beta()", res["result"])

    def test_python_syntax_check(self) -> None:
        bad = self.folder / "bad.py"
        bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
        res = execute("python_syntax_check", {"paths": [self.rel(self.a), self.rel(bad)]})
        self.assertTrue(res["ok"], res)
        self.assertIn("OK", res["result"])
        self.assertIn("ERROR", res["result"])

    def test_diff_and_file_info(self) -> None:
        diff = execute("diff_text", {"path_a": self.rel(self.a), "path_b": self.rel(self.b)})
        self.assertTrue(diff["ok"], diff)
        self.assertIn("+    return 3", diff["result"])
        info = execute("file_info", {"path": self.rel(self.a)})
        self.assertTrue(info["ok"], info)
        self.assertIn('"sha256"', info["result"])

    def test_patch_file(self) -> None:
        res = execute("patch_file", {"path": self.rel(self.a), "old": "return 2", "new": "return 22"},
                      allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        self.assertIn("return 22", self.a.read_text(encoding="utf-8"))
        self.assertTrue(self.a.with_suffix(".py.aether.bak").exists())
        safety.set_confirm_callback(lambda title, detail: False)
        denied = execute("patch_file", {"path": self.rel(self.a), "old": "return 22", "new": "return 3"},
                         allow_dangerous=True)
        self.assertFalse(denied["ok"])
        self.assertIn("denied", denied["error"].lower())

    def test_create_file_aliases(self) -> None:
        for name in ("create_file", "write_code_file", "write_file"):
            with self.subTest(tool=name):
                target = self.folder / f"{name}.py"
                res = execute(name, {"path": self.rel(target), "content": "print('ok')\n"},
                              allow_dangerous=True)
                self.assertTrue(res["ok"], res)
                self.assertEqual(target.read_text(encoding="utf-8"), "print('ok')\n")

    def test_create_venv_uses_fixed_argv(self) -> None:
        env = self.folder / "mock_venv"
        done = subprocess.CompletedProcess([], 0, "venv ready", "")
        with patch.object(devtools_mod.safety, "run_fixed", return_value=done) as run:
            res = execute("create_venv", {"path": self.rel(env)}, allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:4], ["-m", "venv", str(env)])

    def test_venv_pip_install_uses_environment_pip(self) -> None:
        env = self.folder / "mock_venv"
        python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        requirements = self.folder / "requirements.txt"
        requirements.write_text("requests\n", encoding="utf-8")
        done = subprocess.CompletedProcess([], 0, "installed", "")
        with patch.object(devtools_mod.safety, "run_fixed", return_value=done) as run:
            res = execute("venv_pip_install",
                          {"venv": self.rel(env), "requirements": self.rel(requirements)},
                          allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:5], ["-m", "pip", "install", "-r"])
        self.assertEqual(argv[5], str(requirements))

    def test_tool_specs_mark_confirmation(self) -> None:
        specs = specs_text(["coding"]) + specs_text(["files"])
        self.assertIn("search_in_file (coding; read-only", specs)
        self.assertIn("patch_file (coding; needs confirmation", specs)
        self.assertIn("create_venv (coding; needs confirmation", specs)
        self.assertIn("create_file (files; needs confirmation", specs)


class TestLongContext(unittest.TestCase):
    def test_prepare_context_keeps_system_and_clips_tool_output(self) -> None:
        messages = [
            {"role": "system", "content": "SYS"},
            {"role": "system", "content": "SOURCES"},
            {"role": "user", "content": "an older question that should be dropped"},
            {"role": "user", "content": "TOOL RESULT (read_file):\n" + "x" * 20000},
            {"role": "user", "content": "latest coding question"},
        ]
        kept = prepare_context(messages, max_tokens=2048, context=4096)
        self.assertEqual(kept[0]["content"], "SYS")
        self.assertEqual(kept[1]["content"], "SOURCES")
        body = "\n".join(m["content"] for m in kept[2:])
        self.assertIn("latest coding question", body)
        self.assertIn("TOOL RESULT", body)
        self.assertIn("chars omitted", body)
        self.assertNotIn("an older question", body)

    def test_continuation_streams_until_finished(self) -> None:
        import aether.agent as agent_mod
        from aether.memory import Memory

        calls: list[dict] = []
        original = agent_mod.stream

        def fake_stream(messages, on_token, server=None, stop_event=None, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"text": "first chunk " * 30,
                        "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                        "finish_reason": "length"}
            return {"text": "second chunk", "usage": {}, "finish_reason": "stop"}

        agent_mod.stream = fake_stream
        with tempfile.TemporaryDirectory() as td:
            mem = Memory(Path(td) / "cont.db")
            try:
                engine = agent_mod.ChatEngine(server=None, mem=mem)
                res = engine.reply("write some code")
                self.assertEqual(len(calls), 2)
                self.assertIn("second chunk", res["text"])
                self.assertIn("first chunk", res["text"])
            finally:
                mem.close()
                agent_mod.stream = original


if __name__ == "__main__":
    unittest.main()
