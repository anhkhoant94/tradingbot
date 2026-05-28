from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
BACKTEST_CACHE = ROOT / ".cache" / "backtest"
STATUS_PATH = OUT / "dashboard_live_update_status.json"
POLICY_DIR = OUT / "dashboard_policies" / "r46_bear_stop_mcore"

PRICE_DIRS = [
    ROOT / ".cache" / "history",
    BACKTEST_CACHE / "history",
    BACKTEST_CACHE / "history_clean",
]

CP68_DIR = BACKTEST_CACHE / "cp68"


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False, engine="fastparquet")
    except Exception:
        df.to_parquet(path, index=False)


def last_cache_date(symbol: str) -> date | None:
    dates = []
    for directory in PRICE_DIRS:
        path = directory / f"{symbol}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty:
            continue
        col = "time" if "time" in df.columns else "date"
        dates.append(pd.to_datetime(df[col]).max().date())
    return max(dates) if dates else None


def fetch_vps_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    fr = int(pd.Timestamp(start).timestamp())
    to = int(pd.Timestamp(end + timedelta(days=1)).timestamp())
    url = (
        "https://histdatafeed.vps.com.vn/tradingview/history"
        f"?symbol={symbol}&resolution=D&from={fr}&to={to}"
    )
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if payload.get("s") != "ok" or not payload.get("t"):
        return pd.DataFrame()
    times = pd.Series(pd.to_datetime(payload["t"], unit="s")).dt.tz_localize(None)
    df = pd.DataFrame(
        {
            "time": times,
            "open": pd.to_numeric(payload["o"], errors="coerce"),
            "high": pd.to_numeric(payload["h"], errors="coerce"),
            "low": pd.to_numeric(payload["l"], errors="coerce"),
            "close": pd.to_numeric(payload["c"], errors="coerce"),
            "volume": pd.to_numeric(payload["v"], errors="coerce"),
        }
    )
    df = df.dropna(subset=["time", "close"])
    if df.empty:
        return df
    stock_like = symbol.upper() != "VNINDEX"
    median_close = float(df["close"].median())
    if stock_like and median_close > 1000:
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] / 1000.0
    return df.sort_values("time").reset_index(drop=True)


def normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    out = df.copy()
    col = "time" if "time" in out.columns else "date"
    out["time"] = pd.to_datetime(out[col]).dt.tz_localize(None).dt.normalize()
    for price_col in ["open", "high", "low", "close"]:
        out[price_col] = pd.to_numeric(out.get(price_col), errors="coerce")
    out["volume"] = pd.to_numeric(out.get("volume"), errors="coerce").fillna(0)
    out = out[["time", "open", "high", "low", "close", "volume"]]
    out = out[out["close"].gt(0)]
    for price_col in ["open", "high", "low"]:
        out[price_col] = out[price_col].where(out[price_col].gt(0), out["close"])
    return out.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)


def merge_price_cache(symbol: str, fresh: pd.DataFrame) -> dict:
    if fresh.empty:
        return {"symbol": symbol, "ok": False, "reason": "no_data"}
    latest = None
    rows = 0
    for directory in PRICE_DIRS:
        path = directory / f"{symbol}.parquet"
        old = pd.DataFrame()
        if path.exists():
            try:
                old = pd.read_parquet(path)
            except Exception:
                old = pd.DataFrame()
        combined = normalize_price_frame(pd.concat([old, fresh], ignore_index=True))
        write_parquet(combined, path)
        if not combined.empty:
            latest = combined["time"].max().date().isoformat()
            rows = len(combined)
    return {"symbol": symbol, "ok": True, "latest": latest, "rows": rows}


def update_symbol_price(symbol: str) -> dict:
    last = last_cache_date(symbol)
    today = date.today()
    start = (last - timedelta(days=8)) if last else date(2020, 1, 1)
    try:
        fresh = fetch_vps_daily(symbol, start, today)
        return merge_price_cache(symbol, fresh)
    except Exception as exc:
        return {"symbol": symbol, "ok": False, "reason": str(exc)[:160]}


