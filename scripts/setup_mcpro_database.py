"""Project-local MariaDB runtime required by the upstream Remix persistence layer."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "mcpro"
VERSION = "11.4.13"
NAME = f"mariadb-{VERSION}-winx64"
URL = f"https://archive.mariadb.org/mariadb-{VERSION}/winx64-packages/{NAME}.zip"
SHA256 = "d62986d433eeebfde218560b276103831604a61e929e87f1a17f5aebd80257e2"

if __name__ == "__main__":
    RUNTIME.mkdir(parents=True, exist_ok=True)
    package = RUNTIME / (NAME + ".zip")
    if not package.exists():
        print("Downloading verified MariaDB runtime", flush=True)
        with requests.get(URL, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with package.with_suffix(".part").open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
        package.with_suffix(".part").replace(package)
    digest = hashlib.file_digest(package.open("rb"), "sha256").hexdigest()
    if digest != SHA256:
        raise SystemExit("MariaDB archive checksum mismatch")
    base = RUNTIME / NAME
    if not (base / "bin" / "mariadbd.exe").exists():
        with zipfile.ZipFile(package) as archive:
            for entry in archive.infolist():
                target = (RUNTIME / entry.filename).resolve()
                if not target.is_relative_to(RUNTIME.resolve()):
                    raise SystemExit("Archive path outside runtime directory")
            archive.extractall(RUNTIME)
    config_file = RUNTIME / "database.json"
    cfg = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {
        "host": "127.0.0.1", "port": 13306, "user": "root", "password": secrets.token_urlsafe(32),
        "data_dir": str(RUNTIME / "mysql-data"), "base": str(base), "version": VERSION,
        "source": URL, "sha256": SHA256}
    config_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if not (Path(cfg["data_dir"]) / "mysql").exists():
        installer = base / "bin" / "mariadb-install-db.exe"
        with (RUNTIME / "database-init.log").open("w", encoding="utf-8") as log:
            subprocess.run([str(installer), "--datadir="+cfg["data_dir"],
                "--password="+cfg["password"], "--port="+str(cfg["port"])],
                cwd=base / "bin", check=True, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("MariaDB runtime ready; bound address configured by the local service manager", flush=True)
