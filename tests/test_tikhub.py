import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import corpus
import douk
import intelligence
import playback
import store
import tikhub


class TikHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_settings, self.old_jobs = store.DB_PATH, playback.SETTINGS, douk.JOBS
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = Path(self.tmp.name) / "test.db"
        playback.SETTINGS = Path(self.tmp.name) / "settings.json"
        douk.JOBS = Path(self.tmp.name) / "jobs"
        store.init_db()
        douk.init_tables()
        corpus.init()
        playback.init()
        self.aid = "7703151458754472787"

    def tearDown(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS = self.old_db, self.old_settings, self.old_jobs
        self.tmp.cleanup()

    def op(self, action="fetch_video_statistics", prefix=tikhub.APP):
        return next(o for o in tikhub.operations().values() if o["path"] == prefix + action)

    def request(self, action="fetch_video_statistics", params=None, **kw):
        return {"operation_id": self.op(action)["operationId"],
                "params": {"aweme_ids": self.aid} if params is None else params, **kw}

    def configured(self, **kw):
        playback.save_settings({"api_key": "private-workbench-unit-key", **kw})

    def confirmed(self, request):
        return {**request, "allow_charges": True, "fingerprint": tikhub.plan(request)["fingerprint"]}

    @staticmethod
    def response(body, status=200, headers=None):
        return Mock(status_code=status, json=Mock(return_value=body), text="response", headers=headers or {})

    def test_catalog_and_all_operations_resolve_without_transport(self):
        with patch.object(tikhub.requests, "request") as call:
            cat = tikhub.catalog()
            self.assertEqual((cat["total"], len(cat["groups"])), (1058, 51))
            self.assertEqual(sum(o["group"] == "Douyin-App-V3-API" for o in cat["operations"]), 38)
            for op in cat["operations"]:
                detail = tikhub.detail(op["id"])
                self.assertEqual(detail["method"], op["method"])
                for parameter in detail["parameters"]:
                    tikhub.Draft202012Validator.check_schema(parameter["schema"])
                if detail["has_body"]:
                    tikhub.Draft202012Validator.check_schema(detail["body_schema"])
        call.assert_not_called()

    def test_preview_missing_key_is_offline(self):
        with patch.object(tikhub.requests, "request") as call:
            p = tikhub.plan(self.request())
            self.assertFalse(p["allowed"])
            self.assertIn("API Key", p["reason"])
            self.assertEqual(p["calls"], 1)
            with self.assertRaises(ValueError):
                tikhub.execute(self.confirmed(self.request()))
        call.assert_not_called()
        self.assertEqual(tikhub.usage(), 0)

    def test_confirmation_and_changed_params_block_transport(self):
        self.configured()
        request = self.confirmed(self.request())
        with patch.object(tikhub.requests, "request") as call:
            with self.assertRaises(ValueError):
                tikhub.execute(self.request())
            request["params"]["aweme_ids"] = "7703151458754472788"
            with self.assertRaises(ValueError):
                tikhub.execute(request)
        call.assert_not_called()

    def test_unknown_route_parameter_body_and_bad_types_block(self):
        bad = [self.request(operation_id="https://example.com"), self.request(params={"aweme_ids": self.aid, "unknown": 3}),
               self.request(body_json="{}"), self.request("fetch_hashtag_detail", {"ch_id": "1.2"}),
               self.request(params={"aweme_ids": ",".join([str(int(self.aid) + i) for i in range(3)])})]
        for request in bad:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    tikhub.compile_request(request)
        with self.assertRaises(ValueError):
            tikhub.loads("[NaN]", "请求体")

    def test_top_level_array_and_referenced_body_validation(self):
        request = self.request("fetch_multi_video", {}, body_json=json.dumps([self.aid]))
        self.assertEqual(tikhub.compile_request(request)[1][0]["body"], [self.aid])
        request["body_json"] = "{}"
        with self.assertRaises(ValueError):
            tikhub.compile_request(request)
        high = self.request("fetch_multi_video_high_quality_play_url", {}, body_json=json.dumps({"aweme_ids": self.aid, "region": None}))
        self.assertIsNone(tikhub.compile_request(high)[1][0]["body"]["region"])
        high["body_json"] = '{"aweme_ids":99}'
        with self.assertRaises(ValueError):
            tikhub.compile_request(high)

    def test_64_bit_integer_id_and_batch_json_preserve_precision(self):
        request = self.request("fetch_hashtag_detail", {"ch_id": self.aid})
        item = tikhub.compile_request(request)[1][0]
        self.assertEqual(item["params"]["ch_id"], int(self.aid))
        request["items_json"] = '[{"params":{"ch_id":7703151458754472787}}]'
        self.assertEqual(tikhub.compile_request(request)[1][0]["params"]["ch_id"], int(self.aid))
        public = tikhub.public_result({"receipts": [{"response": {"id": int(self.aid)}}]})
        self.assertEqual(public["receipts"][0]["response"]["id"], self.aid)
        self.assertIn(self.aid, public["response_json"])

    def test_get_and_post_use_only_pinned_host_without_redirects(self):
        self.configured()
        with patch.object(tikhub.requests, "request", return_value=self.response({"code": 200, "data": []})) as call:
            tikhub.execute(self.confirmed(self.request()))
            args, kwargs = call.call_args
            self.assertEqual(args, ("GET", playback.BASE + self.op()["path"]))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["params"], [("aweme_ids", self.aid)])
            request = self.request("fetch_multi_video", {}, body_json=json.dumps([self.aid]))
            tikhub.execute(self.confirmed(request))
            args, kwargs = call.call_args
            self.assertEqual(args[0], "POST")
            self.assertEqual(kwargs["json"], [self.aid])

    def test_shared_budget_and_batch_upper_bound(self):
        self.configured(daily_call_limit=2, max_calls_per_job=2)
        store.x("INSERT INTO playback_requests(created_at,source,mode,video_ids,status) VALUES(?,?,?,?,?)", (store.now_str(), "tikhub", "app", "[]", "completed"))
        request = self.request(items_json=json.dumps([{"params": {"aweme_ids": self.aid}}] * 2))
        self.assertFalse(tikhub.plan(request)["allowed"])
        request.pop("items_json")
        with patch.object(tikhub.requests, "request", return_value=self.response({"data": []})):
            tikhub.execute(self.confirmed(request))
        self.assertFalse(playback.plan("app", [self.aid])["allowed"])
        self.assertEqual(tikhub.usage(), 2)

    def test_failure_is_recorded_and_never_retried(self):
        self.configured()
        request = self.request(items_json=json.dumps([{"params": {"aweme_ids": self.aid}}] * 2))
        with patch.object(tikhub.requests, "request", side_effect=tikhub.requests.Timeout("private-workbench-unit-key")) as call:
            result = tikhub.execute(self.confirmed(request))
        self.assertEqual(call.call_count, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(tikhub.usage(), 1)
        self.assertNotIn("private-workbench-unit-key", json.dumps(result))
        self.assertEqual(store.q("SELECT status FROM playback_requests")[0]["status"], "failed")

    def test_http_and_business_errors_keep_response_receipt(self):
        self.configured()
        for body, status in [({"detail": "no credit"}, 402), ({"code": 400, "message": "bad"}, 200)]:
            with patch.object(tikhub.requests, "request", return_value=self.response(body, status)):
                result = tikhub.execute(self.confirmed(self.request()))
            self.assertFalse(result["ok"])
            self.assertEqual(result["receipts"][0]["response"], body)

    def test_unauthenticated_health_omits_authorization_and_redirect_is_not_followed(self):
        op = self.op("check", "/api/v1/health/")
        request = {"operation_id": op["operationId"], "params": {}}
        self.assertTrue(tikhub.plan(request)["allowed"])
        response = self.response({}, 302, {"Location": "https://example.com/download"})
        with patch.object(tikhub.requests, "request", return_value=response) as call:
            result = tikhub.execute(self.confirmed(request))
        self.assertNotIn("Authorization", call.call_args.kwargs["headers"])
        self.assertEqual(result["receipts"][0]["redirect_url"], "https://example.com/download")
        self.assertEqual(call.call_count, 1)

    def test_bounded_pagination_advances_only_with_explicit_has_more(self):
        self.configured()
        request = self.request("fetch_user_post_videos", {"sec_user_id": "TEST", "max_cursor": "0"}, page_limit=3)
        responses = [self.response({"data": {"aweme_list": [{"aweme_id": self.aid}], "max_cursor": 100, "has_more": 1}}),
                     self.response({"data": {"aweme_list": [], "max_cursor": 100, "has_more": 1}})]
        with patch.object(tikhub.requests, "request", side_effect=responses) as call:
            result = tikhub.execute(self.confirmed(request))
        self.assertEqual(call.call_count, 2)
        self.assertIn(("max_cursor", "100"), call.call_args.kwargs["params"])
        self.assertEqual((result["attempted"], result["requested"]), (2, 3))
        self.assertIsNone(tikhub.next_cursor({"data": {"max_cursor": 200}}, "max_cursor", 0))
        self.assertIsNone(tikhub.next_cursor({"data": {"max_cursor": 200, "has_more": 0}}, "max_cursor", 0))

    def test_cancel_stops_remaining_pages_and_keeps_receipt(self):
        self.configured()
        flag = [False]
        request = self.request("fetch_user_post_videos", {"sec_user_id": "TEST"}, page_limit=3)
        response = self.response({"data": {"aweme_list": [], "has_more": 1, "max_cursor": 200}})
        with patch.object(tikhub.requests, "request", return_value=response) as call:
            result = tikhub.execute(self.confirmed(request), lambda _: flag.__setitem__(0, True), lambda: flag[0])
        self.assertEqual(call.call_count, 1)
        self.assertTrue(result["cancelled"])
        self.assertEqual(len(result["receipts"]), 1)

    def test_credentials_redacted_but_response_params_and_data_preserved(self):
        self.configured()
        request = self.request()
        request["body_json"] = '{"cookie":"private-cookie-value"}'
        result = tikhub.redact({"params": {"aweme_id": self.aid, "cookie": "private-cookie-value"},
                                "echo": "private-cookie-value private-workbench-unit-key", "download": "https://example.com/a"}, request)
        self.assertEqual(result["params"]["aweme_id"], self.aid)
        self.assertEqual(result["params"]["cookie"], "[hidden]")
        self.assertNotIn("private-cookie-value", json.dumps(result))
        self.assertNotIn("private-workbench-unit-key", json.dumps(result))
        self.assertEqual(result["download"], "https://example.com/a")

    def test_statistics_import_validates_identity_and_updates_existing_dashboard_source(self):
        uid = "MS4wLjABAAAA_TIKHUB_TEST"
        intelligence.add_account(uid, "TikHub", "own")
        intelligence.save_account_posts(uid, "TikHub", [{"aweme_id": self.aid, "title": "test"}])
        body = {"code": 200, "data": {"statistics_list": [{"aweme_id": self.aid, "play_count": 12222}]}}
        counts = tikhub.import_response(self.op(), body, {"aweme_ids": self.aid})
        self.assertEqual(counts["playback"], 1)
        self.assertEqual(intelligence.video_history(self.aid)[-1]["metric_status"], "tikhub_app")
        body["data"]["statistics_list"][0]["aweme_id"] = "7703151458754472788"
        with self.assertRaises(ValueError):
            tikhub.import_response(self.op(), body, {"aweme_ids": self.aid})
        counts = tikhub.import_response(self.op(), body, {"aweme_ids": "7703151458754472788"})
        self.assertEqual(counts, {"imported": 0, "playback": 0})

    def test_comment_import_checks_identity_before_writing(self):
        body = {"data": {"comments": [{"cid": "123", "aweme_id": self.aid, "text": "好看", "user": {"nickname": "test"}}]}}
        counts = tikhub.import_response(self.op("fetch_video_comments"), body, {"aweme_id": self.aid})
        self.assertEqual(counts["comments"], 1)
        self.assertEqual(store.q("SELECT content FROM content_comments")[0]["content"], "好看")
        body["data"]["comments"][0]["aweme_id"] = "wrong"
        with self.assertRaises(ValueError):
            tikhub.import_response(self.op("fetch_video_comments"), body, {"aweme_id": self.aid})

    def test_top_level_cookie_and_rotated_api_key_do_not_leak(self):
        op = self.op("get_top_ads_filters", "/api/v1/tiktok/ads/")
        request = {"operation_id": op["operationId"], "params": {}, "body_json": '"private-cookie-body"'}
        self.assertNotIn("private-cookie-body", json.dumps(tikhub.redact({"echo": "private-cookie-body"}, request)))
        request.pop("body_json")
        request["items_json"] = '[{"body":"private-batch-cookie"}]'
        self.assertNotIn("private-batch-cookie", json.dumps(tikhub.redact({"echo": "private-batch-cookie"}, request)))
        self.configured()
        def respond(*_, **kw):
            key = kw["headers"]["Authorization"]
            playback.save_settings({"clear_key": True})
            return self.response({"data": {"echo": key}})
        with patch.object(tikhub.requests, "request", side_effect=respond):
            result = tikhub.execute(self.confirmed(self.request()))
        self.assertNotIn("private-workbench-unit-key", json.dumps(result))

    def test_queue_export_preserves_full_statistics_and_retry_requires_new_preview(self):
        self.configured()
        request = self.confirmed(self.request())
        request["endpoint"] = "tikhub_api"
        with patch.object(douk, "STARTED", True):
            job = douk.submit(request)
            douk.QUEUE.remove(job["id"])
        folder = douk.metadata(job["id"]).parent
        douk.atomic(folder / "result.json", {"data": [{"aweme_id": self.aid, "download_count": 77, "statistics": {"extra": 8}}], "receipts": []})
        public = douk.job(job["id"], True)
        self.assertEqual(public["records"][0]["download_count"], 77)
        for fmt in ("json", "csv", "xlsx"):
            raw, _ = douk.export(job["id"], fmt)
            self.assertGreater(len(raw), 40)
        with self.assertRaises(ValueError):
            douk.retry(job["id"])


if __name__ == "__main__":
    unittest.main()
