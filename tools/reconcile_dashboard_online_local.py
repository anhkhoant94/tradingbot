#!/usr/bin/env python
"""Reconcile local dashboard artifacts against the canonical Ez public dashboard."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
OUT = ROOT / "output" / "dashboard_reconciliation"
DEFAULT_BASE_URL = "https://ez-trading.vercel.app"
ICT = dt.timezone(dt.timedelta(hours=7))


def now_ict() -> dt.datetime:
    return dt.datetime.now(ICT)


def fetch_text(base_url: str, path: str) -> str:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"User-Agent": "codex-dashboard-reconcile/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_json_text(text: str) -> dict[str, Any]:
    return json.loads(text.encode("utf-8").decode("utf-8-sig"))


def load_json_file(path: Path) -> dict[str, Any]:
    return load_json_text(read_text(path))


def parse_embedded_dashboard_data(index: str) -> dict[str, Any]:
    match = re.search(r"const D\s*=\s*(\{.*?\});\s*function f", index, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def age_seconds(payload: dict[str, Any]) -> float | None:
    raw_utc = payload.get("updatedAtUtc") or payload.get("computedAtUtc")
    if raw_utc:
        try:
            stamp = dt.datetime.fromisoformat(str(raw_utc).replace("Z", "+00:00"))
            return max(0.0, (dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds())
        except Exception:
            pass
    for key in ("updatedAtICT", "computedAtICT", "updatedAt", "computedAt"):
        raw = payload.get(key)
        if not raw:
            continue
        try:
            stamp = dt.datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
            if key.endswith("ICT"):
                stamp = stamp.replace(tzinfo=ICT)
            else:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            return max(0.0, (dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds())
        except Exception:
            continue
    return None


def intish(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def status_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "updatedAtICT": payload.get("updatedAtICT"),
        "updatedAtUtc": payload.get("updatedAtUtc"),
        "latestPriceDate": payload.get("latestPriceDate"),
        "vnindexDate": (payload.get("vnindex") or {}).get("latest") or (payload.get("vnindex") or {}).get("date"),
        "vnindexClose": (payload.get("vnindex") or {}).get("latestClose") or (payload.get("vnindex") or {}).get("close"),
        "ageSeconds": age_seconds(payload),
    }


def full_universe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    same_day = intish(payload.get("sameDaySymbols", payload.get("symbolsAtTargetOrNewer")))
    stale = intish(payload.get("staleButUsableSymbols"))
    total = intish(payload.get("symbolsTotal"))
    usable = intish(payload.get("usableSymbols")) or min(total, same_day + stale)
    return {
        "updatedAtICT": payload.get("updatedAtICT"),
        "targetDate": payload.get("targetDate"),
        "latestPriceDate": payload.get("latestPriceDate"),
        "symbolsTotal": total,
        "usableSymbols": usable,
        "sameDaySymbols": same_day,
        "staleButUsableSymbols": stale,
        "symbolsFailed": intish(payload.get("symbolsFailed")),
        "usableForForecast": bool(payload.get("usableForForecast")),
        "coverageMode": payload.get("coverageMode"),
        "ageSeconds": age_seconds(payload),
    }


def forecast_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "asOf": payload.get("asOf"),
        "planDate": payload.get("planDate"),
        "rows": len(payload.get("rows") or []),
        "computedAtICT": payload.get("computedAtICT"),
        "computedAtUtc": payload.get("computedAtUtc"),
        "hasFallbackMeta": any(
            str(k).startswith("cloudR46Refresh") or str(k).startswith("cloudFullUniverseRefresh")
            for k in (payload.get("meta") or {})
        ),
        "ageSeconds": age_seconds(payload),
    }


def execution_summary(payload: dict[str, Any]) -> dict[str, Any]:
    orders = payload.get("orders") or []
    executed = [
        row for row in orders
        if str(row.get("status") or "EXECUTED").upper() in {"EXECUTED", "FILLED"}
    ]
    last = executed[-1] if executed else {}
    return {
        "orders": len(orders),
        "executedOrders": len(executed),
        "lastExecutedDate": payload.get("lastExecutedDate") or last.get("date") or last.get("executedDate"),
        "lastExecutedSymbol": last.get("symbol"),
        "lastExecutedSide": last.get("side"),
        "lastExecutedShares": intish(last.get("shares", last.get("orderShares"))),
        "cashPct": payload.get("cashPct"),
        "exposurePct": payload.get("exposurePct"),
    }


def embedded_summary(index: str) -> dict[str, Any]:
    data = parse_embedded_dashboard_data(index)
    if not data:
        return {"parsed": False}
    full = data.get("fullUniverseStatus") or {}
    planned = ((data.get("policy") or {}).get("plannedOrders") or {})
    exec_state = data.get("executionState") or {}
    trades = data.get("tradesLatest") or []
    first_trade = trades[0] if trades else {}
    return {
        "parsed": True,
        "asOf": data.get("asOf"),
        "forecastDisplayState": planned.get("forecastDisplayState"),
        "forecastPlanDate": planned.get("forecastPlanDate"),
        "plannedRows": len(planned.get("rows") or []),
        "fullUniverseUsable": intish(full.get("usableSymbols")),
        "fullUniverseSameDay": intish(full.get("sameDaySymbols", full.get("symbolsAtTargetOrNewer"))),
        "fullUniverseTotal": intish(full.get("symbolsTotal")),
        "copyCashPct": (data.get("copyAccount") or {}).get("cashPct"),
        "copyExposurePct": (data.get("copyAccount") or {}).get("exposurePct"),
        "executionOrdersEmbedded": len(exec_state.get("orders") or []),
        "tradeCount": data.get("tradeCount") or len(data.get("ledger") or []),
        "firstTrade": {
            "date": first_trade.get("date"),
            "symbol": first_trade.get("symbol"),
            "side": first_trade.get("side"),
            "shares": intish(first_trade.get("shares")),
        },
        "hasCopyExecRows": 'id="copyExecRows"' in index,
        "hasOldProjectLink": "trading-execution-desk-khoa" in index,
    }


def compare_value(issues: list[dict[str, Any]], name: str, local: Any, online: Any, severity: str = "critical") -> None:
    if local != online:
        issues.append({"severity": severity, "field": name, "local": local, "online": online})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-online-live-age-minutes", type=float, default=30.0)
    parser.add_argument("--max-online-forecast-age-minutes", type=float, default=45.0)
    parser.add_argument("--strict", action="store_true", help="exit non-zero on warnings as well as critical issues")
    args = parser.parse_args()

    online = {
        "live": load_json_text(fetch_text(args.base_url, "dashboard_live_update_status.json")),
        "fullUniverse": load_json_text(fetch_text(args.base_url, "full_universe_live_update_status.json")),
        "forecast": load_json_text(fetch_text(args.base_url, "r46_forecast.json")),
        "execution": load_json_text(fetch_text(args.base_url, "r46_execution_state.json")),
        "index": fetch_text(args.base_url, ""),
    }
    local = {
        "live": load_json_file(DASH / "dashboard_live_update_status.json"),
        "fullUniverse": load_json_file(DASH / "full_universe_live_update_status.json"),
        "forecast": load_json_file(DASH / "r46_forecast.json"),
        "execution": load_json_file(DASH / "r46_execution_state.json"),
        "index": read_text(DASH / "index.html"),
    }

    report = {
        "checkedAtICT": now_ict().strftime("%Y-%m-%d %H:%M:%S"),
        "baseUrl": args.base_url.rstrip("/"),
        "online": {
            "live": status_summary(online["live"]),
            "fullUniverse": full_universe_summary(online["fullUniverse"]),
            "forecast": forecast_summary(online["forecast"]),
            "execution": execution_summary(online["execution"]),
            "embedded": embedded_summary(online["index"]),
        },
        "local": {
            "live": status_summary(local["live"]),
            "fullUniverse": full_universe_summary(local["fullUniverse"]),
            "forecast": forecast_summary(local["forecast"]),
            "execution": execution_summary(local["execution"]),
            "embedded": embedded_summary(local["index"]),
        },
        "issues": [],
    }
    issues: list[dict[str, Any]] = report["issues"]

    online_live = report["online"]["live"]
    online_full = report["online"]["fullUniverse"]
    online_forecast = report["online"]["forecast"]
    online_exec = report["online"]["execution"]
    online_embedded = report["online"]["embedded"]
    local_live = report["local"]["live"]
    local_full = report["local"]["fullUniverse"]
    local_forecast = report["local"]["forecast"]
    local_exec = report["local"]["execution"]
    local_embedded = report["local"]["embedded"]

    today = dt.date.today().isoformat()
    if online_live["latestPriceDate"] != today:
        issues.append({"severity": "critical", "field": "online.live.latestPriceDate", "online": online_live["latestPriceDate"], "expected": today})
    if online_live["ageSeconds"] is None or online_live["ageSeconds"] > args.max_online_live_age_minutes * 60:
        issues.append({"severity": "critical", "field": "online.live.ageSeconds", "online": online_live["ageSeconds"], "max": args.max_online_live_age_minutes * 60})
    if online_forecast["status"] != "COMPUTED":
        issues.append({"severity": "critical", "field": "online.forecast.status", "online": online_forecast["status"], "expected": "COMPUTED"})
    if online_forecast["asOf"] != online_full["latestPriceDate"]:
        issues.append({"severity": "critical", "field": "online.forecast.asOf", "online": online_forecast["asOf"], "expected": online_full["latestPriceDate"]})
    if online_forecast["hasFallbackMeta"]:
        issues.append({"severity": "critical", "field": "online.forecast.hasFallbackMeta", "online": True, "expected": False})
    if online_forecast["ageSeconds"] is None or online_forecast["ageSeconds"] > args.max_online_forecast_age_minutes * 60:
        issues.append({"severity": "warning", "field": "online.forecast.ageSeconds", "online": online_forecast["ageSeconds"], "max": args.max_online_forecast_age_minutes * 60})
    if not online_full["usableForForecast"]:
        issues.append({"severity": "critical", "field": "online.fullUniverse.usableForForecast", "online": online_full["usableForForecast"], "expected": True})
    if online_full["usableSymbols"] < int(online_full["symbolsTotal"] * 0.95):
        issues.append({"severity": "critical", "field": "online.fullUniverse.usableSymbols", "online": online_full["usableSymbols"], "min": int(online_full["symbolsTotal"] * 0.95)})
    if online_full["symbolsFailed"] > max(5, int(online_full["symbolsTotal"] * 0.05)):
        issues.append({"severity": "warning", "field": "online.fullUniverse.symbolsFailed", "online": online_full["symbolsFailed"]})
    if not online_embedded.get("parsed"):
        issues.append({"severity": "critical", "field": "online.embedded.parsed", "online": False})
    if online_embedded.get("hasOldProjectLink"):
        issues.append({"severity": "critical", "field": "online.embedded.hasOldProjectLink", "online": True})
    if online_embedded.get("forecastDisplayState") != "COMPUTED":
        issues.append({"severity": "critical", "field": "online.embedded.forecastDisplayState", "online": online_embedded.get("forecastDisplayState"), "expected": "COMPUTED"})
    if online_embedded.get("executionOrdersEmbedded") != online_exec["executedOrders"]:
        issues.append({"severity": "critical", "field": "online.embedded.executionOrdersEmbedded", "online": online_embedded.get("executionOrdersEmbedded"), "expected": online_exec["executedOrders"]})
    if online_exec["executedOrders"] and not online_embedded.get("hasCopyExecRows"):
        issues.append({"severity": "critical", "field": "online.embedded.hasCopyExecRows", "online": False, "expected": True})

    compare_value(issues, "latestPriceDate", local_live["latestPriceDate"], online_live["latestPriceDate"], "critical")
    compare_value(issues, "forecastStatus", local_forecast["status"], online_forecast["status"], "critical")
    compare_value(issues, "forecastAsOf", local_forecast["asOf"], online_forecast["asOf"], "critical")
    compare_value(issues, "forecastPlanDate", local_forecast["planDate"], online_forecast["planDate"], "critical")
    compare_value(issues, "forecastRows", local_forecast["rows"], online_forecast["rows"], "critical")
    compare_value(issues, "executionExecutedOrders", local_exec["executedOrders"], online_exec["executedOrders"], "critical")
    compare_value(issues, "embeddedTradeCount", local_embedded.get("tradeCount"), online_embedded.get("tradeCount"), "warning")
    if local_full["usableSymbols"] != online_full["usableSymbols"] or local_full["symbolsTotal"] != online_full["symbolsTotal"]:
        issues.append({
            "severity": "warning",
            "field": "fullUniverseCoverage",
            "local": {"usable": local_full["usableSymbols"], "total": local_full["symbolsTotal"], "sameDay": local_full["sameDaySymbols"]},
            "online": {"usable": online_full["usableSymbols"], "total": online_full["symbolsTotal"], "sameDay": online_full["sameDaySymbols"]},
        })

    critical_count = sum(1 for row in issues if row.get("severity") == "critical")
    warning_count = sum(1 for row in issues if row.get("severity") == "warning")
    report["verdict"] = "PASS" if critical_count == 0 and (warning_count == 0 or not args.strict) else "FAIL"
    report["criticalCount"] = critical_count
    report["warningCount"] = warning_count

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = now_ict().strftime("%Y%m%d_%H%M%S")
    json_path = OUT / f"reconcile_{stamp}.json"
    md_path = OUT / f"reconcile_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_lines = [
        f"# Ez dashboard reconciliation {report['checkedAtICT']}",
        "",
        f"Verdict: **{report['verdict']}** ({critical_count} critical, {warning_count} warning)",
        "",
        "## Online",
        f"- live: {online_live['latestPriceDate']} / {online_live['updatedAtICT']} / VNI {online_live['vnindexClose']}",
        f"- universe: usable {online_full['usableSymbols']}/{online_full['symbolsTotal']}, same-day {online_full['sameDaySymbols']}, stale usable {online_full['staleButUsableSymbols']}",
        f"- forecast: {online_forecast['status']} asOf {online_forecast['asOf']} planDate {online_forecast['planDate']} rows {online_forecast['rows']}",
        f"- execution: {online_exec['executedOrders']} executed orders, last {online_exec['lastExecutedDate']} {online_exec['lastExecutedSymbol']} {online_exec['lastExecutedSide']}",
        "",
        "## Local",
        f"- live: {local_live['latestPriceDate']} / {local_live['updatedAtICT']} / VNI {local_live['vnindexClose']}",
        f"- universe: usable {local_full['usableSymbols']}/{local_full['symbolsTotal']}, same-day {local_full['sameDaySymbols']}, stale usable {local_full['staleButUsableSymbols']}",
        f"- forecast: {local_forecast['status']} asOf {local_forecast['asOf']} planDate {local_forecast['planDate']} rows {local_forecast['rows']}",
        f"- execution: {local_exec['executedOrders']} executed orders, last {local_exec['lastExecutedDate']} {local_exec['lastExecutedSymbol']} {local_exec['lastExecutedSide']}",
        "",
        "## Issues",
    ]
    if issues:
        for row in issues:
            md_lines.append(f"- {row['severity'].upper()} {row['field']}: local={row.get('local')} online={row.get('online')} expected={row.get('expected', row.get('min', row.get('max', '')))}")
    else:
        md_lines.append("- none")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "verdict": report["verdict"],
        "criticalCount": critical_count,
        "warningCount": warning_count,
        "json": str(json_path.relative_to(ROOT)),
        "md": str(md_path.relative_to(ROOT)),
    }, ensure_ascii=False))
    if critical_count > 0 or (args.strict and warning_count > 0):
        sys.exit(1)


if __name__ == "__main__":
    main()
