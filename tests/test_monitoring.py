from datetime import datetime, timedelta
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
import monitoring
import playback
import store
import tikhub
import workbench


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = store.DB_PATH, playback.SETTINGS, douk.JOBS, douk.STARTED
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH = Path(self.temp.name) / "test.db"
        playback.SETTINGS = Path(self.temp.name) / "settings.json"
        douk.JOBS = Path(self.temp.name) / "jobs"
        douk.STARTED = False
        douk.QUEUE.clear()
        store.init_db()
        douk.init_tables()
        corpus.init()
        playback.init()
        self.now = datetime(2026, 11, 4, 8, 30, tzinfo=monitoring.TZ)
        self.clock = patch.object(monitoring, "clock", return_value=self.now)
        self.clock.start()
        self.uid, self.peer = "MS4wLjABAAAA_MONITOR_OWN", "MS4wLjABAAAA_MONITOR_PEER"
        intelligence.add_account(self.uid, "自有", "own")
        intelligence.add_account(self.peer, "同行", "peer")
        self.ids = [str(7703151458754472787 + n) for n in range(6)]
        posts = []
        for aid, days in zip(self.ids, (1, 6, 8, 2, -1, None)):
            posts.append({"aweme_id": aid, "title": aid, "create_time": int((self.now - timedelta(days=days)).timestamp()) if days is not None else None})
        intelligence.save_account_posts(self.uid, "自有", [p for i, p in enumerate(posts) if i != 3])
        intelligence.save_account_posts(self.peer, "同行", [posts[3]])
        playback.save_settings({"api_key": "private-monitoring-unit-key"})

    def tearDown(self):
        self.clock.stop()
        douk.QUEUE.clear()
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS, douk.STARTED = self.old
        self.temp.cleanup()

    def policy(self, **kw):
        return {"enabled": True, "account_ids": [self.uid], "publication_days": 7, "frequency": "daily",
                "at_time": "09:00", "interval_minutes": 60, "start_date": "2026-11-04", "end_date": "", "use_batch": False, **kw}

    def test_single_endpoint_is_default_and_each_request_has_one_id(self):
        p = workbench.prepare({"action": "playback", "video_ids": self.ids[:3]})
        self.assertEqual(p["calls"], 3)
        self.assertIn("fetch_video_statistics_", p["request"]["operation_id"])
        items = json.loads(p["request"]["items_json"])
        self.assertEqual([i["params"]["aweme_ids"] for i in items], self.ids[:3])
        self.assertEqual(playback.plan("app", self.ids[:3])["calls"], 3)

    def test_batch_is_blocked_in_all_entry_points_without_opt_in(self):
        p = workbench.prepare({"action": "playback", "video_ids": self.ids[:2], "playback_method": "batch"})
        self.assertFalse(playback.settings()["allow_batch_statistics"])
        self.assertFalse(p["allowed"])
        self.assertFalse(playback.plan("app_batch", self.ids[:2])["allowed"])
        with patch.object(tikhub.requests, "request") as call:
            with self.assertRaises(ValueError):
                tikhub.execute({**p["request"], "fingerprint": p["fingerprint"], "allow_charges": True})
        call.assert_not_called()
        self.assertEqual(tikhub.usage(), 0)

    def test_opt_in_enables_batch_but_default_remains_single(self):
        playback.save_settings({"allow_batch_statistics": True})
        self.assertTrue(workbench.plan({"action": "playback", "video_ids": self.ids[:2], "playback_method": "batch"})["allowed"])
        self.assertEqual(workbench.plan({"action": "playback", "video_ids": self.ids[:2]})["calls"], 2)
        p = workbench.prepare({"action": "playback", "video_ids": self.ids[:2], "playback_method": "batch"})
        playback.save_settings({"allow_batch_statistics": False})
        with self.assertRaises(ValueError):
            douk.submit({**p["request"], "fingerprint": p["fingerprint"], "allow_charges": True})

    def test_disabling_batch_mid_job_stops_remaining_calls(self):
        playback.save_settings({"allow_batch_statistics": True})
        request = {"operation_id": workbench.op_id("fetch_multi_video_statistics"), "params": {},
                   "items_json": json.dumps([{"params": {"aweme_ids": self.ids[0]}}] * 2)}
        p = tikhub.plan(request)
        def respond(*_, **kw):
            playback.save_settings({"allow_batch_statistics": False})
            return Mock(status_code=200, headers={}, json=lambda: {"code": 200, "data": {"statistics_list": []}})
        with patch.object(tikhub.requests, "request", side_effect=respond) as call:
            result = tikhub.execute({**request, "fingerprint": p["fingerprint"], "allow_charges": True})
        self.assertEqual(call.call_count, 1)
        self.assertIn("关闭", result["errors"][0]["error"])

    def test_schedule_defaults_are_disabled_daily_last_seven_days(self):
        cfg = monitoring.config()
        self.assertFalse(cfg["enabled"])
        self.assertEqual((cfg["publication_days"], cfg["frequency"], cfg["at_time"]), (7, "daily", "09:00"))
        with patch.object(douk, "submit") as submit:
            self.assertIsNone(monitoring.tick(self.now + timedelta(days=1)))
        submit.assert_not_called()

    def test_targets_use_publication_time_selected_enabled_accounts(self):
        cfg = self.policy()
        self.assertEqual({r["aweme_id"] for r in monitoring.targets(cfg)}, set(self.ids[:2]))
        cfg["account_ids"].append(self.peer)
        self.assertEqual({r["aweme_id"] for r in monitoring.targets(cfg)}, {self.ids[0], self.ids[1], self.ids[3]})
        self.assertEqual(monitoring.preview(self.policy())["calls"], 2)

    def test_invalid_ranges_and_empty_accounts_cannot_enable_schedule(self):
        for change in ({"account_ids": []}, {"end_date": "2026-11-03"}, {"at_time": "25:00"},
                       {"interval_minutes": 0}, {"account_ids": ["missing"]}, {"use_batch": True}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    monitoring.save(self.policy(**change))

    def test_daily_schedule_claims_once_and_survives_restart(self):
        monitoring.save(self.policy())
        cfg = monitoring.config()
        self.assertEqual(monitoring.next_run(cfg), "2026-11-04T09:00:00+08:00")
        with patch.object(douk, "submit", return_value={"id": "a" * 16}) as submit:
            self.assertIsNone(monitoring.tick(self.now.replace(hour=8, minute=59)))
            self.assertIsNotNone(monitoring.tick(self.now.replace(hour=9, minute=1)))
            store._CONN.close()
            store._CONN = None
            self.assertIsNone(monitoring.tick(self.now.replace(hour=9, minute=5)))
        self.assertEqual(submit.call_count, 1)
        request = submit.call_args.args[0]
        self.assertIn("fetch_video_statistics_", request["operation_id"])
        self.assertEqual(len(json.loads(request["items_json"])), 2)

    def test_new_enable_after_daily_time_waits_until_next_day(self):
        with patch.object(monitoring, "clock", return_value=self.now.replace(hour=13)):
            monitoring.save(self.policy())
            self.assertEqual(monitoring.next_run(), "2026-11-05T09:00:00+08:00")
            self.assertIsNone(monitoring.tick())

    def test_interval_catches_only_latest_slot_and_avoids_overlap(self):
        monitoring.save(self.policy(frequency="interval"))
        self.assertEqual(monitoring.next_run(), "2026-11-04T09:30:00+08:00")
        jid = "b" * 16
        douk.atomic(douk.metadata(jid), {"id": jid, "status": "running"})
        with patch.object(douk, "submit", return_value={"id": jid}) as submit:
            monitoring.tick(self.now.replace(hour=11, minute=0))
            monitoring.tick(self.now.replace(hour=11, minute=31))
        self.assertEqual(submit.call_count, 1)
        rows = store.q("SELECT scheduled_for,status FROM monitor_schedule_runs ORDER BY scheduled_for")
        self.assertIn("10:30", rows[0]["scheduled_for"])
        self.assertEqual(rows[1]["status"], "skipped")

    def test_end_date_and_disabled_policy_stop_future_runs(self):
        monitoring.save(self.policy(end_date="2026-11-04"))
        self.assertIsNone(monitoring.tick(self.now + timedelta(days=1)))
        self.assertIsNone(monitoring.next_run(now=self.now + timedelta(days=1)))
        monitoring.save(self.policy(enabled=False))
        self.assertIsNone(monitoring.tick(self.now.replace(hour=10)))

    def test_daily_budget_change_blocks_slot_without_partial_refresh_or_retry(self):
        monitoring.save(self.policy())
        playback.save_settings({"daily_call_limit": 1})
        with patch.object(douk, "submit") as submit:
            monitoring.tick(self.now.replace(hour=9, minute=1))
            monitoring.tick(self.now.replace(hour=9, minute=2))
        submit.assert_not_called()
        row = store.q("SELECT status,calls FROM monitor_schedule_runs")[0]
        self.assertEqual((row["status"], row["calls"]), ("blocked", 2))
        self.assertEqual(tikhub.usage(), 0)

    def test_failed_queue_is_recorded_and_never_retried_for_same_slot(self):
        monitoring.save(self.policy())
        with patch.object(douk, "submit", side_effect=ValueError("failed")) as submit:
            monitoring.tick(self.now.replace(hour=9, minute=1))
            monitoring.tick(self.now.replace(hour=9, minute=2))
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(store.q("SELECT status FROM monitor_schedule_runs")[0]["status"], "failed")

    def test_scheduled_job_uses_normal_queue_and_persists_single_source(self):
        monitoring.save(self.policy())
        with patch.object(douk, "STARTED", True):
            monitoring.tick(self.now.replace(hour=9, minute=1))
        def respond(*args, **kw):
            self.assertTrue(args[1].endswith("/fetch_video_statistics"))
            aid = dict(kw["params"])["aweme_ids"]
            self.assertNotIn(",", aid)
            return Mock(status_code=200, headers={}, json=lambda: {"code": 200, "data": {"statistics_list": [{"aweme_id": aid, "play_count": 12345}]}})
        with patch.object(tikhub.requests, "request", side_effect=respond) as call, patch.object(monitoring, "clock", return_value=self.now.replace(hour=9, minute=1)):
            douk.consume()
        self.assertEqual(call.call_count, 2)
        state = monitoring.status()
        self.assertEqual(state["history"][0]["status"], "completed")
        self.assertEqual({r["source"] for r in store.q("SELECT source FROM playback_measurements")}, {"tikhub_app"})
        self.assertNotIn("private-monitoring-unit-key", json.dumps(state))

    def test_disabling_plan_cancels_already_queued_scheduled_requests(self):
        monitoring.save(self.policy())
        with patch.object(douk, "STARTED", True):
            monitoring.tick(self.now.replace(hour=9, minute=1))
        monitoring.save(self.policy(enabled=False))
        with patch.object(tikhub.requests, "request") as call:
            douk.consume()
        call.assert_not_called()
        self.assertEqual(tikhub.usage(), 0)

    def test_crash_after_claim_is_interrupted_without_resubmitting_slot(self):
        monitoring.save(self.policy())
        instant = self.now.replace(hour=9, minute=1)
        with patch.object(douk, "submit", side_effect=SystemExit("simulate process exit")):
            with self.assertRaises(SystemExit):
                monitoring.tick(instant)
        monitoring.recover()
        with patch.object(douk, "submit") as submit:
            self.assertIsNone(monitoring.tick(instant))
        submit.assert_not_called()
        self.assertEqual(store.q("SELECT status FROM monitor_schedule_runs")[0]["status"], "interrupted")

    def test_disable_is_available_even_if_selected_account_was_removed(self):
        monitoring.save(self.policy())
        store.x("UPDATE tracked_accounts SET enabled=0 WHERE sec_uid=?", (self.uid,))
        result = monitoring.save({"enabled": False})
        self.assertFalse(result["config"]["enabled"])

    def test_disable_is_available_after_batch_permission_is_revoked(self):
        playback.save_settings({"allow_batch_statistics": True})
        monitoring.save(self.policy(use_batch=True))
        playback.save_settings({"allow_batch_statistics": False})
        self.assertFalse(monitoring.save({"enabled": False})["config"]["enabled"])


if __name__ == "__main__":
    unittest.main()
