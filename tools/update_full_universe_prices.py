from __future__ import annotations

import argparse
import json
import math
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

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
ICT = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_EXCHANGES = ("HOSE", "HNX")
OIL_GAS_SYMBOLS = {"BSR", "PVD", "PVS", "PVT", "GAS", "PLX", "PVC", "PVB", "PVP", "OIL"}


def classify_sector(industry_name: str) -> str:
    name = str(industry_name or "").lower()
    if "ngân hàng" in name or "ngan hang" in name:
        return "bank"
    if "chứng khoán" in name or "chung khoan" in name:
        return "securities"
    if "dầu" in name or "khí" in name or "dau" in name or "khi" in name:
        return "oil_gas"
    return "non_financial"


def read_cached_universe() -> pd.DataFrame:
    if not UNIVERSE_PATH.exists():
        return pd.DataFrame(columns=["symbol", "exchange", "type", "industry_name", "sector_group"])
    universe = pd.read_parquet(UNIVERSE_PATH)
    if "symbol" in universe.columns:
        universe["symbol"] = universe["symbol"].astype(str).str.upper().str.strip()
    return universe


def fetch_listed_universe(exchanges: set[str]) -> pd.DataFrame:
    from vnstock import Listing

    listing = Listing(source="kbs")
    raw = listing.symbols_by_exchange("HOSE")
    raw["symbol"] = raw["symbol"].astype(str).str.upper().str.strip()
    raw["exchange"] = raw.get("exchange", "").astype(str).str.upper().str.strip()
    raw["type"] = raw.get("type", "").astype(str).str.lower().str.strip()
    universe = raw[(raw["type"].eq("stock")) & (raw["exchange"].isin(exchanges))].drop_duplicates("symbol").copy()
    try:
        industries = listing.symbols_by_industries()
    except Exception:
        industries = pd.DataFrame(columns=["symbol", "industry_code", "industry_name"])
    if not industries.empty:
        industries["symbol"] = industries["symbol"].astype(str).str.upper().str.strip()
    universe = universe.merge(industries, how="left", on="symbol", suffixes=("", "_industry"))
    if "sector_group" not in universe.columns:
        universe["sector_group"] = universe.get("industry_name", "").map(classify_sector)
    universe.loc[universe["symbol"].isin(OIL_GAS_SYMBOLS), "sector_group"] = "oil_gas"
    return universe.sort_values(["exchange", "symbol"]).reset_index(drop=True)


def refresh_universe_cache(target: date, inactive_days: int, exchanges: set[str], skip_refresh: bool) -> tuple[pd.DataFrame, dict]:
    cached = read_cached_universe()
    meta: dict = {
        "cachePath": str(UNIVERSE_PATH.relative_to(ROOT)),
        "cachedSymbols": int(cached["symbol"].nunique()) if "symbol" in cached.columns else 0,
        "listingRefresh": "skipped" if skip_refresh else "not_attempted",
        "exchanges": sorted(exchanges),
    }
    if skip_refresh:
        return cached, meta

    try:
        listed = fetch_listed_universe(exchanges)
    except Exception as exc:
        meta["listingRefresh"] = "failed"
        meta["listingError"] = str(exc)[:240]
        return cached, meta

    listed_symbols = set(listed["symbol"].astype(str))
    cached_symbols = set(cached["symbol"].astype(str)) if "symbol" in cached.columns else set()
    cutoff = target - timedelta(days=max(1, int(inactive_days)))
    keep_missing = []
    if not cached.empty and "symbol" in cached.columns:
        for _, row in cached[~cached["symbol"].isin(listed_symbols)].iterrows():
            sym = str(row.get("symbol") or "").upper().strip()
            last = live.last_cache_date(sym)
            if sym and last is not None and last >= cutoff:
                keep_missing.append(row)
    if keep_missing:
        listed = pd.concat([listed, pd.DataFrame(keep_missing)], ignore_index=True)
        listed = listed.drop_duplicates("symbol", keep="first").sort_values(["exchange", "symbol"]).reset_index(drop=True)

    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    listed.to_parquet(UNIVERSE_PATH, index=False)
    meta.update(
        {
            "listingRefresh": "ok",
            "listedSymbols": int(len(listed_symbols)),
            "newListedSymbols": sorted(listed_symbols - cached_symbols)[:50],
            "newListedCount": int(len(listed_symbols - cached_symbols)),
            "missingCachedSymbols": sorted(cached_symbols - listed_symbols)[:50],
            "missingCachedCount": int(len(cached_symbols - listed_symbols)),
            "recentMissingKeptCount": int(len(keep_missing)),
        }
    )
    return listed, meta


def read_universe_symbols(limit: int | None = None, universe: pd.DataFrame | None = None) -> list[str]:
    universe = read_cached_universe() if universe is None else universe
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


