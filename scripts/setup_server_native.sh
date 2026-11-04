set -euo pipefail
cd /opt/douyin-monitor
export UV_PYTHON_INSTALL_DIR=/opt/douyin-monitor/data/native-runtime/python
export UV_PYTHON_BIN_DIR=/opt/douyin-monitor/data/native-runtime/bin
export UV_CACHE_DIR=/opt/douyin-monitor/data/native-runtime/cache
mkdir -p data/native-runtime
if [ ! -x data/native-runtime/tools/bin/uv ]; then
    python3 -m venv data/native-runtime/tools
    data/native-runtime/tools/bin/pip install --disable-pip-version-check --only-binary=:all: uv==0.13.7
fi
./.venv/bin/python -u - <<'PY'
import hashlib,json,urllib.request,subprocess
from pathlib import Path
base=Path('/opt/douyin-monitor/data/native-runtime')
if not (base/'node/bin/node').is_file():
    releases=json.load(urllib.request.urlopen('https://nodejs.org/dist/index.json',timeout=40))
    version=next(r['version'] for r in releases if r['version'].startswith('v22.') and r['lts'])
    name=f'node-{version}-linux-x64.tar.xz'
    url='https://nodejs.org/dist/'+version+'/'
    checksum=urllib.request.urlopen(url+'SHASUMS256.txt',timeout=40).read().decode()
    digest=next(line.split()[0] for line in checksum.splitlines() if line.split()[-1]==name)
    path=base/name
    with urllib.request.urlopen(url+name,timeout=90) as response,path.open('wb') as out:
        while block:=response.read(1024*1024):out.write(block)
    if hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=digest:raise RuntimeError('Node checksum mismatch')
    subprocess.run(['tar','-xJf',str(path),'--no-same-owner','-C',str(base)],check=True)
    (base/'node').symlink_to('node-'+version+'-linux-x64',target_is_directory=True)
    path.unlink()
    print('Installed Node',version,flush=True)
PY
data/native-runtime/tools/bin/uv python install 3.12
for folder in MediaCrawlerPro-SignSrv MediaCrawlerPro-CookieBridge/server MediaCrawlerPro-Downloader/DownloadServer; do
    printf 'Installing native runtime: %s\n' "$folder"
    (cd "vendor/MediaCrawlerPro/$folder" && /opt/douyin-monitor/data/native-runtime/tools/bin/uv sync --frozen --no-dev --python 3.12)
done
chown -R douyin-monitor:douyin-monitor data/native-runtime vendor/MediaCrawlerPro/MediaCrawlerPro-SignSrv/.venv vendor/MediaCrawlerPro/MediaCrawlerPro-CookieBridge/server/.venv vendor/MediaCrawlerPro/MediaCrawlerPro-Downloader/DownloadServer/.venv
data/native-runtime/node/bin/node --version
df -h /
