from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
DASH = ROOT / "dashboard"
DASH.mkdir(exist_ok=True)


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


def main() -> None:
    full = pd.read_csv(OUT / "screening_full_results.csv")
    candidates = pd.read_csv(OUT / "screening_candidates.csv")
    summary = json.loads((OUT / "screening_summary.json").read_text(encoding="utf-8"))
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
