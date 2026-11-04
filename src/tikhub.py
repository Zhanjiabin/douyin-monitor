"""OpenAPI-driven TikHub workbench with shared budgets and explicit execution.

The pinned catalog is the only route authority. Planning is offline. A paid request
is reserved in playback_requests before transport, never retried or redirected.
"""
import copy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache
import hashlib
import json
import math
import re
import threading
import time
from urllib.parse import quote, quote_plus, urlsplit

from jsonschema import Draft202012Validator
import requests

from config import ROOT
import playback
import store

SPEC = ROOT / "integrations" / "tikhub.openapi.json"
APP = "/api/v1/douyin/app/v3/"
CURSORS = ("max_cursor", "cursor", "cursor_str", "continuation", "next_cursor", "offset", "page")
SECRET = re.compile(r"cookie|authorization|api[_-]?key|access[_-]?token|sessionid|password", re.I)
VIDEO_ACTIONS = {"fetch_one_video", "fetch_one_video_v2", "fetch_one_video_v3", "fetch_one_video_by_share_url",
                 "fetch_multi_video", "fetch_multi_video_v2", "fetch_user_post_videos", "fetch_user_like_videos",
                 "fetch_video_mix_post_list", "fetch_series_video_list", "fetch_music_video_list", "fetch_hashtag_video_list"}
STAT_PATHS = {APP + "fetch_video_statistics": "app", APP + "fetch_multi_video_statistics": "app_batch",
              playback.MODES["xingtu"]["path"]: "xingtu"}
APP_TITLES = dict(zip((
    "fetch_one_video fetch_one_video_v2 fetch_one_video_v3 fetch_share_info_by_share_code "
    "fetch_multi_video fetch_multi_video_v2 fetch_one_video_by_share_url fetch_video_high_quality_play_url "
    "fetch_multi_video_high_quality_play_url fetch_video_statistics fetch_multi_video_statistics handler_user_profile "
    "fetch_user_fans_list fetch_user_post_videos fetch_user_like_videos fetch_video_comments fetch_video_comment_replies "
    "fetch_video_mix_detail fetch_video_mix_post_list fetch_user_series_list fetch_series_video_list fetch_series_detail "
    "fetch_music_detail fetch_music_video_list fetch_hashtag_detail fetch_hashtag_video_list fetch_hot_search_list "
    "fetch_live_hot_search_list fetch_music_hot_search_list fetch_brand_hot_search_list fetch_brand_hot_search_list_detail "
    "generate_douyin_short_url generate_douyin_video_share_qrcode register_device open_douyin_app_to_video_detail "
    "open_douyin_app_to_user_profile open_douyin_app_to_keyword_search open_douyin_app_to_send_private_message"
).split(), (
    "作品详情", "作品详情 V2", "作品详情 V3", "分享口令解析", "批量作品详情", "批量作品详情 V2", "分享链接解析",
    "原画视频链接", "批量原画视频链接", "视频播放统计", "批量视频播放统计", "账号资料", "账号粉丝列表",
    "账号发布作品", "账号喜欢的作品", "视频评论", "评论回复", "合集详情", "合集作品", "账号短剧列表", "短剧作品",
    "短剧详情", "音乐详情", "音乐关联作品", "话题详情", "话题作品", "热搜榜", "直播热榜", "音乐热榜", "品牌热榜",
    "品牌榜详情", "生成短链接", "生成作品分享码", "注册设备", "生成作品跳转链接", "生成账号跳转链接", "生成搜索跳转链接", "生成私信跳转链接"
)))


@lru_cache(maxsize=1)
def spec():
    value = json.loads(SPEC.read_text(encoding="utf-8"))
    # Local refs only: validation must never fetch an arbitrary remote schema.
    def check(node):
        if isinstance(node, dict):
            if "$ref" in node and not node["$ref"].startswith("#/"):
                raise ValueError("接口规范含不支持的外部引用")
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(value)
    return value


