from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
DASH = ROOT / "dashboard"
DASH.mkdir(exist_ok=True)
HISTORY_CLEAN = ROOT / ".cache" / "backtest" / "history_clean"


def safe_num(value, default=math.nan) -> float:
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return float(value)
    except Exception:
        return default


def load_liquidity_fallback(year: int) -> dict[str, float]:
    """Fallback avg 20D liquidity (bil VND) for symbols missing in screening_full_results.

    Use the latest non-null yearly_clean_coverage value at or before `year`.
    Source `avg_value` is thousand VND, so convert to bil VND via /1e6.
    """
    path = OUT / "phase28_data_quality" / "yearly_clean_coverage.csv"
    if not path.exists():
        return {}
    cov = pd.read_csv(path)
    if not {"symbol", "year", "avg_value"}.issubset(cov.columns):
        return {}
    cov["year_num"] = pd.to_numeric(cov["year"], errors="coerce")
    cov = cov[cov["year_num"].notna() & (cov["year_num"] <= float(year))].copy()
    if cov.empty:
        return {}
    cov["avg_value_num"] = pd.to_numeric(cov["avg_value"], errors="coerce")
    cov = cov[cov["avg_value_num"].notna() & (cov["avg_value_num"] > 0)].copy()
    if cov.empty:
        return {}
    cov = cov.sort_values(["symbol", "year_num"]).groupby("symbol", as_index=False).tail(1)
    cov["avg_value_bil_fallback"] = cov["avg_value_num"] / 1_000_000.0
    return {str(r.symbol).upper(): float(r.avg_value_bil_fallback) for _, r in cov.iterrows()}


def clean_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    available = [c for c in cols if c in df.columns]
    data = df[available].copy()
    for col in data.columns:
        if data[col].dtype == "float64":
            data[col] = data[col].round(4)
    return [
        {key: clean_value(value) for key, value in row.items()}
        for row in data.to_dict(orient="records")
    ]


def latest_live_status() -> dict:
    for path in [
        DASH / "full_universe_live_update_status.json",
        OUT / "full_universe_live_update_status.json",
        DASH / "dashboard_live_update_status.json",
        OUT / "dashboard_live_update_status.json",
    ]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
    return {}


def history_metrics(symbol: str) -> dict | None:
    path = HISTORY_CLEAN / f"{symbol}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty:
        return None
    tcol = "time" if "time" in df.columns else "date"
    df = df.copy()
    df["time"] = pd.to_datetime(df[tcol], errors="coerce").dt.tz_localize(None).dt.normalize()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df = df.dropna(subset=["time", "close"])
    df = df[df["close"].gt(0)].sort_values("time")
    if df.empty:
        return None
    close = df["close"].astype(float)
    high = df["high"].where(df["high"].gt(0), close).astype(float)
    low = df["low"].where(df["low"].gt(0), close).astype(float)
    volume = df["volume"].fillna(0).astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    latest = df.iloc[-1]
    return {
        "history_last_date": latest["time"].date().isoformat(),
        "history_last_time": latest["time"].isoformat(),
        "current_price_k": float(close.iloc[-1]),
        "close_price": float(close.iloc[-1] * 1000.0),
        "avg_value_20d_bil": float((close * volume * 1000.0).tail(20).mean() / 1e9),
        "atr20_k": float(true_range.tail(20).mean()),
        "support20_k": float(low.tail(20).min()),
        "price_vs_sma20": float(close.iloc[-1] / close.tail(20).mean() - 1) if len(close) >= 20 and close.tail(20).mean() else math.nan,
        "price_vs_sma50": float(close.iloc[-1] / close.tail(50).mean() - 1) if len(close) >= 50 and close.tail(50).mean() else math.nan,
        "return_20d": float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 and close.iloc[-21] else math.nan,
        "volatility_20d": float(close.pct_change().tail(20).std() * math.sqrt(252)),
    }


def fallback_stop_pct(row: pd.Series) -> float:
    sector = str(row.get("sector_group") or "")
    if sector == "bank":
        return 0.09
    if sector == "oil_gas":
        return 0.13
    if sector == "securities":
        return 0.12
    return 0.10


