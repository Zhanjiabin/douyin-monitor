"""Persistent, opt-in playback schedules. All times use Asia/Shanghai (UTC+8)."""
from datetime import datetime, date, time, timedelta, timezone
import json
import re
import threading
import uuid

import douk
import intelligence
import playback
import store
import workbench

TZ = timezone(timedelta(hours=8))
LOCK = threading.RLock()
FIELDS = {"enabled", "account_ids", "publication_days", "frequency", "at_time", "interval_minutes", "start_date", "end_date", "use_batch"}


def clock():
    return datetime.now(TZ)


def init():
    intelligence.init_account_tables()
    store.x("""CREATE TABLE IF NOT EXISTS monitor_policy (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL, config_json TEXT NOT NULL)""")
    store.x("""CREATE TABLE IF NOT EXISTS monitor_schedule_runs (
        id TEXT PRIMARY KEY,slot TEXT NOT NULL UNIQUE,scheduled_for TEXT NOT NULL,
        created_at TEXT NOT NULL,status TEXT NOT NULL,job_id TEXT,eligible INTEGER NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,message TEXT,config_json TEXT NOT NULL)""")


def config():
    init()
    defaults = {"enabled": False, "account_ids": [a["sec_uid"] for a in intelligence.tracked_account_summary() if a.get("enabled")],
                "publication_days": 7, "frequency": "daily", "at_time": "09:00", "interval_minutes": 60,
                "start_date": clock().date().isoformat(), "end_date": "", "use_batch": False}
    rows = store.q("SELECT * FROM monitor_policy WHERE id=1")
    if rows:
        row = rows[0]
        return {**defaults, **json.loads(row["config_json"]), "revision": row["revision"], "updated_at": row["updated_at"]}
    return {**defaults, "revision": 0, "updated_at": ""}


def validate(data):
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("定时监控设置格式错误")
    value = {k: v for k, v in config().items() if k in FIELDS}
    value.update(data)
    for key in ("enabled", "use_batch"):
        if not isinstance(value[key], bool):
            raise ValueError("监控和批量开关必须为布尔值")
    ids = value["account_ids"]
    if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(i, str) for i in ids):
        raise ValueError("监控账号需为列表")
    known = {a["sec_uid"] for a in intelligence.tracked_account_summary() if a.get("enabled")}
    value["account_ids"] = list(dict.fromkeys(ids))
    if value["enabled"] and set(ids) - known:
        raise ValueError("包含不存在或已停用的账号，请重新选择")
    if not value["enabled"]:
        value["account_ids"] = [identifier for identifier in value["account_ids"] if identifier in known]
    if value["enabled"] and not ids:
        raise ValueError("启用前请至少选择一个监控账号")
    for key, upper in (("publication_days", 365), ("interval_minutes", 10080)):
        if isinstance(value[key], bool) or not re.fullmatch(r"\d+", str(value[key])) or not 1 <= int(value[key]) <= upper:
            raise ValueError("作品天数或刷新间隔超出范围")
        value[key] = int(value[key])
    if value["frequency"] not in {"daily", "interval"}:
        raise ValueError("请选择每天定点或固定间隔")
    if not isinstance(value["at_time"], str) or not re.fullmatch(r"\d{2}:\d{2}", value["at_time"]):
        raise ValueError("刷新时间格式为 HH:MM")
    try:
        time.fromisoformat(value["at_time"])
        start = date.fromisoformat(value["start_date"])
        end = date.fromisoformat(value["end_date"]) if value["end_date"] else None
    except (ValueError, TypeError):
        raise ValueError("开始日期、结束日期或时间格式不正确") from None
    if end and end < start:
        raise ValueError("结束日期不能早于开始日期")
    if value["enabled"] and end and end < clock().date():
        raise ValueError("结束日期已经过去")
    if value["use_batch"] and not playback.settings()["allow_batch_statistics"]:
        if value["enabled"]:
            raise ValueError("批量播放统计已关闭，请先在数据连接中明确开启")
        value["use_batch"] = False
    return value


