from __future__ import annotations

import unittest

from src.google_ads import _check, _extract_page_words
from src.schema import PageSnapshot


def _assets(**overrides):
    result = {
        "headlines": [
            "Taoist Inner Energy",
            "Zen Silence Practice",
            "Tao Te Ching Energy",
            "Han Shan and Zen",
            "Wu Wei and Vital Spirit",
        ],
        "descriptions": [
            "A Taoist reflection on inner energy and silence.",
            "For readers drawn to Zen practice and quiet attention.",
            "Notes on Taoist hermits and contemplative practice.",
        ],
        "core_keywords": ["[taoist inner energy]", "[zen silence]", "[taoist hermits]"],
        "keyword_variants": {
            "[taoist inner energy]": ["[inner energy taoism]", "[taoist energy practice]"],
            "[zen silence]": ["[silence in zen]", "[zen silence practice]"],
            "[taoist hermits]": ["[tao hermits]", "[hermits taoism]"],
        },
        "negative_keywords": ["energy drink", "energy healing", "power company"],
    }
    result.update(overrides)
    return result


class GoogleAdsValidationTests(unittest.TestCase):
    def test_accepts_clean_assets(self):
        self.assertEqual(_check(_assets()), [])

    def test_rejects_campaign_negative_repeated_as_page_negative(self):
        failures = _check(_assets(negative_keywords=["free", "energy drink", "battery"]))
        self.assertTrue(any("repeat campaign negatives" in value for value in failures))

    def test_rejects_positive_negative_overlap(self):
        failures = _check(
            _assets(negative_keywords=["taoist inner energy", "energy drink", "battery"])
        )
        self.assertTrue(any("both positive and negative" in value for value in failures))

    def test_accepts_empty_negative_list(self):
        """A focused page may have no wrong-intent terms; empty is valid."""
        self.assertEqual(_check(_assets(negative_keywords=[])), [])

    def test_rejects_page_derived_negatives(self):
        """The 'mud settling' case: negatives mined from the page's own imagery."""
        page_words = _extract_page_words(
            PageSnapshot(
                url="https://example.org/stillness",
                title="Stillness and the Tao Te Ching",
                h1="Do You Have the Patience to Wait Till Your Mud Settles?",
                meta_description="Reflections on stillness in chapter 15.",
                headings=["The Muddy Water Clears"],
                body_text=(
                    "Like muddy water left to settle, the mind clears in stillness. "
                    "The rice vessel sits empty; the dipper rests by the spring water."
                ),
            )
        )
        failures = _check(
            _assets(negative_keywords=["mud settling", "rice vessel", "hiking gear"]),
            page_words=page_words,
        )
        self.assertTrue(any("page's own content" in value for value in failures))
        joined = " ".join(failures)
        self.assertIn("mud settling", joined)
        self.assertIn("rice vessel", joined)
        # A legitimate wrong-intent negative must NOT be flagged.
        self.assertNotIn("hiking gear", joined)

    def test_rejects_negatives_for_products_the_studio_sells(self):
        """Today's production case: model tried to negate print commerce terms."""
        failures = _check(
            _assets(
                negative_keywords=[
                    "art prints", "canvas prints", "framed art",
                    "poster prints", "mug designs",
                ]
            )
        )
        joined = " ".join(failures)
        self.assertIn("products the studio", joined)
        self.assertIn("art prints", joined)
        self.assertIn("canvas prints", joined)
        self.assertIn("framed art", joined)
        # Merchandise the studio does not sell stays a valid negative.
        self.assertNotIn("mug designs", joined)


if __name__ == "__main__":
    unittest.main()
