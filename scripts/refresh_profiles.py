"""Refresh tracked public profiles and thumbnails without re-fetching collections."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import intelligence
from config import load_config
from collector import DouyinCollector

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with DouyinCollector(load_config(), log=lambda s:print(s,flush=True)) as col:
        for a in intelligence.active_tracked_accounts():
            result = col.collect_author_posts(a["sec_uid"], max_pages=30)
            intelligence.save_discovery(a["sec_uid"], result)
            print(result["author_name"],len(result["items"]),result["profile"].get("homepage_count"),result["scan_status"],flush=True)
