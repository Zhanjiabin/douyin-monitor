import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import insights
import intelligence
import metric_history as history
import playback
import store


class MetricHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=store.DB_PATH
        if store._CONN:store._CONN.close()
        store._CONN=None;store.DB_PATH=Path(self.tmp.name)/'history.db'
        self.uid='MS4wLjABAAAA_HISTORY_TEST';self.aid='7703151458754472787'
        intelligence.add_account(self.uid,'History','own')
        intelligence.save_account_posts(self.uid,'History',[{'aweme_id':self.aid,'title':'History work'}])
        playback.init()

    def tearDown(self):
        if store._CONN:store._CONN.close()
        store._CONN=None;history._READY=None;store.DB_PATH=self.old_db;self.tmp.cleanup()

    def row(self,play=100,**extra):
        return {'aweme_id':self.aid,'source':'tikhub_app','scope':'lifetime','play_count':play,
                'digg_count':10,'comment_count':3,'collect_count':2,'share_count':1,'request_id':'provider-receipt',**extra}

    def test_same_day_refreshes_preserve_every_value_and_timestamp(self):
        with patch.object(history,'timestamp',wraps=history.timestamp) as clock:
            playback.persist([self.row(100)],event_key='request:1')
            playback.persist([self.row(180,digg_count=18)],event_key='request:2')
        rows=history.video(self.aid)
        self.assertEqual([r['play_count'] for r in rows],[100,180])
        self.assertTrue(all(r['observed_at'].endswith('+08:00') for r in rows))
        self.assertTrue(clock.called)
        self.assertEqual(rows[-1]['play_delta'],80)
        self.assertEqual(store.q('SELECT COUNT(*) n FROM playback_measurements')[0]['n'],1)
        self.assertEqual(history.latest()[self.aid]['history_count'],2)
        self.assertEqual(insights.dashboard()['videos'][0]['metrics_updated_at'],rows[-1]['observed_at'])

    def test_reimporting_same_request_is_idempotent_but_new_request_is_retained(self):
        playback.persist([self.row()],event_key='request:1')
        playback.persist([self.row()],event_key='request:1')
        playback.persist([self.row()],event_key='request:2')
        self.assertEqual(len(history.video(self.aid)),2)

    def test_independent_request_ledgers_do_not_collide(self):
        playback.persist([self.row(100)],event_key='playback-request:1')
        playback.persist([self.row(200)],event_key='tikhub-request:1')
        self.assertEqual([r['play_count'] for r in history.video(self.aid)],[100,200])

    def test_missing_fields_remain_missing_and_zero_interactions_are_valid(self):
        playback.persist([self.row(100)])
        playback.persist([self.row(None,digg_count=0,comment_count=None,collect_count=None,share_count=None)])
        last=history.video(self.aid)[-1]
        self.assertIsNone(last['play_count']);self.assertEqual(last['digg_count'],0)
        self.assertIsNone(last['comment_count'])
        self.assertEqual(store.q('SELECT play_count FROM playback_measurements')[0]['play_count'],100)
        self.assertIsNone(last['play_delta'])

    def test_empty_response_does_not_mark_metrics_as_updated(self):
        row={'aweme_id':self.aid,'source':'tikhub_app','scope':'lifetime',**{k:None for k in history.METRICS}}
        history.record(row)
        self.assertEqual(history.video(self.aid),[])
        self.assertNotIn(self.aid,history.latest())

    def test_legacy_backfill_runs_once_and_keeps_source_and_scope(self):
        store.x('INSERT INTO playback_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (self.aid,'2026-11-01','2026-11-01 12:30:17','tikhub_app','lifetime',900,90,9,8,7,'old-receipt'))
        store.x('DELETE FROM metric_history_migrations');history._READY=None
        history.init();history._READY=None;history.init()
        rows=history.video(self.aid)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['origin'],rows[0]['observed_at'],rows[0]['play_count']),
                         ('legacy_daily','2026-11-01T12:30:17.000+08:00',900))
        self.assertEqual(rows[0]['scope'],'lifetime')

    def test_history_sorts_timezones_and_compares_only_same_scope(self):
        for time,source,scope,value in [('2026-11-05T02:00:00Z','app','lifetime',100),
                                      ('2026-11-05 11:00:00','app','window',900),
                                      ('2026-11-05 12:00:00','other','lifetime',700),
                                      ('2026-11-05 13:00:00','app','lifetime',150)]:
            history.record({'aweme_id':self.aid,'play_count':value},source,scope,time)
        rows=history.video(self.aid)
        self.assertEqual([r['play_delta'] for r in rows],[None,None,None,50])
        self.assertEqual(rows[0]['observed_at'],'2026-11-05T10:00:00.000+08:00')

    def test_concurrent_refreshes_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda i:playback.persist([self.row(i)],event_key=f'request:{i}'),range(30)))
        self.assertEqual(len(history.video(self.aid)),30)

    def test_mix_refresh_after_homepage_has_chronological_order(self):
        intelligence.save_account_posts(self.uid,'History',[{'aweme_id':self.aid,'digg_count':1}])
        store.upsert_mix({'mix_id':'mix','sec_uid':self.uid})
        store.save_video_snapshots('mix',[{'aweme_id':self.aid,'play_count':12345}])
        self.assertEqual(history.video(self.aid)[-1]['play_count'],12345)


if __name__=='__main__':unittest.main()
