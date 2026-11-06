import unittest
from datetime import datetime, timezone

from adresearch.analyze import dedupe, summarize, top_phrases
from adresearch.apify import estimate_cost, search_url
from adresearch.normalize import hook_of, normalize

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def item(ad_id, page, start, body, active=True, end=None, collation=1, camel=False):
    if camel:
        return {
            "adArchiveID": ad_id, "pageID": page, "pageName": f"Page {page}",
            "startDate": start, "endDate": end, "isActive": active,
            "collationCount": collation,
            "snapshot": {"body": {"text": body}, "ctaText": "Learn more", "displayFormat": "video",
                         "videos": [{"video_preview_image_url": "https://img/v.jpg", "video_hd_url": "https://v.mp4"}]},
        }
    return {
        "ad_archive_id": ad_id, "page_id": page, "page_name": f"Page {page}",
        "start_date": start, "end_date": end, "is_active": active,
        "collation_count": collation, "publisher_platform": ["FACEBOOK", "INSTAGRAM"],
        "snapshot": {"body": {"text": body}, "title": "Check eligibility", "cta_text": "Apply Now",
                     "display_format": "IMAGE", "images": [{"original_image_url": "https://img/a.jpg"}]},
    }


class NormalizeTests(unittest.TestCase):
    def test_snake_case(self):
        a = normalize(item("1", "p1", ts(2026, 8, 1), "Turning 65? You may qualify. Call now."), NOW)
        self.assertEqual(a["days_running"], 98)
        self.assertEqual(a["hook"], "Turning 65? You may qualify.")
        self.assertEqual(a["cta"], "Apply Now")
        self.assertEqual(a["image"], "https://img/a.jpg")
        self.assertEqual(a["platforms"], ["FACEBOOK", "INSTAGRAM"])
        self.assertEqual(a["library_url"], "https://www.facebook.com/ads/library/?id=1")

    def test_camel_case_and_video(self):
        a = normalize(item("2", "p2", ts(2026, 11, 1) * 1000, "Hi", camel=True, collation=4), NOW)
        self.assertEqual(a["days_running"], 6)
        self.assertEqual(a["variations"], 4)
        self.assertEqual(a["format"], "VIDEO")
        self.assertEqual(a["video"], "https://v.mp4")
        self.assertEqual(a["image"], "https://img/v.jpg")

    def test_inactive_uses_end_date(self):
        a = normalize(item("3", "p", ts(2026, 1, 1), "x", active=False, end=ts(2026, 1, 31)), NOW)
        self.assertEqual(a["days_running"], 31)

    def test_strips_dynamic_template(self):
        a = normalize(item("4", "p", ts(2026, 10, 1), "{{product.brand}} Save on dental"), NOW)
        self.assertEqual(a["body"], "Save on dental")

    def test_missing_id(self):
        self.assertIsNone(normalize({"page_name": "x"}, NOW))

    def test_hook_truncates(self):
        self.assertTrue(hook_of("word " * 60).endswith("…"))


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        mk = lambda *a, **k: normalize(item(*a, **k), NOW)
        self.data = {
            "medicare advantage": [
                mk("1", "p1", ts(2026, 5, 1), "Turning 65? Get your $0 premium plan review today"),
                mk("2", "p1", ts(2026, 9, 1), "Turning 65? Get your $0 premium plan review today"),  # same creative
                mk("3", "p2", ts(2026, 10, 30), "New plan alert for seniors"),
            ],
            "turning 65": [
                mk("1", "p1", ts(2026, 5, 1), "Turning 65? Get your $0 premium plan review today"),  # same ad, other kw
                mk("4", "p3", ts(2026, 7, 1), "Seniors: $0 premium plans with dental, call for review today"),
                mk("5", "p4", ts(2026, 6, 1), "Your $0 premium plan review is waiting today"),
            ],
        }

    def test_dedupe_merges_ids_and_creatives(self):
        ads = {a["id"]: a for a in dedupe(self.data)}
        self.assertEqual(set(ads), {"1", "3", "4", "5"})
        self.assertEqual(ads["1"]["copies"], 2)
        self.assertEqual(ads["1"]["keywords"], ["medicare advantage", "turning 65"])

    def test_summary_ranking_and_diff(self):
        s = summarize(self.data, 30, previous_ids_proven={"1"}, previous_ids={"1", "4"})
        self.assertEqual([a["id"] for a in s["top_ads"]], ["1", "5", "4", "3"])
        self.assertEqual(s["stats"]["proven"], 3)
        self.assertEqual({a["id"] for a in s["newly_proven"]}, {"4", "5"})
        self.assertTrue(s["top_ads"][1]["new_since_last_run"])   # 5 is new
        self.assertFalse(s["top_ads"][2]["new_since_last_run"])  # 4 seen before
        self.assertEqual(s["advertisers"][0]["page_id"], "p1")
        self.assertIn("$0 premium", [p["phrase"] for p in s["phrases"]])

    def test_first_run_has_no_diff(self):
        s = summarize(self.data, 30)
        self.assertEqual(s["newly_proven"], [])
        self.assertFalse(any(a["new_since_last_run"] for a in s["top_ads"]))

    def test_phrases_skip_stopword_edges(self):
        ads = [{"body": "call the number today", "title": "", "page_id": str(i)} for i in range(3)]
        self.assertNotIn("call the", [p["phrase"] for p in top_phrases(ads)])

    def test_phrases_count_advertisers_not_ads(self):
        spam = [{"body": "big spender phrase", "title": "", "page_id": "one"}] * 10
        market = [{"body": "don’t overpay médico plan", "title": "", "page_id": str(i)} for i in range(3)]
        phrases = {p["phrase"]: p["advertisers"] for p in top_phrases(spam + market)}
        self.assertNotIn("big spender", phrases)
        self.assertEqual(phrases.get("don't overpay médico plan"), 3)


class ApifyTests(unittest.TestCase):
    def test_search_url(self):
        u = search_url("$0 premium", "US", True)
        self.assertIn("active_status=active", u)
        self.assertIn("q=%240+premium", u)
        self.assertIn("country=US", u)

    def test_cost(self):
        self.assertAlmostEqual(estimate_cost(19, 100), 1.425)


if __name__ == "__main__":
    unittest.main()
