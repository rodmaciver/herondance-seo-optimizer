from __future__ import annotations

import unittest

from src.google_ads import _check, _extract_page_words, _sanitize_negatives
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

    def test_duplicate_headline_failure_names_the_lines(self):
        """Tonight's production case: retry loop needs to see WHICH lines collide."""
        failures = _check(
            _assets(
                headlines=[
                    "Tao of Suffering and Desire",
                    "Suffering and Desire of Tao",
                    "Zen Silence Practice",
                ]
            )
        )
        joined = " ".join(failures)
        self.assertIn("Near-duplicate headlines", joined)
        self.assertIn("Tao of Suffering and Desire", joined)
        self.assertIn("Suffering and Desire of Tao", joined)

    def test_sanitizer_drops_page_derived_negatives(self):
        """The homepage case: 'guided meditation' mined from the page itself.

        Instead of failing validation and burning retries, the sanitizer
        silently removes it, keeps legitimate wrong-intent negatives, and
        the assets then pass _check.
        """
        page_words = _extract_page_words(
            PageSnapshot(
                url="https://example.org/",
                title="Zen Mountain Journal — Art and Reflection",
                h1="Contemplative Art and Writing",
                meta_description="Guided meditation, Taoist poetry, inner quiet.",
                headings=["Guided Meditation and Stillness"],
                body_text=(
                    "Guided meditation and contemplative practice. "
                    "Reflections on Taoist poetry and wilderness solitude."
                ),
            )
        )
        assets = _assets(
            negative_keywords=["guided meditation", "energy drink", "power company"]
        )
        dropped = _sanitize_negatives(assets, page_words)
        self.assertEqual(dropped, ["guided meditation"])
        self.assertEqual(
            assets["negative_keywords"], ["energy drink", "power company"]
        )
        self.assertEqual(_check(assets, page_words=page_words), [])

    def test_sanitizer_drops_protected_product_and_campaign_repeats(self):
        assets = _assets(
            negative_keywords=["art prints", "free", "energy drink"]
        )
        dropped = _sanitize_negatives(assets)
        self.assertIn("art prints", dropped)
        self.assertIn("free", dropped)
        self.assertEqual(assets["negative_keywords"], ["energy drink"])
        self.assertEqual(_check(assets), [])

    def test_sanitizer_drops_positive_negative_overlap(self):
        assets = _assets(
            negative_keywords=["taoist inner energy", "energy drink"]
        )
        dropped = _sanitize_negatives(assets)
        self.assertEqual(dropped, ["taoist inner energy"])
        self.assertEqual(assets["negative_keywords"], ["energy drink"])
        self.assertEqual(_check(assets), [])

    def test_sanitizer_leaves_clean_negatives_alone(self):
        assets = _assets()
        self.assertEqual(_sanitize_negatives(assets), [])
        self.assertEqual(
            assets["negative_keywords"],
            ["energy drink", "energy healing", "power company"],
        )

    def test_rejects_retired_brand_name(self):
        failures = _check(
            _assets(
                descriptions=[
                    "A Taoist reflection on inner energy and silence.",
                    "Reflections on love in the Tao. Heron Dance Art Studio.",
                ]
            )
        )
        joined = " ".join(failures)
        self.assertIn("retired brand name", joined)
        self.assertIn("Zen Mountain Journal", joined)


if __name__ == "__main__":
    unittest.main()
