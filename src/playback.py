"""TikHub playback queries with explicit scope, request limits and provenance."""
from datetime import datetime
import json
from pathlib import Path
import re
import threading

import requests
from config import DATA_DIR
import store

SETTINGS = DATA_DIR / "playback" / "settings.json"
LOCK = threading.RLock()
BASE = "https://api.tikhub.io"
MODES = {
    "app": {"name": "视频播放统计", "path": "/api/v1/douyin/app/v3/fetch_video_statistics",
            "batch": 1, "scope": "organic", "scope_label": "自然播放（供应商口径）",
            "docs": "https://docs.tikhub.io/186826221e0"},
    "app_batch": {"name": "批量视频播放统计", "path": "/api/v1/douyin/app/v3/fetch_multi_video_statistics",
                  "batch": 50, "scope": "unspecified", "scope_label": "批量统计（投流范围未注明）",
                  "docs": "https://docs.tikhub.io/256258480e0"},
    "xingtu": {"name": "星图播放统计", "path": "/api/v1/douyin/xingtu_v2/get_item_play_count",
               "batch": 1, "scope": "total", "scope_label": "含投流总播放（供应商口径）",
               "docs": "https://docs.tikhub.io/493289600e0"},
}


def init():
    store.x("""CREATE TABLE IF NOT EXISTS playback_requests (
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL,
        mode TEXT NOT NULL, video_ids TEXT NOT NULL, status TEXT NOT NULL,
        request_id TEXT, message TEXT)""")
    store.x("""CREATE TABLE IF NOT EXISTS playback_measurements (
        aweme_id TEXT NOT NULL, fetch_date TEXT NOT NULL, fetched_at TEXT NOT NULL,
        source TEXT NOT NULL, scope TEXT NOT NULL, play_count INTEGER,
        digg_count INTEGER, comment_count INTEGER, collect_count INTEGER, share_count INTEGER,
        request_id TEXT, PRIMARY KEY(aweme_id,fetch_date,source,scope))""")


def settings(public=True):
    value = {"api_key": "", "max_calls_per_job": 5, "daily_call_limit": 100, "allow_batch_statistics": False}
    with LOCK:
        try:
            value.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if public:
        value["key_set"] = bool(value.pop("api_key", ""))
    return value


def save_settings(data):
    if not isinstance(data, dict) or set(data) - {"api_key", "max_calls_per_job", "daily_call_limit", "clear_key", "allow_batch_statistics"}:
        raise ValueError("设置格式错误")
    with LOCK:
        value = settings(False)
        if "allow_batch_statistics" in data:
            if not isinstance(data["allow_batch_statistics"], bool):
                raise ValueError("批量统计开关必须为布尔值")
            value["allow_batch_statistics"] = data["allow_batch_statistics"]
        if data.get("clear_key"):
            value["api_key"] = ""
        elif str(data.get("api_key", "")).strip():
            value["api_key"] = str(data["api_key"]).strip()
        for key, max_ in (("max_calls_per_job", 100), ("daily_call_limit", 10000)):
            if key in data:
                number = int(data[key])
                if not 1 <= number <= max_:
                    raise ValueError(f"{key} 需在 1 至 {max_} 之间")
                value[key] = number
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(SETTINGS)
    return settings()


def ids(values):
    if not isinstance(values, list):
        raise ValueError("作品 ID 必须为列表")
    result = []
    for value in values:
        value = str(value).strip()
        match = re.search(r"/video/(\d{10,25})(?:[/?#]|$)", value)
        value = match.group(1) if match else value
        if not re.fullmatch(r"\d{10,25}", value):
            raise ValueError("请输入有效作品 ID 或抖音视频长链接")
        if value not in result:
            result.append(value)
    if not result or len(result) > 1000:
        raise ValueError("每次选择 1 至 1000 条作品")
    return result