def recompute_price_sensitive_fields(full: pd.DataFrame, summary: dict) -> tuple[pd.DataFrame, dict]:
    status = latest_live_status()
    live_as_of = str(status.get("latestPriceDate") or status.get("targetDate") or "")
    base_as_of = str(summary.get("as_of") or "")
    if not live_as_of or live_as_of <= base_as_of:
        return full, {"applied": False, "reason": "live_not_newer", "liveAsOf": live_as_of, "baseAsOf": base_as_of}

    x = full.copy()
    updated = 0
    for idx, row in x.iterrows():
        sym = str(row.get("symbol") or "").upper().strip()
        metrics = history_metrics(sym)
        if not metrics:
            continue
        for key, value in metrics.items():
            x.at[idx, key] = value

        current = safe_num(metrics.get("current_price_k"))
        old_current = safe_num(row.get("current_price_k"))
        old_market_cap = safe_num(row.get("market_cap_bil"))
        if math.isfinite(current) and current > 0 and math.isfinite(old_current) and old_current > 0 and math.isfinite(old_market_cap):
            x.at[idx, "market_cap_bil"] = old_market_cap * current / old_current

        rr_min = safe_num(row.get("rr_min"), 2.0)
        if not math.isfinite(rr_min) or rr_min <= 0:
            rr_min = 2.0
        atr20 = safe_num(metrics.get("atr20_k"))
        support20 = safe_num(metrics.get("support20_k"))
        stop_candidates = []
        if math.isfinite(current) and current > 0:
            stop_candidates.append(current * (1.0 - fallback_stop_pct(row)))
        if math.isfinite(atr20):
            stop_candidates.append(current - 2.2 * atr20)
        if math.isfinite(support20):
            stop_candidates.append(support20 * 0.98)
        stop_candidates = [v for v in stop_candidates if math.isfinite(v) and v > 0]
        if current > 0 and stop_candidates:
            stop = min(stop_candidates)
            target = current + rr_min * (current - stop)
            x.at[idx, "stop_price_k"] = stop
            x.at[idx, "target_price_k"] = target
            x.at[idx, "upside_pct"] = target / current - 1
            x.at[idx, "downside_pct"] = 1 - stop / current
            x.at[idx, "risk_reward"] = (target / current - 1) / (1 - stop / current) if stop < current else math.nan
            x.at[idx, "buy_zone_low_k"] = max(support20 if math.isfinite(support20) else current * 0.98, current * 0.98)
            x.at[idx, "buy_zone_high_k"] = current * 1.02

        ret20 = safe_num(metrics.get("return_20d"), 0.0)
        vol20 = safe_num(metrics.get("volatility_20d"), 0.5)
        sma20 = safe_num(metrics.get("price_vs_sma20"))
        sma50 = safe_num(metrics.get("price_vs_sma50"))
        technical = (
            (35.0 if math.isfinite(sma20) and sma20 > 0 else 0.0)
            + (30.0 if math.isfinite(sma50) and sma50 > 0 else 0.0)
            + ((min(max(ret20, -0.2), 0.2) + 0.2) / 0.4) * 20.0
            + (1.0 - min(max(vol20, 0.0), 0.8) / 0.8) * 15.0
        )
        x.at[idx, "technical_score"] = max(0.0, min(100.0, technical))
        quality = safe_num(row.get("quality_score"), 0.0)
        valuation = safe_num(row.get("valuation_score"), 0.0)
        catalyst = safe_num(row.get("catalyst_score"), 0.0)
        composite = quality * 0.30 + valuation * 0.25 + catalyst * 0.20 + x.at[idx, "technical_score"] * 0.25
        x.at[idx, "composite_score"] = composite

        thresholds = summary.get("filters") or {}
        liq_ok = safe_num(x.at[idx, "avg_value_20d_bil"]) >= safe_num(thresholds.get("avg_value_20d_bil"), 5.0)
        cap_ok = safe_num(x.at[idx, "market_cap_bil"]) >= safe_num(thresholds.get("market_cap_bil"), 1500.0)
        price_ok = current >= safe_num(thresholds.get("price_k"), 5.0)
        old_gate = str(row.get("hard_gate") or "PASS")
        if not (liq_ok and cap_ok and price_ok):
            gate = "FAIL_SIZE_LIQUIDITY"
        elif old_gate == "FAIL_SIZE_LIQUIDITY":
            gate = "PASS"
        else:
            gate = old_gate
        x.at[idx, "hard_gate"] = gate

        status_label = "AVOID"
        if gate == "PASS" and composite >= 80:
            status_label = "BUY"
        elif gate == "PASS" and composite >= 70:
            status_label = "ACCUMULATE"
        elif gate == "PASS" and composite >= 60:
            status_label = "WATCH"
        industry = str(row.get("industry_name") or "")
        if "Bất động sản" in industry and status_label in {"BUY", "ACCUMULATE"}:
            status_label = "WATCH"
        x.at[idx, "status"] = status_label
        if status_label == "BUY":
            x.at[idx, "action_note"] = "Buy in tranches if price remains in buy zone"
        elif status_label == "ACCUMULATE":
            x.at[idx, "action_note"] = "Accumulate on pullback / confirmation"
        elif status_label == "WATCH":
            x.at[idx, "action_note"] = "Watch; validate catalyst and core earnings"
        else:
            x.at[idx, "action_note"] = "Memo required before trade"
        updated += 1

    overlay = {
        "applied": updated > 0,
        "source": ".cache/backtest/history_clean",
        "baseAsOf": base_as_of,
        "liveAsOf": live_as_of,
        "updatedSymbols": updated,
    }
    if updated:
        summary["base_as_of"] = base_as_of
        summary["as_of"] = live_as_of
        summary["generated_at"] = live_as_of
        summary["live_overlay"] = overlay
        summary["hard_gate_counts"] = x["hard_gate"].fillna("UNKNOWN").value_counts().to_dict()
        summary["status_counts"] = x["status"].fillna("UNKNOWN").value_counts().to_dict()
        summary["buy_accumulate_count"] = int(x["status"].isin(["BUY", "ACCUMULATE"]).sum())
    return x, overlay