@lru_cache(maxsize=1)
def operations():
    result = {}
    for path, methods in spec()["paths"].items():
        if not path.startswith("/api/") or any(x in path for x in ("..", "?", "#", ":", "\\")):
            raise ValueError("接口路径不合法")
        for method, op in methods.items():
            if method not in {"get", "post"}:
                raise ValueError("规范新增请求方式，需要先更新客户端")
            result[op["operationId"]] = {**op, "path": path, "method": method.upper()}
    return result


def operation(identifier):
    if not isinstance(identifier, str) or identifier not in operations():
        raise ValueError("请选择清单中的接口")
    return operations()[identifier]


def resolve(schema, depth=0):
    if depth > 20:
        return {"description": "递归结构；请查看完整规范"}
    if isinstance(schema, list):
        return [resolve(v, depth + 1) for v in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        target = spec()
        for key in schema["$ref"][2:].split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
        return resolve({**target, **{k: v for k, v in schema.items() if k != "$ref"}}, depth + 1)
    result = {k: resolve(v, depth + 1) for k, v in schema.items()}
    # A few upstream schemas use OpenAPI named examples inside JSON Schema.
    # Normalize this annotation; all actual validation constraints stay intact.
    if isinstance(result.get("examples"), dict):
        result["examples"] = [v.get("value", v) if isinstance(v, dict) else v for v in result["examples"].values()]
    return result


def title(op):
    if op["path"].startswith(APP) and op["path"][len(APP):] in APP_TITLES:
        return APP_TITLES[op["path"][len(APP):]]
    return op.get("summary", op["path"].rsplit("/", 1)[-1]).split("/")[0].strip()


def import_kind(op):
    if op["path"] in STAT_PATHS:
        return "播放统计"
    if op["path"].startswith(APP):
        action = op["path"][len(APP):]
        if action in VIDEO_ACTIONS:
            return "作品"
        if action in {"fetch_video_comments", "fetch_video_comment_replies"}:
            return "评论"
    return ""


def brief(op):
    return {"id": op["operationId"], "title": title(op), "path": op["path"], "method": op["method"],
            "group": op.get("tags", ["Other"])[0], "auth": bool(op.get("security", spec().get("security"))),
            "import_kind": import_kind(op)}


def catalog():
    ops = [brief(op) for op in operations().values()]
    counts = {}
    for op in ops:
        counts[op["group"]] = counts.get(op["group"], 0) + 1
    return {"version": spec()["info"]["version"], "operations": ops,
            "groups": [{"id": key, "count": count} for key, count in counts.items()],
            "source": "https://api.tikhub.io/openapi.json", "total": len(ops)}


def example(schema):
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema or "oneOf" in schema:
        return example(next((v for v in schema.get("anyOf", schema.get("oneOf")) if v.get("type") != "null"), {}))
    kind = schema.get("type")
    if kind == "object":
        return {key: example(value) for key, value in schema.get("properties", {}).items()}
    if kind == "array":
        return [example(schema.get("items", {}))]
    return {"integer": 0, "number": 0, "boolean": False, "null": None}.get(kind, "")


def detail(identifier):
    op = operation(identifier)
    params = [{**p, "schema": resolve(p.get("schema", {})), "secret": bool(SECRET.search(p["name"]))}
              for p in op.get("parameters", [])]
    body = op.get("requestBody", {})
    schema = resolve(body.get("content", {}).get("application/json", {}).get("schema", {}))
    return {**brief(op), "parameters": params, "body_schema": schema,
            "has_body": bool(body), "body_required": body.get("required", False),
            "body_example": json.dumps(example(schema), ensure_ascii=False, indent=2) if body else "",
            "description": op.get("description", "").split("# [English]")[0].strip(),
            "cursor_parameter": next((k for k in CURSORS if any(p["name"] == k for p in params)), None),
            "docs": "https://api.tikhub.io/#/" + quote(op.get("tags", [""])[0]) + "/" + quote(identifier)}


def loads(raw, label):
    def invalid(_):
        raise ValueError(label + " 不允许 NaN 或 Infinity")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError(label + " 必须是 1MB 以内的 JSON 文本")
    try:
        return json.loads(raw, parse_constant=invalid)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from None


def validate(value, schema, label):
    validator = Draft202012Validator({**schema, "components": spec().get("components", {})})
    error = next(validator.iter_errors(value), None)
    if error:
        path = ".".join(str(v) for v in error.absolute_path)
        # Schema errors can include Cookie/credential values; report location and rule only.
        raise ValueError(f"{label}{'.' + path if path else ''} 不符合接口规则（{error.validator}）")


def coerce(value, schema, label):
    kinds = schema.get("anyOf", schema.get("oneOf", [schema]))
    types = {x.get("type") for x in kinds}
    if isinstance(value, str):
        if "integer" in types and "string" not in types:
            if not re.fullmatch(r"[+-]?\d+", value):
                raise ValueError(label + " 必须为整数")
            value = int(value)
        elif "number" in types and "string" not in types:
            try:
                value = float(value)
            except ValueError:
                raise ValueError(label + " 必须为数字") from None
            if not math.isfinite(value):
                raise ValueError(label + " 必须为有限数字")
        elif "boolean" in types and "string" not in types:
            if value not in {"true", "false"}:
                raise ValueError(label + " 必须为 true 或 false")
            value = value == "true"
        elif types & {"array", "object"} and "string" not in types:
            value = loads(value, label)
    validate(value, schema, label)
    return value


def prepare(op, params, body_json=None):
    if not isinstance(params, dict):
        raise ValueError("查询参数必须为对象")
    definitions = {p["name"]: p for p in op.get("parameters", [])}
    if set(params) - set(definitions):
        raise ValueError("包含此接口未声明的查询参数")
    values = {}
    for name, p in definitions.items():
        if p["in"] != "query":
            raise ValueError("当前规范含新的参数位置，需要先更新客户端")
        if name not in params:
            if p.get("required"):
                raise ValueError(name + " 为必填参数")
            continue
        schema = resolve(p.get("schema", {}))
        values[name] = coerce(params[name], schema, name)
        if p.get("required") and values[name] == "":
            raise ValueError(name + " 不能为空")
    body = None
    if op.get("requestBody"):
        if body_json in (None, ""):
            if op["requestBody"].get("required"):
                raise ValueError("请填写 JSON 请求体")
        else:
            schema = op["requestBody"]["content"]["application/json"]["schema"]
            body = loads(body_json, "请求体")
            validate(body, schema, "请求体")
    elif body_json not in (None, ""):
        raise ValueError("此接口不接受请求体")
    action = op["path"]
    limits = {APP + "fetch_video_statistics": 2, APP + "fetch_multi_video_statistics": 50,
              APP + "fetch_multi_video_high_quality_play_url": 50}
    if action in limits:
        ids = (body or {}).get("aweme_ids", "") if isinstance(body, dict) else values.get("aweme_ids", "")
        if ids:
            if len(playback.ids(str(ids).split(","))) > limits[action]:
                raise ValueError(f"此接口每次最多 {limits[action]} 个作品 ID")
    return {"params": values, "body": body}


def statistics_queue(request, op, prepared, pages):
    """Only the single-video statistics adapter may use the concurrent queue."""
    if not request.get("single_video_queue"):
        return None
    if request["single_video_queue"] is not True or op["path"] != APP + "fetch_video_statistics" or pages != 1:
        raise ValueError("逐条刷新队列只支持单作品播放统计接口")
    identifiers = []
    for item in prepared:
        group = playback.ids(str(item["params"].get("aweme_ids", "")).split(","))
        if len(group) != 1 or group[0] in identifiers:
            raise ValueError("逐条刷新每个请求必须是一个不同的作品 ID")
        identifiers.extend(group)
    workers = request.get("concurrency", 3)
    if isinstance(workers, bool) or not re.fullmatch(r"[1-5]", str(workers)):
        raise ValueError("同时刷新数量需为 1 至 5")
    return {"kind": "single_video_queue", "concurrency": min(int(workers), len(prepared))}


def compile_request(request):
    op = operation(request.get("operation_id"))
    base = prepare(op, request.get("params", {}), request.get("body_json")) if not request.get("items_json") else None
    if request.get("items_json"):
        items = loads(request["items_json"], "批量任务")
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValueError("批量任务需为 1 至 1000 项的数组")
        prepared = []
        for item in items:
            if not isinstance(item, dict) or set(item) - {"params", "body"}:
                raise ValueError("批量任务每项只能包含 params 和 body")
            prepared.append(prepare(op, item.get("params", {}),
                                    json.dumps(item["body"], ensure_ascii=False) if "body" in item else None))
    else:
        prepared = [base]
    try:
        pages = int(request.get("page_limit", 1))
    except (ValueError, TypeError):
        raise ValueError("分页上限必须为整数") from None
    if not 1 <= pages <= 100:
        raise ValueError("分页上限为 1 至 100 页")
    cursor = detail(op["operationId"])["cursor_parameter"]
    if pages > 1 and (not cursor or op["method"] != "GET"):
        raise ValueError("此接口不支持自动分页，请使用单次或批量请求")
    canonical = {"operation": op["operationId"], "version": spec()["info"]["version"],
                 "requests": prepared, "pages": pages, "import_results": request.get("import_results") is True}
    execution = statistics_queue(request, op, prepared, pages)
    if execution:
        canonical["execution"] = execution
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return op, prepared, pages, fingerprint


def usage():
    playback.init()
    return store.q("SELECT COUNT(*) n FROM playback_requests WHERE created_at>=?", (store.today_str(),))[0]["n"]


def plan(request):
    op, prepared, pages, fingerprint = compile_request(request)
    execution = statistics_queue(request, op, prepared, pages)
    cfg, used = playback.settings(), usage()
    calls = len(prepared) * pages
    reason = ""
    if brief(op)["auth"] and not cfg["key_set"]:
        reason = "未配置 TikHub API Key"
    elif not execution and calls > cfg["max_calls_per_job"]:
        reason = "请求上限超过单次任务限额"
    elif calls + used > cfg["daily_call_limit"]:
        reason = "请求上限超过今日剩余额度"
    if batch_statistics(op) and not cfg["allow_batch_statistics"]:
        reason = "批量播放统计已关闭，请在设置中明确开启后使用"
    return {"operation": brief(op), "calls": calls, "batches": len(prepared), "page_limit": pages,
            "today_calls": used, "daily_limit": cfg["daily_call_limit"], "job_limit": cfg["max_calls_per_job"],
            "allowed": not reason, "reason": reason, "fingerprint": fingerprint,
            "single_video_queue": bool(execution), "concurrency": execution["concurrency"] if execution else 1,
            "preview": redact(prepared, request), "pricing": "https://tikhub.io/zh/pricing#plans"}


def batch_statistics(op):
    action = op["path"].rsplit("/", 1)[-1]
    return "statistic" in action and ("multi" in action or "batch" in action)


def secrets(request):
    result = [playback.settings(False).get("api_key", "")]
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if SECRET.search(str(key)) and child not in (None, ""):
                    result.append(str(child))
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(request)
    for key in ("body_json", "items_json"):
        try:
            walk(json.loads(request.get(key) or "null"))
        except (TypeError, ValueError):
            pass
    # Four official operations accept a Cookie as a top-level JSON string.
    # It has no property name for walk() to inspect.
    if request.get("operation_id") in operations():
        op = operation(request["operation_id"])
        body_schema = resolve(op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}))
        if body_schema.get("type") == "string" and SECRET.search(body_schema.get("title", "") + body_schema.get("description", "")):
            try:
                value = json.loads(request.get("body_json") or "null")
                if isinstance(value, str):
                    result.append(value)
                items = json.loads(request.get("items_json") or "[]")
                if isinstance(items, list):
                    result.extend(item["body"] for item in items if isinstance(item, dict) and isinstance(item.get("body"), str))
            except (TypeError, ValueError):
                pass
    return [v for v in result if v]


