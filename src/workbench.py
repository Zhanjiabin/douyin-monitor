"""Product-facing workflows over the validated data providers and local library."""
import hashlib
import json
import re
import shutil

import auth
from config import OUTPUT_DIR
import corpus
import douk
import insights
import intelligence
import integrations
import playback
import store
import tikhub

APP = "/api/v1/douyin/app/v3/"


def state():
    data = insights.dashboard(90, include_archived=True)
    return {"videos": data["videos"], "accounts": data["accounts"], "stats": data["stats"],
            "settings": playback.settings(), "today_calls": tikhub.usage(), "jobs": douk.jobs(),
            "generated_at": data["generated_at"]}


def op_id(action):
    return next(op["operationId"] for op in tikhub.operations().values() if op["path"] == APP + action)


def prepare(data):
    action = data.get("action", "account")
    if action not in {"account", "video", "playback", "comments", "download"}:
        raise ValueError("请选择同步账号、作品详情、播放统计、评论或视频下载")
    videos = insights.dashboard(90, include_archived=True)["videos"]
    account = str(data.get("account") or "")
    raw = str(data.get("input") or "").strip()
    selected_ids = data.get("video_ids")
    try:
        pages = int(data.get("pages", 1))
    except (TypeError, ValueError):
        raise ValueError("页数必须为整数") from None
    if not 1 <= pages <= 5:
        raise ValueError("每次最多采集 5 页")
    titles = {"account": "同步账号作品", "video": "采集作品详情", "playback": "更新播放量", "comments": "采集作品评论", "download": "下载视频"}
    if action == "download":
        identifiers = playback.ids([x for x in re.split(r"[\s,，]+", raw) if x])
        if len(identifiers) > 20:
            raise ValueError("每次最多下载 20 条作品")
        request = {"endpoint": "mcpro", "action": "parse", "platform": "dy", "mode": "detail",
                   "input": "\n".join("https://www.douyin.com/video/" + aid for aid in identifiers),
                   "download": True, "title": titles[action]}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        reason = ""
        if auth.enabled():
            if shutil.disk_usage(OUTPUT_DIR).free < 128*1024**2:
                reason = "服务器可用空间不足，请先扩容或清理空间，再下载新视频"
            elif not integrations.python("MediaCrawlerPro-Downloader/DownloadServer").is_file():
                reason = "服务器下载引擎尚未安装，请先完成下载运行环境部署"
        return {"action": action, "title": titles[action], "calls": len(identifiers), "allowed": not bool(reason),
                "paid": False, "reason": reason, "units": len(identifiers), "fingerprint": fingerprint,
                "provider": "本机视频解析", "request": request}
    if action == "account":
        accounts = [a for a in intelligence.tracked_account_summary() if a.get("enabled") and (not account or a["sec_uid"] == account)]
        if not accounts:
            raise ValueError("请先添加监控账号")
        items = [{"params": {"sec_user_id": a["sec_uid"], "max_cursor": 0, "count": 20}} for a in accounts]
        api_action = "fetch_user_post_videos"
        units = len(items)
    else:
        if selected_ids is not None:
            identifiers = playback.ids(selected_ids)
        elif raw:
            identifiers = playback.ids([x for x in re.split(r"[\s,，]+", raw) if x])
        elif action == "playback":
            identifiers = [v["aweme_id"] for v in videos if (not account or v["sec_uid"] == account)
                           and (data.get("missing_only", True) is not True or v.get("play_count") is None)]
        else:
            raise ValueError("请输入作品 ID 或视频长链接")
        if not identifiers:
            raise ValueError("当前作品均有播放记录，无需补采；取消“只补缺失”可更新已有记录")
        units = len(identifiers)
        if action == "playback":
            method = data.get("playback_method", "single")
            if method not in ("single", "batch"):
                raise ValueError("播放统计方式无效")
            api_action = "fetch_multi_video_statistics" if method == "batch" else "fetch_video_statistics"
            batch_size = 50 if method == "batch" else 1
            items = [{"params": {"aweme_ids": ",".join(identifiers[i:i + batch_size])}} for i in range(0, units, batch_size)]
            pages = 1
        elif action == "video":
            api_action = "fetch_one_video_v3"
            items = [{"params": {"aweme_id": aid}} for aid in identifiers]
            pages = 1
        else:
            api_action = "fetch_video_comments"
            items = [{"params": {"aweme_id": aid, "cursor": 0, "count": 20}} for aid in identifiers]
    request = {"endpoint": "tikhub_api", "operation_id": op_id(api_action), "params": {},
               "items_json": json.dumps(items, ensure_ascii=False), "page_limit": pages,
               "import_results": True, "title": titles[action]}
    preview = tikhub.plan(request)
    return {**preview, "action": action, "paid": True, "units": units, "title": titles[action],
            "provider": "TikHub", "request": request,
            "playback_method": data.get("playback_method", "single") if action == "playback" else None}


def plan(data):
    result = prepare(data)
    # The product page needs a plan, not a second copy of the underlying API input.
    return {k: v for k, v in result.items() if k not in {"request", "preview", "operation"}}


def submit(data):
    prepared = prepare(data)
    if data.get("fingerprint") != prepared["fingerprint"] or data.get("confirmed") is not True:
        raise ValueError("参数已变更，请重新预览并确认请求")
    if not prepared["allowed"]:
        raise ValueError(prepared["reason"])
    request = prepared["request"]
    if prepared["paid"]:
        request.update(allow_charges=True, fingerprint=prepared["fingerprint"])
    return douk.submit(request)


def research(aid):
    if not isinstance(aid, str) or not re.fullmatch(r"\d{10,25}", aid):
        raise ValueError("作品 ID 无效")
    data = insights.dashboard(90, include_archived=True)
    video = next((v for v in data["videos"] if v["aweme_id"] == aid), None)
    if not video:
        import discovery
        video = discovery.work(aid)
        if not video:
            raise ValueError("作品不在监控或发现结果中")
    corpus.init()
    content = store.q("SELECT description,title,published_at FROM content_library WHERE platform='dy' AND content_id=?", (aid,))
    description = (content[0].get("description") if content else "") or video.get("title") or ""
    related = [v for v in data["videos"] if v["aweme_id"] != aid and v["sec_uid"] == video["sec_uid"]]
    related.sort(key=lambda v: v.get("play_count") or -1, reverse=True)
    files = [f for f in integrations.assets()["items"] if aid in f["name"]]
    account = next((a for a in data["accounts"] if a["sec_uid"] == video.get("sec_uid")), {})
    import discovery
    return {"video": video, "description": description, "history": intelligence.video_history(aid),
            "comparison": discovery.comparison(aid,data["videos"]),
            "is_own": account.get("role") == "own", "monitored": video.get("monitored", True),
            "comments": corpus.comments("dy", aid)["items"], "files": files, "related": related[:6],
            "title_features": video.get("features", []), "topic": video.get("topic")}