def split_active_symbols(symbols: list[str], target: date, inactive_days: int) -> tuple[list[str], list[dict]]:
    cutoff = target - timedelta(days=max(1, int(inactive_days)))
    active: list[str] = []
    inactive: list[dict] = []
    for sym in symbols:
        last = live.last_cache_date(sym)
        if last is None:
            active.append(sym)
        elif last >= cutoff:
            active.append(sym)
        else:
            inactive.append({"symbol": sym, "last": last.isoformat(), "inactiveDays": int((target - last).days)})
    return active, inactive


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
    parser.add_argument("--inactive-days", type=int, default=45)
    parser.add_argument("--probe-inactive-limit", type=int, default=60)
    parser.add_argument("--skip-universe-refresh", action="store_true")
    parser.add_argument("--exchanges", default=",".join(DEFAULT_EXCHANGES))
    parser.add_argument("--retry-stale-passes", type=int, default=1)
    parser.add_argument("--retry-workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-date", default=None)
    args = parser.parse_args()

    started = time.time()
    target = pd.Timestamp(args.target_date).date() if args.target_date else date.today()
    exchanges = {x.strip().upper() for x in str(args.exchanges).split(",") if x.strip()}
    universe, universe_meta = refresh_universe_cache(target, args.inactive_days, exchanges or set(DEFAULT_EXCHANGES), args.skip_universe_refresh)
    candidate_symbols = read_universe_symbols(args.limit or None, universe=universe)
    active_pre, inactive_pre = split_active_symbols(candidate_symbols, target, args.inactive_days)
    inactive_probe = [
        row["symbol"]
        for row in sorted(inactive_pre, key=lambda item: (-int(item.get("inactiveDays", 0)), str(item.get("symbol", ""))))
    ][: max(0, int(args.probe_inactive_limit))]
    base_todo = active_pre if args.force else [sym for sym in active_pre if needs_update(sym, target, args.lookback_days)]
    todo = sorted(set(base_todo) | set(inactive_probe))
    attempted_symbols = set(todo)

    results: list[dict] = run_price_updates(todo, args.workers)

    retry_rounds: list[dict] = []
    for round_no in range(max(0, int(args.retry_stale_passes))):
        latest_now = latest_date_map(active_pre)
        stale = [sym for sym in active_pre if latest_now.get(sym, "") < target.isoformat()]
        fresh_count = len(active_pre) - len(stale)
        min_required_pre = max(0, int(len(active_pre) * float(args.min_fresh_pct)))
        retry_rounds.append({"round": round_no + 1, "freshBefore": fresh_count, "staleBefore": len(stale)})
        if min_required_pre <= 0 or fresh_count >= min_required_pre or not stale:
            break
        retry_results = run_price_updates(stale, args.retry_workers)
        results.extend(retry_results)
        attempted_symbols.update(stale)
        retry_rounds[-1]["attempted"] = len(stale)
        retry_rounds[-1]["ok"] = sum(1 for r in retry_results if r.get("ok"))

    vni_result = update_vnindex_2012()
    active_symbols, inactive_post = split_active_symbols(candidate_symbols, target, args.inactive_days)
    history_cache_result = rebuild_history_cache(active_symbols)
    latest_by_symbol = latest_date_map(active_symbols)
    latest_dates = list(latest_by_symbol.values())

    ok_symbols = {str(r.get("symbol", "")).upper() for r in results if r.get("ok") and r.get("symbol")}
    failed = [
        r
        for r in results
            if not r.get("ok") and str(r.get("symbol", "")).upper() not in ok_symbols
    ]
    min_required = max(0, int(len(active_symbols) * float(args.min_fresh_pct)))
    symbols_at_target = sum(1 for d in latest_dates if d >= target.isoformat())
    min_cache = math.ceil(len(active_symbols) * 0.95) if active_symbols else 0
    history_symbols = int(history_cache_result.get("symbols") or 0)
    history_latest = str(history_cache_result.get("latestPriceDate") or "")
    latest_price_date = max(latest_dates) if latest_dates else None
    same_day_ok = min_required <= 0 or symbols_at_target >= min_required
    refresh_clean = (
        len(attempted_symbols) > 0
        and len(ok_symbols) >= min_required
        and len(failed) <= max(5, math.floor(len(attempted_symbols) * 0.05))
    )
    cache_usable = (
        history_symbols >= min_cache
        and bool(latest_price_date and latest_price_date >= target.isoformat())
        and bool(history_latest and history_latest >= target.isoformat())
        and refresh_clean
    )
    usable_for_forecast = bool(same_day_ok or cache_usable)
    coverage_mode = "same_day_rows" if same_day_ok else ("successful_refresh_with_last_close" if cache_usable else "insufficient_usable_quotes")
    payload = {
        "updatedAt": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "updatedAtICT": datetime.now(ICT).strftime("%Y-%m-%d %H:%M:%S"),
        "targetDate": target.isoformat(),
        "symbolsTotal": len(active_symbols),
        "candidateSymbolsTotal": len(candidate_symbols),
        "inactiveSymbolsExcluded": len(inactive_post),
        "inactiveDaysThreshold": int(args.inactive_days),
        "inactiveSample": inactive_post[:30],
        "inactiveProbeAttempted": len(inactive_probe),
        "inactiveProbeSample": inactive_probe[:30],
        "symbolsAttempted": len(attempted_symbols),
        "symbolsUpdated": len(ok_symbols),
        "symbolsFailed": len(failed),
        "latestPriceDate": latest_price_date,
        "symbolsAtTargetOrNewer": symbols_at_target,
        "staleButUsableSymbols": max(0, len(active_symbols) - symbols_at_target),
        "minFreshSymbols": min_required,
        "minUsableCacheSymbols": min_cache,
        "usableForForecast": usable_for_forecast,
        "coverageMode": coverage_mode,
        "vnindex": vni_result,
        "historyCache": history_cache_result,
        "universe": universe_meta,
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
    if min_required > 0 and not usable_for_forecast:
        raise SystemExit("full universe price refresh did not reach enough usable quotes")


if __name__ == "__main__":
    main()
