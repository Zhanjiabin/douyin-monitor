import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import playback
import intelligence
import insights
import store


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db,self.old_settings=store.DB_PATH,playback.SETTINGS
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH=Path(self.tmp.name)/'test.db'
        playback.SETTINGS=Path(self.tmp.name)/'private.json'
        self.aid='7703151458754472787'
        self.other='7703151458754472788'
        self.uid='MS4wLjABAAAA_PLAYBACK_TEST'
        intelligence.add_account(self.uid,'Playback','own')
        intelligence.save_account_posts(self.uid,'Playback',[{'aweme_id':self.aid,'title':'已跟踪作品'}])
        playback.init()

    def tearDown(self):
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH,self.old_db=self.old_db,store.DB_PATH
        playback.SETTINGS=self.old_settings
        self.tmp.cleanup()

    def configured(self,**limits):
        playback.save_settings({'api_key':'private-unit-test-key',**limits})

    def body(self,count=123,aid=None):
        return {'code':200,'request_id':'receipt-1','data':{'statistics_list':[{'aweme_id':aid or self.aid,'play_count':count,'digg_count':20}]}}

    def test_preview_is_offline_and_requires_key(self):
        with patch.object(playback.requests,'get') as call:
            result=playback.plan('app',[self.aid,self.other,self.aid])
        call.assert_not_called()
        self.assertEqual((result['videos'],result['calls'],result['allowed']),(2,2,False))

    def test_limits_and_unknown_ids_are_not_sent(self):
        self.configured(max_calls_per_job=1,daily_call_limit=1)
        self.assertFalse(playback.plan('xingtu',[self.aid,self.other])['allowed'])
        with self.assertRaises(ValueError):playback.plan('app',['https://example.com/invalid'])
        with self.assertRaises(ValueError):playback.parse(self.body(100,self.other),'app',[self.aid])

    def test_key_never_appears_in_public_settings(self):
        self.configured()
        self.assertTrue(playback.settings()['key_set'])
        self.assertNotIn('private-unit-test-key',json.dumps(playback.status()))
        playback.save_settings({'api_key':'','daily_call_limit':20})
        self.assertTrue(playback.settings()['key_set'])
        playback.save_settings({'clear_key':True})
        self.assertFalse(playback.settings()['key_set'])

    def test_confirmation_and_missing_key_never_send_request(self):
        with patch.object(playback.requests,'get') as call:
            with self.assertRaises(ValueError):playback.execute({'mode':'app','video_ids':[self.aid]},lambda _:None)
            with self.assertRaises(ValueError):playback.execute({'mode':'app','video_ids':[self.aid],'allow_charges':True},lambda _:None)
        call.assert_not_called()

    def test_network_failure_records_attempt_and_does_not_retry(self):
        self.configured()
        with patch.object(playback.requests,'get',side_effect=playback.requests.Timeout('test')) as call:
            result=playback.execute({'mode':'xingtu','video_ids':[self.aid,self.other],'allow_charges':True},lambda _:None)
        self.assertEqual(call.call_count,1)
        self.assertFalse(result['ok'])
        self.assertEqual(playback.status()['requests'][0]['status'],'failed')
        self.assertEqual(len(playback.status()['requests']),1)

    def test_response_identity_and_duplicate_checks_are_atomic(self):
        body=self.body(100)
        body['data']['statistics_list'].append(body['data']['statistics_list'][0])
        with self.assertRaises(ValueError):playback.parse(body,'app',[self.aid])
        self.assertEqual(store.q('SELECT * FROM playback_measurements'),[])

    def test_zero_placeholder_does_not_replace_positive_or_create_unknown_work(self):
        positive=playback.parse(self.body(900),'app',[self.aid])
        playback.persist(positive)
        playback.persist(playback.parse(self.body(0),'app',[self.aid]))
        playback.persist(playback.parse(self.body(800,self.other),'app',[self.other]))
        rows=store.q('SELECT * FROM playback_measurements')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['play_count'],900)
        self.assertEqual(insights.dashboard()['videos'][0]['play_count'],900)
        for value in [0,None,False,-3,'invalid']:
            self.assertIsNone(playback.parse(self.body(value),'app',[self.aid])[0]['play_count'])

    def test_xingtu_requires_matching_identity_and_keeps_paid_scope(self):
        body={'code':200,'params':{'item_id':self.aid},'data':{'exist':True,'watch_cnt':1234,'stats':{'like_cnt':10}}}
        result=playback.parse(body,'xingtu',[self.aid])[0]
        self.assertEqual((result['play_count'],result['scope'],result['source']),(1234,'total','tikhub_xingtu'))
        body['params']['item_id']=self.other
        with self.assertRaises(ValueError):playback.parse(body,'xingtu',[self.aid])

    def test_success_persists_request_id_and_budget(self):
        self.configured(daily_call_limit=1)
        response=Mock(status_code=200)
        response.json.return_value=self.body()
        with patch.object(playback.requests,'get',return_value=response) as call:
            result=playback.execute({'mode':'app','video_ids':[self.aid],'allow_charges':True},lambda _:None)
        self.assertEqual(result['playback'],1)
        self.assertFalse(call.call_args.kwargs['allow_redirects'])
        self.assertEqual(playback.status()['requests'][0]['request_id'],'receipt-1')
        self.assertFalse(playback.plan('app',[self.aid])['allowed'])

    def test_different_sources_and_scopes_do_not_produce_growth(self):
        playback.persist(playback.parse(self.body(900),'app',[self.aid]))
        store.x("UPDATE playback_measurements SET fetch_date=date(fetch_date,'-1 day')")
        body={'code':200,'data':{'item_id':self.aid,'watch_cnt':1800}}
        playback.persist(playback.parse(body,'xingtu',[self.aid]))
        row=insights.dashboard()['videos'][0]
        self.assertEqual(row['play_count'],1800)
        self.assertIsNone(row['daily_growth'])
        self.assertIsNone(row['period_delta'])


if __name__=='__main__':unittest.main()
