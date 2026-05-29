#!/usr/bin/env python
"""Lightweight public health check for the deployed dashboard."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import urllib.request


DEFAULT_BASE_URL = "https://trading-execution-desk-khoa.vercel.app"


def fetch(base_url: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"User-Agent": "codex-dashboard-health/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--require-fresh-live",
        action="store_true",
        help="exit non-zero when dashboard live quote status is not from today",
    )
    parser.add_argument(
        "--require-vni-history",
        action="store_true",
        help="exit non-zero when history.js has no numeric VN-Index points for the performance chart",
    )
    args = parser.parse_args()

    idx_status, _ = fetch(args.url, "/")
    css_status, css = fetch(args.url, "/styles.css")
    ana_status, analysis = fetch(args.url, "/analysis.js")
    data_status, data_js = fetch(args.url, "/data.js")
    hist_status, history = fetch(args.url, "/history.js")
    live_status, live_raw = fetch(args.url, "/dashboard_live_update_status.json")

    today = dt.date.today().isoformat()
    as_of_match = re.search(r'"as_of"\s*:\s*"(\d{4}-\d{2}-\d{2})"', data_js)
    live_payload = json.loads(live_raw)
    live_updated_at = str(live_payload.get("updatedAt") or "")
    live_latest_price_date = str(live_payload.get("latestPriceDate") or "")
    payload = {
        "base_url": args.url,
        "index_status": idx_status,
        "css_status": css_status,
        "analysis_status": ana_status,
        "data_status": data_status,
        "history_status": hist_status,
        "live_status": live_status,
        "data_as_of": as_of_match.group(1) if as_of_match else None,
        "live_updated_at": live_updated_at,
        "live_latest_price_date": live_latest_price_date,
        "live_is_today": live_updated_at.startswith(today) or live_latest_price_date == today,
        "vni_history_points": len(re.findall(r'"vniClose"\s*:\s*[0-9]', history)),
        "has_r46_key": "r46_bear_stop_mcore" in analysis,
        "has_r23_key": "r23_nav3b_mcore" in analysis,
        "has_hide_planned_orders_rule": ".planned-orders[hidden]" in css,
    }
    print(json.dumps(payload, ensure_ascii=False))
    if args.require_fresh_live and not payload["live_is_today"]:
        raise SystemExit(1)
    if args.require_vni_history and payload["vni_history_points"] <= 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
