"""Browser acceptance with isolated jobs/settings/database; no platform requests."""
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import app
import douk
import store
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    store.DB_PATH = root / "fixture.db"
    douk.RUNTIME = root / "douk"
    douk.JOBS = douk.RUNTIME / "jobs"
    douk.OUTPUT_DIR = root / "output"
    douk.STARTED = True  # Keep submitted fixture tasks queued; never call upstream.
    douk.init_tables()
    jid = "0123456789abcdef"
    douk.atomic(douk.metadata(jid), {"id": jid, "endpoint": "/douyin/detail", "platform": "douyin",
        "title": "界面验收作品", "status": "completed", "count": 51, "created_at": "2026-11-04 01:00:00"})
    douk.atomic(douk.metadata(jid).parent / "result.json", {"ok": True, "data": [
        {"aweme_id": str(8000000000000000000 + n), "desc": "验收作品 " + str(n),
         "author": {"sec_uid": "UI_TEST", "nickname": "测试账号"}, "video": {},
         "statistics": {"play_count": 0 if n == 0 else n * 100, "digg_count": n},
         "create_time": 1791000000} for n in range(51)]})
    server = app.LocalHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            base = f"http://127.0.0.1:{server.server_port}"
            media = douk.OUTPUT_DIR / "downloads" / "fixture.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"0123456789")
            partial = page.request.get(base + "/api/douk/file?path=fixture.mp4", headers={"Range": "bytes=2-5"})
            assert partial.status == 206 and partial.body() == b"2345"
            invalid = page.request.get(base + "/api/douk/file?path=fixture.mp4", headers={"Range": "bytes=20-"})
            assert invalid.status == 416
            page.goto(base + "/studio")
            page.wait_for_selector("#resultTable tbody tr")
            assert page.locator("#resultTable tbody tr").count() == 40
            assert "未提供" in page.locator("#resultTable tbody tr").first.inner_text()
            page.click("#resultNext")
            assert page.locator("#resultTable tbody tr").count() == 11
            page.fill("#resultSearch", "验收作品 50")
            assert page.locator("#resultTable tbody tr").count() == 1
            page.locator("[data-raw]").first.click()
            assert "验收作品 50" in page.locator("#rawData").inner_text()
            page.click("#closeRaw")
            for format_ in ("xlsx", "csv", "json"):
                page.select_option("#exportFormat", format_)
                with page.expect_download() as dl:
                    page.click("#exportResult")
                assert dl.value.suggested_filename.endswith("." + format_)
                assert Path(dl.value.path()).stat().st_size > 100
            checked = []
            for platform in ("douyin", "tiktok"):
                page.click(f'[data-platform="{platform}"]')
                paths = page.locator("#operation option").evaluate_all("els => els.map(e => e.value)")
                for path in paths:
                    page.select_option("#operation", path)
                    assert page.locator("#taskForm").is_visible()
                    checked.append(path)
                if platform == "tiktok":
                    page.select_option("#operation", "/tiktok/live")
                    assert page.locator('[data-param="room_id"]').count() == 1
            assert len(checked) == 26
            page.click('[data-platform="douyin"]')
            page.fill('[data-param="detail_id"]', "8000000000000000001\n8000000000000000002")
            page.click("#submitTask")
            page.wait_for_function("document.querySelectorAll('[data-cancel]').length === 1")
            new_id = page.locator("[data-cancel]").get_attribute("data-cancel")
            request = douk.read(douk.metadata(new_id).parent / "request.json")
            assert len(request["items"]) == 2
            page.locator("[data-cancel]").click()
            page.wait_for_function("document.querySelectorAll('[data-cancel]').length === 0")
            assert douk.job(new_id)["status"] == "cancelled"
            page.locator(f'[data-retry="{new_id}"]').click()
            page.wait_for_function("document.querySelectorAll('[data-cancel]').length === 1")
            page.click('[data-view="settings"]')
            assert page.locator("#capabilityList span").count() == 26
            page.fill("#dyCookie", "sessionid=fixture-secret")
            page.locator('#settingsForm button[type="submit"]').click()
            page.wait_for_function("document.querySelector('#dyCookieStatus').textContent === '已保存'")
            assert "fixture-secret" not in page.locator("#settingsJson").input_value()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.click('[data-view="collect"]')
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
        print(json.dumps({"passed": True, "routes": len(checked), "exports": 3,
                          "batch_cancel_retry": True, "fixture_only": True}))
    finally:
        server.shutdown()
        if store._CONN:
            store._CONN.close()
            store._CONN = None
