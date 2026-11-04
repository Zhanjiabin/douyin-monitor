"""Interaction regression checks using isolated data and mocked provider transport."""
import json
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import Mock, patch
from urllib.parse import unquote

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import app
import corpus
import douk
import intelligence
import integrations
import monitoring
import playback
import store
import tikhub

OUT = ROOT / 'output' / 'ux-check'
OUT.mkdir(parents=True, exist_ok=True)
report = {'checks': [], 'errors': [], 'provider_calls': []}
OWN = 'MS4wLjABAAAA_UX_OWN'
PEER = 'MS4wLjABAAAA_UX_PEER'
IDS = [str(7703151458754472780 + i) for i in range(8)]


class Handler(app.Handler):
    def log_message(self, *_):
        pass


def provider(method, url, **kw):
    params = dict(kw['params'])
    if url.endswith('/fetch_video_statistics'):
        assert params['aweme_ids'] == IDS[1], 'A different work or multiple works were requested'
        report['provider_calls'].append({'path':url.split('api.tikhub.io')[-1],'id':params['aweme_ids']})
        return Mock(status_code=200,headers={},json=lambda:{'code':200,'data':{'statistics_list':[
            {'aweme_id':params['aweme_ids'],'play_count':7654321,'digg_count':54321,'comment_count':1234,'collect_count':3456,'share_count':4321}]}})
    assert url.endswith('/fetch_video_comments'), 'Unexpected provider request: ' + url
    report['provider_calls'].append({'path': url.split('api.tikhub.io')[-1], 'id': params['aweme_id']})
    return Mock(status_code=200, headers={}, json=lambda: {'code': 200, 'data': {'comments': [
        {'cid': 'fixture-comment-1', 'aweme_id': params['aweme_id'], 'text': '原地采集的测试评论',
         'digg_count': 12, 'create_time': 1793500000, 'user': {'nickname': '测试观众'}}], 'has_more': False}})


