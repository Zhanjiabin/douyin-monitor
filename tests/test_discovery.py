"""Discovery contracts, budget boundaries, identity and private authorization."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import corpus
import discovery as d
import douk
import intelligence
import playback
import store
import tikhub


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.old=store.DB_PATH,playback.SETTINGS,douk.JOBS
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH=self.root/'test.db';playback.SETTINGS=self.root/'api.json';douk.JOBS=self.root/'jobs'
        store.init_db();d.init();corpus.init();playback.init()
        playback.save_settings({'api_key':'fixture-discovery-key','max_calls_per_job':5,'daily_call_limit':20})
        self.aid='7703151458754472787';self.uid='MS4wLjABAAAA_DISCOVERY_TEST'

    def tearDown(self):
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH,playback.SETTINGS,douk.JOBS=self.old
        self.temp.cleanup()

    def response(self,data):
        return Mock(status_code=200,json=lambda:{'code':200,'request_id':'fixture','data':data},headers={})

    def perform(self,q,data):
        p=d.plan(q)
        with patch.object(tikhub.requests,'request',return_value=self.response(data)) as call:
            result=d.execute({'query':p['query'],'pages':p['pages'],'page_start':p['page_start'],'confirmed':True,'fingerprint':p['fingerprint']})
        return result,call

    def board(self):
        return {'code':0,'data':{'page':{'total':90,'page':1,'page_size':20},'objs':[{
            'item_id':self.aid,'item_title':'末日测试','nick_name':'测试作者','fans_cnt':5000,
            'play_cnt':500000,'like_cnt':6000,'publish_time':1793500000,'score':80000}]}}

    def test_cached_search_catalog_and_planning_are_offline(self):
        with patch.object(tikhub.requests,'request') as transport:
            self.assertIsNone(d.cached({'source':'lowfan'})['snapshot'])
            self.assertEqual(d.plan({'source':'lowfan'})['calls'],1)
            self.assertGreater(len(d.catalog()['categories']),30)
        transport.assert_not_called()

    def test_all_product_routes_compile_against_pinned_spec(self):
        intelligence.add_account(self.uid,'测试','peer')
        cases=[{'source':s} for s in ['lowfan','completion','likes','followers','rising','topics','searches']]
        cases += [{'source':'search','keyword':'末日'},{'source':'industry','category':'607'},
                  {'source':'portrait','sec_uid':self.uid},{'source':'monitor','aweme_id':self.aid}]
        for q in cases:
            with self.subTest(q=q):self.assertTrue(d.plan(q)['allowed'])

    def test_keyword_special_endpoint_requires_opt_in_and_single_word(self):
        self.assertFalse(d.plan({'source':'keyword','keyword':'末日'})['allowed'])
        d.save_settings({'allow_keyword_trends':True})
        p=d.prepare({'source':'keyword','keyword':'末日'})
        self.assertTrue(p['allowed']);self.assertEqual(p['calls'],1)
        self.assertEqual(p['requests'][0]['params']['keyword_list'],'末日')
        with self.assertRaises(ValueError):d.plan({'source':'keyword','keyword':'末日,重生'})
        self.assertFalse(playback.settings()['allow_batch_statistics'])

    def test_search_index_is_never_stored_as_playback(self):
        result,_=self.perform({'source':'search','keyword':'末日'},{'data':[{
            'itemId':self.aid,'title':'测试','index':'123456','likes':'1200','fans':90,'nickname':'作者'}]})
        row=result['data'][0]
        self.assertIsNone(row['play_count']);self.assertIsNone(row['board_play_count'])
        self.assertEqual(row['index'],123456)
        self.assertFalse(store.q('SELECT * FROM tracked_account_videos'))

    def test_board_window_count_and_snapshot_time_are_preserved(self):
        result,call=self.perform({'source':'lowfan'},self.board())
        self.assertTrue(result['ok']);call.assert_called_once()
        row=d.cached({'source':'lowfan'})['snapshot']['items'][0]
        self.assertEqual(row['board_play_count'],500000);self.assertIsNone(row['play_count'])
        self.assertTrue(row['fetched_at']);self.assertEqual(row['id'],self.aid)
        self.assertEqual(tikhub.usage(),1)

    def test_changed_or_unconfirmed_input_never_calls_transport(self):
        p=d.plan({'source':'lowfan'})
        with patch.object(tikhub.requests,'request') as transport:
            with self.assertRaises(ValueError):d.execute({'query':p['query'],'fingerprint':p['fingerprint']})
            with self.assertRaises(ValueError):d.execute({'query':{'source':'likes'},'confirmed':True,'fingerprint':p['fingerprint']})
        transport.assert_not_called()

    def test_nested_provider_error_is_not_an_empty_success(self):
        result,_=self.perform({'source':'lowfan'},{'code':401,'data':{},'message':'denied'})
        self.assertFalse(result['ok']);self.assertTrue(result['errors'])
        self.assertIsNone(d.cached({'source':'lowfan'})['snapshot'])

    def test_partial_industry_result_stops_and_preserves_evidence(self):
        p=d.plan({'source':'industry','category':'607'})
        good=self.response({'data':{'trend':[{'date':'20261101','play_count':100}]}})
        bad=Mock(status_code=503,json=lambda:{'code':503},headers={})
        with patch.object(tikhub.requests,'request',side_effect=[good,bad]) as transport:
            result=d.execute({'query':p['query'],'confirmed':True,'fingerprint':p['fingerprint']})
        self.assertEqual(transport.call_count,2);self.assertTrue(result['ok']);self.assertTrue(result['errors'])
        snap=d.cached(p['query'])['snapshot'];self.assertEqual(snap['status'],'partial');self.assertTrue(snap['series'])

    def test_daily_and_per_job_budgets_cover_combined_industry_calls(self):
        playback.save_settings({'max_calls_per_job':1})
        self.assertFalse(d.plan({'source':'industry','category':'607'})['allowed'])
        playback.save_settings({'max_calls_per_job':5,'daily_call_limit':1})
        self.assertFalse(d.plan({'source':'industry','category':'607'})['allowed'])

    def test_post_pagination_is_explicit_and_search_has_no_fake_pages(self):
        p=d.prepare({'source':'topics','pages':3})
        self.assertEqual(p['calls'],3)
        self.assertEqual([json.loads(r['body_json'])['page'] for r in p['requests']],[1,2,3])
        with self.assertRaises(ValueError):d.plan({'source':'search','keyword':'末日','pages':2})
        with self.assertRaises(ValueError):d.plan({'source':'lowfan','page_start':2})

    def own(self):
        intelligence.add_account(self.uid,'自有','own')
        intelligence.save_account_posts(self.uid,'自有',[{'aweme_id':self.aid,'title':'我的作品'}])

    def test_monitor_verifies_video_identity_before_import(self):
        result,_=self.perform({'source':'monitor','aweme_id':self.aid},{'aweme_detail':{'aweme_id':'7703151458754472788','author':{'sec_uid':self.uid}}})
        self.assertFalse(result['ok']);self.assertFalse(store.q('SELECT * FROM tracked_accounts'))

    def test_monitor_imports_verified_author_and_preserves_existing_role(self):
        intelligence.add_account(self.uid,'自己','own')
        raw={'aweme_id':self.aid,'desc':'测试','author':{'sec_uid':self.uid,'nickname':'自己'},'statistics':{'play_count':100},'video':{}}
        result,_=self.perform({'source':'monitor','aweme_id':self.aid},{'aweme_detail':raw})
        self.assertTrue(result['ok']);self.assertEqual(store.q('SELECT role FROM tracked_accounts')[0]['role'],'own')
        self.assertEqual(store.q('SELECT aweme_id FROM tracked_account_videos')[0]['aweme_id'],self.aid)

    def test_hot_rank_changes_can_be_negative_and_index_is_not_playback(self):
        rows=d.parse_hot({'objs':[{'sentence':'测试热点','rank':3,'rank_diff':-2,'hot_score':500}]},'rising')
        self.assertEqual(rows[0]['rank_change'],-2);self.assertEqual(rows[0]['heat'],500)
        self.assertNotIn('play_count',rows[0])

    def test_zero_valid_series_values_survive_and_missing_values_do_not_become_zero(self):
        series=d.leaf_series({'trend':[{'key':'00:01','value':0},{'key':'00:02','value':None},{'key':'00:03','value':.5}]})
        self.assertEqual([p['metrics']['value'] for p in series[0]['points']],[0,None,.5])

    def test_cancelled_pages_keep_partial_receipts_without_skipping_a_page(self):
        p=d.plan({'source':'lowfan','pages':3})
        body={'data':{'page':{'total':100},'objs':[{'item_id':self.aid,'item_title':'one'}]}}
        calls=[]
        def response(*args,**kwargs):
            calls.append(1)
            return self.response(body)
        with patch.object(tikhub.requests,'request',side_effect=response):
            result=d.execute({'query':p['query'],'pages':3,'confirmed':True,'fingerprint':p['fingerprint']},cancelled=lambda:bool(calls))
        self.assertTrue(result['cancelled'])
        self.assertEqual(len(calls),1)
        saved=d.cached(p['query'])['snapshot']
        self.assertEqual(saved['status'],'partial')
        self.assertIsNone(saved['next_page'])

    def test_series_export_keeps_units_zero_gaps_and_escapes_csv_formulas(self):
        import csv,io
        q=d.query({'source':'industry','category':'607'})
        payload={'series':[{'name':'=formula','unit':'provider_rate','points':[{'x':'20261101','metrics':{'rate':0}},{'x':'20261102','metrics':{'rate':None}}]}]}
        store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',('export',d.key(q),q['source'],json.dumps(q),json.dumps(payload),d.now().isoformat(),'completed',''))
        output,mime=d.export(q)
        rows=list(csv.reader(io.StringIO(output.decode('utf-8-sig'))))
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[1][0],"'=formula")
        self.assertEqual(rows[1][3:5],['0','provider_rate'])
        self.assertEqual(rows[2][3],'')
        self.assertIn('csv',mime)

    def test_schedule_claims_once_and_pause_blocks_queued_execution(self):
        q={'source':'lowfan'}
        rules=d.save_rule({'query':q,'interval_hours':1,'enabled':True})['items']
        future=d.now()+timedelta(hours=2)
        with patch.object(douk,'submit',return_value={'id':'fixture-job'}) as submit:
            d.tick(future);d.tick(future)
        submit.assert_called_once();request=submit.call_args.args[0]
        self.assertTrue(d.rule_allowed(request))
        d.save_rule({'id':rules[0]['id'],'enabled':False})
        self.assertFalse(d.rule_allowed(request))

    def test_failed_schedule_slot_is_not_automatically_retried(self):
        d.save_rule({'query':{'source':'lowfan'},'interval_hours':1,'enabled':True})
        future=d.now()+timedelta(hours=2)
        with patch.object(douk,'submit',side_effect=RuntimeError('interrupted')) as submit:
            d.tick(future);d.tick(future)
        submit.assert_called_once();self.assertEqual(store.q('SELECT status FROM discovery_slots')[0]['status'],'blocked')

    def test_real_topic_and_search_field_names_are_not_dropped(self):
        topic=d.parse_hot({'objs':[{'challenge_id':'123','challenge_name':'动漫解说','play_cnt':900,'publish_cnt':25,'score':100,'real_rank':3}]},'topics')[0]
        self.assertEqual((topic['title'],topic['topic_plays'],topic['publish_count'],topic['rank']),('动漫解说',900,25,3))
        search=d.parse_hot({'objs':[{'key_word':'末日兼职','search_score':1200}]},'searches')[0]
        self.assertEqual((search['title'],search['heat']),('末日兼职',1200))

    def test_portrait_ratios_and_tgi_indices_have_different_units(self):
        node={'portrait':{'portrait_data':[{'name':'female','value':.7}]},'portrait_tgi':{'portrait_data':[{'name':'female','value':157.2}]}}
        result=d.normalize({'data':node},{'source':'portrait'})
        self.assertEqual([s['unit'] for s in result['series']],['ratio','index'])
        self.assertEqual(result['series'][1]['points'][0]['metrics']['value'],157.2)

    def test_board_duration_is_milliseconds_and_industry_rate_is_not_a_count(self):
        row=d.parse_works([{'item_id':self.aid,'item_duration':11981}],'completion')[0]
        self.assertEqual(row['duration'],11.981)
        result=d.normalize({'data':{'supply_trend':[{'date':'20261101','rate':.006}]}},{'source':'industry'})
        self.assertEqual(result['series'][0]['unit'],'provider_rate')
        self.assertEqual(result['series'][0]['points'][0]['metrics'],{'rate':.006})

    def test_keyword_provider_tree_yields_index_points(self):
        result=d.normalize({'data':{'hot_list':[{'keyword':'末日','hot_list':[{'datetime':'20261101','index':'1200'}]}]}},{'source':'keyword'})
        self.assertEqual(result['series'][0]['points'][0]['metrics'],{'index':1200})

    def test_two_call_industry_job_can_finish_at_exact_daily_budget(self):
        playback.save_settings({'daily_call_limit':2})
        p=d.plan({'source':'industry','category':'607'})
        response=self.response({'data':{'trend':[{'date':'20261101','rate':.1}]}})
        with patch.object(tikhub.requests,'request',return_value=response) as call:
            result=d.execute({'query':p['query'],'confirmed':True,'fingerprint':p['fingerprint']})
        self.assertEqual(call.call_count,2);self.assertFalse(result['errors'])

    def test_removing_tracked_keyword_pauses_its_paid_schedule(self):
        d.save_settings({'allow_keyword_trends':True})
        watch=d.watches({'keyword':'末日'})['items'][0]
        d.save_rule({'query':{'source':'keyword','keyword':'末日'},'enabled':True})
        d.watches({'delete':watch['id']})
        self.assertEqual(store.q('SELECT enabled FROM discovery_rules')[0]['enabled'],0)

    def test_comparison_rejects_scope_date_and_stale_mismatches(self):
        base={'sec_uid':self.uid,'age_band':'8–30天','play_source':'app','play_scope':'natural','play_date':'2026-11-04','play_count':100,'stale':False}
        rows=[{**base,'aweme_id':self.aid}]+[{**base,'aweme_id':str(int(self.aid)+i+1),'play_count':200} for i in range(5)]
        rows += [{**base,'aweme_id':'extra1','play_scope':'paid','play_count':99999},{**base,'aweme_id':'extra2','play_date':'2026-11-03'},{**base,'aweme_id':'extra3','stale':True}]
        result=d.comparison(self.aid,rows)
        self.assertEqual(result['sample_count'],5);self.assertEqual(result['ratio'],.5)

    def test_product_has_no_creator_cookie_or_private_analysis_operations(self):
        sources={s['id'] for s in d.catalog()['sources']}
        self.assertTrue(sources.isdisjoint({'play_source','retention','bounce','like_points'}))
        with self.assertRaises(ValueError):d.plan({'source':'retention','aweme_id':self.aid})


if __name__=='__main__':unittest.main()
