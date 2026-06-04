"""Pair657 regime diagnostic, no new data fetch.

Goal:
- Test whether Pair657 edge is regime-specific before doing more slow searches.
- Use only ex-ante weekly market features already present in candidate matrices.
- Attribute existing strict daily NAV returns by regime and year.

This is a diagnostic, not a production router.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "beat_vni30_parallel" / "pair657_regime_diagnostic_20260527"
OUT.mkdir(parents=True, exist_ok=True)

MATRIX_2016 = ROOT / ".cache" / "backtest" / "yearly_floor_candidate_matrix_2016_2021_fullpanel.parquet"
MATRIX_2021 = ROOT / ".cache" / "backtest" / "yearly_floor_candidate_matrix_live_preview.parquet"
EQ_2016 = ROOT / "output" / "beat_vni30_parallel" / "pair657_2016_2021_extension_20260527" / "pair657_w10_cap40" / "equity_20bps.parquet"
EQ_2021 = ROOT / "output" / "beat_vni30_parallel" / "pair657_codex_final_audit_20260527" / "fullsignals_w10_cap40" / "equity_20bps.parquet"
VNI_DAILY = ROOT / ".cache" / "backtest" / "vnindex_daily_2012_ohlcv.parquet"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_market_panel() -> pd.DataFrame:
    cols = [
        "date",
        "symbol",
        "close",
        "sma30",
        "sma40",
        "ret4",
        "ret13",
        "ret26",
        "vni_close",
        "vni_ret4",
        "vni_ret13",
        "vni_ret26",
        "vni_sma30",
        "vni_sma40",
        "vni_vol13",
        "trend_template",
        "avg_value_20d_bil",
    ]
    a = pd.read_parquet(MATRIX_2016, columns=cols)
    b = pd.read_parquet(MATRIX_2021, columns=cols)
    a["date"] = pd.to_datetime(a["date"]).dt.normalize()
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    # Avoid duplicate 2021 weeks. The 2016 panel already covers through 2021-12-27.
    b = b[b["date"] > a["date"].max()].copy()
    raw = pd.concat([a, b], ignore_index=True)

    rows = []
    for date, g in raw.groupby("date", sort=True):
        close = _num(g["close"])
        sma30 = _num(g["sma30"])
        sma40 = _num(g["sma40"])
        ret4 = _num(g["ret4"])
        ret13 = _num(g["ret13"])
        ret26 = _num(g["ret26"])
        liq = _num(g["avg_value_20d_bil"])
        valid = close.notna()
        liquid = valid & liq.ge(1.0)
        base = g.loc[liquid].copy()
        if len(base) < 50:
            base = g.loc[valid].copy()
        rows.append(
            {
                "date": date,
                "n_symbols": int(valid.sum()),
                "n_liquid_1b": int(liquid.sum()),
                "vni_close": float(_num(g["vni_close"]).dropna().iloc[0]),
                "vni_ret4": float(_num(g["vni_ret4"]).dropna().iloc[0]) if _num(g["vni_ret4"]).notna().any() else np.nan,
                "vni_ret13": float(_num(g["vni_ret13"]).dropna().iloc[0]) if _num(g["vni_ret13"]).notna().any() else np.nan,
                "vni_ret26": float(_num(g["vni_ret26"]).dropna().iloc[0]) if _num(g["vni_ret26"]).notna().any() else np.nan,
                "vni_sma30": float(_num(g["vni_sma30"]).dropna().iloc[0]) if _num(g["vni_sma30"]).notna().any() else np.nan,
                "vni_sma40": float(_num(g["vni_sma40"]).dropna().iloc[0]) if _num(g["vni_sma40"]).notna().any() else np.nan,
                "vni_vol13": float(_num(g["vni_vol13"]).dropna().iloc[0]) if _num(g["vni_vol13"]).notna().any() else np.nan,
                "breadth_ma30": float((_num(base["close"]) >= _num(base["sma30"])).mean()) if len(base) else np.nan,
                "breadth_ma40": float((_num(base["close"]) >= _num(base["sma40"])).mean()) if len(base) else np.nan,
                "breadth_ret13_pos": float((_num(base["ret13"]) > 0).mean()) if len(base) else np.nan,
                "breadth_trend": float((_num(base["trend_template"]) >= 1).mean()) if "trend_template" in base else np.nan,
                "dispersion_ret4": float(_num(base["ret4"]).std(ddof=0)) if len(base) else np.nan,
                "dispersion_ret13": float(_num(base["ret13"]).std(ddof=0)) if len(base) else np.nan,
                "median_ret13": float(_num(base["ret13"]).median()) if len(base) else np.nan,
            }
        )
        if len(base):
            base_liq = _num(base["avg_value_20d_bil"])
            q30 = base_liq.quantile(0.30)
            q70 = base_liq.quantile(0.70)
            low = base[base_liq <= q30]
            high = base[base_liq >= q70]
            rows[-1].update(
                {
                    "low_liq_ret13_median": float(_num(low["ret13"]).median()) if len(low) else np.nan,
                    "high_liq_ret13_median": float(_num(high["ret13"]).median()) if len(high) else np.nan,
                    "low_liq_breadth_ret13": float((_num(low["ret13"]) > 0).mean()) if len(low) else np.nan,
                    "high_liq_breadth_ret13": float((_num(high["ret13"]) > 0).mean()) if len(high) else np.nan,
                    "low_liq_ret4_median": float(_num(low["ret4"]).median()) if len(low) else np.nan,
                    "high_liq_ret4_median": float(_num(high["ret4"]).median()) if len(high) else np.nan,
                }
            )
        else:
            rows[-1].update(
                {
                    "low_liq_ret13_median": np.nan,
                    "high_liq_ret13_median": np.nan,
                    "low_liq_breadth_ret13": np.nan,
                    "high_liq_breadth_ret13": np.nan,
                    "low_liq_ret4_median": np.nan,
                    "high_liq_ret4_median": np.nan,
                }
            )
    panel = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    panel["vni_peak_26"] = panel["vni_close"].rolling(26, min_periods=8).max()
    panel["vni_dd_26"] = panel["vni_close"] / panel["vni_peak_26"] - 1.0
    panel["shock_13w"] = panel["vni_dd_26"].rolling(13, min_periods=4).min()
    panel["vni_range_13"] = (
        panel["vni_close"].rolling(13, min_periods=8).max()
        / panel["vni_close"].rolling(13, min_periods=8).min()
        - 1.0
    )
    panel["vol_q35_156"] = panel["vni_vol13"].rolling(156, min_periods=40).quantile(0.35)
    panel["ret13_delta_4"] = panel["vni_ret13"] - panel["vni_ret13"].shift(4)
    panel["micro_adv_ret13"] = panel["low_liq_ret13_median"] - panel["high_liq_ret13_median"]
    panel["micro_adv_breadth"] = panel["low_liq_breadth_ret13"] - panel["high_liq_breadth_ret13"]
    return panel


def label_regimes(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    labels = []
    for r in out.itertuples(index=False):
        v13 = float(r.vni_ret13) if pd.notna(r.vni_ret13) else np.nan
        v26 = float(r.vni_ret26) if pd.notna(r.vni_ret26) else np.nan
        b30 = float(r.breadth_ma30) if pd.notna(r.breadth_ma30) else np.nan
        b13 = float(r.breadth_ret13_pos) if pd.notna(r.breadth_ret13_pos) else np.nan
        vol = float(r.vni_vol13) if pd.notna(r.vni_vol13) else np.nan
        vol_q = float(r.vol_q35_156) if pd.notna(r.vol_q35_156) else np.nan
        rng = float(r.vni_range_13) if pd.notna(r.vni_range_13) else np.nan
        shock = float(r.shock_13w) if pd.notna(r.shock_13w) else 0.0
        delta = float(r.ret13_delta_4) if pd.notna(r.ret13_delta_4) else 0.0
        below_sma40 = pd.notna(r.vni_sma40) and float(r.vni_close) < float(r.vni_sma40)

        if shock <= -0.20 and (v13 > -0.05 or delta > 0.08) and v13 > -0.12:
            label = "RECOVERY"
        elif (v13 < -0.08 and v26 < -0.05) or (below_sma40 and v13 < -0.06):
            label = "BEAR"
        elif v13 > 0.05 and v26 > 0.08 and (b30 >= 0.55 or b13 >= 0.60):
            label = "BROAD_BULL"
        elif (v13 > 0.03 or v26 > 0.08) and (b30 < 0.55 or b13 < 0.60):
            label = "NARROW_BULL"
        elif abs(v13) <= 0.05 and abs(v26) <= 0.10 and (pd.isna(vol_q) or vol <= vol_q or rng <= 0.13):
            label = "SIDEWAYS"
        else:
            label = "MIXED"
        labels.append(label)
    out["regime"] = labels
    return out


def label_style_regimes(panel: pd.DataFrame) -> pd.DataFrame:
    """Add a second diagnostic label for leadership style.

    Liquidity ranks are cross-sectional within each week. They are not a final
    production liquidity check because the historical raw/adjusted issue remains
    open, but they are useful for a quick leadership diagnostic.
    """
    out = panel.copy()
    labels = []
    for r in out.itertuples(index=False):
        v13 = float(r.vni_ret13) if pd.notna(r.vni_ret13) else np.nan
        v26 = float(r.vni_ret26) if pd.notna(r.vni_ret26) else np.nan
        b30 = float(r.breadth_ma30) if pd.notna(r.breadth_ma30) else np.nan
        micro_adv = float(r.micro_adv_ret13) if pd.notna(r.micro_adv_ret13) else 0.0
        micro_breadth = float(r.micro_adv_breadth) if pd.notna(r.micro_adv_breadth) else 0.0
        high_ret = float(r.high_liq_ret13_median) if pd.notna(r.high_liq_ret13_median) else 0.0
        low_ret = float(r.low_liq_ret13_median) if pd.notna(r.low_liq_ret13_median) else 0.0
        shock = float(r.shock_13w) if pd.notna(r.shock_13w) else 0.0
        delta = float(r.ret13_delta_4) if pd.notna(r.ret13_delta_4) else 0.0
        rng = float(r.vni_range_13) if pd.notna(r.vni_range_13) else np.nan
        below_sma40 = pd.notna(r.vni_sma40) and float(r.vni_close) < float(r.vni_sma40)

        if shock <= -0.20 and (v13 > -0.05 or delta > 0.08) and v13 > -0.12:
            label = "RECOVERY"
        elif (v13 < -0.08 and v26 < -0.05) or (below_sma40 and v13 < -0.06):
            label = "BEAR"
        elif abs(v13) <= 0.05 and abs(v26) <= 0.10 and (pd.isna(rng) or rng <= 0.13):
            label = "SIDEWAYS"
        elif v13 > 0.03 or v26 > 0.08:
            if micro_adv >= 0.05 and micro_breadth >= -0.02:
                label = "MICRO_LEADERSHIP"
            elif high_ret - low_ret >= 0.03:
                label = "LIQUID_LEADERSHIP"
            elif b30 >= 0.58:
                label = "BROAD_UP"
            else:
                label = "NARROW_UP"
        else:
            label = "MIXED"
        labels.append(label)
    out["style_regime"] = labels
    return out


def load_pair657_equity() -> pd.DataFrame:
    a = pd.read_parquet(EQ_2016)
    b = pd.read_parquet(EQ_2021)
    for df in (a, b):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    b = b[b["date"] > a["date"].max()].copy()
    eq = pd.concat([a, b], ignore_index=True).sort_values("date")
    eq = eq.drop_duplicates("date", keep="last").reset_index(drop=True)
    eq["ret"] = _num(eq["ret"]).fillna(_num(eq["nav"]).pct_change()).fillna(0.0)
    eq["year"] = eq["date"].dt.year
    return eq


def load_vni_daily() -> pd.DataFrame:
    vni = pd.read_parquet(VNI_DAILY)
    date_col = "time" if "time" in vni.columns else "date"
    vni["date"] = pd.to_datetime(vni[date_col]).dt.normalize()
    vni = vni[["date", "close"]].dropna().sort_values("date").drop_duplicates("date")
    vni["vni_ret_daily"] = _num(vni["close"]).pct_change().fillna(0.0)
    return vni


def compound(s: pd.Series) -> float:
    x = _num(s).dropna()
    if len(x) == 0:
        return 0.0
    return float((1.0 + x).prod() - 1.0)


def max_drawdown_from_returns(s: pd.Series) -> float:
    x = (1.0 + _num(s).fillna(0.0)).cumprod()
    if len(x) == 0:
        return 0.0
    return float((x / x.cummax() - 1.0).min())


def attribution(
    eq: pd.DataFrame,
    regimes: pd.DataFrame,
    vni: pd.DataFrame,
    label_col: str = "regime",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    reg = regimes[["date", label_col]].sort_values("date")
    daily = pd.merge_asof(eq.sort_values("date"), reg, on="date", direction="backward")
    daily = pd.merge_asof(daily.sort_values("date"), vni[["date", "vni_ret_daily"]].sort_values("date"), on="date", direction="backward")
    daily[label_col] = daily[label_col].fillna("UNLABELED")
    daily["year"] = daily["date"].dt.year

    regime_rows = []
    for regime, g in daily.groupby(label_col, sort=True):
        strat = compound(g["ret"])
        bench = compound(g["vni_ret_daily"])
        regime_rows.append(
            {
                label_col: regime,
                "days": int(len(g)),
                "years_touched": int(g["year"].nunique()),
                "pair_return_pct": strat * 100.0,
                "vni_return_pct": bench * 100.0,
                "edge_pp": (strat - bench) * 100.0,
                "avg_exposure": float(_num(g["exposure"]).mean()),
                "hit_rate_daily": float((_num(g["ret"]) > 0).mean()),
                "maxdd_within_regime_pct": max_drawdown_from_returns(g["ret"]) * 100.0,
                "log_contribution": float(np.log1p(_num(g["ret"]).fillna(0.0)).sum()),
            }
        )
    by_regime = pd.DataFrame(regime_rows).sort_values("edge_pp", ascending=False)

    year_rows = []
    for (year, regime), g in daily.groupby(["year", label_col], sort=True):
        strat = compound(g["ret"])
        bench = compound(g["vni_ret_daily"])
        year_rows.append(
            {
                "year": int(year),
                label_col: regime,
                "days": int(len(g)),
                "pair_return_pct": strat * 100.0,
                "vni_return_pct": bench * 100.0,
                "edge_pp": (strat - bench) * 100.0,
                "avg_exposure": float(_num(g["exposure"]).mean()),
            }
        )
    by_year_regime = pd.DataFrame(year_rows).sort_values(["year", label_col])

    coverage = (
        regimes.assign(year=regimes["date"].dt.year)
        .groupby(["year", label_col])
        .size()
        .rename("weeks")
        .reset_index()
        .sort_values(["year", label_col])
    )
    return by_regime, by_year_regime, coverage


def fmt_pct(v: float) -> str:
    if pd.isna(v):
        return ""
    return f"{v:.2f}%"


def write_summary(
    regimes: pd.DataFrame,
    by_regime: pd.DataFrame,
    by_year_regime: pd.DataFrame,
    coverage: pd.DataFrame,
    by_style: pd.DataFrame,
    by_year_style: pd.DataFrame,
    style_coverage: pd.DataFrame,
) -> None:
    pair_preferred = by_regime[by_regime["regime"].isin(["NARROW_BULL", "BEAR"])]
    pair_avoid = by_regime[by_regime["regime"].isin(["BROAD_BULL", "RECOVERY", "SIDEWAYS"])]
    preferred_edge = float(pair_preferred["edge_pp"].mean()) if len(pair_preferred) else np.nan
    avoid_edge = float(pair_avoid["edge_pp"].mean()) if len(pair_avoid) else np.nan

    side = by_regime[by_regime["regime"].eq("SIDEWAYS")]
    bear = by_regime[by_regime["regime"].eq("BEAR")]
    micro = by_style[by_style["style_regime"].eq("MICRO_LEADERSHIP")]
    liquid = by_style[by_style["style_regime"].eq("LIQUID_LEADERSHIP")]
    side_edge = float(side["edge_pp"].iloc[0]) if len(side) else np.nan
    bear_edge = float(bear["edge_pp"].iloc[0]) if len(bear) else np.nan
    micro_edge = float(micro["edge_pp"].iloc[0]) if len(micro) else np.nan
    liquid_edge = float(liquid["edge_pp"].iloc[0]) if len(liquid) else np.nan

    lines = [
        "# Pair657 Regime Diagnostic - 2026-05-27",
        "",
        "Scope: existing strict daily Pair657 equity curves only. No new price fetch. Regime labels use weekly market features available at the signal date.",
        "",
        "## Headline",
        "",
        f"- Pair657 NARROW_BULL + BEAR average edge: {preferred_edge:.2f}pp.",
        f"- Pair657 BROAD_BULL + RECOVERY + SIDEWAYS average edge: {avoid_edge:.2f}pp.",
        f"- BEAR edge: {bear_edge:.2f}pp. SIDEWAYS edge: {side_edge:.2f}pp.",
        f"- MICRO_LEADERSHIP edge: {micro_edge:.2f}pp. LIQUID_LEADERSHIP edge: {liquid_edge:.2f}pp.",
    ]
    if pd.notna(side_edge) and side_edge < 0:
        lines.append("- Strong confirmed kill-zone: SIDEWAYS. Pair657 should not run full gross in low-direction markets.")
    if pd.notna(bear_edge) and bear_edge < 0:
        lines.append("- Bear-only deployment is not supported by this diagnostic. Pair657 did not produce positive edge in BEAR labels.")
    if pd.notna(micro_edge) and (pd.isna(liquid_edge) or micro_edge > liquid_edge):
        lines.append("- Style test supports the micro-cap leadership thesis directionally, subject to the historical liquidity data caveat.")
    else:
        lines.append("- Style test does not isolate a clean micro-leadership edge. Do not promote a router before the liquidity fix.")
    lines += [
        "",
        "## Market Regime Attribution",
        "",
        by_regime.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Style Leadership Attribution",
        "",
        by_style.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Year x Market Regime Attribution",
        "",
        by_year_regime.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Year x Style Leadership Attribution",
        "",
        by_year_style.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Weekly Regime Coverage",
        "",
        coverage.to_markdown(index=False),
        "",
        "## Weekly Style Coverage",
        "",
        style_coverage.to_markdown(index=False),
        "",
        "## Interpretation",
        "",
        "- This test is deliberately small. It answers whether a top-level regime gate is worth building before running another expensive strategy search.",
        "- It does not fix the historical liquidity issue. Any production promotion must wait for raw-price liquidity or a proven equivalent adjustment.",
        "- Next efficient step: if the attribution shows stable positive edge only in NARROW_BULL/BEAR, build a conditional router: Pair657 in those regimes, liquid-quality fallback in BROAD_BULL/RECOVERY, low gross in SIDEWAYS.",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    panel = load_market_panel()
    regimes = label_regimes(panel)
    regimes = label_style_regimes(regimes)
    eq = load_pair657_equity()
    vni = load_vni_daily()
    by_regime, by_year_regime, coverage = attribution(eq, regimes, vni, "regime")
    by_style, by_year_style, style_coverage = attribution(eq, regimes, vni, "style_regime")

    regimes.to_csv(OUT / "regime_labels.csv", index=False)
    by_regime.to_csv(OUT / "regime_return_attribution.csv", index=False)
    by_year_regime.to_csv(OUT / "year_regime_attribution.csv", index=False)
    coverage.to_csv(OUT / "regime_coverage.csv", index=False)
    by_style.to_csv(OUT / "style_return_attribution.csv", index=False)
    by_year_style.to_csv(OUT / "year_style_attribution.csv", index=False)
    style_coverage.to_csv(OUT / "style_coverage.csv", index=False)
    write_summary(regimes, by_regime, by_year_regime, coverage, by_style, by_year_style, style_coverage)

    print(f"Wrote {OUT}")
    print(by_regime.to_string(index=False))
    print(by_style.to_string(index=False))


if __name__ == "__main__":
    main()
