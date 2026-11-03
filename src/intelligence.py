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

def create_collection(name,keywords=""):
    init();name=(name or "").strip()
    if not name: raise ValueError("合集名称不能为空")
    if len(name)>60: raise ValueError("合集名称不能超过60字")
    kws=",".join(dict.fromkeys(x.strip() for x in (keywords or "").replace("，",",").split(",") if x.strip()))
    store.x("INSERT INTO intel_collections(name,keywords,created_at) VALUES(?,?,?)",(name,kws,store.now_str()))
    return collections()

def delete_collection(cid):
    init()
    store.x("DELETE FROM intel_collection_mix WHERE collection_id=?",(int(cid),))
    store.x("DELETE FROM intel_collections WHERE id=?",(int(cid),))
    return collections()

def alerts():
    from analyze import health,overview
    h=health();o=overview();a=[]
    for x in h.get("issues",[]):a.append({"type":"校验差异","level":"warning","title":x.get("mix_name"),"detail":"官方总量与分集求和差异 "+str(x.get("diff"))})
    if o.get("stop_count"):a.append({"type":"停涨观察","level":"info","title":"合集停涨","detail":str(o["stop_count"])+" 个合集最近两次快照无增长"})
    r=store.last_runs(1)
    if r and r[0].get("failed"):a.append({"type":"采集失败","level":"warning","title":"最近采集存在失败","detail":str(r[0]["failed"])+" 条链接失败"})
    return a

def ai_summary(niche, stage, content_format, frequency):
    import json
    import os
    import urllib.request
    key=os.environ.get("OPENAI_API_KEY","").strip()
    if not key:
        return {"configured":False,"message":"未配置 OPENAI_API_KEY。当前规则诊断可用；配置后启用模型总结。"}
    base=os.environ.get("OPENAI_BASE_URL","https://api.openai.com/v1").rstrip("/")
    model=os.environ.get("OPENAI_MODEL","gpt-4o-mini")
    sample=videos()[:30]
    payload={"niche":niche,"stage":stage,"format":content_format,"weekly_posts":frequency,
      "sample":[{"title":v.get("title"),"plays":v.get("play_count"),"play_delta":v.get("play_delta"),
      "likes":v.get("digg_count"),"comments":v.get("comment_count"),"author":v.get("author_name"),
      "mix":v.get("mix_name")} for v in sample]}
    prompt=("你是短视频增长分析师。只根据提供的样本提出可验证的运营假设。"
      "明确区分观测事实与推测；样本不足要直说；不得声称知道曝光、完播率、推荐流量来源或算法权重。"
      "请用中文输出：账号定位、样本洞察、3个可测试内容支柱、7天发布实验、指标复盘方法、风险与证据缺口。"
      "用户需求与监控样本如下："+json.dumps(payload,ensure_ascii=False))
    body=json.dumps({"model":model,"messages":[{"role":"system","content":"你输出务实、具体、可验证的短视频运营建议。"},{"role":"user","content":prompt}],"temperature":0.4}).encode("utf-8")
    req=urllib.request.Request(base+"/chat/completions",data=body,headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=45) as response:
            data=json.loads(response.read().decode("utf-8"))
        text=data.get("choices",[{}])[0].get("message",{}).get("content","")
        if not text: return {"configured":True,"error":"模型返回为空"}
        return {"configured":True,"model":model,"content":text,"sample_count":len(sample)}
    except Exception as exc:
        return {"configured":True,"error":str(exc)[:300]}