def targets(cfg, now=None):
    now = now or clock()
    ids = cfg["account_ids"]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return store.q(f"""SELECT v.aweme_id,v.title,v.sec_uid,v.create_time,a.nickname account_name
        FROM tracked_account_videos v JOIN tracked_accounts a ON a.sec_uid=v.sec_uid
        WHERE a.enabled=1 AND v.sec_uid IN ({marks}) AND v.create_time>=? AND v.create_time<=?
        ORDER BY v.create_time DESC,v.aweme_id""",
        (*ids, int((now - timedelta(days=cfg["publication_days"])).timestamp()), int(now.timestamp())))


def preview(cfg=None, now=None):
    cfg = cfg or config()
    videos = targets(cfg, now)
    if not videos:
        return {"eligible": 0, "calls": 0, "allowed": True, "reason": "范围内暂无已入库作品", "videos": [], "method": "batch" if cfg["use_batch"] else "single"}
    p = workbench.plan({"action": "playback", "video_ids": [v["aweme_id"] for v in videos],
                        "playback_method": "batch" if cfg["use_batch"] else "single"})
    return {"eligible": len(videos), "calls": p["calls"], "allowed": p["allowed"], "reason": p["reason"],
            "videos": videos[:100], "method": "batch" if cfg["use_batch"] else "single"}


def anchor(cfg, now):
    return max(datetime.fromisoformat(cfg["updated_at"]) if cfg.get("updated_at") else now,
               datetime.combine(date.fromisoformat(cfg["start_date"]), time(), TZ))


