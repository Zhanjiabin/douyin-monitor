"""Small daily backups of database snapshots, task history and private settings."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BACKUPS=ROOT/'data/server-backups'


def backup():
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    if shutil.disk_usage(ROOT).free < 32*1024**2:
        raise RuntimeError('磁盘空间不足，未创建备份')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    target=BACKUPS/('guanchao-'+stamp+'.tar.gz')
    pending=target.with_suffix('.pending')
    try:
        with tempfile.TemporaryDirectory(prefix='snapshot-',dir=BACKUPS) as tmp,tarfile.open(pending,'w:gz') as archive:
            for name in ['data/douyin.db','data/mcpro/crawler.db','data/mcpro/vox-review.db','data/douk/DouK-Downloader.db']:
                source=ROOT/name
                if not source.is_file():continue
                snapshot=Path(tmp)/source.name
                with sqlite3.connect(source) as src,sqlite3.connect(snapshot) as dst:src.backup(dst)
                archive.add(snapshot,arcname=name)
            for name in ['config.json','data/playback/settings.json','data/douk/settings.json','data/auth/settings.json','data/mcpro/app-secrets.json','data/mcpro/manual-logins.json','data/mcpro/database.json']:
                if (ROOT/name).is_file():archive.add(ROOT/name,arcname=name)
            for folder in ['data/douk/jobs','data/mcpro/server-migration']:
                if (ROOT/folder).is_dir():archive.add(ROOT/folder,arcname=folder)
        os.chmod(pending,0o600)
        pending.replace(target)
        for old in sorted(BACKUPS.glob('guanchao-????????-??????.tar.gz'))[:-7]:
            if old.resolve().parent==BACKUPS.resolve():old.unlink()
        print('Backup ready:',target.name,target.stat().st_size,'bytes')
    except Exception:
        if pending.is_file():pending.unlink()
        raise


if __name__=='__main__':
    backup()
