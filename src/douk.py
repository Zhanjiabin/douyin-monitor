"""Local task queue and data bridge for the pinned TikTokDownloader engine."""
from collections import deque
from datetime import datetime
import copy
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from config import ROOT, DATA_DIR, OUTPUT_DIR, load_config
import store
import intelligence

RUNTIME = DATA_DIR / "douk"
JOBS = RUNTIME / "jobs"
CATALOG = ROOT / "integrations" / "douk_catalog.json"
PYTHON = ROOT / ".douk-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
WORKER = ROOT / "scripts" / "douk_worker.py"
SECRET_KEYS = {"cookie", "cookie_tiktok", "authorization", "token", "mstoken", "sessionid", "sessionid_ss", "sid_tt", "headers", "params"}
LOCK = threading.RLock()
QUEUE = deque()
ACTIVE = {}
STARTED = False
BROWSER_STATE = None


def atomic(path, value):
    # Windows denies replace while another thread holds the target open.
    # Polling, cancellation and worker writes must share the same file lock.
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
        temp.replace(path)


def read(path, default=None):
    with LOCK:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return default


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(sessionid(?:_ss)?|sid_tt|msToken|ttwid|cookie|authorization)[=:][^\s,}]+", r"\1=[hidden]", value)
    return value


def catalog():
    cat = read(CATALOG, {})
    if not cat:
        raise ValueError("缺少引擎功能清单，请运行 scripts/setup_douk.py")
    return cat


def settings(public=True):
    result = copy.deepcopy(catalog()["defaults"])
    result.update({"root": str(OUTPUT_DIR / "downloads"), "storage_format": "xlsx", "max_pages": 30, "max_retry": 2})
    result.update(read(RUNTIME / "settings.json", {}))
    if public:
        result["cookie_set"] = bool(result.pop("cookie", ""))
        result["cookie_tiktok_set"] = bool(result.pop("cookie_tiktok", ""))
    return result


def save_settings(data):
    if not isinstance(data, dict):
        raise ValueError("设置必须是对象")
    allowed = set(catalog()["defaults"]) - {"run_command", "root"}
    unknown = set(data) - allowed - {"cookie_set", "cookie_tiktok_set", "root", "run_command"}
    if unknown:
        raise ValueError("未知设置：" + ", ".join(sorted(unknown)))
    with LOCK:
        value = settings(False)
        value.update({k: v for k, v in data.items() if k in allowed})
        for key in ("max_pages", "max_retry", "timeout", "chunk", "max_size"):
            value[key] = int(value[key])
            if value[key] < 0:
                raise ValueError(key + " 不能小于 0")
        value["run_command"] = ""
        value["root"] = str(OUTPUT_DIR / "downloads")
        atomic(RUNTIME / "settings.json", value)
    return settings()


def init_tables():
    intelligence.init_account_tables()
    store.x("""CREATE TABLE IF NOT EXISTS external_video_observations (
        aweme_id TEXT NOT NULL, fetch_date TEXT NOT NULL, fetched_at TEXT NOT NULL,
        play_count INTEGER, digg_count INTEGER, comment_count INTEGER, collect_count INTEGER,
        share_count INTEGER, source TEXT NOT NULL, PRIMARY KEY(aweme_id,fetch_date,source))""")
    store.x("""CREATE TABLE IF NOT EXISTS external_playback_checks (
        aweme_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT, checked_at TEXT NOT NULL)""")


