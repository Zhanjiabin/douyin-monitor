import json
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import corpus
import douk
import intelligence
import playback
import store
import tikhub
import workbench


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = store.DB_PATH, playback.SETTINGS, douk.JOBS
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = Path(self.temp.name) / "test.db"
        playback.SETTINGS = Path(self.temp.name) / "settings.json"
        douk.JOBS = Path(self.temp.name) / "jobs"
        store.init_db()
        douk.init_tables()
        corpus.init()
        self.aid = "7703151458754472787"
        self.uid = "MS4wLjABAAAA_WORKBENCH_TEST"
        intelligence.add_account(self.uid, "测试账号", "own")
        intelligence.save_account_posts(self.uid, "测试账号", [{"aweme_id": self.aid, "title": "测试作品 #短剧"}])

    def tearDown(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS = self.old
        self.temp.cleanup()

    def configured(self, **kw):
        playback.save_settings({"api_key": "private-workbench-test-key", **kw})

    def test_primary_workflows_plan_without_network_or_cookies(self):
        self.configured()
        with patch.object(tikhub.requests, "request") as transport:
            for action in ("video", "playback", "comments"):
                p = workbench.prepare({"action": action, "input": self.aid})
                self.assertTrue(p["allowed"])
                self.assertEqual(p["calls"], 1)
                self.assertTrue(p["request"]["import_results"])
            p = workbench.plan({"action": "account", "account": self.uid})
            self.assertEqual((p["units"], p["calls"]), (1, 1))
            self.assertNotIn("request", p)
            self.assertNotIn("private-workbench-test-key", json.dumps(p))
        transport.assert_not_called()

    def test_changed_input_or_missing_confirmation_cannot_queue_paid_job(self):
        self.configured()
        data = {"action": "playback", "input": self.aid}
        p = workbench.plan(data)
        with patch.object(douk, "submit") as submit:
            with self.assertRaises(ValueError):
                workbench.submit({**data, "fingerprint": p["fingerprint"]})
            with self.assertRaises(ValueError):
                workbench.submit({**data, "input": "7703151458754472788", "confirmed": True, "fingerprint": p["fingerprint"]})
        submit.assert_not_called()

    def test_submission_reuses_tikhub_queue_and_limits(self):
        self.configured(max_calls_per_job=1)
        data = {"action": "comments", "input": self.aid}
        p = workbench.plan(data)
        with patch.object(douk, "submit", return_value={"id": "queued"}) as submit:
            self.assertEqual(workbench.submit({**data, "confirmed": True, "fingerprint": p["fingerprint"]})["id"], "queued")
        request = submit.call_args.args[0]
        self.assertEqual(request["endpoint"], "tikhub_api")
        self.assertTrue(request["allow_charges"])
        self.assertFalse(workbench.plan({**data, "pages": 2})["allowed"])

    def test_missing_only_never_refreshes_known_plays_without_selection(self):
        self.configured()
        rows = [{"aweme_id": self.aid, "source": "tikhub_app", "scope": "organic", "play_count": 123,
                 "digg_count": None, "comment_count": None, "collect_count": None, "share_count": None, "request_id": "test"}]
        playback.persist(rows)
        with self.assertRaises(ValueError):
            workbench.plan({"action": "playback", "missing_only": True})
        self.assertEqual(workbench.plan({"action": "playback", "missing_only": False})["units"], 1)

    def test_download_uses_native_parser_without_vendor_api_key(self):
        p = workbench.prepare({"action": "download", "input": self.aid})
        self.assertFalse(p["paid"])
        self.assertEqual(p["request"]["endpoint"], "mcpro")
        self.assertTrue(p["request"]["download"])

    def test_research_reads_real_caption_comments_history_and_assets_only(self):
        store.x("INSERT INTO content_comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("dy", "cid", self.aid, "", "好看", "2026-11-04", 3, "", "读者", "test", "", "", "{}"))
        with patch.object(workbench.integrations, "assets", return_value={"items": [{"name": self.aid + ".mp4", "url": "/local-file", "size": 2}]}):
            detail = workbench.research(self.aid)
        self.assertIn("测试作品", detail["description"])
        self.assertEqual(detail["comments"][0]["content"], "好看")
        self.assertEqual(len(detail["files"]), 1)
        with self.assertRaises(ValueError):
            workbench.research("../../settings.json")

    def test_account_cards_and_details_keep_old_local_works(self):
        old_date = (date.fromisoformat(store.today_str()) - timedelta(days=120)).isoformat()
        store.x("UPDATE account_video_snapshots SET fetch_date=?,play_count=123 WHERE aweme_id=?", (old_date, self.aid))
        self.assertIn(self.aid, {v["aweme_id"] for v in workbench.state()["videos"]})
        with patch.object(workbench.integrations, "assets", return_value={"items": []}):
            detail = workbench.research(self.aid)
        self.assertEqual(detail["video"]["aweme_id"], self.aid)
        self.assertTrue(detail["video"]["stale"])


if __name__ == "__main__":
    unittest.main()
