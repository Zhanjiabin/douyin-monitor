"""Inspect public fields for the exact video in the supplied comparison screenshot."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from collector import DouyinCollector,EP_DETAIL
from config import load_config

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    aid='7703151458754472787'
    with DouyinCollector(load_config()) as col:
        result=col.collect_video(aid)
        detail=next((r.get('aweme_detail') for r in col._cap.get(EP_DETAIL,[]) if str((r.get('aweme_detail') or {}).get('aweme_id'))==aid),{})
        video=detail.get('video') or {}
        summary={'aweme_id':aid,'statistics':detail.get('statistics'),'mix_info':detail.get('mix_info'),
                 'series_info_keys':list((detail.get('series_info') or {}).keys()),
                 'duration_ms':video.get('duration'),'has_play_address':bool((video.get('play_addr') or {}).get('url_list')),
                 'has_download_address':bool((video.get('download_addr') or {}).get('url_list')),
                 'caption_fields':{k:v for k,v in detail.items() if any(s in k for s in ('caption','subtitle','transcri'))},
                 'video_keys':list(video.keys()),'text_fields':[k for k in detail if any(s in k for s in ('text','desc','chapter'))]}
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
        (Path(__file__).resolve().parents[1]/'output'/'reference-video-fields.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
