from __future__ import annotations

import json
import math
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


# vnstock imports vnstock.common.viz during symbol validation. A chart backend is
# irrelevant for this screener, so a local stub avoids blocking data retrieval.
stub = types.ModuleType("vnstock_chart")
for name in [
    "BarChart",
    "BoxplotChart",
    "CandleChart",
    "HeatmapChart",
    "LineChart",
    "ScatterChart",
]:
    setattr(stub, name, type(name, (), {}))
sys.modules.setdefault("vnstock_chart", stub)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CACHE = ROOT / ".cache"
OUT.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

AS_OF = date.today().isoformat()
START_HISTORY = "2026-01-01"
MODE = "conservative" if "--conservative" in sys.argv else "opportunity"


def cli_int(name: str, default: int) -> int:
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    try:
        return int(sys.argv[idx + 1])
    except Exception:
        return default


WORKERS = max(1, cli_int("--workers", 1))
LIMIT_PRELIM = max(0, cli_int("--limit-prelim", 0))
FILTERS = {
    "opportunity": {"market_cap_bil": 1_500, "avg_value_20d_bil": 5, "price_k": 5},
    "conservative": {"market_cap_bil": 3_000, "avg_value_20d_bil": 10, "price_k": 5},
}

BANK_INDUSTRY_KEYWORDS = ["Ngân hàng"]
SECURITIES_KEYWORDS = ["Chứng khoán"]
OIL_GAS_KEYWORDS = ["Dầu khí", "Dầu", "Khí"]
OIL_GAS_SYMBOLS = {
    "BSR",
    "PVD",
    "PVS",
    "PVT",
    "GAS",
    "PLX",
    "PVC",
    "PVB",
    "PVP",
    "OIL",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def safe_float(value):
    if value is None:
        return math.nan
    try:
        if pd.isna(value):
            return math.nan
    except TypeError:
        pass
    try:
        return float(value)
    except Exception:
        return math.nan


def classify(industry_name: str) -> str:
    name = str(industry_name or "")
    if any(k.lower() in name.lower() for k in BANK_INDUSTRY_KEYWORDS):
        return "bank"
    if any(k.lower() in name.lower() for k in SECURITIES_KEYWORDS):
        return "securities"
    if any(k.lower() in name.lower() for k in OIL_GAS_KEYWORDS):
        return "oil_gas"
    return "non_financial"


def get_universe() -> pd.DataFrame:
    path = CACHE / "universe.parquet"
    if path.exists():
        return pd.read_parquet(path)

    from vnstock import Listing

    listing = Listing(source="kbs")
    raw = listing.symbols_by_exchange("HOSE")
    universe = raw[
        (raw["type"] == "stock") & (raw["exchange"].isin(["HOSE", "HNX"]))
    ].drop_duplicates("symbol")

    try:
        industries = listing.symbols_by_industries()
    except Exception:
        industries = pd.DataFrame(columns=["symbol", "industry_code", "industry_name"])

    universe = universe.merge(industries, how="left", on="symbol")
    universe["sector_group"] = universe["industry_name"].map(classify)
    universe.loc[universe["symbol"].isin(OIL_GAS_SYMBOLS), "sector_group"] = "oil_gas"
    universe = universe.sort_values(["exchange", "symbol"]).reset_index(drop=True)
    universe.to_parquet(path, index=False)
    return universe


def get_price_board(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"price_board_{AS_OF}_all.parquet"
    if path.exists() and not force_refresh:
        return pd.read_parquet(path)

    from vnstock import Trading

    frames = []
    trader = Trading(source="kbs")
    for i in range(0, len(symbols), 80):
        chunk = symbols[i : i + 80]
        try:
            frames.append(trader.price_board(chunk, get_all=True))
        except Exception as exc:
            log(f"price_board chunk {i}-{i+len(chunk)} failed: {exc}")
        time.sleep(0.25)
    board = pd.concat(frames, ignore_index=True).drop_duplicates("symbol")
    board.to_parquet(path, index=False)
    return board


def fetch_history(symbol: str) -> pd.DataFrame | None:
    path = CACHE / "history" / f"{symbol}.parquet"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        return pd.read_parquet(path)
    try:
        from vnstock import Quote

        df = Quote(source="kbs", symbol=symbol).history(
            start=START_HISTORY, end=AS_OF, interval="1D"
        )
        if df is None or df.empty:
            return None
        df.to_parquet(path, index=False)
        return df
    except Exception:
        return None


def normalize_history_time(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"]).dt.tz_localize(None)
    return out


def incremental_history(symbol: str, board_row: pd.Series | None = None) -> pd.DataFrame | None:
    path = CACHE / "history" / f"{symbol}.parquet"
    if not path.exists():
        return fetch_history(symbol)
    try:
        df = normalize_history_time(pd.read_parquet(path))
        if board_row is not None:
            current = safe_float(board_row.get("close_price")) / 1000
            if not math.isfinite(current) or current <= 0:
                return df
            target_ts = pd.Timestamp(AS_OF)
            today_row = {
                "time": target_ts,
                "open": safe_float(board_row.get("open_price")) / 1000,
                "high": safe_float(board_row.get("high_price")) / 1000,
                "low": safe_float(board_row.get("low_price")) / 1000,
                "close": current,
                "volume": safe_float(board_row.get("volume_accumulated")),
            }
            for key in ["open", "high", "low"]:
                if not math.isfinite(today_row[key]) or today_row[key] <= 0:
                    today_row[key] = current
            if not math.isfinite(today_row["volume"]):
                today_row["volume"] = 0

            # Always refresh the latest cached session. The last candle can be
            # an intraday snapshot, so "last_date >= AS_OF" is not sufficient.
            latest_cached_date = df["time"].max().normalize()
            replacement_rows = [today_row]
            if latest_cached_date == target_ts.normalize():
                df = df[df["time"].dt.normalize() != target_ts.normalize()]
            elif latest_cached_date > target_ts.normalize():
                df = df[df["time"].dt.normalize() <= target_ts.normalize()]

            combined = pd.concat([df, pd.DataFrame(replacement_rows)], ignore_index=True)
            combined = normalize_history_time(combined)
            combined = combined.drop_duplicates("time", keep="last").sort_values("time")
            combined.to_parquet(path, index=False)
            return combined

        last_date = df["time"].max().date()
        target_date = date.fromisoformat(AS_OF)
        if last_date >= target_date:
            return df

        from vnstock import Quote

        start = (last_date + timedelta(days=1)).isoformat()
        new_df = Quote(source="kbs", symbol=symbol).history(
            start=start, end=AS_OF, interval="1D"
        )
        if new_df is not None and not new_df.empty:
            combined = pd.concat([df, normalize_history_time(new_df)], ignore_index=True)
            combined = combined.drop_duplicates("time").sort_values("time")
            combined.to_parquet(path, index=False)
            return combined
        return df
    except Exception:
        return pd.read_parquet(path) if path.exists() else None


def history_metric_row(symbol: str, df: pd.DataFrame | None) -> dict:
    row = {"symbol": symbol, "history_ok": False}
    if df is None or df.empty:
        return row
    df = normalize_history_time(df).sort_values("time")
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    value = close * df["volume"].astype(float) * 1000
    row.update(
        {
            "history_ok": True,
            "history_last_date": df["time"].max().date().isoformat(),
            "history_last_time": df["time"].max().isoformat(),
            "last_close": close.iloc[-1],
            "avg_value_20d": value.tail(20).mean(),
            "avg_volume_20d": df["volume"].tail(20).mean(),
            "sma20": close.tail(20).mean(),
            "sma50": close.tail(50).mean() if len(close) >= 50 else math.nan,
            "sma80": close.tail(80).mean() if len(close) >= 80 else math.nan,
            "atr20": true_range.tail(20).mean(),
            "support20": low.tail(20).min(),
            "resistance20": high.tail(20).max(),
            "return_20d": close.iloc[-1] / close.iloc[-21] - 1
            if len(close) > 21
            else math.nan,
            "return_60d": close.iloc[-1] / close.iloc[-61] - 1
            if len(close) > 61
            else math.nan,
            "volatility_20d": close.pct_change().tail(20).std() * math.sqrt(252),
        }
    )
    return row


def fetch_ratio(symbol: str) -> dict:
    path = CACHE / "ratio" / f"{symbol}.json"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    result = {"symbol": symbol, "ratio_ok": False}
    try:
        from vnstock import Finance

        df = Finance(source="kbs", symbol=symbol, period="quarter", get_all=True).ratio()
        if df is None or df.empty:
            raise ValueError("empty ratio")
        period_cols = [c for c in df.columns if c not in ["item", "item_id"]]
        latest = period_cols[0]
        result["latest_ratio_period"] = latest
        for _, row in df.iterrows():
            item_id = str(row.get("item_id"))
            result[item_id] = safe_float(row.get(latest))
        result["ratio_ok"] = True
    except Exception as exc:
        result["ratio_error"] = str(exc)[:240]

    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def enrich_history_metrics(symbols: list[str], max_workers: int = 1) -> pd.DataFrame:
    rows = []
    if max_workers <= 1:
        iterator = ((s, fetch_history(s)) for s in symbols)
    else:
        pool = ThreadPoolExecutor(max_workers=max_workers)
        future_map = {pool.submit(fetch_history, s): s for s in symbols}
        iterator = ((future_map[f], f.result()) for f in as_completed(future_map))

    for idx, (symbol, df) in enumerate(iterator, 1):
            row = history_metric_row(symbol, df)
            rows.append(row)
            if idx % 100 == 0:
                log(f"history {idx}/{len(symbols)}")
            if max_workers <= 1:
                time.sleep(3.2)
    if max_workers > 1:
        pool.shutdown()
    return pd.DataFrame(rows)


def enrich_ratios(symbols: list[str], max_workers: int = 1) -> pd.DataFrame:
    rows = []
    if max_workers <= 1:
        iterator = (fetch_ratio(s) for s in symbols)
        for idx, result in enumerate(iterator, 1):
            rows.append(result)
            if idx % 100 == 0:
                log(f"ratio {idx}/{len(symbols)}")
            time.sleep(3.2)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {pool.submit(fetch_ratio, s): s for s in symbols}
            for idx, future in enumerate(as_completed(future_map), 1):
                rows.append(future.result())
                if idx % 100 == 0:
                    log(f"ratio {idx}/{len(symbols)}")
    return pd.DataFrame(rows)


def percentile_score(series: pd.Series, high_good: bool = True) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    pct = s.rank(pct=True)
    if not high_good:
        pct = 1 - pct
    return (pct * 100).clip(0, 100)


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    listed_shares = pd.to_numeric(
        x.get("listed_shares", x.get("total_listed_qty")), errors="coerce"
    )
    x["market_cap_bil"] = (
        pd.to_numeric(x["close_price"], errors="coerce")
        * listed_shares
        / 1_000_000_000
    )
    x["current_price_k"] = pd.to_numeric(x["close_price"], errors="coerce") / 1000
    x["market_price_time"] = pd.to_numeric(x.get("time"), errors="coerce")
    x["current_value"] = pd.to_numeric(x["total_value"], errors="coerce")
    x["avg_value_20d_bil"] = pd.to_numeric(x["avg_value_20d"], errors="coerce") / 1e9
    x["price_vs_sma20"] = x["last_close"] / x["sma20"] - 1
    x["price_vs_sma50"] = x["last_close"] / x["sma50"] - 1
    x["atr20_k"] = pd.to_numeric(x.get("atr20"), errors="coerce")
    x["support20_k"] = pd.to_numeric(x.get("support20"), errors="coerce")

    # Some KBS ratio fields are quarter ROE/ROA; trailing values are better when available.
    x["roe_use"] = x["roe_trailling"].combine_first(x["roe"])
    x["roa_use"] = x["roa_trailling"].combine_first(x["roa"])
    x["earnings_growth_use"] = x[
        "profit_after_tax_for_shareholders_of_the_parent_company"
    ].combine_first(x.get("profit_before_tax"))

    thresholds = FILTERS[MODE]
    universe_mask = (
        (x["market_cap_bil"] >= thresholds["market_cap_bil"])
        & (x["avg_value_20d_bil"] >= thresholds["avg_value_20d_bil"])
        & (x["current_price_k"] >= thresholds["price_k"])
    )
    x["liquidity_pass"] = universe_mask

    x["quality_score"] = 0.0
    x["valuation_score"] = 0.0
    x["catalyst_score"] = 0.0
    x["technical_score"] = 0.0

    # Global percentile inputs. Sector-specific gates below determine eligibility.
    x["roe_score"] = percentile_score(x["roe_use"], True)
    x["roa_score"] = percentile_score(x["roa_use"], True)
    x["pe_score"] = percentile_score(x["pe_ratio"], False)
    x["pb_score"] = percentile_score(x["pb_ratio"], False)
    x["growth_score"] = percentile_score(x["earnings_growth_use"], True)
    x["margin_score"] = percentile_score(x.get("net_margin"), True)
    x["technical_raw"] = (
        (x["last_close"] > x["sma20"]).astype(float) * 35
        + (x["last_close"] > x["sma50"]).astype(float) * 30
        + (x["return_20d"].fillna(0).clip(-0.2, 0.2) + 0.2) / 0.4 * 20
        + (1 - x["volatility_20d"].fillna(0.5).clip(0, 0.8) / 0.8) * 15
    )

    x["technical_score"] = x["technical_raw"].clip(0, 100)

    for group, mask in x.groupby("sector_group").groups.items():
        idx = list(mask)
        if group == "bank":
            x.loc[idx, "quality_score"] = (
                x.loc[idx, "roe_score"] * 0.45
                + x.loc[idx, "roa_score"] * 0.25
                + percentile_score(x.loc[idx, "net_interest_margin_nim"], True) * 0.15
                + percentile_score(x.loc[idx, "cost_income_ratio_cir"], False) * 0.15
            )
            x.loc[idx, "valuation_score"] = (
                percentile_score(x.loc[idx, "pb_ratio"], False) * 0.55
                + percentile_score(x.loc[idx, "pe_ratio"], False) * 0.45
            )
            x.loc[idx, "catalyst_score"] = (
                percentile_score(x.loc[idx, "earnings_growth_use"], True) * 0.55
                + percentile_score(x.loc[idx, "deposits_from_customers"], True) * 0.25
                + percentile_score(x.loc[idx, "net_interest_income"], True) * 0.20
            )
        elif group == "oil_gas":
            x.loc[idx, "quality_score"] = (
                x.loc[idx, "roe_score"] * 0.35
                + x.loc[idx, "roa_score"] * 0.25
                + percentile_score(x.loc[idx, "debt_to_equity"], False) * 0.20
                + percentile_score(x.loc[idx, "interest_coverage"], True) * 0.20
            )
            x.loc[idx, "valuation_score"] = (
                percentile_score(x.loc[idx, "pe_ratio"], False) * 0.45
                + percentile_score(x.loc[idx, "pb_ratio"], False) * 0.25
                + percentile_score(x.loc[idx, "ev_ebit"], False) * 0.30
            )
            x.loc[idx, "catalyst_score"] = (
                percentile_score(x.loc[idx, "gross_profit"], True) * 0.45
                + percentile_score(x.loc[idx, "net_revenue"], True) * 0.25
                + percentile_score(x.loc[idx, "earnings_growth_use"], True) * 0.30
            )
        elif group == "securities":
            x.loc[idx, "quality_score"] = (
                x.loc[idx, "roe_score"] * 0.55
                + x.loc[idx, "roa_score"] * 0.20
                + percentile_score(x.loc[idx, "debt_to_equity"], False) * 0.25
            )
            x.loc[idx, "valuation_score"] = (
                percentile_score(x.loc[idx, "pe_ratio"], False) * 0.55
                + percentile_score(x.loc[idx, "pb_ratio"], False) * 0.45
            )
            x.loc[idx, "catalyst_score"] = (
                percentile_score(x.loc[idx, "earnings_growth_use"], True) * 0.50
                + percentile_score(x.loc[idx, "net_revenue"], True) * 0.25
                + percentile_score(x.loc[idx, "total_assets"], True) * 0.25
            )
        else:
            x.loc[idx, "quality_score"] = (
                x.loc[idx, "roe_score"] * 0.40
                + x.loc[idx, "roa_score"] * 0.20
                + x.loc[idx, "margin_score"] * 0.20
                + percentile_score(x.loc[idx, "debt_to_equity"], False) * 0.20
            )
            x.loc[idx, "valuation_score"] = (
                percentile_score(x.loc[idx, "pe_ratio"], False) * 0.45
                + percentile_score(x.loc[idx, "pb_ratio"], False) * 0.30
                + percentile_score(x.loc[idx, "ev_ebit"], False) * 0.25
            )
            x.loc[idx, "catalyst_score"] = (
                percentile_score(x.loc[idx, "earnings_growth_use"], True) * 0.45
                + percentile_score(x.loc[idx, "net_revenue"], True) * 0.25
                + percentile_score(x.loc[idx, "gross_profit"], True) * 0.30
            )

    x["composite_score"] = (
        x["quality_score"] * 0.30
        + x["valuation_score"] * 0.25
        + x["catalyst_score"] * 0.20
        + x["technical_score"] * 0.25
    )

    x["hard_gate"] = "PASS"
    ratio_ok = x["ratio_ok"].astype("boolean").fillna(False)
    history_ok = x["history_ok"].astype("boolean").fillna(False)
    x.loc[~ratio_ok, "hard_gate"] = "DATA_GAP_RATIO"
    x.loc[~history_ok, "hard_gate"] = "DATA_GAP_HISTORY"
    x.loc[~x["liquidity_pass"].fillna(False), "hard_gate"] = "FAIL_SIZE_LIQUIDITY"

    bank_gate = (
        (x["sector_group"] == "bank")
        & ((x["roe_use"] < 12) | (x["roa_use"] < 0.8))
        & (x["hard_gate"] == "PASS")
    )
    x.loc[bank_gate, "hard_gate"] = "FAIL_BANK_QUALITY"

    leverage_gate = (
        (x["sector_group"] == "non_financial")
        & (x["debt_to_equity"] > 400)
        & (x["hard_gate"] == "PASS")
    )
    x.loc[leverage_gate, "hard_gate"] = "FAIL_LEVERAGE"
    low_quality_overlay = (
        (x["sector_group"] == "non_financial")
        & (x["roe_use"] < 10)
        & (x["hard_gate"] == "PASS")
    )

    real_estate_overlay = (
        (x["industry_name"].astype(str).str.contains("Bất động sản", case=False, na=False))
        & (x["hard_gate"] == "PASS")
    )
    x.loc[real_estate_overlay, "qualitative_overlay"] = "REAL_ESTATE_NEEDS_BACKLOG_CORE_REVIEW"
    x.loc[~real_estate_overlay, "qualitative_overlay"] = ""
    x.loc[low_quality_overlay, "qualitative_overlay"] = (
        x.loc[low_quality_overlay, "qualitative_overlay"].where(
            x.loc[low_quality_overlay, "qualitative_overlay"].eq(""),
            x.loc[low_quality_overlay, "qualitative_overlay"] + "; ",
        )
        + "LOW_ROE_NEEDS_TURNAROUND_MEMO"
    )

    x["status"] = "AVOID"
    x.loc[(x["hard_gate"] == "PASS") & (x["composite_score"] >= 80), "status"] = "BUY"
    x.loc[
        (x["hard_gate"] == "PASS")
        & (x["composite_score"] >= 70)
        & (x["composite_score"] < 80),
        "status",
    ] = "ACCUMULATE"
    x.loc[
        (x["hard_gate"] == "PASS")
        & (x["composite_score"] >= 60)
        & (x["composite_score"] < 70),
        "status",
    ] = "WATCH"
    x.loc[real_estate_overlay & x["status"].isin(["BUY", "ACCUMULATE"]), "status"] = "WATCH"

    x["sleeve"] = x["sector_group"].map(
        {
            "bank": "Core Bank",
            "oil_gas": "Cyclical/Tactical",
            "securities": "FTSE/Securities Beta",
            "non_financial": "Quality/Value Non-financial",
        }
    )

    rr_min = x["sector_group"].map(
        {
            "bank": 2.0,
            "oil_gas": 2.5,
            "securities": 2.5,
            "non_financial": 2.0,
        }
    ).fillna(2.0)
    rr_min = rr_min.where(~x["industry_name"].astype(str).str.contains("Vận tải", case=False, na=False), 3.0)
    x["rr_min"] = rr_min
    fallback_stop_pct = x["sector_group"].map(
        {
            "bank": 0.09,
            "oil_gas": 0.13,
            "securities": 0.12,
            "non_financial": 0.10,
        }
    ).fillna(0.10)
    stop_atr = x["current_price_k"] - 2.2 * x["atr20_k"]
    stop_support = x["support20_k"] * 0.98
    stop_fallback = x["current_price_k"] * (1 - fallback_stop_pct)
    x["stop_price_k"] = pd.concat([stop_atr, stop_support, stop_fallback], axis=1).min(axis=1)
    x["stop_price_k"] = x["stop_price_k"].where(x["stop_price_k"] > 0, stop_fallback)
    x["target_price_k"] = x["current_price_k"] + rr_min * (x["current_price_k"] - x["stop_price_k"])
    x["upside_pct"] = x["target_price_k"] / x["current_price_k"] - 1
    x["downside_pct"] = 1 - x["stop_price_k"] / x["current_price_k"]
    x["risk_reward"] = x["upside_pct"] / x["downside_pct"]
    x["buy_zone_low_k"] = pd.concat([x["support20_k"], x["current_price_k"] * 0.98], axis=1).max(axis=1)
    x["buy_zone_high_k"] = x["current_price_k"] * 1.02
    x["action_note"] = "Memo required before trade"
    x.loc[x["status"].eq("BUY"), "action_note"] = "Buy in tranches if price remains in buy zone"
    x.loc[x["status"].eq("ACCUMULATE"), "action_note"] = "Accumulate on pullback / confirmation"
    x.loc[x["status"].eq("WATCH"), "action_note"] = "Watch; validate catalyst and core earnings"
    return x


def main() -> None:
    use_existing_cache = "--score-cache-only" in sys.argv
    incremental_fast = "--incremental-history" in sys.argv
    force_price_board = use_existing_cache or "--refresh-price-board" in sys.argv

    log("loading universe")
    universe = get_universe()
    symbols = universe["symbol"].tolist()
    log(f"universe HOSE/HNX stock symbols: {len(symbols)}")

    log("loading price board")
    board = get_price_board(symbols, force_refresh=force_price_board)

    board_pre = board.copy()
    board_pre["listed_shares_num"] = pd.to_numeric(
        board_pre.get("listed_shares", board_pre.get("total_listed_qty")),
        errors="coerce",
    )
    board_pre["market_cap_bil_pre"] = (
        pd.to_numeric(board_pre["close_price"], errors="coerce")
        * board_pre["listed_shares_num"]
        / 1_000_000_000
    )
    board_pre["current_value_bil_pre"] = (
        pd.to_numeric(board_pre["total_value"], errors="coerce") / 1e9
    )
    board_pre["current_price_k_pre"] = pd.to_numeric(
        board_pre["close_price"], errors="coerce"
    ) / 1000
    thresholds = FILTERS[MODE]
    prelim = board_pre[
        (board_pre["market_cap_bil_pre"] >= thresholds["market_cap_bil"])
        & (board_pre["current_value_bil_pre"] >= thresholds["avg_value_20d_bil"])
        & (board_pre["current_price_k_pre"] >= thresholds["price_k"])
    ]["symbol"].dropna().drop_duplicates().tolist()
    log(
        f"mode={MODE}; precheck pass by current market cap/liquidity: "
        f"{len(prelim)}/{len(symbols)}"
    )
    if LIMIT_PRELIM:
        prelim = prelim[:LIMIT_PRELIM]
        log(f"limit-prelim active: scoring first {len(prelim)} symbols")

    log(f"loading price history for precheck pass symbols (workers={WORKERS})")
    if use_existing_cache:
        rows = []
        for idx, symbol in enumerate(prelim, 1):
            path = CACHE / "history" / f"{symbol}.parquet"
            if incremental_fast:
                match = board[board["symbol"].eq(symbol)]
                board_row = match.iloc[0] if not match.empty else None
                dfh = incremental_history(symbol, board_row)
                rows.append(history_metric_row(symbol, dfh))
            elif path.exists():
                rows.append(history_metric_row(symbol, pd.read_parquet(path)))
            else:
                rows.append({"symbol": symbol, "history_ok": False})
            if incremental_fast and idx % 25 == 0:
                log(f"incremental history {idx}/{len(prelim)}")
        hist_metrics = pd.DataFrame(rows)
    else:
        hist_metrics = enrich_history_metrics(prelim, max_workers=WORKERS)

    log(f"loading financial ratios for precheck pass symbols (workers={WORKERS})")
    if use_existing_cache:
        rows = []
        for symbol in prelim:
            path = CACHE / "ratio" / f"{symbol}.json"
            if path.exists():
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            else:
                rows.append({"symbol": symbol, "ratio_ok": False, "ratio_error": "cache_missing"})
        ratios = pd.DataFrame(rows)
    else:
        ratios = enrich_ratios(prelim, max_workers=WORKERS)

    log("scoring")
    df = universe.merge(board, on="symbol", how="left", suffixes=("", "_board"))
    df = df.merge(hist_metrics, on="symbol", how="left")
    df = df.merge(ratios, on="symbol", how="left")
    scored = compute_scores(df)

    cols = [
        "symbol",
        "organ_name",
        "exchange",
        "industry_name",
        "sector_group",
        "sleeve",
        "status",
        "hard_gate",
        "qualitative_overlay",
        "composite_score",
        "quality_score",
        "valuation_score",
        "catalyst_score",
        "technical_score",
        "market_cap_bil",
        "avg_value_20d_bil",
        "close_price",
        "current_price_k",
        "target_price_k",
        "stop_price_k",
        "buy_zone_low_k",
        "buy_zone_high_k",
        "upside_pct",
        "downside_pct",
        "risk_reward",
        "rr_min",
        "atr20_k",
        "support20_k",
        "history_last_date",
        "history_last_time",
        "action_note",
        "market_price_time",
        "pe_ratio",
        "pb_ratio",
        "roe_use",
        "roa_use",
        "earnings_growth_use",
        "price_vs_sma20",
        "price_vs_sma50",
        "return_20d",
        "volatility_20d",
        "latest_ratio_period",
    ]
    existing_cols = [c for c in cols if c in scored.columns]
    scored[existing_cols].sort_values(
        ["status", "composite_score"], ascending=[True, False]
    ).to_csv(OUT / "screening_full_results.csv", index=False, encoding="utf-8-sig")

    top = scored[scored["status"].isin(["BUY", "ACCUMULATE"])].sort_values(
        "composite_score", ascending=False
    )
    top[existing_cols].to_csv(OUT / "screening_candidates.csv", index=False, encoding="utf-8-sig")
    scored.to_parquet(OUT / "screening_full_results.parquet", index=False)

    summary = {
        "as_of": AS_OF,
        "mode": MODE,
        "filters": FILTERS[MODE],
        "universe_count": int(len(scored)),
        "exchange_counts": scored["exchange"].value_counts(dropna=False).to_dict(),
        "hard_gate_counts": scored["hard_gate"].value_counts(dropna=False).to_dict(),
        "status_counts": scored["status"].value_counts(dropna=False).to_dict(),
        "buy_accumulate_count": int(len(top)),
        "generated_at": date.today().isoformat(),
    }
    (OUT / "screening_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log(json.dumps(summary, ensure_ascii=False))
    log("done")


if __name__ == "__main__":
    main()
