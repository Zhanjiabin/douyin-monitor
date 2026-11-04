"""Launch upstream engines in their own interpreters without importing them into the monitor."""
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "MediaCrawlerPro"
RUNTIME = ROOT / "data" / "mcpro"
kind = sys.argv[1]


def enter(folder):
    target = VENDOR / folder
    os.chdir(target)
    sys.path.insert(0, str(target))
    return target


if kind in {"sign", "cookie", "downloader"}:
    folder = {"sign": "MediaCrawlerPro-SignSrv", "cookie": "MediaCrawlerPro-CookieBridge/server",
              "downloader": "MediaCrawlerPro-Downloader/DownloadServer"}[kind]
    enter(folder)
    import config
    config.IS_DEBUG = False
    import tornado.autoreload
    tornado.autoreload.start = lambda *args, **kwargs: None
    if kind == "cookie":
        # Upstream manual POST stores cookies but does not register the account.
        # Its crawler enumerates the registry, so keep both native stores consistent.
        from apis.cookies_handler import SetCookiesHandler
        from logic.ws_bridge import cookie_store, client_registry
        from apis.base_handler import BaseRequestHandler
        from logic.ws_bridge import ExtensionWebSocketHandler
        import re
        import tornado.web
        def allowed_origin(origin):
            return not origin or bool(re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):(?:8274|8795|4318|4319|5373)',origin)
                or re.fullmatch(r'chrome-extension://[a-p]{32}',origin))
        original_prepare=BaseRequestHandler.prepare
        def local_prepare(self):
            if not allowed_origin(self.request.headers.get('Origin')):
                raise tornado.web.HTTPError(403)
            return original_prepare(self)
        original_headers=BaseRequestHandler.set_default_headers
        def local_headers(self):
            original_headers(self)
            self.clear_header('Access-Control-Allow-Origin')
            origin=self.request.headers.get('Origin')
            if origin and allowed_origin(origin):
                self.set_header('Access-Control-Allow-Origin',origin)
        BaseRequestHandler.prepare=local_prepare
        BaseRequestHandler.set_default_headers=local_headers
        ExtensionWebSocketHandler.check_origin=lambda self,origin:allowed_origin(origin)
        original_post = SetCookiesHandler.post
        async def integrated_post(self, platform):
            await original_post(self, platform)
            client_id = self.request_data.get("client_id", "manual")
            entry = cookie_store.get(client_id, platform)
            if entry and entry.get("cookies") == self.request_data.get("cookies"):
                previous = client_registry.get(client_id) or {}
                platforms = {**previous.get("platforms", {}), platform: {"has_cookies": True}}
                client_registry.register(client_id, platforms, previous.get("nicknames", {}))
                if client_id == "observatory":
                    client_registry.set_connected(client_id, False)
        SetCookiesHandler.post = integrated_post
    import app
    if kind == "sign":
        # Send JSON on stdin instead of a command argument: portable across
        # Windows Node launchers, including URLs containing &, quotes and Unicode.
        from logic.douyin.douyin_web_sign import DouyinWebSignLogic, SECSDK_SIGN_SCRIPT
        async def run_secsdk_stdin(self, payload, req):
            async with self._semaphore:
                proc = await asyncio.create_subprocess_exec(os.environ.get("NODE_BIN","node"),str(SECSDK_SIGN_SCRIPT),
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                try:
                    out,err = await asyncio.wait_for(proc.communicate(payload.encode('utf-8')),timeout=30)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise RuntimeError("SecSDK signing timed out")
            if proc.returncode:
                raise RuntimeError("SecSDK Node runtime failed; inspect the local engine log")
            result=json.loads(out.decode('utf-8'))
            if result.get('error') or not result.get('url'):
                raise RuntimeError("SecSDK did not return a signed URL")
            return result['url'],result.get('headers') or {}
        DouyinWebSignLogic._run_secsdk = run_secsdk_stdin
    asyncio.run(app.create_app())
elif kind == "remix":
    enter("MediaCrawlerPro-ContentRemixAgent/backend")
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8001, log_level="warning")
elif kind == "pictactic":
    target = enter("PicTacticAgent")
    sys.path.insert(0, str(target / "backend" / "src"))
    import uvicorn
    uvicorn.run("pictactic.api.app:app", host="127.0.0.1", port=8019, log_level="warning")
elif kind == "crawl":
    target = enter("MediaCrawlerPro-Python")
    request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    import config
    config.USE_CACHE_TYPE = "memory"
    config.ENABLE_IP_PROXY = False
    config.ENABLE_LOG_FILE = False
    config.CRAWLER_TIME_SLEEP = max(1, int(request.get("interval", 2)))
    sys.argv = [str(target / "main.py"), *request["arguments"]]
    import main
    import db
    if config.PLATFORM == 'dy' or '--platform' in request['arguments']:
        from media_platform.douyin.client import DouYinApiClient
        if request.get('discovery'):
            from native_discovery_hook import install
            from media_platform.douyin.field import SearchSortType,PublishTimeType,SearchChannelType
            from media_platform.douyin.handlers.hot_search_handler import HotSearchHandler
            install(DouYinApiClient,request['discovery'],request['capture_path'],SearchSortType,PublishTimeType,HotSearchHandler,SearchChannelType.VIDEO)
        # Douyin can return a full page even for a small requested count. Clamp
        # the returned page before processing / persistence, not after saving it.
        original_comments=DouYinApiClient.get_aweme_comments
        comment_counts={}
        async def bounded_comments(self, aweme_id, *args, **kwargs):
            limit=max(0,int(os.environ.get('PER_NOTE_MAX_COMMENTS_COUNT','20')))
            remaining=max(0,limit-comment_counts.get(aweme_id,0))
            if not remaining:
                return [],{'has_more':0,'cursor':0}
            rows,response=await original_comments(self,aweme_id,*args,**kwargs)
            rows=rows[:remaining]
            comment_counts[aweme_id]=comment_counts.get(aweme_id,0)+len(rows)
            if comment_counts[aweme_id]>=limit:
                response={**response,'has_more':0}
            return rows,response
        DouYinApiClient.get_aweme_comments=bounded_comments
    async def run_crawler():
        try:
            await main.main()
        finally:
            # async_initialize can fail before upstream reaches its normal close().
            # Close its SQLite worker so a failed task actually exits.
            await db.close()
    asyncio.run(run_crawler())
else:
    raise SystemExit("Unsupported upstream runtime")
