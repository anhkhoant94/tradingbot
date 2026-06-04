from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / ".cache" / "backtest" / "history_clean"
UNIVERSE = ROOT / ".cache" / "universe.parquet"
VNI_PATH = ROOT / ".cache" / "backtest" / "vnindex_daily.parquet"
OUT = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab"

FACTORS = [
    "rs_13w",
    "rs_26w",
    "momentum_accel_13_26",
    "high52_proximity",
    "vol_contraction",
    "breakout_quality_100d",
    "pullback_quality",
    "volume_expansion_20_60",
    "downside_resilience",
]
TARGETS = ["fwd_4w", "fwd_8w"]


def load_vni_weekly() -> pd.DataFrame:
    vni = pd.read_parquet(VNI_PATH).sort_values("date")
    vni["date"] = pd.to_datetime(vni["date"])
    daily = vni.set_index("date")["close"].astype(float)
    weekly = daily.resample("W-FRI").last().dropna().to_frame("vni_close").reset_index()
    weekly["vni_ret_13w"] = weekly["vni_close"].pct_change(13)
    weekly["vni_ret_26w"] = weekly["vni_close"].pct_change(26)

    def regime(value):
        if pd.isna(value):
            return "unknown"
        if value < -0.10:
            return "bear"
        if value > 0.10:
            return "bull"
        return "range"

    weekly["vni_regime"] = weekly["vni_ret_13w"].map(regime)
    return weekly


def symbol_weekly(symbol: str, vni_weekly: pd.DataFrame) -> pd.DataFrame:
    path = HISTORY_DIR / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return pd.DataFrame()
    date_col = "time" if "time" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).drop_duplicates(date_col).set_index(date_col)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            return pd.DataFrame()
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    if len(df) < 260:
        return pd.DataFrame()

    ret = df["close"].pct_change()
    df["ret_13w"] = df["close"] / df["close"].shift(65) - 1
    df["ret_26w"] = df["close"] / df["close"].shift(130) - 1
    df["high252"] = df["close"].rolling(252, min_periods=126).max()
    df["high100_prior"] = df["close"].rolling(100, min_periods=50).max().shift(1)
    df["vol20"] = ret.rolling(20, min_periods=15).std()
    df["vol100"] = ret.rolling(100, min_periods=50).std()
    df["sma20"] = df["close"].rolling(20, min_periods=15).mean()
    df["sma50"] = df["close"].rolling(50, min_periods=25).mean()
    df["sma100"] = df["close"].rolling(100, min_periods=50).mean()
    df["sma200"] = df["close"].rolling(200, min_periods=100).mean()
    df["vol60"] = df["volume"].rolling(60, min_periods=30).mean()
    df["value_bil"] = df["close"] * df["volume"] / 1_000_000.0
    df["avg_value_20d_bil"] = df["value_bil"].rolling(20, min_periods=10).mean()

    weekly = df.resample("W-FRI").last().dropna(subset=["close"]).reset_index().rename(columns={date_col: "date"})
    weekly = pd.merge_asof(
        weekly.sort_values("date"),
        vni_weekly.sort_values("date"),
        on="date",
        direction="backward",
    )
    weekly["symbol"] = symbol
    weekly["rs_13w"] = weekly["ret_13w"] - weekly["vni_ret_13w"]
    weekly["rs_26w"] = weekly["ret_26w"] - weekly["vni_ret_26w"]
    weekly["momentum_accel_13_26"] = weekly["ret_13w"] - weekly["ret_26w"]
    weekly["high52_proximity"] = weekly["close"] / weekly["high252"]
    weekly["vol_contraction"] = -(weekly["vol20"] / weekly["vol100"])
    vol_ratio = weekly["volume"] / weekly["vol60"]
    weekly["breakout_quality_100d"] = (weekly["close"] / weekly["high100_prior"] - 1.0) + 0.05 * vol_ratio.clip(0, 5)
    trend_ok = (weekly["close"] > weekly["sma100"]) & (weekly["sma100"] > weekly["sma200"])
    near_sma20 = -(weekly["close"] / weekly["sma20"] - 1.0).abs()
    weekly["pullback_quality"] = near_sma20.where(trend_ok, -1.0)
    weekly["volume_expansion_20_60"] = weekly["volume"] / weekly["vol60"] - 1.0
    weekly["downside_resilience"] = weekly["rs_13w"].where(weekly["vni_ret_13w"] < 0, weekly["rs_26w"])
    weekly["fwd_4w"] = weekly["close"].shift(-4) / weekly["close"] - 1
    weekly["fwd_8w"] = weekly["close"].shift(-8) / weekly["close"] - 1
    weekly["year"] = weekly["date"].dt.year
    return weekly


def build_panel() -> pd.DataFrame:
    vni_weekly = load_vni_weekly()
    universe = pd.read_parquet(UNIVERSE)
    symbols = universe.loc[universe["type"].astype(str).str.lower().eq("stock"), "symbol"].astype(str).str.upper().drop_duplicates()
    frames = []
    for symbol in symbols:
        frame = symbol_weekly(symbol, vni_weekly)
        if not frame.empty:
            frames.append(frame)
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel
    panel = panel.merge(universe[["symbol", "exchange", "industry_name", "sector_group"]], on="symbol", how="left")
    panel = panel[
        (panel["year"].between(2018, 2026))
        & (panel["avg_value_20d_bil"] >= 3.0)
        & (panel["close"] >= 5.0)
    ].copy()
    return panel


