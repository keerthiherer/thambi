from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import aether.agent as agent_mod
from aether import safety
from aether.agent import ChatEngine, _append_tool_errors, _format_tool_result
from aether.memory import Memory
from aether.config import ROOT
from aether.tools import execute, parse_tool_calls, tool_parse_errors


class TestToolErrorReporting(unittest.TestCase):
    def test_execute_rejects_unknown_arguments(self) -> None:
        res = execute("write_file", {"path": "x.txt", "content": "x", "over_write": True})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "UnknownArgument")
        self.assertIn("over_write", res["message"])

    def test_execute_normalizes_malformed_argument_keys(self) -> None:
        res = execute("pptx_template", {"theme: ": "pitch"})
        self.assertTrue(res["ok"], res)
        self.assertIn('"selected_theme": "pitch"', res["result"])

    def test_execute_allows_empty_required_string(self) -> None:
        folder = ROOT / "data" / "drafts" / f"test_tool_err_{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "empty.txt"
        safety.set_confirm_callback(lambda title, detail: True)
        try:
            res = execute("write_file", {"path": str(target.relative_to(ROOT)), "content": ""},
                          allow_dangerous=True)
            self.assertTrue(res["ok"], res)
            self.assertEqual(target.read_text(encoding="utf-8"), "")
        finally:
            safety.set_confirm_callback(None)
            shutil.rmtree(folder, ignore_errors=True)

    def test_execute_includes_structured_handler_error(self) -> None:
        res = execute("no_such_tool", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "UnknownTool")
        self.assertIn("Unknown tool", res["message"])

    def test_tool_parse_errors(self) -> None:
        errors = tool_parse_errors('```tool\n{"name": "write_file", "args": {"path": }\n```')
        self.assertEqual(len(errors), 1)
        self.assertIn("Malformed tool JSON", errors[0])
        self.assertEqual(tool_parse_errors('```tool\n{"name": "list_tasks", "args": {}}\n```'), [])

    def test_tool_parser_repairs_colon_in_quoted_keys(self) -> None:
        block = '```tool\n{"name": "pptx_inspect", "args": {"path: "pitch_deck.pptx"}}\n```'
        self.assertEqual(tool_parse_errors(block), [])
        calls = parse_tool_calls(block)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "pptx_inspect")
        self.assertEqual(calls[0]["args"]["path"], "pitch_deck.pptx")

    def test_tool_parser_repairs_quoted_key_with_external_colon(self) -> None:
        block = '```tool\n{"name": "pptx_template", "args": {"theme: ": "premium"}}\n```'
        self.assertEqual(tool_parse_errors(block), [])
        calls = parse_tool_calls(block)
        self.assertEqual(calls[0]["args"]["theme"], "premium")

    def test_tool_parser_salvages_malformed_nested_spec_string(self) -> None:
        spec = (
            '{"theme":"premium","title":"Designing What Matters","slides":['
            '{"id":1,"content":"Title Slide: Designing What Matters","type":"title-slide"},'
            '{"id":8,"content":"Quote Slide: Customer voice ("It works")","type":"quote-slide"}]}'
        )
        escaped = spec.replace('"', '\\"').replace('\\"It works\\"', '"It works"')
        block = ('```tool\n{"name": "pptx_create", "args": {"path": "pitch_deck.pptx", '
                 '"spec": "' + escaped + '"}}}\n```')
        self.assertEqual(tool_parse_errors(block), [])
        calls = parse_tool_calls(block)
        self.assertEqual(len(calls), 1)
        parsed_spec = calls[0]["args"]["spec"]
        self.assertEqual(parsed_spec["theme"], "premium")
        self.assertEqual(len(parsed_spec["slides"]), 2)
        self.assertEqual(parsed_spec["slides"][1]["type"], "quote-slide")

    def test_tool_parser_repairs_unquoted_keys_and_placeholders(self) -> None:
        block = ('```tool\n{"name": "pptx_create", "args": {path: "pitch_deck.pptx", '
                 'spec: "template path", theme: "premium", title: "Pitch Deck", '
                 'subtitle: "Your Company Name", slides: "[slide objects]"}}\n```')
        self.assertEqual(tool_parse_errors(block), [])
        calls = parse_tool_calls(block)
        self.assertEqual(len(calls), 1)
        args = calls[0]["args"]
        self.assertEqual(args["path"], "pitch_deck.pptx")
        self.assertEqual(args["theme"], "premium")
        self.assertEqual(args["spec"], "")
        self.assertEqual(args["slides"], [])

    def test_format_and_append_errors(self) -> None:
        result = {"ok": False, "error_type": "FileExistsError", "message": "data/file2.py exists."}
        formatted = _format_tool_result("write_file", result)
        self.assertIn("TOOL ERROR (write_file)", formatted)
        self.assertIn("ERROR TYPE: FileExistsError", formatted)
        self.assertIn("ERROR MESSAGE: data/file2.py exists.", formatted)
        appended = _append_tool_errors("I could not finish.", [{"name": "write_file", **result}])
        self.assertIn("Tool errors encountered:", appended)
        self.assertIn("FileExistsError: data/file2.py exists.", appended)
        already = "write_file: FileExistsError: data/file2.py exists."
        self.assertEqual(_append_tool_errors(already, [{"name": "write_file", **result}]), already)

    def test_repeated_identical_errors_are_collapsed(self) -> None:
        error = {"ok": False, "error_type": "RuntimeError",
                 "message": "HTTP 404 for https://www.example.com/ (placeholder URL)"}
        appended = _append_tool_errors("Answer.", [{"name": "research_url", **error} for _ in range(4)])
        self.assertEqual(appended.count("HTTP 404"), 1)
        self.assertIn("repeated 4 times", appended)


