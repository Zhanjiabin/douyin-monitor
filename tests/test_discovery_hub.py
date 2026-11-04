"""Unified library, observed changes, local reports and bounded paid schedules."""
from datetime import timedelta
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import discovery as d
import discovery_hub as hub
import douk
import playback
import store
import tikhub


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.before=store.DB_PATH,playback.SETTINGS,douk.JOBS
        if store._CONN:store._CONN.close()
        store._CONN=None
        self.path=Path(self.temp.name)
        store.DB_PATH=self.path/'test.db';playback.SETTINGS=self.path/'settings.json';douk.JOBS=self.path/'jobs'
        store.init_db();playback.init();hub.init()
        playback.save_settings({'api_key':'hub-fixture','max_calls_per_job':5,'daily_call_limit':50})
        self.at=d.now().replace(hour=22,minute=0,second=0,microsecond=0)
        self.clock=patch.object(d,'now',return_value=self.at);self.clock.start()
        self.aid='7703151458754472787'

    def tearDown(self):
        self.clock.stop()
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH,playback.SETTINGS,douk.JOBS=self.before
        self.temp.cleanup()

    def work(self,**extra):
        return {'id':self.aid,'aweme_id':self.aid,'title':'假如机器人有一天醒来 #AI短片 #原创动画','rank':3,
                'published_at':self.at.replace(hour=7).isoformat(),'account_name':'AI作者','fans':5000,
                'board_play_count':100000,'digg_count':3000,'url':'https://www.douyin.com/video/'+self.aid,**extra}

    def hot(self,**extra):
        return {'id':'topic1','title':'AI机器人舞蹈挑战','rank':3,'heat':3000,'category':'科技','allow_publish':True,**extra}

    def snapshot(self,source,items,hour=10,days_ago=0,**q):
        query=d.query({'source':source,**q})
        at=(self.at-timedelta(days=days_ago)).replace(hour=hour).isoformat()
        identity=uuid.uuid4().hex
        items=[{**r,'source':source} for r in items]
        store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',
                (identity,d.key(query),source,json.dumps(query),json.dumps({'items':items}),at,'completed',''))
        hub.ingest(identity)
        return identity

    def test_total_board_contracts_use_single_page_requests_and_explicit_categories(self):
        with patch.object(tikhub.requests,'request') as transport:
            p=d.prepare({'source':'video_total','categories':['643','607'],'pages':5})
            hot=d.prepare({'source':'hot_total','pages':5})
        self.assertEqual(p['calls'],5)
        for i,request in enumerate(p['requests']):
            body=json.loads(request['body_json'])
            self.assertEqual(body['page'],i+1);self.assertEqual(body['page_size'],20)
            self.assertEqual(body['tags'],[{'value':607},{'value':643}]);self.assertEqual(body['sub_type'],1001)
        self.assertEqual(hot['requests'][0]['params']['start_date'],(self.at-timedelta(days=6)).strftime('%Y%m%d'))
        self.assertFalse(playback.settings()['allow_batch_statistics']);transport.assert_not_called()

    def test_realistic_video_fields_and_live_hot_zero_values_are_preserved(self):
        r=d.parse_works({'objs':[{'item_id':self.aid,'item_title':'AI视频','fans_cnt':100,'play_cnt':300,
              'like_cnt':20,'follow_cnt':5,'score':90,'like_rate':.06,'follow_rate':.001,'item_duration':17334}]},'video_total')[0]
        self.assertEqual(r['follow_count'],5);self.assertEqual(r['index'],90);self.assertEqual(r['duration'],17.334)
        hot=d.parse_hot({'word_list':[{'word':'AI新工具','hot_value':0}]},'hot_live')[0]
        self.assertEqual(hot['heat'],0);self.assertIsNone(hot['rank']);self.assertIn('provider_data',hot)
        pinned=d.parse_hot({'word_list':[{'word':'置顶内容','hot_value':0,'hotlist_param':'{"rank":0}'}]},'hot_live')[0]
        self.assertEqual(pinned['rank'],0)

    def test_idempotent_snapshot_index_and_deduplication_preserve_board_membership(self):
        identity=self.snapshot('video_total',[self.work()],categories=['643'])
        hub.ingest(identity);hub.backfill()
        self.snapshot('lowfan',[self.work(rank=6,board_play_count=7000)],hour=20)
        data=hub.library()
        self.assertEqual(data['total'],1)
        self.assertEqual(len(data['items'][0]['boards']),2)
        self.assertEqual(data['items'][0]['board_play_count'],7000)
        self.assertEqual(len(hub.history('works',self.aid)['history']),2)

    def test_observation_changes_do_not_mix_boards_or_query_windows(self):
        self.snapshot('video_total',[self.work(rank=20)],categories=['643'],window=24)
        self.snapshot('video_total',[self.work(rank=2)],hour=20,categories=['643'],window=168)
        self.assertIsNone(hub.library()['items'][0]['comparison'])
        self.snapshot('hot_total',[self.hot(rank=1)],hour=10)
        self.snapshot('hot_live',[self.hot(rank=10)],hour=11)
        self.assertIsNone(hub.library({'kind':'hot'})['items'][0]['comparison'])
        self.snapshot('hot_live',[self.hot(rank=4,heat=6000)],hour=20)
        c=hub.library({'kind':'hot'})['items'][0]['comparison']
        self.assertEqual(c['rank_change'],6);self.assertEqual(c['heat_percent'],100)

    def test_initial_sample_never_fabricates_rising_metrics(self):
        self.snapshot('hot_live',[self.hot(heat=0)])
        row=hub.library({'kind':'hot'})['items'][0]
        self.assertIsNone(row['comparison'])
        report=hub.generate_report()
        self.assertEqual(report['coverage']['rising_hot'],0)
        self.assertEqual(report['hot'][0]['heat'],0)

    def test_library_search_sort_pagination_and_exports_are_offline(self):
        works=[self.work(id=str(int(self.aid)+i),aweme_id=str(int(self.aid)+i),title=f'AI作品 {i}',digg_count=i) for i in range(45)]
        self.snapshot('video_total',works,categories=['643'])
        with patch.object(tikhub.requests,'request') as transport:
            data=hub.library({'keyword':'AI作品','source':'video_total','category':'643','sort':'likes','page':2,'page_size':20})
            self.assertEqual(data['total'],45);self.assertEqual(len(data['items']),20)
            self.assertEqual(data['items'][0]['digg_count'],24)
            self.assertEqual(hub.library({'page':500,'page_size':20})['page'],3)
            self.assertEqual(len(hub.export_library({}).decode('utf-8-sig').splitlines()),46)
        transport.assert_not_called();self.assertEqual(tikhub.usage(),0)

    def test_native_history_remains_saved_but_is_not_in_default_tikhub_library(self):
        self.snapshot('native',[self.work()],keyword='AI短片',profile='custom')
        self.assertEqual(hub.library()['total'],0)
        self.assertEqual(hub.library({'provider':'all'})['total'],1)

    def test_unknown_fans_do_not_pass_numeric_fan_limit(self):
        self.snapshot('video_total',[self.work(fans=None)],categories=['643'])
        self.assertEqual(hub.library({'max_fans':10000})['total'],0)

    def test_csv_formula_protection(self):
        self.snapshot('video_total',[self.work(title='=DANGEROUS()')],categories=['643'])
        self.assertIn("'=DANGEROUS()",hub.export_library({}).decode('utf-8-sig'))

    def test_local_report_evidence_is_saved_exportable_and_does_not_call_provider(self):
        self.snapshot('video_total',[self.work()],categories=['643'])
        self.snapshot('hot_live',[self.hot(rank=10)],hour=10)
        self.snapshot('hot_live',[self.hot(rank=3,heat=4000)],hour=20)
        with patch.object(tikhub.requests,'request') as transport:
            r=hub.generate_report()
            self.assertEqual(r['coverage']['works'],1);self.assertEqual(r['coverage']['rising_hot'],1)
            self.assertEqual(r['ideas'][0]['reference_id'],self.aid)
            self.assertIn('创意草案',r['ideas'][0]['note'])
            self.assertEqual(hub.reports(r['id'])['id'],r['id'])
            self.assertIn('AI机器人舞蹈挑战',hub.export_report(r['id']).decode())
        transport.assert_not_called();self.assertEqual(tikhub.usage(),0)

    def test_report_does_not_recommend_topics_marked_unpublishable(self):
        self.snapshot('hot_total',[self.hot(allow_publish=False)])
        self.assertEqual(hub.generate_report()['ideas'],[])

    def test_keyword_medians_only_combine_same_board_and_scope(self):
        self.snapshot('video_total',[self.work(id='7703151458754472788',aweme_id='7703151458754472788')],categories=['643'],window=24)
        self.snapshot('video_total',[self.work()],categories=['643'],window=168)
        self.assertEqual(hub.generate_report()['keywords'],[])

    def test_history_does_not_mutate_monitored_accounts(self):
        self.snapshot('video_total',[self.work()],categories=['643'])
        hub.history('works',self.aid);hub.generate_report()
        self.assertEqual(store.q('SELECT COUNT(*) n FROM tracked_accounts')[0]['n'],0)

    def test_sync_quote_respects_each_job_and_shared_daily_limits(self):
        with patch.object(tikhub.requests,'request') as transport:
            p=hub.sync_plan()
        self.assertEqual(p['calls'],11);self.assertEqual(p['daily_requests'],22)
        self.assertEqual(p['estimated_usd'],.011);self.assertTrue(p['allowed'])
        self.assertEqual([plan['calls'] for plan in p['plans']],[5,5,1]);transport.assert_not_called()
        playback.save_settings({'max_calls_per_job':2})
        self.assertFalse(hub.sync_plan()['allowed'])
        playback.save_settings({'max_calls_per_job':5,'daily_call_limit':10})
        self.assertFalse(hub.sync_plan()['allowed'])

    def test_manual_sync_needs_current_quote_and_confirmed_scope(self):
        p=hub.sync_plan()
        with patch.object(douk,'submit') as submit:
            for data in [{},{'confirmed':True,'fingerprint':'old'}, {'confirmed':True,'fingerprint':p['fingerprint'],'pages':3}]:
                with self.assertRaises(ValueError):hub.submit_sync(data)
        submit.assert_not_called()

    def test_repeated_click_does_not_queue_overlapping_paid_round(self):
        p=hub.sync_plan()
        with patch.object(douk,'submit',side_effect=[{'id':'one'},{'id':'two'},{'id':'three'}]),patch.object(douk,'jobs',return_value=[]):
            r=hub.submit_sync({'confirmed':True,'fingerprint':p['fingerprint']})
        with patch.object(douk,'jobs',return_value=[{'id':'one','status':'running'}]),patch.object(douk,'submit') as submit:
            with self.assertRaisesRegex(ValueError,'已有榜单同步'):hub.submit_sync({'confirmed':True,'fingerprint':p['fingerprint']})
        submit.assert_not_called();self.assertTrue(r['id'])

    def test_schedule_claim_is_durable_and_never_replayed_after_failed_submission(self):
        with patch.object(d,'now',return_value=self.at.replace(hour=7)):
            hub.save_config({'enabled':True})
        at=self.at.replace(hour=8,minute=1)
        with patch.object(hub,'submit_sync',side_effect=RuntimeError('network outcome unknown')) as submit:
            hub.tick(at);hub.tick(at)
        self.assertEqual(submit.call_count,1)
        self.assertEqual(store.q("SELECT status FROM discovery_hub_slots WHERE kind='sync'")[0]['status'],'blocked')

    def test_pause_invalidates_queued_schedule_before_next_paid_page(self):
        cfg=hub.save_config({'enabled':True})['settings']
        p=d.plan({'source':'video_total','categories':['643']})
        hub.save_config({'enabled':False})
        with patch.object(tikhub.requests,'request') as transport:
            result=d.execute({'query':p['query'],'confirmed':True,'fingerprint':p['fingerprint'],'hub_revision':cfg['revision']})
        transport.assert_not_called();self.assertFalse(result['ok']);self.assertTrue(result['errors'])

    def test_daily_and_weekly_reports_are_free_and_claimed_once(self):
        monday=self.at-timedelta(days=self.at.weekday())
        with patch.object(hub,'generate_report',return_value={'id':'report'}) as generate,patch.object(hub,'submit_sync') as paid:
            hub.tick(monday);hub.tick(monday)
        self.assertEqual(generate.call_count,2);paid.assert_not_called()
        weekly=next(c.args[0] for c in generate.call_args_list if c.args[0]['kind']=='weekly')
        self.assertEqual(weekly['date'],(monday-timedelta(days=1)).date().isoformat())

    def test_automation_validation(self):
        for cfg in [{'times':['29:10']},{'times':[]},{'categories':['missing']},{'pages':6},{'include_live':'yes'}]:
            with self.subTest(cfg=cfg),self.assertRaises(ValueError):hub.save_config(cfg)
        with self.assertRaises(ValueError):hub.generate_report({'date':(self.at+timedelta(days=1)).date().isoformat()})

    def test_empty_or_last_page_stops_remaining_paid_page_requests(self):
        for objs,total in [([],1000),([{'item_id':self.aid,'item_title':'AI视频'}],1)]:
            with self.subTest(total=total):
                response=Mock(status_code=200,json=lambda:{'code':200,'data':{'page':{'total':total},'objs':objs}},headers={})
                p=d.plan({'source':'video_total','categories':['643'],'pages':5})
                with patch.object(tikhub.requests,'request',return_value=response) as request:
                    result=d.execute({'query':p['query'],'pages':5,'confirmed':True,'fingerprint':p['fingerprint']})
                self.assertEqual(request.call_count,1);self.assertEqual(result['attempted'],1)
                self.assertIsNone(d.cached(p['query'])['snapshot']['next_page'])

    def test_restart_only_claims_latest_due_sync_instead_of_replaying_all_times(self):
        with patch.object(d,'now',return_value=self.at.replace(hour=7)):
            hub.save_config({'enabled':True,'daily_report':False,'weekly_report':False})
        with patch.object(hub,'submit_sync',return_value={'id':'fixture'}) as submit:
            hub.tick(self.at);hub.tick(self.at)
        self.assertEqual(submit.call_count,1)
        self.assertIn('20:00',store.q("SELECT slot FROM discovery_hub_slots WHERE kind='sync'")[0]['slot'])

    def test_report_waits_until_overlapping_sync_finishes(self):
        store.x('INSERT INTO discovery_hub_runs VALUES(?,?,?)',('run',self.at.isoformat(),json.dumps({'jobs':['job'],'errors':[]})))
        with patch.object(douk,'jobs',return_value=[{'id':'job','status':'running'}]),patch.object(hub,'generate_report') as generate:
            hub.tick(self.at)
        generate.assert_not_called()
        self.assertFalse(store.q("SELECT * FROM discovery_hub_slots WHERE kind='daily'"))
        with patch.object(douk,'jobs',return_value=[{'id':'job','status':'completed'}]),patch.object(hub,'generate_report',return_value={'id':'report'}) as generate:
            hub.tick(self.at)
        generate.assert_called_once()

    def test_merging_more_pages_does_not_refresh_old_page_observation_dates(self):
        original=self.work(fetched_at=self.at.replace(hour=10).isoformat())
        self.snapshot('video_total',[original],hour=10,categories=['643'])
        added=self.work(id='7703151458754472788',aweme_id='7703151458754472788',fetched_at=self.at.replace(hour=20).isoformat())
        self.snapshot('video_total',[original,added],hour=20,categories=['643'])
        old=hub.history('works',self.aid)
        self.assertEqual(len(old['history']),1)
        self.assertEqual(old['item']['last_seen'],self.at.replace(hour=10).isoformat())
        self.assertIsNone(old['item']['comparison'])

    def test_duplicate_timer_check_does_not_keep_database_write_lock(self):
        hub.tick(self.at);hub.tick(self.at)
        self.assertFalse(store.get_conn().in_transaction)
        with closing(sqlite3.connect(store.DB_PATH,timeout=.1)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()

    def test_past_report_uses_requested_date_and_does_not_replace_todays_report(self):
        today=hub.generate_report()
        requested=(self.at-timedelta(days=1)).date().isoformat()
        yesterday=hub.generate_report({'kind':'daily','date':requested})
        self.assertEqual(yesterday['id'],'daily-'+requested)
        self.assertNotEqual(today['id'],yesterday['id'])
        self.assertEqual(hub.reports(today['id'])['start_at'],today['start_at'])


if __name__=='__main__':unittest.main()
