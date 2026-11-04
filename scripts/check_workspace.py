"""Read-only acceptance of the running product and retained advanced tools."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / 'output' / 'workspace-check'
OUT.mkdir(parents=True, exist_ok=True)
report = {'pages': [], 'errors': [], 'failed_requests': []}
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1500, 'height': 980})
    page.on('pageerror', lambda e: report['errors'].append(str(e)))
    page.on('response', lambda r: report['failed_requests'].append({'status': r.status, 'url': r.url.split('?')[0]}) if r.status >= 400 and 'favicon' not in r.url else None)
    for route, selector in [('/home', '.home-account-card'), ('/screen', '#rankBody tr'), ('/screen#accounts', '.home-account-card'),
            ('/research?video=7703151458754472787', '.work-header'), ('/collect', '#collectForm'),
            ('/assets', '#assetRows tr'), ('/settings', '[data-monitor-account]'),
            ('/tikhub', '#parameterFields'), ('/workspace#engines', '#engineRows tr'),
            ('/studio', '#operation'), ('/manage#settings', '#page-settings.active')]:
        page.goto('http://127.0.0.1:8795' + route)
        page.wait_for_selector(selector)
        if route == '/screen':
            page.wait_for_function("document.querySelector('#rankCaption')?.textContent === '644 条作品'")
        assert page.locator('.gc-nav a').count() == 5
        assert page.locator('.gc-nav .is-active,.gc-settings.is-active').count() == 1, route
        report['pages'].append(route)
        if route.startswith('/research') or route in ('/home', '/screen', '/settings', '/collect', '/assets'):
            page.screenshot(path=str(OUT / (route.split('?')[0].strip('/') + '.png')), full_page=True)
    page.goto('http://127.0.0.1:8795/collect?action=playback&video=7703151458754472787')
    page.wait_for_function("document.querySelector('#pageStatus').textContent.includes('更新于')")
    page.click('#previewCollect')
    page.wait_for_function("document.querySelector('#collectPreview').textContent.includes('最多 1 次请求')")
    report['preview'] = page.locator('#collectPreview').inner_text()
    assert not page.locator('#confirmCollect').is_checked()
    assert page.locator('#startCollect').is_disabled()
    for route in ('/home', '/screen', '/research?video=7703151458754472787', '/collect', '/assets', '/settings'):
        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto('http://127.0.0.1:8795' + route)
        page.wait_for_timeout(450)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), route
        page.click('.gc-menu')
        assert page.locator('body').evaluate("e=>e.classList.contains('gc-nav-open')")
        page.keyboard.press('Escape')
    browser.close()
(OUT / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
assert not report['errors'], report['errors']
assert not report['failed_requests'], report['failed_requests']
