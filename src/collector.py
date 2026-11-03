"""抖音采集内核。

原理（2026-10 实测）：抖音页面是 JS 反爬壳，接口需 a_bogus 签名，
因此不自己逆向签名，而是用真实浏览器打开页面，监听浏览器自身发出的接口响应，取回 JSON。

三个关键接口：
  aweme/detail  视频详情     → 作者 sec_uid、所属合集 mix_id / mix_name
  mix/aweme     合集内视频   → 该合集【每一集的真实 play_count】（核心数据源）
  mix/list      账号合集列表 → 合集【官方口径总播放量 play_vv】

注意：aweme/detail 里的 statistics.play_count 恒为 0，不要用它；
      单集播放量只在 mix/aweme 里是真实值。
"""
import random
import time

from playwright.sync_api import sync_playwright

from config import BROWSER_PROFILE, USER_AGENT

EP_DETAIL = "aweme_detail_"
EP_MIX_AWEME = "mix_aweme_"
EP_MIX_LIST = "mix_list_"

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
        if key not in (EP_DETAIL, EP_MIX_AWEME, EP_MIX_LIST):
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
    def collect_video(self, aweme_id):
        self._reset_cap()
        self._page.goto(
            f"https://www.douyin.com/video/{aweme_id}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        details = self._wait_cap(EP_DETAIL, timeout=25)
        if not details:
            raise RuntimeError("未捕获到视频详情接口，可能被风控或页面结构变化")

        detail = details[0].get("aweme_detail") or {}
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

        # 合集内视频列表（含每集真实播放量）
        self._wait_cap(EP_MIX_AWEME, timeout=20)
        self._expand_mix_panel()

        eps = {}
        for resp in self._cap.get(EP_MIX_AWEME, []):
            for a in resp.get("aweme_list") or []:
                st = a.get("statistics") or {}
                aid = str(a.get("aweme_id") or "")
                if not aid:
                    continue
                eps[aid] = {
                    "aweme_id": aid,
                    "title": a.get("desc"),
                    "play_count": _num(st.get("play_count")),
                    "digg_count": _num(st.get("digg_count")),
                    "comment_count": _num(st.get("comment_count")),
                    "share_count": _num(st.get("share_count")),
                    "collect_count": _num(st.get("collect_count")),
                    "create_time": _num(a.get("create_time")),
                }
        ordered = self._order_episodes(eps)
        result["episodes"] = ordered
        result["has_more_left"] = self._mix_has_more()
        return result

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
