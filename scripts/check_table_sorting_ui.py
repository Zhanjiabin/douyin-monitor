"""Sorting, column alignment and publication labels across real paginated UIs."""
import csv
import json
from datetime import datetime,timedelta
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app
import auth
import corpus
import douk
import intelligence
import metric_history
import playback
import store

OUT=ROOT/'output'/'table-sorting-check'
OUT.mkdir(parents=True,exist_ok=True)
OWN='MS4wLjABAAAA_SORT_OWN';PEER='MS4wLjABAAAA_SORT_PEER'
report={'checks':[],'errors':[],'post_requests':[]}


class Handler(app.Handler):
    def log_message(self,*_):pass


with tempfile.TemporaryDirectory(prefix='sorting-ui-') as folder,patch.object(auth,'settings',return_value=None),patch.object(playback.requests,'request',side_effect=AssertionError('No vendor calls permitted')):
    store.DB_PATH=Path(folder)/'fixture.db';store._CONN=None
    douk.JOBS=Path(folder)/'jobs';playback.SETTINGS=Path(folder)/'settings.json'
    douk.init_tables();corpus.init()
    today=datetime.now(metric_history.TZ).date()
    for uid,name,role,count,start in [(OWN,'账号甲','own',54,0),(PEER,'账号乙','peer',3,100)]:
        intelligence.add_account(uid,name,role)
        for i in range(count):
            aid=str(7703151458754472700+start+i)
            published=int(datetime.combine(today-timedelta(days=i%6),datetime.min.time(),metric_history.TZ).replace(hour=i%24).timestamp())
            intelligence.save_account_posts(uid,name,[{'aweme_id':aid,'title':f'作品 {start+i:03d} #AI动画','create_time':published}])
            play=None if i==0 else [9,99,9999,10000,1000000,100000001][i%6]+i
            playback.persist([{'aweme_id':aid,'source':'tikhub_app','scope':'organic','play_count':play,'digg_count':(i*1709)%130000,'comment_count':i%11,'collect_count':i*490,'share_count':i%7,'request_id':'fixture'}])
    aid='7703151458754472701'
    for i,value in enumerate([9,10000,None]):
        metric_history.record({'aweme_id':aid,'play_count':value,'digg_count':i},source='tikhub_app',scope='organic',observed_at=str(today)+f' 00:0{i}:00')
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1600,'height':1000})
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('request',lambda r:report['post_requests'].append(r.url) if r.method=='POST' else None)
            base=f'http://127.0.0.1:{server.server_port}'
            data=page.request.get(base+'/api/workbench/state').json()
            allrows=data['videos'];lookup={r['aweme_id']:r for r in allrows};own=[r for r in allrows if r['sec_uid']==OWN]

            def direction(root,key,wanted):
                button=page.locator(root+f' [data-sort-key="{key}"]')
                for _ in range(2):
                    if button.locator('..').get_attribute('aria-sort')==wanted:return
                    button.click()
                assert button.locator('..').get_attribute('aria-sort')==wanted

            def numeric_order(ids,rows,key,wanted):
                values=[lookup[i].get(key) for i in ids]
                expected=sorted((r[key] for r in rows if r.get(key) is not None),reverse=wanted=='descending')
                known=[v for v in values if v is not None]
                assert known==expected[:len(known)],(key,wanted,known[:5],expected[:5])

            def alignment(head,body):
                delta=page.evaluate('''([h,b])=>Math.abs(document.querySelector(h).getBoundingClientRect().right-document.querySelector(b).getBoundingClientRect().right)''',[head,body])
                assert delta<=1.1,delta

            page.goto(base+'/screen');page.wait_for_selector('#rankBody [data-video]')
            assert page.locator('#rankBody').count()==1
            assert page.locator('#rankBody').evaluate('(e)=>e.closest("table").querySelectorAll("thead [data-sort-key]").length')==11
            for key in ['play_count','digg_count','comment_count','collect_count','share_count']:
                for wanted in ['ascending','descending']:
                    direction('.screen-results thead',key,wanted)
                    ids=page.locator('#rankBody [data-video]').evaluate_all('(els)=>els.map(e=>e.dataset.video)')
                    numeric_order(ids,allrows,key,wanted)
            direction('.screen-results thead','play_count','ascending')
            page.fill('#rankPagination [data-pager-jump]','2');page.click('#rankPagination [data-pager-go]')
            assert lookup[page.locator('#rankBody [data-video]').last.get_attribute('data-video')]['play_count'] is None
            direction('.screen-results thead','comment_count','ascending')
            expect(page.locator('#rankPagination [data-pager-position]')).to_have_text('1 / 2')
            page.check('#selectPage');expect(page.locator('#selectedWorkCount')).to_have_text('已选 50 条')
            page.click('#clearSelection')
            direction('.screen-results thead','play_count','descending')
            alignment('.screen-results [data-sort-key="play_count"]>span:first-child','#rankBody tr:first-child td:nth-child(6) .metric-value')
            page.fill('#searchInput','尚未提交的条件')
            direction('.screen-results thead','digg_count','ascending')
            assert page.locator('#rankBody tr').count()==50
            assert page.input_value('#searchInput')=='尚未提交的条件'
            page.click('#reloadSnapshot')
            expect(page.locator('.screen-results [data-sort-key=digg_count]').locator('..')).to_have_attribute('aria-sort','ascending')
            page.screenshot(path=str(OUT/'monitor.png'),full_page=True)
            report['checks'].append('作品监控全部表头、跨页真实数值排序、缺失末尾、选择框和刷新保留排序')

            page.evaluate('(id)=>Guanchao.openAccount(id)',OWN)
            page.wait_for_selector('#accountDetailDialog [data-account-rows] tr')
            assert page.locator('#accountDetailDialog thead [data-sort-key]').count()==8
            assert page.locator('#accountDetailDialog .modal-work>.image-fallback').first.bounding_box()['width']==36
            for wanted in ['ascending','descending']:
                direction('#accountDetailDialog thead','play_count',wanted)
                ids=page.locator('#accountDetailDialog [data-account-rows] .modal-work').evaluate_all('(els)=>els.map(e=>e.dataset.openWork)')
                numeric_order(ids,own,'play_count',wanted)
            alignment('#accountDetailDialog [data-sort-key=play_count]>span:first-child','#accountDetailDialog tbody tr:first-child td:nth-child(4) .metric-value')
            for key in ['title','create_time','metrics_updated_at','digg_count','comment_count','collect_count','share_count']:
                direction('#accountDetailDialog thead',key,'ascending');direction('#accountDetailDialog thead',key,'descending')
            page.screenshot(path=str(OUT/'account-dialog.png'),full_page=True)
            page.click('#accountDetailDialog [data-dismiss]')
            report['checks'].append('账号弹窗标题、独立发布时间和更新时间、五项指标均可双向排序并对齐')

            page.goto(base+'/analytics?account='+OWN)
            page.wait_for_function("document.querySelector('#analyticsContent').getAttribute('aria-busy')==='false'")
            page.click('[data-dimension=works]');page.click('[data-display=table]')
            assert page.locator('#analyticsContent thead [data-sort-key]').count()==8
            direction('#analyticsContent thead','play_count','descending')
            first=page.locator('#analyticsContent tbody tr:first-child td:nth-child(3) .metric-value')
            assert '亿' in first.inner_text() and '100,' in first.get_attribute('title')
            alignment('#analyticsContent [data-sort-key=play_count]>span:first-child','#analyticsContent tbody tr:first-child td:nth-child(3) .metric-value')
            for wanted in ['ascending','descending']:
                direction('#analyticsContent thead','play_count',wanted)
                ids=page.locator('[data-analytics-work]').evaluate_all('(els)=>els.map(e=>e.dataset.analyticsWork)')
                numeric_order(ids,own,'play_count',wanted)
            for key in ['title','create_time','metrics_updated_at','digg_count','comment_count','collect_count','share_count']:
                direction('#analyticsContent thead',key,'ascending');direction('#analyticsContent thead',key,'descending')
            direction('#analyticsContent thead','play_count','descending')
            with page.expect_download() as download:page.click('#analyticsExport')
            download.value.save_as(str(OUT/'sorted.csv'))
            records=list(csv.reader((OUT/'sorted.csv').read_text(encoding='utf-8-sig').splitlines()))
            plays=[int(r[2]) for r in records[1:] if r[2]]
            assert plays==sorted(plays,reverse=True) and len(records)==55
            page.screenshot(path=str(OUT/'analytics-works.png'),full_page=True)
            page.click('[data-dimension=trend]')
            assert page.locator('#analyticsContent thead [data-sort-key]').count()==10
            for key in page.locator('#analyticsContent thead [data-sort-key]').evaluate_all('(els)=>els.map(e=>e.dataset.sortKey)'):
                direction('#analyticsContent thead',key,'ascending');direction('#analyticsContent thead',key,'descending')
            page.click('[data-dimension=publication]')
            assert page.locator('#analyticsContent thead [data-sort-key]').count()==5
            for key in ['weekday','hour','count','known','median_play']:
                direction('#analyticsContent thead',key,'ascending');direction('#analyticsContent thead',key,'descending')
            report['checks'].append('统计三种维度全部数据字段排序、万亿格式与精确悬停、按排序导出完整结果')
            page.click('[data-display=charts]')
            options=page.evaluate("echarts.getInstanceByDom(document.querySelector('#publicationHeat')).getOption()")
            assert options['series'][0]['name']=='播放中位数'
            labels=page.evaluate("(()=>{const s=echarts.getInstanceByDom(document.querySelector('#publicationHeat')).getOption().series[0];return s.data.map(d=>s.label.formatter({data:d}));})()")
            assert any('万' in v or '亿' in v for v in labels) and all('条' in v for v in labels)
            bars=page.evaluate("(()=>{const s=echarts.getInstanceByDom(document.querySelector('#publicationMedian')).getOption().series[0];return s.data.map((value,dataIndex)=>value==null?'':s.label.formatter({value,dataIndex}));})()")
            assert any('条' in v for v in bars)
            page.click('[data-publication-view=count]')
            assert page.evaluate("echarts.getInstanceByDom(document.querySelector('#publicationHeat')).getOption().series[0].name")=='发布数量'
            page.click('[data-publication-view=play]')
            page.screenshot(path=str(OUT/'publication.png'),full_page=True)
            report['checks'].append('热力图默认直接标注播放及作品数、数量切换、柱图标注及缺失数据')
            page.evaluate('(id)=>Guanchao.openWork(id)',aid)
            page.wait_for_selector('#workDetailDialog [data-work-tab=history]');page.click('#workDetailDialog [data-work-tab=history]')
            assert page.locator('#workDetailDialog thead [data-sort-key]').count()==8
            for key in ['observed_at','play_count','digg_count','comment_count','collect_count','share_count','play_delta','source']:
                direction('#workDetailDialog thead',key,'ascending');direction('#workDetailDialog thead',key,'descending')
            page.click('#workDetailDialog [data-dismiss]')
            checks=page.evaluate('''()=>{const cols=[{key:'v',type:'number'}],rows=[{v:10000},{v:null},{v:9},{v:0},{v:100000000},{v:NaN}];return ['asc','desc'].map(direction=>Guanchao.sortRows(rows,{key:'v',direction},cols).map(r=>Number.isNaN(r.v)?null:r.v));}''')
            assert checks==[[0,9,10000,100000000,None,None],[100000000,10000,9,0,None,None]]
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_function("document.querySelector('.gc-sidebar').getBoundingClientRect().right<=1")
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1&&document.documentElement.scrollHeight<=innerHeight+1')
            page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
            page.goto(base+'/screen');page.wait_for_selector('#rankBody [data-video]')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1&&document.documentElement.scrollHeight<=innerHeight+1')
            page.click('#openTrend')
            assert page.locator('#trendDailyRows').evaluate('(e)=>e.closest("table").querySelectorAll("thead [data-sort-key]").length')==6
            page.click('#closeTrend')
            report['checks'].append('历史全部表头、有效零值和缺失值排序、手机布局')
            assert not report['errors'],report['errors']
            assert not report['post_requests'],report['post_requests']
            browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close()
        store._CONN=None
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
