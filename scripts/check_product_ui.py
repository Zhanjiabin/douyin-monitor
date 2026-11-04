"""Isolated acceptance for the simplified product UI; no paid vendor requests."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import app
import corpus
import douk
import intelligence
import playback
import store
import tikhub

OUT = ROOT / "output" / "product-ui-check"
OUT.mkdir(parents=True, exist_ok=True)
report = {"errors": [], "checks": [], "requests": []}
AID = "7703151458754472787"


class Handler(app.Handler):
    def log_message(self, *_):
        pass


with tempfile.TemporaryDirectory(prefix="product-ui-") as temp:
    store.DB_PATH = Path(temp) / "test.db"
    playback.SETTINGS = Path(temp) / "settings.json"
    douk.JOBS = Path(temp) / "jobs"
    store._CONN = None
    store.init_db()
    douk.init_tables()
    corpus.init()
    intelligence.add_account("MS4wLjABAAAA_PRODUCT_OWN", "测试自有账号", "own")
    intelligence.save_account_posts("MS4wLjABAAAA_PRODUCT_OWN", "测试自有账号", [{"aweme_id": AID, "title": "寒潮来了，我在末日开了家咖啡店 #末日 #短剧", "create_time": 1793500000}])
    playback.save_settings({"api_key": "private-product-fixture-key"})
    playback.persist([{"aweme_id": AID, "source": "tikhub_app", "scope": "organic", "play_count": 1966763,
                       "digg_count": 99712, "comment_count": 1923, "collect_count": 22000, "share_count": 164, "request_id": "fixture"}])
    server = app.LocalHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch.object(tikhub.requests, "request") as transport, sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 980})
            page.on("pageerror", lambda e: report["errors"].append(str(e)))
            page.on("response", lambda r: report["requests"].append({"url": r.url, "status": r.status}) if r.status >= 400 and "favicon" not in r.url else None)
            page.goto(base + "/screen")
            page.wait_for_selector("#rankBody tr")
            assert page.locator("#loginButton").count() == 0
            assert page.locator("#runButton").count() == 0
            assert page.locator(".gc-nav>a").count() == 6
            assert page.locator('.gc-nav>a[href="/discover"]').count()==1
            assert "图片工作室" not in page.locator(".gc-nav").inner_text()
            assert '监控账号' not in page.locator('.gc-nav').inner_text()
            page.goto(base+'/screen#accounts')
            page.wait_for_url('**/home')
            page.wait_for_selector('.home-account-card')
            page.locator('.account-card-header').first.click()
            page.wait_for_selector('[data-account-rows]')
            assert page.locator('#accountDetailDialog [data-inline-action=account]').count()==1
            page.locator('#accountDetailDialog [data-dismiss]').click()
            report["checks"].append("simplified_navigation_and_no_old_login_bar")
            page.goto(base + "/research?video=" + AID)
            page.wait_for_selector(".work-header")
            assert page.locator("iframe").count() == 0
            assert "196.7万" in page.locator(".work-metrics").inner_text()
            for tab in ("comments", "history", "media", "caption"):
                page.click('[data-tab="' + tab + '"]')
                page.wait_for_selector("#researchPane")
                assert page.locator("#researchPane").inner_text().strip()
            page.screenshot(path=str(OUT / "research-fixture.png"), full_page=True)
            report["checks"].append("native_research_caption_comments_media_history")
            page.goto(base + "/collect?action=comments&video=" + AID)
            page.wait_for_function("document.querySelector('#pageStatus').textContent.includes('更新于')")
            page.click("#previewCollect")
            page.wait_for_function("document.querySelector('#collectPreview').textContent.includes('最多 1 次请求')")
            page.check("#confirmCollect")
            assert page.locator("#startCollect").is_enabled()
            page.fill("#collectInput", "7703151458754472788")
            assert page.locator("#startCollect").is_disabled()
            assert page.locator("#confirmCollect").is_checked() is False
            transport.assert_not_called()
            report["checks"].append("collection_preview_and_changed_input_guard")
            for route in ("/workspace#remix", "/workspace#vox"):
                page.goto(base + route)
                page.wait_for_url("**/research**")
                page.wait_for_selector(".work-header")
            page.goto(base + "/workspace#playback")
            page.wait_for_url("**/collect**")
            assert page.locator("#collectAction").input_value() == "playback"
            report["checks"].append("old_primary_links_redirect_to_unified_pages")
            for route, selector in (("/assets", "#assetRows tr"), ("/settings#connection", "#keyState")):
                page.goto(base + route)
                page.wait_for_selector(selector)
            assert not page.locator(".advanced-panel").evaluate("e=>e.open")
            assert page.locator("#connectionKey").input_value() == ""
            assert "private-product-fixture-key" not in page.content()
            report["checks"].append("advanced_tools_collapsed_and_secret_hidden")
            for width in (1500, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for route, selector in (("/screen", "#rankBody tr"), ("/research?video=" + AID, ".work-header"), ("/collect", "#collectForm"), ("/assets", "#assetRows tr"), ("/settings#connection", "#keyState")):
                    page.goto(base + route)
                    page.wait_for_selector(selector)
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (route, width)
            report["checks"].append("responsive_1500_1024_768_390")
            transport.assert_not_called()
            page.set_viewport_size({"width": 1500, "height": 980})
            page.goto(base + '/collect?action=playback&video=' + AID)
            page.wait_for_selector('#collectForm')
            transport.return_value=Mock(status_code=200,headers={},json=lambda:{'code':200,'request_id':'fixture-unified-flow','data':{'statistics_list':[{'aweme_id':AID,'play_count':2000000}]}})
            page.click('#previewCollect')
            page.wait_for_function("document.querySelector('#collectPreview').textContent.includes('最多 1 次请求')")
            transport.assert_not_called()
            page.check('#confirmCollect')
            page.click('#startCollect')
            page.wait_for_function("document.querySelector('#jobOutput').textContent.includes('200.0万')", timeout=15000)
            assert transport.call_count==1
            assert store.q("SELECT play_count FROM playback_measurements WHERE source='tikhub_app'")[0]['play_count']==2000000
            report['checks'].append('confirmed_collection_executes_and_persists_once')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if store._CONN:
            store._CONN.close()
            store._CONN = None
(OUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
assert not report["errors"], report["errors"]
assert not report["requests"], report["requests"]
