"""抖音采集内核。

原理（2026-10 实测）：抖音页面是 JS 反爬壳，接口需 a_bogus 签名，
因此不自己逆向签名，而是用真实浏览器打开页面，监听浏览器自身发出的接口响应，取回 JSON。

三个关键接口：
  aweme/detail  视频详情     → 作者 sec_uid、所属合集 mix_id / mix_name
  mix/aweme     合集内视频   → 该合集【每一集的真实 play_count】（核心数据源）
  mix/list      账号合集列表 → 合集【官方口径总播放量 play_vv】

当前以 mix/aweme 作为播放量来源；其它接口返回的零占位不直接入库。
页面可能只调用 series/aweme，需主动请求已知的 mix/aweme 并按游标翻页。
"""
import random
import time

from playwright.sync_api import sync_playwright

from config import BROWSER_PROFILE, USER_AGENT

EP_DETAIL = "aweme_detail_"
EP_MIX_AWEME = "mix_aweme_"
EP_SERIES_AWEME = "series_aweme_"
EP_MIX_LIST = "mix_list_"
EP_AWEME_POST = "aweme_post_"
EP_PROFILE = "user_profile_other_"


class PlaybackUnavailable(RuntimeError):
    """The page exposes episodes but not verified playback metrics."""


JS_SCROLL_POSTS = """() => {
  const candidates = [...document.querySelectorAll('*')].filter(e =>
    e.clientHeight > 200 && e.scrollHeight > e.clientHeight + 80 &&
    /(auto|scroll)/.test(getComputedStyle(e).overflowY));
  candidates.sort((a,b) => b.querySelectorAll('a[href*="/video/"]').length - a.querySelectorAll('a[href*="/video/"]').length);
  const e = candidates[0] || document.scrollingElement;
  e.scrollTop = Math.max(0, e.scrollHeight - e.clientHeight - 120);
  e.scrollBy(0, 300);
  const r = e.getBoundingClientRect();
  return {x: Math.min(innerWidth-20, r.x+r.width/2), y: Math.min(innerHeight-20,r.y+r.height/2)};
}"""

JS_KILL_OVERLAY = """
() => {
  let n = 0;
  document.querySelectorAll('div[id^="login-full-panel"], div[id^="login-panel"]').forEach(e => { e.remove(); n++; });
  document.querySelectorAll('div').forEach(e => {
    const s = getComputedStyle(e);
    if ((s.position === 'fixed' || s.position === 'absolute') &&
        parseInt(s.zIndex || 0) > 500 && e.offsetHeight > 400 &&
        e.innerText && e.innerText.includes('登录') && e.innerText.length < 400) {
      e.remove(); n++;
    }
  });
  return n;
}
"""

JS_CLICK_TAB = """
(name) => {
  const els = [...document.querySelectorAll('span,div')];
  const t = els.find(e => e.children.length === 0 && e.innerText.trim() === name);
  if (t) { t.click(); return true; }
  return false;
}
"""


def _num(v):
    try:
        return int(v)
    except Exception:
        return None


