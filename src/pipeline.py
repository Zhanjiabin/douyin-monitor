"""一次完整抓取流程。

清单链接 → 解析 aweme_id → 按合集去重 → 采集该合集每集播放量
        → 采集作者主页各合集官方总播放量 → 落库

关键设计：
1. **边采边存**：每采完一个合集立刻写库，中途崩溃/被中断也不会丢掉已采到的数据。
2. **优雅收尾**：浏览器窗口被关闭、用户点停止，都会保存已有数据并给出明确说明。
"""
import time

import store
from collector import DouyinCollector
from config import load_config
from resolve import ResolveCache, resolve_aweme_id

BROWSER_GONE_HINTS = (
    "has been closed", "browser has been closed", "target closed",
    "browser closed", "context or browser", "page closed", "target page",
)


def _noop(*a, **k):
    pass


def _browser_gone(exc) -> bool:
    """判断异常是不是「浏览器窗口被关掉了」。"""
    s = str(exc).lower()
    return any(h in s for h in BROWSER_GONE_HINTS)


def _persist_mix(mix_id, r, fd):
    """采到一个合集就立刻落库。"""
    eps = r.get("episodes") or []
    play_sum = sum((e.get("play_count") or 0) for e in eps)
    store.upsert_mix({
        "mix_id": mix_id, "sec_uid": r.get("sec_uid"),
        "author_name": r.get("author_name"), "mix_name": r.get("mix_name"),
        "episode_count": len(eps),
    })
    store.save_video_snapshots(mix_id, eps, fd)
    store.save_mix_partial(mix_id, fd, len(eps), play_sum,
                           r.get("updated_to_episode"))