def redact(value, request):
    sensitive = secrets(request)
    def walk(v):
        if isinstance(v, dict):
            return {k: "[hidden]" if SECRET.search(k) else walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            for secret in sensitive:
                v = v.replace(secret, "[hidden]").replace(quote(secret, safe=""), "[hidden]").replace(quote_plus(secret), "[hidden]")
        return v
    return walk(value)


def query_pairs(op, values):
    result = []
    for p in op.get("parameters", []):
        name = p["name"]
        if name not in values:
            continue
        value = values[name]
        # A nullable query parameter is represented by omission, unlike JSON null.
        if value is None:
            continue
        def scalar(v):
            return "null" if v is None else "true" if v is True else "false" if v is False else str(v)
        if isinstance(value, list):
            if p.get("explode", True):
                result.extend((name, scalar(v)) for v in value)
            else:
                result.append((name, ",".join(scalar(v) for v in value)))
        else:
            result.append((name, json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else scalar(value)))
    return result


def records(body):
    """Find the response's main list without discarding the full response evidence."""
    node = body.get("data", body) if isinstance(body, dict) else body
    for _ in range(5):
        if isinstance(node, list):
            return [x if isinstance(x, dict) else {"value": x} for x in node]
        if not isinstance(node, dict):
            return [{"value": node}]
        for key in ("aweme_list", "aweme_details", "aweme_detail", "aweme_infos", "video_list", "statistics_list", "comments", "videos", "user_list", "items", "list", "results"):
            if isinstance(node.get(key), list):
                return [x if isinstance(x, dict) else {"value": x} for x in node[key]]
        child = next((node[k] for k in ("data", "result", "aweme_detail", "aweme_info", "user") if isinstance(node.get(k), (dict, list))), None)
        if child is None:
            return [node] if node else []
        node = child
    return [node] if isinstance(node, dict) else []


def next_cursor(body, parameter, current):
    if not parameter:
        return None
    node = body
    for _ in range(5):
        if not isinstance(node, dict):
            return None
        flag = node.get("has_more", node.get("hasMore"))
        if flag in (True, 1, "1", "true"):
            for key in ("next_cursor", parameter):
                value = node.get(key)
                if value is not None and str(value) != str(current):
                    return value
            if parameter == "page" and str(current or "1").isdigit():
                return int(current or 1) + 1
            return None
        if flag in (False, 0, "0", "false"):
            return None
        node = node.get("data", node.get("result"))
    return None


def import_response(op, body, params, job_id=""):
    kind = import_kind(op)
    if kind == "播放统计":
        mode = STAT_PATHS[op["path"]]
        expected = playback.ids([params["item_id"]] if mode == "xingtu" else str(params["aweme_ids"]).split(","))
        rows = playback.parse(body, mode, expected)
        known = {r["aweme_id"] for r in store.q("SELECT aweme_id FROM tracked_account_videos")}
        tracked = [row for row in rows if row["aweme_id"] in known]
        playback.persist(tracked)
        return {"imported": len(tracked), "playback": sum(r["play_count"] is not None for r in tracked)}
    if kind == "作品":
        import douk
        # Explicit App V3 adapter; other platforms never enter the Douyin library.
        rows = [row for row in records(body) if row.get("aweme_id") and ("desc" in row or "video" in row or "author" in row)]
        return douk.import_rows(rows, source="tikhub_app_v3")
    if kind == "评论":
        import corpus
        corpus.init()
        content_id = str(params.get("aweme_id", params.get("item_id", "")))
        rows = [row for row in records(body) if row.get("cid")]
        if any(str(row.get("aweme_id", content_id)) != content_id for row in rows):
            raise ValueError("评论返回的作品 ID 与请求不一致，未写入")
        for row in rows:
            user = row.get("user") or {}
            store.x("""INSERT OR REPLACE INTO content_comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("dy", str(row["cid"]), content_id, str(row.get("reply_id") or params.get("comment_id") or ""),
                     row.get("text", ""), corpus.published(row.get("create_time")), playback.number(row.get("digg_count")),
                     str(user.get("sec_uid") or user.get("uid") or ""), user.get("nickname", ""), "tikhub_app_v3",
                     job_id, store.now_str(), json.dumps(row, ensure_ascii=False)))
        return {"imported": len(rows), "comments": len(rows)}
    return {"imported": 0}


def execute_statistics_queue(request, preview, prepared, progress, cancelled):
    """Bounded fan-out, one ID per request, using the shared budget and adapter.

    The ordinary per-job cap applies to each one-request child. The explicitly
    selected queue is limited by the daily budget, with every HTTP attempt
    reserved under playback.LOCK. Completed children are never replayed.
    """
    items = [{"aweme_id": str(item["params"]["aweme_ids"]), "status": "pending"} for item in prepared]
    results, stop, reason = {}, threading.Event(), ""
    workers = preview["concurrency"]

    def publish():
        counts = {status: sum(i["status"] == status for i in items) for status in
                  ("pending", "running", "completed", "failed", "not_returned", "stopped")}
        progress({"done": counts["completed"] + counts["failed"] + counts["not_returned"],
                  "total": len(items), "succeeded": counts["completed"], "failed": counts["failed"],
                  "missing": counts["not_returned"], "running": counts["running"],
                  "pending": counts["pending"], "stopped": counts["stopped"],
                  "count": sum(len(r.get("data", [])) for r in results.values()),
                  "concurrency": workers, "items": copy.deepcopy(items)})

    def run(index):
        child = {k: v for k, v in request.items() if k not in {"single_video_queue", "concurrency", "fingerprint", "params"}}
        child.update(params={}, items_json=json.dumps([{"params": prepared[index]["params"]}], ensure_ascii=False))
        child["_statistics_queue_child"] = True
        try:
            child["fingerprint"] = plan(child)["fingerprint"]
            return execute(child, cancelled=lambda: stop.is_set() or cancelled())
        except Exception:
            # Re-read only public guards; never expose an unexpected transport error.
            p = plan(child)
            return {"ok": False, "data": [], "receipts": [], "errors": [{"error": p["reason"] or "该作品刷新未完成，请查看请求记录"}], "guard_blocked": True}

    publish()
    next_item = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-refresh") as pool:
        pending = {}
        while pending or next_item < len(items):
            if cancelled():
                reason = "已停止后续刷新，在途请求结束后保留结果"
                stop.set()
            while not stop.is_set() and len(pending) < workers and next_item < len(items):
                items[next_item]["status"] = "running"
                pending[pool.submit(run, next_item)] = next_item
                next_item += 1
            publish()
            if not pending:
                break
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                index = pending.pop(future)
                result = future.result()
                results[index] = result
                receipts = result.get("receipts", [])
                errors = result.get("errors", [])
                row = items[index]
                if any(r.get("http_status") in {401, 402, 403, 429} for r in receipts) or result.get("guard_blocked"):
                    reason = errors[0]["error"] if errors else "接口暂不可用，已停止后续刷新"
                    stop.set()
                if receipts:
                    row["status"] = "failed" if errors or not result["ok"] else "not_returned"
                    if row["status"] != "failed":
                        body = receipts[0].get("response", {})
                        try:
                            parsed = playback.parse(body, "app", [row["aweme_id"]])
                            play_count = next((r["play_count"] for r in parsed if r["aweme_id"] == row["aweme_id"]), None)
                            if play_count is not None:
                                row.update(status="completed", play_count=play_count)
                        except ValueError:
                            row["status"] = "failed"
                    if errors:
                        row["message"] = errors[0]["error"]
                else:
                    row.update(status="stopped", message=errors[0]["error"] if errors else reason or "已停止")
            publish()
    for row in items:
        if row["status"] in {"pending", "running"}:
            row.update(status="stopped", message=reason or "未执行")
    publish()
    receipts, data, errors, imported = [], [], [], {}
    for index, result in sorted(results.items()):
        receipts.extend({**r, "batch": index + 1} for r in result.get("receipts", []))
        data.extend(result.get("data", []))
        errors.extend({**e, "batch": index + 1, "aweme_id": items[index]["aweme_id"]} for e in result.get("errors", []))
        for key, value in result.get("imported", {}).items():
            imported[key] = imported.get(key, 0) + value
    if reason and not any(e["error"] == reason for e in errors):
        errors.append({"error": reason})
    succeeded = sum(r["status"] == "completed" for r in items)
    failed = sum(r["status"] == "failed" for r in items)
    missing = sum(r["status"] == "not_returned" for r in items)
    return {"ok": any(r["ok"] for r in receipts), "data": data, "errors": errors, "receipts": receipts,
            "operation": preview["operation"], "attempted": len(receipts), "requested": len(items),
            "imported": imported, "cancelled": cancelled(), "refresh_items": items,
            "message": f"已更新 {succeeded} 条，失败 {failed} 条，未返回播放 {missing} 条，未执行 {sum(r['status']=='stopped' for r in items)} 条"}


def execute(request, progress=lambda _: None, cancelled=lambda: False):
    request = copy.deepcopy(request)
    preview = plan(request)
    if request.get("allow_charges") is not True or request.get("fingerprint") != preview["fingerprint"]:
        raise ValueError("参数已变化或尚未确认，请重新预览请求数")
    if not preview["allowed"]:
        raise ValueError(preview["reason"])
    op, prepared, pages, _ = compile_request(request)
    if preview["single_video_queue"]:
        return execute_statistics_queue(request, preview, prepared, progress, cancelled)
    cursor = detail(op["operationId"])["cursor_parameter"]
    receipts, all_rows, errors, imported, calls = [], [], [], {}, 0
    halted = False
    for batch, item in enumerate(prepared):
        params = copy.deepcopy(item["params"])
        seen = {str(params.get(cursor))}
        for page in range(pages):
            if cancelled() or halted:
                break
            if request.get("monitor_run_id"):
                import monitoring
                if not monitoring.job_allowed(request):
                    errors.append({"error": "监控计划已停用、到期或范围变化，已停止后续请求"})
                    halted = True
                    break
            with playback.LOCK:
                cfg = playback.settings(False)
                if batch_statistics(op) and not cfg["allow_batch_statistics"]:
                    errors.append({"error": "批量播放统计已关闭，已停止后续请求"})
                    halted = True
                    break
                if usage() >= cfg["daily_call_limit"] or calls >= cfg["max_calls_per_job"]:
                    errors.append({"error": "请求限额已用完，已停止后续请求"})
                    halted = True
                    break
                rid = store.x("INSERT INTO playback_requests(created_at,source,mode,video_ids,status) VALUES(?,?,?,?,?)",
                              (store.now_str(), "tikhub_api", op["operationId"], "[]", "started"))
            calls += 1
            receipt = {"batch": batch + 1, "page": page + 1, "ledger_id": rid, "ok": False}
            start = time.monotonic()
            try:
                headers = {"Accept": "application/json"}
                # Keep the key used by this attempt available for redaction even
                # if the user clears or changes settings while HTTP is in flight.
                request["_transport_api_key"] = cfg["api_key"]
                if brief(op)["auth"]:
                    if not cfg["api_key"]:
                        raise ValueError("API Key 已清除，已停止请求")
                    headers["Authorization"] = "Bearer " + cfg["api_key"]
                response = requests.request(op["method"], playback.BASE + op["path"],
                    params=query_pairs(op, params), json=item["body"] if op.get("requestBody") else None,
                    headers=headers, timeout=(10, 120), allow_redirects=False)
                receipt["http_status"] = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = {"text": response.text[:500000]}
                receipt["response"] = redact(body, request)
                receipt["request_id"] = str(body.get("request_id", "")) if isinstance(body, dict) else ""
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "")
                    if urlsplit(location).scheme in {"http", "https"}:
                        receipt["redirect_url"] = redact(location, request)
                    receipt["ok"] = True
                    rows = [{"redirect_url": receipt.get("redirect_url", "")}]
                else:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ValueError(f"TikHub HTTP {response.status_code}，已停止；未自动重试")
                    if isinstance(body, dict) and body.get("code", 200) not in (200, "200", 0, "0"):
                        raise ValueError("接口返回业务错误，详见本次响应；已停止后续请求")
                    if isinstance(body, dict) and body.get("success") is False:
                        raise ValueError("接口返回 success=false，已停止后续请求")
                    rows = records(body)
                    receipt["ok"] = True
                all_rows.extend(redact(rows, request))
                finish_statistics = request.get("_statistics_queue_child") is True and op["path"] == APP + "fetch_video_statistics"
                if request.get("import_results") is True and (not cancelled() or finish_statistics) and not receipt.get("redirect_url"):
                    try:
                        counts = import_response(op, redact(body, request), params, request.get("job_id", ""))
                        for key, value in counts.items():
                            imported[key] = imported.get(key, 0) + value
                        receipt["imported"] = counts
                    except Exception:
                        errors.append({"batch": batch + 1, "page": page + 1, "error": "响应已保存，写入本机内容库失败；未重发接口请求"})
                new_cursor = next_cursor(body, cursor, params.get(cursor))
                receipt["next_cursor"] = new_cursor
                store.x("UPDATE playback_requests SET status=?,request_id=? WHERE id=?",
                        ("cancelled" if cancelled() else "completed", receipt["request_id"], rid))
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                message = "网络请求失败；可能已经计费，请核对请求记录；未自动重试" if isinstance(exc, requests.RequestException) else str(exc)[:200]
                message = redact(message, request)
                receipt["error"] = message
                errors.append({"batch": batch + 1, "page": page + 1, "error": message})
                store.x("UPDATE playback_requests SET status='failed',message=?,request_id=? WHERE id=?", (message, receipt.get("request_id", ""), rid))
                halted, new_cursor = True, None
            receipt["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            receipts.append(receipt)
            progress({"done": calls, "total": preview["calls"], "count": len(all_rows)})
            if new_cursor is None or str(new_cursor) in seen:
                break
            seen.add(str(new_cursor))
            definition = next(p for p in op["parameters"] if p["name"] == cursor)
            try:
                params[cursor] = coerce(new_cursor, resolve(definition["schema"]), cursor)
            except ValueError:
                errors.append({"batch": batch + 1, "page": page + 1, "error": "下一页游标不符合接口参数类型，已停止翻页"})
                halted = True
                break
        if cancelled() or halted:
            break
    return {"ok": any(r["ok"] for r in receipts), "data": all_rows, "errors": errors,
            "receipts": receipts, "operation": brief(op), "attempted": calls, "requested": preview["calls"],
            "imported": imported, "cancelled": cancelled(),
            "message": f"完成 {sum(r['ok'] for r in receipts)}/{calls} 次请求，返回 {len(all_rows)} 条记录"}


def public_result(value):
    """Keep raw JSON exact while making 64-bit IDs safe in browser tables."""
    def safe(node):
        if isinstance(node, dict):
            return {k: safe(v) for k, v in node.items()}
        if isinstance(node, list):
            return [safe(v) for v in node]
        if isinstance(node, int) and abs(node) > 2**53 - 1:
            return str(node)
        return node
    result = safe(value)
    result["response_json"] = json.dumps(value.get("receipts", []), ensure_ascii=False, indent=2)
    return result
