"""Single-instance web application; server deployments use login and a reverse proxy."""
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze
import intelligence
import insights
import douk
import playback
import tikhub
import workbench
import monitoring
import integrations
import corpus
import store
import auth
import discovery
from config import (ROOT, WEBUI_DIR, ensure_dirs, load_config, resolve_output_dir,
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
douk.BROWSER_STATE = STATE
SCHED = None
MONITOR_SCHED = None


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


def start_login_async():
    """Use the collector's own persistent profile; never request credentials in the app."""
    with STATE.lock:
        if STATE.running:
            return False
        STATE.running = True
        STATE.stop_flag = False
        STATE.done = STATE.total = 0
        STATE.msg = "请在打开的抖音窗口登录，完成后关闭该窗口"
    def worker():
        from collector import DouyinCollector
        try:
            cfg = {**load_config(), "headless": False}
            with DouyinCollector(cfg, log=STATE.log) as col:
                col._page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
                STATE.log("请在抖音官方窗口完成登录，随后关闭窗口；登录状态只保存在本机采集浏览器。")
                deadline = time.monotonic() + 600
                while not STATE.stop_flag and time.monotonic() < deadline and not col._page.is_closed():
                    col._page.wait_for_timeout(500)
        except Exception as exc:
            if "closed" not in str(exc).lower():
                STATE.log(f"登录窗口：{str(exc)[:160]}")
        finally:
            with STATE.lock:
                STATE.running = False
                STATE.msg = "登录窗口已关闭，可重新采集"
    STATE.thread = threading.Thread(target=worker, daemon=True)
    STATE.thread.start()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "DouyinMonitor/1.0"

    def log_message(self, *a):
        pass

    # ---------- 工具 ----------
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def _redirect(self, location, code=302):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, obj, code=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
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
            auth_on = auth.enabled()
            if path == "/api/auth/session":
                username = auth.current_user(self.headers) if auth_on else None
                return self._json({"enabled": auth_on, "authenticated": bool(username), "username": username})
            if path in {"/login.css", "/login.js", "/theme.css", "/theme.js"}:
                return self._file(path[1:])
            if path == "/login":
                target = auth.safe_next(parse_qs(urlparse(self.path).query).get("next", ["/home"])[0])
                if not auth_on or auth.current_user(self.headers):
                    return self._redirect(target)
                return self._file("login.html")
            if auth_on and not auth.current_user(self.headers):
                if path.startswith("/api/"):
                    return self._json({"error": "请先登录", "login_required": True}, 401)
                from urllib.parse import quote
                return self._redirect("/login?next=" + quote(auth.safe_next(self.path), safe=""))
            if path in ("/home", "/collect", "/research", "/assets", "/settings"):
                return self._file("workbench.html")
            if path in ("/workbench.js", "/workbench.css", "/monitoring-settings.js", "/screen-refresh.js", "/product.js", "/product.css"):
                return self._file(path[1:])
            if path == "/analytics":
                return self._file("analytics.html")
            if path in ("/analytics.js", "/analytics.css", "/vendor/echarts/echarts.min.js"):
                return self._file(path[1:])
            if path == "/api/analytics":
                import analytics
                args=parse_qs(urlparse(self.path).query)
                return self._json(analytics.dashboard(args.get('account',[''])[0],args.get('days',['30'])[0],args.get('keyword',[''])[0]))
            if path == "/discover":
                return self._file("discovery-hub.html")
            if path == "/discover/advanced":
                return self._file("discovery.html")
            if path in ("/discovery-hub.js", "/discovery-hub.css"):
                return self._file(path[1:])
            if path.startswith('/api/discovery/hub/'):
                import discovery_hub as hub
                args = parse_qs(urlparse(self.path).query)
                try:
                    query = json.loads(args.get('query', ['{}'])[0])
                    if path.endswith('/library'):
                        return self._json(hub.library(query))
                    if path.endswith('/history'):
                        return self._json(hub.history(args.get('kind', ['works'])[0], args.get('id', [''])[0]))
                    if path.endswith('/automation'):
                        return self._json(hub.automation())
                    if path.endswith('/run'):
                        return self._json(hub.run_state(args.get('id', [''])[0]))
                    if path.endswith('/reports'):
                        return self._json(hub.reports(args.get('id', [None])[0]))
                    if path.endswith('/export') or path.endswith('/report-export'):
                        report = path.endswith('/report-export')
                        body = hub.export_report(args.get('id', [''])[0]) if report else hub.export_library(query)
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html; charset=utf-8' if report else 'text/csv; charset=utf-8')
                        self.send_header('Content-Disposition', 'attachment; filename="'+('discovery-report.html' if report else 'discovery-library.csv')+'"')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        return self.wfile.write(body)
                except (ValueError, TypeError) as exc:
                    return self._json({'error': str(exc)}, 400)
            if path in ("/discovery.js", "/discovery.css", "/audience.js"):
                return self._file(path[1:])
            if path == "/api/discovery/catalog":
                return self._json(discovery.catalog())
            if path == "/api/discovery/rules":
                return self._json(discovery.rules())
            if path == "/api/discovery/watches":
                return self._json(discovery.watches())
            if path in ("/api/discovery/cache", "/api/discovery/export"):
                args = parse_qs(urlparse(self.path).query)
                try:
                    query = json.loads(args.get("query", ["{}"])[0])
                    if path.endswith("/cache"):
                        return self._json(discovery.cached(query))
                    body, ctype = discovery.export(query, args.get("format", ["csv"])[0])
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", 'attachment; filename="discovery-export.' + ("json" if ctype.startswith("application/json") else "csv") + '"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path == "/api/workbench/state":
                return self._json(workbench.state())
            if path == "/api/monitoring/status":
                return self._json(monitoring.status())
            if path == "/api/workbench/jobs":
                return self._json({"items": douk.jobs()})
            if path == "/api/workbench/research":
                args = parse_qs(urlparse(self.path).query)
                return self._json(workbench.research(args.get("video", [""])[0]))
            if path in ("/", "/index.html"):
                self.send_response(302)
                self.send_header("Location", "/home")
                self.end_headers()
                return
            if path == "/manage":
                return self._file("index.html")
            if path == "/workspace":
                return self._file("workspace.html")
            if path == "/tikhub":
                return self._file("tikhub.html")
            if path in ("/tikhub.js", "/tikhub.css"):
                return self._file(path[1:])
            if path == "/api/tikhub/catalog":
                return self._json(tikhub.catalog())
            if path == "/api/tikhub/status":
                return self._json({"settings": playback.settings(), "today_calls": tikhub.usage(),
                                   "jobs": [j for j in douk.jobs() if j.get("endpoint") == "tikhub_api"]})
            if path == "/api/tikhub/operation":
                args = parse_qs(urlparse(self.path).query)
                return self._json(tikhub.detail(args.get("id", [""])[0]))
            if path in ("/workspace.js", "/workspace.css", "/shell.js", "/shell.css"):
                return self._file(path[1:])
            if path == "/api/integrations/status":
                return self._json(integrations.status())
            if path == "/api/integrations/assets":
                return self._json(integrations.assets())
            if path == "/api/integrations/logins":
                return self._json(integrations.login_status())
            if path == "/api/integrations/contents":
                args = parse_qs(urlparse(self.path).query)
                return self._json(corpus.contents(args.get("platform", [""])[0], args.get("q", [""])[0]))
            if path == "/api/integrations/content-comments":
                args = parse_qs(urlparse(self.path).query)
                return self._json(corpus.comments(args.get("platform", [""])[0],args.get("content_id", [""])[0]))
            if path == "/api/integrations/inventory":
                target = ROOT / "outputs" / "01a1a206-0159-7d73-836a-2c0d96ac52d4" / "项目接入清单.xlsx"
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", 'attachment; filename="project-inventory.xlsx"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path == "/screen":
                return self._file("screen.html")
            if path in ("/screen.js", "/screen.css"):
                return self._file(path[1:])
            if path == "/studio":
                return self._file("studio.html")
            if path in ("/studio.js", "/studio.css"):
                return self._file(path[1:])
            if path == "/api/douk/catalog":
                return self._json(douk.catalog())
            if path == "/api/douk/status":
                return self._json(douk.status())
            if path == "/api/playback/status":
                return self._json(playback.status())
            if path.startswith("/api/douk/jobs/"):
                return self._json(douk.job(path.rsplit("/", 1)[-1], True))
            if path == "/api/douk/export":
                args = parse_qs(urlparse(self.path).query)
                jid, fmt = args.get("id", [""])[0], args.get("format", ["json"])[0]
                body, ctype = douk.export(jid, fmt)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="douk-{jid}.{fmt}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path in {"/api/douk/file", "/api/integrations/asset"}:
                args = parse_qs(urlparse(self.path).query)
                target = (integrations.asset_path(args.get("source", [""])[0],args.get("path", [""])[0])
                    if path == "/api/integrations/asset" else douk.file_path(args.get("path", [""])[0]))
                size = target.stat().st_size
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                if range_header:
                    import re
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                    try:
                        if not match or not any(match.groups()) or not size:
                            raise ValueError()
                        left, right = match.groups()
                        start = int(left) if left else max(0, size - int(right))
                        end = min(size - 1, int(right)) if left and right else size - 1
                        if start > end or start >= size:
                            raise ValueError()
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.send_header("Accept-Ranges", "bytes")
                if args.get("download", [""])[0] == "1":
                    from urllib.parse import quote
                    self.send_header("Content-Disposition", "attachment; filename=\"download" + target.suffix + "\"; filename*=UTF-8''" + quote(target.name, safe=""))
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("X-Content-Type-Options", "nosniff")
                if path == "/api/integrations/asset":
                    self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:")
                self.end_headers()
                with target.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            if path.startswith("/media/"):
                from media_cache import media_path
                p = media_path(path[len("/media/"):])
                if not p or not p.is_file():
                    return self.send_error(404)
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(p.name)[0])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, no-store" if auth_on else "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                return self.wfile.write(body)
            if path == "/api/insights":
                args = parse_qs(urlparse(self.path).query)
                return self._json(insights.dashboard(args.get("days", ["30"])[0],
                    args.get("role", ["all"])[0], args.get("account", [""])[0], args.get("topic", [""])[0], args.get("query", [""])[0]))
            if path == "/app.js":
                return self._file("app.js")
            if path == "/ai.js":
                return self._file("ai.js")
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
                details = {m["mix_id"]: analyze.mix_detail(m["mix_id"], m)
                           for m in ov["mixes"]}
                return self._json({"overview": ov, "details": details})
            if path == "/api/runs":
                return self._json({"runs": analyze.recent_runs(15)})
            if path == "/api/health":
                return self._json(analyze.health())
            if path == "/api/intel/tracked-accounts":
                return self._json({"items": intelligence.tracked_account_summary()})
            if path == "/api/intel/discovered-videos":
                return self._json({"items": intelligence.discovered_videos()})
            if path.startswith("/api/intel/video-history/"):
                aweme_id = path.rsplit("/", 1)[-1]
                return self._json({"items": intelligence.video_history(aweme_id)})
            if path == "/api/intel/videos":
                return self._json({"items": intelligence.videos()})
            if path == "/api/intel/accounts":
                return self._json({"items": intelligence.accounts()})
            if path == "/api/intel/collections":
                return self._json({"items": intelligence.collections()})
            if path == "/api/intel/alerts":
                return self._json({"items": intelligence.alerts()})
            return self.send_error(404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            return
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            auth_on = auth.enabled()
            if auth_on and not auth.same_origin(self.headers):
                return self._json({"error": "请从本站页面提交请求"}, 403)
            if path == "/api/auth/login":
                if int(self.headers.get("Content-Length", "0")) > 8192:
                    return self._json({"error": "登录请求过大"}, 413)
                data = self._body()
                if not isinstance(data, dict):
                    return self._json({"error": "登录格式错误"}, 400)
                value, age = auth.login(data.get("username"), data.get("password"), auth.client_ip(self))
                return self._json({"ok": True, "next": auth.safe_next(data.get("next"))},
                                  headers={"Set-Cookie": auth.session_cookie(value, age)})
            if path == "/api/auth/logout":
                auth.logout(self.headers)
                return self._json({"ok": True}, headers={"Set-Cookie": auth.session_cookie("", 0)})
            if auth_on and not auth.current_user(self.headers):
                return self._json({"error": "请先登录", "login_required": True}, 401)
        except auth.TooManyAttempts as exc:
            return self._json({"error": str(exc)}, 429, headers={"Retry-After": "600"})
        except ValueError as exc:
            return self._json({"error": str(exc)}, 401)
        except Exception:
            return self._json({"error": "登录服务暂不可用"}, 503)
        if not auth_on and path.startswith(("/api/douk/", "/api/playback/", "/api/integrations/", "/api/tikhub/", "/api/workbench/", "/api/monitoring/", "/api/discovery/")):
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self._json({"error": "只接受本机页面发起的操作"}, 403)
        data = self._body()
        try:
            if path == "/api/discovery/plan":
                return self._json(discovery.plan(data))
            if path.startswith('/api/discovery/hub/'):
                import discovery_hub as hub
                if path.endswith('/automation'):
                    return self._json(hub.save_config(data))
                if path.endswith('/sync-plan'):
                    return self._json(hub.sync_plan(data))
                if path.endswith('/sync'):
                    return self._json(hub.submit_sync(data), 202)
                if path.endswith('/generate'):
                    return self._json(hub.generate_report(data))
            if path == "/api/discovery/submit":
                return self._json(discovery.submit(data), 202)
            if path == "/api/discovery/settings":
                return self._json(discovery.save_settings(data))
            if path == "/api/discovery/watches":
                return self._json(discovery.watches(data))
            if path == "/api/discovery/rules":
                return self._json(discovery.save_rule(data))
            if path == "/api/monitoring/settings":
                return self._json(monitoring.save(data))
            if path == "/api/monitoring/preview":
                return self._json(monitoring.preview(monitoring.validate(data)))
            if path == "/api/workbench/refresh-plan":
                return self._json(workbench.plan({**data, "action": "playback"}))
            if path == "/api/workbench/refresh":
                return self._json(workbench.submit({**data, "action": "playback"}), 202)
            if path == "/api/workbench/plan":
                return self._json(workbench.plan(data))
            if path == "/api/workbench/submit":
                return self._json(workbench.submit(data), 202)
            if path == "/api/tikhub/plan":
                return self._json(tikhub.plan(data))
            if path == "/api/integrations/login":
                return self._json(integrations.save_login(data.get("platform"),data.get("cookie")))
            if path == "/api/integrations/open-extension":
                return self._json(integrations.open_extension())
            if path == "/api/playback/settings":
                return self._json(playback.save_settings(data))
            if path == "/api/playback/plan":
                return self._json(playback.plan(data.get("mode", "app"), data.get("video_ids")))
            if path == "/api/douk/jobs":
                return self._json(douk.submit(data), 202)
            if path == "/api/douk/cancel":
                return self._json(douk.cancel(data.get("id", "")))
            if path == "/api/douk/retry":
                return self._json(douk.retry(data.get("id", "")), 202)
            if path == "/api/douk/reset-downloads":
                return self._json(douk.reset_download_records(data.get("ids", [])))
            if path == "/api/douk/settings":
                return self._json(douk.save_settings(data))
            if path == "/api/douk/import":
                result = douk.job(data.get("id", ""), True)
                return self._json(douk.import_rows(result["result"].get("data", []), result["platform"]))
            if path == "/api/intel/account":
                items = intelligence.add_account(data.get("url", ""), data.get("note", ""), data.get("role"))
                return self._json({"ok": True, "items": items})
            if path == "/api/intel/account-role":
                intelligence.set_account_role(data.get("sec_uid", ""), data.get("role"))
                return self._json({"ok": True})
            if path == "/api/intel/account-settings":
                items = intelligence.update_account_settings(data.get("sec_uid", ""), data.get("note", ""),
                    data.get("role"), data.get("enabled"))
                return self._json({"ok": True, "items": items})
            if path == "/api/intel/account-toggle":
                items = intelligence.toggle_tracked_account(data.get("sec_uid", ""), bool(data.get("enabled")))
                return self._json({"ok": True, "items": items})
            if path == "/api/intel/account-delete":
                items = intelligence.delete_tracked_account(data.get("sec_uid", ""))
                return self._json({"ok": True, "items": items})
            if path == "/api/intel/group":
                return self._json({"ok": True, "items": intelligence.create_collection(data.get("name"), data.get("keywords", ""))})
            if path == "/api/intel/ai-summary":
                result = intelligence.ai_summary(data.get("niche", ""), data.get("stage", ""), data.get("format", ""), data.get("frequency", 5))
                return self._json(result)
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
            if path == "/api/login":
                return self._json({"ok": start_login_async()})
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
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
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
    allow_reuse_address = os.name != "nt"


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
    global SCHED, MONITOR_SCHED
    _setup_console()
    ensure_dirs()
    auth.settings()  # Fail closed if a server's configured login file is missing.
    store.init_db()
    douk.init_tables()
    playback.init()
    import metric_history
    metric_history.init()
    corpus.init()
    discovery.init()
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

    douk.recover()
    monitoring.recover()

    SCHED = Scheduler(trigger=start_run_async, log=STATE.log)
    SCHED.start()
    MONITOR_SCHED = monitoring.MonitorScheduler(log=STATE.log)
    MONITOR_SCHED.start()

    httpd = None
    for p in ((port,) if auth.enabled() else range(port, port + 20)):
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
        f"   旧采集定时：{SCHED.next_run_text()}",
        f"   播放刷新定时：{monitoring.next_run() or '未启用（可在设置页配置）'}",
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
        if MONITOR_SCHED:
            MONITOR_SCHED.stop()
        httpd.server_close()


def main():
    _setup_console()
    no_browser = "--no-browser" in sys.argv
    serve(open_browser=not no_browser)


if __name__ == "__main__":
    main()
