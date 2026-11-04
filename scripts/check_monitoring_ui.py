"""Isolated browser acceptance: selected refresh, default single API, schedule UI."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import app
import corpus
import douk
import intelligence
import monitoring
import playback
import store
import tikhub

OUT = ROOT / "output" / "monitoring-ui-check"
OUT.mkdir(parents=True, exist_ok=True)
report = {"errors": [], "checks": [], "transport": []}
IDS = [str(7703151458754472787+i) for i in range(7)]
transport_lock=threading.Lock()
active_calls=peak_calls=0


class Handler(app.Handler):
    def log_message(self, *_):
        pass


def transport(method, url, **kw):
    global active_calls,peak_calls
    assert url.endswith('/fetch_video_statistics'), 'A batch endpoint was unexpectedly selected'
    aid = dict(kw['params'])['aweme_ids']
    assert ',' not in aid
    with transport_lock:
        active_calls+=1;peak_calls=max(peak_calls,active_calls)
        report['transport'].append({'path': url.split('api.tikhub.io')[-1], 'aweme_id': aid})
    time.sleep(1.1)
    with transport_lock:active_calls-=1
    return Mock(status_code=200, headers={}, json=lambda: {'code': 200, 'request_id': 'fixture-single', 'data': {'statistics_list': [{'aweme_id': aid, 'play_count': 2000000}]}})


with tempfile.TemporaryDirectory(prefix='monitor-ui-') as temp:
    store.DB_PATH = Path(temp) / 'test.db'
    store._CONN = None
    playback.SETTINGS = Path(temp) / 'settings.json'
    douk.JOBS = Path(temp) / 'jobs'
    store.init_db()
    douk.init_tables()
    corpus.init()
    uid = 'MS4wLjABAAAA_MONITOR_UI_OWN'
    intelligence.add_account(uid, '测试自有', 'own')
    now = monitoring.clock()
    intelligence.save_account_posts(uid, '测试自有', [{'aweme_id': aid, 'title': '监控作品 ' + str(i), 'create_time': int((now-timedelta(hours=i+1)).timestamp())} for i, aid in enumerate(IDS)])
    playback.save_settings({'api_key': 'private-monitor-ui-fixture-key'})
    server = app.LocalHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(tikhub.requests, 'request', side_effect=transport) as call, sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1500, 'height': 980})
            page.on('pageerror', lambda e: report['errors'].append(str(e)))
            service_requests=[]
            page.on('request',lambda r:service_requests.append(r.url) if r.url.endswith('/api/integrations/status') else None)
            page.route('**/api/integrations/status',lambda route:route.fulfill(json={'services':[{'name':'测试服务','running':True}]}))
            page.goto(base + '/screen')
            page.wait_for_selector('[data-select-work]')
            page.check('#selectPage')
            assert page.locator('#selectedWorkCount').inner_text() == '已选 7 条'
            page.click('#refreshSelected')
            page.wait_for_function("document.querySelector('#refreshQuote').textContent.includes('7 次请求')")
            assert page.locator('#refreshMethod').input_value() == 'single'
            assert page.locator('#refreshMethod option[value=batch]').evaluate('e=>e.disabled')
            call.assert_not_called()
            page.screenshot(path=str(OUT/'refresh-dialog.png'), full_page=True)
            page.click('#confirmRefresh')
            page.wait_for_function("document.querySelector('#refreshProgress progress')?.value>0 && document.querySelector('#refreshProgress progress').value<7")
            assert page.locator('#refreshDialog').is_visible()
            page.screenshot(path=str(OUT/'refresh-progress.png'),full_page=True)
            page.click('#closeRefresh')
            assert not page.locator('#refreshDialog').is_visible()
            page.reload()
            page.wait_for_selector('#refreshTaskLink:visible')
            page.click('#refreshTaskLink')
            assert page.locator('#refreshDialog').is_visible()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('数据已刷新')", timeout=18000)
            assert call.call_count == 7
            assert peak_calls==3
            assert page.locator('#refreshProgress [data-status=completed]').count()==7
            page.screenshot(path=str(OUT/'refresh-completed.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            bounds=page.locator('#refreshDialog').bounding_box()
            assert bounds and bounds['x']>=0 and bounds['y']>=0 and bounds['x']+bounds['width']<=390 and bounds['y']+bounds['height']<=845
            page.screenshot(path=str(OUT/'refresh-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1500,'height':980})
            assert {r['source'] for r in store.q('SELECT source FROM playback_measurements')} == {'tikhub_app'}
            report['checks'].append('selected_refresh_seven_individual_calls_with_three_workers_live_progress_background_and_persist')
            page.goto(base + '/settings#monitoringSettings')
            page.wait_for_selector('[data-monitor-account]')
            assert page.locator('[data-settings-panel]:visible').count()==1
            assert page.locator('#settings-monitoring').is_visible()
            assert not service_requests, 'Opening settings should not load advanced service health'
            assert not page.locator('#allowBatchStats').is_checked()
            assert not page.locator('#monitorEnabled').is_checked()
            assert page.locator('#monitorDays').input_value() == '7'
            assert page.locator('#monitorBatch').is_disabled()
            page.click('#monitorOwn')
            page.click('#monitorPreview')
            page.wait_for_function("document.querySelector('#monitorPlan').textContent.includes('7 条作品 · 7 次请求')")
            page.select_option('#monitorFrequency', 'interval')
            assert page.locator('#monitorInterval').is_visible()
            page.fill('#monitorInterval', '120')
            page.click('[data-settings-tab=connection]')
            page.fill('#connectionMax','6')
            page.click('[data-settings-tab=monitoring]')
            assert page.locator('#monitorInterval').input_value()=='120'
            page.click('#reloadPage')
            page.click('[data-settings-tab=connection]')
            assert page.locator('#connectionMax').input_value()=='6'
            assert playback.settings()['max_calls_per_job']==5
            page.click('[data-settings-tab=advanced]')
            page.locator('.service-panel summary').click()
            page.wait_for_selector('#serviceList .service-item')
            assert len(service_requests)==1
            assert not page.locator('#legacyTools').evaluate('e=>e.open')
            page.click('[data-settings-tab=monitoring]')
            assert page.locator('#monitorInterval').input_value()=='120'
            report['checks'].append('settings_tabs_preserve_drafts_lazy_services_and_collapsed_legacy_tools')
            page.fill('#monitorEnd', (now.date()+timedelta(days=30)).isoformat())
            page.check('#monitorEnabled')
            page.locator('#monitorForm button[type=submit]').click()
            page.wait_for_function("document.querySelector('#monitorNext').textContent.includes('下次')")
            cfg = monitoring.config()
            assert cfg['enabled'] and cfg['account_ids'] == [uid]
            assert (cfg['frequency'], cfg['interval_minutes'], cfg['publication_days'], cfg['use_batch']) == ('interval', 120, 7, False)
            assert call.call_count == 7, 'Saving schedule executed paid calls immediately'
            report['checks'].append('save_scoped_interval_schedule_without_immediate_call')
            page.screenshot(path=str(OUT/'settings-desktop.png'), full_page=True)
            page.uncheck('#monitorEnabled')
            page.locator('#monitorForm button[type=submit]').click()
            page.wait_for_function("document.querySelector('#monitorNext').textContent.includes('未启用')")
            assert not monitoring.config()['enabled']
            report['checks'].append('disable_schedule')
            page.click('[data-settings-tab=connection]')
            page.locator('#batchPermission summary').click()
            page.check('#allowBatchStats')
            page.locator('#connectionForm button[type=submit]').click()
            page.wait_for_function("!document.querySelector('#monitorBatch').disabled")
            assert playback.settings()['allow_batch_statistics']
            page.uncheck('#allowBatchStats')
            page.locator('#connectionForm button[type=submit]').click()
            page.wait_for_function("document.querySelector('#monitorBatch').disabled")
            assert not playback.settings()['allow_batch_statistics']
            assert call.call_count == 7
            report['checks'].append('global_batch_toggle_opt_in_and_disable')
            for width in (1500, 1024, 768, 390):
                page.set_viewport_size({'width': width, 'height': 900})
                for route, selector in [('/screen', '[data-select-work]'), ('/settings#monitoringSettings', '[data-monitor-account]'), ('/collect?action=playback&video='+IDS[0], '#collectForm')]:
                    page.goto(base+route)
                    page.wait_for_selector(selector)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width, route)
            report['checks'].append('responsive_selection_and_schedule_1500_1024_768_390')
            assert not report['errors'], report['errors']
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if store._CONN:
            store._CONN.close()
            store._CONN = None
(OUT/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(report, ensure_ascii=False))
assert not report['errors'], report['errors']