def plan(mode, video_ids):
    init()
    if mode not in MODES:
        raise ValueError("不支持的播放统计方式")
    video_ids = ids(video_ids)
    batch = MODES[mode]["batch"]
    chunks = [video_ids[i:i+batch] for i in range(0, len(video_ids), batch)]
    cfg = settings()
    used = store.q("SELECT COUNT(*) n FROM playback_requests WHERE created_at>=?", (store.today_str(),))[0]["n"]
    reason = "未配置 TikHub API Key" if not cfg["key_set"] else ""
    if len(chunks) > cfg["max_calls_per_job"]:
        reason = "预计请求数超过单次任务上限，请减少作品或调整上限"
    elif used + len(chunks) > cfg["daily_call_limit"]:
        reason = "预计请求数超过今日上限"
    if mode == "app_batch" and not cfg["allow_batch_statistics"]:
        reason = "批量播放统计已关闭，请在设置中明确开启后使用"
    return {"mode": mode, "videos": len(video_ids), "calls": len(chunks), "today_calls": used,
            "allowed": not reason, "reason": reason, "scope": MODES[mode]["scope_label"],
            "pricing": "https://tikhub.io/zh/pricing#plans", "ids": video_ids}


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def parse(body, mode, expected):
    if not isinstance(body, dict) or body.get("code", 200) != 200:
        raise ValueError("TikHub 未返回成功结果；请检查密钥、额度和该接口权限")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("TikHub 响应缺少数据对象")
    if mode == "xingtu":
        if data.get("exist") is False:
            return []
        item = data.get("item") or {}
        stats = data.get("stats") or item.get("stats") or {}
        rows = [{"aweme_id": data.get("item_id") or item.get("id") or item.get("item_id") or (body.get("params") or {}).get("item_id"),
                 "play_count": data.get("watch_cnt"), "digg_count": stats.get("like_cnt"),
                 "comment_count": stats.get("comment_cnt"), "collect_count": stats.get("favorite_cnt"),
                 "share_count": stats.get("share_cnt")}]
    else:
        rows = data.get("statistics_list")
        if not isinstance(rows, list):
            raise ValueError("TikHub 响应缺少 statistics_list")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError("TikHub 统计记录格式无效")
        aid = str(row.get("aweme_id") or "")
        if aid not in expected:
            raise ValueError("响应作品 ID 与请求不一致，未写入播放数据")
        if aid in seen:
            raise ValueError("响应包含重复作品 ID")
        seen.add(aid)
        values = {key: number(row.get(key)) for key in
                  ("play_count", "digg_count", "comment_count", "collect_count", "share_count")}
        # The API may expose a zero placeholder; keep it in evidence, never invent a view.
        raw_play = values["play_count"]
        values["play_count"] = raw_play if raw_play and raw_play > 0 else None
        result.append({"aweme_id": aid, **values, "raw_play_count": raw_play,
                       "source": "tikhub_" + mode, "scope": MODES[mode]["scope"],
                       "scope_label": MODES[mode]["scope_label"], "request_id": str(body.get("request_id") or ""),
                       "status": "available" if values["play_count"] is not None else "not_returned"})
    return result


def persist(rows):
    init()
    for row in rows:
        if not store.q("SELECT aweme_id FROM tracked_account_videos WHERE aweme_id=?", (row["aweme_id"],)):
            continue
        store.x("""INSERT INTO playback_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(aweme_id,fetch_date,source,scope) DO UPDATE SET
          fetched_at=CASE WHEN excluded.play_count IS NULL AND playback_measurements.play_count IS NOT NULL
            THEN playback_measurements.fetched_at ELSE excluded.fetched_at END,
          play_count=COALESCE(excluded.play_count,playback_measurements.play_count),
          digg_count=COALESCE(excluded.digg_count,playback_measurements.digg_count),
          comment_count=COALESCE(excluded.comment_count,playback_measurements.comment_count),
          collect_count=COALESCE(excluded.collect_count,playback_measurements.collect_count),
          share_count=COALESCE(excluded.share_count,playback_measurements.share_count),
          request_id=CASE WHEN excluded.play_count IS NULL AND playback_measurements.play_count IS NOT NULL
            THEN playback_measurements.request_id ELSE excluded.request_id END""",
          (row["aweme_id"], store.today_str(), store.now_str(), row["source"], row["scope"],
           row["play_count"], row["digg_count"], row["comment_count"], row["collect_count"], row["share_count"], row["request_id"]))