def run_once(progress=None, log=None, should_stop=None, cfg=None):
    """执行一次抓取。progress(done,total,msg) 用于界面进度；返回统计 dict。"""
    progress = progress or _noop
    log = log or _noop
    should_stop = should_stop or (lambda: False)
    cfg = cfg or load_config()

    store.init_db()
    items = store.active_watch_items()
    total = len(items)
    run_id = store.run_start(total)
    fd = store.today_str()

    # no_mix = 视频本身不在任何合集（已处理）；skipped = 因中断没跑到的条数。
    # 两者以前混在一个计数器里，看日志分不清是哪种。
    stat = {"total": total, "ok": 0, "failed": 0, "skipped": 0, "no_mix": 0,
            "mixes": 0, "interrupted": False}
    if total == 0:
        log("清单为空，无需抓取")
        store.run_finish(run_id, 0, 0, 0, "done", "empty watchlist")
        stat["elapsed"] = 0
        return stat

    cache = ResolveCache()
    mix_data = {}
    authors = {}
    failed_items = []
    interrupted = False
    t0 = time.time()

    def _interrupt(reason):
        nonlocal interrupted
        interrupted = True
        stat["interrupted"] = True
        log(f"{reason}；已采集的 {len(mix_data)} 个合集数据已保存")

    try:
        with DouyinCollector(cfg, log=log) as col:
            # 连续失败计数：跑几百条时浏览器可能越跑越不稳，
            # 达到阈值就重建一次。正常路径完全不触发。
            restart_at = int(cfg.get("restart_after_fails", 5) or 0)
            consecutive_fail = 0
            for idx, item in enumerate(items, 1):
                if should_stop():
                    _interrupt("收到停止指令")
                    break
                url = item["url"]
                progress(idx - 1, total, f"({idx}/{total}) {url}")

                # ---- 解析 aweme_id（纯 HTTP，很快）----
                aid = item.get("aweme_id") or resolve_aweme_id(url)
                if not aid:
                    stat["failed"] += 1
                    failed_items.append(url)
                    store.update_watch_resolved(item["id"], {}, "链接解析失败")
                    log(f"({idx}/{total}) {url}  失败：链接解析失败")
                    continue

                mix_id = item.get("mix_id") or (cache.get(aid) or {}).get("mix_id")

                # ---- 本批已采过同一合集，直接复用 ----
                if mix_id and mix_id in mix_data:
                    r = mix_data[mix_id]
                    store.update_watch_resolved(item["id"], {
                        "aweme_id": aid, "mix_id": mix_id, "sec_uid": r.get("sec_uid"),
                        "author_name": r.get("author_name"), "mix_name": r.get("mix_name"),
                        "episode_count": len(r.get("episodes") or []),
                    })
                    stat["ok"] += 1
                    consecutive_fail = 0
                    log(f"({idx}/{total}) {url}  复用合集「{r.get('mix_name')}」")
                    continue

                # ---- 开浏览器采集 ----
                open_aid = aid
                if mix_id:
                    rep = cache.representative_of(mix_id)
                    if rep:
                        open_aid = rep

                retries = int(cfg.get("max_retries", 3))
                r, err, browser_dead = None, None, False
                for attempt in range(1, retries + 1):
                    try:
                        r = col.collect_video(open_aid)
                        err = None
                        break
                    except Exception as e:
                        err = str(e)[:200]
                        if _browser_gone(e):
                            browser_dead = True
                            break
                        log(f"({idx}/{total}) 第 {attempt} 次失败：{err}")
                        if attempt < retries:
                            time.sleep(3)

                if browser_dead:
                    _interrupt("浏览器窗口已被关闭，本次任务提前结束")
                    break
                if r is None:
                    stat["failed"] += 1
                    failed_items.append(url)
                    store.update_watch_resolved(item["id"], {}, err)
                    consecutive_fail += 1
                    log(f"({idx}/{total}) {url}  失败：{err}")
                    if restart_at and consecutive_fail >= restart_at:
                        consecutive_fail = 0
                        log(f"   连续 {restart_at} 条失败，重建浏览器后继续 ...")
                        try:
                            col.close()
                            col.start()
                        except Exception as re:
                            if _browser_gone(re):
                                _interrupt("浏览器窗口已被关闭，本次任务提前结束")
                                break
                            log(f"   重建浏览器失败：{str(re)[:120]}")
                    continue

                if not r.get("mix_id"):
                    # 与「因中断没跑到」区分：这类是视频本身没发布在任何合集里
                    stat["no_mix"] += 1
                    store.update_watch_resolved(item["id"], {
                        "aweme_id": aid, "sec_uid": r.get("sec_uid"),
                        "author_name": r.get("author_name"),
                    })
                    log(f"({idx}/{total}) {url}  跳过：该视频未发布在任何合集中")
                    continue

                consecutive_fail = 0
                mid = r["mix_id"]
                mix_data[mid] = r
                if r.get("sec_uid"):
                    authors[r["sec_uid"]] = r.get("author_name")

                # 合集内所有分集的 aweme_id 一并缓存，之后可省一次页面加载
                info = {"mix_id": mid, "mix_name": r.get("mix_name"),
                        "sec_uid": r.get("sec_uid"), "author_name": r.get("author_name")}
                for e in r.get("episodes") or []:
                    cache.put(e["aweme_id"], info)
                cache.put(open_aid, info)
                if aid != open_aid:
                    cache.put(aid, info)

                store.update_watch_resolved(item["id"], {
                    "aweme_id": aid, "mix_id": mid, "sec_uid": r.get("sec_uid"),
                    "author_name": r.get("author_name"), "mix_name": r.get("mix_name"),
                    "episode_count": len(r.get("episodes") or []),
                })

                # ★ 立刻落库，不等全部采完
                try:
                    _persist_mix(mid, r, fd)
                except Exception as e:
                    log(f"   写入数据库失败：{str(e)[:120]}")

                if r.get("has_more_left"):
                    log(f"   提示：合集「{r.get('mix_name')}」可能仍有未取完的分集")
                stat["ok"] += 1
                log(f"({idx}/{total}) {url}  合集「{r.get('mix_name')}」"
                    f"{len(r.get('episodes') or [])} 集")
                progress(idx, total, f"({idx}/{total}) 已采集 {len(mix_data)} 个合集")

                if idx < total:
                    try:
                        col.sleep_between()
                    except Exception as e:
                        if _browser_gone(e):
                            _interrupt("浏览器窗口已被关闭，本次任务提前结束")
                            break
                        raise

            # ---- 作者维度：官方合集总播放量（失败不影响已保存的分集数据）----
            if cfg.get("fetch_mix_total") and authors and not interrupted:
                log(f"开始采集 {len(authors)} 个账号的合集官方总播放量 ...")
                for sec_uid, name in authors.items():
                    if should_stop():
                        _interrupt("收到停止指令")
                        break
                    try:
                        ms = col.collect_author_mixes(sec_uid)
                    except Exception as e:
                        if _browser_gone(e):
                            _interrupt("浏览器窗口已被关闭")
                            break
                        log(f"   账号「{name}」合集列表采集失败：{str(e)[:120]}")
                        continue
                    hit = 0
                    for mid, m in ms.items():
                        if mid not in mix_data:
                            continue
                        store.upsert_mix({
                            "mix_id": mid, "mix_name": m.get("mix_name"),
                            "sec_uid": sec_uid, "author_name": name,
                            "play_vv": m.get("play_vv"), "collect_vv": m.get("collect_vv"),
                            "updated_to_episode": m.get("updated_to_episode"),
                        })
                        store.update_mix_official(mid, fd, m.get("play_vv"),
                                                  m.get("collect_vv"),
                                                  m.get("updated_to_episode"))
                        hit += 1
                    log(f"   账号「{name}」取到 {len(ms)} 个合集（匹配 {hit} 个）")
                    try:
                        col.sleep_between()
                    except Exception as e:
                        if _browser_gone(e):
                            _interrupt("浏览器窗口已被关闭")
                            break
                        raise
    finally:
        cache.save()

    stat["mixes"] = len(mix_data)

    # ---- 官方总量没取到的合集，回填分集求和，避免总览出现空值 ----
    for mid, r in mix_data.items():
        row = store.q("SELECT play_vv FROM mix_snapshots WHERE mix_id=? AND fetch_date=?",
                      (mid, fd))
        if row and row[0]["play_vv"] is None:
            play_sum = sum((e.get("play_count") or 0) for e in (r.get("episodes") or []))
            store.update_mix_official(mid, fd, play_vv=play_sum)

    elapsed = int(time.time() - t0)
    stat["elapsed"] = elapsed
    if interrupted:
        done = stat["ok"] + stat["failed"] + stat["skipped"] + stat["no_mix"]
        if done < total:
            stat["skipped"] += total - done
        status = "interrupted"
        head = "本次抓取被中断" if not should_stop() else "已手动停止"
    else:
        status = "done"
        head = "本次抓取结束"
    msg = f"{head}。耗时 {elapsed}s；成功 {stat['ok']}，失败 {stat['failed']}"
    if stat.get("no_mix"):
        msg += f"，不在合集 {stat['no_mix']}"
    if stat["skipped"]:
        msg += f"，未采集 {stat['skipped']}"
    if failed_items:
        msg += "；失败链接：" + "、".join(failed_items[:10])
    store.run_finish(run_id, stat["ok"], stat["failed"], stat["skipped"], status, msg)
    progress(total, total, "完成" if not interrupted else "已中断")
    log(msg)
    return stat


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=== 抖音合集播放量抓取 ===")
    r = run_once(log=lambda m: print(m, flush=True))
    print(r)


if __name__ == "__main__":
    main()
