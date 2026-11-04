"""Read-only page audit. Save endpoint names and metric evidence, never auth headers."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from collector import DouyinCollector
from config import load_config


def metrics(value, path="", found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        for key, child in value.items():
            current = path + "." + key
            if key in {"play_count", "watch_cnt", "view_count", "playCount"}:
                found.append({"field": current, "value": child})
            elif isinstance(child, (dict, list)):
                metrics(child, current, found)
    elif isinstance(value, list):
        for index, child in enumerate(value[:4]):
            metrics(child, path + f"[{index}]", found)
    return found


if __name__ == "__main__":
    report = []
    with DouyinCollector({**load_config(), "headless": True}, log=lambda _: None) as col:
        observed = []
        def response(res):
            path = urlsplit(res.url).path
            if not any(word in path.lower() for word in ("/api/", "/aweme/", "/web/api")):
                return
            try:
                value = res.json()
                found = metrics(value)
                observed.append({"host": urlsplit(res.url).hostname, "path": path,
                                 "http": res.status, "metrics": found[:15],
                                 "keys": list(value)[:8] if isinstance(value, dict) else []})
            except Exception:
                pass
        col._page.on("response", response)
        for label, url in [("xingtu", "https://www.xingtu.cn/"),
                           ("creator", "https://creator.douyin.com/creator-micro/content/manage")]:
            observed.clear()
            try:
                col._page.goto(url, wait_until="domcontentloaded", timeout=35000)
                col._page.wait_for_timeout(7000)
                row = {"page": label, "path": urlsplit(col._page.url).path,
                       "title": col._page.title(), "login_visible":
                       col._page.get_by_text("扫码登录", exact=False).count() > 0,
                       "endpoints": list(observed)}
                col._page.screenshot(path=str(ROOT / "output" / f"audit-{label}.png"))
            except Exception as exc:
                row = {"page": label, "error": type(exc).__name__, "endpoints": list(observed)}
            report.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "output" / "playback-source-pages.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
