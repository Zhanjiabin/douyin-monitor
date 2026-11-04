"""Numbered pagination, dense account detail and integrated account management.

Uses isolated records, fixture assets/jobs, and a transport that rejects API calls.
"""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app
import corpus
import douk
import intelligence
import integrations
import monitoring
import playback
import store
import tikhub

OUT=ROOT/'output'/'pagination-check'
OUT.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[]}
UID='MS4wLjABAAAA_PAGINATION_OWNER'
BASE_ID=7703151458754472000


class Handler(app.Handler):
    def log_message(self,*_):pass


with tempfile.TemporaryDirectory(prefix='pagination-') as temp:
    temp=Path(temp);store.DB_PATH=temp/'test.db';store._CONN=None
    playback.SETTINGS=temp/'settings.json';douk.JOBS=temp/'jobs'
    store.init_db();douk.init_tables();corpus.init()
    now=monitoring.clock()
    intelligence.add_account(UID,'分页主账号','own')
    intelligence.save_account_posts(UID,'分页主账号',[{'aweme_id':str(BASE_ID+i),'title':f'分页作品 {i:03}',
        'create_time':int((now-timedelta(hours=i+1)).timestamp()),'play_count':100000+i,'digg_count':100+i} for i in range(123)])
    for i in range(24):intelligence.add_account('MS4wLjABAAAA_PAGING_PEER_'+str(i),f'测试同行{i:02}','peer')
    files=temp/'assets';files.mkdir()
    for i in range(61):(files/f'素材-{i:03}.md').write_text('fixture',encoding='utf-8')
    jobs=[{'id':f'{i+1:016x}','title':f'测试任务 {i:02}','status':'completed','created_at':'2026-11-04 10:00:00','platform':'douyin','count':123} for i in range(23)]
    records=[{'aweme_id':str(BASE_ID+i),'title':f'任务记录 {i:03}','play_count':100000+i,'digg_count':100+i} for i in range(123)]
    def job(jid,*_):return {**next(j for j in jobs if j['id']==jid),'endpoint':'/detail','message':'完成','records':records,'result':{'data':records}}
    server=app.LocalHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(douk,'jobs',return_value=jobs),patch.object(douk,'job',side_effect=job),patch.object(integrations,'asset_roots',return_value={'downloads':(files,'测试素材')}),patch.object(tikhub.requests,'request',side_effect=AssertionError('No provider requests permitted')) as transport,sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1500,'height':980})
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            def jump(selector,value):
                pager=page.locator(selector)
                pager.locator('[data-pager-jump]').fill(str(value))
                pager.locator('[data-pager-go]').click()
            page.goto(base+'/home')
            expect(page.locator('.home-account-card')).to_have_count(12)
            assert page.locator('.gc-nav a').count()==5
            assert '监控账号' not in page.locator('.gc-nav').inner_text()
            jump('#homePagination',3)
            expect(page.locator('.home-account-card')).to_have_count(1)
            expect(page.locator('#homePagination [data-pager-total]')).to_contain_text('25')
            page.fill('#homeSearch','分页主账号')
            page.locator('#homeFilterForm [type=submit]').click()
            expect(page.locator('.home-account-card')).to_have_count(1)
            expect(page.locator('#homePagination [data-pager-position]')).to_have_text('1 / 1')
            page.locator('.account-card-header').first.click()
            expect(page.locator('[data-account-rows] tr')).to_have_count(50)
            assert page.locator('[data-account-result],#accountDetailDialog .result-toolbar').count()==0
            pager=page.locator('[data-account-pagination]')
            expect(pager.locator('[data-pager-total]')).to_contain_text('123')
            bounds=page.locator('.account-works-scroll').bounding_box()
            row_height=page.locator('[data-account-rows] tr').first.bounding_box()['height']
            assert bounds['height']/row_height>=9,(bounds,row_height)
            assert pager.bounding_box()['y']>=bounds['y']+bounds['height']-1
            assert pager.bounding_box()['y']+pager.bounding_box()['height']<=980
            assert page.locator('#accountDetailDialog').evaluate('e=>e.scrollHeight<=e.clientHeight+1')
            pager.locator('[data-pager-jump]').fill('3')
            expect(page.locator('[data-account-rows] .modal-work').first).to_contain_text('分页作品 000')
            pager.locator('[data-pager-jump]').press('Enter')
            expect(page.locator('[data-account-rows] tr')).to_have_count(23)
            expect(page.locator('[data-account-rows] .modal-work').first).to_contain_text('分页作品 100')
            jump('[data-account-pagination]',999)
            expect(pager.locator('[data-pager-position]')).to_have_text('3 / 3')
            assert not pager.locator('[data-pager-jump]').evaluate('e=>e.validity.valid')
            jump('[data-account-pagination]',2)
            expect(page.locator('[data-account-rows] .modal-work').first).to_contain_text('分页作品 050')
            page.screenshot(path=str(OUT/'compact-account.png'))
            page.locator('#accountDetailDialog [data-dismiss]').click()
            report['checks'].append('dense_modal_nine_visible_rows_one_scroll_footer_total_and_validated_jump')
            page.locator('[data-edit-account]').first.click()
            page.wait_for_selector('#accountEditorForm')
            page.fill('#accountEditorForm [name=note]','已修改备注')
            page.select_option('#accountEditorForm [name=role]','peer')
            page.uncheck('#accountEditorForm [name=enabled]')
            assert intelligence.list_tracked_accounts()[-1]['role']=='own'
            page.locator('#accountEditorForm [type=submit]').click()
            page.wait_for_function("!document.querySelector('#accountEditorDialog').open")
            row=next(a for a in intelligence.list_tracked_accounts() if a['sec_uid']==UID)
            assert (row['note'],row['role'],row['enabled'])==('已修改备注','peer',0)
            assert len(intelligence.tracked_account_videos(UID))==123
            page.locator('[data-add-account]').click()
            page.fill('#accountEditorForm [name=url]','https://www.douyin.com/user/MS4wLjABAAAA_NEW_PAGING_ACCOUNT')
            page.fill('#accountEditorForm [name=note]','新账号')
            page.select_option('#accountEditorForm [name=role]','own')
            page.locator('#accountEditorForm [type=submit]').click()
            page.wait_for_function("!document.querySelector('#accountEditorDialog').open")
            assert len(intelligence.list_tracked_accounts())==26
            page.goto(base+'/screen#accounts')
            page.wait_for_url('**/home')
            report['checks'].append('home_add_edit_role_pause_preserves_history_old_account_page_redirects')
            page.goto(base+'/screen')
            expect(page.locator('#rankCaption')).to_have_text('123 条作品')
            jump('#rankPagination',3)
            expect(page.locator('#rankBody tr')).to_have_count(23)
            expect(page.locator('#rankBody .table-title').first).to_contain_text('分页作品 100')
            page.fill('#rankPageJump','2')
            page.evaluate('refresh()')
            expect(page.locator('#rankPageJump')).to_have_value('2')
            page.click('#rankPageGo')
            expect(page.locator('#rankBody .table-title').first).to_contain_text('分页作品 050')
            page.goto(base+'/research')
            page.wait_for_selector('.library-work')
            jump('#libraryPagination',3)
            expect(page.locator('.library-work')).to_have_count(23)
            expect(page.locator('.library-work').first).to_contain_text('分页作品 100')
            page.goto(base+'/assets')
            expect(page.locator('#assetRows tr')).to_have_count(50)
            jump('#assetsPagination',2)
            expect(page.locator('#assetRows tr')).to_have_count(11)
            page.fill('#assetSearch','素材-000')
            page.locator('#assetFilterForm [type=submit]').click()
            expect(page.locator('#assetsPagination [data-pager-position]')).to_have_text('1 / 1')
            expect(page.locator('#assetRows tr')).to_have_count(1)
            page.goto(base+'/collect')
            expect(page.locator('.collection-job')).to_have_count(20)
            jump('#jobsPagination',2)
            expect(page.locator('.collection-job')).to_have_count(3)
            page.locator('.collection-job').first.click()
            page.wait_for_selector('#jobRecordsPagination [data-pager-go]')
            jump('#jobRecordsPagination',3)
            expect(page.locator('#jobRecordRows tr')).to_have_count(23)
            expect(page.locator('#jobRecordRows tr').first).to_contain_text('任务记录 100')
            page.goto(base+'/studio')
            page.wait_for_selector('[data-job]')
            page.locator('[data-job]').first.click()
            page.wait_for_selector('#resultPageJump')
            jump('#studioPagination',3)
            expect(page.locator('#resultTable tbody tr').first).to_contain_text('任务记录 080')
            report['checks'].append('global_jump_home_monitor_research_assets_jobs_results_and_legacy_studio')
            for width in (1024,768,390):
                page.set_viewport_size({'width':width,'height':900})
                for route,selector in [('/home','.home-account-card'),('/screen','#rankCaption'),('/research','.library-work'),('/assets','#assetRows'),('/collect','.collection-job')]:
                    page.goto(base+route);page.wait_for_selector(selector)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,route)
                page.goto(base+'/home');page.wait_for_selector('.home-account-card')
                page.locator('.account-card-header').first.click()
                page.wait_for_selector('[data-account-pagination]')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,'modal')
                assert page.locator('[data-account-pagination]').bounding_box()['y']<900
                page.locator('#accountDetailDialog [data-dismiss]').click()
            report['checks'].append('pagination_and_modal_responsive_1024_768_390')
            transport.assert_not_called()
            assert not report['errors'],report['errors']
            browser.close()
    finally:
        server.shutdown();server.server_close()
        if store._CONN:store._CONN.close();store._CONN=None
(OUT/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
