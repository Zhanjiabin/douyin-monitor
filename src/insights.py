"""Content comparisons from local observations; no synthetic metrics or recommendation claims."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
import re

import intelligence
import store

TZ = timezone(timedelta(hours=8))
TOPICS = {
    "逆袭复仇": ("重生", "复仇", "逆袭", "打脸"),
    "都市情感": ("总裁", "闪婚", "离婚", "甜宠", "爱情"),
    "古装穿越": ("穿越", "王爷", "嫡女", "宫廷", "古装"),
    "悬疑探案": ("悬疑", "探案", "凶手", "失踪", "真凶"),
    "家庭伦理": ("婆婆", "婆媳", "亲妈", "儿媳", "家庭"),
    "奇幻异能": ("末日", "丧尸", "修仙", "异能", "玄幻"),
}
FEATURES = {
    "悬念提问": r"[?？]|为什么|究竟|到底|谁是",
    "转折反差": r"没想到|竟然|居然|原来|却|结果",
    "身份关系": r"总裁|前妻|丈夫|妻子|婆婆|千金|王爷|赘婿",
    "数字信息": r"\d|[一二三四五六七八九十百千万]+[年天次亿万]",
}
METRICS = ("play_count", "digg_count", "comment_count", "share_count", "collect_count")


def mid(values):
    values = [v for v in values if v is not None]
    return round(median(values), 3) if values else None


def title_features(title):
    text = re.sub(r"#[^\s#]+", "", title or "")
    text = re.sub(r"第\s*[\d一二三四五六七八九十百]+\s*[集话期]", "", text).strip()
    return [name for name, pattern in FEATURES.items() if text and re.search(pattern, text)]


def infer_topic(text):
    counts = {k: sum(w in text for w in words) for k, words in TOPICS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else "未识别"


def observations():
    """Unify both collectors by video and day; preserve metric dates and provenance."""
    intelligence.init_account_tables()
    account = store.q("""SELECT s.*,v.sec_uid,v.mix_id,v.mix_name,v.title,v.create_time,v.cover_path,
        a.nickname author_name, NULL episode_no FROM account_video_snapshots s
        JOIN tracked_account_videos v ON v.aweme_id=s.aweme_id
        LEFT JOIN tracked_accounts a ON a.sec_uid=v.sec_uid""")
    mix = store.q("""SELECT s.*,m.sec_uid,m.mix_name,m.author_name FROM video_snapshots s
        LEFT JOIN mixes m ON m.mix_id=s.mix_id""")
    external = []
    if store.q("SELECT name FROM sqlite_master WHERE type='table' AND name='external_video_observations'"):
        external = store.q("""SELECT s.*,v.sec_uid,v.mix_id,v.mix_name,v.title,v.create_time,v.cover_path,
            a.nickname author_name,NULL episode_no FROM external_video_observations s
            JOIN tracked_account_videos v ON v.aweme_id=s.aweme_id
            LEFT JOIN tracked_accounts a ON a.sec_uid=v.sec_uid ORDER BY s.fetched_at,s.source""")
    providers = []
    if store.q("SELECT name FROM sqlite_master WHERE type='table' AND name='playback_measurements'"):
        providers = store.q("""SELECT s.*,v.sec_uid,v.mix_id,v.mix_name,v.title,v.create_time,v.cover_path,
            a.nickname author_name,NULL episode_no FROM playback_measurements s
            JOIN tracked_account_videos v ON v.aweme_id=s.aweme_id
            LEFT JOIN tracked_accounts a ON a.sec_uid=v.sec_uid ORDER BY s.fetched_at,s.source""")
    by_day = {}
    for source, rows in (("author_post", account), ("douk_detail", external), ("mix", mix), ("provider", providers)):
        for row in rows:
            key = (row["aweme_id"], row["fetch_date"])
            previous = by_day.get(key, {})
            merged = {**previous, **{k: v for k, v in row.items() if v is not None}}
            merged["play_source"] = row.get("source", source) if row.get("play_count") is not None else previous.get("play_source")
            if source == "provider" and row.get("play_count") is not None:
                merged["play_scope"] = row["scope"]
            by_day[key] = merged
    return list(by_day.values())


def dashboard(days=30, role="all", account="", topic="", query="", include_archived=False):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    if days not in (7, 30, 90):
        days = 30
    today = date.fromisoformat(store.today_str())
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()
    accounts = intelligence.tracked_account_summary()
    account_map = {a["sec_uid"]: a for a in accounts}
    checks = {r["mix_id"]:r for r in store.q("SELECT * FROM playback_checks")}
    external_checks = {}
    if store.q("SELECT name FROM sqlite_master WHERE type='table' AND name='external_playback_checks'"):
        external_checks = {r["aweme_id"]:r for r in store.q("SELECT * FROM external_playback_checks")}
    histories = defaultdict(list)
    for row in observations():
        if row["fetch_date"] <= end:
            histories[row["aweme_id"]].append(row)
    import metric_history
    update_times=metric_history.latest()
    videos = []
    selected = {}
    for aid, history in histories.items():
        history.sort(key=lambda x: x["fetch_date"])
        latest = history[-1]
        if latest["fetch_date"] < start and not include_archived:
            continue
        identity = account_map.get(latest.get("sec_uid"), {})
        group = identity.get("role", "unclassified")
        if role in ("own", "peer", "unclassified") and group != role:
            continue
        if account and latest.get("sec_uid") != account:
            continue
        category = infer_topic((latest.get("title") or "") + " " + (latest.get("mix_name") or ""))
        if topic and category != topic:
            continue
        haystack = " ".join(str(latest.get(key) or "") for key in ("title", "aweme_id", "mix_name", "author_name"))
        haystack += " " + str(identity.get("nickname") or "") + " " + str(identity.get("note") or "")
        if query and str(query).strip().casefold() not in haystack.casefold():
            continue
        # Never relabel an old mix count as today's account observation.
        known = [r for r in history if r.get("play_count") is not None]
        playback = known[-1] if known else {}
        recent = [r for r in known if r["fetch_date"] >= start]
        growth, elapsed, per_day = None, None, None
        if len(recent) >= 2:
            first, last = recent[0], recent[-1]
            elapsed = (date.fromisoformat(last["fetch_date"]) - date.fromisoformat(first["fetch_date"])).days
            if first.get("play_source") == last.get("play_source") and first.get("play_scope") == last.get("play_scope"):
                growth = last["play_count"] - first["play_count"]
                if elapsed and growth >= 0:
                    per_day = round(growth / elapsed, 3)
        play_date = playback.get("fetch_date")
        stale = bool(play_date and play_date < (today - timedelta(days=1)).isoformat())
        age = None
        if latest.get("create_time") and play_date:
            try:
                age = (date.fromisoformat(play_date) - datetime.fromtimestamp(latest["create_time"], TZ).date()).days
                if age < 0:
                    age = None
            except (ValueError, OSError, OverflowError):
                pass
        age_band = None if age is None else "0–7天" if age <= 7 else "8–30天" if age <= 30 else "31天以上"
        check = checks.get(latest.get("mix_id"), {})
        if playback.get("play_count") is not None:
            play_status, play_reason = "available", "已取得播放快照；日期见播放采集日。"
        elif aid in external_checks:
            check = external_checks[aid]
            play_status, play_reason = "checked_missing", check["message"]
        elif not latest.get("mix_id"):
            play_status, play_reason = "not_provided", "当前主页数据没有有效播放量，也未标注合集入口；其他来源尚未完成核验，不代表该作品的播放量一定无法获取。"
        elif check.get("status") == "unavailable":
            play_status, play_reason = "unavailable", "已检查分集接口，但没有可验证的播放量。登录不保证该指标可用。"
        elif check.get("status") == "failed":
            play_status, play_reason = "failed", "合集播放补采失败，可重新采集。" + (check.get("message") or "")
        else:
            play_status, play_reason = "pending", "已识别合集，该作品尚无有效播放快照；需补查合集分段或接口是否提供该指标。"
        videos.append({**latest,**update_times.get(aid,{}), "play_updated_at":playback.get("fetched_at"), "avatar_path":identity.get("avatar_path"), "play_count": playback.get("play_count"), "play_date": play_date,
            "play_status":play_status, "play_reason":play_reason, "play_checked_at":check.get("checked_at"),
            "play_source": playback.get("play_source"), "role": group, "topic": category,
            "play_scope": playback.get("play_scope"),
            "account_name": identity.get("nickname") or identity.get("note") or latest.get("author_name") or "未命名账号",
            "period_delta": growth, "span_days": elapsed, "daily_growth": per_day,
            "stale": stale, "age_band": age_band, "normalized": None,
            "features": title_features(latest.get("title"))})
        selected[aid] = history

    fresh = [v for v in videos if not v["stale"] and v["play_count"] is not None]
    cohorts = defaultdict(list)
    for v in fresh:
        if v.get("sec_uid") and v["age_band"]:
            cohorts[(v["sec_uid"], v["age_band"], v["play_source"])].append(v)
    for rows in cohorts.values():
        baseline = mid(v["play_count"] for v in rows)
        if len(rows) >= 3 and baseline:
            for v in rows:
                v["normalized"] = round(v["play_count"] / baseline, 3)
    features = []
    comparable = [v for v in fresh if v["normalized"] is not None and v.get("title")]
    for name in FEATURES:
        yes = [v for v in comparable if name in v["features"]]
        no = [v for v in comparable if name not in v["features"]]
        enough = (len(yes) >= 10 and len(no) >= 10
                  and len({v["sec_uid"] for v in yes}) >= 3 and len({v["sec_uid"] for v in no}) >= 3)
        a, b = mid(v["normalized"] for v in yes), mid(v["normalized"] for v in no)
        features.append({"name": name, "n": len(yes), "control_n": len(no),
            "ratio": round(a / b, 2) if enough and b else None,
            "status": "探索性相关" if enough and b else "样本不足", "sample_ids": [v["aweme_id"] for v in yes]})
    topics = []
    for name in sorted({v["topic"] for v in videos}):
        rows = [v for v in fresh if v["topic"] == name]
        topics.append({"name": name, "total":sum(v["topic"] == name for v in videos), "n": len(rows), "accounts": len({v["sec_uid"] for v in rows if v.get("sec_uid")}),
            "median_play": mid(v["play_count"] for v in rows),
            "median_growth": mid(v["daily_growth"] for v in rows),
            "growth_n": sum(v["daily_growth"] is not None for v in rows)})
    topics.sort(key=lambda t: t["median_growth"] if t["median_growth"] is not None else -1, reverse=True)

    by_date = defaultdict(dict)
    for aid, history in selected.items():
        for row in history:
            if start <= row["fetch_date"] <= end and row.get("play_count") is not None:
                by_date[row["fetch_date"]][aid] = row
    roles = {v["aweme_id"]: v["role"] for v in videos}
    uids = {v["aweme_id"]: v.get("sec_uid") for v in videos}
    trend = []
    for i in range(days):
        day = today - timedelta(days=days - 1 - i)
        current = by_date.get(day.isoformat(), {})
        prev = by_date.get((day - timedelta(days=1)).isoformat(), {})
        groups = defaultdict(list)
        for aid in current.keys() & prev.keys():
            if (current[aid].get("play_source") != prev[aid].get("play_source") or
                current[aid].get("play_scope") != prev[aid].get("play_scope")):
                continue
            delta = current[aid]["play_count"] - prev[aid]["play_count"]
            if delta >= 0:
                groups[roles[aid]].append((uids[aid], delta))
        own, peers = groups["own"], groups["peer"]
        peer_n = len({uid for uid, _ in peers})
        trend.append({"date": day.isoformat(), "own": sum(x[1] for x in own) if own else None,
            "peer": round(sum(x[1] for x in peers) / peer_n, 2) if peers else None,
            "own_n": len(own), "peer_n": len(peers), "peer_accounts": peer_n})

    for a in accounts:
        rows = [v for v in videos if v.get("sec_uid") == a["sec_uid"]]
        measured = [v for v in rows if not v["stale"] and v["play_count"] is not None]
        a["measured_count"] = len(measured)
        a["observed_count"] = len(rows)
        a["play_sum"] = sum(v["play_count"] for v in measured) if measured else None
        a["median_growth"] = mid(v["daily_growth"] for v in measured)
    videos.sort(key=lambda v: v["daily_growth"] if v["daily_growth"] is not None and not v["stale"] else -1, reverse=True)
    return {"days": days, "start": start, "end": end, "accounts": accounts, "videos": videos,
        "topics": topics, "features": features, "trend": trend,
        "stats": {"videos": len(videos), "known": len(fresh),
            "play": sum(v["play_count"] for v in fresh) if fresh else None,
            "median_growth": mid(v["daily_growth"] for v in fresh),
            "growth_n": sum(v["daily_growth"] is not None for v in fresh),
            "stale": sum(v["stale"] for v in videos),
            "missing": sum(v["play_count"] is None for v in videos),
            "declines": sum(v["period_delta"] is not None and v["period_delta"] < 0 for v in videos)},
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}
