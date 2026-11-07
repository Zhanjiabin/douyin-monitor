"""Creative relevance, provenance and free focused reports over saved data."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import creative_focus as creative
import discovery as d
import discovery_hub as hub
import douk
import playback
import store
import tikhub
import media_cache
import intelligence


class ClassificationTests(unittest.TestCase):
    def test_four_lanes_and_named_ips(self):
        for title, lane in [('主角终于发现真相 #AI漫剧', 'comic'), ('扮丑嫁良缘 #真人AIGC #AI短剧', 'drama'),
                            ('云咪vlog #AI创作 #ai小猫', 'pet'), ('渡骸 第一集', 'cinema'),
                            ('南僵 第四集', 'cinema'), ('《南僵》第四集', 'cinema'), ('南僵第四集', 'cinema'),
                            ('山海奇遇 #AI原创电影', 'cinema')]:
            with self.subTest(title=title):
                self.assertIn(lane, creative.classify({'title': title})['lanes'])

    def test_general_news_pets_tutorials_and_query_context_do_not_become_ai_content(self):
        for title in ['猫咪好可爱 #萌宠', '假期出行人山人海', '热门短剧推荐 婆媳大战',
                      'AI电影保姆级教程', 'AI萌宠提示词和工作流', 'Seedance工具测评', 'AI工具发布会',
                      '云南僵蚕 #干货批发', '湖南僵尸村民间故事', '越南僵尸王复仇', '奶猫真可爱']:
            item = {'title': title, 'category': '动漫', 'query': {'keyword': 'AI萌宠'}, 'account_name': 'AI导演'}
            with self.subTest(title=title):
                self.assertFalse(creative.matches(item, 'ai'))

    def test_generic_creative_short_has_a_place_but_not_a_made_up_lane(self):
        item = {'title': '假如未来城市失去了颜色 #AI短片 #原创动画'}
        self.assertTrue(creative.matches(item, 'ai'))
        self.assertFalse(creative.matches(item, 'pet'))
        self.assertIn('other', creative.classify(item)['lanes'])
        with self.assertRaises(ValueError):
            creative.validate('unknown')

    def test_reference_catalog_has_primary_links_and_no_invented_metrics(self):
        refs = creative.catalog()['references']
        self.assertEqual({r['name'] for r in refs}, {'渡骸', '南僵', '猫子柒', '云咪'})
        self.assertTrue(all(r['url'].startswith(('https://www.douyin.com/video/', 'https://www.bilibili.com/video/')) for r in refs))
        self.assertTrue(all('play_count' not in r and 'sec_uid' not in r for r in refs))


class FocusedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.before = store.DB_PATH, playback.SETTINGS, douk.JOBS
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        root = Path(self.tmp.name)
        store.DB_PATH = root / 'test.db'; playback.SETTINGS = root / 'settings.json'; douk.JOBS = root / 'jobs'
        store.init_db(); playback.init(); hub.init()
        self.at = d.now().replace(hour=22, minute=0, second=0, microsecond=0)
        self.clock = patch.object(d, 'now', return_value=self.at)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        if store._CONN:
            store._CONN.close()
        store._CONN = None
        store.DB_PATH, playback.SETTINGS, douk.JOBS = self.before
        self.tmp.cleanup()

    def seed(self, source='video_total', hour=12, **query):
        rows = [{'id': str(7703151458754472700+i), 'title': title, 'account_name': '作者'+str(i), 'rank': i+1,
                 'digg_count': 100-i, 'published_at': self.at.replace(hour=8).isoformat(),
                 'url': 'https://www.douyin.com/video/'+str(7703151458754472700+i)}
                for i, title in enumerate(['第1集 #AI漫剧', '南僵 第一集 #AI剧情大片', '猫子柒喝咖啡', '本地房产营销', 'AI电影提示词教程'])]
        q = d.query({'source': source, **query}); identity = uuid.uuid4().hex
        store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',
                (identity, d.key(q), source, json.dumps(q), json.dumps({'items': rows}), self.at.replace(hour=hour).isoformat(), 'completed', ''))
        hub.ingest(identity)

    def test_focus_filter_is_optional_non_destructive_and_exports_same_scope(self):
        self.seed()
        self.assertEqual(hub.library()['total'], 5)
        self.assertEqual(hub.library({'focus': 'ai'})['total'], 3)
        self.assertEqual(hub.library({'focus': 'cinema'})['total'], 1)
        self.assertEqual(hub.library({'focus': 'comic', 'page': 8, 'page_size': 1})['page'], 1)
        output = hub.export_library({'focus': 'ai'}).decode('utf-8-sig')
        self.assertIn('南僵', output); self.assertNotIn('营销', output); self.assertNotIn('教程', output)
        self.assertEqual(store.q('SELECT COUNT(*) n FROM discovery_entries')[0]['n'], 5)

    def test_named_search_does_not_bypass_relevance_or_mix_scope_comparisons(self):
        self.seed('search', keyword='AI漫剧')
        self.seed('search', hour=18, keyword='AI萌宠')
        rows = hub.library({'focus': 'ai'})['items']
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['comparison'] is None for r in rows))

    def test_focused_reports_preserve_general_reports_and_never_call_paid_services(self):
        self.seed()
        with patch.object(tikhub.requests, 'request', side_effect=AssertionError('No paid calls')), patch.object(douk, 'submit', side_effect=AssertionError('No jobs')):
            general = hub.generate_report()
            focused = hub.generate_report({'focus': 'ai'})
            self.assertNotEqual(general['id'], focused['id'])
            self.assertEqual(hub.reports(general['id'])['coverage']['works'], 5)
            self.assertEqual(focused['coverage']['works'], 3)
            self.assertEqual(focused['ideas'], [])
            self.assertEqual(len(focused['opportunities']), 3)
            self.assertTrue(all('creative' in r for r in focused['references']))
            output = hub.export_report(focused['id']).decode()
            self.assertIn('南僵', output); self.assertNotIn('房产营销', output); self.assertNotIn('提示词教程', output)
        self.assertFalse(store.q('SELECT * FROM tracked_accounts'))

    def test_large_search_is_durable_before_bounded_optional_thumbnail_io(self):
        playback.save_settings({'api_key': 'fixture-only', 'max_calls_per_job': 5, 'daily_call_limit': 100})
        query = {'source': 'search', 'keyword': 'AI漫剧', 'days': 7}
        preview = d.plan(query)
        self.assertTrue(preview['allowed'], preview['reason'])
        works = [{'id': str(7703151458754000000+i), 'aweme_id': str(7703151458754000000+i),
                  'title': '第1集 #AI漫剧', 'cover_url': f'https://p.douyinpic.com/{i}.jpg',
                  'avatar_url': f'https://p.douyinpic.com/avatar-{i}.jpg'} for i in range(1000)]
        def images(urls):
            self.assertLessEqual(len(urls), 24)
            self.assertEqual(hub.library({'focus': 'ai'})['total'], 1000)
            self.assertEqual(store.q('SELECT COUNT(*) n FROM discovery_works')[0]['n'], 1000)
            raise OSError('optional thumbnail filesystem failure')
        with patch.object(tikhub.requests, 'request', side_effect=AssertionError('No live API')), \
             patch.object(tikhub, 'execute', return_value={'attempted': 1, 'receipts': [{'ok': True, 'response': {}}], 'errors': []}), \
             patch.object(d, 'normalize', return_value={'items': works, 'series': [], 'raw_data': {}}), \
             patch.object(media_cache, 'cache_images', side_effect=images) as thumbnails:
            result = d.execute({'query': query, 'confirmed': True, 'fingerprint': preview['fingerprint']})
        self.assertTrue(result['ok']); self.assertEqual(len(result['data']), 1000)
        thumbnails.assert_called_once()

    def account_work(self, uid=''):
        self.seed()
        aid = '7703151458754472700'
        row = store.q('SELECT snapshot_id,value_json FROM discovery_sightings WHERE item_id=?', (aid,))[0]
        item = json.loads(row['value_json'])
        item.update(sec_uid=uid, avatar_path='/media/fixture.jpg', fans=12345)
        store.x('UPDATE discovery_sightings SET value_json=? WHERE snapshot_id=? AND item_id=?',
                (json.dumps(item), row['snapshot_id'], aid))
        return aid

    def test_account_shortcut_reuses_id_without_paid_requests_and_preserves_settings(self):
        uid = 'MS4wLjABAAAA_SHORTCUT_ACCOUNT'
        aid = self.account_work(uid)
        with patch.object(d, 'submit', side_effect=AssertionError('No paid lookup')), patch.object(tikhub.requests, 'request', side_effect=AssertionError('No network')):
            result = hub.monitor_account({'aweme_id': aid})
            self.assertTrue(result['created']); self.assertIsNone(result['job'])
            account = intelligence.list_tracked_accounts()[0]
            self.assertEqual((account['nickname'], account['avatar_path'], account['role'], account['follower_count']), ('作者0', '/media/fixture.jpg', 'peer', 12345))
            intelligence.update_account_settings(uid, '自有账号备注', 'own', False)
            result = hub.monitor_account({'aweme_id': aid})
            self.assertFalse(result['created']); self.assertTrue(result['resumed'])
            account = intelligence.list_tracked_accounts()[0]
            self.assertEqual((account['note'], account['role'], account['enabled']), ('自有账号备注', 'own', 1))
            self.assertEqual(len(intelligence.list_tracked_accounts()), 1)
            self.assertTrue(hub.history('works', aid)['item']['account_monitor']['enabled'])

    def test_missing_author_id_queues_one_lookup_and_reuses_pending_job(self):
        aid = self.account_work()
        playback.save_settings({'api_key': 'fixture-only', 'max_calls_per_job': 5, 'daily_call_limit': 100})
        pending = {'id': 'abcdef1234567890', 'status': 'running', 'endpoint': 'discovery'}
        request = douk.metadata(pending['id']).parent/'request.json'; request.parent.mkdir(parents=True)
        request.write_text(json.dumps({'query': {'source': 'monitor', 'aweme_ids': [aid]}}))
        with patch.object(douk, 'jobs', return_value=[]), patch.object(d, 'submit', return_value=pending) as submit:
            self.assertEqual(hub.monitor_account({'aweme_id': aid})['job']['id'], pending['id'])
            self.assertEqual(submit.call_args.args[0]['aweme_id'], aid)
            self.assertEqual(submit.call_args.args[0]['source'], 'monitor')
            with patch.object(douk, 'jobs', return_value=[pending]):
                self.assertEqual(hub.monitor_account({'aweme_id': aid})['job']['id'], pending['id'])
            submit.assert_called_once()
            with self.assertRaises(ValueError):
                hub.monitor_account({'aweme_id': aid, 'resolve_only': True})
            submit.assert_called_once()  # Poll completion can never start a second paid call.

    def test_shortcut_rejects_spoofed_or_conflicting_author_and_unknown_work(self):
        aid = self.account_work('MS4wLjABAAAA_CACHED_AUTHOR')
        uid = 'MS4wLjABAAAA_BOUND_AUTHOR'
        intelligence.add_account(uid, '保留备注', 'own')
        intelligence.save_account_posts(uid, '已核实作者', [{'aweme_id': aid, 'title': '已监控作品'}])
        with patch.object(d, 'submit', side_effect=AssertionError('No lookup')):
            for data in [{'aweme_id': aid}, {'aweme_id': aid, 'sec_uid': uid}, {'aweme_id': '7700000000000000000'}]:
                with self.assertRaises(ValueError):
                    hub.monitor_account(data)
        self.assertEqual(len(intelligence.list_tracked_accounts()), 1)
        self.assertTrue(hub.history('works', aid)['item']['account_identity_conflict'])

    def test_saved_report_shows_live_account_state_from_verified_video_binding(self):
        aid = self.account_work()
        report = hub.generate_report({'focus': 'ai'})
        uid = 'MS4wLjABAAAA_REPORT_ACCOUNT'
        intelligence.add_account(uid, role='peer')
        intelligence.save_account_posts(uid, '作者0', [{'aweme_id': aid, 'title': '已核实作品'}])
        loaded = hub.reports(report['id'])
        work = next(r for r in loaded['references'] if r['id'] == aid)
        self.assertEqual(work['sec_uid'], uid)
        self.assertTrue(work['account_monitor']['enabled'])
        intelligence.toggle_tracked_account(uid, False)
        work = next(r for r in hub.reports(report['id'])['references'] if r['id'] == aid)
        self.assertFalse(work['account_monitor']['enabled'])
        self.assertEqual(hub.reports(report['id'])['generated_at'], report['generated_at'])


if __name__ == '__main__':
    unittest.main()