def execute(request, progress, cancelled=lambda: False):
    if request.get("allow_charges") is not True:
        raise ValueError("请先预览请求数，再确认使用 TikHub 额度")
    mode = request.get("mode", "app")
    preview = plan(mode, request.get("video_ids"))
    if not preview["allowed"]:
        raise ValueError(preview["reason"])
    video_ids, batch = preview["ids"], MODES[mode]["batch"]
    result, errors = [], []
    for offset in range(0, len(video_ids), batch):
        if cancelled():
            break
        group = video_ids[offset:offset+batch]
        with LOCK:
            fresh = plan(mode, group)
            if not fresh["allowed"]:
                errors.append({"index": offset, "error": fresh["reason"]})
                break
            rid = store.x("INSERT INTO playback_requests(created_at,source,mode,video_ids,status) VALUES(?,?,?,?,?)",
                    (store.now_str(), "tikhub", mode, json.dumps(group), "started"))
        try:
            params = {"item_id": group[0]} if mode == "xingtu" else {"aweme_ids": ",".join(group)}
            response = requests.get(BASE + MODES[mode]["path"], params=params,
                headers={"Authorization": "Bearer " + settings(False)["api_key"]},
                timeout=(10, 40), allow_redirects=False)
            if response.status_code != 200:
                raise ValueError(f"TikHub HTTP {response.status_code}；请检查密钥、额度及接口权限")
            body = response.json()
            if isinstance(body,dict):
                store.x("UPDATE playback_requests SET request_id=? WHERE id=?",(str(body.get("request_id") or ""),rid))
            rows = parse(body, mode, group)
            if cancelled():
                store.x("UPDATE playback_requests SET status='cancelled' WHERE id=?", (rid,))
                break
            persist(rows)
            result.extend(rows)
            omitted = [aid for aid in group if aid not in {row['aweme_id'] for row in rows}]
            for aid in omitted:
                result.append({"aweme_id": aid, "play_count": None, "source": "tikhub_"+mode,
                               "scope": MODES[mode]["scope"], "status": "not_returned"})
            store.x("UPDATE playback_requests SET status='completed',request_id=? WHERE id=?",
                    (str(body.get("request_id") or ""), rid))
        except requests.RequestException:
            message = "TikHub 网络请求失败；未自动重试，请先检查额度记录"
            store.x("UPDATE playback_requests SET status='failed',message=? WHERE id=?", (message, rid))
            errors.append({"index": offset, "error": message})
            break
        except (ValueError, TypeError, KeyError) as exc:
            message = str(exc)[:180]
            store.x("UPDATE playback_requests SET status='failed',message=? WHERE id=?", (message, rid))
            errors.append({"index": offset, "error": message})
            break
        progress({"done": min(offset+batch, len(video_ids)), "total": len(video_ids), "count": len(result)})
    valid = sum(row.get("play_count") is not None for row in result)
    return {"ok": bool(result), "data": result, "errors": errors,
            "message": f"已返回 {len(result)} 条，其中 {valid} 条有有效播放量", "playback": valid}


def status():
    init()
    return {"settings": settings(), "modes": MODES,
            "today_calls":store.q("SELECT COUNT(*) n FROM playback_requests WHERE created_at>=?",(store.today_str(),))[0]["n"],
            "requests": store.q("SELECT * FROM playback_requests ORDER BY id DESC LIMIT 50")}
