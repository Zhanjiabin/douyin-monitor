import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import insights
import intelligence
import store
from scheduler import Scheduler


class InsightsTests(unittest.TestCase):
    def setUp(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = store.DB_PATH
        store.DB_PATH = Path(self.tmp.name) / "test.db"
        self.today = date.fromisoformat(store.today_str())
        self.uid = "MS4wLjABAAAA_TEST_INSIGHTS"
        intelligence.add_account(self.uid, "Own", "own")
        store.upsert_mix({"mix_id": "mix", "sec_uid": self.uid, "author_name": "Own", "mix_name": "重生"})

    def tearDown(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def snapshot(self, day, value, aid="1"):
        created = datetime.combine(self.today - timedelta(days=5), datetime.min.time(), timezone.utc).timestamp()
        store.save_video_snapshots("mix", [{"aweme_id": aid, "play_count": value,
            "title": "没想到重生后竟然变成千金", "create_time": int(created)}],
            (self.today - timedelta(days=day)).isoformat())

    def test_first_snapshot_has_no_growth_or_title_claim(self):
        self.snapshot(0, 100)
        data = insights.dashboard()
        self.assertIsNone(data["videos"][0]["daily_growth"])
        self.assertTrue(all(p["own"] is None for p in data["trend"]))
        self.assertTrue(all(f["ratio"] is None for f in data["features"]))

    def test_keyword_search_updates_metrics_topics_and_samples_together(self):
        intelligence.save_account_posts(self.uid, "Own", [
            {"aweme_id": "7703151458754472787", "title": "末日咖啡店", "play_count": 200},
            {"aweme_id": "7703151458754472788", "title": "重生复仇", "play_count": 300}])
        data = insights.dashboard(query="咖啡")
        self.assertEqual([v["aweme_id"] for v in data["videos"]], ["7703151458754472787"])
        self.assertEqual((data["stats"]["videos"], data["stats"]["play"]), (1, 200))
        self.assertEqual(sum(t["total"] for t in data["topics"]), 1)
        self.assertEqual(insights.dashboard(query="7703151458754472788")["stats"]["play"], 300)
        self.assertEqual(insights.dashboard(query="own")["stats"]["videos"], 2)

    def test_missing_playback_reason_requires_recorded_evidence(self):
        self.snapshot(0,None)
        self.assertEqual(insights.dashboard()["videos"][0]["play_status"],"pending")
        intelligence.save_playback_check("mix","unavailable","unverified series")
        self.assertEqual(insights.dashboard()["videos"][0]["play_status"],"unavailable")
        intelligence.save_playback_check("mix","failed","timeout")
        self.assertEqual(insights.dashboard()["videos"][0]["play_status"],"failed")
        self.snapshot(0,123)
        self.assertEqual(insights.dashboard()["videos"][0]["play_status"],"available")

    def test_nonconsecutive_dates_use_actual_days_and_chart_gap(self):
        self.snapshot(3, 100)
        self.snapshot(0, 400)
        data = insights.dashboard()
        self.assertEqual(data["videos"][0]["daily_growth"], 100)
        self.assertEqual(data["videos"][0]["span_days"], 3)
        self.assertTrue(all(p["own"] is None for p in data["trend"]))

    def test_unknown_and_declining_counts_do_not_become_zero(self):
        self.snapshot(1, 200)
        self.snapshot(0, 100)
        self.snapshot(0, None, "2")
        data = insights.dashboard()
        self.assertIsNone(data["stats"]["median_growth"])
        self.assertEqual(data["stats"]["missing"], 1)
        self.assertEqual(data["stats"]["declines"], 1)
        self.assertEqual(data["stats"]["play"], 100)

    def test_account_observation_does_not_refresh_old_play_date(self):
        self.snapshot(4, 300)
        intelligence.save_account_posts(self.uid, "Own", [{"aweme_id": "1", "title": "new title", "play_count": 0}])
        data = insights.dashboard()
        video = data["videos"][0]
        self.assertEqual(video["play_count"], 300)
        self.assertEqual(video["play_date"], (self.today - timedelta(days=4)).isoformat())
        self.assertTrue(video["stale"])
        self.assertIsNone(data["stats"]["play"])

    def test_roles_persist_on_scan_and_filter(self):
        intelligence.upsert_account_identity(self.uid, "Own renamed")
        self.snapshot(0, 100)
        self.assertEqual(insights.dashboard(role="own")["stats"]["videos"], 1)
        self.assertEqual(insights.dashboard(role="peer")["stats"]["videos"], 0)
        with self.assertRaises(ValueError):
            intelligence.add_account("MS4wLjABAAAA_OTHER_TEST", "bad", "invalid")
        self.assertEqual(len(intelligence.list_tracked_accounts()), 1)
        with self.assertRaises(ValueError):
            intelligence.add_account("https://evil.example/user/" + self.uid)

    def test_daily_refresh_even_without_new_posts(self):
        intelligence.mark_account_mix_scanned(self.uid, "mix", 10)
        self.assertFalse(intelligence.account_mix_needs_scan(self.uid, "mix", 10))
        with patch.object(store, "today_str", return_value=(self.today + timedelta(days=1)).isoformat()):
            self.assertTrue(intelligence.account_mix_needs_scan(self.uid, "mix", 10))

    def test_account_only_scheduler_catch_up(self):
        scheduler = Scheduler(lambda: True)
        scheduler._times = ["00:00"]
        self.assertTrue(scheduler._should_catch_up(datetime.now()))

    def test_same_day_deduplication_and_title_features(self):
        intelligence.save_account_posts(self.uid, "Own", [{"aweme_id": "1", "play_count": 20}])
        self.snapshot(0, 100)
        result = insights.dashboard()
        self.assertEqual(result["stats"]["play"], 100)
        self.assertEqual(result["stats"]["videos"], 1)
        self.assertEqual(insights.title_features("第12集 #总裁 #逆袭"), [])

    def test_source_change_does_not_infer_growth(self):
        with patch.object(store, "today_str", return_value=(self.today - timedelta(days=1)).isoformat()):
            intelligence.save_account_posts(self.uid, "Own", [{"aweme_id": "1", "play_count": 20}])
        self.snapshot(0, 100)
        self.assertIsNone(insights.dashboard()["videos"][0]["daily_growth"])


if __name__ == "__main__":
    unittest.main()
