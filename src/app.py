"""本地服务：提供操作界面 + 本地接口。仅监听 127.0.0.1，不对外开放。"""
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze
import store
from config import (WEBUI_DIR, ensure_dirs, load_config, resolve_output_dir,
                    save_config)
from pipeline import run_once
from resolve import extract_urls, parse_aweme_id, resolve_aweme_id
from scheduler import Scheduler


class RunState:
    def __init__(self):
        # 用 RLock：log() 可能在已持锁的路径里被调用，普通 Lock 会死锁
        self.lock = threading.RLock()
        self.running = False
        self.stop_flag = False
        self.done = 0
        self.total = 0
        self.msg = ""
        self.logs = []
        self.started_at = None
        self.finished_at = None
        self.last_result = None
        self.thread = None

    def log(self, m):
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 800:
                self.logs = self.logs[-800:]
        print(line, flush=True)

    def progress(self, done, total, msg):
        with self.lock:
            self.done, self.total, self.msg = done, total, msg

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running, "done": self.done, "total": self.total,
                "msg": self.msg, "started_at": self.started_at,
                "finished_at": self.finished_at, "last_result": self.last_result,
                "logs": self.logs[-300:],
            }


STATE = RunState()
SCHED = None


def start_run_async():
    with STATE.lock:
        if STATE.running:
            return False
        STATE.running = True
        STATE.stop_flag = False
        STATE.done = 0
        STATE.total = 0
        STATE.msg = "正在启动 ..."
        STATE.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE.finished_at = None

    def worker():
        try:
            res = run_once(progress=STATE.progress, log=STATE.log,
                           should_stop=lambda: STATE.stop_flag,
                           cfg=load_config())
            with STATE.lock:
                STATE.last_result = res
        except Exception as e:
            STATE.log(f"抓取异常终止：{e}")
        finally:
            with STATE.lock:
                STATE.running = False
                STATE.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    STATE.thread = threading.Thread(target=worker, daemon=True)
    STATE.thread.start()
    return True