def init_account_tables():
    store.x("CREATE TABLE IF NOT EXISTS tracked_accounts (sec_uid TEXT PRIMARY KEY, profile_url TEXT NOT NULL, nickname TEXT, note TEXT DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT, last_scan_at TEXT, last_error TEXT)")
    store.x("CREATE TABLE IF NOT EXISTS tracked_account_videos (aweme_id TEXT PRIMARY KEY, sec_uid TEXT NOT NULL, title TEXT, create_time INTEGER, mix_id TEXT, mix_name TEXT, first_seen TEXT, last_seen TEXT, metric_status TEXT DEFAULT 'unknown')")
    store.x("CREATE INDEX IF NOT EXISTS idx_tav_sec_uid ON tracked_account_videos(sec_uid, create_time)")
    store.x("CREATE TABLE IF NOT EXISTS account_video_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, aweme_id TEXT NOT NULL, fetch_date TEXT NOT NULL, fetched_at TEXT NOT NULL, play_count INTEGER, digg_count INTEGER, comment_count INTEGER, share_count INTEGER, collect_count INTEGER, metric_status TEXT NOT NULL, source TEXT NOT NULL, UNIQUE(aweme_id,fetch_date))")
    store.x("CREATE INDEX IF NOT EXISTS idx_avs_aweme ON account_video_snapshots(aweme_id,fetch_date)")

def add_account(profile_url, note=""):
    import re
    from urllib.parse import urlparse
    url=(profile_url or "").strip()
    match=re.search(r"/user/([^/?#]+)",url)
    sec_uid=match.group(1) if match else (url if re.fullmatch(r"[A-Za-z0-9_=-]{12,}",url) else "")
    if not sec_uid: raise ValueError("请输入有效的抖音账号主页链接")
    canonical="https://www.douyin.com/user/"+sec_uid
    init_account_tables()
    store.x("INSERT INTO tracked_accounts(sec_uid,profile_url,note,created_at) VALUES(?,?,?,?) ON CONFLICT(sec_uid) DO UPDATE SET profile_url=excluded.profile_url,note=CASE WHEN excluded.note='' THEN tracked_accounts.note ELSE excluded.note END",
      (sec_uid,canonical,note or "",store.now_str()))
    return list_tracked_accounts()

def list_tracked_accounts():
    init_account_tables()
    return store.q("SELECT * FROM tracked_accounts ORDER BY created_at DESC")

def active_tracked_accounts():
    init_account_tables()
    return store.q("SELECT * FROM tracked_accounts WHERE enabled=1 ORDER BY created_at")

def toggle_tracked_account(sec_uid, enabled):
    init_account_tables()
    store.x("UPDATE tracked_accounts SET enabled=? WHERE sec_uid=?",(1 if enabled else 0,sec_uid))
    return list_tracked_accounts()

def delete_tracked_account(sec_uid):
    init_account_tables()
    store.x("DELETE FROM tracked_accounts WHERE sec_uid=?",(sec_uid,))
    return list_tracked_accounts()

def upsert_account_identity(sec_uid, nickname):
    if not sec_uid: return
    init_account_tables()
    url="https://www.douyin.com/user/"+sec_uid
    store.x("INSERT INTO tracked_accounts(sec_uid,profile_url,nickname,created_at) VALUES(?,?,?,?) ON CONFLICT(sec_uid) DO UPDATE SET nickname=COALESCE(NULLIF(excluded.nickname,''),tracked_accounts.nickname)",
      (sec_uid,url,nickname or "",store.now_str()))

def save_account_posts(sec_uid, nickname, posts):
    init_account_tables()
    upsert_account_identity(sec_uid,nickname)
    now=store.now_str();fd=store.today_str()
    for p in posts or []:
        aid=str(p.get("aweme_id") or "")
        if not aid: continue
        mix_id=p.get("mix_id")
        play=p.get("play_count")
        # 作者作品列表接口中播放量可能为 0 占位；绝不将其记成真实 0。
        public_play=play if play is not None and int(play)>0 else None
        status="public" if public_play is not None else "not_exposed"
        store.x("INSERT INTO tracked_account_videos(aweme_id,sec_uid,title,create_time,mix_id,mix_name,first_seen,last_seen,metric_status) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(aweme_id) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''),tracked_account_videos.title),create_time=COALESCE(excluded.create_time,tracked_account_videos.create_time),mix_id=COALESCE(excluded.mix_id,tracked_account_videos.mix_id),mix_name=COALESCE(excluded.mix_name,tracked_account_videos.mix_name),last_seen=excluded.last_seen,metric_status=excluded.metric_status",
          (aid,sec_uid,p.get("title"),p.get("create_time"),mix_id,p.get("mix_name"),now,now,status))
        store.x("INSERT INTO account_video_snapshots(aweme_id,fetch_date,fetched_at,play_count,digg_count,comment_count,share_count,collect_count,metric_status,source) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(aweme_id,fetch_date) DO UPDATE SET fetched_at=excluded.fetched_at,play_count=COALESCE(excluded.play_count,account_video_snapshots.play_count),digg_count=COALESCE(excluded.digg_count,account_video_snapshots.digg_count),comment_count=COALESCE(excluded.comment_count,account_video_snapshots.comment_count),share_count=COALESCE(excluded.share_count,account_video_snapshots.share_count),collect_count=COALESCE(excluded.collect_count,account_video_snapshots.collect_count),metric_status=excluded.metric_status,source=excluded.source",
          (aid,fd,now,public_play,p.get("digg_count"),p.get("comment_count"),p.get("share_count"),p.get("collect_count"),status,"author_post"))
    store.x("UPDATE tracked_accounts SET nickname=COALESCE(NULLIF(?,''),nickname),last_scan_at=?,last_error=NULL WHERE sec_uid=?",(nickname or "",now,sec_uid))
    return len(posts or [])

