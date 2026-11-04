"""Offline browser acceptance for discovery, billing boundaries and single-layer details."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch,Mock

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app,auth,corpus,discovery as d,douk,intelligence,playback,store,tikhub,media_cache

OUT=ROOT/'output/discovery-ui';OUT.mkdir(exist_ok=True)
AID='7703151458754472787';UID='MS4wLjABAAAA_DISCOVERY_BROWSER'
calls=[];errors=[]
def transport(method,url,**kwargs):
    calls.append(url)
    data={'code':0,'data':{'page':{'total':45},'objs':[{'item_id':str(int(AID)+i),'item_title':f'末日生存 · 第 {i+1} 集，暴雨停电后的一次选择','nick_name':'测试作者','fans_cnt':8500,'play_cnt':120000+i*3000,'like_cnt':8000+i,'publish_time':1793500000,'score':10000+i} for i in range(45)]}}
    if 'fetch_hot_rise' in url:
        data={'code':0,'data':{'objs':[{'sentence':f'热点测试 {i+1}','rank':i+1,'rank_diff':3,'hot_score':500000-i*100,'trends':[{'date':'20261101','value':100},{'date':'20261102','value':120}]} for i in range(10)]}}
    if 'fetch_one_video_v3' in url:
        data={'aweme_detail':{'aweme_id':dict(kwargs['params'])['aweme_id'],'desc':'新发现的末日作品','create_time':1793500000,'author':{'sec_uid':UID+'_PEER','nickname':'发现的同行'},'statistics':{'play_count':170000,'digg_count':12000}}}
    if 'fans_portrait' in url:
        data={'code':0,'data':{'portrait':{'portrait_data':[{'name':'female','value':.72},{'name':'male','value':.28}]},'portrait_tgi':{'portrait_data':[{'name':'female','value':157.18},{'name':'male','value':52.32}]}}}
    return Mock(status_code=200,json=lambda:{'code':200,'request_id':'browser-fixture','data':data},headers={})

with tempfile.TemporaryDirectory(prefix='discovery-browser-') as folder:
    temp=Path(folder);store.DB_PATH=temp/'test.db';store._CONN=None;playback.SETTINGS=temp/'settings.json';douk.JOBS=temp/'jobs';douk.STARTED=False
    store.init_db();d.init();corpus.init();playback.init();playback.save_settings({'api_key':'browser-fixture-key','max_calls_per_job':5,'daily_call_limit':100})
    intelligence.add_account(UID,'测试作者','own');intelligence.save_account_posts(UID,'测试作者',[{'aweme_id':AID,'title':'我的末日作品','create_time':1793500000}])
    with patch.object(tikhub.requests,'request',side_effect=transport):
        for q in [{'source':'lowfan'},{'source':'rising'}]:
            p=d.plan(q);d.execute({'query':p['query'],'confirmed':True,'fingerprint':p['fingerprint']})
    calls.clear()
    server=app.LocalHTTPServer(('127.0.0.1',0),app.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(tikhub.requests,'request',side_effect=transport),sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1500,'height':980});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/discover');page.wait_for_selector('[data-select]')
            assert page.locator('#appNavigation a.is-active').inner_text()=='发现'
            assert not calls
            page.screenshot(path=str(OUT/'works-desktop.png'),full_page=True)
            page.fill('#discoveryKeyword','重生')
            assert page.locator('[data-select]').count()==20
            page.click('#discoveryFetch')
            assert not page.locator('#discoveryQuote').is_visible()
            assert not calls
            page.fill('#discoveryKeyword','');page.click('#discoveryFilters button[type=submit]')
            page.click('#discoveryFetch');page.wait_for_selector('[data-confirm]')
            assert not calls
            page.click('#discoveryQuote [data-cancel]')
            page.click('#discoveryFetch');page.wait_for_selector('[data-confirm]');page.click('#discoveryQuote [data-confirm]')
            page.wait_for_function("document.querySelector('#discoveryQuote strong').textContent==='抓取完成'")
            assert len(calls)==1,calls
            page.locator('[data-select]').nth(1).check();page.click('#discoveryMonitor');page.wait_for_selector('[data-confirm]')
            assert len(calls)==1
            page.click('#discoveryQuote [data-confirm]');page.wait_for_function("document.querySelector('#discoveryQuote strong').textContent==='抓取完成'")
            assert len(calls)==2,calls
            assert any(a['sec_uid']==UID+'_PEER' for a in intelligence.list_tracked_accounts())
            calls.clear()
            page.locator('[data-pager-jump]').fill('2');page.locator('[data-pager-go]').click();assert page.locator('[data-pager-position]').inner_text()=='2 / 3'
            page.click('[data-view=hot]');page.wait_for_selector('[data-related]')
            assert page.locator('#appNavigation a.is-active').inner_text()=='发现'
            page.screenshot(path=str(OUT/'hot-desktop.png'),full_page=True)
            page.locator('[data-hot-trend]').first.click();page.wait_for_selector('#hotTrendDialog[open] svg')
            page.click('#hotTrendDialog [data-close]');assert not calls
            page.click('[data-view=keywords]');page.wait_for_selector('#addKeyword')
            page.fill('#addKeyword input','末日');page.click('#addKeyword button');page.wait_for_selector('[data-keyword-trend]')
            page.click('[data-keyword-trend]');page.wait_for_selector('#audienceDialog[open]')
            page.click('#audienceDialog [data-refresh]');page.wait_for_selector('#audienceDialog [data-confirm]')
            assert page.locator('#audienceDialog [data-confirm]').is_disabled()
            assert not calls
            page.click('#audienceDialog [data-close]')
            page.goto(base+'/home');page.wait_for_selector('[data-card-account]')
            page.locator('[data-card-account="'+UID+'"]').first.click();page.wait_for_selector('#accountDetailDialog[open] [data-open-work]')
            page.locator('#accountDetailDialog [data-open-work]').first.click();page.wait_for_selector('#workDetailDialog[open]')
            assert page.locator('dialog[open]').count()==1
            assert page.locator('[data-work-tab=audience]').count()==0
            page.screenshot(path=str(OUT/'work-detail.png'),full_page=True)
            page.click('#workDetailDialog [data-dismiss]');page.wait_for_selector('#accountDetailDialog[open]')
            assert page.locator('dialog[open]').count()==1
            page.get_by_role('button',name='粉丝画像',exact=True).click();page.wait_for_selector('#audienceDialog[open] [data-dimension]')
            assert page.locator('dialog[open]').count()==1
            page.click('#audienceDialog [data-refresh]');page.wait_for_selector('#audienceDialog [data-confirm]')
            assert not page.locator('#audienceDialog [data-confirm]').is_disabled()
            assert not calls
            page.click('#audienceDialog [data-confirm]');page.wait_for_selector('#audienceDialog svg')
            assert len(calls)==1 and 'fans_portrait' in calls[0]
            assert '72.0%' in page.locator('#audienceDialog').inner_text()
            assert '157.18%' not in page.locator('#audienceDialog').inner_text()
            page.screenshot(path=str(OUT/'portrait-desktop.png'),full_page=True)
            export=page.locator('#audienceDialog [data-export]').get_attribute('href')
            assert 'provider_rate' not in page.request.get(base+export).text()
            calls.clear()
            page.click('#audienceDialog [data-close]');page.wait_for_selector('#accountDetailDialog[open]')
            page.goto(base+'/settings#connection');page.wait_for_selector('[data-permission]')
            assert not page.locator('[data-permission] input').is_checked()
            page.set_viewport_size({'width':390,'height':844});page.goto(base+'/discover#hot');page.wait_for_selector('[data-related]')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'page-level horizontal overflow'
            page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
            browser.close()
        assert not errors,errors
        assert not calls,calls
        print(json.dumps({'checks':'cache/free filters, confirmed mock fetch, verified add-monitor loop, hot charts, pagination jump, keyword permission, public portrait/CSV, no private inputs, single dialog, mobile','errors':errors,'real_paid_calls':0,'mock_paid_calls':3},ensure_ascii=False))
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close();store._CONN=None