def update_vnindex() -> dict:
    today = date.today()
    path = BACKTEST_CACHE / "vnindex_daily.parquet"
    start = date(2016, 1, 1)
    if path.exists():
        try:
            current = pd.read_parquet(path)
            if not current.empty:
                start = pd.to_datetime(current["date"]).max().date() - timedelta(days=8)
        except Exception:
            pass
    try:
        fresh = fetch_vps_daily("VNINDEX", start, today)
        if fresh.empty:
            return {"symbol": "VNINDEX", "ok": False, "reason": "no_data"}
        fresh = fresh.rename(columns={"time": "date"})[["date", "close"]]
        frames = []
        for p in [BACKTEST_CACHE / "vnindex_daily.parquet", BACKTEST_CACHE / "vnindex_daily_v6.parquet"]:
            old = pd.DataFrame()
            if p.exists():
                try:
                    old = pd.read_parquet(p)
                except Exception:
                    old = pd.DataFrame()
            combined = pd.concat([old, fresh], ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"]).dt.tz_localize(None).dt.normalize()
            combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
            combined = combined.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")
            write_parquet(combined, p)
            frames.append(combined)
        latest = frames[-1]["date"].max().date().isoformat() if frames else None
        return {"symbol": "VNINDEX", "ok": True, "latest": latest, "rows": len(frames[-1]) if frames else 0}
    except Exception as exc:
        return {"symbol": "VNINDEX", "ok": False, "reason": str(exc)[:160]}


def read_symbols() -> list[str]:
    symbols: list[str] = []

    holdings_path = POLICY_DIR / "holdings.parquet"
    if holdings_path.exists():
        try:
            holdings = pd.read_parquet(holdings_path)
            holdings["date"] = pd.to_datetime(holdings["date"], errors="coerce")
            latest = holdings["date"].max()
            for sym in holdings.loc[holdings["date"].eq(latest), "symbol"].astype(str).str.upper():
                if sym and sym not in symbols:
                    symbols.append(sym)
        except Exception:
            pass

    trades_path = POLICY_DIR / "trades.parquet"
    if trades_path.exists():
        try:
            trades = pd.read_parquet(trades_path)
            trades["date"] = pd.to_datetime(trades["date"], errors="coerce")
            latest = trades["date"].max()
            for sym in trades.loc[trades["date"].eq(latest), "symbol"].astype(str).str.upper():
                if sym and sym not in symbols:
                    symbols.append(sym)
        except Exception:
            pass

    portfolio_path = OUT / "portfolio_holdings.json"
    if portfolio_path.exists():
        try:
            payload = json.loads(portfolio_path.read_text(encoding="utf-8"))
            for row in payload.get("holdings", []):
                sym = str(row.get("symbol", "")).upper().strip()
                if sym and sym not in symbols:
                    symbols.append(sym)
        except Exception:
            pass

    deep_path = OUT / "deep_analysis.json"
    if deep_path.exists():
        try:
            payload = json.loads(deep_path.read_text(encoding="utf-8"))
            planned = payload.get("plannedOrders") or {}
            for row in planned.get("rows", []):
                sym = str(row.get("symbol", "")).upper().strip()
                if sym and sym not in symbols:
                    symbols.append(sym)
        except Exception:
            pass

    return [sym for sym in symbols if sym and sym != "VNINDEX"][:16]


def update_bctc(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    try:
        from backtest.cp68_scraper import fetch_quarterly_bctc
    except Exception as exc:
        return [{"symbol": "*", "ok": False, "reason": f"import_failed:{exc}"}]

    def fetch_one(sym: str) -> dict:
        started = time.time()
        try:
            df = fetch_quarterly_bctc(
                sym,
                cache_dir=CP68_DIR,
                force=True,
                timeout=8,
                delay_s=0,
                max_retries=1,
            )
            return {
                "symbol": sym,
                "ok": df is not None and not df.empty,
                "rows": 0 if df is None else len(df),
                "seconds": round(time.time() - started, 2),
            }
        except Exception as exc:
            return {"symbol": sym, "ok": False, "reason": str(exc)[:120], "seconds": round(time.time() - started, 2)}

    results = []
    with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
        futures = [pool.submit(fetch_one, sym) for sym in symbols[:8]]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["symbol"])


def finite_latest(results: list[dict]) -> str | None:
    dates = [r.get("latest") for r in results if r.get("ok") and r.get("latest")]
    return max(dates) if dates else None


def main() -> None:
    started = time.time()
    OUT.mkdir(exist_ok=True)
    symbols = read_symbols()
    price_results = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(symbols)))) as pool:
        futures = [pool.submit(update_symbol_price, sym) for sym in symbols]
        for future in as_completed(futures):
            price_results.append(future.result())
    price_results = sorted(price_results, key=lambda item: item["symbol"])
    vni_result = update_vnindex()
    bctc_results = update_bctc(symbols)
    payload = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - started, 2),
        "symbols": symbols,
        "latestPriceDate": finite_latest(price_results),
        "prices": price_results,
        "vnindex": vni_result,
        "bctc": bctc_results,
    }
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_prices = sum(1 for row in price_results if row.get("ok"))
    ok_bctc = sum(1 for row in bctc_results if row.get("ok"))
    print(
        f"Dashboard live update: prices {ok_prices}/{len(price_results)}, "
        f"BCTC {ok_bctc}/{len(bctc_results)}, latest {payload['latestPriceDate']}, "
        f"{payload['seconds']}s"
    )
    if any(not row.get("ok") for row in price_results):
        failed = [f"{row['symbol']}:{row.get('reason', 'fail')}" for row in price_results if not row.get("ok")]
        print("Price warnings: " + "; ".join(failed))


if __name__ == "__main__":
    main()
