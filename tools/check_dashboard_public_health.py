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


def fetch_edge_live_status(base_url: str) -> dict | None:
    try:
        status, raw = fetch_bytes(base_url, f"/api/live-status?symbols=MSB&_health={int(time.time())}")
        payload = json.loads(decode(raw))
        payload["_http_status"] = status
        return payload
    except Exception:
        return None


def parse_embedded_dashboard_data(index: str) -> dict:
    match = re.search(r"const D\s*=\s*(\{.*?\});\s*function f", index, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_status_age_seconds(payload: dict) -> float | None:
    raw_utc = payload.get("updatedAtUtc")
    if raw_utc:
        try:
            stamp = dt.datetime.fromisoformat(str(raw_utc).replace("Z", "+00:00"))
            return max(0.0, (dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds())
        except Exception:
            pass
    for key in ("updatedAtICT", "updatedAt"):
        raw = payload.get(key)
        if not raw:
            continue
        try:
            stamp = dt.datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
            if key == "updatedAtICT":
                stamp = stamp.replace(tzinfo=dt.timezone(dt.timedelta(hours=7)))
            else:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            return max(0.0, (dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds())
        except Exception:
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--require-fresh-live",
        action="store_true",
        help="exit non-zero when bundled dashboard live quote status is not from today, too old, or not reconciled to latest VN-Index close",
    )
    parser.add_argument(
        "--max-live-age-minutes",
        type=float,
        default=30.0,
        help="maximum age for bundled dashboard_live_update_status.json when --require-fresh-live is set",
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
        "--require-edge-live",
        action="store_true",
        help="exit non-zero when the Vercel live-status API is missing or older than 10 minutes",
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
    exec_state_status, exec_state_raw = fetch_bytes(args.url, "/r46_execution_state.json")
    css = decode(css_raw)
    index = decode(idx_raw)
    analysis = decode(analysis_raw)
    data_js = decode(data_raw)
    history = decode(history_raw)

    today = dt.date.today().isoformat()
    as_of_match = re.search(r'"as_of"\s*:\s*"(\d{4}-\d{2}-\d{2})"', data_js)
    live_payload = json.loads(decode(live_raw))
    forecast_payload = json.loads(decode(forecast_raw))
    exec_state_payload = json.loads(decode(exec_state_raw))
    embedded_data = parse_embedded_dashboard_data(index)
    expected_exec_orders = [
        row for row in (exec_state_payload.get("orders") or [])
        if str(row.get("status") or "EXECUTED").upper() in {"EXECUTED", "FILLED"}
    ]
    embedded_exec_orders = ((embedded_data.get("executionState") or {}).get("orders") or [])
    expected_exec_keys = {
        (
            str(row.get("date") or row.get("executedDate") or ""),
            str(row.get("symbol") or "").upper(),
            int(float(row.get("shares") or row.get("orderShares") or 0)),
            str(row.get("side") or "").upper(),
        )
        for row in expected_exec_orders
    }
    embedded_exec_keys = {
        (
            str(row.get("date") or ""),
            str(row.get("symbol") or "").upper(),
            int(float(row.get("shares") or 0)),
            str(row.get("side") or "").upper(),
        )
        for row in embedded_exec_orders
    }
    copy_execution_matches_state = expected_exec_keys <= embedded_exec_keys
    copy_execution_nav_scaled = (
        "scaledCopyExecution" in index
        and "copyExecutionBaseNavMil" in index
        and 'id="copyExecScaleTag"' in index
        and "NAV copy 1" not in index
    )
    embedded_holdings = embedded_data.get("holdings") or []
    embedded_copy_account = embedded_data.get("copyAccount") or {}
    embedded_chart = embedded_data.get("chart") or []
    embedded_copy_cash_pct = num(embedded_copy_account.get("cashPct"))
    embedded_copy_exposure_pct = num(embedded_copy_account.get("exposurePct"))
    embedded_chart_last_date = str((embedded_chart[-1] or {}).get("date") or "") if embedded_chart else ""
    full_cash_expected = bool(expected_exec_orders and not embedded_holdings)
    copy_full_cash_consistent = (
        not full_cash_expected
        or (
            embedded_copy_cash_pct is not None
            and embedded_copy_cash_pct >= 99.9
            and (embedded_copy_exposure_pct or 0.0) <= 0.01
        )
    )
    live_updated_at = str(live_payload.get("updatedAt") or "")
    live_updated_at_ict = str(live_payload.get("updatedAtICT") or "")
    live_updated_at_utc = str(live_payload.get("updatedAtUtc") or "")
    live_status_age_seconds = parse_status_age_seconds(live_payload)
    live_latest_price_date = str(live_payload.get("latestPriceDate") or "")
    forecast_meta = forecast_payload.get("meta") or {}
    live_vnindex = live_payload.get("vnindex") or {}
    live_vni_date = str(live_vnindex.get("latest") or live_vnindex.get("date") or "")
    live_vni_close = live_vnindex.get("latestClose", live_vnindex.get("close"))
    try:
        live_vni_close = float(live_vni_close)
    except (TypeError, ValueError):
        live_vni_close = None
    source_vni = None
    source_vni_error = None
    if args.require_current_vni or args.require_current_forecast:
        try:
            source_vni = fetch_vps_vnindex_latest()
        except Exception as exc:  # VPS reference can timeout; do not hide dashboard health facts.
            source_vni_error = str(exc)[:200]
    edge_payload = fetch_edge_live_status(args.url)
    edge_age_seconds = parse_status_age_seconds(edge_payload or {}) if edge_payload else None
    edge_latest_price_date = str((edge_payload or {}).get("latestPriceDate") or "")
    edge_vnindex = (edge_payload or {}).get("vnindex") or {}
    edge_vni_date = str(edge_vnindex.get("latest") or edge_vnindex.get("date") or "")
    edge_vni_close = edge_vnindex.get("latestClose", edge_vnindex.get("close"))
    try:
        edge_vni_close = float(edge_vni_close)
    except (TypeError, ValueError):
        edge_vni_close = None
    forecast_display_match = re.search(r'"forecastDisplayState"\s*:\s*"([^"]+)"', index)
    embedded_forecast_display_state = forecast_display_match.group(1) if forecast_display_match else None
    forecast_has_fallback_meta = any(
        str(k).startswith("cloudR46Refresh") or str(k).startswith("cloudFullUniverseRefresh")
        for k in forecast_meta
    )
    forecast_as_of = str(forecast_payload.get("asOf") or "")
    forecast_as_of_matches_source = source_vni is None or forecast_as_of == source_vni.get("date")
    live_vni_exact_match = (
        source_vni is not None
        and live_vni_date == source_vni.get("date")
        and live_vni_close is not None
        and abs(live_vni_close - float(source_vni.get("close"))) <= 0.01
    )
    live_vni_recent_snapshot = (
        source_vni is not None
        and live_vni_date == source_vni.get("date")
        and live_vni_close is not None
        and live_status_age_seconds is not None
        and live_status_age_seconds <= 1200
    )
    edge_vni_exact_match = (
        source_vni is not None
        and edge_vni_date == source_vni.get("date")
        and edge_vni_close is not None
        and abs(edge_vni_close - float(source_vni.get("close"))) <= 0.01
    )
    edge_vni_recent_snapshot = (
        source_vni is not None
        and edge_vni_date == source_vni.get("date")
        and edge_vni_close is not None
        and edge_age_seconds is not None
        and edge_age_seconds <= 600
    )
    edge_live_ok = (
        edge_payload is not None
        and edge_payload.get("_http_status") == 200
        and edge_latest_price_date == today
        and edge_age_seconds is not None
        and edge_age_seconds <= 600
    )
    static_live_is_today = live_updated_at.startswith(today) or live_latest_price_date == today
    static_live_is_recent = (
        live_status_age_seconds is not None
        and live_status_age_seconds <= max(0.0, float(args.max_live_age_minutes)) * 60.0
    )
    static_live_current_snapshot = static_live_is_today and live_vni_exact_match
    chart_current_after_execution = (
        not full_cash_expected
        or not live_latest_price_date
        or (embedded_chart_last_date >= live_latest_price_date)
    )
    source_unavailable_live_ok = (
        source_vni is None
        and source_vni_error is not None
        and (
            edge_live_ok
            or (static_live_is_today and static_live_is_recent)
        )
    )
    live_vni_check_pass = (
        source_unavailable_live_ok
        or live_vni_exact_match
        or live_vni_recent_snapshot
        or edge_vni_exact_match
        or edge_vni_recent_snapshot
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
        "execution_state_status_http": exec_state_status,
        "data_as_of": as_of_match.group(1) if as_of_match else None,
        "live_updated_at": live_updated_at,
        "live_updated_at_ict": live_updated_at_ict or None,
        "live_updated_at_utc": live_updated_at_utc or None,
        "live_status_age_seconds": round(live_status_age_seconds, 1) if live_status_age_seconds is not None else None,
        "live_latest_price_date": live_latest_price_date,
        "static_live_is_today": static_live_is_today,
        "static_live_is_recent": static_live_is_recent,
        "static_live_current_snapshot": static_live_current_snapshot,
        "live_is_today": static_live_is_today or edge_latest_price_date == today,
        "live_vni_date": live_vni_date or None,
        "live_vni_close": live_vni_close,
        "source_vni_date": source_vni.get("date") if source_vni else None,
        "source_vni_close": source_vni.get("close") if source_vni else None,
        "source_vni_error": source_vni_error,
        "live_vni_matches_source": live_vni_exact_match,
        "live_vni_recent_snapshot": live_vni_recent_snapshot,
        "edge_live_status": (edge_payload or {}).get("_http_status"),
        "edge_live_updated_at_ict": (edge_payload or {}).get("updatedAtICT"),
        "edge_live_status_age_seconds": round(edge_age_seconds, 1) if edge_age_seconds is not None else None,
        "edge_live_latest_price_date": edge_latest_price_date or None,
        "edge_live_is_fresh": edge_live_ok,
        "edge_vni_date": edge_vni_date or None,
        "edge_vni_close": edge_vni_close,
        "edge_vni_matches_source": edge_vni_exact_match,
        "edge_vni_recent_snapshot": edge_vni_recent_snapshot,
        "live_vni_check_pass": live_vni_check_pass,
        "forecast_status": forecast_payload.get("status"),
        "forecast_as_of": forecast_as_of or None,
        "forecast_plan_date": forecast_payload.get("planDate"),
        "forecast_rows": len(forecast_payload.get("rows") or []),
        "forecast_has_fallback_meta": forecast_has_fallback_meta,
        "forecast_as_of_matches_source": forecast_as_of_matches_source,
        "embedded_forecast_display_state": embedded_forecast_display_state,
        "embedded_forecast_is_computed": embedded_forecast_display_state == "COMPUTED",
        "has_execution_desk": (
            '"executionDesk"' in index
            and ('"bearStop"' in index or '"bearStopLossPct"' in index or '"regime": "FULL_CASH"' in index)
            and 'id="execRows"' in index
            and '"regime": "UNKNOWN"' not in index
        ),
        "execution_state_order_count": len(expected_exec_orders),
        "embedded_copy_execution_count": len(embedded_exec_orders),
        "copy_execution_matches_state": copy_execution_matches_state,
        "copy_execution_nav_scaled": copy_execution_nav_scaled,
        "has_copy_execution_table": 'id="copyExecRows"' in index,
        "full_cash_expected": full_cash_expected,
        "embedded_holdings_count": len(embedded_holdings),
        "embedded_copy_cash_pct": embedded_copy_cash_pct,
        "embedded_copy_exposure_pct": embedded_copy_exposure_pct,
        "copy_full_cash_consistent": copy_full_cash_consistent,
        "embedded_chart_last_date": embedded_chart_last_date or None,
        "chart_current_after_execution": chart_current_after_execution,
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
    if args.require_fresh_live and not (
        payload["static_live_is_today"]
        and (payload["static_live_is_recent"] or payload["static_live_current_snapshot"])
    ):
        raise SystemExit(1)
    if args.require_edge_live and not payload["edge_live_is_fresh"]:
        raise SystemExit(1)
    if args.require_vni_history and payload["vni_history_points"] <= 0:
        raise SystemExit(1)
    if args.require_current_vni and not payload["live_vni_check_pass"]:
        raise SystemExit(1)
    if args.require_execution_desk and not payload["has_execution_desk"]:
        raise SystemExit(1)
    if args.require_execution_desk and expected_exec_orders and not (
        payload["has_copy_execution_table"]
        and payload["copy_execution_matches_state"]
        and payload["copy_execution_nav_scaled"]
        and payload["copy_full_cash_consistent"]
        and payload["chart_current_after_execution"]
    ):
        raise SystemExit(1)
    if args.require_current_forecast and (
        payload["forecast_status"] != "COMPUTED"
        or payload["forecast_has_fallback_meta"]
        or not payload["forecast_as_of_matches_source"]
        or payload["embedded_forecast_display_state"] != "COMPUTED"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
