from __future__ import annotations

import unittest

from src.google_ads import _check


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


if __name__ == "__main__":
    unittest.main()
