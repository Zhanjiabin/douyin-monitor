"""Serve built upstream web apps with their native API paths on loopback only."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import sys
from urllib.parse import urlsplit

import requests
import json

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    "remix": ("MediaCrawlerPro-ContentRemixAgent/frontend/dist", 5373, 8001),
    "pictactic": ("PicTacticAgent/frontend/dist", 3019, 8019),
    "vox": ("MediaCrawlerPro-VoxAgent/apps/workbench/dist", 4319, 4318),
}
folder, port, backend = SERVICES[sys.argv[1]]
DIST = ROOT / "vendor" / "MediaCrawlerPro" / folder


def integrate_html(body):
    extra=(f'<script>document.documentElement.dataset.gcEngine="{sys.argv[1]}";</script>'
        '<link rel="stylesheet" href="/integration-skin.css"><script defer src="/integration-context.js"></script>')
    return body.replace(b'</head>',extra.encode('utf-8')+b'</head>')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, *args):
        pass

    def proxy(self):
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return self.send_error(403, "Unsupported origin")
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self.websocket()
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in
                       {"host", "connection", "content-length", "accept-encoding"}}
            if origin:
                headers["Origin"] = f"http://127.0.0.1:{backend}"
            with requests.request(self.command, f"http://127.0.0.1:{backend}"+self.path,
                    headers=headers, data=body, stream=True, timeout=(10, 3600), allow_redirects=False) as response:
                self.send_response(response.status_code)
                for k, v in response.headers.items():
                    if k.lower() not in {"connection", "transfer-encoding", "content-encoding", "content-length", "x-frame-options"}:
                        self.send_header(k, v)
                self.send_header("Content-Security-Policy", "frame-ancestors 'self' http://127.0.0.1:8795 http://localhost:8795")
                self.end_headers()
                if self.command=='GET' and 'text/html' in response.headers.get('Content-Type',''):
                    self.wfile.write(integrate_html(response.content))
                    return
                chunk_size = 1 if 'text/event-stream' in response.headers.get('Content-Type','') else 65536
                for chunk in response.iter_content(chunk_size):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except requests.RequestException:
            self.send_error(502, "Local upstream is unavailable")

    def websocket(self):
        with socket.create_connection(("127.0.0.1", backend), timeout=10) as upstream:
            request = f"GET {self.path} HTTP/1.1\r\nHost: 127.0.0.1:{backend}\r\n"
            request += "".join(f"{k}: {v}\r\n" for k, v in self.headers.items() if k.lower() != "host") + "\r\n"
            upstream.sendall(request.encode("latin-1"))
            sockets = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(sockets, [], [], 60)
                if not ready:
                    continue
                for peer in ready:
                    chunk = peer.recv(65536)
                    if not chunk:
                        return
                    (upstream if peer is self.connection else self.connection).sendall(chunk)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in {'/integration-skin.css','/integration-context.js'}:
            target=ROOT/'src/webui'/path.lstrip('/')
            body=target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/css' if path.endswith('.css') else 'application/javascript')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return self.wfile.write(body)
        if sys.argv[1] == "vox":
            return self.proxy()
        if path == "/integration-bootstrap":
            sys.path.insert(0, str(ROOT / "src"))
            from integrations import local_session
            try:
                session = local_session(sys.argv[1])
                prefix = "remix" if sys.argv[1] == "remix" else "pictactic"
                payload = json.dumps(session, ensure_ascii=False).replace("<", "\\u003c")
                html = f'''<!doctype html><meta charset="utf-8"><script>
const s={payload};localStorage.setItem('{prefix}_access_token',s.access_token);
localStorage.setItem('{prefix}_refresh_token',s.refresh_token);
if(s.user)localStorage.setItem('{prefix}_user',JSON.stringify(s.user));
location.replace('/');</script>'''.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            except (ValueError, requests.RequestException):
                return self.send_error(502, "Local workspace session initialization failed")
        target = (DIST / path.lstrip("/")).resolve()
        if path.startswith(("/api/", "/ws", "/media/", "/static/")):
            return self.proxy()
        if path.startswith("/assets/") and not target.is_file():
            return self.proxy()
        if not target.is_relative_to(DIST.resolve()):
            return self.send_error(404)
        if not target.exists() and not Path(path).suffix:
            self.path = "/index.html"
        if path in {'/','/index.html'} or self.path=='/index.html':
            body=integrate_html((DIST/'index.html').read_bytes())
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return self.wfile.write(body)
        return super().do_GET()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = proxy


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
