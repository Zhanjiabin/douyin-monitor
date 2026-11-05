"""Real ECharts/browser acceptance with isolated saved data; no provider calls."""
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
import metric_history as history
import playback
import store

OUT=ROOT/'output'/'analytics-ui-check'
OUT.mkdir(parents=True,exist_ok=True)
OWN='MS4wLjABAAAA_ANALYTICS_OWN';PEER='MS4wLjABAAAA_ANALYTICS_PEER'
AID='7703151458754472787'
report={'checks':[],'errors':[]}


class Handler(app.Handler):
    def log_message(self,*_):pass


with tempfile.TemporaryDirectory(prefix='analytics-ui-') as folder,patch.object(auth,'settings',return_value=None),patch.object(playback.requests,'request',side_effect=AssertionError('No paid API calls in this check')):
    store.DB_PATH=Path(folder)/'test.db';store._CONN=None
    douk.JOBS=Path(folder)/'jobs';playback.SETTINGS=Path(folder)/'settings.json'
    douk.init_tables();corpus.init()
    today=datetime.now(history.TZ).date()
    for uid,name,role in [(OWN,'安夏 · 测试账号','own'),(PEER,'同行 · 测试账号','peer')]:
        intelligence.add_account(uid,name,role)
        rows=[{'aweme_id':str(int(AID)+i+(100 if uid==PEER else 0)),
               'title':f'{i+1:02d} 末日咖啡店：如果今天能重来 #AI动画 #短剧',
               'create_time':int(datetime.combine(today-timedelta(days=i%4),datetime.min.time(),history.TZ).replace(hour=18).timestamp())}
              for i in range(26 if uid==OWN else 1)]
        intelligence.save_account_posts(uid,name,rows)
        for v in rows:
            aid=v['aweme_id']
            for offset in [3,2,1]:
                observed=str(today-timedelta(days=offset))+' 20:00:00'
                history.record({'aweme_id':aid,'play_count':10000-offset*1000,'digg_count':1000-offset*100,'comment_count':100-offset*10,'collect_count':50,'share_count':20},source='tikhub_app',scope='organic',observed_at=observed)
            playback.persist([{'aweme_id':aid,'play_count':10000+int(aid[-2:])*100,'digg_count':1500,'comment_count':150,'collect_count':75,'share_count':30,'source':'tikhub_app','scope':'organic','request_id':'fixture'}])
    for i in range(25):
        history.record({'aweme_id':AID,'play_count':12500+i,'digg_count':1200+i,'comment_count':120+i,'collect_count':60+i,'share_count':25+i},source='tikhub_app',scope='organic',observed_at=str(today)+f' 00:{i:02d}:10')
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            api_calls=[];page.on('request',lambda r:api_calls.append(r.url) if '/api/analytics?' in r.url else None)
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/analytics')
            page.wait_for_function("document.querySelector('#analyticsContent').getAttribute('aria-busy')==='false'")
            expect(page.locator('#analyticsAccount h1')).to_contain_text('安夏')
            assert page.locator('.gc-nav a.is-active').inner_text()=='数据统计'
            assert page.evaluate("echarts.version").startswith('6.')
            assert page.evaluate("echarts.getInstanceByDom(document.querySelector('#accountTotals')).getOption().series[0].data.filter(v=>v!=null).length")>=4
            assert page.locator('#accountGrowth canvas').count()>=1
            assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
            page.screenshot(path=str(OUT/'account-trend.png'),full_page=True)
            count=len(api_calls)
            page.select_option('[name=account]',PEER)
            page.fill('[name=keyword]','搜索测试')
            assert len(api_calls)==count
            expect(page.locator('#analyticsAccount h1')).to_contain_text('安夏')
            page.fill('[name=keyword]','')
            page.locator('#analyticsFilters [type=submit]').click()
            expect(page.locator('#analyticsAccount h1')).to_contain_text('同行')
            assert len(api_calls)==count+1
            page.locator('#analyticsFilters [type=reset]').click()
            expect(page.locator('#analyticsAccount h1')).to_contain_text('安夏')
            assert page.input_value('[name=keyword]')==''
            report['checks'].append('账号隔离、筛选确认、重置重新搜索')
            page.click('[data-dimension=works]')
            assert page.evaluate("echarts.getInstanceByDom(document.querySelector('#accountRanking')).getOption().series[0].data.length")==10
            assert page.locator('#accountInteractions canvas').count()>=1
            page.screenshot(path=str(OUT/'work-performance.png'),full_page=True)
            page.click('[data-display=table]')
            assert page.locator('#analyticsContent tbody tr').count()==20
            expect(page.locator('#analyticsPagination [data-pager-total]')).to_contain_text('26')
            page.fill('#analyticsPagination [data-pager-jump]','2');page.click('#analyticsPagination [data-pager-go]')
            assert page.locator('#analyticsContent tbody tr').count()==6
            assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
            with page.expect_download() as download:page.click('#analyticsExport')
            download.value.save_as(str(OUT/'works.csv'))
            csv=(OUT/'works.csv').read_text(encoding='utf-8-sig')
            assert '上次数据更新' in csv and '同行 · 测试账号' not in csv
            report['checks'].append('作品排行、互动构成、数据明细分页跳转及导出')
            page.goto(base+'/research?video='+AID)
            page.wait_for_selector('#researchContent .work-last-update')
            expect(page.locator('#researchContent .work-last-update')).to_contain_text('上次数据更新')
            page.click('[data-tab=history]')
            assert page.locator('#researchPane th').count()==8
            assert page.locator('#researchPane tbody tr').count()==20
            page.select_option('#researchPane [data-pager-size]','50')
            assert page.locator('#researchPane tbody tr').count()==29
            report['checks'].append('内容研究的完整逐次历史与时间')
            page.goto(base+'/home');page.wait_for_selector('.latest-work .metric-update-time')
            page.locator('[data-open-work="'+AID+'"]').first.click()
            page.wait_for_selector('#workDetailDialog .work-last-update')
            page.click('#workDetailDialog [data-work-tab=history]')
            assert page.locator('#workDetailDialog th').all_inner_texts()==['更新时间（北京时间）','播放','点赞','评论','收藏','分享','播放变化','来源 / 口径']
            assert page.locator('#workDetailDialog tbody tr').count()==20
            page.fill('#workDetailDialog [data-pager-jump]','2');page.click('#workDetailDialog [data-pager-go]')
            assert page.locator('#workDetailDialog tbody tr').count()==9
            page.select_option('#workDetailDialog [data-pager-size]','50')
            assert '00:24:10' in page.locator('#workDetailDialog tbody').inner_text()
            assert page.evaluate("document.querySelector('#workDetailDialog').scrollHeight<=document.querySelector('#workDetailDialog').clientHeight+1")
            assert '12,524' in page.locator('#workDetailDialog tbody').inner_text()
            page.screenshot(path=str(OUT/'work-history.png'),full_page=True)
            page.click('#workDetailDialog [data-dismiss]')
            page.goto(base+'/screen');page.wait_for_selector('#rankBody .metric-updated-cell')
            assert str(today) in page.locator('#rankBody .metric-updated-cell').first.inner_text()
            page.locator('#rankBody [data-account]').first.click()
            page.wait_for_selector('#accountDetailDialog [data-account-rows] tr')
            assert page.locator('#accountDetailDialog a[href^="/analytics?account="]').count()==1
            assert page.locator('#accountDetailDialog .cell-note').first.inner_text()!='—'
            report['checks'].append('工作台、作品监控、账号弹窗均展示更新时间')
            page.goto(base+'/analytics?account='+OWN);page.wait_for_selector('#accountTotals canvas')
            page.click('[data-dimension=publication]')
            assert page.locator('#publicationHeat canvas').count()>=1
            assert page.locator('#publicationMedian canvas').count()>=1
            page.screenshot(path=str(OUT/'publication.png'),full_page=True)
            page.click('[data-display=table]')
            assert '北京时间' in page.locator('#analyticsContent thead').inner_text()
            report['checks'].append('发布时段热力图、播放中位数及样本明细')
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_function("document.querySelector('.gc-sidebar').getBoundingClientRect().right<=1")
            assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.click('[data-display=charts]')
            assert page.locator('#publicationHeat canvas').count()>=1
            assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
            assert page.evaluate("document.querySelector('.analytics-chart-grid').scrollHeight>document.querySelector('.analytics-chart-grid').clientHeight")
            assert page.evaluate("document.querySelector('#publicationHeat').getBoundingClientRect().bottom<=document.querySelector('#publicationHeat').closest('section').getBoundingClientRect().bottom")
            page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
            report['checks'].append('手机宽度外层固定、图表区域独立滚动')
            store.x('DELETE FROM tracked_accounts')
            page.goto(base+'/analytics')
            page.wait_for_selector('.analytics-empty a[href="/home"]')
            report['checks'].append('无账号空状态')
            assert not report['errors'],report['errors']
            browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close()
        store._CONN=None
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
