from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from aether import retrieval, safety, utcp
from aether.config import ROOT
from aether.idle import IdleManager
from aether.llm import LlamaServer
from aether.memory import Memory
from aether.tools import (
    parse_tool_calls,
    tool_parse_errors,
    execute,
    designer,
    ece,
    files,
    kicad,
    opencode_tools,
    papers,
    psychology,
    research,
    utcp_tools,
)


class TestSafetyAndParsingRegressions(unittest.TestCase):
    def test_validate_read_rejects_outside_roots(self) -> None:
        outside = ROOT.parent / "windows_ini_should_not_be_readable.txt"
        with self.assertRaises(PermissionError):
            safety.validate_read(outside)

    def test_validate_http_url_rejects_private_addresses(self) -> None:
        with self.assertRaises(ValueError):
            safety.validate_http_url("http://127.0.0.1:8191", "UTCP URL")

    def test_valid_json_string_with_word_colon_is_not_repaired(self) -> None:
        block = '```tool\n{"name": "search_documents", "args": {"query": "alpha, beta: gamma"}}\n```'
        calls = parse_tool_calls(block)
        self.assertEqual(calls, [{"name": "search_documents", "args": {"query": "alpha, beta: gamma"}}])

    def test_malformed_json_without_comma_reports_parse_error(self) -> None:
        block = '```tool\n{"name": "x" "args": {}}\n```'
        errors = tool_parse_errors(block)
        self.assertTrue(errors)
        self.assertIn("Malformed tool JSON", errors[0])
        self.assertEqual(parse_tool_calls(block), [])

    def test_execute_rejects_non_dict_args(self) -> None:
        result = execute("list_tasks", ["not", "a", "dict"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "InvalidArguments")


class TestConfirmationRegressions(unittest.TestCase):
    def test_web_search_denial_prevents_network(self) -> None:
        with mock.patch.object(safety, "require_external", return_value=False), \
             mock.patch.object(opencode_tools, "_web_search_items") as search:
            with self.assertRaises(PermissionError):
                opencode_tools.web_search("secret topic")
            search.assert_not_called()

    def test_deep_research_denial_prevents_network(self) -> None:
        with mock.patch.object(safety, "require_external", return_value=False), \
             mock.patch.object(opencode_tools, "_web_search_items") as search:
            with self.assertRaises(PermissionError):
                research.deep_web_research("topic", index=False)
            search.assert_not_called()

    def test_paper_lookup_denial_prevents_network(self) -> None:
        with mock.patch.object(safety, "require_external", return_value=False), \
             mock.patch.object(papers.requests, "get") as get:
            with self.assertRaises(PermissionError):
                papers.paper_lookup("topic")
            get.assert_not_called()

    def test_utcp_remote_bind_requires_opt_in(self) -> None:
        server = utcp.UtcpServer(host="0.0.0.0", port=0)
        with mock.patch.object(utcp, "get", return_value=False):
            with self.assertRaises(PermissionError):
                server.start()

    def test_utcp_external_calls_do_not_send_local_token(self) -> None:
        manual = {"tools": [{"name": "remote", "tool_provider": {
            "provider_type": "http", "url": "https://provider.example.com/call"}}]}
        with mock.patch.object(safety, "require_external", return_value=True), \
             mock.patch.object(utcp.config if hasattr(utcp, "config") else utcp, "get", return_value="LOCAL_SECRET"), \
             mock.patch.object(utcp.requests, "get") as get, \
             mock.patch.object(utcp.requests, "post") as post:
            get.return_value.status_code = 200
            get.return_value.json.return_value = manual
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"ok": True}
            utcp_tools.utcp_call("https://manual.example.com/utcp/manual", "remote", {})
        self.assertNotIn("X-UTCP-Token", get.call_args.kwargs.get("headers", {}))
        self.assertNotIn("X-UTCP-Token", post.call_args.kwargs.get("headers", {}))


