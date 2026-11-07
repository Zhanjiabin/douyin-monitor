"""Offline acceptance: real HTTP app + real queue, mocked paid transport only."""
from contextlib import ExitStack
from datetime import timedelta
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from test_ip_radar import RadarTests
import app, auth, discovery as d, discovery_hub as hub, douk, media_cache, metric_history as mh, ip_radar as radar, tikhub, store

OUT = ROOT/'output/ip-radar-ui'; OUT.mkdir(parents=True, exist_ok=True)
fixture = RadarTests(); fixture.setUp(); fixture.clock.stop()
fixture.now = d.now()-timedelta(minutes=20)
rows = fixture.seed(64)
covers = ['/media/'+p.name for p in media_cache.MEDIA_DIR.glob('*') if p.suffix.lower() in {'.jpg','.png','.webp'}][:12]
if covers:
    stored = store.q('SELECT * FROM discovery_sightings')
    for i, row in enumerate(stored):
        value=json.loads(row['value_json']);value['cover_path']=covers[i%len(covers)]
        store.x('UPDATE discovery_sightings SET value_json=? WHERE snapshot_id=? AND item_id=?', (json.dumps(value),row['snapshot_id'],row['item_id']))
transport_calls=[]; errors=[]


def fake_request(method, url, **kwargs):
    transport_calls.append(url)
    time.sleep(.12)
    params=dict(kwargs.get('params') or [])
    aid=str(params.get('aweme_id') or params.get('aweme_ids') or fixture.aid)
    if url.endswith('/fetch_one_video_v3'):
        return fixture.response(fixture.detail(aid, 'MS4wLjABAAAA_UI_VERIFIED_'+aid[-3:]))
    if url.endswith('/fetch_video_statistics'):
        return fixture.response({'code':200,'data':{'statistics_list':[{'aweme_id':aid,'play_count':1200000+int(aid[-2:]),'digg_count':11000}]}})
    if '/fetch_video_search_v3' in url:
        return fixture.response({'code':200,'data':{'items':[{'itemId':'7703151458754472999','title':'《星海便利店》第1集 #AI漫剧',
                        'nickname':'全新作者','likes':8000,'createTime':int(d.now().timestamp())}]}})
    raise AssertionError('Unexpected paid endpoint: '+url)


class Handler(app.Handler):
    def log_message(self, *_): pass


def frame(page, scroller, pager):
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1 && document.documentElement.scrollHeight<=innerHeight+1'), ('outer overflow',page.viewport_size)
    for selector in ['.topbar','.hub-tabs',pager]:
        box=page.locator(selector).bounding_box()
        assert box and box['y']>=0 and box['y']+box['height']<=page.viewport_size['height']+1, (selector,box,page.viewport_size)
    box=page.locator(scroller).bounding_box()
    assert box and box['height']>70, (scroller,box,page.viewport_size)
    page.locator(scroller).evaluate('e=>e.scrollTop=200')
    assert page.evaluate('scrollY===0')
    page.locator(scroller).evaluate('e=>e.scrollTop=0')