def next_run(cfg=None, now=None):
    cfg, now = cfg or config(), now or clock()
    if not cfg["enabled"]:
        return None
    base = anchor(cfg, now)
    if cfg["frequency"] == "daily":
        candidate = datetime.combine(max(now.date(), base.date()), time.fromisoformat(cfg["at_time"]), TZ)
        if candidate <= max(now, base):
            candidate += timedelta(days=1)
    else:
        delta = timedelta(minutes=cfg["interval_minutes"])
        candidate = base + max(1, int((now - base) // delta) + 1) * delta
    if cfg["end_date"] and candidate.date() > date.fromisoformat(cfg["end_date"]):
        return None
    return candidate.isoformat(timespec="seconds")


def due(cfg, now):
    if not cfg["enabled"] or now.date() < date.fromisoformat(cfg["start_date"]):
        return None
    if cfg["end_date"] and now.date() > date.fromisoformat(cfg["end_date"]):
        return None
    base = anchor(cfg, now)
    if cfg["frequency"] == "daily":
        candidate = datetime.combine(now.date(), time.fromisoformat(cfg["at_time"]), TZ)
    else:
        delta = timedelta(minutes=cfg["interval_minutes"])
        step = int((now - base) // delta)
        if step < 1:
            return None
        candidate = base + step * delta
    return candidate if base < candidate <= now else None


def save(data):
    with LOCK:
        value = validate(data)
        p = preview(value)
        if value["enabled"] and not p["allowed"]:
            raise ValueError(f"当前范围需要 {p['calls']} 次请求：{p['reason']}。请调整范围或数据连接中的请求上限")
        previous = config()
        store.x("""INSERT INTO monitor_policy VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            revision=excluded.revision,updated_at=excluded.updated_at,config_json=excluded.config_json""",
            (previous["revision"] + 1, clock().isoformat(timespec="seconds"), json.dumps(value, ensure_ascii=False)))
    return status()


def reconcile():
    for run in store.q("SELECT id,job_id FROM monitor_schedule_runs WHERE status='queued' AND job_id IS NOT NULL"):
        try:
            job = douk.job(run["job_id"])
            if job["status"] in {"queued", "running"}:
                continue
            store.x("UPDATE monitor_schedule_runs SET status=?,message=? WHERE id=?",
                    (job["status"], job.get("message", ""), run["id"]))
        except ValueError:
            store.x("UPDATE monitor_schedule_runs SET status='interrupted',message='关联任务不存在；未自动重发' WHERE id=?", (run["id"],))


def recover():
    init()
    store.x("UPDATE monitor_schedule_runs SET status='interrupted',message='服务重启时提交未完成；本轮未自动重发' WHERE status='started'")
    reconcile()


def status():
    with LOCK:
        cfg = config()
        reconcile()
        return {"config": cfg, "next_run": next_run(cfg), "timezone": "Asia/Shanghai", "preview": preview(cfg),
                "accounts": [{k: a.get(k) for k in ("sec_uid", "nickname", "note", "role", "enabled")} for a in intelligence.tracked_account_summary()],
                "limits": playback.settings(),
                "history": store.q("SELECT id,scheduled_for,created_at,status,job_id,eligible,calls,message FROM monitor_schedule_runs ORDER BY created_at DESC,rowid DESC LIMIT 15")}


def tick(now=None):
    """Claim the newest due slot once, then queue a normal guarded TikHub job.

    Restart catches up at most one slot; failure never retries that paid slot.
    The unique durable claim precedes queueing to favor no duplicate charges.
    """
    now = now or clock()
    with LOCK:
        cfg = config()
        scheduled = due(cfg, now)
        if scheduled is None:
            return None
        slot = f"{cfg['revision']}:{scheduled.isoformat()}"
        rid = uuid.uuid4().hex[:16]
        with store._LOCK:
            conn = store.get_conn()
            cur = conn.execute("""INSERT OR IGNORE INTO monitor_schedule_runs
                (id,slot,scheduled_for,created_at,status,eligible,calls,config_json) VALUES(?,?,?,?,?,0,0,?)""",
                (rid, slot, scheduled.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), "started", json.dumps(cfg, ensure_ascii=False)))
            conn.commit()
            if cur.rowcount != 1:
                return None
        try:
            reconcile()
            if store.q("SELECT id FROM monitor_schedule_runs WHERE status='queued' AND id<>?", (rid,)):
                store.x("UPDATE monitor_schedule_runs SET status='skipped',message='上一轮仍在执行，本轮跳过' WHERE id=?", (rid,))
                return rid
            videos = targets(cfg, now)
            if not videos:
                store.x("UPDATE monitor_schedule_runs SET status='skipped',message='范围内暂无已入库作品' WHERE id=?", (rid,))
                return rid
            prepared = workbench.prepare({"action": "playback", "video_ids": [v["aweme_id"] for v in videos],
                                          "playback_method": "batch" if cfg["use_batch"] else "single"})
            store.x("UPDATE monitor_schedule_runs SET eligible=?,calls=? WHERE id=?", (len(videos), prepared["calls"], rid))
            if not prepared["allowed"]:
                store.x("UPDATE monitor_schedule_runs SET status='blocked',message=? WHERE id=?", (prepared["reason"], rid))
                return rid
            request = {**prepared["request"], "fingerprint": prepared["fingerprint"], "allow_charges": True,
                       "title": f"定时刷新 · {len(videos)} 条作品", "monitor_run_id": rid}
            job = douk.submit(request)
            store.x("UPDATE monitor_schedule_runs SET status='queued',job_id=?,message='已加入采集队列' WHERE id=?", (job["id"], rid))
        except Exception:
            store.x("UPDATE monitor_schedule_runs SET status='failed',message='本轮未完整提交，请查看任务记录；未自动重试' WHERE id=?", (rid,))
        return rid


def job_allowed(request):
    """Recheck standing authorization before every scheduled HTTP call."""
    if not request.get("monitor_run_id"):
        return True
    with LOCK:
        cfg, now = config(), clock()
        rows = store.q("SELECT scheduled_for,config_json FROM monitor_schedule_runs WHERE id=?", (request["monitor_run_id"],))
        if not rows or not cfg["enabled"]:
            return False
        original = json.loads(rows[0]["config_json"])
        if original["revision"] != cfg["revision"] or datetime.fromisoformat(rows[0]["scheduled_for"]) > now:
            return False
        if now.date() < date.fromisoformat(cfg["start_date"]) or (cfg["end_date"] and now.date() > date.fromisoformat(cfg["end_date"])):
            return False
        allowed = {v["aweme_id"] for v in targets(cfg, now)}
        items = json.loads(request.get("items_json") or "[]")
        return bool(items) and all(set(str(i["params"]["aweme_ids"]).split(",")) <= allowed for i in items)


class MonitorScheduler(threading.Thread):
    def __init__(self, log=None):
        super().__init__(daemon=True, name="playback-monitor")
        self._stop_event = threading.Event()
        self.log = log or (lambda _: None)

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                tick()
            except Exception:
                self.log("播放定时监控本轮检查失败；未自动提交请求")
            try:
                import discovery
                discovery.tick()
            except Exception:
                self.log("发现定时更新本轮检查失败；未自动提交请求")
            self._stop_event.wait(15)
