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
