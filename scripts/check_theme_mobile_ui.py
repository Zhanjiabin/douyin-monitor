"""Authenticated theme/mobile browser acceptance with isolated data, no provider calls."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import app, auth, corpus, discovery, discovery_hub as hub, douk, intelligence
import media_cache, metric_history as history, playback, store

OUT = ROOT / 'output/theme-mobile-ui'
OUT.mkdir(parents=True, exist_ok=True)
OWN = 'MS4wLjABAAAA_THEME_OWN'
AID = '7703151458754472787'
ROUTES = ['/home', '/screen', '/analytics', '/discover', '/research', '/collect', '/assets', '/settings',
          '/discover/advanced', '/workspace', '/studio', '/tikhub', '/manage']
report = {'checks': [], 'errors': [], 'layout': []}


class Handler(app.Handler):
    def log_message(self, *_): pass


def frame(page, route, fixed=False):
    sizes = page.evaluate('''() => ({width:innerWidth, height:innerHeight,
      scrollWidth:document.documentElement.scrollWidth, scrollHeight:document.documentElement.scrollHeight,
      bodyWidth:document.body.scrollWidth})''')
    report['layout'].append({'route': route, **sizes})
    assert sizes['scrollWidth'] <= sizes['width'] + 1, (route, sizes)
    if fixed:
        assert sizes['scrollHeight'] <= sizes['height'] + 1, (route, sizes)
    tools = page.locator('.gc-account-tools').bounding_box()
    assert tools and tools['x'] >= 0 and tools['x'] + tools['width'] <= sizes['width'] + 1, (route, tools)
    assert tools['y'] >= 0 and tools['y'] < 60, (route, tools)
    expect(page.locator('.gc-account-tools .gc-user-name')).to_have_text('nuannuan')
    assert page.locator('.gc-sidebar-footer .gc-user-session').count() == 0
    expect(page.locator('.gc-logout')).to_be_visible()


with tempfile.TemporaryDirectory(prefix='theme-mobile-') as folder, ExitStack() as stack:
    folder = Path(folder)
    store.DB_PATH = folder / 'test.db'; store._CONN = None
    douk.JOBS = folder / 'jobs'; playback.SETTINGS = folder / 'settings.json'
    douk.init_tables(); corpus.init()
    today = datetime.now(history.TZ).date()
    covers = ['/media/' + p.name for p in media_cache.MEDIA_DIR.glob('*') if p.suffix in {'.jpg', '.webp', '.png'}][:12]
    for i in range(5):
        uid = OWN if not i else f'MS4wLjABAAAA_THEME_PEER_{i}'
        name = ['安夏ANXIA', '步方', '夜不黑', '苏妍', '海底蟹黄堡'][i]
        intelligence.add_account(uid, name, 'own' if not i else 'peer')
        store.x('UPDATE tracked_accounts SET nickname=?,follower_count=?,avatar_path=? WHERE sec_uid=?',
                (name, 1296000 - i * 20000, covers[i % len(covers)] if covers else None, uid))
        posts = [{'aweme_id': str(int(AID) + i * 100 + j), 'title': f'{j+1:02d} 假如世界突然停电，我在末日开了一家咖啡店 #AI动画 #原创短剧',
                  'create_time': int(datetime.combine(today - timedelta(days=j % 5), datetime.min.time(), history.TZ).replace(hour=18).timestamp())}
                 for j in range(28)]
        intelligence.save_account_posts(uid, name, posts)
        for j, post in enumerate(posts):
            if covers: store.x('UPDATE tracked_account_videos SET cover_path=? WHERE aweme_id=?', (covers[j % len(covers)], post['aweme_id']))
            for offset in [2, 1, 0]:
                history.record({'aweme_id': post['aweme_id'], 'play_count': 1966763 + j * 1000 - offset * 10000,
                                'digg_count': 99712, 'comment_count': 1923, 'collect_count': 22000, 'share_count': 164},
                               source='tikhub_app', scope='organic', observed_at=str(today-timedelta(days=offset))+' 20:00:00')
            playback.persist([{'aweme_id': post['aweme_id'], 'play_count': 1966763 + j * 1000,
                               'digg_count': 99712, 'comment_count': 1923, 'collect_count': 22000,
                               'share_count': 164, 'source': 'tikhub_app', 'scope': 'organic', 'request_id': 'fixture'}])
    hub.init()
    query = discovery.query({'source': 'video_total', 'categories': ['643']})
    rows = [{'id': str(int(AID)+j), 'title': f'AI电影角色走进现实 {j+1} #AI视频 #原创动画',
             'source': 'video_total', 'rank': j+1, 'fans': 17000, 'digg_count': 97500,
             'board_play_count': 1966000, 'account_name': '安夏ANXIA', 'cover_url': '',
             'published_at': int(datetime.now(history.TZ).timestamp())} for j in range(35)]
    store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',
            ('theme-fixture', discovery.key(query), 'video_total', json.dumps(query), json.dumps({'items': rows}), datetime.now(history.TZ).isoformat(), 'completed', ''))
    hub.ingest('theme-fixture')
    cfg = folder/'auth.json'
    cfg.write_text(json.dumps({'username': 'nuannuan', 'password_hash': auth.hash_password('fixture-password')}), encoding='utf-8')
    server = app.LocalHTTPServer(('127.0.0.1', 0), Handler)
    base = f'http://127.0.0.1:{server.server_port}'
    stack.enter_context(patch.dict(os.environ, {'DOUYIN_AUTH_FILE': str(cfg), 'DOUYIN_AUTH_DB': str(folder/'sessions.db'), 'DOUYIN_PUBLIC_ORIGIN': base}))
    transport = stack.enter_context(patch.object(playback.requests, 'request', side_effect=AssertionError('No provider calls in UI acceptance')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960}, color_scheme='light')
            page = context.new_page()
            page.on('pageerror', lambda e: report['errors'].append(str(e)))
            requests = []
            page.on('request', lambda r: requests.append((r.method, r.url)) if '/api/' in r.url else None)
            page.goto(base+'/login')
            expect(page.locator('.brand')).to_have_text('暖暖影视')
            assert page.evaluate('AppTheme.get()') == 'light'
            page.locator('.theme-toggle').click()
            page.reload()
            assert page.evaluate('AppTheme.get()') == 'dark'
            page.screenshot(path=str(OUT/'login-dark.png'))
            page.fill('#username', 'nuannuan'); page.fill('#password', 'fixture-password')
            page.click('.login-submit'); page.wait_for_url('**/home')
            page.wait_for_selector('.home-account-card')
            frame(page, '/home')
            expect(page.locator('.gc-brand')).to_have_text('暖暖影视')
            assert page.evaluate('AppTheme.get()') == 'dark'
            page.screenshot(path=str(OUT/'home-dark-desktop.png'))
            page.locator('.theme-toggle').click()
            assert page.evaluate('AppTheme.get()') == 'light'
            page.screenshot(path=str(OUT/'home-light-desktop.png'))
            report['checks'].append('登录前后统一品牌、主题持久化、右上角账号与退出')
            for theme in (['dark'] if '--quick' in sys.argv else ['light', 'dark']):
                page.evaluate('(mode)=>AppTheme.set(mode)', theme)
                for width, height in ([(390, 844)] if '--quick' in sys.argv else [(1440, 960), (768, 1024), (390, 844), (360, 800)]):
                    page.set_viewport_size({'width': width, 'height': height})
                    for route in ROUTES:
                        page.goto(base+route)
                        page.wait_for_selector('.gc-user-name')
                        page.wait_for_timeout(180)
                        assert page.evaluate('AppTheme.get()') == theme
                        frame(page, route, fixed=width<=600 and route in ROUTES[:8])
                        assert '暖暖影视' in page.title(), (route, page.title())
                        if theme=='dark' and width==390 and route in ROUTES[:8]:
                            page.screenshot(path=str(OUT/(route[1:]+'-dark-mobile.png')))
                report['checks'].append(f'{theme}: 13个页面 × 桌面/平板/390/360手机宽度')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(base+'/home');page.wait_for_selector('.home-account-card')
            page.locator('[data-add-account]').click()
            expect(page.locator('#accountEditorDialog[open]')).to_be_visible()
            box=page.locator('#accountEditorDialog').bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=391 and box['y']+box['height']<=845
            page.screenshot(path=str(OUT/'account-editor-dark-mobile.png'))
            page.locator('#accountEditorDialog [data-dismiss]').first.click()
            page.locator('.account-card-header').first.click()
            page.wait_for_selector('#accountDetailDialog [data-account-rows] tr')
            expect(page.locator('#accountDetailDialog [data-pager-go]')).to_be_visible()
            report['account_dialog'] = page.locator('#accountDetailDialog').evaluate('''e=>[e,...e.querySelectorAll('.product-dialog-body,.modal-filter-layout,.filter-results,.account-works-scroll,.pagination,.pagination-controls')].map(e=>({cls:e.className,width:e.getBoundingClientRect().width,left:e.getBoundingClientRect().x,height:e.getBoundingClientRect().height,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,scrollHeight:e.scrollHeight,display:getComputedStyle(e).display,flexShrink:getComputedStyle(e).flexShrink}))''')
            expect(page.locator('#accountDetailDialog [data-pager-go]')).to_be_in_viewport()
            assert page.locator('#accountDetailDialog .account-works-scroll').bounding_box()['width']<=374
            expect(page.locator('#accountDetailDialog .account-works-scroll')).to_be_visible()
            page.screenshot(path=str(OUT/'account-detail-dark-mobile.png'))
            page.locator('#accountDetailDialog [data-dismiss]').click()
            page.evaluate('(aid)=>Guanchao.openWork(aid)', AID)
            page.wait_for_selector('#workDetailDialog [data-work-tab=history]')
            page.click('#workDetailDialog [data-work-tab=history]')
            expect(page.locator('#workDetailDialog .metric-history-scroll')).to_be_visible()
            page.screenshot(path=str(OUT/'work-history-dark-mobile.png'))
            page.keyboard.press('Escape')
            page.goto(base+'/analytics');page.wait_for_selector('#accountTotals canvas')
            c=page.evaluate('''() => {const c=echarts.getInstanceByDom(document.querySelector('#accountTotals'));return c.getOption()}''')
            assert c['xAxis'][0]['axisLabel']['color']=='#91a5b9'
            before=len(requests);page.locator('.theme-toggle').click()
            assert len(requests)==before, 'theme switching must not fetch data'
            c=page.evaluate("echarts.getInstanceByDom(document.querySelector('#accountTotals')).getOption()")
            assert c['xAxis'][0]['axisLabel']['color']=='#94a6b3'
            page.click('.gc-menu');expect(page.locator('.gc-sidebar')).to_be_in_viewport()
            page.click('.gc-settings');page.wait_for_url('**/settings')
            assert 'gc-nav-open' not in (page.locator('body').get_attribute('class') or '')
            expect(page.locator('#monitorDays')).to_be_visible()
            report['checks'].append('手机账号编辑、账号作品、逐次历史、可跳页分页、菜单与设置')
            report['checks'].append('ECharts即时切换配色且不产生数据请求')
            # First visit follows system; explicit choice overrides later system changes.
            other=browser.new_context(viewport={'width':390,'height':844},color_scheme='dark')
            p2=other.new_page();p2.goto(base+'/login')
            assert p2.evaluate('AppTheme.get()')=='dark'
            p2.locator('.theme-toggle').click();p2.emulate_media(color_scheme='dark')
            assert p2.evaluate('AppTheme.get()')=='light'
            p2.screenshot(path=str(OUT/'login-light-mobile.png'));other.close()
            page.locator('.gc-logout').click();page.wait_for_url('**/login')
            assert context.request.get(base+'/api/workbench/state').status==401
            assert not report['errors'], report['errors']
            assert transport.call_count==0
            browser.close()
    finally:
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        server.shutdown();server.server_close()
        if store._CONN is not None:store._CONN.close();store._CONN=None
print(json.dumps({'checks':report['checks'],'errors':report['errors'],'page_layouts':len(report['layout'])},ensure_ascii=False,indent=2))
