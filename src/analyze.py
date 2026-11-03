"""聚合分析：总览、日增、分集占比、停涨识别、播放量分级。"""
from datetime import datetime

from config import load_config
import store

# 等级：0 普通 / 1 达标 / 2 优秀(优秀线) / 3 爆款(爆款线)


def _d(s):
    return s or ""


def _thresholds(cfg=None):
    """返回 (达标线, 优秀线, 爆款线)，并做归一化保证 top >= high >= success。"""
    cfg = cfg or load_config()

    def _i(k, d):
        try:
            v = int(cfg.get(k) or d)
        except Exception:
            v = d
        return max(v, 0)

    # 严格递增：即使用户把阈值填反了，三个等级也不会互相吞掉
    s = _i("level_success", 20000)
    h = max(_i("level_high", 500000), s + 1)
    t = max(_i("level_top", 1000000), h + 1)
    return s, h, t


def _level_label(v):
    """把阈值数字转成中文短标签，如 500000 -> '50万+'。"""
    if not v:
        return ""
    if v >= 10000:
        n = v / 10000
        return f"{int(n)}万+" if n == int(n) else f"{n:.1f}万+"
    return f"{v}+"


def level_of(play, cfg=None):
    """播放量 -> 等级 0/1/2/3。"""
    s, h, t = _thresholds(cfg)
    p = play or 0
    if p >= t:
        return 3
    if p >= h:
        return 2
    if p >= s:
        return 1
    return 0


def level_label(level, cfg=None):
    """等级 -> 徽章文字。1 恒为「达标」，2/3 用对应阈值命名。"""
    s, h, t = _thresholds(cfg)
    return {1: "达标", 2: _level_label(h), 3: _level_label(t)}.get(level, "")


def overview(cfg=None):
    cfg = cfg or load_config()
    fd = store.latest_date()
    if not fd:
        s, h, t = _thresholds(cfg)
        return {"fetch_date": None, "prev_date": None, "mixes": [],
                "total_play": 0, "total_delta": 0, "mix_count": 0,
                "stop_count": 0, "accounts": [],
                "thresholds": {"success": s, "high": h, "top": t},
                "labels": {1: "达标", 2: _level_label(h), 3: _level_label(t)}}
    pd_ = store.prev_date(fd)
    rows = store.mix_overview(fd)

    total_play = sum((r.get("play_vv") or 0) for r in rows)
    total_delta = sum((r.get("delta") or 0) for r in rows)

    for r in rows:
        # 注意：不要在这里给每个合集查分集。全项目没人消费这个字段
        # （报表/看板/界面都用 mix_detail 的 episodes），而它要按全局最新日期查，
        # 对当天没采到的合集还会查到空列表 —— 纯浪费 N 次查询。
        r["account"] = r.get("author_name") or "-"
        r["level"] = level_of(r.get("play_vv"), cfg)
        r["level_label"] = level_label(r["level"], cfg)
        r["first_publish_at"] = r.get("first_publish_at") or None

    stop_count = 0
    for r in rows:
        tr = store.mix_trend(r["mix_id"])
        if len(tr) >= 2:
            last2 = tr[-2:]
            a = last2[0].get("play_vv") or 0
            b = last2[1].get("play_vv") or 0
            if b - a == 0:
                stop_count += 1
    accounts = {}
    for r in rows:
        a = accounts.setdefault(r["account"], {"account": r["account"], "mixes": 0,
                                              "play": 0, "delta": 0})
        a["mixes"] += 1
        a["play"] += r.get("play_vv") or 0
        a["delta"] += r.get("delta") or 0

    s, h, t = _thresholds(cfg)
    return {
        "fetch_date": fd, "prev_date": pd_, "mixes": rows,
        "total_play": total_play, "total_delta": total_delta,
        "mix_count": len(rows), "stop_count": stop_count,
        "accounts": sorted(accounts.values(), key=lambda x: x["play"], reverse=True),
        "thresholds": {"success": s, "high": h, "top": t},
        "labels": {1: "达标", 2: _level_label(h), 3: _level_label(t)},
    }


def mix_detail(mix_id, mix_row=None):
    """分集明细。mix_row 由调用方传入可避免重复全表查询（n 个合集时是 O(n²)）。"""
    if mix_row is None:
        mix_row = next((m for m in store.mix_overview() if m["mix_id"] == mix_id), {})
    dates = [r["fetch_date"] for r in store.q(
        "SELECT fetch_date FROM mix_snapshots WHERE mix_id=? ORDER BY fetch_date",
        (mix_id,))]
    fd = dates[-1] if dates else store.latest_date()
    pd_ = dates[-2] if len(dates) >= 2 else None

    eps = store.episodes_of(mix_id, fd)
    prev = {e["aweme_id"]: e for e in store.episodes_of(mix_id, pd_)} if pd_ else {}
    total = sum((e.get("play_count") or 0) for e in eps) or 0
    out = []
    for e in eps:
        p = prev.get(e["aweme_id"]) or {}
        pc = e.get("play_count") or 0
        ppc = p.get("play_count")
        out.append({
            **e,
            "delta": (pc - ppc) if ppc is not None else None,
            "share": round(pc / total * 100, 1) if total else 0.0,
        })
    return {"mix": mix_row, "episodes": out, "trend": store.mix_trend(mix_id),
            "fetch_date": fd, "prev_date": pd_}


def watch_stats():
    return store.watchlist_stats()


def recent_runs(n=10):
    return store.last_runs(n)


def health():
    """数据健康度：官方总量与分集求和的差异、最近采集情况。"""
    rows = store.mix_overview()
    issues = []
    for r in rows:
        pv, ps = r.get("play_vv"), r.get("play_sum_calc")
        if pv and ps:
            diff = abs(pv - ps)
            if pv and diff / pv > 0.15:
                issues.append({"mix_name": r.get("mix_name"), "play_vv": pv,
                               "play_sum_calc": ps, "diff": diff})
    runs = store.last_runs(1)
    return {"issues": issues, "last_run": runs[0] if runs else None}
