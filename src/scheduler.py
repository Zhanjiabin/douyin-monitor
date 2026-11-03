"""内置定时器：按配置的时间点触发抓取，支持多时间点、改配置即时生效。

另外带一个「补跑」机制，解决两种最常见的漏采场景：
  1. 上班开机时间晚于设定时间点（例如 8:40 才开机，8:30 就永远错过了）
  2. 电脑在设定时间点处于睡眠状态，唤醒后时间点已过
做法：只要【今天还没有采集数据】且【当前已过最早的时间点】，就自动补跑一次。
"""
import threading
import time
from datetime import datetime, timedelta

import store
from config import load_config


class Scheduler(threading.Thread):
    def __init__(self, trigger, log=None):
        super().__init__(daemon=True)
        self.trigger = trigger
        self.log = log or (lambda *a: None)
        self._stop = threading.Event()
        self._fired = set()          # 已触发过的 "YYYY-MM-DD HH:MM" / "catchup:日期"
        self._last_cfg_check = 0
        self._times = []
        self._catch_up = True

    def stop(self):
        self._stop.set()

    def _reload(self):
        try:
            cfg = load_config()
            self._times = [t for t in (cfg.get("schedule_times") or []) if t]
            self._catch_up = bool(cfg.get("catch_up", True))
        except Exception:
            self._times = []

    def next_run_text(self):
        if not self._times:
            return "未启用定时（仅手动抓取）"
        now = datetime.now()
        cands = []
        for t in self._times:
            try:
                hh, mm = t.split(":")
                dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                if dt <= now:
                    dt = dt + timedelta(days=1)
                cands.append(dt)
            except Exception:
                continue
        if not cands:
            return "未启用定时（仅手动抓取）"
        return min(cands).strftime("%Y-%m-%d %H:%M")

    # ---------- 补跑判断 ----------
    def _should_catch_up(self, now):
        if not self._catch_up or not self._times:
            return False
        today = now.strftime("%Y-%m-%d")
        if ("catchup:" + today) in self._fired:
            return False
        # 还没到最早的时间点，不算错过
        if now.strftime("%H:%M") < min(self._times):
            return False
        try:
            if store.has_data_on(today):
                self._fired.add("catchup:" + today)   # 今天已有数据，不再检查
                return False
            if not store.active_watch_items():
                # 清单为空：不补跑，但也【不】记已检查，
                # 这样用户当天后续导入链接后仍能被自动补跑
                return False
        except Exception:
            return False
        return True

    def run(self):
        self._reload()
        self.log(f"定时器启动，时间点：{self._times or '（无）'}"
                 f"{'，已开启补跑' if self._catch_up else ''}")
        while not self._stop.is_set():
            try:
                now = datetime.now()
                if time.time() - self._last_cfg_check > 60:
                    self._reload()
                    self._last_cfg_check = time.time()

                stamp = now.strftime("%Y-%m-%d %H:%M")
                if now.strftime("%H:%M") in self._times and stamp not in self._fired:
                    self._fired.add(stamp)
                    self.log(f"到达定时时间点 {now.strftime('%H:%M')}，自动开始抓取")
                    self.trigger()
                elif self._should_catch_up(now):
                    key = "catchup:" + now.strftime("%Y-%m-%d")
                    if self.trigger():
                        self._fired.add(key)
                        self.log(f"今天（{now.strftime('%Y-%m-%d')}）还没有采集数据，"
                                 f"已自动补跑一次")
                    else:
                        self.log("检测到需要补跑，但有任务正在运行，稍后重试")

                if len(self._fired) > 200:
                    d = datetime.now().strftime("%Y-%m-%d")
                    self._fired = {s for s in self._fired if d in s}
            except Exception as e:
                self.log(f"定时器异常：{e}")
            self._stop.wait(20)
