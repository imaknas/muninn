#!/usr/bin/env python3
"""After a deploy: ask the running server to check its own dependencies
(/api/smoke, with the ingest key), and make sure the app it serves is the
one just deployed (the service worker's cache name matches the repo's).

    bin/smoke.py <runtime dir> <port> <repo dir>

Exit 0 when everything passes; 1 otherwise, with each failing check printed.
Standard library only.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path


def get(url: str, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def shell_version(text: str) -> str | None:
    m = re.search(r"muninn-shell-v\d+", text)
    return m.group(0) if m else None


def main(runtime: Path, port: int, repo: Path) -> int:
    base = f"http://127.0.0.1:{port}"
    token = (runtime / "data" / "secrets" / "ingest_token").read_text().strip()
    failed = []
    status, body = get(f"{base}/api/smoke", token)
    if status != 200:
        failed.append(f"/api/smoke answered {status}")
    else:
        for c in json.loads(body)["checks"]:
            print(f"  {'ok ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
            if not c["ok"]:
                failed.append(c["name"])
    for path in ("/", "/static/app.js"):
        status, _ = get(base + path)
        if status != 200:
            failed.append(f"{path} answered {status}")
    status, live = get(f"{base}/sw.js")
    want = shell_version((repo / "src/muninn/adapters/web/static/sw.js").read_text())
    if status != 200 or shell_version(live) != want:
        failed.append(f"served app is {shell_version(live)}, the repo has {want}")
    else:
        print(f"  ok  app shell: {want}")
    for f in failed:
        print(f"smoke: {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])))
