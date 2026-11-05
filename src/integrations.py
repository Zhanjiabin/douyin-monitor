"""Project-local MediaCrawlerPro service manager and task/data bridge."""
import ast
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import requests
from config import ROOT, DATA_DIR, OUTPUT_DIR
import store

VENDOR = ROOT / "vendor" / "MediaCrawlerPro"
RUNTIME = DATA_DIR / "mcpro"
LOCK = threading.RLock()
PROCESSES = {}
WORKER = ROOT / "scripts" / "mcpro_worker.py"
SERVICES = {
    "database": {"name": "分析数据库", "port": 13306, "health": None, "dependencies": []},
    "mcpro-sign": {"name": "签名服务", "folder": "MediaCrawlerPro-SignSrv", "kind": "sign", "port": 8989, "health": "/signsrv/pong", "dependencies": []},
    "mcpro-cookie": {"name": "登录同步", "folder": "MediaCrawlerPro-CookieBridge/server", "kind": "cookie", "port": 8274, "health": "/ping", "dependencies": []},
    "mcpro-downloader": {"name": "多平台解析", "folder": "MediaCrawlerPro-Downloader/DownloadServer", "kind": "downloader", "port": 8205, "health": "/ping", "dependencies": ["mcpro-sign"]},
    "mcpro-remix": {"name": "内容分析", "folder": "MediaCrawlerPro-ContentRemixAgent/backend", "kind": "remix", "port": 8001, "health": "/health", "dependencies": ["database", "mcpro-downloader"]},
    "pictactic": {"name": "图片生成", "folder": "PicTacticAgent", "kind": "pictactic", "port": 8019, "health": "/health", "dependencies": []},
    "mcpro-vox": {"name": "评论研究", "folder": "MediaCrawlerPro-VoxAgent", "port": 4318, "health": "/api/health", "dependencies": ["mcpro-sign", "mcpro-cookie"]},
    "remix-web": {"name": "内容工作室", "port": 5373, "health": "/", "web": "remix", "dependencies": ["mcpro-remix"]},
    "pictactic-web": {"name": "图片工作室", "port": 3019, "health": "/", "web": "pictactic", "dependencies": ["pictactic"]},
    "vox-web": {"name": "评论工作室", "port": 4319, "health": "/", "web": "vox", "dependencies": ["mcpro-vox"]},
}
CRAWL_MODES={platform:["search","detail","creator",*(["homefeed"] if platform not in {"wb","tieba","reddit"} else []),
    *(["hot_search"] if platform!="reddit" else [])] for platform in ("dy","xhs","bili","ks","wb","tieba","zhihu","reddit")}


def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def runtime_secrets():
    path = RUNTIME / "app-secrets.json"
    value = read(path)
    if not value:
        value = {"jwt": secrets.token_urlsafe(48), "internal_api_key": secrets.token_urlsafe(32)}
        write(path, value)
    return value


def local_session(kind):
    """Provision a local-only account so the unified workspace shares a sign-in."""
    if kind not in {"remix", "pictactic"}:
        raise ValueError("未知工作室")
    port = 8001 if kind == "remix" else 8019
    with LOCK:
        file = RUNTIME / (kind + "-session.json")
        cfg = read(file, {})
        if cfg.get("valid_until", 0) > time.time() + 30:
            return cfg["session"]
        if not cfg.get("password"):
            cfg.update(email="observatory-" + secrets.token_hex(4) + "@local.example.com",
                       password="Ob1!" + secrets.token_urlsafe(28))
            write(file, cfg)
            response = requests.post(f"http://127.0.0.1:{port}/api/v1/auth/register",
                json={"email": cfg["email"], "password": cfg["password"], "display_name": "暖暖影视本机用户"}, timeout=20)
        else:
            response = requests.post(f"http://127.0.0.1:{port}/api/v1/auth/login",
                json={"email": cfg["email"], "password": cfg["password"]}, timeout=20)
        if response.status_code not in {200, 201}:
            raise ValueError("本机工作室会话初始化失败，请检查引擎日志")
        session = response.json()
        if not session.get("access_token"):
            raise ValueError("工作室未返回有效本机会话")
        cfg.update(session=session, valid_until=time.time()+600)
        write(file, cfg)
        return session


def python(folder):
    return VENDOR / folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def tcp(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.3):
            return True
    except OSError:
        return False


def node_binary():
    value = shutil.which("node") or "node"
    # Volta's Windows shim can re-quote query strings containing ampersands.
    volta = shutil.which("volta")
    if volta and "volta" in value.lower():
        probe = subprocess.run([volta,"which","node"],capture_output=True,text=True,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),timeout=10)
        resolved = probe.stdout.strip()
        if probe.returncode == 0 and Path(resolved).is_file():
            return resolved
    return value


