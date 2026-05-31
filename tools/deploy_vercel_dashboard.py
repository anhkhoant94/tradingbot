#!/usr/bin/env python
"""Deploy the static dashboard directory to Vercel via REST API.

Required:
  VERCEL_TOKEN

Optional:
  VERCEL_PROJECT (default: stock-screening-dashboard)
  VERCEL_TEAM_SLUG or VERCEL_TEAM_ID
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = "https://api.vercel.com"
TEXT_EXTS = {".html", ".css", ".js", ".json", ".txt", ".md", ".svg"}


def request_json(method: str, path: str, token: str, body: dict | None = None, query: dict | None = None) -> dict:
    qs = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v})
    url = f"{API_BASE}{path}" + (f"?{qs}" if qs else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "stock-screening-dashboard-deployer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vercel API {method} {path} failed: HTTP {exc.code} {raw}") from exc


def collect_files(root: Path) -> list[dict]:
    files: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_EXTS:
            raise RuntimeError(f"Unexpected non-text dashboard file: {rel}")
        data = path.read_bytes()
        if b"\0" in data:
            raise RuntimeError(f"Refusing to deploy dashboard file containing NUL bytes: {rel}")
        files.append(
            {
                "file": rel,
                "data": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            }
        )
    if not any(f["file"] == "index.html" for f in files):
        raise RuntimeError("dashboard/index.html not found")
    return files


def host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc or parsed.path or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy dashboard to Vercel")
    parser.add_argument("--dashboard-dir", default="dashboard")
    parser.add_argument("--project", default=os.environ.get("VERCEL_PROJECT", "stock-screening-dashboard"))
    parser.add_argument("--team-slug", default=os.environ.get("VERCEL_TEAM_SLUG"))
    parser.add_argument("--team-id", default=os.environ.get("VERCEL_TEAM_ID"))
    parser.add_argument("--target", default="production", choices=["production", "preview"])
    parser.add_argument("--wait", action="store_true", help="wait until deployment is READY or ERROR")
    args = parser.parse_args()

    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        print("Missing VERCEL_TOKEN", file=sys.stderr)
        return 2

    root = Path(args.dashboard_dir).resolve()
    files = collect_files(root)
    query = {
        "forceNew": "1",
        "skipAutoDetectionConfirmation": "1",
        "slug": args.team_slug,
        "teamId": args.team_id,
    }
    payload = {
        "name": args.project,
        "project": args.project,
        "target": args.target,
        "files": files,
        "projectSettings": {
            "framework": None,
            "buildCommand": None,
            "devCommand": None,
            "installCommand": None,
            "outputDirectory": ".",
        },
        "meta": {
            "source": "codex-dashboard-api",
            "dashboardDir": root.name,
        },
    }

    created = request_json("POST", "/v13/deployments", token, payload, query)
    deploy_id = created.get("id")
    url = created.get("url")
    aliases = created.get("alias") or []
    state = created.get("readyState") or created.get("status")

    if args.wait and deploy_id:
        for _ in range(90):
            current = request_json("GET", f"/v13/deployments/{deploy_id}", token, query=query)
            url = current.get("url", url)
            aliases = current.get("alias") or aliases
            state = current.get("readyState") or current.get("status")
            if state in {"READY", "ERROR", "CANCELED"}:
                break
            time.sleep(2)

    public_url = f"https://{aliases[0]}" if aliases else (f"https://{url}" if url else None)
    public_host = host_from_url(os.environ.get("VERCEL_PUBLIC_URL"))
    if public_host and deploy_id:
        try:
            request_json(
                "POST",
                f"/v2/deployments/{deploy_id}/aliases",
                token,
                {"alias": public_host, "redirect": None},
                query,
            )
            if public_host not in aliases:
                aliases.insert(0, public_host)
            public_url = f"https://{public_host}"
        except RuntimeError as exc:
            print(f"Alias assignment warning: {exc}", file=sys.stderr)
    print(
        json.dumps(
            {
                "id": deploy_id,
                "state": state,
                "url": public_url,
                "deployment_url": f"https://{url}" if url else None,
                "aliases": [f"https://{x}" for x in aliases],
            },
            ensure_ascii=False,
        )
    )
    return 0 if state != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
