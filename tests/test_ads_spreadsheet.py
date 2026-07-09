from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src import ads_spreadsheet


def _entry(**overrides):
    result = {
        "url": "http://www.herondance.org/taoist-inner-energy-silence",
        "ads_final_url": "http://www.herondance.org/taoist-inner-energy-silence",
        "flagged": False,
        "flag_reason": "",
        "headlines": [
            "Taoist Inner Energy",
            "Zen and Vital Spirit",
            "Taoist Silence Practice",
            "Tao Te Ching on Energy",
            "Zen Mountain Stillness",
        ],
        "descriptions": [
            "A Taoist reflection on conserving inner energy and cultivating silence.",
            "For readers drawn to Zen practice and the quiet nourishment of vital spirit.",
            "Notes on Taoist hermits, inner energy, and contemplative practice.",
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


class AdsSpreadsheetTests(unittest.TestCase):
    def test_creates_editor_rows_with_expected_statuses_and_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ads_spreadsheet, "OUTPUT_DIR", Path(tmpdir)):
                path = ads_spreadsheet.create_ads_editor_csv(
                    [_entry()],
                    datetime(2026, 7, 7, 9, 30, 0),
                    campaign_budget="10.0",
                    campaign_location="United States",
                )

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(3), b"\xef\xbb\xbf")

            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(Path(path).name, "Google Ads Editor Upload 20260707_093000.csv")
        self.assertIn("Ad type", rows[0])
        self.assertIn("Criterion Type", rows[0])
        self.assertNotIn("Type", rows[0])
        self.assertIn("Headline 12", rows[0])
        self.assertNotIn("Headline 13", rows[0])

        self.assertEqual(rows[0]["Campaign"], "Heron Dance - Zen and Tao")
        self.assertEqual(rows[0]["Campaign Type"], "Search")
        self.assertEqual(rows[0]["Networks"], "Google search")
        self.assertEqual(rows[0]["Budget"], "10.0")
        self.assertEqual(rows[0]["Budget type"], "Daily")
        self.assertEqual(rows[0]["Bid Strategy Type"], "Manual CPC")
        self.assertEqual(rows[0]["Campaign Status"], "Paused")
        self.assertEqual(rows[0]["Location"], "United States")

        ad_group = rows[1]
        self.assertEqual(ad_group["Campaign"], "Heron Dance - Zen and Tao")
        self.assertEqual(ad_group["Ad Group"], "taoist-inner-energy-silence")
        self.assertEqual(ad_group["Ad Group Status"], "Paused")
        self.assertEqual(ad_group["Max CPC"], "0.20")
        self.assertEqual(ad_group["Status"], "")

        rsa = rows[2]
        self.assertEqual(rsa["Ad type"], "Responsive search ad")
        self.assertEqual(rsa["Status"], "Paused")
        self.assertEqual(rsa["Final URL"], "https://www.herondance.org/taoist-inner-energy-silence")
        self.assertIn("utm_campaign=heron_dance_zen_and_tao", rsa["Final URL suffix"])
        self.assertEqual(rsa["Headline 1"], "Taoist Inner Energy")
        self.assertEqual(rsa["Headline 12"], "")

        positive_rows = [row for row in rows if row["Criterion Type"] == "Phrase"]
        negative_rows = [row for row in rows if row["Criterion Type"] == "Negative phrase"]
        self.assertEqual(len(positive_rows), 7)
        self.assertEqual(len(negative_rows), 3)
        self.assertTrue(all(row["Status"] == "Paused" for row in positive_rows))
        self.assertTrue(all(row["Max CPC"] == "0.20" for row in positive_rows))
        self.assertTrue(all(row["Final URL"] == "" for row in positive_rows))
        self.assertTrue(all(row["Final URL suffix"] == "" for row in positive_rows))
        self.assertTrue(all(row["Status"] == "" for row in negative_rows))
        self.assertTrue(all(row["Final URL"] == "" for row in negative_rows))
        self.assertTrue(all(row["Max CPC"] == "" for row in negative_rows))

    def test_rejects_flagged_entries(self):
        with self.assertRaisesRegex(ValueError, "banned phrase"):
            ads_spreadsheet.validate_ads_entry(
                _entry(flagged=True, flag_reason="Contains banned phrase")
            )

    def test_rejects_ad_group_collisions(self):
        second = _entry(
            url="https://example.com/taoist-inner-energy-silence",
            ads_final_url="https://example.com/taoist-inner-energy-silence",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ads_spreadsheet, "OUTPUT_DIR", Path(tmpdir)):
                with self.assertRaisesRegex(ValueError, "maps to multiple final URLs"):
                    ads_spreadsheet.validate_ads_batch([_entry(), second])

    def test_rejects_positive_negative_overlap(self):
        with self.assertRaisesRegex(ValueError, "both positive and negative"):
            ads_spreadsheet.validate_ads_entry(
                _entry(negative_keywords=["taoist inner energy", "energy drink", "battery"])
            )

    def test_dedupes_word_order_and_contained_phrase_keywords(self):
        entry = _entry(
            core_keywords=[
                "[Tao Te Ching simplicity]",
                "[harmony with the Tao]",
                "[zen silence]",
            ],
            keyword_variants={
                "[Tao Te Ching simplicity]": [
                    "[simplicity Tao Te Ching]",
                    "[Tao Te Ching simple life]",
                ],
                "[harmony with the Tao]": [
                    "[living in harmony with the Tao]",
                    "[Tao harmony]",
                ],
                "[zen silence]": ["[silence in zen]", "[zen silence practice]"],
            },
        )
        keywords = ads_spreadsheet._positive_keywords(entry)

        self.assertIn("Tao Te Ching simplicity", keywords)
        self.assertNotIn("simplicity Tao Te Ching", keywords)
        self.assertIn("harmony with the Tao", keywords)
        self.assertNotIn("living in harmony with the Tao", keywords)

    def test_dedupes_stopword_variants_and_prefers_shorter_contained_phrase(self):
        entry = _entry(
            core_keywords=[
                "[wu wei in daily life]",
                "[Zen poetry on stillness]",
                "[Tao Te Ching stillness]",
                "[Taoist stillness practice]",
            ],
            keyword_variants={
                "[wu wei in daily life]": [
                    "[wu wei daily life]",
                    "[wu wei everyday life]",
                ],
                "[Zen poetry on stillness]": [
                    "[Zen stillness poetry]",
                    "[Zen quiet stillness]",
                ],
                "[Tao Te Ching stillness]": [
                    "[stillness in Tao Te Ching]",
                    "[Taoist quiet mind]",
                ],
                "[Taoist stillness practice]": [
                    "[Taoist stillness]",
                    "[Taoism stillness]",
                ],
            },
        )
        keywords = ads_spreadsheet._positive_keywords(entry)

        self.assertIn("wu wei in daily life", keywords)
        self.assertNotIn("wu wei daily life", keywords)
        self.assertIn("Zen poetry on stillness", keywords)
        self.assertNotIn("Zen stillness poetry", keywords)
        self.assertIn("Tao Te Ching stillness", keywords)
        self.assertNotIn("stillness in Tao Te Ching", keywords)
        self.assertNotIn("Taoist stillness practice", keywords)
        self.assertIn("Taoist stillness", keywords)

    def test_omits_cross_ad_group_duplicate_keywords(self):
        second = _entry(
            url="https://www.herondance.org/second-page",
            ads_final_url="https://www.herondance.org/second-page",
            core_keywords=["[taoist inner energy]", "[zen poetry]", "[taoist poems]"],
            keyword_variants={
                "[taoist inner energy]": ["[inner energy taoism]", "[taoist energy practice]"],
                "[zen poetry]": ["[zen poems]", "[poetry zen]"],
                "[taoist poems]": ["[tao poems]", "[poems taoist]"],
            },
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ads_spreadsheet, "OUTPUT_DIR", Path(tmpdir)):
                path = ads_spreadsheet.create_ads_editor_csv(
                    [_entry(), second], datetime(2026, 7, 7, 9, 30, 0)
                )

            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        duplicate_rows = [
            row for row in rows
            if row["Criterion Type"] == "Phrase"
            and row["Keyword"].casefold() == "taoist inner energy"
        ]
        self.assertEqual(len(duplicate_rows), 1)

    def test_keyword_max_cpc_override(self):
        entry = _entry(keyword_max_cpc={"taoist inner energy": "0.35"})
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ads_spreadsheet, "OUTPUT_DIR", Path(tmpdir)):
                path = ads_spreadsheet.create_ads_editor_csv(
                    [entry], datetime(2026, 7, 7, 9, 30, 0)
                )

            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        keyword_rows = {
            row["Keyword"]: row for row in rows if row["Criterion Type"] == "Phrase"
        }
        self.assertEqual(keyword_rows["taoist inner energy"]["Max CPC"], "0.35")
        self.assertEqual(keyword_rows["inner energy taoism"]["Max CPC"], "0.20")


if __name__ == "__main__":
    unittest.main()
