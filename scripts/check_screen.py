"""Browser acceptance check with an isolated fixture database, never production data."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import app
import intelligence
import corpus
import douk
import playback
import store
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as folder:
    store.DB_PATH = Path(folder) / "fixture.db"
    playback.SETTINGS = Path(folder) / 'settings.json'
    douk.JOBS = Path(folder) / 'jobs'
    store.init_db()
    douk.init_tables()
    corpus.init()
    intelligence.add_account("MS4wLjABAAAA_UI_TEST_OWN", "测试自有", "own")
    server = app.LocalHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/screen")
            page.wait_for_function("document.querySelector('#scanNotice').textContent.includes('更新于')")
            assert "尚无作品快照" in page.locator("#rankBody").inner_text()
            page.goto(base+'/home')
            page.wait_for_selector('[data-edit-account]')
            page.locator('[data-edit-account]').first.click()
            page.wait_for_selector('#accountEditorForm')
            page.select_option('#accountEditorForm select[name=role]', 'peer')
            with page.expect_response(lambda response:'/api/intel/account-settings' in response.url and response.request.method=='POST'):
                page.locator('#accountEditorForm [type=submit]').click()
            page.wait_for_function("!document.querySelector('#accountEditorDialog').open")
            page.reload()
            page.wait_for_selector('.home-account-card')
            assert '同行' in page.locator('.home-account-card').first.inner_text()
            assert intelligence.list_tracked_accounts()[0]['role']=='peer'
            store.upsert_mix({"mix_id": "ui-mix", "sec_uid": "MS4wLjABAAAA_UI_TEST_OWN", "mix_name": "重生", "author_name": "测试自有"})
            for offset in (2, 1, 0):
                store.save_video_snapshots("ui-mix", [{"aweme_id": "9990000000000000001", "title": "没想到重生后竟然成为千金", "play_count": 1000-offset*200}],
                                           (date.today()-timedelta(days=offset)).isoformat())
            page.goto(base+'/screen')
            page.reload()
            page.wait_for_selector("#rankBody [data-video]")
            assert page.locator("#rankBody tr").count() == 1
            page.wait_for_function("document.querySelectorAll('#trendChart circle').length === 2")
            page.context.route("https://www.douyin.com/**",lambda route:route.fulfill(status=200,body="<html>External destination fixture</html>"))
            with page.expect_popup() as opened:
                page.locator("#rankBody .table-title").first.click()
            popup=opened.value
            popup.wait_for_load_state()
            assert popup.url == "https://www.douyin.com/video/9990000000000000001"
            popup.close()
            with page.expect_popup() as opened:
                page.locator("#rankBody .author-cell").first.click()
            popup=opened.value
            popup.wait_for_load_state()
            assert popup.url.endswith("/user/MS4wLjABAAAA_UI_TEST_OWN")
            popup.close()
            page.locator("#rankBody [data-video]").first.click()
            page.wait_for_selector("#workDetailDialog .product-metrics")
            page.click('[data-work-tab="history"]')
            page.wait_for_selector("#workDetailDialog table")
            assert page.locator("#workDetailDialog tbody tr").count() == 3
            page.click('#workDetailDialog [data-dismiss]')
            with page.expect_download() as info:
                page.click("#exportButton")
            assert info.value.suggested_filename.endswith(".csv")
            page.click('[data-page="overview"]')
            page.select_option("#roleFilter", "own")
            page.locator("#screenFilterForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '0 条作品'")
            page.select_option("#roleFilter", "all")
            page.locator("#screenFilterForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '1 条作品'")
            intelligence.save_account_posts("MS4wLjABAAAA_UI_TEST_OWN", "测试自有", [
                {"aweme_id":str(8000000000000000000+i),"title":f"分页作品 {i}","create_time":1791000000+i,"digg_count":i}
                for i in range(51)])
            page.reload()
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '52 条作品'")
            assert page.locator("#rankBody tr").count() == 50
            assert "2026" in page.locator("#rankBody .date-cell").first.inner_text()
            page.click("#showMore")
            assert page.locator("#rankBody tr").count() == 2
            page.fill("#searchInput", "分页作品 50")
            page.locator("#screenFilterForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '1 条作品'")
            assert page.locator("#rankBody tr").count() == 1
            page.fill("#searchInput", "")
            page.locator("#screenFilterForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '52 条作品'")
            page.select_option("#pageSize", "25")
            assert page.locator("#rankBody tr").count() == 25
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.click("#fullscreenButton")
            assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")
            page.click("#fullscreenButton")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not errors, errors
            page.goto(base + "/")
            page.wait_for_selector('#homeCards .home-account-card')
            assert page.url.split('#')[0].endswith('/home')
            page.goto(base + "/manage#mixes")
            page.wait_for_selector('#page-mixes.active')
            assert not errors, errors
            browser.close()
        print(json.dumps({"result": "passed", "browser_errors": errors, "fixture_only": True}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if store._CONN:
            store._CONN.close()
            store._CONN = None
