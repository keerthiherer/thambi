from __future__ import annotations

import unittest

from aether.tools import REGISTRY, execute


class TestPsychologyTools(unittest.TestCase):
    def test_tools_registered_in_expected_categories(self) -> None:
        expected_psychology = {
            "emotion_analyze", "psychology_profile", "behavior_profile", "coaching_plan",
            "wellbeing_checkin", "empathy_reply", "conflict_mediation",
        }
        expected_hr = {"hr_interview_kit", "hr_performance_review", "hr_feedback_message"}
        self.assertTrue(expected_psychology <= set(REGISTRY))
        self.assertTrue(expected_hr <= set(REGISTRY))
        for name in expected_psychology:
            self.assertEqual(REGISTRY[name].category, "psychology")
        for name in expected_hr:
            self.assertEqual(REGISTRY[name].category, "hr")

    def test_emotion_analysis_is_non_clinical_and_flags_risk(self) -> None:
        res = execute("emotion_analyze", {
            "text": "I feel worried and overwhelmed. I don't want to live like this.",
            "context": "late-night message",
        })
        self.assertTrue(res["ok"], res)
        body = res["result"]
        self.assertIn("EMOTION REFLECTION", body)
        self.assertIn("not a diagnosis", body)
        self.assertIn("SAFETY", body)

    def test_psychology_profile_is_tentative(self) -> None:
        res = execute("psychology_profile", {
            "notes": "They ask why, check data, compare options, and care about deadlines and accuracy.",
            "purpose": "coaching",
        })
        self.assertTrue(res["ok"], res)
        body = res["result"]
        self.assertIn("TENTATIVE PSYCHOLOGY PROFILE", body)
        self.assertIn("Analytical style", body)
        self.assertIn("tentative hypothesis", body)

    def test_hr_interview_kit_is_structured(self) -> None:
        res = execute("hr_interview_kit", {
            "role": "Embedded Engineer",
            "level": "senior",
            "competencies": "Ownership, PCB design, Collaboration",
            "count": 1,
        })
        self.assertTrue(res["ok"], res)
        body = res["result"]
        self.assertIn("STRUCTURED INTERVIEW KIT", body)
        self.assertIn("PCB design:", body)
        self.assertIn("Scoring rubric", body)
        self.assertIn("Bias controls", body)
        self.assertIn("does not make hiring decisions", body)

    def test_performance_review_does_not_invent_metrics(self) -> None:
        res = execute("hr_performance_review", {
            "notes": "Delivered the update. Needs to improve response time to feedback.",
            "role": "Designer",
        })
        self.assertTrue(res["ok"], res)
        body = res["result"]
        self.assertIn("PERFORMANCE REVIEW DRAFT", body)
        self.assertIn("Delivered the update.", body)
        self.assertNotIn("42%", body)
        self.assertIn("does not make rating", body)

    def test_coaching_and_feedback_tools(self) -> None:
        plan = execute("coaching_plan", {"goal": "Improve focus", "obstacles": "Too many meetings"})
        self.assertTrue(plan["ok"], plan)
        self.assertIn("GROW", plan["result"])
        feedback = execute("hr_feedback_message", {
            "situation": "Monday standup",
            "behavior": "Interrupted twice",
            "impact": "The discussion lost focus",
            "recipient": "Alex",
        })
        self.assertTrue(feedback["ok"], feedback)
        self.assertIn("FEEDBACK DRAFT - SBI", feedback["result"])
        self.assertIn("Situation: Monday standup", feedback["result"])


if __name__ == "__main__":
    unittest.main()