class DouyinCollector:
    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log or (lambda *a, **k: None)
        self._pw = None
        self._ctx = None
        self._page = None
        self._cap = {}

    # ---------- 生命周期 ----------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        self.log("启动浏览器内核 ...")
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            channel=self.cfg.get("browser_channel") or "msedge",
            headless=bool(self.cfg.get("headless", True)),
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 950},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.on("response", self._on_response)
        self.log("浏览器就绪")

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = self._page = None

    # ---------- 响应捕获 ----------
    def _on_response(self, resp):
        url = resp.url
        if "/aweme/v1/web/" not in url:
            return
        key = url.split("?")[0].rsplit("/web/", 1)[-1].replace("/", "_")
        if key not in (EP_DETAIL, EP_MIX_AWEME, EP_MIX_LIST, EP_AWEME_POST, EP_SERIES_AWEME, EP_PROFILE):
            return
        try:
            data = resp.json()
        except Exception:
            return
        self._cap.setdefault(key, []).append(data)

    def _reset_cap(self):
        self._cap = {}

    def _wait_cap(self, key, timeout=20.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._cap.get(key):
                return self._cap[key]
            self._page.wait_for_timeout(300)
        return []

    # ---------- 采集：单条视频 → 所属合集 + 每集播放量 ----------
    def collect_video(self, aweme_id, should_stop=None):
        self._reset_cap()
        self._page.goto(
            f"https://www.douyin.com/video/{aweme_id}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        details = self._wait_cap(EP_DETAIL, timeout=25)
        if not details:
            raise RuntimeError("未捕获到视频详情接口，可能被风控或页面结构变化")

        detail = next((r.get("aweme_detail") for r in details
                       if str((r.get("aweme_detail") or {}).get("aweme_id")) == str(aweme_id)), None)
        if not detail:
            raise RuntimeError("视频详情未返回目标作品，可能页面受限；不能判定为无合集")
        author = detail.get("author") or {}
        mix = detail.get("mix_info") or {}
        statis = mix.get("statis") or {}

        result = {
            "aweme_id": aweme_id,
            "title": detail.get("desc"),
            "sec_uid": author.get("sec_uid"),
            "author_name": author.get("nickname"),
            "mix_id": mix.get("mix_id"),
            "mix_name": mix.get("mix_name"),
            "updated_to_episode": _num(statis.get("updated_to_episode")),
            "episodes": [],
        }

        if not result["mix_id"]:
            result["note"] = "该视频未发布在任何合集中"
            return result

        # A page may only request series/aweme even though mix/aweme still works.
        # Request the known endpoint in the same browser; use its real cursor.
        explicit = self._fetch_mix_pages(str(result["mix_id"]), should_stop)
        if explicit:
            responses = explicit
        else:
            self._wait_cap(EP_MIX_AWEME, timeout=20)
            responses = self._cap.get(EP_MIX_AWEME) or []
            if not responses:
                if self._cap.get(EP_SERIES_AWEME):
                    raise PlaybackUnavailable("页面分集响应无有效播放量，主动请求合集接口也未取得有效数据；保留缺失，不记录为零")
                raise RuntimeError("已识别合集，但主动请求和页面响应均未取得合集数据；稍后重试")
            self._expand_mix_panel()
            responses = self._cap.get(EP_MIX_AWEME) or []

        eps = {}
        for resp in responses:
            for a in resp.get("aweme_list") or []:
                item_mix = (a.get("mix_info") or {}).get("mix_id")
                if item_mix and str(item_mix) != str(result["mix_id"]):
                    continue
                item_author = (a.get("author") or {}).get("sec_uid")
                if item_author and result.get("sec_uid") and item_author != result["sec_uid"]:
                    continue
                st = a.get("statistics") or {}
                aid = str(a.get("aweme_id") or "")
                if not aid:
                    continue
                eps[aid] = {
                    "aweme_id": aid,
                    "title": a.get("desc"),
                    "play_count": _num(st.get("play_count")) if (_num(st.get("play_count")) or 0) > 0 else None,
                    "digg_count": _num(st.get("digg_count")),
                    "comment_count": _num(st.get("comment_count")),
                    "share_count": _num(st.get("share_count")),
                    "collect_count": _num(st.get("collect_count")),
                    "create_time": _num(a.get("create_time")),
                }
        ordered = self._order_episodes(eps)
        result["episodes"] = ordered
        if not ordered:
            raise RuntimeError("合集请求没有返回目标作品列表，保留待重试状态")
        if ordered and not any(e.get("play_count") is not None for e in ordered):
            raise PlaybackUnavailable("合集接口返回了作品，但播放量全为零占位或缺失，尚未取得可验证值")
        result["pagination_incomplete"] = bool(responses[-1].get("has_more"))
        result["has_more_left"] = result["pagination_incomplete"] or len(ordered) < (result.get("updated_to_episode") or 0)
        return result

    def _fetch_mix_pages(self, mix_id, should_stop=None):
        """Same-origin read, no signature reverse engineering or exported cookies."""
        should_stop = should_stop or (lambda: False)
        pages, cursor, seen = [], 0, set()
        limit = min(100, max(1, int(self.cfg.get("account_discovery_pages", 30))))
        for _ in range(limit):
            if should_stop():
                break
            try:
                response = self._page.evaluate("""async ({mixId,cursor}) => {
                    const params = new URLSearchParams({device_platform:'webapp',aid:'6383',mix_id:mixId,cursor:String(cursor),count:'15'});
                    try {
                        const r=await fetch('/aweme/v1/web/mix/aweme/?'+params,
                            {credentials:'same-origin',signal:AbortSignal.timeout(12000)});
                        if(!r.ok) return null;
                        return await r.json();
                    } catch { return null; }
                }""", {"mixId": mix_id, "cursor": cursor})
            except Exception:
                break
            if not isinstance(response, dict) or response.get("status_code") != 0 or not isinstance(response.get("aweme_list"), list):
                break
            rows = response["aweme_list"]
            ids = {str(a.get("aweme_id")) for a in rows if a.get("aweme_id")}
            # Repeated pages must not be interpreted as progress or completion.
            if rows and not ids - seen:
                break
            pages.append(response)
            seen.update(ids)
            if not response.get("has_more"):
                break
            next_cursor = _num(response.get("cursor"))
            if next_cursor is None or next_cursor <= cursor or not rows:
                break
            cursor = next_cursor
            self._page.wait_for_timeout(450)
        return pages

    def _expand_mix_panel(self):
        """翻页：合集集数多时，滚动触发加载更多；已取完则直接返回。"""
        if not self._mix_has_more():
            return
        for _ in range(8):
            before = sum(
                len(r.get("aweme_list") or [])
                for r in self._cap.get(EP_MIX_AWEME, [])
            )
            try:
                self._page.mouse.wheel(0, 2500)
            except Exception:
                pass
            self._page.wait_for_timeout(2200)
            after = sum(
                len(r.get("aweme_list") or [])
                for r in self._cap.get(EP_MIX_AWEME, [])
            )
            if after <= before:
                break

    def collect_collection(self, aweme_id, mix_id, known_posts, should_stop=None):
        """Detail pages expose a window around the current episode, not the entire mix.

        Open uncovered known episodes through normal navigation, merging windows.
        A terminal cursor only ends its window; compare the declared total too.
        """
        should_stop = should_stop or (lambda: False)
        result = self.collect_video(aweme_id, should_stop=should_stop)
        if result.get("mix_id") != mix_id:
            raise RuntimeError("返回合集与目标不一致，未写入播放数据")
        episodes = {e["aweme_id"]: e for e in result.get("episodes") or []}
        candidates = sorted((p for p in known_posts if p.get("mix_id") == mix_id),
                            key=lambda p: int(p.get("create_time") or 0))
        limit = min(100,max(1,int(self.cfg.get("account_discovery_pages",30))))
        visited = 1
        for post in candidates:
            if post["aweme_id"] in episodes:
                continue
            if should_stop() or visited >= limit:
                break
            self._page.wait_for_timeout(800)
            try:
                window = self.collect_video(post["aweme_id"], should_stop=should_stop)
                visited += 1
                if window.get("mix_id") != mix_id:
                    continue
                episodes.update({e["aweme_id"]:e for e in window.get("episodes") or []})
            except Exception as exc:
                self.log(f"合集分段补采中断，保留已取 {len(episodes)} 集：{str(exc)[:120]}")
                break
        result["episodes"] = self._order_episodes(episodes)
        expected = max(result.get("updated_to_episode") or 0,len(candidates))
        result["has_more_left"] = bool(result.get("pagination_incomplete")) or len(episodes) < expected or any(p["aweme_id"] not in episodes for p in candidates)
        return result

    def _mix_has_more(self):
        resps = self._cap.get(EP_MIX_AWEME) or []
        if not resps:
            return False
        return bool(resps[-1].get("has_more"))

    @staticmethod
    def _order_episodes(eps: dict):
        """按 create_time 排序推断集数；无时间时按接口返回顺序。"""
        items = list(eps.values())
        if all(i.get("create_time") for i in items):
            items.sort(key=lambda x: x["create_time"])
        for idx, it in enumerate(items, 1):
            it["episode_no"] = idx
        return items

    # ---------- 采集：作者主页公开作品列表 ----------
    def collect_author_posts(self, sec_uid, max_pages=30, should_stop=None):
        should_stop = should_stop or (lambda: False)
        self._reset_cap()
        self._page.goto(f"https://www.douyin.com/user/{sec_uid}", wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(5000)
        try:
            self._page.evaluate(JS_KILL_OVERLAY)
        except Exception:
            pass
        try:
            self._page.locator("span:text-is('作品')").first.click(timeout=5000)
        except Exception:
            try:
                self._page.evaluate(JS_CLICK_TAB, "作品")
            except Exception:
                pass
        max_pages = min(100, max(1, int(max_pages)))
        captured = self._wait_cap(EP_AWEME_POST, timeout=20)
        if not captured:
            raise RuntimeError("未捕获到作者作品列表接口，可能页面受限或抖音页面结构已变化")
        scan_status = "complete"
        for _ in range(max_pages):
            responses=self._cap.get(EP_AWEME_POST) or []
            if not responses or not responses[-1].get("has_more"):
                break
            if should_stop():
                scan_status = "stopped"
                break
            if self._page.get_by_text("登录后查看更多作品", exact=False).count():
                scan_status = "login_required"
                break
            if len(responses) >= max_pages:
                scan_status = "page_limit"
                break
            before = {str(a.get("aweme_id")) for r in responses for a in r.get("aweme_list") or []}
            advanced = False
            for attempt in range(3):
                pos = self._page.evaluate(JS_SCROLL_POSTS)
                self._page.mouse.move(pos["x"], pos["y"])
                self._page.mouse.wheel(0, 1800)
                for tick in range(12):
                    self._page.wait_for_timeout(400)
                    after = {str(a.get("aweme_id")) for r in self._cap.get(EP_AWEME_POST, []) for a in r.get("aweme_list") or []}
                    if after - before or not self._cap[EP_AWEME_POST][-1].get("has_more"):
                        advanced = True
                        break
                    if should_stop():
                        break
                if advanced or should_stop():
                    break
            if not advanced:
                scan_status = "stopped" if should_stop() else "login_required" if self._page.get_by_text("登录后查看更多作品", exact=False).count() else "stalled"
                break
        found={}
        for response in self._cap.get(EP_AWEME_POST,[]):
            for item in response.get("aweme_list") or []:
                aid=str(item.get("aweme_id") or "")
                if not aid: continue
                author=item.get("author") or {}
                if author.get("sec_uid") and author["sec_uid"] != sec_uid:
                    continue
                stats=item.get("statistics") or {}
                mix=item.get("mix_info") or {}
                mix_stats=mix.get("statis") or {}
                def metric(key):
                    value=_num(stats.get(key))
                    return value if value is not None and value>=0 else None
                found[aid]={
                    "aweme_id":aid,"title":item.get("desc"),
                    "cover_url":next(iter(((item.get("video") or {}).get("cover") or {}).get("url_list") or []), None),
                    "create_time":_num(item.get("create_time")),
                    "sec_uid":author.get("sec_uid") or sec_uid,
                    "author_name":author.get("nickname"),
                    "mix_id":str(mix.get("mix_id") or "") or None,
                    "mix_name":mix.get("mix_name"),
                    "play_count":metric("play_count"),
                    "digg_count":metric("digg_count"),
                    "comment_count":metric("comment_count"),
                    "share_count":metric("share_count"),
                    "collect_count":metric("collect_count"),
                    "mix_play_vv":_num(mix_stats.get("play_vv")),
                }
        items=list(found.values())
        items.sort(key=lambda x:x.get("create_time") or 0,reverse=True)
        nickname=next((x.get("author_name") for x in items if x.get("author_name")),None)
        profile = next((r.get("user") for r in self._cap.get(EP_PROFILE, [])
                        if (r.get("user") or {}).get("sec_uid") == sec_uid), {}) or {}
        avatar = (profile.get("avatar_thumb") or {}).get("url_list") or []
        has_more = bool((self._cap.get(EP_AWEME_POST) or [{}])[-1].get("has_more"))
        if has_more and scan_status == "complete":
            scan_status = "page_limit"
        return {"sec_uid":sec_uid,"author_name":nickname,"items":items,
                "has_more":has_more, "scan_status":scan_status,
                "scan_pages":len(self._cap.get(EP_AWEME_POST, [])),
                "profile":{"avatar_url":avatar[0] if avatar else None,
                           "homepage_count":_num(profile.get("aweme_count")),
                           "follower_count":_num(profile.get("follower_count")),
                           "total_favorited":_num(profile.get("total_favorited"))}}

    # ---------- 采集：作者主页 → 各合集官方总播放量 ----------
    def collect_author_mixes(self, sec_uid):
        self._reset_cap()
        self._page.goto(
            f"https://www.douyin.com/user/{sec_uid}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        self._page.wait_for_timeout(6000)
        try:
            self._page.evaluate(JS_KILL_OVERLAY)
        except Exception:
            pass
        try:
            self._page.locator("span:text-is('合集')").first.click(timeout=8000)
        except Exception:
            try:
                self._page.evaluate(JS_CLICK_TAB, "合集")
            except Exception:
                pass
        self._wait_cap(EP_MIX_LIST, timeout=20)
        self._expand_mix_panel()

        out = {}
        for resp in self._cap.get(EP_MIX_LIST, []):
            for m in resp.get("mix_infos") or []:
                st = m.get("statis") or {}
                mid = str(m.get("mix_id") or "")
                if not mid:
                    continue
                out[mid] = {
                    "mix_id": mid,
                    "mix_name": m.get("mix_name"),
                    "play_vv": _num(st.get("play_vv")),
                    "collect_vv": _num(st.get("collect_vv")),
                    "updated_to_episode": _num(st.get("updated_to_episode")),
                    "create_time": _num(m.get("create_time")),
                    "update_time": _num(m.get("update_time")),
                }
        return out

    def sleep_between(self):
        lo = float(self.cfg.get("interval_seconds", 5))
        self._page.wait_for_timeout(int((lo + random.random() * 3) * 1000))
