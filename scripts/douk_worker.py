"""Isolated adapter for the pinned DouK-Downloader distribution.

Run with .douk-venv Python; never import upstream's ``src`` into the monitor.
"""
import asyncio
import copy
import inspect
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "TikTokDownloader"
RUNTIME = ROOT / "data" / "douk"
sys.path.insert(0, str(VENDOR))
os.chdir(VENDOR)


EXTRAS = {
    "/douyin/user": ("账号资料", {"sec_user_id": "账号 ID"}),
    "/douyin/hot": ("热榜", {}),
    "/douyin/collection": ("我的收藏作品", {"sec_user_id": "自己的账号 ID"}),
    "/douyin/collects": ("收藏夹列表", {}),
    "/douyin/collects/detail": ("收藏夹作品", {"collects_id": "收藏夹 ID"}),
    "/douyin/collects/mix": ("收藏合集", {}),
    "/douyin/collects/music": ("收藏音乐", {}),
    "/douyin/collects/series": ("收藏短剧列表", {}),
    "/tiktok/user": ("账号资料", {"sec_user_id": "账号 secUid"}),
    "/tiktok/mix/list": ("账号合集列表", {"sec_user_id": "账号 secUid"}),
}


def setup_imports():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    import src.custom as custom
    import src.custom.internal as internal
    custom.VOLUME = internal.VOLUME = RUNTIME