server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
try:
    with ExitStack() as stack:
        stack.enter_context(patch.object(auth,'settings',return_value=None))
        stack.enter_context(patch.object(tikhub.requests,'request',side_effect=fake_request))
        stack.enter_context(patch.object(media_cache,'cache_images',return_value={}))
        p=stack.enter_context(sync_playwright());browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1580,'height':940})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/discover');page.wait_for_selector('#hubTable tbody [data-hub-detail]')
        assert page.locator('#hubTable thead').inner_text().count('窗口')==0
        assert page.locator('#hubTable tbody tr').count()==20
        page.select_option('#hubFilters [name=focus]','pet')
        assert page.locator('#hubTable tbody tr').count()==20
        page.click('#hubFilters [type=submit]')
        page.wait_for_function("document.querySelector('#hubPager [data-pager-total]').textContent==='共 16 条作品'")
        page.click('#hubFilters [type=reset]')
        page.wait_for_function("document.querySelector('#hubPager [data-pager-total]').textContent==='共 64 条作品'")
        page.click('[data-hub-sort=likes]');page.wait_for_function("document.querySelector('[data-hub-sort=likes]').textContent.includes('↑')")
        page.click('[data-hub-sort=likes]');page.wait_for_function("document.querySelector('[data-hub-sort=likes]').textContent.includes('↓')")
        page.locator('#hubPager [data-pager-jump]').fill('2');page.locator('#hubPager [data-pager-go]').click()
        page.wait_for_function("document.querySelector('#hubPager [data-pager-position]').textContent==='2 / 4'")
        page.check('#hubTable tbody [data-hub-select] >> nth=0')
        page.check('#hubTable tbody [data-hub-select] >> nth=1')
        page.click('#hubEnrich');page.wait_for_selector('#radarTask [data-radar-confirm]:not([disabled])')
        assert '4 次独立请求' in page.locator('#radarTask').inner_text()
        page.click('#radarTask [data-radar-confirm]')
        page.wait_for_selector('#radarTask progress')
        page.wait_for_function("document.querySelector('#radarTask [data-radar-progress]').textContent.includes('已完成')")
        assert len(transport_calls)==4 and all('multi' not in url for url in transport_calls)
        page.screenshot(path=str(OUT/'single-queue.png'))
        page.click('#radarTask [data-radar-close]')
        page.goto(base+'/discover');page.wait_for_selector('#hubTable tbody [data-hub-detail]')
        page.locator('#hubTable tbody [data-hub-detail]').first.click();page.wait_for_selector('#hubDetail [data-history-tab]')
        page.click('#hubDetail [data-history-tab=search]')
        assert page.locator('#hubDetail [data-history-pane=search]').is_visible()
        page.click('#hubDetail [data-hub-close]')
        page.click('[data-hub-view=references]');page.wait_for_selector('.radar-card')
        assert page.locator('.radar-card').count()==12
        assert '雾海来信' in page.locator('#hubReferencesView').inner_text()
        assert len(transport_calls)==4
        page.fill('#radarFilters [name=keyword]','不存在的IP')
        assert page.locator('.radar-card').count()==12
        page.click('#radarFilters [type=submit]');page.wait_for_selector('#radarCards .hub-empty')
        page.click('#radarFilters [type=reset]');page.wait_for_selector('.radar-card')
        page.locator('[data-radar-group]').first.click();page.wait_for_selector('#radarGroup .radar-group-table tbody tr')
        page.screenshot(path=str(OUT/'ip-detail.png'));page.click('#radarGroup [data-radar-close]')
        page.click('#hubAutomation');page.wait_for_selector('#radarSettingsForm')
        assert not page.locator('#radarSettingsForm [name=enabled]').is_checked()
        page.fill('#radarSettingsForm [name=watch_limit]','8')
        page.click('#radarSettingsForm [type=submit]');page.wait_for_function("document.querySelector('#radarSettingsFeedback').textContent==='已保存'")
        assert radar.config()['watch_limit']==8 and len(transport_calls)==4
        page.screenshot(path=str(OUT/'settings-desktop.png'));page.click('#radarSettings [data-radar-close]')
        page.click('[data-hub-view=reports]');page.click('#hubGenerate');page.wait_for_selector('.radar-report-table')
        assert '近期值得看的 IP' in page.locator('#hubReportBody').inner_text()
        assert '正在建立基线' in page.locator('#hubReportBody').inner_text()
        for theme in ['light','dark']:
            page.evaluate('(theme)=>{localStorage.setItem("gc-theme",theme);document.documentElement.dataset.theme=theme}',theme)
            for width,height in [(1580,940),(1366,768),(950,500),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                for view,scroller,pager in [('library','.hub-table-scroll','#hubPager'),('references','.radar-grid-scroll','#radarPager'),('reports','#hubReportBody','#hubReportFilters')]:
                    page.goto(base+'/discover#'+view)
                    page.wait_for_selector({'library':'#hubTable tbody [data-hub-detail]','references':'.radar-card','reports':'.radar-report-table'}[view])
                    frame(page,scroller,pager)
                    page.screenshot(path=str(OUT/f'{view}-{theme}-{width}.png'))
                page.click('#hubAutomation');page.wait_for_selector('#radarSettingsForm')
                box=page.locator('#radarSettings').bounding_box()
                assert box and box['x']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1
                page.click('#radarSettings [data-radar-close]')
        assert not errors, errors
        assert len(transport_calls)==4
        print(json.dumps({'checks':'filters/reset/sort/pager; real queue with single calls/progress; history; dynamic IP groups; settings; reports; 24 fixed responsive layouts',
                          'mock_requests':len(transport_calls),'real_paid_requests':0,'errors':errors},ensure_ascii=False))
        browser.close()
finally:
    server.shutdown();server.server_close()
    fixture.tearDown()
