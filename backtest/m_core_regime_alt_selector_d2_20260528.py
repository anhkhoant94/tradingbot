"""Lane D2: true alternate selector inside regimes for M core.

C1/C2 showed exposure-only guards are insufficient. D1 showed simple filtering
of existing held names is mostly a no-op. This smoke replaces M-core holdings
with a different selector only during observable style-break/recovery regimes.

Small preregistered smoke, no year/ticker rescue, no broad grid.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backtest") not in sys.path:
    sys.path.insert(0, str(ROOT / "backtest"))

os.environ.setdefault("HISTORY_CACHE_DIR", "history_2012")

from baseline_liquid_leadership_overlay_20260527 import simulate_strict_100lot  # noqa: E402
from beat_vni30_daily_execution_sim import align_history_to_calendar, load_daily_history, load_vni_daily  # noqa: E402
from m_core_convex_sleeve_probe_20260527 import full_yearly, metrics  # noqa: E402
from pair657_regime_diagnostic_20260527 import load_market_panel  # noqa: E402


OUT = ROOT / "output" / "beat_vni30_parallel" / "m_core_regime_alt_selector_d2_20260528"
BASE_HOLDINGS = ROOT / "output" / "beat_vni30_parallel" / "m_core_convex_sleeve_probe_20260527" / "m_alpha0.10_top1" / "holdings.parquet"
REGIME_PATH = ROOT / ".cache" / "backtest" / "regime_features_weekly.parquet"
CACHE = ROOT / ".cache" / "backtest"


def load_base() -> pd.DataFrame:
    h = pd.read_parquet(BASE_HOLDINGS)
    h["date"] = pd.to_datetime(h["date"]).dt.normalize()
    h["symbol"] = h["symbol"].astype(str).str.upper()
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    return h.sort_values(["date", "symbol"]).reset_index(drop=True)


def load_matrix() -> pd.DataFrame:
    cols = [
        "date",
        "score_date",
        "symbol",
        "status",
        "composite_score",
        "avg_value_20d_bil",
        "ret4",
        "ret13",
        "ret26",
        "rs13",
        "near_high52",
        "moneyflow_score",
        "rsi14",
        "close",
        "sma30",
        "sma40",
        "trend_template",
        "fa_rank_all",
        "mom_rank_all",
        "rs_rank_all",
        "high_rank_all",
        "flow_rank_all",
    ]
    a = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_2016_2021_fullpanel.parquet", columns=cols)
    b = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_live_preview.parquet", columns=cols)
    for df in (a, b):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["score_date"] = pd.to_datetime(df["score_date"]).dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        for c in cols:
            if c not in ["date", "score_date", "symbol", "status"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    b = b[b["date"] > a["date"].max()].copy()
    return pd.concat([a, b], ignore_index=True).drop_duplicates(["date", "symbol"], keep="last")


def load_panel() -> pd.DataFrame:
    style = load_market_panel()
    style["date"] = pd.to_datetime(style["date"]).dt.normalize()
    regime = pd.read_parquet(REGIME_PATH)
    regime["date"] = pd.to_datetime(regime["date"]).dt.normalize()
    p = style.merge(regime, on="date", how="left", suffixes=("", "_rr")).sort_values("date")
    p["breadth_ma8_delta_2w"] = pd.to_numeric(p["breadth_ma8"], errors="coerce").diff(2)
    p["style_a"] = (
        pd.to_numeric(p["smallcap_vs_hose13"], errors="coerce").le(-0.05)
        & pd.to_numeric(p["breadth_top200"], errors="coerce").le(0.35)
    )
    p["style_b"] = (
        pd.to_numeric(p["breadth_ma8"], errors="coerce").le(0.25)
        & pd.to_numeric(p["breadth_top200"], errors="coerce").le(0.35)
    )
    p["style_d"] = (
        pd.to_numeric(p["median_ret13"], errors="coerce").le(0.00)
        & pd.to_numeric(p["breadth_ret13_pos"], errors="coerce").le(0.45)
        & pd.to_numeric(p["vni_ret13"], errors="coerce").ge(-0.03)
    )
    p["recovery_a"] = (
        pd.to_numeric(p["breadth_recovery_2w"], errors="coerce").ge(1.0)
        & pd.to_numeric(p["vni_ret13"], errors="coerce").gt(-0.05)
    )
    p["recovery_b"] = (
        pd.to_numeric(p["breadth_recovery_2w"], errors="coerce").ge(1.0)
        & pd.to_numeric(p["breadth_ma8_delta_2w"], errors="coerce").gt(0.03)
        & pd.to_numeric(p["vni_ret13"], errors="coerce").gt(-0.05)
    )
    return p.drop_duplicates("date", keep="last")


def held_trigger(signal_dates: list[pd.Timestamp], panel: pd.DataFrame, trigger: str, hold_weeks: int) -> dict[pd.Timestamp, bool]:
    raw_panel = dict(zip(panel["date"], panel[trigger].fillna(False).astype(bool)))
    pdates = sorted(raw_panel)
    out = {dt: False for dt in signal_dates}
    raw = {}
    k = 0
    last = False
    for dt in signal_dates:
        while k < len(pdates) and pdates[k] <= dt:
            last = bool(raw_panel[pdates[k]])
            k += 1
        raw[dt] = last
    for i, dt in enumerate(signal_dates):
        if raw.get(dt, False):
            for j in range(i, min(len(signal_dates), i + hold_weeks)):
                out[signal_dates[j]] = True
    return out


def _z(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").rank(pct=True).fillna(0.5) * 100.0


def alt_candidates(g: pd.DataFrame, sleeve: str, n: int, liq_min: float) -> pd.DataFrame:
    if g.empty or "avg_value_20d_bil" not in g.columns:
        return pd.DataFrame()
    x = g.copy()
    x = x[pd.to_numeric(x["avg_value_20d_bil"], errors="coerce").ge(liq_min)]
    x = x[x["score_date"].le(x["date"])]
    if sleeve == "style_quality":
        x = x[x["status"].isin(["BUY", "ACCUMULATE", "WATCH"])]
        x = x[pd.to_numeric(x["rsi14"], errors="coerce").fillna(50).between(30, 78)]
        score = (
            0.35 * pd.to_numeric(x["fa_rank_all"], errors="coerce").fillna(0)
            + 0.25 * pd.to_numeric(x["mom_rank_all"], errors="coerce").fillna(0)
            + 0.25 * pd.to_numeric(x["flow_rank_all"], errors="coerce").fillna(0)
            + 0.15 * pd.to_numeric(x["high_rank_all"], errors="coerce").fillna(0)
        )
    elif sleeve == "recovery_rs":
        x = x[pd.to_numeric(x["near_high52"], errors="coerce").ge(0.85)]
        x = x[pd.to_numeric(x["ret13"], errors="coerce").ge(-0.05)]
        x = x[pd.to_numeric(x["rsi14"], errors="coerce").fillna(50).between(35, 85)]
        score = (
            0.35 * pd.to_numeric(x["rs_rank_all"], errors="coerce").fillna(0)
            + 0.25 * pd.to_numeric(x["high_rank_all"], errors="coerce").fillna(0)
            + 0.20 * pd.to_numeric(x["flow_rank_all"], errors="coerce").fillna(0)
            + 0.20 * _z(x["near_high52"])
        )
    elif sleeve == "broad_quality":
        x = x[x["status"].isin(["BUY", "ACCUMULATE", "WATCH"])]
        x = x[pd.to_numeric(x["close"], errors="coerce").ge(pd.to_numeric(x["sma30"], errors="coerce"))]
        score = (
            0.30 * pd.to_numeric(x["fa_rank_all"], errors="coerce").fillna(0)
            + 0.25 * pd.to_numeric(x["rs_rank_all"], errors="coerce").fillna(0)
            + 0.25 * pd.to_numeric(x["flow_rank_all"], errors="coerce").fillna(0)
            + 0.20 * pd.to_numeric(x["mom_rank_all"], errors="coerce").fillna(0)
        )
    else:
        raise ValueError(sleeve)
    x = x.assign(_score=score)
    return x.sort_values(["_score", "avg_value_20d_bil"], ascending=[False, False]).head(n)


def build_alt_holdings(
    base: pd.DataFrame,
    matrix: pd.DataFrame,
    active_style: dict[pd.Timestamp, bool] | None,
    active_recovery: dict[pd.Timestamp, bool] | None,
    *,
    style_sleeve: str | None,
    recovery_sleeve: str | None,
    n: int,
    cap: float,
    liq_min: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    mg = {pd.Timestamp(k): v.copy() for k, v in matrix.groupby("date", sort=False)}
    rows = []
    fires = []
    for dt, g in base.groupby("date", sort=True):
        dt = pd.Timestamp(dt)
        gross = float(g["weight"].sum())
        action = "base"
        pick = pd.DataFrame()
        if active_recovery and active_recovery.get(dt, False) and recovery_sleeve:
            pick = alt_candidates(mg.get(dt, pd.DataFrame()), recovery_sleeve, n=n, liq_min=liq_min)
            action = "recovery" if not pick.empty else "base_no_pick"
        elif active_style and active_style.get(dt, False) and style_sleeve:
            pick = alt_candidates(mg.get(dt, pd.DataFrame()), style_sleeve, n=n, liq_min=liq_min)
            action = "style" if not pick.empty else "base_no_pick"
        if pick.empty:
            out = g[["date", "symbol", "weight"]].copy()
        else:
            w = min(cap, gross / max(1, len(pick)))
            out = pd.DataFrame({"date": dt, "symbol": pick["symbol"].astype(str).tolist(), "weight": w})
            if out["weight"].sum() > gross:
                out["weight"] *= gross / out["weight"].sum()
        rows.append(out)
        fires.append(
            {
                "date": dt,
                "year": dt.year,
                "action": action,
                "style_active": bool(active_style.get(dt, False)) if active_style else False,
                "recovery_active": bool(active_recovery.get(dt, False)) if active_recovery else False,
                "n_pick": int(len(pick)) if not pick.empty else int(g["symbol"].nunique()),
                "gross": float(out["weight"].sum()),
            }
        )
    return pd.concat(rows, ignore_index=True), pd.DataFrame(fires)


def simulate(label: str, holdings: pd.DataFrame, vni: pd.DataFrame, hist: dict[str, pd.DataFrame], signal_dates: list[pd.Timestamp]) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    execution = {"gap": 0.05, "buffer": 0.015, "pullback": 4, "min_sell": 4, "stop": 0.05}
    eq, trades, _ = simulate_strict_100lot(
        holdings[["date", "symbol", "weight"]].copy(),
        hist,
        vni,
        signal_dates,
        execution,
        buy_cost=0.0035,
        sell_cost=0.0045,
    )
    yearly = full_yearly(eq, vni)
    row = {
        "case": label,
        "trade_count": int(len(trades[trades["side"].isin(["BUY", "SELL"])])) if not trades.empty else 0,
        "avg_exposure": float(eq["exposure"].mean()) if "exposure" in eq else np.nan,
        **metrics(eq, yearly),
    }
    return row, yearly, eq, trades


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_base()
    matrix = load_matrix()
    panel = load_panel()
    signal_dates = sorted(pd.Timestamp(x) for x in base["date"].dropna().unique())
    triggers = {
        "style_a_h4": held_trigger(signal_dates, panel, "style_a", 4),
        "style_b_h4": held_trigger(signal_dates, panel, "style_b", 4),
        "style_d_h4": held_trigger(signal_dates, panel, "style_d", 4),
        "recovery_a_h4": held_trigger(signal_dates, panel, "recovery_a", 4),
        "recovery_b_h4": held_trigger(signal_dates, panel, "recovery_b", 4),
        "recovery_a_h8": held_trigger(signal_dates, panel, "recovery_a", 8),
    }

    cases = [
        {"label": "base"},
        {"label": "style_a_quality_top3_cap25", "style": "style_a_h4", "style_sleeve": "style_quality", "cap": 0.25, "n": 3, "liq": 2.0},
        {"label": "style_b_quality_top3_cap25", "style": "style_b_h4", "style_sleeve": "style_quality", "cap": 0.25, "n": 3, "liq": 2.0},
        {"label": "style_b_quality_top3_cap33", "style": "style_b_h4", "style_sleeve": "style_quality", "cap": 0.33, "n": 3, "liq": 2.0},
        {"label": "recovery_a_rs_top3_cap25", "recovery": "recovery_a_h4", "recovery_sleeve": "recovery_rs", "cap": 0.25, "n": 3, "liq": 2.0},
        {"label": "recovery_b_rs_top3_cap33", "recovery": "recovery_b_h4", "recovery_sleeve": "recovery_rs", "cap": 0.33, "n": 3, "liq": 2.0},
        {
            "label": "style_a_quality_recovery_a_rs_cap25",
            "style": "style_a_h4",
            "recovery": "recovery_a_h4",
            "style_sleeve": "style_quality",
            "recovery_sleeve": "recovery_rs",
            "cap": 0.25,
            "n": 3,
            "liq": 2.0,
        },
    ]

    built = {}
    fires = {}
    all_symbols = set(base["symbol"].astype(str))
    for c in cases:
        if c["label"] == "base":
            h = base[["date", "symbol", "weight"]].copy()
            f = pd.DataFrame(
                {
                    "date": signal_dates,
                    "year": [d.year for d in signal_dates],
                    "action": "base",
                    "style_active": False,
                    "recovery_active": False,
                    "n_pick": [int(base.loc[base["date"].eq(d), "symbol"].nunique()) for d in signal_dates],
                    "gross": [float(base.loc[base["date"].eq(d), "weight"].sum()) for d in signal_dates],
                }
            )
        else:
            h, f = build_alt_holdings(
                base,
                matrix,
                triggers.get(c.get("style", "")),
                triggers.get(c.get("recovery", "")),
                style_sleeve=c.get("style_sleeve"),
                recovery_sleeve=c.get("recovery_sleeve"),
                n=int(c["n"]),
                cap=float(c["cap"]),
                liq_min=float(c["liq"]),
            )
        built[c["label"]] = h
        fires[c["label"]] = f.assign(case=c["label"])
        all_symbols.update(h["symbol"].astype(str))

    vni = load_vni_daily()
    daily_dates = [
        pd.Timestamp(x)
        for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()
    ]
    hist = align_history_to_calendar(load_daily_history(sorted(all_symbols)), daily_dates)

    rows = []
    yearly_map = {}
    base_yearly = None
    base_row = None
    for label, h in built.items():
        row, yearly, eq, trades = simulate(label, h, vni, hist, signal_dates)
        if base_yearly is None:
            base_yearly = yearly.copy()
            base_row = row.copy()
        merged = yearly[["year", "edge_vs_vni_pp"]].merge(
            base_yearly[["year", "edge_vs_vni_pp"]].rename(columns={"edge_vs_vni_pp": "base_edge_vs_vni_pp"}),
            on="year",
            how="left",
        )
        for year in [2017, 2019, 2020, 2025, 2026]:
            row[f"delta_edge_{year}"] = float(
                merged.loc[merged["year"].eq(year), "edge_vs_vni_pp"].iloc[0]
                - merged.loc[merged["year"].eq(year), "base_edge_vs_vni_pp"].iloc[0]
            )
        row["min_delta_edge_vs_base"] = float((merged["edge_vs_vni_pp"] - merged["base_edge_vs_vni_pp"]).min())
        row["delta_cagr_vs_base"] = float(row["cagr_pct"] - base_row["cagr_pct"])
        row["delta_maxdd_vs_base"] = float(row["maxdd_pct"] - base_row["maxdd_pct"])
        row["trade_count_delta_pct"] = float((row["trade_count"] / base_row["trade_count"] - 1.0) * 100.0) if base_row["trade_count"] else 0.0
        rows.append(row)
        yearly_map[label] = yearly
        sub = OUT / label
        sub.mkdir(parents=True, exist_ok=True)
        h.to_parquet(sub / "holdings.parquet", index=False)
        yearly.to_csv(sub / "yearly.csv", index=False)
        eq.to_parquet(sub / "equity.parquet", index=False)
        trades.to_csv(sub / "trades.csv", index=False)
        print(label, row, flush=True)

    summary = pd.DataFrame(rows).sort_values(
        ["pass_vni20_all", "min_delta_edge_vs_base", "cagr_pct"],
        ascending=[False, False, False],
    )
    summary.to_csv(OUT / "summary.csv", index=False)
    fire_all = pd.concat(fires.values(), ignore_index=True)
    fire_year = (
        fire_all.groupby(["case", "year"], as_index=False)
        .agg(
            signal_weeks=("date", "count"),
            style_weeks=("style_active", "sum"),
            recovery_weeks=("recovery_active", "sum"),
            alt_weeks=("action", lambda s: int((s != "base").sum())),
            avg_gross=("gross", "mean"),
            avg_pick_count=("n_pick", "mean"),
        )
    )
    fire_year.to_csv(OUT / "fire_by_year.csv", index=False)
    best_label = str(summary.iloc[0]["case"])
    compare = yearly_map[best_label].merge(
        base_yearly[["year", "edge_vs_vni_pp"]].rename(columns={"edge_vs_vni_pp": "base_edge_vs_vni_pp"}),
        on="year",
        how="left",
    )
    compare["delta_edge_vs_base"] = compare["edge_vs_vni_pp"] - compare["base_edge_vs_vni_pp"]
    compare.to_csv(OUT / "best_yearly_compare.csv", index=False)

    score_violations = int((matrix["score_date"] > matrix["date"]).sum())
    lines = [
        "# M Core Regime Alternate Selector D2 - 2026-05-28",
        "",
        "Strict daily 100-lot smoke. Replace M-core picks only on style-break/recovery trigger weeks.",
        "",
        f"Matrix score_date > date violations: {score_violations}",
        "",
        "## Summary",
        "",
        summary.to_markdown(index=False, floatfmt=".2f"),
        "",
        f"## Best Case: {best_label}",
        "",
        compare.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Fire By Year",
        "",
        fire_year.to_markdown(index=False, floatfmt=".2f"),
        "",
        "Pass gate: add one VNI+20 year or improve 2019/2020 by >=10pp, no other year worse by >5pp, CAGR drop <=2pp, MaxDD not worse by >3pp, trade count <=1.5x M-core.",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("OUT", OUT)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