def stop_run():
    with STATE.lock:
        if not STATE.running:
            return False
        STATE.stop_flag = True
    STATE.log("已请求停止，将在当前条目结束后中断")
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "DouyinMonitor/1.0"

    def log_message(self, *a):
        pass

    # ---------- 工具 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _file(self, rel):
        p = (WEBUI_DIR / rel).resolve()
        if not str(p).startswith(str(WEBUI_DIR.resolve())) or not p.exists():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if p.suffix in (".html", ".js", ".css"):
            ctype += "; charset=utf-8"
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._file("index.html")
            if path == "/app.js":
                return self._file("app.js")
            if path == "/style.css":
                return self._file("style.css")

            if path == "/api/state":
                cfg = load_config()
                return self._json({
                    "run": STATE.snapshot(),
                    "stats": analyze.watch_stats(),
                    "config": cfg,
                    "today": store.today_str(),
                    "effective_output_dir": str(resolve_output_dir(cfg)),
                    "next_run": SCHED.next_run_text() if SCHED else "",
                    "overview": {k: v for k, v in analyze.overview().items() if k != "mixes"},
                })
            if path == "/api/watchlist":
                return self._json({"items": store.list_watch_items(),
                                   "stats": analyze.watch_stats()})
            if path == "/api/result":
                ov = analyze.overview()
                details = {m["mix_id"]: analyze.mix_detail(m["mix_id"], m)["episodes"]
                           for m in ov["mixes"]}
                return self._json({"overview": ov, "details": details})
            if path == "/api/runs":
                return self._json({"runs": analyze.recent_runs(15)})
            if path == "/api/health":
                return self._json(analyze.health())
            return self.send_error(404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._body()
        try:
            if path == "/api/watchlist/add":
                urls = extract_urls(data.get("text", ""))
                if not urls:
                    return self._json({"ok": False, "error": "未识别到抖音链接"}, 400)
                added, dup = store.add_watch_items(urls, data.get("note", ""))
                return self._json({"ok": True, "added": added, "dup": dup,
                                   "total": len(urls)})
            if path == "/api/watchlist/toggle":
                store.set_watch_enabled(int(data["id"]), bool(data.get("enabled")))
                return self._json({"ok": True})
            if path == "/api/watchlist/delete":
                store.delete_watch_item(int(data["id"]))
                return self._json({"ok": True})
            if path == "/api/watchlist/resolve":
                n = 0
                for it in store.list_watch_items():
                    if it.get("aweme_id"):
                        continue
                    aid = resolve_aweme_id(it["url"])
                    if aid:
                        store.update_watch_resolved(it["id"], {"aweme_id": aid})
                        n += 1
                return self._json({"ok": True, "resolved": n})
            if path == "/api/run":
                return self._json({"ok": start_run_async()})
            if path == "/api/stop":
                return self._json({"ok": stop_run()})
            if path == "/api/config":
                cfg = load_config()
                cfg.update(data or {})
                save_config(cfg)
                return self._json({"ok": True, "config": cfg})
            if path == "/api/export":
                import report
                return self._json({"ok": True, "path": report.export_daily()})
            if path == "/api/dashboard":
                import dashboard
                return self._json({"ok": True, "path": dashboard.export_dashboard()})
            if path == "/api/open":
                target = data.get("path")
                if target and Path(target).exists():
                    os.startfile(target)  # noqa: S606  (Windows)
                    return self._json({"ok": True})
                return self._json({"ok": False, "error": "文件不存在"}, 400)
            return self.send_error(404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def _setup_console():
    """让 Windows 控制台正确显示中文，避免双击 exe 后黑窗口里是乱码。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


class LocalHTTPServer(ThreadingHTTPServer):
    """Windows 上必须关掉 allow_reuse_address。

    Python 默认会设 SO_REUSEADDR，而 Windows 下它允许**两个进程绑同一个端口**，
    导致「端口被占用就换下一个」的逻辑失效、请求随机落到某个实例上。
    """
    allow_reuse_address = False


def _existing_instance_alive(port, timeout=1.5) -> bool:
    """探测指定端口上是否已经有本程序在跑。"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/state", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return isinstance(data, dict) and "run" in data and "config" in data
    except Exception:
        return False


def serve(port=None, open_browser=True):
    global SCHED
    _setup_console()
    ensure_dirs()
    store.init_db()
    n = store.reap_stale_runs()
    if n:
        STATE.log(f"清理了 {n} 条上次异常退出留下的运行记录")
    # 补齐可能因中途异常而漏算的首播日（对老库无副作用，缺失 create_time 时直接返回 0）
    try:
        m = store.backfill_first_publish()
        if m:
            STATE.log(f"补齐了 {m} 个合集的发布日期")
    except Exception:
        pass
    cfg = load_config()
    port = int(port or cfg.get("port") or 8765)

    # 已经在跑就不重复启动，直接把界面打开
    if _existing_instance_alive(port):
        url = f"http://127.0.0.1:{port}/"
        print(f"\n程序已经在运行中，直接打开界面：{url}\n", flush=True)
        if open_browser:
            webbrowser.open(url)
        return

    SCHED = Scheduler(trigger=start_run_async, log=STATE.log)
    SCHED.start()

    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = LocalHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("端口被占用，无法启动本地服务")

    url = f"http://127.0.0.1:{port}/"
    STATE.log(f"本地界面地址：{url}")
    banner = "\n".join([
        "",
        "=" * 58,
        "   抖音合集播放量监控  已启动",
        f"   界面地址：{url}",
        "   界面会自动在浏览器中打开",
        "   若没弹出，手动把上面这个地址粘到浏览器地址栏",
        "-" * 58,
        f"   定时抓取：{SCHED.next_run_text()}",
        "   [重要] 不要关闭这个黑色窗口，关闭窗口 = 退出程序",
        "=" * 58,
        "",
    ])
    print(banner, flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if SCHED:
            SCHED.stop()
        httpd.server_close()


def main():
    _setup_console()
    no_browser = "--no-browser" in sys.argv
    serve(open_browser=not no_browser)


if __name__ == "__main__":
    main()
