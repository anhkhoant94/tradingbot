from __future__ import annotations

import argparse
import json
import pickle
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
HISTORY_CACHE_PATH = ROOT / ".cache" / "backtest" / "history_cache.pkl"


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


def run_price_updates(symbols: list[str], workers: int) -> list[dict]:
    results: list[dict] = []
    if not symbols:
        return results
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = {pool.submit(update_symbol_price_safe, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({"symbol": futures[fut], "ok": False, "reason": str(exc)[:160]})
    return results


def latest_date_map(symbols: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym in symbols:
        last = live.last_cache_date(sym)
        if last is not None:
            out[sym] = last.isoformat()
    return out


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


def rebuild_history_cache(symbols: list[str]) -> dict:
    cache: dict[str, pd.DataFrame] = {}
    latest_dates: list[str] = []
    price_dir = live.BACKTEST_CACHE / "history_clean"
    for sym in symbols:
        path = price_dir / f"{sym}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            norm = live.normalize_price_frame(df)
        except Exception:
            continue
        if norm.empty:
            continue
        cache[sym] = norm
        latest_dates.append(norm["time"].max().date().isoformat())
    HISTORY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CACHE_PATH.open("wb") as f:
        pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
    return {
        "path": str(HISTORY_CACHE_PATH.relative_to(ROOT)),
        "symbols": len(cache),
        "latestPriceDate": max(latest_dates) if latest_dates else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--lookback-days", type=int, default=0)
    parser.add_argument("--min-fresh-pct", type=float, default=0.65)
    parser.add_argument("--retry-stale-passes", type=int, default=1)
    parser.add_argument("--retry-workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-date", default=None)
    args = parser.parse_args()

    started = time.time()
    target = pd.Timestamp(args.target_date).date() if args.target_date else date.today()
    symbols = read_universe_symbols(args.limit or None)
    todo = symbols if args.force else [sym for sym in symbols if needs_update(sym, target, args.lookback_days)]

    results: list[dict] = run_price_updates(todo, args.workers)

    min_required = max(0, int(len(symbols) * float(args.min_fresh_pct)))
    retry_rounds: list[dict] = []
    for round_no in range(max(0, int(args.retry_stale_passes))):
        latest_now = latest_date_map(symbols)
        stale = [sym for sym in symbols if latest_now.get(sym, "") < target.isoformat()]
        fresh_count = len(symbols) - len(stale)
        retry_rounds.append({"round": round_no + 1, "freshBefore": fresh_count, "staleBefore": len(stale)})
        if min_required <= 0 or fresh_count >= min_required or not stale:
            break
        retry_results = run_price_updates(stale, args.retry_workers)
        results.extend(retry_results)
        retry_rounds[-1]["attempted"] = len(stale)
        retry_rounds[-1]["ok"] = sum(1 for r in retry_results if r.get("ok"))

    vni_result = update_vnindex_2012()
    history_cache_result = rebuild_history_cache(symbols)
    latest_by_symbol = latest_date_map(symbols)
    latest_dates = list(latest_by_symbol.values())

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
        "historyCache": history_cache_result,
        "retryRounds": retry_rounds,
        "failedSample": failed[:20],
        "seconds": round(time.time() - started, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    DASH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    STATUS_PATH.write_text(text + "\n", encoding="utf-8")
    DASH_STATUS_PATH.write_text(text + "\n", encoding="utf-8")
    print(text)
    if min_required > 0 and payload["symbolsAtTargetOrNewer"] < min_required:
        raise SystemExit("full universe price refresh did not reach enough symbols")


if __name__ == "__main__":
    main()
