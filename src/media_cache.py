"""Persist public Douyin thumbnails. Never proxy arbitrary URLs from web requests."""
import hashlib
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import requests
from config import DATA_DIR

MEDIA_DIR = DATA_DIR / "media"
SAFE_NAME = re.compile(r"[a-f0-9]{64}\.(?:jpg|png|webp)$")


def media_path(name):
    return MEDIA_DIR / name if SAFE_NAME.fullmatch(name) else None


def cache_image(url):
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host.endswith("." + d) for d in ("douyinpic.com", "byteimg.com", "ibytedtos.com")):
        return None
    key = hashlib.sha256(url.encode()).hexdigest()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("jpg", "png", "webp"):
        if (MEDIA_DIR / f"{key}.{ext}").is_file():
            return f"/media/{key}.{ext}"
    try:
        with requests.get(url, timeout=(5, 10), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                return None
            raw = bytearray()
            for chunk in response.iter_content(32768):
                raw.extend(chunk)
                if len(raw) > 3_000_000:
                    return None
            ext = "jpg" if raw.startswith(b'\xff\xd8\xff') else "png" if raw.startswith(b'\x89PNG\r\n\x1a\n') else "webp" if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None
            if not ext:
                return None
            target = MEDIA_DIR / f"{key}.{ext}"
            temporary = target.with_suffix(".part")
            temporary.write_bytes(raw)
            temporary.replace(target)
            return f"/media/{target.name}"
    except (requests.RequestException, OSError):
        return None


def cache_images(urls):
    unique = list(dict.fromkeys(u for u in urls if u))
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(zip(unique, pool.map(cache_image, unique)))
