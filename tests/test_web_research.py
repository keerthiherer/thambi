from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import aether.agent as agent_mod
import aether.tools.research as research_mod
from aether.retrieval import retrieve
from aether.ui import _dedupe_source_items


class TestWebResearch(unittest.TestCase):
    def test_multi_angle_queries_are_unique(self) -> None:
        angles = research_mod._research_angles("compare react vs vue", 6)
        self.assertEqual(len(angles), 6)
        self.assertEqual(len({a.lower() for a in angles}), 6)
        self.assertTrue(any("comparison" in a for a in angles))

    def test_source_selection_prefers_distinct_domains(self) -> None:
        items = [
            {"title": "A1", "url": "https://a.com/1", "snippet": "one"},
            {"title": "A2", "url": "https://a.com/2", "snippet": "two"},
            {"title": "B1", "url": "https://b.org/1", "snippet": "three"},
            {"title": "C1", "url": "https://c.edu/1", "snippet": "four"},
        ]
        chosen = research_mod._diverse_sources(items, 3)
        self.assertEqual(len(chosen), 3)
        self.assertEqual(len({research_mod._source_domain(i["url"]) for i in chosen}), 3)

    def test_deep_web_research_builds_diverse_brief(self) -> None:
        pools = [
            [
                {"title": "Alpha", "url": "https://alpha.com/a", "snippet": "alpha snippet"},
                {"title": "Alpha two", "url": "https://alpha.com/b", "snippet": "alpha second"},
                {"title": "Beta", "url": "https://beta.org/b", "snippet": "beta snippet"},
            ],
            [
                {"title": "Gamma", "url": "https://gamma.edu/g", "snippet": "gamma snippet"},
                {"title": "Delta", "url": "https://delta.com/d", "snippet": "delta snippet"},
            ],
        ]
        mem = Mock()
        with patch.object(research_mod.safety, "require_external", return_value=True), \
             patch.object(research_mod, "default_memory", return_value=mem), \
             patch.object(research_mod, "_fetch_research_text", return_value="Evidence text from page."), \
             patch("aether.tools.opencode_tools._web_search_items", side_effect=lambda q, n: pools.pop(0) if pools else []):
            out = research_mod.deep_web_research("best local research workflow", sources=4)

        self.assertIn("MULTI-SOURCE RESEARCH BRIEF", out)
        self.assertIn("SEARCH ANGLES:", out)
        self.assertIn("[R1]", out)
        self.assertIn("[R4]", out)
        self.assertIn("Cite each distinct URL once", out)
        self.assertEqual(mem.upsert_document.call_count, 4)


class TestCitationCleanup(unittest.TestCase):
    def test_retrieval_groups_chunks_per_unique_url(self) -> None:
        mem = Mock()
        mem.search_chunks.return_value = [
            {"path": "url:https://alpha.com/a", "heading": "Intro", "text": "alpha one", "score": 0.9},
            {"path": "url:https://alpha.com/a", "heading": "Details", "text": "alpha two", "score": 0.8},
            {"path": "url:https://beta.org/b", "heading": "", "text": "beta text", "score": 0.7},
        ]
        mem.search_memories.return_value = []
        cfg = {"memory": {"retrieval_chunks": 8, "max_context_chars": 9000}}
        with patch("aether.retrieval.load", return_value=cfg):
            context, sources = retrieve("query", mem)

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["id"], "S1")
        self.assertEqual(sources[1]["id"], "S2")
        self.assertIn("alpha one", context)
        self.assertIn("alpha two", context)
        self.assertIn("beta text", context)

    def test_ui_source_items_are_deduplicated_and_renumbered(self) -> None:
        sources = [
            {"id": "S7", "path": "url:https://alpha.com/a"},
            {"id": "S8", "path": "https://alpha.com/a/"},
            {"id": "S9", "path": "url:https://beta.org/b"},
        ]
        cleaned = _dedupe_source_items(sources)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual([s["id"] for s in cleaned], ["S1", "S2"])

    def test_system_prompt_requires_multi_source_citations(self) -> None:
        system = agent_mod.build_system("chat")
        self.assertIn("deep_web_research", system)
        self.assertIn("Never repeat one URL", system)
        self.assertIn("/web <question>", agent_mod.HELP)


if __name__ == "__main__":
    unittest.main()
