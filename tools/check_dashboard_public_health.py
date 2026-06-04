#!/usr/bin/env python
"""Lightweight public health check for the deployed dashboard."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import urllib.request


DEFAULT_BASE_URL = "https://ez-trading.vercel.app"


def fetch_bytes(base_url: str, path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"User-Agent": "codex-dashboard-health/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


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
    parser.add_argument(
        "--require-current-forecast",
        action="store_true",
        help="exit non-zero when r46_forecast.json or embedded plannedOrders is not a current COMPUTED forecast",
    )
    args = parser.parse_args()

    idx_status, idx_raw = fetch_bytes(args.url, "/")
    css_status, css_raw = fetch_bytes(args.url, "/styles.css")
    ana_status, analysis_raw = fetch_bytes(args.url, "/analysis.js")
    data_status, data_raw = fetch_bytes(args.url, "/data.js")
    hist_status, history_raw = fetch_bytes(args.url, "/history.js")
    live_status, live_raw = fetch_bytes(args.url, "/dashboard_live_update_status.json")
    forecast_status, forecast_raw = fetch_bytes(args.url, "/r46_forecast.json")
    css = decode(css_raw)
    index = decode(idx_raw)
    analysis = decode(analysis_raw)
    data_js = decode(data_raw)
    history = decode(history_raw)

    today = dt.date.today().isoformat()
    as_of_match = re.search(r'"as_of"\s*:\s*"(\d{4}-\d{2}-\d{2})"', data_js)
    live_payload = json.loads(decode(live_raw))
    forecast_payload = json.loads(decode(forecast_raw))
    live_updated_at = str(live_payload.get("updatedAt") or "")
    live_latest_price_date = str(live_payload.get("latestPriceDate") or "")
    forecast_meta = forecast_payload.get("meta") or {}
    forecast_display_match = re.search(r'"forecastDisplayState"\s*:\s*"([^"]+)"', index)
    embedded_forecast_display_state = forecast_display_match.group(1) if forecast_display_match else None
    forecast_has_fallback_meta = any(
        str(k).startswith("cloudR46Refresh") or str(k).startswith("cloudFullUniverseRefresh")
        for k in forecast_meta
    )
    payload = {
        "base_url": args.url,
        "index_status": idx_status,
        "css_status": css_status,
        "analysis_status": ana_status,
        "data_status": data_status,
        "history_status": hist_status,
        "live_status": live_status,
        "forecast_status_http": forecast_status,
        "data_as_of": as_of_match.group(1) if as_of_match else None,
        "live_updated_at": live_updated_at,
        "live_latest_price_date": live_latest_price_date,
        "live_is_today": live_updated_at.startswith(today) or live_latest_price_date == today,
        "forecast_status": forecast_payload.get("status"),
        "forecast_as_of": forecast_payload.get("asOf"),
        "forecast_plan_date": forecast_payload.get("planDate"),
        "forecast_rows": len(forecast_payload.get("rows") or []),
        "forecast_has_fallback_meta": forecast_has_fallback_meta,
        "embedded_forecast_display_state": embedded_forecast_display_state,
        "embedded_forecast_is_computed": embedded_forecast_display_state == "COMPUTED",
        "vni_history_points": len(re.findall(r'"vniClose"\s*:\s*[0-9]', history)),
        "has_r46_key": "r46_bear_stop_mcore" in analysis,
        "has_r23_key": "r23_nav3b_mcore" in analysis,
        "has_hide_planned_orders_rule": ".planned-orders[hidden]" in css,
        "nul_bytes": {
            "index": idx_raw.count(b"\0"),
            "analysis": analysis_raw.count(b"\0"),
            "data": data_raw.count(b"\0"),
            "history": history_raw.count(b"\0"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    if any(payload["nul_bytes"].values()):
        raise SystemExit(1)
    if args.require_fresh_live and not payload["live_is_today"]:
        raise SystemExit(1)
    if args.require_vni_history and payload["vni_history_points"] <= 0:
        raise SystemExit(1)
    if args.require_current_forecast and (
        payload["forecast_status"] != "COMPUTED"
        or payload["forecast_rows"] <= 0
        or payload["forecast_has_fallback_meta"]
        or payload["embedded_forecast_display_state"] != "COMPUTED"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
