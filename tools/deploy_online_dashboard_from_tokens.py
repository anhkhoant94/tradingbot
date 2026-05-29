#!/usr/bin/env python
"""Push dashboard fixes and deploy the online dashboard without git/gh/vercel CLI.

This helper exists for the Codex desktop workspace, where the current folder may
not be a git checkout and CLI credentials may not be available.

Secrets are read from:
  %USERPROFILE%/.cache/stock_screening_deploy_secrets.json

Example secret JSON:
{
  "github_token": "...",
  "vercel_token": "...",
  "repo": "anhkhoant94/tradingbot",
  "branch": "main",
  "vercel_project": "trading-execution-desk-khoa",
  "vercel_public_url": "https://trading-execution-desk-khoa.vercel.app"
}
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATH = Path.home() / ".cache" / "stock_screening_deploy_secrets.json"

FILES_TO_PUSH = [
    ".github/workflows/dashboard-auto-refresh.yml",
    "dashboard/app.js",
    "dashboard/index.html",
    "tools/check_dashboard_public_health.py",
    "tools/deploy_online_dashboard_from_tokens.py",
    "ONLINE_AUTO_REFRESH_SETUP.md",
]


def load_secrets() -> dict:
    if not SECRET_PATH.exists():
        raise SystemExit(f"Missing secrets file: {SECRET_PATH}")
    data = json.loads(SECRET_PATH.read_text(encoding="utf-8-sig"))
    data.setdefault("repo", "anhkhoant94/tradingbot")
    data.setdefault("branch", "main")
    data.setdefault("vercel_project", "trading-execution-desk-khoa")
    data.setdefault("vercel_public_url", "https://trading-execution-desk-khoa.vercel.app")
    missing = [k for k in ["github_token", "vercel_token"] if not data.get(k)]
    if missing:
        raise SystemExit(f"Missing required secret keys: {', '.join(missing)}")
    return data


def github_json(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = "https://api.github.com" + path
    raw = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=raw,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "stock-screening-dashboard-deployer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} failed: HTTP {exc.code} {text}") from exc


def github_file_sha(repo: str, branch: str, rel_path: str, token: str) -> str | None:
    encoded = urllib.parse.quote(rel_path, safe="")
    try:
        payload = github_json("GET", f"/repos/{repo}/contents/{encoded}?ref={branch}", token)
        return payload.get("sha")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def push_file(repo: str, branch: str, rel_path: str, token: str) -> None:
    path = ROOT / rel_path
    content = path.read_bytes()
    sha = github_file_sha(repo, branch, rel_path, token)
    encoded = urllib.parse.quote(rel_path, safe="")
    body = {
        "message": f"Update dashboard auto-refresh: {rel_path}",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    github_json("PUT", f"/repos/{repo}/contents/{encoded}", token, body)
    print(f"pushed {rel_path}")


def run_step(args: list[str], env: dict | None = None) -> None:
    print("+ " + " ".join(args))
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def vercel_json(method: str, path: str, secrets: dict, body: dict | None = None) -> dict:
    query = {
        "slug": secrets.get("vercel_team_slug"),
        "teamId": secrets.get("vercel_team_id"),
    }
    qs = urllib.parse.urlencode({k: v for k, v in query.items() if v})
    url = "https://api.vercel.com" + path + (f"?{qs}" if qs else "")
    raw = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=raw,
        method=method,
        headers={
            "Authorization": f"Bearer {secrets['vercel_token']}",
            "Content-Type": "application/json",
            "User-Agent": "stock-screening-dashboard-deployer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vercel API {method} {path} failed: HTTP {exc.code} {text}") from exc


def build_dashboard() -> None:
    run_step([sys.executable, "update_dashboard_live_data.py"])
    run_step([sys.executable, "generate_deep_analysis.py"])
    run_step([sys.executable, "generate_model_history.py"])
    run_step([sys.executable, "generate_dashboard_data.py"])


def deploy_vercel(secrets: dict) -> None:
    env = os.environ.copy()
    env["VERCEL_TOKEN"] = secrets["vercel_token"]
    env["VERCEL_PROJECT"] = secrets["vercel_project"]
    if secrets.get("vercel_team_slug"):
        env["VERCEL_TEAM_SLUG"] = secrets["vercel_team_slug"]
    if secrets.get("vercel_team_id"):
        env["VERCEL_TEAM_ID"] = secrets["vercel_team_id"]
    args = [
        sys.executable,
        "tools/deploy_vercel_dashboard.py",
        "--dashboard-dir",
        "dashboard",
        "--project",
        secrets["vercel_project"],
        "--target",
        "production",
        "--wait",
    ]
    print("+ " + " ".join(args))
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    deployment = json.loads(lines[-1]) if lines else {}
    deploy_id = deployment.get("id")
    public_host = urllib.parse.urlparse(secrets["vercel_public_url"]).netloc
    if deploy_id and public_host:
        vercel_json(
            "POST",
            f"/v2/deployments/{deploy_id}/aliases",
            secrets,
            {"alias": public_host, "redirect": None},
        )
        print(f"assigned alias https://{public_host} -> {deploy_id}")


def verify_public() -> None:
    # Vercel aliases can take a few seconds to settle.
    for attempt in range(1, 7):
        try:
            run_step([sys.executable, "tools/check_dashboard_public_health.py", "--require-fresh-live"])
            return
        except subprocess.CalledProcessError:
            if attempt == 6:
                raise
            time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true", help="push patched files to GitHub")
    parser.add_argument("--build", action="store_true", help="rebuild local dashboard artifacts")
    parser.add_argument("--deploy", action="store_true", help="deploy dashboard/ to Vercel")
    parser.add_argument("--verify", action="store_true", help="verify public dashboard freshness")
    parser.add_argument("--all", action="store_true", help="push, build, deploy, and verify")
    args = parser.parse_args()

    if args.all:
        args.push = args.build = args.deploy = args.verify = True

    secrets = load_secrets()

    if args.push:
        for rel_path in FILES_TO_PUSH:
            push_file(secrets["repo"], secrets["branch"], rel_path, secrets["github_token"])
    if args.build:
        build_dashboard()
    if args.deploy:
        deploy_vercel(secrets)
    if args.verify:
        verify_public()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
