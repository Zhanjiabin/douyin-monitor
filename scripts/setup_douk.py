"""Install the pinned, vendored engine into its own environment (Python >= 3.12)."""
from pathlib import Path
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info < (3, 12):
    raise SystemExit("TikTokDownloader 5.8 需要 Python 3.12 或以上版本")
environment = ROOT / ".douk-venv"
venv.EnvBuilder(with_pip=True).create(environment)
python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "vendor/TikTokDownloader/requirements.txt")], check=True)
(ROOT / "integrations").mkdir(exist_ok=True)
subprocess.run([str(python), "-X", "utf8", str(ROOT / "scripts/douk_worker.py"), "catalog", str(ROOT / "integrations/douk_catalog.json")], check=True)
print("引擎安装完成，启动主程序后打开 /studio")
