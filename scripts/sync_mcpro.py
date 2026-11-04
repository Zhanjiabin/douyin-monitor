"""Fetch the authorized upstream organization, excluding Go, without exposing credentials."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vendor" / "MediaCrawlerPro"
ENV = {**os.environ, "GH_CONFIG_DIR": str(ROOT / "data" / "github-auth"),
       "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, env=ENV, check=True, text=True,
                          encoding="utf-8", **kwargs)


if __name__ == "__main__":
    repos = json.loads(run(["gh", "api", "orgs/MediaCrawlerPro/repos?per_page=100"],
                          capture_output=True).stdout)
    DEST.mkdir(parents=True, exist_ok=True)
    records = []
    for repo in repos:
        name = repo["name"]
        if repo.get("language") == "Go" or "golang" in name.lower():
            print("Excluded:", name, flush=True)
            continue
        target = DEST / name
        if not target.exists():
            run(["gh", "repo", "clone", repo["full_name"], str(target), "--", "--depth", "1"])
        sha = run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True).stdout.strip()
        records.append({"name": name, "repository": repo["html_url"], "commit": sha,
                        "source": str(target.relative_to(ROOT)).replace("\\", "/"),
                        "private": repo["private"]})
        print("Fetched:", name, sha[:12], flush=True)
    (ROOT / "integrations" / "mcpro.lock.json").write_text(
        json.dumps({"excluded": ["MediaCrawlerPro-Golang"], "projects": records},
                   ensure_ascii=False, indent=2), encoding="utf-8")
