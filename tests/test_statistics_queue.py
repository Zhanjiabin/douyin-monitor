"""The multi-select workflow must issue separate, bounded single-ID requests."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import corpus
import douk
import intelligence
import playback
import store
import tikhub
import workbench


class StatisticsQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = store.DB_PATH, playback.SETTINGS, douk.JOBS
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = Path(self.temp.name) / "test.db"
        playback.SETTINGS = Path(self.temp.name) / "settings.json"
        douk.JOBS = Path(self.temp.name) / "jobs"
        store.init_db();douk.init_tables();corpus.init();playback.init()
        self.ids = [str(7703151458754472787 + i) for i in range(9)]
        intelligence.add_account("STATISTICS_QUEUE", "测试账号", "peer")
        intelligence.save_account_posts("STATISTICS_QUEUE", "测试账号", [{"aweme_id": i, "title": i} for i in self.ids])
        playback.save_settings({"api_key": "private-queue-fixture-key", "max_calls_per_job": 1, "daily_call_limit": 100})

    def tearDown(self):
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS = self.old
        self.temp.cleanup()

    def prepared(self, ids=None, **kw):
        p = workbench.prepare({"action": "playback", "video_ids": ids or self.ids[:7], **kw})
        return {**p["request"], "allow_charges": True, "fingerprint": p["fingerprint"]}

    def response(self, aid, play=12345, status=200):
        return Mock(status_code=status, headers={}, json=lambda: {"code": 200, "request_id": "fixture-request", "data": {"statistics_list": [{"aweme_id": aid, "play_count": play}]}})

    def test_seven_selected_works_ignore_ordinary_job_cap_but_not_daily_budget(self):
        with patch.object(tikhub.requests, "request") as transport:
            p = workbench.plan({"action": "playback", "video_ids": self.ids[:7]})
            self.assertTrue(p["allowed"]);self.assertEqual((p["calls"], p["concurrency"]), (7, 3))
            playback.save_settings({"daily_call_limit": 6})
            self.assertIn("今日", workbench.plan({"action": "playback", "video_ids": self.ids[:7]})["reason"])
        transport.assert_not_called()

    def test_bounded_parallel_single_calls_progress_and_persistence(self):
        gate, lock = threading.Barrier(3), threading.Lock()
        running = peak = 0
        seen = []
        def respond(method, url, **kw):
            nonlocal running, peak
            self.assertTrue(url.endswith("/fetch_video_statistics"))
            aid = dict(kw["params"])["aweme_ids"];self.assertNotIn(",", aid)
            with lock:
                seen.append(aid);running += 1;peak = max(peak, running);number = len(seen)
            if number <= 3:
                gate.wait(timeout=5)
            time.sleep(.02)
            with lock:
                running -= 1
            return self.response(aid)
        events = []
        with patch.object(tikhub.requests, "request", side_effect=respond):
            result = tikhub.execute(self.prepared(), events.append)
        self.assertEqual((peak, len(seen), len(set(seen))), (3, 7, 7))
        self.assertEqual((result["attempted"], result["imported"]["playback"]), (7, 7))
        self.assertEqual(events[-1]["done"], 7);self.assertEqual(events[-1]["succeeded"], 7)
        self.assertTrue(any(e["running"] == 3 for e in events))
        self.assertEqual(tikhub.usage(), 7)
        self.assertEqual(len(store.q("SELECT * FROM playback_measurements")), 7)
        self.assertNotIn("private-queue-fixture-key", json.dumps(result))

    def test_one_failure_and_missing_play_do_not_block_other_works_or_retry(self):
        attempts = []
        def respond(*_, **kw):
            aid = dict(kw["params"])["aweme_ids"];attempts.append(aid)
            if aid == self.ids[1]:
                raise tikhub.requests.Timeout("private-queue-fixture-key")
            return self.response(aid, play=0 if aid == self.ids[2] else 12345)
        with patch.object(tikhub.requests, "request", side_effect=respond):
            result = tikhub.execute(self.prepared())
        self.assertEqual(len(attempts), 7)
        self.assertEqual(len(set(attempts)), 7)
        states = {r["aweme_id"]: r["status"] for r in result["refresh_items"]}
        self.assertEqual(states[self.ids[1]], "failed");self.assertEqual(states[self.ids[2]], "not_returned")
        self.assertEqual(list(states.values()).count("completed"), 5)
        self.assertNotIn("private-queue-fixture-key", json.dumps(result))

    def test_provider_global_error_stops_waiting_work(self):
        with patch.object(tikhub.requests, "request", return_value=self.response(self.ids[0], status=429)) as transport:
            result = tikhub.execute(self.prepared(concurrency=1))
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(sum(i["status"] == "stopped" for i in result["refresh_items"]), 6)

    def test_cancel_stops_new_requests_and_saves_inflight_success(self):
        flag = threading.Event()
        def respond(*_, **kw):
            flag.set()
            return self.response(dict(kw["params"])["aweme_ids"])
        with patch.object(tikhub.requests, "request", side_effect=respond) as transport:
            result = tikhub.execute(self.prepared(concurrency=1), cancelled=flag.is_set)
        self.assertEqual(transport.call_count, 1);self.assertTrue(result["cancelled"])
        self.assertEqual(len(store.q("SELECT * FROM playback_measurements")), 1)
        self.assertEqual(sum(i["status"] == "stopped" for i in result["refresh_items"]), 6)

    def test_concurrency_is_validated_and_included_in_confirmation(self):
        for value in (0, 6, True, "3.0"):
            with self.assertRaises(ValueError):
                self.prepared(concurrency=value)
        request = self.prepared(concurrency=2)
        request["concurrency"] = 3
        with patch.object(tikhub.requests, "request") as transport:
            with self.assertRaises(ValueError):
                tikhub.execute(request)
        transport.assert_not_called()

    def test_queue_flag_cannot_relax_other_operations_or_multi_id_calls(self):
        request = self.prepared()
        request["operation_id"] = workbench.op_id("fetch_multi_video_statistics")
        with self.assertRaises(ValueError):
            tikhub.plan(request)
        request = self.prepared()
        request["items_json"] = json.dumps([{"params": {"aweme_ids": ",".join(self.ids[:2])}}])
        with self.assertRaises(ValueError):
            tikhub.plan(request)
        ordinary = self.prepared();ordinary.pop("single_video_queue")
        self.assertFalse(tikhub.plan(ordinary)["allowed"])

    def test_concurrent_children_share_atomic_daily_budget(self):
        playback.save_settings({"daily_call_limit": 7})
        request = self.prepared()
        started = threading.Event();release = threading.Event()
        def respond(*_, **kw):
            started.set();release.wait(timeout=5)
            return self.response(dict(kw["params"])["aweme_ids"])
        with patch.object(tikhub.requests, "request", side_effect=respond) as transport,ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tikhub.execute, request)
            self.assertTrue(started.wait(timeout=5))
            playback.save_settings({"daily_call_limit": 3});release.set()
            result = future.result(timeout=10)
        self.assertLessEqual(transport.call_count, 3)
        self.assertEqual(tikhub.usage(), transport.call_count)
        self.assertTrue(any(i["status"] == "stopped" for i in result["refresh_items"]))


if __name__ == "__main__":
    unittest.main()