def tracked_account_videos(sec_uid=None):
    init_account_tables()
    sql="SELECT v.*,a.nickname,a.profile_url,s.play_count,s.digg_count,s.comment_count,s.share_count,s.collect_count,s.metric_status snapshot_status,s.fetch_date FROM tracked_account_videos v LEFT JOIN tracked_accounts a ON a.sec_uid=v.sec_uid LEFT JOIN account_video_snapshots s ON s.aweme_id=v.aweme_id AND s.fetch_date=(SELECT MAX(s2.fetch_date) FROM account_video_snapshots s2 WHERE s2.aweme_id=v.aweme_id)"
    args=()
    if sec_uid:
        sql+=" WHERE v.sec_uid=?";args=(sec_uid,)
    sql+=" ORDER BY COALESCE(v.create_time,0) DESC"
    return store.q(sql,args)

def tracked_account_summary():
    init_account_tables()
    accounts=list_tracked_accounts()
    for a in accounts:
        counts=store.q("SELECT COUNT(*) c FROM tracked_account_videos WHERE sec_uid=?",(a["sec_uid"],))
        a["video_count"]=counts[0]["c"] if counts else 0
    return accounts

def discovered_videos():
    init_account_tables()
    rows=store.q("SELECT v.*,a.nickname,a.profile_url,s.play_count,s.digg_count,s.comment_count,s.share_count,s.collect_count,s.metric_status snapshot_status,s.fetch_date FROM tracked_account_videos v LEFT JOIN tracked_accounts a ON a.sec_uid=v.sec_uid LEFT JOIN account_video_snapshots s ON s.aweme_id=v.aweme_id AND s.fetch_date=(SELECT MAX(s2.fetch_date) FROM account_video_snapshots s2 WHERE s2.aweme_id=v.aweme_id) ORDER BY COALESCE(v.create_time,0) DESC")
    for r in rows:
        mix=store.q("SELECT play_count,digg_count,comment_count,share_count,collect_count,fetch_date FROM video_snapshots WHERE aweme_id=? AND mix_id IS NOT NULL AND play_count IS NOT NULL ORDER BY fetch_date DESC LIMIT 1",(r["aweme_id"],))
        if mix:
            for k in ("play_count","digg_count","comment_count","share_count","collect_count"):
                if mix[0].get(k) is not None:r[k]=mix[0][k]
            r["snapshot_status"]="public_mix_metric"
            prev=store.q("SELECT play_count FROM video_snapshots WHERE aweme_id=? AND mix_id IS NOT NULL AND fetch_date<? ORDER BY fetch_date DESC LIMIT 1",(r["aweme_id"],mix[0]["fetch_date"]))
        else:
            prev=store.q("SELECT play_count FROM account_video_snapshots WHERE aweme_id=? AND fetch_date<? ORDER BY fetch_date DESC LIMIT 1",(r["aweme_id"],r.get("fetch_date") or ""))
        r["delta"]=(r.get("play_count")-prev[0]["play_count"]) if prev and r.get("play_count") is not None and prev[0].get("play_count") is not None else None
    return rows