class TestToolCorrectnessRegressions(unittest.TestCase):
    def test_crossref_author_name_field_is_kept(self) -> None:
        item = {
            "DOI": "10.1/test", "title": ["Test"],
            "author": [{"name": "World Health Organization"}, {"given": "Jane", "family": "Doe"}],
            "published": {"date-parts": [[2024]]}, "container-title": ["Journal"],
            "URL": "https://doi.org/10.1/test", "is-referenced-by-count": 1,
        }
        with mock.patch.object(safety, "require_external", return_value=True), \
             mock.patch.object(papers.requests, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"message": {"items": [item], "total-results": 1}}
            out = papers.paper_lookup("test")
        self.assertIn("World Health Organization, Jane Doe", out)
        self.assertIn("is-referenced-by-count", str(get.call_args.kwargs.get("params", {}).get("select")))

    def test_bibtex_escapes_special_characters_and_deduplicates_keys(self) -> None:
        out = papers.bibtex_builder([
            {"key": "smith2024", "type": "article", "title": "Cost 100% & Risk_A",
             "author": "Doe, Jane", "year": "2024", "venue": "J. Test"},
            {"key": "smith2024_b", "type": "article", "title": "Second", "author": "Roe", "year": "2024"},
            {"key": "smith2024", "type": "article", "title": "Third", "author": "Poe", "year": "2024"},
        ])
        self.assertIn(r"Cost 100\% \& Risk\_A", out)
        keys = [line.split("{", 1)[1].split(",", 1)[0] for line in out.splitlines() if line.startswith("@article")]
        self.assertEqual(len(keys), len(set(keys)))

    def test_latex_section_escapes_plain_dollar_sign(self) -> None:
        source = papers.latex_paper("T", sections=[("Costs", "Unit cost is $5 per item")])
        self.assertIn(r"Unit cost is \$5 per item", source)

    def test_literature_review_does_not_assign_unsupported_handle(self) -> None:
        out = papers.literature_review("[S1] AI data\n[S2] ML data\n")
        self.assertIn("| none |", out)
        self.assertIn("no source can be attributed", out)

    def test_claim_verifier_does_not_substring_match(self) -> None:
        out = research.claim_verifier(
            "The data improves latency",
            "[S1] source.example | https://source.example\nWhy: Metadata improves latency.",
            min_sources=1,
        )
        self.assertNotIn("SUPPORTED BY PROVIDED EVIDENCE", out)

    def test_research_matrix_keeps_confidence_local_to_source(self) -> None:
        brief = (
            "MULTI-SOURCE RESEARCH BRIEF\n"
            "[R1] https://a.example.edu\nWhy: The method improves latency substantially in modern systems.\n"
            "[R2] https://b.example.org\nWhy: However, the result may not generalize across systems.\n\n"
            "EXTRACTED EVIDENCE:\n"
            "[R1] https://a.example.edu\nThe method improves latency substantially in modern systems.\n"
            "[R2] https://b.example.org\nHowever, the result may not generalize across systems.\n"
        )
        out = research.research_matrix(brief)
        rows = [line for line in out.splitlines() if line.startswith("| ") and len(line) > 2 and line[2].isdigit()]
        confidences = [row.rsplit("|", 2)[1].strip() for row in rows]
        self.assertIn("high", confidences)
        self.assertIn("low", confidences)

    def test_ipc2221_layer_constants_and_dt_alias(self) -> None:
        result = json.loads(ece.ece_calculate("trace_width", {
            "current_a": 2, "temp_rise_c": 10, "copper_oz": 1,
        }))["result"]
        internal = json.loads(ece.ece_calculate("trace_width", {
            "current_a": 2, "dt": 10, "copper_oz": 1, "internal": True,
        }))["result"]
        self.assertLess(result["width_mm"], internal["width_mm"])

    def test_large_kicad_board_stays_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.kicad_pcb"
            body = ["(kicad_pcb (version 20221018)"]
            for i in range(500):
                body.append(f'(footprint "Lib:F{i}" (at {i} 0) (layer "F.Cu") '
                            f'(property "Reference" "R{i}") (property "Value" "10k"))')
            body.append(")")
            path.write_text("\n".join(body), encoding="utf-8")
            data = json.loads(kicad.kicad_inspect(str(path)))
        self.assertEqual(len(data["bom"]), 400)
        self.assertTrue(any("truncated" in note for note in data["notes"]))

    def test_designer_section_slide_alias_builds(self) -> None:
        builder = designer._build({"theme": "midnight", "slides": [
            {"type": "section slide", "title": "Break"},
        ]})
        self.assertEqual(len(builder.slides), 1)

    def test_psychology_matches_use_word_boundaries(self) -> None:
        self.assertEqual(psychology._matches("She noted strong results and focused on delivery.", ("no",)), [])
        self.assertEqual(psychology._matches("No strong results.", ("no",)), ["no"])

    def test_prior_art_search_is_not_read_only(self) -> None:
        from aether.tools.registry import REGISTRY
        self.assertFalse(REGISTRY["prior_art_search"].read_only)


class TestFilesAndIndexRegressions(unittest.TestCase):
    def test_list_dir_rejects_outside_root(self) -> None:
        with self.assertRaises(PermissionError):
            files.list_dir(str(ROOT.parent))

    def test_delete_path_supports_folders(self) -> None:
        folder = ROOT / "data" / "drafts" / f"test_delete_{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(safety, "require_confirmation", return_value=True), \
             mock.patch.object(files, "_recycle") as recycle:
            out = files.delete_path(str(folder))
        self.assertIn("Moved to Recycle Bin", out)
        recycle.assert_called_once()

    def test_indexing_second_folder_does_not_remove_first(self) -> None:
        base = ROOT / "data" / "drafts" / f"test_index_{uuid.uuid4().hex[:8]}"
        first = base / "one"
        second = base / "two"
        first.mkdir(parents=True, exist_ok=True)
        second.mkdir(parents=True, exist_ok=True)
        (first / "a.txt").write_text("alpha", encoding="utf-8")
        (second / "b.txt").write_text("beta", encoding="utf-8")
        db = base / "test.db"
        mem = Memory(db)
        try:
            retrieval.index_folders([first], mem)
            report = retrieval.index_folders([second], mem)
            self.assertEqual(report["removed"], 0)
            paths = {Path(p).name for p in mem.document_state()}
            self.assertEqual(paths, {"a.txt", "b.txt"})
        finally:
            mem.close()
            for path in (first / "a.txt", second / "b.txt", db):
                path.unlink(missing_ok=True)
            first.rmdir()
            second.rmdir()
            base.rmdir()


class TestIdleRegressions(unittest.TestCase):
    def test_external_server_is_not_claimed_unloaded(self) -> None:
        server = LlamaServer()
        states: list[str] = []
        manager = IdleManager(server=server, on_state=states.append)
        with mock.patch.object(server, "is_running", return_value=True), \
             mock.patch.object(server, "stop") as stop:
            manager._maybe_unload()
        stop.assert_called_once()
        self.assertFalse(manager._unloaded)
        self.assertTrue(any("failed" in state for state in states))


if __name__ == "__main__":
    unittest.main()
