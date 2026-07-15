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





class RetryAndTruncationTests(unittest.TestCase):
    def test_sanitizer_truncates_negatives_to_ten(self):
        from src.google_ads import _sanitize_negatives
        twelve = [f"wrong intent {i}" for i in range(12)]
        assets = _assets(negative_keywords=list(twelve))
        dropped = _sanitize_negatives(assets)
        self.assertEqual(len(assets["negative_keywords"]), 10)
        self.assertEqual(assets["negative_keywords"], twelve[:10])
        self.assertIn("wrong intent 10", dropped)
        self.assertIn("wrong intent 11", dropped)
        self.assertEqual(_check(assets), [])

    def test_retry_coaching_targets_each_failure_type(self):
        from src.google_ads import _retry_coaching
        text = _retry_coaching([
            "Headline 1 is 32 chars (max 30): 'Zen Journaling as Honest Witness'",
            "Description 2 is 95 chars (max 90): '...'",
            "Contains banned phrase: 'awaken'",
        ])
        self.assertIn("AT MOST 25 characters", text)
        self.assertIn("AT MOST 80 characters", text)
        self.assertIn("awaken", text)
        self.assertIn("ANY form or conjugation", text)

    def test_retry_coaching_empty_for_other_failures(self):
        from src.google_ads import _retry_coaching
        self.assertEqual(_retry_coaching(["Near-duplicate headlines: a / b"]), "")


class SalvageTests(unittest.TestCase):
    """The 07/15 batch cases: one bad line among enough good ones should be
    dropped, not fail the whole page after five retries."""

    def test_drops_overlength_description_when_minimum_remains(self):
        from src.google_ads import _salvage_assets, _check
        long_d = (
            "Essays on balance and harmony within unpredictability. "
            "From a contemplative artist-painter."  # 91 chars, the balance-harmony case
        )
        assets = _assets(
            descriptions=[
                "A Taoist reflection on inner energy and silence.",
                "For readers drawn to Zen practice and quiet attention.",
                long_d,
            ]
        )
        dropped = _salvage_assets(assets)
        self.assertIn(long_d, dropped)
        self.assertEqual(len(assets["descriptions"]), 2)
        self.assertEqual(_check(assets), [])

    def test_keeps_originals_when_dropping_would_break_minimum(self):
        from src.google_ads import _salvage_assets, _check
        long_d = "x" * 95
        assets = _assets(
            descriptions=["A Taoist reflection on inner energy.", long_d]
        )
        dropped = _salvage_assets(assets)
        self.assertEqual(dropped, [])
        self.assertEqual(len(assets["descriptions"]), 2)
        # _check must still flag it so the retry loop fires.
        self.assertTrue(any("(max 90)" in f for f in _check(assets)))

    def test_drops_ungrounded_and_overlength_headlines(self):
        from src.google_ads import _salvage_assets, _check
        ungrounded = "Morning Light and Birdsong"        # no Zen/Tao anchor
        overlength = "Zen Poetry Paintings, Asheville"    # 31 chars, asheville case
        assets = _assets(
            headlines=[
                "Taoist Inner Energy",
                "Zen Silence Practice",
                "Han Shan and Zen",
                ungrounded,
                overlength,
            ]
        )
        dropped = _salvage_assets(assets)
        self.assertIn(ungrounded, dropped)
        self.assertIn(overlength, dropped)
        self.assertEqual(len(assets["headlines"]), 3)
        self.assertEqual(_check(assets), [])

    def test_drops_banned_word_description(self):
        from src.google_ads import _salvage_assets, _check
        banned_d = "The Tao Te Ching Journal is available for pre-order."
        assets = _assets(
            descriptions=[
                "A Taoist reflection on inner energy and silence.",
                "For readers drawn to Zen practice and quiet attention.",
                banned_d,
            ]
        )
        dropped = _salvage_assets(assets)
        self.assertIn(banned_d, dropped)
        self.assertEqual(_check(assets), [])

    def test_drops_near_duplicate_headline_keeps_first(self):
        from src.google_ads import _salvage_assets, _check
        assets = _assets(
            headlines=[
                "Tao of Suffering and Desire",
                "Suffering and Desire of Tao",
                "Zen Silence Practice",
                "Han Shan and Zen",
            ]
        )
        dropped = _salvage_assets(assets)
        self.assertEqual(dropped, ["Suffering and Desire of Tao"])
        self.assertEqual(assets["headlines"][0], "Tao of Suffering and Desire")
        self.assertEqual(_check(assets), [])

    def test_clean_assets_untouched(self):
        from src.google_ads import _salvage_assets
        assets = _assets()
        self.assertEqual(_salvage_assets(assets), [])
        self.assertEqual(len(assets["headlines"]), 5)
        self.assertEqual(len(assets["descriptions"]), 3)


if __name__ == "__main__":
    unittest.main()
