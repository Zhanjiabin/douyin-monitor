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
