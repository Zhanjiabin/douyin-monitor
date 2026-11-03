"""链接解析：短链展开、aweme_id 提取、清单文本解析，全部走纯 HTTP，不需要浏览器。"""
import json
import re

import requests

from config import CACHE_PATH, USER_AGENT, ensure_dirs

URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff，。、；：\"'）)】]+")
AWEME_ID_RE = re.compile(r"/(?:video|note|share/video)/(\d{6,})")
SHORT_HOST_RE = re.compile(r"https?://v\.douyin\.com/([A-Za-z0-9_\-]+)")
DOUYIN_HOST_RE = re.compile(r"^https?://([\w\-]+\.)*(douyin\.com|iesdouyin\.com)(/|$)", re.I)


def is_douyin_url(url: str) -> bool:
    """只认抖音域名，避免把用户粘贴的其它链接也当抓取目标。"""
    return bool(DOUYIN_HOST_RE.match((url or "").strip()))


def extract_urls(text: str) -> list:
    """从一段文本（可能含多行/分享文案）里抽出所有【抖音】链接，去重保序。"""
    found = URL_RE.findall(text or "")
    seen, out = set(), []
    for u in found:
        u = u.rstrip("/").strip()
        if not is_douyin_url(u):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_aweme_id(url: str):
    m = AWEME_ID_RE.search(url or "")
    return m.group(1) if m else None


def expand_short_url(url: str, timeout: int = 15):
    """展开 v.douyin.com 短链，返回最终 URL；失败返回 None。"""
    if not SHORT_HOST_RE.search(url or ""):
        return url
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=timeout
        )
        return resp.url
    except Exception:
        return None


def resolve_aweme_id(url: str):
    """任意形式的抖音视频链接 → aweme_id。"""
    aid = parse_aweme_id(url)
    if aid:
        return aid
    final = expand_short_url(url)
    if final:
        return parse_aweme_id(final)
    return None


class ResolveCache:
    """aweme_id → {mix_id, mix_name, sec_uid, author_name} 的本地缓存。"""

    def __init__(self, path=None):
        ensure_dirs()
        self.path = path or CACHE_PATH
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, aweme_id: str):
        return self.data.get(aweme_id)

    def put(self, aweme_id: str, info: dict) -> None:
        if not aweme_id:
            return
        cur = self.data.setdefault(aweme_id, {})
        cur.update({k: v for k, v in (info or {}).items() if v is not None})

    def save(self) -> None:
        ensure_dirs()
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def representative_of(self, mix_id: str):
        """已知缓存里，属于该合集的一个 aweme_id，用于后续直接刷新该合集。"""
        for aid, info in self.data.items():
            if info.get("mix_id") == mix_id:
                return aid
        return None
