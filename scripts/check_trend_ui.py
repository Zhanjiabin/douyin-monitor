"""Isolated browser acceptance for the on-demand playback trend and fixed list."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app,corpus,douk,intelligence,monitoring,playback,store,tikhub

OUT=ROOT/'output/trend-ui';OUT.mkdir(exist_ok=True)
errors=[];checks=[]

class Handler(app.Handler):
    def log_message(self,*_):pass

def fixed_list(page):
    assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1 && document.documentElement.scrollWidth<=innerWidth+1'),'outer frame overflow'
    for selector in ['#screenFilterForm','#rankPagination']:
        box=page.locator(selector).bounding_box()
        assert box and box['y']>=0 and box['y']+box['height']<=page.viewport_size['height']+1,(selector,box,page.viewport_size)
    pane=page.locator('#page-overview .table-scroll');box=pane.bounding_box()
    assert box and box['height']>=60,(box,page.viewport_size)
    pane.evaluate('e=>e.scrollTop=0')
    page.mouse.move(min(box['x']+box['width']/2,page.viewport_size['width']-20),box['y']+box['height']/2)
    page.mouse.wheel(0,400)
    page.wait_for_function("document.querySelector('#page-overview .table-scroll').scrollTop>0")
    assert page.evaluate('scrollY===0')
    pane.evaluate('e=>e.scrollTop=0')

with tempfile.TemporaryDirectory(prefix='trend-ui-') as temp:
    temp=Path(temp);store.DB_PATH=temp/'test.db';store._CONN=None;playback.SETTINGS=temp/'api.json';douk.JOBS=temp/'jobs'
    store.init_db();douk.init_tables();corpus.init();playback.init()
    now=monitoring.clock();day=now.date();ids=[]
    for j,(role,count) in enumerate([('own',12),('peer',10),('peer',8)]):
        uid=f'MS4wLjABAAAA_TREND_FIXTURE_{j}'
        intelligence.add_account(uid,['自有短剧','同行动画','同行故事'][j],role)
        posts=[]
        for i in range(count):
            aid=str(7703151458754472787+j*100+i);ids.append(aid)
            posts.append({'aweme_id':aid,'title':f'末日来临时，谁能找到最后一束光？第 {i+1} 集','create_time':int((now-timedelta(hours=i+1)).timestamp())})
        intelligence.save_account_posts(uid,['自有短剧','同行动画','同行故事'][j],posts)
    def observations(offsets):
        for offset in offsets:
            date=(day-timedelta(days=offset)).isoformat()
            for i,aid in enumerate(ids):
                store.x('INSERT OR REPLACE INTO playback_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (aid,date,date+' 19:00:00','tikhub_app','organic',900000+(6-offset)*(i+1)*7500,1000,60,100,150,'fixture'))
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(tikhub.requests,'request',side_effect=AssertionError('No paid calls in trend UI checks')) as transport,sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1500,'height':980});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/screen');page.wait_for_selector('[data-select-work]')
            assert page.locator('#page-overview .chart-grid').count()==0
            assert page.locator('#trendChart').count()==0 and not page.locator('#trendDialog').is_visible()
            fixed_list(page);page.screenshot(path=str(OUT/'list-desktop.png'),full_page=True)
            page.click('#openTrend');page.wait_for_selector('#trendEmpty:visible')
            assert page.locator('#exportTrend').is_disabled()
            assert page.locator('#trendChart').count()==0
            page.screenshot(path=str(OUT/'no-comparable-days.png'),full_page=True)
            page.click('#closeTrend');checks.append('default_dense_list_and_clear_empty_state')
            observations([1,0]);page.reload();page.wait_for_selector('[data-select-work]')
            page.click('#openTrend');page.wait_for_selector('#trendAvailability:visible')
            assert page.locator('#trendDailyRows tr').count()==1
            assert page.locator('#trendChart').count()==0
            assert page.locator('#trendChartTab').is_disabled()
            with page.expect_download() as download:page.click('#exportTrend')
            text=Path(download.value.path()).read_text(encoding='utf-8-sig')
            assert len(text.splitlines())==2 and '同行账号平均增长' in text,text[:200]
            page.screenshot(path=str(OUT/'one-comparable-day.png'),full_page=True)
            page.click('#closeTrend');checks.append('one_day_uses_actual_table_instead_of_empty_graph_and_exports_csv')
            observations([4,3,2]);page.reload();page.wait_for_selector('[data-select-work]')
            page.select_option('#roleFilter','peer')
            page.click('#openTrend');page.wait_for_selector('#trendChart')
            assert '全部账号' in page.locator('#trendScope').inner_text()
            assert page.locator('#trendChart circle').count()==8
            assert page.locator('#trendChart [data-trend-series=own]').get_attribute('d').count('L')==3
            assert 'NaN' not in page.locator('#trendChart').inner_html()
            page.locator('[data-trend-point]').first.focus()
            assert (day-timedelta(days=3)).isoformat() in page.locator('#trendPointDetail').inner_text()
            page.locator('#trendChartTab').focus();page.keyboard.press('ArrowRight')
            assert page.locator('#trendDailyTab').get_attribute('aria-selected')=='true'
            assert page.locator('#trendDailyRows tr').count()==4
            page.click('#trendChartTab')
            page.screenshot(path=str(OUT/'trend-desktop.png'),full_page=True)
            page.click('#closeTrend');checks.append('real_curve_keyboard_inspection_and_daily_table')
            page.locator('#screenFilterForm button[type=submit]').click()
            page.wait_for_function("document.querySelector('#rankCaption').textContent==='18 条作品'")
            page.click('#openTrend');page.wait_for_selector('#trendChart')
            assert '同行账号' in page.locator('#trendScope').inner_text()
            assert page.locator('[data-trend-series=own]').get_attribute('d')==''
            page.click('#closeTrend');checks.append('trend_scope_uses_confirmed_search_not_draft_filters')
            missing=(day-timedelta(days=2)).isoformat()
            store.x('DELETE FROM playback_measurements WHERE fetch_date=?',(missing,))
            page.reload();page.wait_for_selector('[data-select-work]');page.click('#openTrend');page.wait_for_selector('#trendChart')
            path=page.locator('[data-trend-series=peer]').get_attribute('d')
            assert path.count('M')==2 and 'L' not in path,path
            page.click('#closeTrend');checks.append('missing_days_are_not_connected_or_filled_with_zero')
            for width,height in [(1900,900),(1366,768),(950,500),(390,844)]:
                page.set_viewport_size({'width':width,'height':height});fixed_list(page)
                page.click('#openTrend');page.wait_for_selector('#trendChart')
                box=page.locator('#trendDialog').bounding_box()
                assert box and box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1,(box,width,height)
                assert page.locator('dialog[open]').count()==1
                if width==390:page.screenshot(path=str(OUT/'trend-mobile.png'),full_page=True)
                page.click('#closeTrend')
            checks.append('fixed_outer_frame_inner_list_scroll_and_responsive_trend_dialog')
            assert not errors,errors;transport.assert_not_called()
            print(json.dumps({'checks':checks,'page_errors':errors,'real_paid_calls':0},ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close();store._CONN=None
