from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from aether.agent import build_system, parse_tool_calls, ChatEngine
from aether.ui import format_response
from aether.memory import Memory


class TestAgent(unittest.TestCase):
    def test_build_system(self):
        for mod in ("chat", "research", "coding", "ece", "desktop", "ceo", "psychology", "hr", "voice"):
            sys_prompt = build_system(mod)
            self.assertIn("THAMBI", sys_prompt)
            self.assertIn("Melouz Technologies", sys_prompt)
            self.assertIn("Tool protocol", sys_prompt)
            self.assertIn(f"Active module:", sys_prompt)
        self.assertIn("emotion_analyze", build_system("psychology"))
        self.assertIn("hr_interview_kit", build_system("hr"))

    def test_designer_prompt_self_heals_without_asking_user(self):
        sys_prompt = build_system("designer")
        self.assertIn("design_pitch_deck ONCE", sys_prompt)
        self.assertIn("Do not ask the user to confirm file creation", sys_prompt)
        self.assertIn("recover immediately", sys_prompt)

    def test_parse_tool_calls(self):
        sample_tool_block = """Let me calculate that for you:
```tool
{"name": "ece_calculate", "args": {"calculation": "ohms_law", "parameters": {"voltage": 12, "resistance": 100}}}
```
"""
        calls = parse_tool_calls(sample_tool_block)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "ece_calculate")
        self.assertEqual(calls[0]["args"]["calculation"], "ohms_law")

        sample_xml_tag = """<tool>{"name": "list_tasks", "args": {}}</tool>"""
        calls2 = parse_tool_calls(sample_xml_tag)
        self.assertEqual(len(calls2), 1)
        self.assertEqual(calls2[0]["name"], "list_tasks")

    def test_command_dispatch(self):
        tmp_dir = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp_dir.name) / "test_cmd.db")
        try:
            engine = ChatEngine(server=None, mem=mem)
            res_help = engine.command("/help")
            self.assertTrue(res_help["command"])
            self.assertIn("/help", res_help["text"])

            res_hw = engine.command("/hw")
            self.assertTrue(res_hw["command"])
            self.assertIn("machine", res_hw["text"])

            res_calc = engine.command('/calc ohms_law {"voltage": 10, "resistance": 5}')
            self.assertTrue(res_calc["command"])
            self.assertIn('"current_A": 2.0', res_calc["text"])

            res_task = engine.command("/task Order PCB samples")
            self.assertTrue(res_task["command"])
            self.assertIn("Task #", res_task["text"])
        finally:
            mem.close()
            tmp_dir.cleanup()


class TestResponseFormatting(unittest.TestCase):
    def test_markdown_and_math_are_readable(self):
        sample = (
            "### Even Signal\n"
            "A signal **x(t)** is even if \\(x(-t)=x(t)\\).\n\n"
            "- \\(x(t)=t^2\\)\n"
            "- \\(x(t)=\\cos(2\\pi t)\\)\n\n"
            "SOURCES\n"
            "[S1] url:https://en.wikipedia.org/wiki/Diode\n"
            "[S2] url:https://en.wikipedia.org/wiki/Diode\n"
        )
        out = format_response(sample)
        self.assertIn("Even Signal", out)
        self.assertIn("• x(t)=t²", out)
        self.assertIn("cos(2π t)", out)
        self.assertNotIn("###", out)
        self.assertNotIn("\\(", out)
        self.assertNotIn("**", out)
        self.assertEqual(out.count("Diode"), 1)

    def test_code_blocks_are_preserved(self):
        out = format_response("```python\nprint('hello')\n```")
        self.assertIn("Code:", out)
        self.assertIn("print('hello')", out)


if __name__ == "__main__":
    unittest.main()
