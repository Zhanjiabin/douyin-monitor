"""Refresh playback for known collections using saved account discovery results."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import intelligence, store
from collector import DouyinCollector, PlaybackUnavailable
from config import load_config
from pipeline import _persist_mix

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with DouyinCollector(load_config(), log=lambda s:print(s,flush=True)) as col:
        for account in intelligence.active_tracked_accounts():
            posts=intelligence.tracked_account_videos(account["sec_uid"])
            mids=list(dict.fromkeys(p["mix_id"] for p in posts if p.get("mix_id")))
            for mid in mids:
                group=sorted((p for p in posts if p.get("mix_id")==mid),key=lambda p:p.get("create_time") or 0)
                try:
                    result=col.collect_collection(group[0]["aweme_id"],mid,group)
                    _persist_mix(mid,result,store.today_str())
                    if not result.get("has_more_left"):
                        intelligence.mark_account_mix_scanned(account["sec_uid"],mid,max(p.get("create_time") or 0 for p in group))
                    print(account["nickname"],group[0].get("mix_name"),len(result["episodes"]),'partial',result['has_more_left'],flush=True)
                except Exception as exc:
                    status="unavailable" if isinstance(exc,PlaybackUnavailable) else "failed"
                    intelligence.save_playback_check(mid,status,str(exc)[:300])
                    if status == "unavailable":
                        intelligence.mark_account_mix_scanned(account["sec_uid"],mid,max(p.get("create_time") or 0 for p in group))
                    print(account["nickname"],group[0].get("mix_name"),status,str(exc)[:100],flush=True)
