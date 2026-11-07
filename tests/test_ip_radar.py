"""The radar's evidence boundaries, costs, rotating discovery and real data overlay."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import creative_focus as creative
import discovery as d
import discovery_hub as hub
import douk
import intelligence
import ip_radar as radar
import metric_history as mh
import playback
import store
import tikhub


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.before = store.DB_PATH, playback.SETTINGS, douk.JOBS
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        root = Path(self.temp.name)
        store.DB_PATH = root / 'db.sqlite'; playback.SETTINGS = root / 'api.json'; douk.JOBS = root / 'jobs'
        store.init_db(); playback.init(); hub.init(); intelligence.init_account_tables(); radar.init()
        playback.save_settings({'api_key': 'fixture-radar', 'daily_call_limit': 100})
        self.now = d.now().replace(hour=15, minute=0, second=0, microsecond=0)
        self.clock = patch.object(d, 'now', return_value=self.now); self.clock.start()
        self.uid = 'MS4wLjABAAAA_RADAR_FIXTURE'
        self.aid = '7703151458754472787'

    def tearDown(self):
        self.clock.stop()
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS = self.before
        self.temp.cleanup()

    def seed(self, count=4, **overrides):
        titles = ['《雾海来信》第1集 #AI漫剧', '《零号旅店》第1集 #AI真人短剧',
                  '《毛球冒险记》第1集 #AI萌宠', '《纸月》第1集 #AI原创电影']
        rows = [{'id': str(int(self.aid)+i), 'aweme_id': str(int(self.aid)+i), 'title': titles[i % 4],
                 'account_name': '作者'+str(i), 'avatar_url': 'https://example.com/'+str(i), 'source': 'search',
                 'digg_count': 1000+i, 'fans': 3000, 'published_at': (self.now-timedelta(days=2)).isoformat(),
                 'first_seen': self.now.isoformat(), 'last_seen': self.now.isoformat(),
                 'url': 'https://www.douyin.com/video/'+str(int(self.aid)+i), **overrides} for i in range(count)]
        q = d.query({'source': 'search', 'keyword': 'AI漫剧', 'days': 7})
        ident = uuid.uuid4().hex
        store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)', (ident, d.key(q), 'search', json.dumps(q), json.dumps({'items': rows}),
                                                                           self.now.isoformat(), 'completed', ''))
        hub.ingest(ident)
        return rows

    def detail(self, aid=None, uid=None):
        return {'code': 200, 'request_id': 'detail-fixture', 'data': {'aweme_detail': {
            'aweme_id': aid or self.aid, 'desc': '《雾海来信》第1集 #AI漫剧',
            'create_time': int((self.now-timedelta(days=2)).timestamp()),
            'author': {'sec_uid': uid or self.uid, 'nickname': '核实作者', 'follower_count': 5000},
            'statistics': {'play_count': 0, 'digg_count': 1200, 'comment_count': 80, 'collect_count': 200, 'share_count': 60},
            'video': {'duration': 45000, 'cover': {'url_list': ['https://example.com/cover.jpg']}},
            'mix_info': {'mix_id': '123456789012', 'mix_name': '雾海来信'}}}}

    def response(self, body):
        return Mock(status_code=200, json=lambda: body, headers={})

    def test_new_series_not_whitelisted_and_original_reaction_separate(self):
        rows = self.seed(2, sec_uid=self.uid)
        rows[1]['title'] = '《雾海来信》第2集 #AI漫剧'
        rows.append({**rows[0], 'id': '7703151458754472990', 'title': '《雾海来信》剧情解说 #AI漫剧'})
        for r in rows:
            r['creative'] = creative.classify(r)
        groups = radar.groups(rows)
        self.assertEqual(len(groups), 2)
        original = next(g for g in groups if g['kind'] == 'narrative')
        self.assertEqual(original['name'], '雾海来信'); self.assertEqual(original['works'], 2)
        self.assertEqual(original['state'], 'candidate')
        self.assertTrue(original['identity_verified'])
        self.assertNotIn(original['name'], {r['name'] for r in creative.REFERENCES})

    def test_duplicate_nicknames_do_not_verify_or_merge_authors(self):
        rows = self.seed(2, account_name='同名作者')
        rows[1]['title'] = rows[0]['title']
        for r in rows:
            r['creative'] = creative.classify(r)
        groups = radar.groups(rows)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(not r['identity_verified'] for r in groups))

    def test_old_newly_found_reference_is_never_recent_hot(self):
        self.seed(1, published_at=(self.now-timedelta(days=80)).isoformat())
        self.assertEqual(radar.library()['total'], 0)
        self.assertEqual(radar.library({'state': 'classic'})['items'][0]['state'], 'classic')
        self.assertFalse(radar.targets(radar.works(), radar.config()))

    def test_detail_then_single_statistics_fills_real_fields_without_following_account(self):
        self.seed(1)
        quote = radar.plan({'action': 'enrich', 'ids': [self.aid]})
        stats = {'code': 200, 'request_id': 'stats-fixture', 'data': {'statistics_list': [
            {'aweme_id': self.aid, 'play_count': 145678, 'digg_count': 1300, 'comment_count': None, 'collect_count': None, 'share_count': 65}]}}
        with patch.object(tikhub.requests, 'request', side_effect=[self.response(self.detail()), self.response(stats)]) as transport:
            progress = []
            result = radar.execute({'radar_plan': quote, 'confirmed': True}, progress.append)
        self.assertTrue(result['ok']); self.assertFalse(result['errors'])
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(transport.call_args_list[1].args[1].endswith('/fetch_video_statistics'))
        self.assertEqual(transport.call_args_list[1].kwargs['params'], [('aweme_ids', self.aid)])
        item = hub.library({'focus': 'ai'})['items'][0]
        self.assertEqual(item['play_count'], 145678); self.assertEqual(item['comment_count'], 80)
        self.assertEqual(item['collect_count'], 200); self.assertEqual(item['digg_count'], 1300)
        self.assertEqual(item['sec_uid'], self.uid); self.assertEqual(item['play_scope'], 'organic')
        self.assertEqual(item['duration'], 45); self.assertEqual(item['play_status'], 'available')
        self.assertEqual(len(hub.history('works', self.aid)['metric_history']), 2)
        self.assertEqual(store.q('SELECT COUNT(*) n FROM tracked_accounts')[0]['n'], 0)
        self.assertEqual(store.q('SELECT COUNT(*) n FROM tracked_account_videos')[0]['n'], 0)
        self.assertEqual(radar.usage(), 2)
        self.assertEqual(progress[-1]['done'], 2)

    def test_wrong_id_or_identity_is_not_written(self):
        self.seed(1)
        with self.assertRaisesRegex(ValueError, 'ID 不匹配'):
            radar.save_detail(self.detail(aid='7703151458754472999'), self.aid, radar.stamp(), 'wrong')
        radar.save_detail(self.detail(), self.aid, radar.stamp(), 'good')
        with self.assertRaisesRegex(ValueError, '冲突'):
            radar.save_detail(self.detail(uid=self.uid+'B'), self.aid, radar.stamp(), 'conflict')
        self.assertEqual(len(store.q('SELECT * FROM radar_details')), 1)

    def test_metric_growth_never_mixes_source_scope_or_short_intervals(self):
        event = lambda hours, count, scope='organic': {'observed_at': radar.stamp((self.now-timedelta(hours=hours)).isoformat()),
                                                   'source': 'tikhub_app', 'scope': scope, 'play_count': count}
        self.assertIsNone(radar.growth([event(24, 1000, 'total'), event(0, 2000)], self.now))
        self.assertIsNone(radar.growth([event(.2, 1000), event(0, 2000)], self.now))
        g = radar.growth([event(24, 1000), event(0, 3000)], self.now)
        self.assertEqual(g['per_day'], 2000); self.assertEqual(g['percent'], 200)
        g = radar.growth([event(24, 3000), event(0, 1000)], self.now)
        self.assertEqual(g['status'], 'correction'); self.assertIsNone(g['per_day'])

    def test_overlay_preserves_newer_search_metric_and_historical_cutoff(self):
        self.seed(1, digg_count=9000)
        old = radar.stamp((self.now-timedelta(days=1)).isoformat())
        radar.save_detail(self.detail(), self.aid, old, 'old-detail')
        mh.record({'aweme_id': self.aid, 'play_count': 50000}, source='tikhub_app', scope='organic', observed_at=old, event_key='old-plays')
        latest = hub.library({'focus': 'ai'})['items'][0]
        self.assertEqual(latest['digg_count'], 9000)
        self.assertEqual(latest['play_count'], 50000)
        before = radar.overlay([{'id': self.aid}], until=(self.now-timedelta(days=2)).isoformat())[0]
        self.assertNotIn('play_count', before)
        self.assertNotIn('sec_uid', before)

    def test_rotation_expansion_is_bounded_and_not_every_hashtag(self):
        rows = self.seed(2, sec_uid=self.uid)
        rows[1]['title'] = '《雾海来信》第2集 #AI漫剧'
        for r in rows:
            r['creative'] = creative.classify(r)
        cfg = radar.config()
        pool = radar.keyword_pool(rows, cfg)
        self.assertIn('雾海来信', [p['keyword'] for p in pool])
        self.assertNotIn('AI漫剧', [p['keyword'] for p in pool if p['origin'] != '方向词'])
        for i, key in enumerate(cfg['keywords'][:3]):
            store.x('INSERT INTO radar_attempts VALUES(?,?,?,?,?,?,?)', (str(i), radar.stamp(), '', 'search', key, 'completed', ''))
        self.assertEqual(radar.keyword_pool(rows, cfg)[0]['keyword'], '雾海来信')

    def test_lane_diversity_and_daily_circuit_breaker(self):
        self.seed(40)
        cfg = {**radar.config(), 'watch_limit': 4}
        chosen = radar.targets(radar.works(), cfg)
        self.assertEqual({r['creative']['lanes'][0] for r in chosen}, {'comic', 'drama', 'pet', 'cinema'})
        self.assertEqual(len(radar.plan({'action': 'cycle'})['tasks']), 5)
        for i in range(30):
            store.x('INSERT INTO radar_attempts VALUES(?,?,?,?,?,?,?)', (str(i), radar.stamp(), '', 'detail', 'x', 'failed', 'fixture'))
        self.assertFalse(radar.plan({'action': 'cycle'})['allowed'])

    def test_no_automatic_retry_after_error_and_pause_stops_in_flight_job(self):
        self.seed(1)
        quote = radar.plan({'action': 'enrich', 'ids': [self.aid]})
        with patch.object(tikhub.requests, 'request', return_value=Mock(status_code=429, json=lambda: {'code': 429}, headers={})) as transport:
            result = radar.execute({'radar_plan': quote, 'confirmed': True})
        self.assertEqual(transport.call_count, 1); self.assertTrue(result['errors'])
        tasks = radar.task_list('cycle', [], radar.config(), radar.works())
        self.assertFalse(any(t['kind'] == 'detail' for t in tasks))
        quote = radar.plan({'action': 'enrich', 'ids': [self.aid]})
        def pause(*_, **__):
            radar.save_config({'enabled': False})
            return self.response(self.detail())
        with patch.object(tikhub.requests, 'request', side_effect=pause) as transport:
            result = radar.execute({'radar_plan': quote, 'confirmed': True})
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(result['errors'])

    def test_tick_claims_slot_once_and_enable_requires_explicit_confirmation(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, '确认'):
            radar.save_config({'enabled': True})
        radar.save_config({'enabled': True, 'confirmed': True})
        with patch.object(radar, 'submit', return_value={'id': 'fixture-job'}) as submit:
            self.assertEqual(radar.tick(self.now)['id'], 'fixture-job')
            self.assertIsNone(radar.tick(self.now+timedelta(minutes=15)))
            self.assertEqual(submit.call_count, 1)
        radar.save_config({'enabled': False})
        with patch.object(radar, 'submit', side_effect=AssertionError('Must not submit')):
            self.assertIsNone(radar.tick(self.now+timedelta(days=1)))

    def test_old_reports_cannot_read_future_enrichment(self):
        self.seed(1)
        report = hub.generate_report({'kind': 'daily', 'date': self.now.date().isoformat(), 'focus': 'ai'})
        self.assertIn('radar', report)
        self.assertEqual(report['radar']['coverage']['play_count'], 0)
        mh.record({'aweme_id': self.aid, 'play_count': 99999}, source='tikhub_app', scope='organic', observed_at=(self.now+timedelta(hours=1)).isoformat(), event_key='future')
        report2 = hub.generate_report({'kind': 'daily', 'date': self.now.date().isoformat(), 'focus': 'ai'})
        self.assertEqual(report2['radar']['coverage']['play_count'], 0)

    def test_multi_selection_is_single_requests_not_batch_even_above_old_job_cap(self):
        self.seed(7)
        quote = radar.plan({'action': 'enrich', 'ids': [str(int(self.aid)+i) for i in range(7)]})
        self.assertTrue(quote['allowed']); self.assertEqual(quote['calls'], 14)
        self.assertFalse(any('multi' in e for e in quote['endpoints']))
        self.assertTrue(all(len(radar.request_for(t)['params'].values()) == 1 for t in quote['tasks']))
        stale = {**quote, 'tasks': quote['tasks'][:1], 'calls': 1}
        with self.assertRaisesRegex(ValueError, '校验失败'):
            radar.execute({'radar_plan': stale, 'confirmed': True})

    def test_fresh_search_does_not_replace_the_existing_observation_cohort(self):
        self.seed(4)
        items = radar.works(); cfg = {**radar.config(), 'watch_limit': 4}
        chosen = radar.targets(items, cfg)
        for item in chosen:
            store.x('INSERT INTO radar_auto_watch VALUES(?,?,?)', (item['id'], radar.stamp(), radar.stamp((self.now+timedelta(days=7)).isoformat())))
        new = []
        for i, item in enumerate(items):
            new.append({**item, 'id': str(7703151458754472990+i), 'digg_count': 9999999,
                        'account_name': '新热门作者'+str(i)})
        self.assertEqual({r['id'] for r in radar.targets(items+new, cfg)}, {r['id'] for r in chosen})
        for item in chosen:
            store.x('UPDATE radar_auto_watch SET expires_at=? WHERE aweme_id=?', (radar.stamp((self.now-timedelta(hours=1)).isoformat()), item['id']))
        self.assertEqual({r['id'] for r in radar.targets(items+new, cfg)}, {r['id'] for r in new})

    def test_pet_watching_movie_is_not_an_ai_feature_film(self):
        title = '姐姐带猫咪去看露天电影 #AI萌宠 #猫咪拟人'
        result = creative.classify({'title': title})
        self.assertIn('pet', result['lanes']); self.assertNotIn('cinema', result['lanes'])
        for title in ['《纸村》第1集 #AIGC #中式恐怖', '《火星来信》 #AI原创电影', '海底归来 #AI科幻大片']:
            self.assertIn('cinema', creative.classify({'title': title})['lanes'])

    def test_repeated_promotional_tags_do_not_become_ip_keywords(self):
        self.seed(2, title='【商城补贴】故事开始 #AI漫剧 #商城补贴 #漫剧推荐 #原创动画 #模仿 #本作品使用LibTV制作',
                  account_name='故事作者', avatar_url='https://example.com/same.jpg')
        items=radar.works()
        for row in items:
            self.assertFalse(radar.names(row))
        self.assertTrue(all(k['origin']=='方向词' for k in radar.keyword_pool(items,radar.config())))

    def test_ready_first_and_explicit_coverage_filters(self):
        self.seed(4)
        radar.save_detail(self.detail(),self.aid,radar.stamp(),'ready-detail')
        mh.record({'aweme_id':self.aid,'play_count':120000},source='tikhub_app',scope='organic',observed_at=radar.stamp(),event_key='ready-play')
        self.assertEqual(hub.library({'sort':'quality'})['items'][0]['id'],self.aid)
        self.assertEqual(hub.library({'data_status':'available'})['total'],1)
        self.assertEqual(hub.library({'data_status':'pending'})['total'],3)
        radar.pin({'id':self.aid,'enabled':True})
        self.assertEqual(hub.library({'data_status':'watching'})['items'][0]['id'],self.aid)
        radar.pin({'id':self.aid,'enabled':False})
        self.assertEqual(hub.library({'data_status':'watching'})['total'],0)


if __name__ == '__main__':
    unittest.main()
