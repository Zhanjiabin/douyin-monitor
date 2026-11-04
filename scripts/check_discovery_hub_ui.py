"""Offline browser verification of the unified discovery library and report UX."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app,corpus,discovery as d,discovery_hub as hub,douk,media_cache,playback,store,tikhub

OUT=ROOT/'output/discovery-hub-ui';OUT.mkdir(exist_ok=True)
errors=[];submitted=[];jobs=[]
captions=['假如一座未来城市突然失去颜色，机器人要怎么找回来 #AI短片 #原创动画',
          '没想到最后出门的人竟然是它，一分钟看完这个反转 #AI视频 #脑洞',
          '如何用AI制作电影感的角色动画，完整过程与镜头设计 #AI教程 #原创动画']
at=d.now()-timedelta(minutes=10)
covers=['/media/'+p.name for p in list(media_cache.MEDIA_DIR.glob('*'))[:30] if p.suffix in {'.jpg','.png','.webp'}]


def seed(source,rows,when,**extra):
    q=d.query({'source':source,**extra});sid=uuid.uuid4().hex
    store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',(sid,d.key(q),source,json.dumps(q),json.dumps({'items':rows}),when.isoformat(),'completed',''))
    hub.ingest(sid)


def fake_submit(req):
    submitted.append(req)
    job={'id':uuid.uuid4().hex[:16],'title':req.get('title','测试同步'),'status':'completed','created_at':at.isoformat(),'count':20}
    jobs.append(job)
    return job


class Handler(app.Handler):
    def log_message(self,*_):pass


def check_fixed_frame(page, scroller='.hub-table-scroll', pager=True):
    """Prove that the content scrolls without moving or clipping the frame."""
    pane=page.locator(scroller)
    pane.evaluate('e=>{e.scrollTop=0;e.scrollLeft=0}')
    pinned=['.topbar','.hub-tabs','#hubFilters','#hubPager'] if pager else ['.topbar','.hub-tabs','#hubReportFilters']
    before=[page.locator(s).bounding_box() for s in pinned]
    for box in before:
        assert box and box['y']>=0 and box['y']+box['height']<=page.viewport_size['height']+1,(page.viewport_size,box)
    assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1 && document.documentElement.scrollWidth<=innerWidth+1'),'outer frame overflow'
    box=pane.bounding_box()
    assert box and box['height']>=60,(page.viewport_size,box)
    assert pane.evaluate('e=>e.scrollHeight>e.clientHeight'),'fixture must overflow the inner pane'
    page.mouse.move(min(box['x']+box['width']/2,page.viewport_size['width']-20),box['y']+box['height']/2)
    page.mouse.wheel(0,500)
    page.wait_for_function('(selector)=>document.querySelector(selector).scrollTop>0',arg=scroller)
    assert page.evaluate('scrollY===0 && scrollX===0'),'scroll escaped to the page'
    assert before==[page.locator(s).bounding_box() for s in pinned],'fixed controls moved'
    # Scrolling beyond the list end must not move the surrounding page either.
    pane.evaluate('e=>e.scrollTop=e.scrollHeight')
    page.mouse.wheel(0,500);page.wait_for_timeout(100)
    assert page.evaluate('scrollY===0 && scrollX===0')
    pane.evaluate('e=>{e.scrollTop=0;e.scrollLeft=0}')


with tempfile.TemporaryDirectory(prefix='discovery-hub-browser-') as temp:
    temp=Path(temp);store.DB_PATH=temp/'test.db';store._CONN=None;playback.SETTINGS=temp/'api.json';douk.JOBS=temp/'jobs'
    store.init_db();playback.init();corpus.init();hub.init()
    playback.save_settings({'api_key':'fixture-only','max_calls_per_job':5,'daily_call_limit':100})
    works=[{'id':str(7703151458754472787+i),'aweme_id':str(7703151458754472787+i),'title':captions[i%3]+f' 第{i+1}篇',
            'source':'video_total','cover_path':covers[i%len(covers)] if covers else '',
            'account_name':['光影实验室','未来放映室','AI故事制造'][i%3],'fans':8500+i*180,'rank':i+1,
            'board_play_count':2300000-i*23000,'digg_count':63000-i*317,'follow_count':500+i,
            'like_rate':.0274,'follow_rate':.0019,'index':139000-i*100,'duration':87,
            'published_at':(at-timedelta(hours=i+3)).isoformat(),'url':'https://www.douyin.com/video/'+str(7703151458754472787+i)} for i in range(61)]
    seed('video_total',works,at-timedelta(hours=9),categories=['643'])
    seed('video_total',[{**r,'rank':max(1,r['rank']-2)} for r in works],at,categories=['643'])
    hot=[{'id':f'topic-{i}','title':['AI电影角色走进现实','机器人舞蹈挑战火了','电影里的未来城市','猫咪也有隐藏超能力'][i%4]+f' {i+1}',
          'source':'hot_total','category':'科技','allow_publish':True,'rank':i+6,'heat':5300000-i*30000,'publish_count':1600-i*5,
          'trend':[{'datetime':(at-timedelta(days=j)).date().isoformat(),'hot_score':1000000+j*100000} for j in [3,2,1,0]]} for i in range(48)]
    seed('hot_live',hot,at-timedelta(hours=8))
    seed('hot_live',[{**h,'rank':i+1,'heat':h['heat']+300000} for i,h in enumerate(hot)],at)
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(tikhub.requests,'request',side_effect=AssertionError('No real provider calls in browser acceptance')) as transport,patch.object(douk,'submit',side_effect=fake_submit),patch.object(douk,'jobs',side_effect=lambda:jobs),sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1580,'height':1020});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/discover');page.wait_for_selector('[data-hub-detail]')
            assert page.locator('#appNavigation a.is-active').inner_text()=='发现'
            assert page.locator('#hubPager [data-pager-total]').inner_text()=='共 61 条作品'
            assert page.locator('#hubTable tbody tr').count()==20
            assert page.locator('.stats').count()==0
            for width,height in [(1580,1020),(1900,900),(1366,768),(950,500)]:
                page.set_viewport_size({'width':width,'height':height})
                check_fixed_frame(page)
            page.set_viewport_size({'width':1900,'height':900})
            page.screenshot(path=str(OUT/'library-desktop.png'),full_page=True)
            page.set_viewport_size({'width':1580,'height':1020})
            page.fill('#hubFilters input[name=keyword]','这个词不存在')
            assert page.locator('#hubTable tbody tr').count()==20
            page.click('#hubFilters button[type=submit]');page.wait_for_selector('.hub-empty')
            page.click('#hubFilters button[type=reset]');page.wait_for_selector('[data-hub-detail]')
            assert page.locator('#hubFilters input[name=keyword]').input_value()==''
            assert page.locator('#hubPager [data-pager-total]').inner_text()=='共 61 条作品'
            page.locator('#hubPager [data-pager-jump]').fill('3');page.locator('#hubPager [data-pager-go]').click()
            page.wait_for_function("document.querySelector('#hubPager [data-pager-position]').textContent==='3 / 4'")
            page.locator('#hubTable [data-hub-detail]').first.click();page.wait_for_selector('#hubDetail[open] details')
            assert page.locator('#hubDetail .table-scroll tbody tr').count()==2
            assert '窗口涨粉' in page.locator('#hubDetail').inner_text()
            assert page.locator('dialog[open]').count()==1
            page.screenshot(path=str(OUT/'work-detail.png'),full_page=True)
            page.click('#hubDetail [data-hub-close]')
            page.click('[data-hub-view=pulse]');page.wait_for_selector('#hubTable [data-kind=hot]')
            check_fixed_frame(page)
            page.screenshot(path=str(OUT/'hot-desktop.png'),full_page=True)
            page.locator('#hubTable [data-hub-detail]').first.click();page.wait_for_selector('#hubDetail[open] svg')
            assert '历史记录' in page.locator('#hubDetail').inner_text()
            word=page.locator('#hubDetail h3').inner_text()
            page.click('#hubDetail [data-hub-use]');page.wait_for_function("location.hash==='#library'")
            page.wait_for_function("document.querySelector('#hubFilters input[name=keyword]').value!==''")
            assert page.locator('#hubFilters input[name=keyword]').input_value()==word
            assert '条件已修改' in page.locator('#hubFeedback').inner_text()
            page.click('#hubFilters button[type=reset]');page.wait_for_selector('#hubTable [data-kind=works]')
            page.click('[data-hub-view=reports]');page.wait_for_selector('#hubGenerate')
            page.click('#hubGenerate');page.wait_for_selector('.hub-report-document')
            assert page.locator('.hub-idea').count()==6
            assert '排名上升的热点' in page.locator('#hubReportBody').inner_text()
            check_fixed_frame(page,'#hubReportBody',pager=False)
            export=page.locator('#hubReportExport').get_attribute('href')
            response=page.request.get(base+export);assert response.status==200 and 'AI电影角色' in response.text()
            page.screenshot(path=str(OUT/'report-desktop.png'),full_page=True)
            page.click('#hubAutomation');page.wait_for_selector('#hubSchedule [data-save]:not([disabled])')
            page.locator('#hubSchedule input[name=enabled]').check()
            page.select_option('#hubSchedule select[name=pages]','3')
            page.fill('#hubSchedule input[name=times]','09:00，21:00')
            page.wait_for_function("document.querySelector('#hubSchedule [data-estimate]').textContent.includes('本轮最多 7 次')")
            assert not submitted
            page.screenshot(path=str(OUT/'automation.png'),full_page=True)
            page.click('#hubSchedule [data-save]');page.wait_for_selector('#hubSchedule',state='hidden')
            assert hub.config()['enabled'];assert hub.config()['times']==['09:00','21:00']
            page.click('#hubSync');page.wait_for_selector('#hubSchedule [data-save]:not([disabled])')
            page.select_option('#hubSchedule select[name=pages]','1')
            page.wait_for_function("document.querySelector('#hubSchedule [data-estimate]').textContent.includes('本轮最多 3 次')")
            assert not submitted
            page.click('#hubSchedule [data-save]');page.wait_for_selector('#hubSchedule',state='hidden')
            assert len(submitted)==3
            assert all('multi_video_statistics' not in str(req) for req in submitted)
            page.set_viewport_size({'width':390,'height':844});page.goto(base+'/discover');page.wait_for_selector('[data-hub-detail]')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'page-level overflow on mobile'
            check_fixed_frame(page)
            page.screenshot(path=str(OUT/'library-mobile.png'),full_page=True)
            page.click('[data-hub-view=pulse]');page.wait_for_selector('#hubTable [data-kind=hot]')
            check_fixed_frame(page)
            page.click('[data-hub-view=reports]');page.wait_for_selector('.hub-report-document')
            check_fixed_frame(page,'#hubReportBody',pager=False)
            assert not errors,errors
            transport.assert_not_called()
            print(json.dumps({'checks':'local library/search/reset/pagination, rich details/history, deliberate keyword search, evidence report/export, editable auto schedule, quoted grouped sync, fixed outer frame and internal scrolling across desktop/mobile library/hot/reports','errors':errors,'real_paid_calls':0,'mock_jobs':len(submitted)},ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close();store._CONN=None
