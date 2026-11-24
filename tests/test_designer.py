from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pptx import Presentation

import aether.tools.designer as designer_mod
from aether import safety
from aether.agent import MODULE_CATEGORIES, build_system
from aether.config import ROOT
from aether.tools import REGISTRY, execute, specs_text


class TestDesignerMode(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = ROOT / "data" / "drafts" / f"test_designer_{uuid.uuid4().hex[:8]}"
        self.folder.mkdir(parents=True, exist_ok=True)
        safety.set_confirm_callback(lambda title, detail: True)

    def tearDown(self) -> None:
        safety.set_confirm_callback(None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def rel(self, path: Path) -> str:
        return str(path.relative_to(ROOT))

    def test_designer_persona_and_tools(self) -> None:
        prompt = build_system("designer")
        self.assertIn("Executive presentation designer", prompt)
        self.assertIn("pptx_create", prompt)
        self.assertEqual(MODULE_CATEGORIES["designer"][0], "designer")
        for name in ("pptx_template", "pptx_create", "pptx_inspect", "open_design_app",
                     "open_presentation"):
            self.assertIn(name, REGISTRY)
        specs = specs_text(["designer"])
        self.assertIn("pptx_create (designer; needs confirmation", specs)

    def test_template_lists_design_system(self) -> None:
        res = execute("pptx_template", {"theme": "pitch"})
        self.assertTrue(res["ok"], res)
        data = json.loads(res["result"])
        self.assertIn("midnight", data["themes"])
        self.assertIn("stats", data["slide_types"])
        self.assertTrue(data["design_rules"])

    def test_create_and_inspect_high_quality_deck(self) -> None:
        image = self.folder / "visual.png"
        Image.new("RGB", (640, 360), (14, 165, 233)).save(image)
        target = self.folder / "pitch.pptx"
        spec = {
            "theme": "pitch",
            "title": "Melouz Growth Deck",
            "subtitle": "Designer smoke test",
            "author": "THAMBI",
            "slides": [
                {"type": "title", "title": "Designing What Matters", "subtitle": "2026 pitch"},
                {"type": "agenda", "title": "Flow", "items": ["Problem", "Solution", "Proof"]},
                {"type": "section", "title": "The opportunity"},
                {"type": "content", "title": "One sharp problem", "points": ["Slow", "Inconsistent"], "callout": "Focus."},
                {"type": "cards", "title": "Our advantage", "cards": [{"title": "Speed", "body": "Rapid delivery."}, {"title": "Quality", "body": "Consistent design."}]},
                {"type": "stats", "title": "Traction", "stats": {"42%": "Faster delivery", "3.1x": "Higher conversion"}},
                {"type": "timeline", "title": "Roadmap", "steps": ["Discover", "Build", "Scale"]},
                {"type": "chart", "title": "Growth", "labels": ["Q1", "Q2"], "values": [12, 34], "series": "Revenue", "callout": "Compounding adoption."},
                {"type": "quote", "title": "Voice", "quote": "It feels like one team.", "author": "Pilot customer"},
                {"type": "comparison", "title": "Why us", "left_title": "Before", "left_points": ["Manual"], "right_title": "After", "right_points": ["Automated"]},
                {"type": "table", "title": "Plan", "headers": ["Phase", "Status"], "rows": [["Pilot", "Ready"]]},
                {"type": "image", "title": "Visual", "image": self.rel(image), "caption": "Brand visual"},
                {"type": "closing", "title": "Let's build it", "subtitle": "Pilot in 30 days"},
            ],
        }
        created = execute("pptx_create", {"path": self.rel(target), "spec": spec}, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        self.assertTrue(target.exists())
        inspected = execute("pptx_inspect", {"path": self.rel(target)})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 13", inspected["result"])
        self.assertIn("Designing What Matters", inspected["result"])
        prs = Presentation(str(target))
        self.assertEqual(len(prs.slides), 13)
        self.assertAlmostEqual(prs.slide_width / 914400, 13.333, places=2)
        self.assertAlmostEqual(prs.slide_height / 914400, 7.5, places=2)

    def test_premium_theme_and_unknown_theme_fallback(self) -> None:
        premium = execute("pptx_template", {"theme": "premium"})
        self.assertTrue(premium["ok"], premium)
        premium_data = json.loads(premium["result"])
        self.assertEqual(premium_data["selected_theme"], "premium")
        self.assertIn("premium", premium_data["themes"])

        luxury = execute("pptx_template", {"theme": "luxury"})
        self.assertTrue(luxury["ok"], luxury)
        self.assertEqual(json.loads(luxury["result"])["selected_theme"], "premium")

        unknown = execute("pptx_template", {"theme": "does_not_exist"})
        self.assertTrue(unknown["ok"], unknown)
        unknown_data = json.loads(unknown["result"])
        self.assertEqual(unknown_data["selected_theme"], "midnight")
        self.assertIn("resolved", unknown_data["theme_note"])

        deck = execute("pptx_create", {
            "path": self.rel(self.folder / "premium.pptx"),
            "theme": "premium",
            "slides": [{"type": "title", "title": "Premium Deck"}],
        }, allow_dangerous=True)
        self.assertTrue(deck["ok"], deck)

    def test_minimal_deck_with_bare_filename(self) -> None:
        name = f"simple_pitch_{uuid.uuid4().hex[:8]}.pptx"
        created = execute("pptx_create", {
            "path": name,
            "title": "Simple Pitch",
            "subtitle": "Minimal spec",
            "points": ["Problem", "Solution", "Traction"],
            "slides": [],
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        self.assertIn(f"CREATED PPTX PATH: {ROOT / 'data' / 'drafts' / name}", created["result"])
        expected = ROOT / "data" / "drafts" / name
        self.assertTrue(expected.exists(), expected)
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 3", inspected["result"])
        self.assertIn("Simple Pitch", inspected["result"])
        resolved = execute("pptx_inspect", {"path": "<path_to_deck>.pptx"})
        self.assertTrue(resolved["ok"], resolved)
        self.assertIn(str(expected), resolved["result"])
        expected.unlink(missing_ok=True)

    def test_model_slide_aliases_and_content_slides_are_accepted(self) -> None:
        name = f"alias_slides_{uuid.uuid4().hex[:8]}.pptx"
        slides = [
            {"id": 1, "content": "Title Slide: Designing What Matters", "type": "title-slide"},
            {"id": 2, "content": "Agenda Slide: Story flow (Problem, Solution, Proof)", "type": "agenda-slide"},
            {"id": 3, "content": "Cards Slide: Our advantage (Speed, Quality, Trust)", "type": "cards-slide"},
            {"id": 4, "content": "Closing Slide: Let's build it (Pilot in 30 days)", "type": "closing-slide"},
        ]
        created = execute("pptx_create", {
            "path": name, "title": "Designing What Matters", "slides": slides,
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 4", inspected["result"])
        self.assertNotIn("Untitled slide", inspected["result"])
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_add_slides_inserts_new_pages(self) -> None:
        name = f"add_slides_{uuid.uuid4().hex[:8]}.pptx"
        execute("pptx_create", {
            "path": name, "title": "Pitch Deck",
            "slides": [{"type": "title"}, {"type": "content", "title": "Old slide"}, {"type": "closing"}],
        }, allow_dangerous=True)
        added = execute("pptx_add_slides", {
            "path": name,
            "after_slide": 1,
            "slides": "New market | Size, Timing, Wedge;New product | One line, One proof",
        }, allow_dangerous=True)
        self.assertTrue(added["ok"], added)
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 5", inspected["result"])
        self.assertIn("New market", inspected["result"])
        self.assertIn("New product", inspected["result"])
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_garbage_spec_and_slides_never_error(self) -> None:
        name = f"garbage_spec_{uuid.uuid4().hex[:8]}.pptx"
        created = execute("pptx_create", {
            "path": name,
            "spec": "not json at all",
            "slides": "also not json",
            "title": "Pitch Deck",
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_design_pitch_deck_from_simple_brief(self) -> None:
        name = f"brief_deck_{uuid.uuid4().hex[:8]}.pptx"
        created = execute("design_pitch_deck", {
            "topic": "THAMBI local assistant",
            "audience": "investors",
            "theme": "premium",
            "slides": 8,
            "highlights": ["Runs fully local", "No cloud dependency", "Fast on 8GB laptops"],
            "path": name,
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        self.assertIn("CREATED PPTX PATH:", created["result"])
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 8", inspected["result"])
        self.assertNotIn("Untitled slide", inspected["result"])
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_placeholder_spec_values_produce_a_deck(self) -> None:
        name = f"placeholder_spec_{uuid.uuid4().hex[:8]}.pptx"
        created = execute("pptx_create", {
            "path": name,
            "spec": "template path",
            "title": "Pitch Deck",
            "subtitle": "Your Company Name",
            "slides": "[slide objects]",
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertIn("Slides: 2", inspected["result"])
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_placeholder_path_without_deck_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(designer_mod, "ROOT", Path(td)):
            res = execute("pptx_inspect", {"path": "<path_to_deck>.pptx"})
        self.assertFalse(res["ok"])
        self.assertIn("placeholder", res["message"].lower())

    def test_untitled_slides_get_meaningful_headings(self) -> None:
        name = f"untitled_defaults_{uuid.uuid4().hex[:8]}.pptx"
        created = execute("pptx_create", {
            "path": name,
            "title": "Pitch Deck",
            "slides": [{"type": "title"}, {"type": "content", "points": ["One"]}, {"type": "closing"}],
        }, allow_dangerous=True)
        self.assertTrue(created["ok"], created)
        inspected = execute("pptx_inspect", {"path": name})
        self.assertTrue(inspected["ok"], inspected)
        self.assertNotIn("Untitled slide", inspected["result"])
        self.assertIn("Key points", inspected["result"])
        self.assertIn("Thank you", inspected["result"])
        (ROOT / "data" / "drafts" / name).unlink(missing_ok=True)

    def test_invalid_slide_type_is_reported(self) -> None:
        res = execute("pptx_create", {
            "path": self.rel(self.folder / "bad.pptx"),
            "slides": [{"type": "unknown"}],
        }, allow_dangerous=True)
        self.assertFalse(res["ok"])
        self.assertIn("Unknown slide type", res["message"])

    def test_open_design_app_validation(self) -> None:
        res = execute("open_design_app", {"app": "photoshop"}, allow_dangerous=True)
        self.assertFalse(res["ok"])
        self.assertIn("canva", res["message"].lower())


if __name__ == "__main__":
    unittest.main()
