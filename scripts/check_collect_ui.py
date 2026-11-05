"""Collection result rendering and stable layout, using saved jobs and no paid calls."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app,auth,corpus,douk,intelligence,playback,store

OUT=ROOT/'output/collect-ui-check';OUT.mkdir(parents=True,exist_ok=True)
AID='7703151458754472787'
HOT='0000000000000001';VIDEO='0000000000000002';RUNNING='0000000000000003';FAILED='0000000000000004'
report={'checks':[],'errors':[]}


class Handler(app.Handler):
    def log_message(self,*_):pass


def same(a,b):
    assert all(abs(a[k]-b[k])<1 for k in ['x','y','width','height']),(a,b)


def frame(page):
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1&&document.documentElement.scrollHeight<=innerHeight+1')
    assert page.locator('#taskFilterForm').bounding_box()['y']>=0
    assert 'not defined' not in page.locator('#toast').text_content()


with tempfile.TemporaryDirectory(prefix='collect-ui-') as folder,patch.object(auth,'settings',return_value=None),patch.object(playback.requests,'request',side_effect=AssertionError('No paid calls in UI acceptance')) as transport:
    folder=Path(folder);store.DB_PATH=folder/'test.db';store._CONN=None;douk.JOBS=folder/'jobs';playback.SETTINGS=folder/'playback.json'
    store.init_db();douk.init_tables();corpus.init()
    uid='MS4wLjABAAAA_COLLECT_TEST';intelligence.add_account(uid,'测试账号','own')
    intelligence.save_account_posts(uid,'测试账号',[{'aweme_id':AID,'title':'咖啡店的AI短片','create_time':int(datetime.now().timestamp())}])

    def job(jid,title,status,records,endpoint='tikhub_api',errors=None,progress=None):
        directory=douk.metadata(jid).parent;directory.mkdir(parents=True,exist_ok=True)
        douk.atomic(directory/'meta.json',{'id':jid,'title':title,'status':status,'endpoint':endpoint,'platform':'api','count':len(records),'created_at':f'2026-11-05 12:00:{40-int(jid,16):02d}'})
        douk.atomic(directory/'result.json',{'data':records,'attempted':1,'requested':1,'errors':errors or []})
        if progress:douk.atomic(directory/'progress.json',progress)

    job(HOT,'榜单同步 · 抖音实时热榜','completed',[{'title':f'AI短片热点 {i:02d}','heat':6800000-i*10000,'rank':i+1} for i in range(51)],endpoint='discovery')
    job(VIDEO,'更新播放量','completed',[{'aweme_id':AID if not i else str(int(AID)+i),'title':f'作品 {i:02d}','play_count':2000000+i,'digg_count':98000,'comment_count':123,'collect_count':260,'share_count':45} for i in range(54)])
    job(RUNNING,'逐条更新中的任务','running',[],progress={'total':7,'done':2,'running':3,'items':[]})
    job(FAILED,'失败的任务','failed',[],errors=[{'error':'测试：网络请求超时'}])
    for i in range(5,27):job(f'{i:016x}',f'其他任务 {i}','completed',[{'text':'已保存的评论','digg_count':5}])
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page(viewport={'width':1500,'height':980})
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.goto(base+'/collect');page.wait_for_selector('#jobRecordRows tr')
            expect(page.locator('#jobTitle')).to_have_text('榜单同步 · 抖音实时热榜')
            assert page.locator('#jobRecordHead').inner_text().split()==['作品','/','内容','排名','热度']
            assert page.locator('#jobRecordRows tr').count()==50
            expect(page.locator('#jobRecordsPagination [data-pager-total]')).to_have_text('共 51 条记录')
            expect(page.locator('#jobRefreshProgress')).to_be_hidden()
            assert '680.0万' in page.locator('#jobRecordRows').inner_text()
            frame(page)
            page.locator('#jobRecordsPagination [data-pager-jump]').fill('2');page.locator('#jobRecordsPagination [data-pager-go]').click()
            assert page.locator('#jobRecordRows tr').count()==1
            report['checks'].append('完成的热点任务正常渲染51条记录，展示排名与热度，支持分页跳转')
            for theme in ['light','dark']:
                page.evaluate('(theme)=>AppTheme.set(theme)',theme)
                for width,height in [(1500,980),(1366,768),(1024,600),(390,844),(360,800)]:
                    page.set_viewport_size({'width':width,'height':height});page.goto(base+'/collect');page.wait_for_selector('#jobRecordRows tr')
                    layout=page.locator('.collect-layout').bounding_box();filters=page.locator('#taskFilterForm').bounding_box();tasks=page.locator('.task-column').bounding_box()
                    for jid in [VIDEO,RUNNING,FAILED,HOT]:
                        page.locator('[data-job="'+jid+'"]').click();page.wait_for_function('(id)=>document.querySelector(".collection-job.active").dataset.job===id',arg=jid)
                        page.wait_for_function('(title)=>document.querySelector("#jobTitle").textContent===title',arg={HOT:'榜单同步 · 抖音实时热榜',VIDEO:'更新播放量',RUNNING:'逐条更新中的任务',FAILED:'失败的任务'}[jid])
                        same(layout,page.locator('.collect-layout').bounding_box());same(filters,page.locator('#taskFilterForm').bounding_box());same(tasks,page.locator('.task-column').bounding_box());frame(page)
                        if jid==VIDEO:assert '播放' in page.locator('#jobRecordHead').inner_text() and '200.0万' in page.locator('#jobRecordRows').inner_text()
                        if jid==RUNNING:expect(page.locator('#jobRefreshProgress')).to_be_visible()
                        if jid==FAILED:expect(page.locator('#jobRecordErrors')).to_have_text('测试：网络请求超时')
                    if width<800:page.click('[data-collect-pane=compose]')
                    for action in ['account','video','comments','playback','download']:
                        page.select_option('#collectAction',action);same(layout,page.locator('.collect-layout').bounding_box());frame(page)
                    if width<800:
                        page.fill('#collectInput',AID);page.click('[data-collect-pane=tasks]');page.click('[data-collect-pane=compose]')
                        expect(page.locator('#collectInput')).to_have_value(AID)
                        page.click('[data-collect-pane=tasks]')
                    expect(page.locator('#jobsPagination [data-pager-go]')).to_be_in_viewport()
                    expect(page.locator('#jobRecordsPagination [data-pager-go]')).to_be_in_viewport()
                    assert page.locator('#jobRecordTable').bounding_box()['height']>=48
                    page.screenshot(path=str(OUT/f'collect-{theme}-{width}.png'))
            report['checks'].append('深浅色、桌面/平板/手机切换任务与采集类型尺寸不变，结果和任务独立滚动')
            page.set_viewport_size({'width':1500,'height':980});page.goto(base+'/collect');page.wait_for_selector('#jobRecordRows tr')
            page.fill('#taskSearch','不存在的任务');assert page.locator('.collection-job').count()==20
            page.click('#taskFilterForm [type=submit]');assert page.locator('.collection-job').count()==0
            page.click('#taskFilterForm [type=reset]');page.wait_for_selector('.collection-job')
            assert page.locator('.collection-job').count()==20
            page.locator('#jobsPagination [data-pager-jump]').fill('2');page.locator('#jobsPagination [data-pager-go]').click()
            assert page.locator('.collection-job').count()==6
            report['checks'].append('筛选确认、重置重新搜索、任务翻页和手机表单草稿均正常')
            assert not report['errors'],report['errors'];transport.assert_not_called();browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close();store._CONN=None
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
