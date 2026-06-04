#!/usr/bin/env python
"""Lightweight public health check for the deployed dashboard."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
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


def fetch_vps_vnindex_latest() -> dict:
    today = dt.date.today()
    start = today - dt.timedelta(days=14)
    fr = int(time.mktime(start.timetuple()))
    to = int(time.mktime((today + dt.timedelta(days=1)).timetuple()))
    url = (
        "https://histdatafeed.vps.com.vn/tradingview/history"
        f"?symbol=VNINDEX&resolution=D&from={fr}&to={to}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "codex-dashboard-health/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(decode(resp.read()))
    if payload.get("s") != "ok" or not payload.get("t") or not payload.get("c"):
        raise RuntimeError("VPS VNINDEX returned no daily close")
    idx = len(payload["t"]) - 1
    close = float(payload["c"][idx])
    date = dt.datetime.fromtimestamp(int(payload["t"][idx])).date().isoformat()
    return {"date": date, "close": close}


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
    parser.add_argument(
        "--require-current-vni",
        action="store_true",
        help="exit non-zero when public VN-Index close is stale vs VPS daily source",
    )
    parser.add_argument(
        "--require-execution-desk",
        action="store_true",
        help="exit non-zero when the deployed page does not embed the R46 execution desk",
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
    live_vnindex = live_payload.get("vnindex") or {}
    live_vni_date = str(live_vnindex.get("latest") or live_vnindex.get("date") or "")
    live_vni_close = live_vnindex.get("latestClose", live_vnindex.get("close"))
    try:
        live_vni_close = float(live_vni_close)
    except (TypeError, ValueError):
        live_vni_close = None
    source_vni = fetch_vps_vnindex_latest() if args.require_current_vni else None
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
        "live_vni_date": live_vni_date or None,
        "live_vni_close": live_vni_close,
        "source_vni_date": source_vni.get("date") if source_vni else None,
        "source_vni_close": source_vni.get("close") if source_vni else None,
        "live_vni_matches_source": (
            source_vni is None
            or (
                live_vni_date == source_vni.get("date")
                and live_vni_close is not None
                and abs(live_vni_close - float(source_vni.get("close"))) <= 0.01
            )
        ),
        "forecast_status": forecast_payload.get("status"),
        "forecast_as_of": forecast_payload.get("asOf"),
        "forecast_plan_date": forecast_payload.get("planDate"),
        "forecast_rows": len(forecast_payload.get("rows") or []),
        "forecast_has_fallback_meta": forecast_has_fallback_meta,
        "embedded_forecast_display_state": embedded_forecast_display_state,
        "embedded_forecast_is_computed": embedded_forecast_display_state == "COMPUTED",
        "has_execution_desk": (
            '"executionDesk"' in index
            and '"bearStop"' in index
            and 'id="execRows"' in index
            and '"regime": "UNKNOWN"' not in index
        ),
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
    if args.require_current_vni and not payload["live_vni_matches_source"]:
        raise SystemExit(1)
    if args.require_execution_desk and not payload["has_execution_desk"]:
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