def spread_pp(data: pd.DataFrame, factor: str, target: str) -> float | None:
    data = data[[factor, target]].dropna()
    if len(data) < 100 or data[factor].nunique() < 5:
        return None
    try:
        bucket = pd.qcut(data[factor].rank(method="first"), 5, labels=False)
    except ValueError:
        return None
    return float((data.loc[bucket == 4, target].mean() - data.loc[bucket == 0, target].mean()) * 100.0)


def evaluate(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    year_rows = []
    regime_rows = []
    breadth_rows = []
    weekly_breadth = panel.groupby("date").agg(
        pct_above_sma50=("close", lambda s: float("nan")),
        n=("symbol", "nunique"),
    ).reset_index()
    # Compute breadth from already present SMA columns.
    breadth = panel.copy()
    breadth["above_sma50"] = breadth["close"] > breadth["sma50"]
    breadth["above_sma200"] = breadth["close"] > breadth["sma200"]
    breadth["near_high52"] = breadth["high52_proximity"] >= 0.95
    weekly_breadth = breadth.groupby("date").agg(
        pct_above_sma50=("above_sma50", "mean"),
        pct_above_sma200=("above_sma200", "mean"),
        pct_near_high52=("near_high52", "mean"),
        n=("symbol", "nunique"),
    ).reset_index()
    breadth_rows.extend(weekly_breadth.to_dict(orient="records"))

    for factor in FACTORS:
        for target in TARGETS:
            for year, part in panel.groupby("year"):
                data = part[[factor, target]].dropna()
                if len(data) < 150:
                    continue
                year_rows.append({
                    "factor": factor,
                    "target": target,
                    "year": int(year),
                    "n": int(len(data)),
                    "rank_ic": float(data[factor].corr(data[target], method="spearman")),
                    "top_minus_bottom_pp": spread_pp(part, factor, target),
                })
            for regime, part in panel.groupby("vni_regime"):
                if regime == "unknown":
                    continue
                data = part[[factor, target]].dropna()
                if len(data) < 300:
                    continue
                regime_rows.append({
                    "factor": factor,
                    "target": target,
                    "regime": regime,
                    "n": int(len(data)),
                    "rank_ic": float(data[factor].corr(data[target], method="spearman")),
                    "top_minus_bottom_pp": spread_pp(part, factor, target),
                })
    return pd.DataFrame(year_rows), pd.DataFrame(regime_rows), pd.DataFrame(breadth_rows)


def summarize(year_df: pd.DataFrame, regime_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (factor, target), part in year_df.groupby(["factor", "target"]):
        regs = regime_df[(regime_df["factor"].eq(factor)) & (regime_df["target"].eq(target))]
        pos_ic = int((part["rank_ic"] > 0).sum())
        pos_spread = int((part["top_minus_bottom_pp"] > 0).sum())
        pos_reg = int((regs["rank_ic"] > 0).sum())
        rows.append({
            "factor": factor,
            "target": target,
            "years_tested": int(part["year"].nunique()),
            "positive_ic_years": pos_ic,
            "positive_spread_years": pos_spread,
            "positive_regimes": pos_reg,
            "mean_rank_ic": float(part["rank_ic"].mean()),
            "mean_top_minus_bottom_pp": float(part["top_minus_bottom_pp"].mean()),
            "stability_pass": bool(
                pos_ic >= 6
                and pos_spread >= 6
                and pos_reg >= 2
                and part["rank_ic"].mean() > 0
                and part["top_minus_bottom_pp"].mean() > 0
            ),
        })
    return pd.DataFrame(rows).sort_values(
        ["stability_pass", "positive_ic_years", "positive_spread_years", "mean_top_minus_bottom_pp"],
        ascending=[False, False, False, False],
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = build_panel()
    panel.to_parquet(OUT / "technical_weekly_panel.parquet", index=False)
    year_df, regime_df, breadth_df = evaluate(panel)
    summary = summarize(year_df, regime_df)
    year_df.to_csv(OUT / "technical_factor_ic_by_year.csv", index=False, encoding="utf-8-sig")
    regime_df.to_csv(OUT / "technical_factor_ic_by_regime.csv", index=False, encoding="utf-8-sig")
    breadth_df.to_csv(OUT / "technical_market_breadth.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUT / "technical_factor_stability_summary.csv", index=False, encoding="utf-8-sig")
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "track": "technical_price_lab",
        "data_status": "PRICE_VOLUME_ONLY_PIT_RESEARCH",
        "panel_rows": int(len(panel)),
        "symbols": int(panel["symbol"].nunique()) if not panel.empty else 0,
        "factor_pass_count": int(summary["stability_pass"].sum()) if not summary.empty else 0,
        "best_rows": summary.head(10).to_dict(orient="records"),
        "next_gate": "Claude CV-T1/CV-T2 review before any portfolio test.",
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Technical Price/Volume Factor Stability",
        "",
        "Status: **price/volume only**, research stage. No BCTC, no sector current-tag dependency, no portfolio test yet.",
        "",
        f"Panel rows: {len(panel):,}",
        f"Symbols: {status['symbols']}",
        f"Factors passing stability gate: **{status['factor_pass_count']}**",
        "",
        "## Top Factors",
        "",
        summary.head(12).to_markdown(index=False) if not summary.empty else "No factors.",
        "",
        "Gate: positive IC >=6/9 years, positive top-bottom spread >=6/9 years, positive IC in >=2 VNI regimes.",
    ]
    (OUT / "technical_factor_stability.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
