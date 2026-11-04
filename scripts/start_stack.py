"""Start the installed local suite, reusing existing healthy services."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import app
import integrations


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if (integrations.VENDOR/'MediaCrawlerPro-Python/.venv').is_dir():
        for service in integrations.start_all():
            print(service['service']+': '+service['status']+(' · '+service.get('message','') if service.get('message') else ''),flush=True)
    app.serve(open_browser=not args.no_browser)
