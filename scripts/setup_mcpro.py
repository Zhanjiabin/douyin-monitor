"""Install isolated upstream runtimes. Does not generate media or register global CLIs."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "MediaCrawlerPro"
JOBS = [
    ("mcpro-sign", "MediaCrawlerPro-SignSrv"),
    ("mcpro-python", "MediaCrawlerPro-Python"),
    ("mcpro-cookie", "MediaCrawlerPro-CookieBridge/server"),
    ("mcpro-downloader", "MediaCrawlerPro-Downloader/DownloadServer"),
    ("mcpro-remix", "MediaCrawlerPro-ContentRemixAgent/backend"),
    ("pictactic", "PicTacticAgent"),
    ("mcpro-vox", "MediaCrawlerPro-VoxAgent"),
]


if __name__ == "__main__":
    report = {}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "UV_PYTHON": "3.12"}
    logroot = ROOT / "output" / "integration-setup"
    logroot.mkdir(parents=True, exist_ok=True)
    for key, folder in JOBS:
        target = VENDOR / folder
        print("Installing", key, flush=True)
        start = time.monotonic()
        with (logroot / (key + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(["uv", "sync", "--no-dev", "--python", "3.12"],
                                    cwd=target, env=env, stdout=log, stderr=log)
        report[key] = {"installed": result.returncode == 0,
                       "seconds": round(time.monotonic() - start, 1), "exit_code": result.returncode}
        (logroot / "status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(key, report[key], flush=True)
