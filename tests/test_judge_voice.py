from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from src.judge import _banned_voice_hits, validate_plan
from src.schema import ExecutionPlan, PlanItem

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def _plan(**field_decisions) -> ExecutionPlan:
    items = [
        PlanItem(field=field, decision=decision, source="test",
                 rationale="", rubric_check="", brand_check="")
        for field, decision in field_decisions.items()
    ]
    return ExecutionPlan(
        page_url="https://example.org/",
        items=items,
        body_changes=[],
        primary_keyword="taoist poetry",
        secondary_keywords=[],
        keyword_pool=[],
        redirect_mapping=None,
    )


class VoiceEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.brand = _load_yaml("brand_constitution.yaml")
        self.rubric = _load_yaml("seo_rubric.yaml")
        self.banned = self.brand["voice"]["banned_marketing_language"]

    def test_brand_constitution_has_voice_section(self):
        voice = self.brand.get("voice", {})
        self.assertIn("identity_anchor", voice)
        self.assertIn("questions", voice["identity_anchor"].lower())
        self.assertTrue(voice.get("banned_marketing_language"))
        self.assertTrue(voice.get("edit_examples"))
        for example in voice["edit_examples"]:
            self.assertIn("suggested", example)
            self.assertIn("revised", example)
            self.assertIn("lesson", example)

    def test_banned_hits_word_boundary(self):
        # 'Nurture' as a command is banned...
        self.assertEqual(
            _banned_voice_hits("Nurture inner quiet and ancient wisdom", self.banned),
            ["nurture"],
        )
        # ...but 'transformation' must NOT trip 'transform your'
        # (Zhuangzi's 'transformation of things' is core vocabulary).
        self.assertEqual(
            _banned_voice_hits("The transformation of things", self.banned), []
        )
        self.assertEqual(
            _banned_voice_hits("Inner quiet, creative life, ancient wisdom", self.banned),
            [],
        )

    def test_validate_plan_flags_banned_language_in_reader_facing_fields(self):
        plan = _plan(
            meta_description="Nurture inner quiet and unlock ancient wisdom.",
            seo_title="Tao Te Ching & Zen Poetry | Zen Mountain Journal",
        )
        plan = validate_plan(plan, self.rubric, self.brand)
        by_field = {i.field: i for i in plan.items}
        self.assertIn("banned marketing language", by_field["meta_description"].brand_check)
        self.assertIn("nurture", by_field["meta_description"].brand_check)
        self.assertIn("unlock", by_field["meta_description"].brand_check)
        self.assertIn("✗", by_field["meta_description"].brand_check)
        self.assertNotIn("banned", by_field["seo_title"].brand_check)

    def test_validate_plan_skips_keep_current_and_non_reader_fields(self):
        plan = _plan(
            h1="keep current",
            url_slug="nurture-garden",  # slug is not reader-facing prose
        )
        plan = validate_plan(plan, self.rubric, self.brand)
        for item in plan.items:
            self.assertNotIn("banned", item.brand_check)

    def test_validate_plan_backward_compatible_without_brand(self):
        plan = _plan(meta_description="Nurture inner quiet.")
        plan = validate_plan(plan, self.rubric)  # no brand arg
        self.assertNotIn("banned", plan.items[0].brand_check)


class SingleModelConfigTests(unittest.TestCase):
    def test_defaults_use_single_claude_generator_and_claude_judge(self):
        cfg = _load_yaml("models.yaml")
        self.assertEqual(cfg["defaults"]["generators"], ["claude"])
        self.assertEqual(cfg["defaults"]["judge"], "claude")
        claude = next(m for m in cfg["models"] if m["id"] == "claude")
        self.assertEqual(claude["model"], "claude-sonnet-4-6")


if __name__ == "__main__":
    unittest.main()
