"""全局配置：路径、默认参数、读写 config.json。"""
import json
import sys
from pathlib import Path


def _project_root() -> Path:
    """源码运行时 = 项目根目录；打包成 exe 后 = exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _project_root()
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

if getattr(sys, "frozen", False):
    # 打包后：前端资源随 exe 一起打进临时解包目录
    WEBUI_DIR = Path(getattr(sys, "_MEIPASS", ROOT)) / "webui"
    if not WEBUI_DIR.exists():
        WEBUI_DIR = ROOT / "webui"
else:
    WEBUI_DIR = Path(__file__).resolve().parent / "webui"

BROWSER_PROFILE = ROOT / ".browser_profile"
CONFIG_PATH = ROOT / "config.json"
DB_PATH = DATA_DIR / "douyin.db"
CACHE_PATH = DATA_DIR / "resolve_cache.json"

DEFAULT_CONFIG = {
    "schedule_times": [],          # 空 = 只手动抓取（本用户偏好）
    "catch_up": True,
    "interval_seconds": 5,
    "max_retries": 3,
    # 连续失败多少条后重建一次浏览器（0 = 不启用）。
    # 只在真的连续失败时才触发，正常抓取零开销，不改变采集节奏。
    "restart_after_fails": 5,
    "headless": True,
    "browser_channel": "msedge",
    "fetch_mix_total": True,
    "output_dir": "",              # 空 = 程序目录下的 output（便于整机拷贝）
    "port": 8765,
    # 播放量分级阈值（可在设置页改）
    "level_success": 20000,        # 达标线
    "level_high": 500000,          # 优秀线
    "level_top": 1000000,          # 爆款线
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, OUTPUT_DIR, LOG_DIR, BROWSER_PROFILE):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg or {})
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_output_dir(cfg: dict = None) -> Path:
    """把 output_dir 解析成实际目录。

    - 留空 → 程序目录下的 output（默认，整机拷贝不会出问题）
    - 填了路径 → 用它；但如果该盘符/根目录不存在（比如换了机器），
      自动回退到默认目录，避免把文件写到莫名其妙的地方
    """
    cfg = cfg if cfg is not None else load_config()
    raw = str(cfg.get("output_dir") or "").strip()
    if not raw:
        return OUTPUT_DIR
    p = Path(raw)
    try:
        anchor = p.anchor
        if anchor and not Path(anchor).exists():
            return OUTPUT_DIR
    except Exception:
        return OUTPUT_DIR
    return p
