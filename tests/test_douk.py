import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import douk
import insights
import intelligence
import store


class DoukIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = store.DB_PATH
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = Path(self.tmp.name) / "test.db"
        store.init_db()
        douk.init_tables()
        self.aid = "7703151458754472787"
        self.uid = "MS4wLjABAAAA_TEST"

    def tearDown(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def raw(self, play=0):
        return {"aweme_id": self.aid, "desc": "测试标题", "create_time": 1780000000,
                "author": {"sec_uid": self.uid, "nickname": "测试"}, "video": {},
                "statistics": {"play_count": play, "digg_count": 100}}

    def test_zero_missing_and_wrong_types_never_become_valid_playback(self):
        for value in [0, None, "", "unknown", -1, True]:
            self.assertIsNone(douk.normalize(self.raw(value))["play_count"])
        self.assertEqual(douk.normalize(self.raw("1930000"))["play_count"], 1930000)
        self.assertIsNone(douk.normalize({"cid": self.aid, "text": "评论"}))

    def test_external_source_is_queryable_without_overwriting_known_mix(self):
        intelligence.save_account_posts(self.uid, "测试", [{"aweme_id": self.aid, "title": "测试", "play_count": None}])
        store.upsert_mix({"mix_id": "mix", "sec_uid": self.uid, "mix_name": "合集"})
        store.save_video_snapshots("mix", [{"aweme_id": self.aid, "title": "测试", "play_count": 200}])
        result = douk.import_rows([self.raw(0)], known_only=True)
        self.assertEqual(result, {"imported": 1, "playback": 0})
        video = insights.dashboard()["videos"][0]
        self.assertEqual(video["play_count"], 200)
        self.assertEqual(video["play_source"], "mix")
        self.assertEqual(store.q("SELECT play_count FROM video_snapshots")[0]["play_count"], 200)
        self.assertTrue(any(r["metric_status"] == "douk_detail" for r in intelligence.video_history(self.aid)))

    def test_known_only_and_tiktok_cannot_pollute_douyin_watchlist(self):
        self.assertEqual(douk.import_rows([self.raw(500)], known_only=True)["imported"], 0)
        self.assertEqual(douk.import_rows([self.raw(500)], platform="tiktok")["imported"], 0)
        self.assertFalse(store.q("SELECT * FROM tracked_account_videos"))

    def test_positive_external_playback_survives_later_missing_response(self):
        douk.import_rows([self.raw(1234)])
        douk.import_rows([self.raw(0)])
        video = insights.dashboard()["videos"][0]
        self.assertEqual(video["play_count"], 1234)
        self.assertEqual(video["play_source"], "douk_detail")
        self.assertIsNone(video["daily_growth"])

    def test_secrets_are_not_exposed_in_result_export(self):
        result = douk.clean({"cookie": "secret", "data": [{"token": "secret", "value": 1}],
                             "error": "msToken=secret&x=1"})
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["data"], [{"value": 1}])

    def test_file_path_stays_in_download_directory(self):
        with patch.object(douk, "OUTPUT_DIR", Path(self.tmp.name)):
            (Path(self.tmp.name) / "outside.txt").write_text("private")
            with self.assertRaises(ValueError):
                douk.file_path("../outside.txt")

    def test_exports_escape_spreadsheet_formulas(self):
        with patch.object(douk, "job", return_value={"records": [{"title": "=HYPERLINK(\"x\")"}], "result": {}}):
            content, _ = douk.export("test", "csv")
            self.assertIn("'=HYPERLINK", content.decode("utf-8-sig"))

    def test_feature_catalog_contains_all_routes_and_correct_tiktok_live_schema(self):
        cat = douk.catalog()
        paths = cat["openapi"]["paths"]
        self.assertEqual(len([p for p in paths if p.startswith(("/douyin/", "/tiktok/"))]), 16)
        self.assertEqual(len(cat["extra"]), 10)
        ref = paths["/tiktok/live"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        self.assertEqual(ref, "#/components/schemas/LiveTikTok")


if __name__ == "__main__":
    unittest.main()
