"""Build upstream web apps locally; no global packages or generation calls."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "MediaCrawlerPro"
JOBS = [("remix", "MediaCrawlerPro-ContentRemixAgent/frontend", "build"),
        ("pictactic", "PicTacticAgent/frontend", "build"),
        ("vox", "MediaCrawlerPro-VoxAgent", "build:web")]

if __name__ == "__main__":
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    report = {}
    folder = ROOT / "output" / "integration-setup"
    folder.mkdir(parents=True, exist_ok=True)
    for key, target, build in JOBS:
        project = VENDOR / target
        print("Building", key, flush=True)
        with (folder / (key + "-web.log")).open("w", encoding="utf-8") as log:
            install = subprocess.run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                                     cwd=project, stdout=log, stderr=log)
            result = subprocess.run([npm, "run", build], cwd=project, stdout=log, stderr=log) if install.returncode == 0 else install
        report[key] = {"built": result.returncode == 0, "exit_code": result.returncode}
        (folder / "web-status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(key, report[key], flush=True)
