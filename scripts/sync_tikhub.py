"""Pin the official TikHub OpenAPI catalog; never needs or sends an API key."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://api.tikhub.io/openapi.json"


def sync(source=None):
    if source:
        raw = Path(source).read_bytes()
    else:
        response = requests.get(URL, timeout=(10, 60), allow_redirects=False)
        response.raise_for_status()
        raw = response.content
    spec = json.loads(raw)
    if not str(spec.get("openapi", "")).startswith("3."):
        raise ValueError("Unsupported OpenAPI version")
    operations = []
    for path, item in spec["paths"].items():
        if not path.startswith("/api/") or ".." in path or "?" in path:
            raise ValueError("Invalid API path")
        for method, op in item.items():
            if method not in ("get", "post"):
                raise ValueError("Review new HTTP methods before updating")
            operations.append(op["operationId"])
    if len(operations) != len(set(operations)):
        raise ValueError("Duplicate operation IDs")

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value and not value["$ref"].startswith("#/"):
                raise ValueError("External schema references are not allowed")
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs(spec)
    target = ROOT / "integrations"
    target.mkdir(exist_ok=True)
    # Keep the original bytes so the upstream digest is independently verifiable.
    temporary = target / "tikhub.openapi.tmp"
    temporary.write_bytes(raw)
    temporary.replace(target / "tikhub.openapi.json")
    lock = {"source": URL, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "version": spec["info"]["version"], "sha256": hashlib.sha256(raw).hexdigest(),
            "operations": len(operations), "groups": len({tag for item in spec["paths"].values()
                                                           for op in item.values() for tag in op.get("tags", [])}),
            "app_v3_operations": sum("Douyin-App-V3-API" in op.get("tags", [])
                                     for item in spec["paths"].values() for op in item.values())}
    (target / "tikhub.lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lock


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Use an already downloaded official spec")
    print(json.dumps(sync(parser.parse_args().source), ensure_ascii=False))