class TestReplyErrorDisclosure(unittest.TestCase):
    def test_vague_answer_gets_exact_tool_error(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "reply.db")
        engine = ChatEngine(server=None, mem=mem)
        replies = [
            '```tool\n{"name":"patch_file","args":{"path":"data/drafts/missing_file.py",'
            '"old":"x","new":"y"}}\n```',
            "I could not finish the edit.",
        ]

        def fake_stream(messages, on_token, server=None, stop_event=None, **kwargs):
            return {"text": replies.pop(0), "usage": {}, "finish_reason": "stop"}

        try:
            with patch.object(agent_mod, "stream", side_effect=fake_stream):
                result = engine.reply("edit the file")
        finally:
            mem.close()
            tmp.cleanup()
        self.assertIn("Tool errors encountered:", result["text"])
        self.assertIn("patch_file", result["text"])
        self.assertIn("FileNotFoundError", result["text"])


class TestRepeatedToolSuppression(unittest.TestCase):
    def test_identical_failed_calls_are_suppressed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "repeat.db")
        engine = ChatEngine(server=None, mem=mem)
        call = '```tool\n{"name":"pptx_template","args":{"theme":"premium"}}\n```'
        replies = [call, call, call, "Here is the deck plan."]

        def fake_stream(messages, on_token, server=None, stop_event=None, **kwargs):
            return {"text": replies.pop(0) if replies else "Done.", "usage": {}, "finish_reason": "stop"}

        try:
            with patch.object(agent_mod, "stream", side_effect=fake_stream), \
                 patch.object(agent_mod, "execute", return_value={
                     "ok": False, "error_type": "ValueError", "message": "Unknown theme 'premium'"
                 }) as executed:
                result = engine.reply("design a premium pitch deck", module="designer")
        finally:
            mem.close()
            tmp.cleanup()

        self.assertEqual(executed.call_count, 2)
        self.assertTrue(any(t.get("error_type") == "RepeatedToolFailure"
                            for t in result["tool_results"]))


class TestFileToolFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_file_fix_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.rel = str(self.folder.relative_to(ROOT))
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_write_file_accepts_line_list(self) -> None:
        res = execute("write_file", {"path": f"{self.rel}/lines.py", "content": ["a = 1", "b = 2"]},
                      allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.folder / "lines.py").read_text(encoding="utf-8"), "a = 1\nb = 2")

    def test_patch_file_accepts_line_list(self) -> None:
        target = self.folder / "code.py"
        target.write_text("old line\nkeep\n", encoding="utf-8")
        res = execute("patch_file", {"path": f"{self.rel}/code.py", "old": ["old line"],
                                     "new": ["new line"]}, allow_dangerous=True)
        self.assertTrue(res["ok"], res)
        self.assertIn("new line", target.read_text(encoding="utf-8"))

    def test_patch_file_rejects_empty_old(self) -> None:
        target = self.folder / "empty_old.py"
        target.write_text("content\n", encoding="utf-8")
        res = execute("patch_file", {"path": f"{self.rel}/empty_old.py", "old": "", "new": "x"},
                      allow_dangerous=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "ValueError")
        self.assertIn("must not be empty", res["message"])


if __name__ == "__main__":
    unittest.main()