def health(key):
    service = SERVICES[key]
    if not tcp(service["port"]):
        return False
    if not service["health"]:
        return True
    try:
        response = requests.get(f"http://127.0.0.1:{service['port']}"+service["health"], timeout=1)
        return response.status_code == 200
    except requests.RequestException:
        return False


def command(key):
    service = SERVICES[key]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "IS_DEBUG": "false", "APP_HOST": "127.0.0.1", "APP_ADDRESS": "127.0.0.1",
           "APP_PORT": str(service["port"]), "NODE_BIN": node_binary()}
    cfg = runtime_secrets()
    if key == "database":
        db = read(RUNTIME / "database.json")
        if not db:
            raise ValueError("请先运行 scripts/setup_mcpro_database.py")
        args = [str(Path(db["base"]) / "bin" / "mariadbd.exe"),
                "--defaults-file="+str(Path(db["data_dir"]) / "my.ini"),
                "--bind-address=127.0.0.1", "--port="+str(db["port"]),
                "--skip-name-resolve", "--innodb-buffer-pool-size=64M"]
    elif service.get("web"):
        args = [sys.executable, str(ROOT / "scripts" / "mcpro_webhost.py"), service["web"]]
    elif key == "mcpro-vox":
        node = node_binary()
        folder = VENDOR / service["folder"]
        args = [node, "--import", (folder / "node_modules" / "tsx" / "dist" / "loader.mjs").as_uri(),
                str(folder / "apps" / "local-server" / "src" / "cli.ts"), "--port", str(service["port"]), "--no-open"]
        env.update(VOX_HOST="127.0.0.1", VOX_SIGN_SERVICE_AUTO_START="false")
        env.update(MCPRO_ROOT=str(VENDOR), SRC_DB_TYPE="sqlite", DST_DB_TYPE="sqlite",
                   SRC_SQLITE_PATH=str(RUNTIME / "crawler.db"), DST_SQLITE_PATH=str(RUNTIME / "vox-review.db"),
                   MCPRO_ACCOUNT_POOL_SAVE_TYPE="cookie_bridge")
    else:
        executable = python(service["folder"])
        if not executable.exists():
            raise ValueError("运行环境未安装，请运行 scripts/setup_mcpro.py")
        args = [str(executable), "-X", "utf8", str(WORKER), service["kind"]]
        if key == "mcpro-remix":
            db = read(RUNTIME / "database.json", {})
            env.update(AGENT_DB_HOST="127.0.0.1", AGENT_DB_PORT=str(db.get("port", 13306)),
                       AGENT_DB_USER=db.get("user", "root"), AGENT_DB_PASSWORD=db.get("password", ""),
                       AGENT_DB_NAME="observatory_remix", MEMORY_BACKEND="mysql", JWT_SECRET_KEY=cfg["jwt"],
                       DOWNLOAD_SERVER_BASE="http://127.0.0.1:8205", ASR_BACKEND="bcut", LOG_FORMAT="pretty")
        elif key == "pictactic":
            env.update(JWT_SECRET_KEY=cfg["jwt"], INTERNAL_API_KEY=cfg["internal_api_key"])
    return args, env


def start(key, timeout=75):
    if key not in SERVICES:
        raise ValueError("未知引擎")
    with LOCK:
        if health(key):
            return {"service": key, "status": "running", "port": SERVICES[key]["port"]}
        if tcp(SERVICES[key]["port"]):
            raise ValueError(f"端口 {SERVICES[key]['port']} 已占用，请检查运行中的服务")
        for dep in SERVICES[key]["dependencies"]:
            start(dep, timeout)
        args, env = command(key)
        logs = RUNTIME / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = (logs / (key + ".log")).open("a", encoding="utf-8")
        cwd = VENDOR / SERVICES[key]["folder"] if key == "mcpro-vox" else ROOT
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.close()
        PROCESSES[key] = proc
        saved = read(RUNTIME / "services.json", {})
        saved[key] = {"pid": proc.pid, "port": SERVICES[key]["port"], "started_at": store.now_str()}
        write(RUNTIME / "services.json", saved)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health(key):
            return {"service": key, "status": "running", "port": SERVICES[key]["port"]}
        if proc.poll() is not None:
            raise ValueError(f"{SERVICES[key]['name']} 启动失败；查看本机引擎日志")
        time.sleep(.5)
    raise ValueError(f"{SERVICES[key]['name']} 仍在启动，可刷新状态")


