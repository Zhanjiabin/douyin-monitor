"""Browser acceptance against an isolated local DB and simulated TikHub transport.

No vendor requests or API charges. Covers every App V3 form and execution flow.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
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

OUT = ROOT / "output" / "tikhub-check"
OUT.mkdir(parents=True, exist_ok=True)
AID = "7703151458754472787"
report = {"forms": [], "errors": [], "requests": [], "checks": []}
threading.excepthook = lambda info: report["errors"].append(str(info.exc_value))


class Handler(app.Handler):
    def log_message(self, *_):
        pass


def fake_request(method, url, **kwargs):
    assert url.startswith(playback.BASE + "/api/")
    report["requests"].append({"method": method, "path": url.removeprefix(playback.BASE)})
    if url.endswith("/fetch_video_comments"):
        time.sleep(.5)
        body = {"code": 200, "request_id": "fixture-comments", "data": {"comments": [{"cid": "123", "aweme_id": AID, "text": "测试评论", "user": {"nickname": "test"}}], "has_more": 0}}
    elif url.endswith("/fetch_user_post_videos"):
        time.sleep(1)
        body = {"code": 200, "request_id": "fixture-pages", "data": {"aweme_list": [], "has_more": 1, "max_cursor": 100}}
    else:
        body = {"code": 200, "request_id": "fixture-statistics", "data": {"statistics_list": [{"aweme_id": AID, "play_count": 1234567, "digg_count": 100, "download_count": 88}]}}
    return Mock(status_code=200, json=lambda: body, headers={}, text="fixture")


with tempfile.TemporaryDirectory(prefix="tikhub-ui-") as temp:
    store.DB_PATH = Path(temp) / "app.db"
    store._CONN = None
    playback.SETTINGS = Path(temp) / "settings.json"
    douk.JOBS = Path(temp) / "jobs"
    store.init_db()
    douk.init_tables()
    corpus.init()
    playback.init()
    intelligence.add_account("MS4wLjABAAAA_TEST", "测试账号", "own")
    intelligence.save_account_posts("MS4wLjABAAAA_TEST", "测试账号", [{"aweme_id": AID, "title": "监控作品"}])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch.object(tikhub.requests, "request", side_effect=fake_request), sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 980})
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(base + "/tikhub")
            page.wait_for_selector('[data-param="aweme_ids"]')
            page.locator('[data-param="aweme_ids"]').fill(AID)
            page.click("#previewRequest")
            page.wait_for_function("document.querySelector('#requestPreview').textContent.includes('未配置')")
            page.check("#confirmCalls")
            assert page.locator("#runRequest").is_disabled()
            assert not report["requests"]
            report["checks"].append("missing_key_blocks_transport")
            page.click("#openSettings")
            page.fill("#apiKey", "local-browser-fixture-key")
            page.fill("#maxCalls", "20")
            page.click('#settingsForm button[type="submit"]')
            page.wait_for_function("!document.querySelector('#settingsDialog').open")
            ops = [o for o in tikhub.catalog()["operations"] if o["group"] == "Douyin-App-V3-API"]
            for op in ops:
                page.locator('[data-op="' + op["id"] + '"]').click()
                page.wait_for_function("id => new URL(location.href).searchParams.get('operation')===id", arg=op["id"])
                detail = tikhub.detail(op["id"])
                for param in detail["parameters"]:
                    if not param.get("required"):
                        continue
                    schema = param["schema"]
                    value = param.get("example", schema.get("default", AID if schema.get("type") == "integer" else "test"))
                    locator = page.locator('[data-param="' + param["name"] + '"]')
                    locator.fill(str(value))
                if detail["has_body"]:
                    page.click("#fillBodyExample")
                page.click("#previewRequest")
                page.wait_for_function("!document.querySelector('#previewRequest').disabled")
                assert page.locator("#requestError").inner_text() == "", (op["path"], page.locator("#requestError").inner_text())
                assert "最多 1 次请求" in page.locator("#requestPreview").inner_text(), op["path"]
                report["forms"].append(op["path"])
            assert not report["requests"], "Preview made an external request"
            page.locator('[data-op="fetch_video_statistics_api_v1_douyin_app_v3_fetch_video_statistics_get"]').click()
            page.wait_for_selector('[data-param="aweme_ids"]')
            page.select_option("#localSource", "v:0")
            assert page.locator('[data-param="aweme_ids"]').input_value() == AID
            page.click("#previewRequest")
            page.wait_for_function("document.querySelector('#requestPreview').textContent.includes('最多 1 次请求')")
            page.check("#confirmCalls")
            page.click("#runRequest")
            page.wait_for_selector("#responseContent tbody tr", timeout=20000)
            assert "1234567" in page.locator("#responseContent").inner_text()
            assert "88" in page.locator("#responseContent").inner_text()
            assert store.q("SELECT play_count FROM playback_measurements")[0]["play_count"] == 1234567
            page.screenshot(path=str(OUT / "desktop.png"), full_page=True)
            page.click('[data-result-view="json"]')
            assert "fixture-statistics" in page.locator("#responseContent").inner_text()
            assert "local-browser-fixture-key" not in page.content()
            for fmt in ("json", "csv", "xlsx"):
                page.select_option("#exportFormat", fmt)
                with page.expect_download() as download:
                    page.click("#exportResult")
                assert download.value.suggested_filename.endswith('.' + fmt)
            report["checks"].append("execute_statistics_import_and_exports")
            page.click('[data-result-view="history"]')
            page.wait_for_selector(".job-row")
            page.locator(".job-row").first.click()
            page.click("#favorite")
            page.check("#onlyFavorites")
            assert page.locator(".endpoint-item").count() == 1
            page.uncheck("#onlyFavorites")
            page.fill("#endpointSearch", "fetch_multi_video_high_quality_play_url")
            assert page.locator(".endpoint-item").count() == 1
            page.fill("#endpointSearch", "")
            page.locator('[data-op="fetch_video_comments_api_v1_douyin_app_v3_fetch_video_comments_get"]').click()
            page.wait_for_selector('[data-param="aweme_id"]')
            page.locator('[data-param="aweme_id"]').fill(AID)
            page.click("#previewRequest")
            page.wait_for_function("document.querySelector('#requestPreview').textContent.includes('最多 1 次请求')")
            page.locator('[data-param="aweme_id"]').fill("7703151458754472788")
            page.check("#confirmCalls")
            assert page.locator("#runRequest").is_disabled()
            report["checks"].append("parameter_change_invalidates_confirmation")
            page.locator('[data-param="aweme_id"]').fill(AID)
            page.locator('#batchDetails summary').click()
            page.fill('#batchJson', json.dumps([{"params": {"aweme_id": AID}}, {"params": {"aweme_id": AID}}]))
            page.click('#previewRequest')
            page.wait_for_function("document.querySelector('#requestPreview').textContent.includes('最多 2 次请求')")
            page.check('#confirmCalls')
            page.click('#runRequest')
            page.wait_for_function("document.querySelector('#resultSummary').textContent.includes('完成') && document.querySelector('#resultSummary').textContent.includes('2/2')", timeout=20000)
            assert store.q('SELECT COUNT(*) n FROM content_comments')[0]['n'] == 1
            report['checks'].append('batch_comments_import_deduplicates')
            page.locator('[data-op="fetch_user_post_videos_api_v1_douyin_app_v3_fetch_user_post_videos_get"]').click()
            page.wait_for_selector('[data-param="sec_user_id"]')
            page.locator('[data-param="sec_user_id"]').fill('TEST')
            page.fill('#pageLimit', '3')
            page.click('#previewRequest')
            page.wait_for_function("document.querySelector('#requestPreview').textContent.includes('最多 3 次请求')")
            page.check('#confirmCalls')
            page.click('#runRequest')
            page.wait_for_selector('#cancelRequest:visible')
            for _ in range(80):
                if any(r['path'].endswith('/fetch_user_post_videos') for r in report['requests']):
                    break
                page.wait_for_timeout(25)
            page.click('#cancelRequest')
            page.wait_for_function("document.querySelector('#resultSummary').textContent.includes('已取消')")
            page.wait_for_timeout(1300)
            assert sum(r['path'].endswith('/fetch_user_post_videos') for r in report['requests']) == 1
            page.wait_for_selector('#receiptList .receipt', timeout=12000)
            report['checks'].append('cancel_bounded_pagination')
            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
            page.click(".gc-menu")
            assert page.locator('body').evaluate("el => el.classList.contains('gc-nav-open')")
            page.keyboard.press("Escape")
            report["checks"].append("responsive_1440_1024_768_390")
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
