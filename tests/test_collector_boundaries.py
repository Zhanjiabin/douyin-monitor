import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from collector import DouyinCollector, EP_DETAIL, EP_SERIES_AWEME, EP_AWEME_POST, EP_PROFILE, EP_MIX_AWEME


class CollectorBoundariesTests(unittest.TestCase):
    def collector(self, detail):
        col = DouyinCollector({})
        col._page = Mock()
        col._reset_cap = lambda: None
        col._wait_cap = lambda key, timeout: [{"aweme_detail": detail}] if key == EP_DETAIL else []
        return col

    def test_empty_detail_is_failure_not_no_collection(self):
        col = self.collector({})
        with self.assertRaisesRegex(RuntimeError, "未返回目标作品"):
            col.collect_video("1")

    def test_series_placeholder_is_not_saved_as_zero(self):
        col = self.collector({"aweme_id": "1", "mix_info": {"mix_id": "m"}})
        col._cap[EP_SERIES_AWEME] = [{"aweme_list": [{"statistics": {"play_count": 0}}]}]
        with self.assertRaisesRegex(RuntimeError, "主动请求合集接口"):
            col.collect_video("1")

    def test_actual_non_collection_video_remains_supported(self):
        col = self.collector({"aweme_id": "1", "desc": "standalone"})
        self.assertIsNone(col.collect_video("1")["mix_id"])

    def test_terminal_window_is_not_whole_mix_and_excludes_foreign_mix(self):
        col=self.collector({"aweme_id":"1","mix_info":{"mix_id":"m","statis":{"updated_to_episode":36}}})
        col._cap[EP_MIX_AWEME]=[{"has_more":0,"aweme_list":[
            {"aweme_id":"1","mix_info":{"mix_id":"m"},"statistics":{"play_count":321}},
            {"aweme_id":"2","mix_info":{"mix_id":"other"},"statistics":{"play_count":999}}]}]
        r=col.collect_video("1")
        self.assertTrue(r["has_more_left"])
        self.assertEqual([e["aweme_id"] for e in r["episodes"]],["1"])

    def profile_collector(self, gated=False):
        col = DouyinCollector({})
        col._page = Mock()
        col._page.get_by_text.return_value.count.return_value = int(gated)
        col._page.evaluate.return_value = {"x":800,"y":600}
        col._reset_cap = lambda: None
        col._cap = {EP_AWEME_POST:[{"has_more":1,"aweme_list":[{"aweme_id":"1","author":{"sec_uid":"uid"},"create_time":100}]}],
                    EP_PROFILE:[{"user":{"sec_uid":"uid","aweme_count":128,"follower_count":42}}]}
        col._wait_cap = lambda key, timeout: col._cap.get(key, [])
        return col

    def test_login_gate_preserves_posts_and_reports_homepage_total(self):
        col = self.profile_collector(True)
        r = col.collect_author_posts("uid")
        self.assertEqual(r["scan_status"],"login_required")
        self.assertEqual(len(r["items"]),1)
        self.assertEqual(r["profile"]["homepage_count"],128)
        col._page.mouse.wheel.assert_not_called()

    def test_pagination_deduplicates_and_excludes_other_authors(self):
        col = self.profile_collector()
        def more(*_):
            col._cap[EP_AWEME_POST].append({"has_more":0,"aweme_list":[
                {"aweme_id":"1","author":{"sec_uid":"uid"}},
                {"aweme_id":"2","author":{"sec_uid":"uid"}},
                {"aweme_id":"3","author":{"sec_uid":"other"}}]})
        col._page.mouse.wheel.side_effect = more
        r=col.collect_author_posts("uid")
        self.assertEqual({p["aweme_id"] for p in r["items"]},{"1","2"})
        self.assertEqual(r["scan_status"],"complete")
        self.assertFalse(r["has_more"])

    def test_page_limit_and_stall_are_not_complete(self):
        col=self.profile_collector()
        self.assertEqual(col.collect_author_posts("uid",max_pages=1)["scan_status"],"page_limit")
        self.assertEqual(col.collect_author_posts("uid",max_pages=3)["scan_status"],"stalled")

    def test_collection_merges_overlapping_windows(self):
        col=DouyinCollector({})
        col._page=Mock()
        posts=[{"aweme_id":str(i),"mix_id":"m","create_time":i} for i in range(1,6)]
        def window(aid, **kwargs):
            ids=("1","2","3") if aid=="1" else ("3","4","5")
            return {"mix_id":"m","updated_to_episode":5,"has_more_left":False,
                    "episodes":[{"aweme_id":i,"play_count":100,"create_time":int(i)} for i in ids]}
        col.collect_video=Mock(side_effect=window)
        result=col.collect_collection("1","m",posts)
        self.assertEqual(len(result["episodes"]),5)
        self.assertFalse(result["has_more_left"])
        self.assertEqual([c.args[0] for c in col.collect_video.call_args_list],["1","4"])

    def test_collection_keeps_partial_data_on_later_failure(self):
        col=DouyinCollector({});col._page=Mock()
        col.collect_video=Mock(side_effect=[{"mix_id":"m","updated_to_episode":3,"episodes":[{"aweme_id":"1","play_count":42}]},RuntimeError("timeout")])
        result=col.collect_collection("1","m",[{"aweme_id":i,"mix_id":"m"} for i in ("1","2","3")])
        self.assertTrue(result["has_more_left"])
        self.assertEqual(result["episodes"][0]["play_count"],42)

    def test_explicit_mix_pagination_uses_server_cursor(self):
        col=DouyinCollector({});col._page=Mock()
        col._page.evaluate.side_effect=[
            {"status_code":0,"cursor":15,"has_more":1,"aweme_list":[{"aweme_id":"a"}]},
            {"status_code":0,"cursor":30,"has_more":0,"aweme_list":[{"aweme_id":"b"}]}]
        rows=col._fetch_mix_pages("m")
        self.assertEqual(len(rows),2)
        self.assertEqual([c.args[1]["cursor"] for c in col._page.evaluate.call_args_list],[0,15])

    def test_explicit_repeat_page_keeps_partial_flag(self):
        col=DouyinCollector({});col._page=Mock()
        col._page.evaluate.return_value={"status_code":0,"cursor":15,"has_more":1,"aweme_list":[{"aweme_id":"a"}]}
        pages=col._fetch_mix_pages("m")
        self.assertEqual(len(pages),1)
        self.assertEqual(pages[-1]["has_more"],1)

    def test_series_page_can_be_recovered_by_explicit_mix(self):
        col=self.collector({"aweme_id":"1","mix_info":{"mix_id":"m","statis":{"updated_to_episode":1}}})
        col._cap[EP_SERIES_AWEME]=[{"aweme_list":[{"aweme_id":"1","statistics":{"play_count":0}}]}]
        col._page.evaluate.return_value={"status_code":0,"cursor":1,"has_more":0,"aweme_list":[{"aweme_id":"1","mix_info":{"mix_id":"m"},"statistics":{"play_count":2468}}]}
        result=col.collect_video("1")
        self.assertEqual(result["episodes"][0]["play_count"],2468)
        self.assertFalse(result["has_more_left"])


if __name__ == "__main__":
    unittest.main()