def integer(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
        return value if value >= (1 if positive else 0) else None
    except (ValueError, TypeError):
        return None


def normalize(row, platform="douyin"):
    if not isinstance(row, dict):
        return None
    row = row.get("aweme_info") or row.get("aweme_detail") or row.get("itemInfo", {}).get("itemStruct") or row
    aid = str(row.get("aweme_id") or row.get("id") or "")
    if not re.fullmatch(r"\d{10,25}", aid) or not ("video" in row or "statistics" in row or "stats" in row):
        return None
    author = row.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    stats = row.get("statistics") or row.get("stats") or {}
    video = row.get("video") or {}
    cover = video.get("cover") or {}
    cover = (cover.get("url_list") or [""])[0] if isinstance(cover, dict) else cover
    mix = row.get("mix_info") or {}
    names = {"play_count": "playCount", "digg_count": "diggCount", "comment_count": "commentCount", "share_count": "shareCount", "collect_count": "collectCount"}
    avatar = author.get("avatar_thumb") or author.get("avatar_medium") or {}
    avatar = (avatar.get("url_list") or [""])[0] if isinstance(avatar, dict) else ""
    return {"aweme_id": aid, "platform": platform, "title": row.get("desc") or "",
            "avatar_url": avatar,
            "sec_uid": author.get("sec_uid") or author.get("secUid") or "",
            "author_name": author.get("nickname") or "", "cover_url": cover,
            "create_time": integer(row.get("create_time", row.get("createTime"))),
            "mix_id": mix.get("mix_id"), "mix_name": mix.get("mix_name"),
            **{k: integer(stats.get(k, stats.get(v)), k == "play_count") for k, v in names.items()}}


def import_rows(rows, platform="douyin", known_only=False, source="douk_detail"):
    init_tables()
    imported = valid = 0
    thumbnails = []
    if platform != "douyin":
        return {"imported": 0, "playback": 0}
    for raw in rows:
        item = normalize(raw, platform)
        if not item:
            continue
        aid = item["aweme_id"]
        known = store.q("SELECT * FROM tracked_account_videos WHERE aweme_id=?", (aid,))
        if known_only and not known:
            continue
        uid = item["sec_uid"] or (known[0]["sec_uid"] if known else "")
        if not uid:
            continue
        intelligence.upsert_account_identity(uid, item["author_name"])
        if not known or not known[0].get("cover_path"):
            thumbnails.append(("cover", aid, item.get("cover_url")))
        account = store.q("SELECT avatar_path FROM tracked_accounts WHERE sec_uid=?", (uid,))
        if account and not account[0].get("avatar_path"):
            thumbnails.append(("avatar", uid, item.get("avatar_url")))
        now, day = store.now_str(), store.today_str()
        store.x("""INSERT INTO tracked_account_videos(aweme_id,sec_uid,title,create_time,mix_id,mix_name,first_seen,last_seen,metric_status)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(aweme_id) DO UPDATE SET
            title=COALESCE(NULLIF(excluded.title,''),tracked_account_videos.title),
            create_time=COALESCE(excluded.create_time,tracked_account_videos.create_time),
            mix_id=COALESCE(excluded.mix_id,tracked_account_videos.mix_id),
            mix_name=COALESCE(excluded.mix_name,tracked_account_videos.mix_name),last_seen=excluded.last_seen""",
            (aid, uid, item["title"], item["create_time"], item["mix_id"], item["mix_name"], now, now, "external"))
        metrics = [item[k] for k in ("play_count", "digg_count", "comment_count", "collect_count", "share_count")]
        store.x("""INSERT INTO external_video_observations VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(aweme_id,fetch_date,source) DO UPDATE SET fetched_at=excluded.fetched_at,
            play_count=COALESCE(excluded.play_count,external_video_observations.play_count),
            digg_count=COALESCE(excluded.digg_count,external_video_observations.digg_count),
            comment_count=COALESCE(excluded.comment_count,external_video_observations.comment_count),
            collect_count=COALESCE(excluded.collect_count,external_video_observations.collect_count),
            share_count=COALESCE(excluded.share_count,external_video_observations.share_count)""",
            (aid, day, now, *metrics, source))
        status = "available" if item["play_count"] else "not_exposed"
        source_name = "TikHub App V3" if source == "tikhub_app_v3" else "DouK" if source == "douk_detail" else "多平台解析"
        message = source_name+" 接口返回有效播放量" if item["play_count"] else source_name+" 已取得作品数据，但播放量为 0 或缺失；不作为真实零播放入库"
        store.x("INSERT OR REPLACE INTO external_playback_checks VALUES(?,?,?,?)", (aid, status, message, now))
        import corpus
        canonical={**item,'platform':'dy','content_id':aid,'description':item['title'],
            'url':'https://www.douyin.com/video/'+aid,'creator_hash':'','published_at':corpus.published(item.get('create_time'))}
        corpus.save_content(canonical,clean(raw),source,'')
        imported += 1
        valid += bool(item["play_count"])
    if any(t[2] for t in thumbnails):
        from media_cache import cache_images
        images = cache_images([t[2] for t in thumbnails])
        for kind, identity, url in thumbnails:
            if path := images.get(url):
                if kind == "cover":
                    store.x("UPDATE tracked_account_videos SET cover_path=? WHERE aweme_id=?", (path, identity))
                else:
                    store.x("UPDATE tracked_accounts SET avatar_path=? WHERE sec_uid=?", (path, identity))
    return {"imported": imported, "playback": valid}


def metadata(job_id):
    if not re.fullmatch(r"[a-f0-9]{16}", job_id):
        raise ValueError("任务不存在")
    return JOBS / job_id / "meta.json"


def jobs():
    JOBS.mkdir(parents=True, exist_ok=True)
    values = [read(p, {}) for p in JOBS.glob("*/meta.json")]
    for value in values:
        if value.get("status") == "running":
            progress = read(metadata(value["id"]).parent / "progress.json", {})
            value["count"] = progress.get("count", value.get("count", 0))
            value["progress"] = progress
    return sorted(values, key=lambda x: x.get("created_at", ""), reverse=True)[:100]


def job(job_id, with_data=False):
    value = read(metadata(job_id))
    if value is None:
        raise ValueError("任务不存在")
    value["progress"] = read(metadata(job_id).parent / "progress.json", {})
    if with_data:
        if value.get("endpoint") in {"tikhub_api", "discovery"}:
            import tikhub
            response = tikhub.public_result(read(metadata(job_id).parent / "result.json", {}))
        else:
            response = clean(read(metadata(job_id).parent / "result.json", {}))
        value["result"] = response
        log_path = metadata(job_id).parent / "engine.log"
        if log_path.is_file():
            with log_path.open('rb') as handle:
                handle.seek(max(0, log_path.stat().st_size-16000))
                lines=handle.read().decode('utf-8',errors='replace').splitlines()[-70:]
            value["logs"] = '\n'.join('[凭据日志已隐藏]' if re.search(r'cookie|token|authorization|sessionid|verify_fp|s_v_web_id',line,re.I)
                else re.sub(r'\x1b\[[0-9;]*m','',line) for line in lines)
        value["records"] = (response.get("data", []) if value.get("endpoint") in {"tikhub_api", "discovery"} else
                            [normalize(x, value.get("platform", "douyin")) or x for x in response.get("data", [])])
        if value.get("platform") == "douyin":
            covers = {row["aweme_id"]: row["cover_path"] for row in
                      store.q("SELECT aweme_id,cover_path FROM tracked_account_videos WHERE cover_path IS NOT NULL")}
            for record in value["records"]:
                if isinstance(record, dict) and record.get("aweme_id") in covers:
                    record["cover_path"] = covers[record["aweme_id"]]
    return value


def status():
    return {"installed": PYTHON.exists(), "version": catalog()["version"],
            "settings": settings(), "jobs": jobs(), "files": files(),
            "unavailable": catalog()["unavailable"]}


def files():
    root = OUTPUT_DIR / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    return sorted([{"path": str(p.relative_to(root)).replace("\\", "/"), "name": p.name,
                    "size": p.stat().st_size, "modified": p.stat().st_mtime}
                   for p in root.rglob("*") if p.is_file() and p.suffix not in {".tmp", ".part"}],
                  key=lambda x: x["modified"], reverse=True)[:500]


def file_path(name):
    root = (OUTPUT_DIR / "downloads").resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("文件不存在")
    return target


def submit(data):
    global STARTED
    endpoint = data.get("endpoint", "")
    if endpoint not in {"tikhub", "tikhub_api", "mcpro", "discovery"} and not PYTHON.exists():
        raise ValueError("引擎未安装，请运行 scripts/setup_douk.py")
    allowed = set(catalog()["openapi"]["paths"]) | set(catalog()["extra"]) | {"sync", "backfill", "record", "tikhub", "tikhub_api", "mcpro", "discovery"}
    if endpoint not in allowed or endpoint in {"/", "/settings", "/token"}:
        raise ValueError("不支持的任务类型")
    request = copy.deepcopy(data)
    if endpoint == "discovery":
        import discovery
        preview = discovery.plan({**request.get("query", {}), "pages": request.get("pages", 1), "page_start": request.get("page_start", 1)})
        if not preview["allowed"] or request.get("confirmed") is not True or request.get("fingerprint") != preview["fingerprint"]:
            raise ValueError(preview["reason"] or "请重新预览发现请求")
    if endpoint == "tikhub_api":
        import tikhub
        preview = tikhub.plan(request)
        if not preview["allowed"]:
            raise ValueError(preview["reason"])
        if request.get("allow_charges") is not True or request.get("fingerprint") != preview["fingerprint"]:
            raise ValueError("请重新预览参数并确认请求数")
        # Generic API results use the explicit adapter, never the DouK import flag.
        request.pop("import_data", None)
    request["params"] = request.get("params") or {}
    if not isinstance(request["params"], dict):
        raise ValueError("参数格式错误")
    if len(request.get("items") or []) > 1000:
        raise ValueError("每次最多 1000 条")
    for payload in [request["params"], *(request.get("items") or [])]:
        if not isinstance(payload, dict) or (endpoint != "tikhub_api" and any(k in payload for k in ("cookie", "cookie_tiktok"))):
            raise ValueError("请在引擎设置中保存登录状态")
    with LOCK:
        job_id = uuid.uuid4().hex[:16]
        directory = metadata(job_id).parent
        directory.mkdir(parents=True, exist_ok=True)
        request["progress_path"] = str(directory / "progress.json")
        if endpoint in {"tikhub_api", "discovery"}:
            request["job_id"] = job_id
        atomic(directory / "request.json", request)
        value = {"id": job_id, "endpoint": endpoint, "platform": "tiktok" if endpoint.startswith("/tiktok") else "douyin",
                 "title": str(data.get("title") or endpoint)[:100], "engine_action":data.get("action"), "status": "queued", "created_at": store.now_str(),
                 "download": bool(data.get("download")), "count": 0}
        if endpoint == "tikhub_api":
            value.update(platform="api", operation_id=request["operation_id"])
            if preview["single_video_queue"]:
                value.update(refresh_total=preview["calls"], concurrency=preview["concurrency"])
        atomic(directory / "meta.json", value)
        QUEUE.append(job_id)
        if not STARTED:
            STARTED = True
            threading.Thread(target=consume, daemon=True).start()
    return value


def sync_session():
    from collector import DouyinCollector
    state = BROWSER_STATE
    if state:
        with state.lock:
            if state.running:
                raise ValueError("采集浏览器正在使用中，请待当前采集完成后同步")
            state.running = True
            state.msg = "同步引擎登录状态"
    try:
        with DouyinCollector({**load_config(), "headless": True}, log=lambda _: None) as col:
            cookies = col._ctx.cookies("https://www.douyin.com/")
            value = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            if not any(c["name"] in {"sessionid", "sessionid_ss", "sid_tt"} for c in cookies):
                raise ValueError("采集浏览器没有有效登录状态，请先登录")
            save_settings({"cookie": value})
        return {"ok": True, "data": [], "message": "已同步本机采集浏览器登录状态"}
    finally:
        if state:
            with state.lock:
                state.running = False
                state.msg = "登录状态同步结束"


def consume():
    global STARTED
    while True:
        with LOCK:
            if not QUEUE:
                STARTED = False
                return
            jid = QUEUE.popleft()
        with LOCK:
            value = read(metadata(jid), {})
            if value.get("status") == "cancelled":
                continue
            folder = metadata(jid).parent
            request = read(folder / "request.json", {})
            value.update(status="running", started_at=store.now_str(), owner_pid=os.getpid())
            atomic(metadata(jid), value)
        try:
            if request["endpoint"] == "sync":
                result = sync_session()
                atomic(folder / "result.json", result)
            else:
                if request["endpoint"] == "backfill":
                    import insights
                    missing = [v for v in insights.dashboard(90)["videos"] if v.get("play_count") is None]
                    request.update(endpoint="/douyin/detail", items=[{"detail_id": v["aweme_id"]} for v in missing], import_data=True, known_only=True)
                    if not missing:
                        atomic(folder / "result.json", {"ok": True, "data": [], "message": "当前没有缺失播放量的作品"})
                        value.update(status="completed", count=0)
                        atomic(metadata(jid), value)
                        continue
                if request["endpoint"] == "discovery":
                    import discovery
                    result = discovery.execute(request,
                        lambda p: atomic(folder / "progress.json", p),
                        lambda: read(metadata(jid), {}).get("status") == "cancelled")
                    atomic(folder / "result.json", result)
                elif request["endpoint"] == "tikhub_api":
                    import tikhub
                    result = tikhub.execute(request,
                        lambda p: atomic(folder / "progress.json", p),
                        lambda: read(metadata(jid), {}).get("status") == "cancelled")
                    atomic(folder / "result.json", result)
                elif request["endpoint"] == "tikhub":
                    import playback
                    result = playback.execute(request,
                        lambda p: atomic(folder / "progress.json", p),
                        lambda: read(metadata(jid), {}).get("status") == "cancelled")
                    atomic(folder / "result.json", result)
                elif request["endpoint"] == "mcpro":
                    import integrations
                    result = integrations.execute(request, jid)
                    atomic(folder / "result.json", result)
                elif request["endpoint"] == "record":
                    result = record_live(request, jid)
                    atomic(folder / "result.json", result)
                else:
                    # Save merged defaults before worker reads its private configuration.
                    if not (RUNTIME / "settings.json").exists():
                        save_settings({})
                    atomic(folder / "request.json", request)
                    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
                    with (folder / "engine.log").open("w", encoding="utf-8") as log:
                        proc = subprocess.Popen([str(PYTHON), "-X", "utf8", str(WORKER), str(folder / "request.json"), str(folder / "result.json")],
                              cwd=ROOT, stdout=log, stderr=log, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        with LOCK:
                            ACTIVE[jid] = proc
                        try:
                            proc.wait(timeout=3600)
                        except subprocess.TimeoutExpired:
                            terminate(proc)
                            raise ValueError("任务超过 60 分钟，已停止，可分批重试")
                    result = read(folder / "result.json", {"ok": False, "error": "引擎未返回结果，请检查本机引擎日志"})
                if read(metadata(jid), {}).get("status") == "cancelled":
                    continue
                rows = result.get("data") or []
                value["count"] = len(rows)
                if request.get("import_data"):
                    value.update(import_rows(rows, value["platform"], request.get("known_only", False)))
                errors = result.get("errors") or []
                value["message"] = clean(result.get("error") or result.get("message") or (errors[0]["error"] if errors else ""))
                value["attempted"] = result.get("attempted")
                value["requested"] = result.get("requested")
            value["status"] = "completed" if result.get("ok") else "failed"
            if result.get("ok") and result.get("errors"):
                value["status"] = "partial"
            if request.get("download"):
                value["download_files"] = len(files())
        except Exception as exc:
            value.update(status="failed", message=clean(str(exc)[:600]))
        finally:
            with LOCK:
                ACTIVE.pop(jid, None)
                if read(metadata(jid), {}).get("status") != "cancelled":
                    value["finished_at"] = store.now_str()
                    atomic(metadata(jid), value)


def terminate(proc):
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.terminate()
        proc.wait(timeout=10)


def cancel(jid):
    with LOCK:
        value = job(jid)
        if value["status"] not in {"queued", "running"}:
            raise ValueError("任务已经结束")
        if value["endpoint"] == "sync" and value["status"] == "running":
            raise ValueError("登录状态同步即将结束，请稍候")
        value.update(cancelled_from=value["status"], status="cancelled", finished_at=store.now_str())
        atomic(metadata(jid), value)
        proc = ACTIVE.get(jid)
        if proc:
            terminate(proc)
    return value


def retry(jid):
    original = read(metadata(jid).parent / "request.json", {})
    if original.get("endpoint") in {"tikhub", "tikhub_api", "discovery"}:
        raise ValueError("TikHub 重试可能再次计费，请回到接口页面重新预览并提交")
    if not original:
        raise ValueError("原任务参数不存在")
    original.pop("progress_path", None)
    if job(jid)["endpoint"] == "backfill":
        original["endpoint"] = "backfill"
    return submit(original)


def reset_download_records(ids):
    ids = [str(i) for i in ids if re.fullmatch(r"\d{10,25}", str(i))]
    if not ids or len(ids) > 1000:
        raise ValueError("请选择 1 至 1000 个有效作品 ID")
    db = RUNTIME / "DouK-Downloader.db"
    if not db.exists():
        return {"deleted": 0}
    with sqlite3.connect(db) as conn:
        before = conn.total_changes
        conn.executemany("DELETE FROM download_data WHERE ID=?", [(i,) for i in ids])
        return {"deleted": conn.total_changes - before}


def record_live(request, jid):
    # Only stream URLs returned by a prior live query are accepted.
    source = job(request.get("source_job", ""), True)
    if not source["endpoint"].endswith("/live"):
        raise ValueError("请选择直播查询任务")
    url = request.get("stream_url", "")
    def urls(v):
        if isinstance(v, str):
            return [v] if v.startswith("https://") or v.startswith("http://") else []
        if isinstance(v, dict):
            return [u for x in v.values() for u in urls(x)]
        if isinstance(v, list):
            return [u for x in v for u in urls(x)]
        return []
    if url not in urls(source["result"].get("data", [])):
        raise ValueError("拉流地址不属于所选直播任务")
    ffmpeg = shutil.which("ffmpeg") or settings(False).get("ffmpeg")
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise ValueError("未找到 FFmpeg，请在引擎设置中填写可执行文件路径")
    seconds = max(10, min(3600, int(request.get("seconds", 300))))
    target = OUTPUT_DIR / "downloads" / "Live" / (jid + ".mp4")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (metadata(jid).parent / "engine.log").open("w") as log:
        proc = subprocess.Popen([ffmpeg, "-nostdin", "-y", "-i", url, "-t", str(seconds), "-c", "copy", str(target)], stdout=log, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with LOCK:
            ACTIVE[jid] = proc
        try:
            code = proc.wait(timeout=seconds + 120)
        except subprocess.TimeoutExpired:
            terminate(proc)
            raise ValueError("直播录制连接超时")
    return {"ok": code == 0 and target.exists() and target.stat().st_size > 0,
            "data": [{"file": str(target.relative_to(OUTPUT_DIR / "downloads"))}],
            "message": "录制结束" if code == 0 else "录制失败，请检查直播是否仍在进行"}


def export(jid, format_):
    result = job(jid, True)
    rows = result["records"]
    if format_ == "json":
        return json.dumps(result["result"], ensure_ascii=False, indent=2).encode(), "application/json"
    rows = [x if isinstance(x, dict) else {"value": x} for x in rows]
    keys = list(dict.fromkeys(k for row in rows for k in row))
    def cell(v):
        s = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v if v is not None else "")
        return "'" + s if s.startswith(("=", "+", "-", "@", "\t", "\r")) else s
    if format_ == "xlsx":
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.append(keys)
        for row in rows:
            ws.append([cell(row.get(k)) for k in keys])
        out = io.BytesIO(); wb.save(out)
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if format_ != "csv":
        raise ValueError("不支持的导出格式")
    out = io.StringIO(newline=""); writer = csv.writer(out); writer.writerow(keys)
    writer.writerows([[cell(row.get(k)) for k in keys] for row in rows])
    return out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"


def recover():
    for value in jobs():
        if value.get("status") in {"queued", "running"}:
            if value.get("owner_pid") and process_alive(value["owner_pid"]):
                continue
            value.update(status="interrupted", message="服务重启，任务已中断，可重新提交")
            atomic(metadata(value["id"]), value)


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
