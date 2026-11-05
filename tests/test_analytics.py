import sys
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import analytics
import intelligence
import metric_history as history
import playback
import store


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=store.DB_PATH
        if store._CONN:store._CONN.close()
        store._CONN=None;store.DB_PATH=Path(self.tmp.name)/'analytics.db'
        self.uid='MS4wLjABAAAA_STATS_OWN';self.peer='MS4wLjABAAAA_STATS_PEER'
        self.aid='7703151458754472787';self.other='7703151458754472788'
        self.clock=patch.object(store,'today_str',return_value='2026-11-05');self.clock.start()
        for uid,role,aid,title in [(self.peer,'peer',self.other,'同行末日'),(self.uid,'own',self.aid,'自己的咖啡店')]:
            intelligence.add_account(uid,title,role)
            published=int(datetime(2026,11,4,23,30,tzinfo=history.TZ).timestamp())
            intelligence.save_account_posts(uid,title,[{'aweme_id':aid,'title':title,'create_time':published}])

    def tearDown(self):
        self.clock.stop()
        if store._CONN:store._CONN.close()
        store._CONN=None;history._READY=None;store.DB_PATH=self.old_db;self.tmp.cleanup()

    def event(self,day,play,aid=None,source='app',scope='lifetime',**metrics):
        history.record({'aweme_id':aid or self.aid,'play_count':play,**metrics},source,scope,day)

    def test_default_is_own_account_and_other_account_never_leaks_into_totals(self):
        self.event('2026-11-05 12:00',100)
        self.event('2026-11-05 12:00',900000,aid=self.other)
        with patch.object(playback.requests,'request',side_effect=AssertionError('must stay offline')):
            own=analytics.dashboard();peer=analytics.dashboard(self.peer)
        self.assertEqual(own['account']['sec_uid'],self.uid)
        self.assertEqual(own['series'][-1]['play_count'],100)
        self.assertEqual(peer['series'][-1]['play_count'],900000)
        self.assertEqual([w['aweme_id'] for w in own['works']],[self.aid])

    def test_many_refreshes_do_not_inflate_day_totals_and_growth(self):
        self.event('2026-11-04 10:00',100,digg_count=10)
        self.event('2026-11-05 10:00',180,digg_count=18)
        self.event('2026-11-05 18:00',220,digg_count=22)
        result=analytics.dashboard(self.uid,7)
        last=result['series'][-1]
        self.assertEqual((last['play_count'],last['growth']['play_count'],last['samples']['play_count'],last['growth_samples']['play_count']),(220,120,1,1))
        self.assertEqual(result['interactions']['digg_count'],22)
        self.assertEqual(result['works'][0]['metrics_updated_at'],'2026-11-05T18:00:00.000+08:00')

    def test_gap_or_changed_scope_source_and_decline_are_not_positive_growth(self):
        cases=[('2026-11-03 10:00',100,'app','lifetime',200,'app','lifetime'),
               ('2026-11-04 10:00',100,'app','window',200,'app','lifetime'),
               ('2026-11-04 10:00',100,'other','lifetime',200,'app','lifetime'),
               ('2026-11-04 10:00',300,'app','lifetime',200,'app','lifetime')]
        for before,old,src,scope,new,newsrc,newscope in cases:
            with self.subTest(before=before,source=src,scope=scope,value=old):
                store.x('DELETE FROM video_metric_events')
                self.event(before,old,source=src,scope=scope)
                self.event('2026-11-05 10:00',new,source=newsrc,scope=newscope)
                self.assertIsNone(analytics.dashboard(self.uid,7)['series'][-1]['growth']['play_count'])

    def test_missing_metrics_are_not_zero_or_carried_across_days(self):
        self.event('2026-11-04 10:00',100,digg_count=10)
        self.event('2026-11-05 10:00',None,digg_count=0)
        data=analytics.dashboard(self.uid,7)
        self.assertIsNone(data['series'][-1]['play_count'])
        self.assertEqual(data['series'][-1]['digg_count'],0)
        self.assertIsNone(data['series'][0]['play_count'])
        self.assertEqual(data['works'][0]['play_count'],100)
        self.assertEqual(data['works'][0]['digg_count'],0)

    def test_period_limits_latest_values_and_keyword_is_account_scoped(self):
        self.event('2026-10-01 10:00',999)
        self.event('2026-11-05 10:00',None,digg_count=10)
        data=analytics.dashboard(self.uid,7,'咖啡')
        self.assertIsNone(data['works'][0]['play_count'])
        self.assertIsNone(analytics.dashboard(self.uid,7,'同行')['series'][-1]['digg_count'])
        self.assertEqual(analytics.dashboard(self.uid,90)['works'][0]['play_count'],999)

    def test_publication_hours_use_shanghai_and_only_period_publications(self):
        self.event('2026-11-05 10:00',200)
        result=analytics.dashboard(self.uid,7)
        self.assertEqual(result['publication'],[{'weekday':2,'hour':23,'count':1,'known':1,'median_play':200}])
        self.assertEqual(result['hours'][23]['median_play'],200)
        self.assertEqual(sum(r['count'] for r in result['weekdays']),1)

    def test_legacy_mix_only_works_are_counted(self):
        store.upsert_mix({'mix_id':'old-mix','sec_uid':self.uid})
        store.save_video_snapshots('old-mix',[{'aweme_id':'7703151458754472799','title':'旧合集作品','play_count':700}])
        self.assertEqual(analytics.dashboard(self.uid,7)['series'][-1]['play_count'],700)

    def test_unknown_account_invalid_period_and_large_query_are_rejected(self):
        for args in [('missing',30,''),(self.uid,365,''),(self.uid,7,'x'*121)]:
            with self.subTest(args=args),self.assertRaises(ValueError):analytics.dashboard(*args)

    def test_no_accounts_is_a_valid_empty_page(self):
        store.x('DELETE FROM tracked_accounts')
        result=analytics.dashboard()
        self.assertIsNone(result['account']);self.assertEqual(result['series'],[])


if __name__=='__main__':unittest.main()