with tempfile.TemporaryDirectory(prefix='ux-') as temp:
    temp = Path(temp)
    store._CONN = None
    store.DB_PATH = temp / 'test.db'
    playback.SETTINGS = temp / 'settings.json'
    douk.JOBS = temp / 'jobs'
    store.init_db()
    douk.init_tables()
    corpus.init()
    now = monitoring.clock()
    for uid, name, role, identifiers in [(OWN, '自有短剧', 'own', IDS[:5]), (PEER, '同行动画', 'peer', IDS[5:])]:
        intelligence.add_account(uid, name, role)
        intelligence.save_account_posts(uid, name, [
            {'aweme_id': aid, 'title': ('末日短剧' if uid == OWN else '重生复仇') + f' 第 {i} 集',
             'create_time': int((now - timedelta(days=i+1)).timestamp()),
             'play_count': 100000 + i * 10000, 'digg_count': i + 30, 'comment_count': 99,
             'collect_count': i + 10, 'share_count': i + 5} for i, aid in enumerate(identifiers)])
    playback.save_settings({'api_key': 'private-ux-fixture-key'})
    assets = temp / 'assets'
    assets.mkdir()
    video_file = assets / (IDS[0] + '-测试视频.mp4')
    video_file.write_bytes(b'fixture-video-content')
    (assets / '测试文案.md').write_text('fixture caption', encoding='utf-8')
    server = app.LocalHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(tikhub.requests, 'request', side_effect=provider) as transport, patch.object(integrations, 'asset_roots', return_value={'downloads': (assets, '采集下载')}), sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1620, 'height': 1020})
            page.on('pageerror', lambda e: report['errors'].append(str(e)))
            page.goto(base + '/')
            page.wait_for_url('**/home')
            expect(page.locator('.home-account-card')).to_have_count(2)
            expect(page.locator('.gc-nav a.is-active')).to_have_text('工作台')
            for uid, ids in [(OWN, IDS[:3]), (PEER, IDS[5:8])]:
                actual = page.locator(f'[data-card-account="{uid}"] .latest-work').evaluate_all('items=>items.map(x=>x.dataset.openWork)')
                assert actual == ids, (actual, ids)
            page.fill('#homeSearch', '同行')
            expect(page.locator('.home-account-card')).to_have_count(2)
            page.click('#reloadPage')
            expect(page.locator('#homeSearch')).to_have_value('同行')
            page.locator('#homeFilterForm button[type=submit]').click()
            expect(page.locator('.home-account-card')).to_have_count(1)
            page.locator('#homeFilterForm button[type=reset]').click()
            expect(page.locator('.home-account-card')).to_have_count(1)
            page.locator('#homeFilterForm button[type=submit]').click()
            expect(page.locator('.home-account-card')).to_have_count(2)
            report['checks'].append('default_home_latest_three_and_explicit_search')
            page.locator(f'[data-card-account="{OWN}"] .account-card-header').click()
            expect(page.locator('#accountDetailDialog')).to_be_visible()
            expect(page.locator('[data-account-rows] tr')).to_have_count(5)
            expect(page.locator('[data-account-rows] .row-update')).to_have_count(5)
            page.locator(f'[data-account-rows] .row-update[data-aid="{IDS[1]}"]').click()
            expect(page.locator('[data-action-quote]')).to_contain_text('最多 1 次请求')
            transport.assert_not_called()
            page.click('[data-confirm-action]')
            expect(page.locator('[data-action-progress]')).to_contain_text('已完成',timeout=20000)
            page.locator('#inlineActionDialog [data-dismiss]').click()
            expect(page.locator('[data-account-rows]')).to_contain_text('765.4万')
            expect(page.locator('[data-account-rows]')).to_contain_text('1,234')
            assert transport.call_count==1
            assert page.url==base+'/home'
            assert store.q("SELECT play_count FROM playback_measurements WHERE aweme_id=? AND source='tikhub_app'",(IDS[1],))[0]['play_count']==7654321
            report['checks'].append('per_work_update_button_targets_one_single_request_and_updates_modal_in_place')
            page.locator('[data-account-form] input').fill('第 0 集')
            expect(page.locator('[data-account-rows] tr')).to_have_count(5)
            page.locator('[data-account-form] button[type=submit]').click()
            expect(page.locator('[data-account-rows] tr')).to_have_count(1)
            page.locator('[data-account-rows] [data-open-work]').first.click()
            expect(page.locator('#workDetailDialog .product-metrics')).to_contain_text('10.0万')
            for tab in ('comments', 'media', 'history', 'caption'):
                page.locator(f'[data-work-tab="{tab}"]').click()
                expect(page.locator('[data-work-pane]')).not_to_be_empty()
            page.screenshot(path=str(OUT / 'nested-details.png'))
            page.locator('#workDetailDialog [data-dismiss]').click()
            page.locator('#accountDetailDialog [data-dismiss]').click()
            assert page.url == base + '/home'
            report['checks'].append('account_modal_all_works_search_and_complete_work_details')
            page.goto(base + '/screen')
            expect(page.locator('#rankCaption')).to_have_text('8 条作品')
            assert '题材研究' not in page.locator('.gc-nav').inner_text()
            assert '标题分析' not in page.locator('.gc-nav').inner_text()
            assert page.locator('.gc-nav a').count()==5
            assert '监控账号' not in page.locator('.gc-nav').inner_text()
            page.goto(base+'/screen#accounts')
            page.wait_for_url('**/home')
            page.wait_for_selector('.home-account-card')
            page.goto(base+'/screen')
            for old in ('topics','titles'):
                page.goto(base+'/screen#'+old)
                page.wait_for_url('**/screen#overview')
                expect(page.locator('.gc-nav a.is-active')).to_have_attribute('data-page','overview')
                assert page.locator('#page-topics,#page-titles').count()==0
            expect(page.locator('#rankCaption')).to_have_text('8 条作品')
            expect(page.locator('#period')).to_be_hidden()
            page.locator('#screenFilterForm .filter-more').click()
            expect(page.locator('#period')).to_be_visible()
            page.select_option('#period', '90')
            expect(page.locator('#rankCaption')).to_have_text('8 条作品')
            page.select_option('#roleFilter', 'own')
            page.select_option('#accountFilter', OWN)
            page.fill('#searchInput', '第 0 集')
            expect(page.locator('#rankCaption')).to_have_text('8 条作品')
            # The timer must retain drafts and must not apply them.
            page.evaluate('refresh()')
            expect(page.locator('#rankCaption')).to_have_text('8 条作品')
            expect(page.locator('#accountFilter')).to_have_value(OWN)
            expect(page.locator('#searchInput')).to_have_value('第 0 集')
            page.locator('#screenFilterForm button[type=submit]').click()
            expect(page.locator('#rankCaption')).to_have_text('1 条作品')
            before_url = page.url
            page.locator('#rankBody [data-account]').first.click()
            expect(page.locator('#accountDetailDialog')).to_be_visible()
            assert page.url == before_url
            page.locator('#accountDetailDialog [data-dismiss]').click()
            expect(page.locator('#accountFilter')).to_have_value(OWN)
            assert page.locator('.cover').first.bounding_box()['height'] >= 80
            page.screenshot(path=str(OUT / 'monitoring.png'))
            report['checks'].append('removed_analysis_pages_redirect_and_nav_highlight')
            report['checks'].append('top_filters_submit_together_expand_drafts_survive_poll_large_covers')
            page.goto(base + '/research?video=' + IDS[0])
            page.wait_for_selector('.work-header')
            expect(page.locator('.library-work')).to_have_count(8)
            page.select_option('#workAccount', PEER)
            page.fill('#workSearch', '第 0 集')
            expect(page.locator('.library-work')).to_have_count(8)
            page.locator('#researchFilterForm button[type=submit]').click()
            expect(page.locator('.library-work')).to_have_count(1)
            page.click('[data-tab=comments]')
            page.locator('#researchPane [data-inline-action=comments]').click()
            expect(page.locator('[data-action-quote]')).to_contain_text('最多 1 次请求')
            assert transport.call_count==1
            url = page.url
            page.click('[data-confirm-action]')
            expect(page.locator('[data-action-progress]')).to_contain_text('已完成', timeout=20000)
            page.locator('#inlineActionDialog [data-dismiss]').click()
            expect(page.locator('#researchPane')).to_contain_text('原地采集的测试评论')
            assert page.url == url
            assert transport.call_count == 2
            report['checks'].append('research_explicit_search_and_inline_comment_collection_without_navigation')
            page.goto(base + '/collect')
            page.wait_for_selector('.collection-job')
            old_count = page.locator('.collection-job').count()
            page.fill('#taskSearch', '不存在的任务')
            assert page.locator('.collection-job').count() == old_count
            page.locator('#taskFilterForm button[type=submit]').click()
            expect(page.locator('.collection-job')).to_have_count(0)
            page.click('#reloadPage')
            expect(page.locator('.collection-job')).to_have_count(0)
            report['checks'].append('collection_top_search_keeps_filter_during_queue_updates')
            page.goto(base + '/assets')
            expect(page.locator('#assetRows tr')).to_have_count(2)
            page.select_option('#assetType', 'video')
            expect(page.locator('#assetRows tr')).to_have_count(2)
            page.locator('#assetFilterForm button[type=submit]').click()
            expect(page.locator('#assetRows tr')).to_have_count(1)
            with page.expect_download() as pending:
                page.locator('#assetRows a[download]').click()
            downloaded = pending.value
            assert downloaded.suggested_filename == video_file.name
            assert Path(downloaded.path()).read_bytes() == video_file.read_bytes()
            href = page.locator('#assetRows a[download]').get_attribute('href')
            response = page.request.get(base + href, headers={'Range': 'bytes=0-6'})
            assert response.status == 206 and response.body() == b'fixture'
            assert 'attachment' in response.headers['content-disposition']
            assert video_file.name in unquote(response.headers['content-disposition'])
            assert page.request.get(base + '/api/integrations/asset?source=downloads&path=../settings.json&download=1').status >= 400
            report['checks'].append('asset_explicit_search_real_download_utf8_filename_range_and_path_guard')
            # Shared search bars remain above results on desktop.
            for route, form, results in [('/home', '#homeFilterForm', '#homeCards'), ('/screen', '#screenFilterForm', '#page-overview'), ('/research', '#researchFilterForm', '#researchContent'), ('/assets', '#assetFilterForm', '#assetRows'), ('/collect', '#taskFilterForm', '#collectionJobs')]:
                page.goto(base + route)
                page.wait_for_selector(results)
                bar=page.locator(form).bounding_box(); content=page.locator(results).bounding_box()
                assert bar['y']+bar['height'] <= content['y']+1, (route,bar,content)
                assert page.locator('#homeSummary,#stats,#collectionSummary,#dataOverview,.account-detail-metrics').count()==0
            for width in (1500, 1024, 768, 390):
                page.set_viewport_size({'width': width, 'height': 950})
                for route, selector in [('/home', '.home-account-card'), ('/screen', '#rankBody'), ('/research', '.work-header'), ('/collect', '#collectForm'), ('/assets', '#assetRows'), ('/settings', '[data-monitor-account]')]:
                    page.goto(base + route)
                    page.wait_for_selector(selector)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (route, width)
                page.goto(base + '/home')
                page.wait_for_selector('.home-account-card')
                page.locator('.account-card-header').first.click()
                page.wait_for_selector('[data-account-rows] tr')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), ('account modal', width)
                if width == 390:
                    page.screenshot(path=str(OUT / 'mobile-details.png'))
                page.locator('#accountDetailDialog [data-dismiss]').click()
            report['checks'].append('top_alignment_no_summary_stats_and_responsive_pages_and_modal_1500_1024_768_390')
            assert transport.call_count == 2
            assert not report['errors'], report['errors']
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if store._CONN:
            store._CONN.close()
            store._CONN = None
(OUT / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