def main() -> None:
    full = pd.read_csv(OUT / "screening_full_results.csv")
    summary = json.loads((OUT / "screening_summary.json").read_text(encoding="utf-8"))
    full, overlay = recompute_price_sensitive_fields(full, summary)
    candidates = full[full["status"].isin(["BUY", "ACCUMULATE"])].sort_values(
        "composite_score", ascending=False
    )
    as_of = str(summary.get("as_of") or "")
    as_of_year = int(as_of[:4]) if len(as_of) >= 4 and as_of[:4].isdigit() else None
    if as_of_year:
        liq_fallback = load_liquidity_fallback(as_of_year)
        if "avg_value_20d_bil" in full.columns and "symbol" in full.columns:
            liq_num = pd.to_numeric(full["avg_value_20d_bil"], errors="coerce")
            mask = liq_num.isna() | (liq_num <= 0)
            if mask.any():
                fallback_liq = pd.to_numeric(
                    full.loc[mask, "symbol"].map(lambda s: liq_fallback.get(str(s).upper())),
                    errors="coerce",
                )
                full.loc[mask, "avg_value_20d_bil"] = fallback_liq.where(fallback_liq.notna(), liq_num.loc[mask])
        if "history_last_date" in full.columns and "current_price_k" in full.columns:
            current_price_num = pd.to_numeric(full["current_price_k"], errors="coerce")
            missing_hist = full["history_last_date"].isna() | (full["history_last_date"].astype(str).str.strip() == "")
            full.loc[missing_hist & current_price_num.gt(0), "history_last_date"] = as_of

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
    ]

    watch = full[full["status"].eq("WATCH")].sort_values(
        "composite_score", ascending=False
    )
    top_all = full.sort_values("composite_score", ascending=False).head(40)

    gate_counts = (
        full["hard_gate"].fillna("UNKNOWN").value_counts().reset_index().values.tolist()
    )
    status_counts = (
        full["status"].fillna("UNKNOWN").value_counts().reset_index().values.tolist()
    )
    sector_counts = (
        full[full["hard_gate"].eq("PASS")]
        .groupby("sector_group")["symbol"]
        .count()
        .sort_values(ascending=False)
        .reset_index()
        .values.tolist()
    )

    payload = {
        "summary": summary,
        "mode": summary.get("mode", "opportunity"),
        "filters": summary.get("filters", {}),
        "candidates": records(candidates, cols),
        "watch": records(watch, cols),
        "topAll": records(top_all, cols),
        "all": records(full, cols),
        "gateCounts": [{"name": k, "value": int(v)} for k, v in gate_counts],
        "statusCounts": [{"name": k, "value": int(v)} for k, v in status_counts],
        "sectorCounts": [{"name": k, "value": int(v)} for k, v in sector_counts],
    }

    content = "window.SCREENING_DASHBOARD_DATA = "
    content += json.dumps(payload, ensure_ascii=False, indent=2)
    content += ";\n"
    (DASH / "data.js").write_text(content, encoding="utf-8")
    print(f"Wrote {DASH / 'data.js'}")


if __name__ == "__main__":
    main()
