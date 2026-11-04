"""Exec one optional, project-isolated native service under systemd."""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import integrations

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'sign','cookie','downloader'}:
        raise SystemExit('Expected sign, cookie or downloader')
    args,env=integrations.command('mcpro-'+sys.argv[1])
    os.execvpe(args[0],args,env)