def catalog():
    from fastapi import FastAPI
    from src.application.main_server import APIServer
    from src.config.settings import Settings
    api = object.__new__(APIServer)
    api.server = FastAPI()
    api.setup_routes()
    spec = api.server.openapi()
    from src.models.live import LiveTikTok
    spec["components"]["schemas"]["LiveTikTok"] = LiveTikTok.model_json_schema()
    spec["paths"]["/tiktok/live"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/LiveTikTok"}
    return {"version": "5.8", "commit": "e327b9549d284a8b1613566f949fe2dbb8a6c2a1",
            "openapi": spec, "extra": EXTRAS, "defaults": Settings.default,
            "unavailable": ["上游 Web UI（重构中，已由观潮页面替代）", "TikTok 评论采集（上游菜单禁用）", "话题作品下载（上游未实现）", "TikTok 第三方原画下载（上游菜单禁用）"]}


async def extra(api, path, payload):
    from src.interface import (User, InfoTikTok, Hot, Collection, Collects,
                               CollectsDetail, CollectsMix, CollectsMusic,
                               CollectsSeries, MixListTikTok)
    classes = {"/douyin/user": User, "/tiktok/user": InfoTikTok,
               "/douyin/collection": Collection, "/douyin/collects": Collects,
               "/douyin/collects/detail": CollectsDetail, "/douyin/collects/mix": CollectsMix,
               "/douyin/collects/music": CollectsMusic, "/douyin/collects/series": CollectsSeries,
               "/tiktok/mix/list": MixListTikTok}
    if path == "/douyin/hot":
        when, boards = await Hot(api.parameter).run()
        return [{"board": Hot.board_params[i].name, "collected_at": when, **row}
                for i, rows in boards for row in rows]
    cls = classes[path]
    allowed = inspect.signature(cls.__init__).parameters
    args = {k: v for k, v in payload.items() if k in allowed and k not in {"params", "self"}}
    return await cls(api.parameter, **args).run()


async def execute(request):
    from fastapi import FastAPI
    from src.application.main_server import APIServer
    from src.config import Parameter, Settings
    from src.manager import Database, DownloadRecorder
    from src.module import Cookie
    from src.record import BaseLogger
    from src.tools import ColorfulConsole

    console = ColorfulConsole(debug=False)
    settings = Settings(RUNTIME, console)
    config = copy.deepcopy(Settings.default)
    saved = RUNTIME / "settings.json"
    if saved.exists():
        config.update(json.loads(saved.read_text(encoding="utf-8-sig")))
    output = ROOT / "output" / "downloads"
    output.mkdir(parents=True, exist_ok=True)
    config.update(root=str(output), run_command="", max_retry=min(int(config.get("max_retry", 2)), 5))
    config["tiktok_platform"] = request["endpoint"].startswith("/tiktok/")
    config["douyin_platform"] = not config["tiktok_platform"]
    db = Database()
    db.file = RUNTIME / "DouK-Downloader.db"
    async with db:
        param = Parameter(settings, Cookie(settings, console), BaseLogger, console,
                          **copy.deepcopy(config), recorder=DownloadRecorder(db, True, console))
        try:
            param.set_headers_cookie()
            await param.update_params_offline()
            api = APIServer(param, db)
            api.server = FastAPI()
            api.setup_routes()
            download_counts = []
            original_chart = api.downloader.downloader_chart
            async def audited_chart(tasks, count, progress, **kwargs):
                await original_chart(tasks, count, progress, **kwargs)
                download_counts.append({k: len(v) for k, v in vars(count).items() if isinstance(v, set)})
            api.downloader.downloader_chart = audited_chart
            endpoint = request["endpoint"]
            items = request.get("items") or [request.get("params") or {}]
            result = []
            errors = []
            raw_rows = []
            for index, payload in enumerate(items):
                try:
                    payload = {**payload, "source": True}
                    links = api.links_tiktok if endpoint.startswith("/tiktok/") else api.links
                    for key, kind in (("detail_id", "detail"), ("sec_user_id", "user"), ("mix_id", "mix"), ("web_rid", "live")):
                        if isinstance(payload.get(key), str) and "http" in payload[key]:
                            parsed = await links.run(payload[key], kind)
                            if kind == "mix":
                                is_mix, ids = parsed[:2]
                                if len(parsed) > 2 and parsed[2]:
                                    payload["mix_title"] = parsed[2][0]
                                if not ids:
                                    raise ValueError("未识别到合集或作品链接")
                                payload.pop(key)
                                payload["mix_id" if is_mix else "detail_id"] = ids[0]
                            else:
                                if not parsed:
                                    raise ValueError("未识别到有效链接")
                                payload[key] = parsed[0]
                    if endpoint in EXTRAS:
                        for key in EXTRAS[endpoint][1]:
                            if not payload.get(key):
                                raise ValueError("缺少参数：" + key)
                        raw = await extra(api, endpoint, payload)
                    elif endpoint == "/tiktok/live":
                        from src.models.live import LiveTikTok
                        raw = (await api.handle_live(LiveTikTok.model_validate(payload), True))[0]
                    elif endpoint in {"/douyin/detail", "/tiktok/detail"}:
                        from src.interface import Detail, DetailTikTok
                        cls = DetailTikTok if endpoint.startswith("/tiktok/") else Detail
                        if not str(payload.get("detail_id", "")).isdigit():
                            raise ValueError("作品 ID 无效")
                        raw = await cls(param, detail_id=payload["detail_id"], proxy=payload.get("proxy")).run()
                        if not raw:
                            raise ValueError("作品详情接口没有返回有效数据")
                    elif endpoint == "/tiktok/mix":
                        # Upstream 5.8 route omitted tiktok=True; use its implementation correctly.
                        raw = await api.deal_mix_detail(True, payload["mix_id"], api=True,
                            source=True, tiktok=True, **{k: payload[k] for k in
                            ("pages", "count", "cursor", "proxy", "mix_title") if k in payload})
                    else:
                        route = next(r for r in api.server.routes if getattr(r, "path", "") == endpoint
                                     and "POST" in getattr(r, "methods", set()))
                        model = inspect.signature(route.endpoint).parameters["extract"].annotation
                        response = await route.endpoint(model.model_validate(payload), token=None)
                        response = response.model_dump()
                        raw = response.get("data", response.get("url"))
                        if raw is None:
                            raise RuntimeError(response.get("message") or "接口没有返回有效数据")
                    if raw is None:
                        raise RuntimeError("接口没有返回有效数据")
                    rows = raw if isinstance(raw, list) else [raw]
                    if endpoint.endswith("/detail") and "detail_id" in payload:
                        for row in rows:
                            if str(row.get("aweme_id") or row.get("id")) != str(payload["detail_id"]):
                                raise ValueError("返回作品 ID 与请求不一致，未写入数据")
                    raw_rows.extend(rows)
                    if request.get("download"):
                        if endpoint.endswith("/collects/music"):
                            formatted = await api.extractor.run(rows, None, "music")
                            await api.downloader.run_music(formatted)
                        elif endpoint.endswith("/live"):
                            raise ValueError("直播请使用结果中的录制按钮，指定录制时长")
                        else:
                            root, record_params, logger = api.record.run(param)
                            async with logger(root, name=Path(request.get("progress_path", "Download")).parent.name,
                                              console=console, **record_params) as recorder:
                                formatted = await api.extractor.run(rows, recorder, tiktok=endpoint.startswith("/tiktok/"))
                            before_counts = len(download_counts)
                            if payload.get("mark"):
                                for item in formatted:
                                    item["mark"] = api.extractor.cleaner.filter_name(payload["mark"])
                            mode = "mix" if endpoint.endswith("/mix") else payload.get("tab", "post")
                            batch = endpoint.endswith(("/account", "/mix"))
                            first = rows[0] if rows else {}
                            author = first.get("author") or {}
                            await api.downloader.run(formatted, "batch" if batch else "detail",
                                tiktok=endpoint.startswith("/tiktok/"), mode=mode,
                                mark=payload.get("mark", ""), user_id=payload.get("sec_user_id", ""),
                                user_name=author.get("nickname", ""), mix_id=payload.get("mix_id", ""),
                                mix_title=payload.get("mix_title") or (first.get("mix_info") or {}).get("mix_name", ""))
                            totals = download_counts[before_counts:]
                            completed = sum(sum(c.values()) for c in totals)
                            if formatted and completed == 0:
                                raise ValueError("已获取作品数据，但媒体没有下载成功；请检查下载地址、代理或文件大小限制")
                    result.append({"index": index, "count": len(rows)})
                except Exception as exc:
                    errors.append({"index": index, "error": str(exc)[:500]})
                    # A broken session should not hammer hundreds of videos.
                    if len(errors) >= 3 and not result:
                        break
                if request.get("progress_path"):
                    write_json(Path(request["progress_path"]), {"done": index + 1, "total": len(items),
                               "count": len(raw_rows), "errors": len(errors)})
                    write_json(Path(request["progress_path"]).with_name("result.json"),
                               {"ok": bool(result), "data": raw_rows, "batches": result, "errors": errors,
                                "attempted": index + 1, "requested": len(items)})
                if len(items) > 1:
                    await asyncio.sleep(1)
            return {"data": raw_rows, "batches": result, "errors": errors,
                    "attempted": len(result) + len(errors), "requested": len(items),
                    "downloads": download_counts}
        finally:
            await param.close_client()


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


if __name__ == "__main__":
    setup_imports()
    if sys.argv[1] == "catalog":
        write_json(ROOT / sys.argv[2], catalog())
    else:
        req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        try:
            value = asyncio.run(execute(req))
            write_json(Path(sys.argv[2]), {"ok": bool(value["batches"]), **value})
        except Exception as exc:
            write_json(Path(sys.argv[2]), {"ok": False, "error": str(exc)[:800]})
            raise
