"""Content intelligence module."""
import store

def init():
    store.x("CREATE TABLE IF NOT EXISTS intel_collections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, keywords TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")

def videos():
    rows = store.q("SELECT v.aweme_id,v.mix_id,v.episode_no,v.title,v.play_count,v.digg_count,v.comment_count,v.share_count,v.collect_count,v.fetch_date,m.mix_name,m.author_name FROM video_snapshots v LEFT JOIN mixes m ON m.mix_id=v.mix_id WHERE v.fetch_date=(SELECT MAX(v2.fetch_date) FROM video_snapshots v2 WHERE v2.aweme_id=v.aweme_id) ORDER BY COALESCE(v.play_count,0) DESC")
    for r in rows:
        prev=store.q("SELECT play_count,digg_count,comment_count,share_count,collect_count FROM video_snapshots WHERE aweme_id=? AND fetch_date<? ORDER BY fetch_date DESC LIMIT 1",(r["aweme_id"],r["fetch_date"]))
        p=prev[0] if prev else {}
        for k in ("play_count","digg_count","comment_count","share_count","collect_count"):
            r[k.replace("_count","_delta")]=r.get(k)-p.get(k) if r.get(k) is not None and p.get(k) is not None else None
    return rows

def accounts():
    rows=store.q("SELECT m.author_name,m.mix_id,m.episode_count,s.play_vv,s.fetch_date FROM mixes m JOIN mix_snapshots s ON s.mix_id=m.mix_id WHERE s.fetch_date=(SELECT MAX(s2.fetch_date) FROM mix_snapshots s2 WHERE s2.mix_id=m.mix_id)")
    out={}
    for r in rows:
        name=r.get("author_name") or "未知作者"
        a=out.setdefault(name,{"name":name,"mix_count":0,"play":0,"episodes":0,"last_seen":r["fetch_date"],"delta":0})
        a["mix_count"]+=1;a["play"]+=r.get("play_vv") or 0;a["episodes"]+=r.get("episode_count") or 0
        p=store.q("SELECT play_vv FROM mix_snapshots WHERE mix_id=? AND fetch_date<? ORDER BY fetch_date DESC LIMIT 1",(r["mix_id"],r["fetch_date"]))
        if p and p[0]["play_vv"] is not None and r["play_vv"] is not None:a["delta"]+=r["play_vv"]-p[0]["play_vv"]
    return sorted(out.values(),key=lambda x:x["play"],reverse=True)

def collections():
    init();out=[];all_videos=videos()
    for c in store.q("SELECT * FROM intel_collections ORDER BY id DESC"):
        kws=[x.strip() for x in c["keywords"].split(",") if x.strip()]
        matched=[v for v in all_videos if any(k.lower() in ((v.get("title") or "")+" "+(v.get("mix_name") or "")).lower() for k in kws)]
        c["video_count"]=len(matched);c["play_sum"]=sum(v.get("play_count") or 0 for v in matched);c["videos"]=matched[:100];out.append(c)
    return out
