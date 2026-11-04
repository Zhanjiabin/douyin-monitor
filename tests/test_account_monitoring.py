import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import store
import intelligence


class AccountMonitoringTests(unittest.TestCase):
    def setUp(self):
        if store._CONN is not None:
            store._CONN.close()
        store._CONN = None
        self.tmp = tempfile.TemporaryDirectory()
        store.DB_PATH = Path(self.tmp.name) / "test.sqlite3"
        store.init_db()
        intelligence.init_account_tables()

    def tearDown(self):
        if store._CONN is not None:
            store._CONN.close()
        store._CONN = None
        self.tmp.cleanup()

    def test_workbench_account_settings_are_atomic_and_preserve_works(self):
        uid="MS4wLjABAAAA_WORKBENCH_SETTINGS"
        intelligence.add_account(uid,"旧备注","own")
        intelligence.save_account_posts(uid,"测试账号",[{"aweme_id":"7703151458754472787","title":"已有作品","play_count":123}])
        intelligence.update_account_settings(uid,"新备注","peer",False)
        row=intelligence.list_tracked_accounts()[0]
        self.assertEqual((row['note'],row['role'],row['enabled']),("新备注","peer",0))
        self.assertEqual(len(intelligence.tracked_account_videos(uid)),1)
        for note,role,enabled in [('不可写入','invalid',True),('不可写入','own','false'),('x'*61,'own',True)]:
            with self.assertRaises(ValueError):intelligence.update_account_settings(uid,note,role,enabled)
            self.assertEqual(intelligence.list_tracked_accounts()[0]['note'],"新备注")
        with self.assertRaises(ValueError):intelligence.update_account_settings('missing','备注','peer',True)

    def test_account_profile_and_standalone_metric_boundary(self):
        sec_uid = "MS4wLjABAAAA_TEST_ACCOUNT"
        rows = intelligence.add_account(
            "https://www.douyin.com/user/" + sec_uid, "test account"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sec_uid"], sec_uid)

        posts = [{
            "aweme_id": "1111111111111111111",
            "title": "standalone work",
            "create_time": 1790000000,
            "mix_id": None,
            "mix_name": None,
            "play_count": 0,
            "digg_count": 17,
            "comment_count": 3,
            "share_count": 2,
            "collect_count": 4,
        }]
        intelligence.save_account_posts(sec_uid, "test creator", posts)
        discovered = intelligence.discovered_videos()
        self.assertEqual(len(discovered), 1)
        self.assertIsNone(discovered[0]["play_count"])
        self.assertEqual(discovered[0]["digg_count"], 17)
        self.assertEqual(discovered[0]["snapshot_status"], "not_exposed")

    def test_profile_metadata_and_legacy_warning_migration(self):
        uid="MS4wLjABAAAA_PROFILE_TEST"
        intelligence.add_account(uid, role="own")
        store.x("UPDATE tracked_accounts SET last_error=? WHERE sec_uid=?",("作品已发现；合集播放量补采失败：旧错误",uid))
        intelligence.init_account_tables()
        a=intelligence.list_tracked_accounts()[0]
        self.assertIsNone(a["last_error"])
        self.assertIn("旧错误",a["playback_warning"])
        with patch("media_cache.cache_images",return_value={"avatar":"/media/a.jpg","cover":"/media/c.jpg"}):
            intelligence.save_discovery(uid,{"author_name":"creator","profile":{"homepage_count":128,"follower_count":42,"avatar_url":"avatar"},"scan_status":"login_required","scan_pages":1,"has_more":True,"items":[{"aweme_id":"123","create_time":12345678,"cover_url":"cover"}]})
        a=intelligence.tracked_account_summary()[0]
        self.assertEqual((a["homepage_count"],a["video_count"],a["role"]),(128,1,"own"))
        self.assertEqual(a["avatar_path"],"/media/a.jpg")
        self.assertEqual(intelligence.tracked_account_videos()[0]["cover_path"],"/media/c.jpg")

    def test_mix_metrics_override_placeholder_and_history_is_queryable(self):
        sec_uid = "MS4wLjABAAAA_TEST_ACCOUNT"
        intelligence.add_account("https://www.douyin.com/user/" + sec_uid)
        aid = "2222222222222222222"
        intelligence.save_account_posts(sec_uid, "test creator", [{
            "aweme_id": aid, "title": "mix episode", "create_time": 1790000000,
            "mix_id": "mix-test", "mix_name": "test mix", "play_count": 0,
            "digg_count": 1, "comment_count": 1, "share_count": 1, "collect_count": 1,
        }])
        store.upsert_mix({"mix_id": "mix-test", "sec_uid": sec_uid,
                          "author_name": "test creator", "mix_name": "test mix"})
        store.save_video_snapshots("mix-test", [{
            "aweme_id": aid, "episode_no": 1, "title": "mix episode",
            "play_count": 12345, "digg_count": 20, "comment_count": 5,
            "share_count": 3, "collect_count": 7, "create_time": 1790000000,
        }])
        row = next(x for x in intelligence.discovered_videos() if x["aweme_id"] == aid)
        self.assertEqual(row["play_count"], 12345)
        self.assertEqual(row["snapshot_status"], "public_mix_metric")
        history = intelligence.video_history(aid)
        self.assertTrue(history)
        self.assertEqual(history[-1]["play_count"], 12345)

    def test_mix_scan_queue_only_reopens_for_newer_work(self):
        sec_uid = "MS4wLjABAAAA_TEST_ACCOUNT"
        mix_id = "mix-queue"
        self.assertTrue(intelligence.account_mix_needs_scan(sec_uid, mix_id, 100))
        intelligence.mark_account_mix_scanned(sec_uid, mix_id, 100)
        self.assertFalse(intelligence.account_mix_needs_scan(sec_uid, mix_id, 100))
        self.assertTrue(intelligence.account_mix_needs_scan(sec_uid, mix_id, 101))


if __name__ == "__main__":
    unittest.main()
