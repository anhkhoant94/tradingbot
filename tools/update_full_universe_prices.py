from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import update_dashboard_live_data as live  # noqa: E402

UNIVERSE_PATH = ROOT / ".cache" / "universe.parquet"
OUT = ROOT / "output"
STATUS_PATH = OUT / "full_universe_live_update_status.json"
DASH_STATUS_PATH = ROOT / "dashboard" / "full_universe_live_update_status.json"


def read_universe_symbols(limit: int | None = None) -> list[str]:
    universe = pd.read_parquet(UNIVERSE_PATH)
    symbols = (
        universe.loc[universe["type"].astype(str).str.lower().eq("stock"), "symbol"]
        .astype(str)
        .str.upper()
        .str.strip()
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    symbols = [sym for sym in symbols if sym and sym != "VNINDEX"]
    return symbols[:limit] if limit else symbols


def needs_update(symbol: str, target: date, lookback_days: int) -> bool:
    last = live.last_cache_date(symbol)
    if last is None:
        return True
    if last < date(2010, 1, 1):
        return True
    return last < target - timedelta(days=max(0, int(lookback_days)))


def update_symbol_price_safe(symbol: str) -> dict:
    last = live.last_cache_date(symbol)
    today = date.today()
    floor_start = date(2020, 1, 1)
    if last is None or last < floor_start:
        start = floor_start
    else:
        start = max(floor_start, last - timedelta(days=8))
    try:
        fresh = live.fetch_vps_daily(symbol, start, today)
        return live.merge_price_cache(symbol, fresh)
    except Exception as exc:
        return {"symbol": symbol, "ok": False, "reason": str(exc)[:160]}


def update_vnindex_2012() -> dict:
    today = date.today()
    floor_start = date(2016, 1, 1)
    path = live.BACKTEST_CACHE / "vnindex_daily.parquet"
    start = floor_start
    if path.exists():
        try:
            current = pd.read_parquet(path)
            if not current.empty:
                last = pd.to_datetime(current["date"], errors="coerce").max().date()
                if last >= floor_start:
                    start = max(floor_start, last - timedelta(days=8))
        except Exception:
            start = floor_start
    try:
        fresh = live.fetch_vps_daily("VNINDEX", start, today)
        if fresh.empty:
            result = {"symbol": "VNINDEX", "ok": False, "reason": "no_data"}
        else:
            fresh = fresh.rename(columns={"time": "date"})[["date", "close"]]
            frames = []
            for p in [live.BACKTEST_CACHE / "vnindex_daily.parquet", live.BACKTEST_CACHE / "vnindex_daily_v6.parquet"]:
                old = pd.read_parquet(p) if p.exists() else pd.DataFrame()
                combined = pd.concat([old, fresh], ignore_index=True)
                combined["date"] = pd.to_datetime(combined["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
                combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
                combined = combined.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")
                live.write_parquet(combined, p)
                frames.append(combined)
            result = {
                "symbol": "VNINDEX",
                "ok": True,
                "latest": frames[-1]["date"].max().date().isoformat() if frames else None,
                "rows": len(frames[-1]) if frames else 0,
            }
    except Exception as exc:
        result = {"symbol": "VNINDEX", "ok": False, "reason": str(exc)[:160]}
    src = live.BACKTEST_CACHE / "vnindex_daily.parquet"
    dst = live.BACKTEST_CACHE / "vnindex_daily_2012.parquet"
    try:
        fresh = pd.read_parquet(src)
        old = pd.read_parquet(dst) if dst.exists() else pd.DataFrame()
        combined = pd.concat([old, fresh], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"]).dt.tz_localize(None).dt.normalize()
        combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
        combined = combined.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")
        live.write_parquet(combined, dst)
        result["vnindex_2012_latest"] = combined["date"].max().date().isoformat()
        result["vnindex_2012_rows"] = int(len(combined))
    except Exception as exc:
        result["vnindex_2012_error"] = str(exc)[:160]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--lookback-days", type=int, default=0)
    parser.add_argument("--min-fresh-pct", type=float, default=0.65)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-date", default=None)
    args = parser.parse_args()

    started = time.time()
    target = pd.Timestamp(args.target_date).date() if args.target_date else date.today()
    symbols = read_universe_symbols(args.limit or None)
    todo = symbols if args.force else [sym for sym in symbols if needs_update(sym, target, args.lookback_days)]

    results: list[dict] = []
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
            futures = {pool.submit(update_symbol_price_safe, sym): sym for sym in todo}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    results.append({"symbol": futures[fut], "ok": False, "reason": str(exc)[:160]})

    vni_result = update_vnindex_2012()
    latest_dates = []
    for sym in symbols:
        last = live.last_cache_date(sym)
        if last is not None:
            latest_dates.append(last.isoformat())

    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    payload = {
        "updatedAt": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "targetDate": target.isoformat(),
        "symbolsTotal": len(symbols),
        "symbolsAttempted": len(todo),
        "symbolsUpdated": len(ok),
        "symbolsFailed": len(failed),
        "latestPriceDate": max(latest_dates) if latest_dates else None,
        "symbolsAtTargetOrNewer": sum(1 for d in latest_dates if d >= target.isoformat()),
        "vnindex": vni_result,
        "failedSample": failed[:20],
        "seconds": round(time.time() - started, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    DASH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    STATUS_PATH.write_text(text + "\n", encoding="utf-8")
    DASH_STATUS_PATH.write_text(text + "\n", encoding="utf-8")
    print(text)
    min_required = max(1, int(len(symbols) * float(args.min_fresh_pct)))
    if payload["symbolsAtTargetOrNewer"] < min_required:
        raise SystemExit("full universe price refresh did not reach enough symbols")


if __name__ == "__main__":
    main()
