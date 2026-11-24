from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import aether.agent as agent_mod
import aether.tools.innovation as innovation_mod
from aether.tools import REGISTRY

SEARCH_ITEMS = [
    {"title": "Offline AI research notes app", "url": "https://alpha.exampletech.com/offline-notes",
     "snippet": "An offline app that organises research notes with local citations."},
    {"title": "Local assistant prior art paper", "url": "https://beta.edu/papers/local-assistant",
     "snippet": "A paper about local assistants, privacy, and citation handling."},
    {"title": "Open source research workspace", "url": "https://gamma.dev/oss/research-workspace",
     "snippet": "An open source workspace for research snippets and local indexing."},
]


class TestNoveltyTools(unittest.TestCase):
    def test_prior_art_search_builds_landscape_and_indexes_sources(self) -> None:
        mem = Mock()
        with patch.object(innovation_mod.safety, "require_external", return_value=True) as confirm, \
             patch.object(innovation_mod, "ensure_dirs"), \
             patch.object(innovation_mod, "default_memory", return_value=mem), \
             patch.object(innovation_mod, "_fetch_research_text", return_value="Evidence text about offline research notes."), \
             patch.object(innovation_mod, "_web_search_items", return_value=SEARCH_ITEMS):
            out = innovation_mod.prior_art_search("offline AI research notes app", field="productivity", index=True)

        self.assertEqual(confirm.call_count, 1)
        self.assertIn("PRIOR ART SCAN", out)
        self.assertIn("[PA1]", out)
        self.assertIn("NOVELTY READOUT", out)
        self.assertIn("not legal advice", out)
        self.assertEqual(mem.upsert_document.call_count, 3)

    def test_patent_search_uses_patent_site_queries(self) -> None:
        queries: list[str] = []

        def fake_search(query: str, limit: int) -> list[dict[str, str]]:
            queries.append(query)
            return SEARCH_ITEMS

        with patch.object(innovation_mod.safety, "require_external", return_value=True), \
             patch.object(innovation_mod, "_web_search_items", side_effect=fake_search):
            out = innovation_mod.patent_search("offline AI research notes", jurisdiction="US", status="granted")

        self.assertTrue(any("site:patents.google.com/patent/US" in q for q in queries))
        self.assertIn("PATENT LANDSCAPE SCAN", out)
        self.assertIn("POTENTIAL OVERLAP", out)
        self.assertIn("freedom to operate", out)


class TestIdeationAndPlanningTools(unittest.TestCase):
    def test_idea_lab_uses_all_frameworks_and_scores(self) -> None:
        out = innovation_mod.idea_lab(
            "make research faster for a solo founder",
            framework="all",
            constraints="8GB RAM,no cloud,offline first",
            count=6,
        )
        self.assertIn("IDEA LAB", out)
        self.assertIn("scamper", out)
        self.assertIn("triz", out)
        self.assertIn("jtbd", out)
        self.assertIn("score", out)
        self.assertIn("TOP PICKS", out)

    def test_experiment_designer_includes_metrics_and_guardrails(self) -> None:
        out = innovation_mod.experiment_designer(
            "Does the new onboarding improve activation?",
            metric="activation rate",
            sample_size=1000,
            variants=2,
            duration_days=21,
            risks="novelty effect,seasonality",
        )
        self.assertIn("EXPERIMENT DESIGN", out)
        self.assertIn("Null hypothesis", out)
        self.assertIn("Primary metric: activation rate", out)
        self.assertIn("MDE", out)
        self.assertIn("RISK CONTROLS", out)
        self.assertIn("seasonality", out)

    def test_decision_matrix_scores_and_stress_tests_weights(self) -> None:
        evidence = "\n".join([
            "Build in-house cost is low.",
            "Build in-house speed is slow.",
            "Build in-house privacy is high.",
            "Buy vendor cost is high.",
            "Buy vendor speed is fast.",
            "Buy vendor privacy is low.",
        ])
        out = innovation_mod.decision_matrix(
            options=["Build in-house", "Buy vendor"],
            criteria=["cost", "speed", "privacy"],
            weights={"cost": "0.5", "speed": "0.3", "privacy": "0.2"},
            evidence=evidence,
        )
        self.assertIn("DECISION MATRIX", out)
        self.assertIn("WEIGHTED RANKING", out)
        self.assertIn("SENSITIVITY", out)
        self.assertIn("EVIDENCE GAPS", out)
        self.assertNotIn("| N/A |", out)


class TestInnovationRegistration(unittest.TestCase):
    def test_tools_registered_and_prompt_updated(self) -> None:
        for name in ("prior_art_search", "patent_search", "idea_lab", "experiment_designer", "decision_matrix"):
            self.assertIn(name, REGISTRY)
        system = agent_mod.build_system("research")
        self.assertIn("prior_art_search", system)
        self.assertIn("Never claim patentability", system)
        self.assertIn("decision_matrix", system)


if __name__ == "__main__":
    unittest.main()