def start_all():
    result = []
    for key in SERVICES:
        try:
            result.append(start(key))
        except ValueError as exc:
            result.append({"service": key, "status": "failed", "message": str(exc)})
    if health("mcpro-cookie"):
        for platform,value in read(RUNTIME / "manual-logins.json",{}).items():
            try:
                save_login(platform,value,persist=False)
            except (ValueError,requests.RequestException):
                pass
        try:
            sync_douyin()
        except (ValueError,requests.RequestException):
            pass
    return result


def stop(key):
    if key not in SERVICES:
        raise ValueError("未知引擎")
    saved = read(RUNTIME / "services.json", {})
    pid = int(saved.get(key, {}).get("pid", 0))
    if not pid:
        raise ValueError("此服务不是当前系统启动的，不能停止")
    if os.name == "nt":
        def process_line(value):
            probe = subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(value)}').CommandLine"],
                    capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return probe.stdout.lower()
        line = process_line(pid)
        if str(ROOT).lower() not in line:
            # Windows venv redirectors and Node's TS loader may leave a child
            # listening after the recorded launcher exits. Verify both port and
            # exact project command before adopting that child.
            probe = subprocess.run(["powershell", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -LocalPort {SERVICES[key]['port']} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess"],
                capture_output=True,text=True,creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            candidate = int(probe.stdout.strip() or 0)
            line = process_line(candidate) if candidate else ""
            service = SERVICES[key]
            marker = ("mcpro_webhost.py " + service["web"] if service.get("web") else
                      "apps\\local-server\\src\\cli.ts" if key == "mcpro-vox" else
                      "mariadbd.exe" if key == "database" else "mcpro_worker.py " + service["kind"])
            if str(ROOT).lower() in line and marker.lower() in line:
                pid = candidate
            else:
                raise ValueError("进程归属已改变，未执行停止")
        if str(ROOT).lower() not in line:
            raise ValueError("进程归属已改变，未执行停止")
        if key == "database":
            db = read(RUNTIME / "database.json", {})
            subprocess.run([str(Path(db["base"]) / "bin" / "mariadb-admin.exe"),
                "--host=127.0.0.1", "--port="+str(db["port"]), "--user="+db["user"], "shutdown"],
                env={**os.environ, "MYSQL_PWD": db["password"]}, capture_output=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
        else:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    saved[key]["stopped_at"] = store.now_str()
    write(RUNTIME / "services.json", saved)
    return {"service": key, "status": "stopped"}


def status():
    projects = read(ROOT / "integrations" / "projects.json", {}).get("projects", [])
    locked = {r["name"]: r for r in read(ROOT / "integrations" / "mcpro.lock.json", {}).get("projects", [])}
    result = []
    for item in projects:
        item = dict(item)
        if item["name"] in locked:
            item.update(source_status="fetched", commit=locked[item["name"]]["commit"])
        if item["id"] in SERVICES:
            item["running"] = health(item["id"])
            item["port"] = SERVICES[item["id"]]["port"]
        result.append(item)
    return {"projects": result,"crawl_modes":CRAWL_MODES, "services": [{"id": key, "name": v["name"], "port": v["port"],
             "running": health(key)} for key, v in SERVICES.items()],
             "excluded": ["MediaCrawlerPro-Golang"]}


def sync_douyin():
    import douk
    value = douk.settings(False).get("cookie")
    if not value:
        raise ValueError("请先在采集工作台同步本机抖音登录")
    start("mcpro-cookie")
    response = requests.post("http://127.0.0.1:8274/api/cookies/dy/set",
                             json={"cookies": value, "client_id": "observatory"}, timeout=10)
    response.raise_for_status()
    body = response.json()
    if not body.get("isok"):
        raise ValueError("登录同步服务未接受 Cookie")
    accounts = requests.get("http://127.0.0.1:8274/api/accounts", timeout=10).json()
    if not any(a.get("client_id") == "observatory" and a.get("platforms", {}).get("dy", {}).get("has_cookies")
               for a in accounts.get("data", {}).get("accounts", [])):
        raise ValueError("登录已保存但未进入账号池，请重启登录同步服务")
    return {"synced": True, "platform": "dy"}


def login_status():
    result=[]
    if health("mcpro-cookie"):
        response=requests.get("http://127.0.0.1:8274/api/accounts",timeout=5).json()
        for account in response.get("data",{}).get("accounts",[]):
            for platform,info in account.get("platforms",{}).items():
                if info.get("has_cookies"):
                    result.append({"platform":platform,"connected":bool(account.get("connected"))})
    return {"accounts":result,"extension":"vendor/MediaCrawlerPro/MediaCrawlerPro-CookieBridge/extension"}


def save_login(platform,value,persist=True):
    if platform not in {"dy","xhs","bili","ks","wb","tieba","zhihu","reddit"} or not isinstance(value,str) or not value.strip():
        raise ValueError("请选择平台并填写有效 Cookie")
    start("mcpro-cookie")
    response=requests.post("http://127.0.0.1:8274/api/cookies/"+platform+"/set",
        json={"client_id":"observatory","cookies":value.strip()},timeout=10)
    response.raise_for_status()
    if not response.json().get("isok"):
        raise ValueError("登录同步服务未接受此 Cookie")
    if persist:
        saved=read(RUNTIME / "manual-logins.json",{})
        saved[platform]=value.strip()
        write(RUNTIME / "manual-logins.json",saved)
    return {"saved":True,"platform":platform}


def open_extension():
    target=(VENDOR / "MediaCrawlerPro-CookieBridge/extension").resolve()
    if not target.is_relative_to(VENDOR.resolve()) or not target.is_dir():
        raise ValueError("扩展源码目录不存在")
    os.startfile(str(target))
    return {"ok":True}


def execute(request, job_id):
    action = request.get("action")
    if action == "start_all":
        rows = start_all()
        return {"ok": all(x["status"] == "running" for x in rows), "data": rows}
    if action == "start":
        row = start(request.get("service"))
        return {"ok": True, "data": [row]}
    if action == "stop":
        return {"ok": True, "data": [stop(request.get("service"))]}
    if action == "sync_douyin":
        return {"ok": True, "data": [sync_douyin()]}
    if action == "crawl":
        return crawl(request, job_id)
    if action == "parse":
        import native_download
        return native_download.execute(request,job_id)
    raise ValueError("不支持的桥接操作")


def crawl(request, job_id):
    import douk
    platform = request.get("platform", "dy")
    mode = request.get("mode", "search")
    if platform not in {"dy", "xhs", "bili", "ks", "wb", "tieba", "zhihu", "reddit"} or mode not in {"search", "detail", "creator", "homefeed", "hot_search"}:
        raise ValueError("不支持的平台或采集类型")
    if mode not in CRAWL_MODES[platform]:
        raise ValueError("当前平台没有此采集方式，请选择支持的内容类型")
    count = int(request.get("count", 20))
    comments = int(request.get("max_comments", 20))
    if not 1 <= count <= 500 or not 0 <= comments <= 1000:
        raise ValueError("作品数或评论数超出范围")
    args = ["--platform", platform, "--type", mode,
            "--max_notes_count", str(count), "--max_comments_count", str(comments),
            "--enable_comments" if request.get("comments") and comments else "--no-enable_comments",
            "--enable_sub_comments" if request.get("sub_comments") else "--no-enable_sub_comments"]
    if mode == "search":
        keyword = str(request.get("input", "")).strip()
        if not keyword:
            raise ValueError("请输入关键词")
        args += ["--keywords", keyword]
    elif mode in {"detail", "creator"}:
        urls = str(request.get("input", "")).strip()
        if not urls:
            raise ValueError("请输入链接或 ID")
        values = [v.strip() for v in urls.splitlines() if v.strip()]
        if platform == "dy":
            pattern = r"/video/(\d+)" if mode == "detail" else r"/user/(MS4w[\w-]+)"
            values = [(re.search(pattern,v).group(1) if re.search(pattern,v) else v) for v in values]
        elif platform == "bili":
            pattern = r"(BV[\w]+)" if mode == "detail" else r"space\.bilibili\.com/(\d+)"
            values = [(re.search(pattern,v).group(1) if re.search(pattern,v) else v) for v in values]
        args += ["--urls", ",".join(values)]
    resume = request.get("checkpoint_id")
    if resume:
        if not re.fullmatch(r"[\w-]{1,100}", str(resume)):
            raise ValueError("断点 ID 无效")
        args += ["--enable_checkpoint", "--checkpoint_id", str(resume)]
    else:
        args += ["--no-enable_checkpoint" if request.get('discovery') else "--enable_checkpoint"]
    start("mcpro-sign"); start("mcpro-cookie")
    if platform == "dy":
        sync_douyin()
    folder = douk.metadata(job_id).parent
    native = folder / "mcpro-request.json"
    native_request={"arguments": args, "interval": 2}
    if request.get('discovery'):
        native_request.update(discovery=request['discovery'],capture_path=str(folder/'discovery-native.json'))
    write(native, native_request)
    env = {**os.environ, "DB_TYPE": "sqlite", "SQLITE_DB_PATH": str(RUNTIME / "crawler.db"),
           "SAVE_DATA_OPTION": "db", "ACCOUNT_POOL_SAVE_TYPE": "cookie_bridge",
           "COOKIE_BRIDGE_URL": "http://127.0.0.1:8274", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    # Native processors copy this at module import time, before CLI arguments run.
    env.update(PER_NOTE_MAX_COMMENTS_COUNT=str(comments), CRAWLER_MAX_NOTES_COUNT=str(count),
               ENABLE_GET_COMMENTS="true" if request.get("comments") and comments else "false")
    managed = platform in {"dy", "bili", "xhs", "reddit"}
    operation_id = str(resume or job_id)
    if managed:
        env.update(VOX_ACQUISITION_ID=operation_id, VOX_ACQUISITION_PLATFORM=platform,
                   VOX_ACQUISITION_RESUME="1" if resume else "0")
    started_ms = int(time.time()*1000)-1000
    with (folder / "engine.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([str(python("MediaCrawlerPro-Python")), "-X", "utf8", str(WORKER), "crawl", str(native)],
               cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with douk.LOCK:
            douk.ACTIVE[job_id] = proc
        try:
            deadline=time.monotonic()+3600
            position=0
            with (folder / "engine.log").open(encoding="utf-8",errors="replace") as progress_log:
                while proc.poll() is None:
                    for line in progress_log:
                        if line.startswith("@@MCPRO_EVENT@@"):
                            try:
                                event=json.loads(line[len("@@MCPRO_EVENT@@"):])
                                write(folder / "progress.json",{"done":event.get("item_count",0),"total":count,
                                    "count":event.get("item_count",0),"comments":event.get("comment_count",0),
                                    "checkpoint_id":event.get("checkpoint_id"),"current":event.get("current_object")})
                            except ValueError:
                                pass
                    if time.monotonic()>deadline:
                        raise subprocess.TimeoutExpired(proc.args,3600)
                    time.sleep(.5)
            code=proc.wait()
        except subprocess.TimeoutExpired:
            douk.terminate(proc)
            raise ValueError("采集超时，可使用断点继续")
    import corpus
    imported = corpus.import_native(RUNTIME / "crawler.db", platform, started_ms, job_id,operation_id if managed else None)
    rows = imported["data"]
    summary = {}
    for line in (folder / "engine.log").read_text(encoding="utf-8",errors="replace").splitlines():
        if line.startswith("---CRAWL_SUMMARY: ") and line.endswith("---"):
            try:
                summary = json.loads(line[len("---CRAWL_SUMMARY: "):-3])
            except ValueError:
                pass
    # Some upstream crawlers catch errors and exit 0 with no rows. Report that explicitly.
    ok = code == 0 and summary.get("status") == "completed" and bool(rows or imported["comments"])
    return {"ok": ok, **imported,
            "checkpoint_id": operation_id if managed else resume,
            "errors": [{"error": str(summary["error"])[:300]}] if summary.get("error") else [],
            "message": f"已入库 {len(rows)} 条作品、{imported['comments']} 条评论" if ok else
                "本次未取得内容；检查任务日志、链接与平台登录状态。已有数据已保留。"}


def asset_roots():
    return {
        "downloads": (OUTPUT_DIR / "downloads", "采集下载"),
        "mcpro": (RUNTIME / "downloads", "多平台下载"),
        "remix": (VENDOR / "MediaCrawlerPro-ContentRemixAgent/backend/data/assets", "内容分析"),
        "pictactic": (VENDOR / "PicTacticAgent/storage/images", "图片工作室"),
        "vox": (VENDOR / "MediaCrawlerPro-VoxAgent/reports", "评论研究"),
    }


ASSET_TYPES = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".md", ".html", ".srt", ".vtt", ".csv", ".xlsx"}


def asset_path(source, name):
    if source not in asset_roots():
        raise ValueError("未知素材来源")
    root = asset_roots()[source][0].resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in ASSET_TYPES:
        raise ValueError("素材不存在")
    return target


def assets():
    result=[]
    for source, (root, label) in asset_roots().items():
        if not root.is_dir():
            continue
        for target in root.rglob("*"):
            if target.is_file() and target.suffix.lower() in ASSET_TYPES and target.resolve().is_relative_to(root.resolve()):
                name=target.relative_to(root).as_posix()
                stat=target.stat()
                result.append({"name":name,"source":label,"size":stat.st_size,"modified":stat.st_mtime,
                    "url":"/api/integrations/asset?source="+source+"&path="+quote(name)})
    return {"items":sorted(result,key=lambda x:x["modified"],reverse=True)[:1000]}
