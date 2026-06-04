"""Lane D4: tiny winner-retention / exit-discipline smoke for M core.

D3 showed fail-year future winners do not have a stable higher-score feature
gap versus M-core picks. The next plausible low-cost mechanism is to retain
already-held winners longer when their own trend remains intact.

No new symbols are introduced. This only post-processes M-core target holdings.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from m_core_regime_alt_selector_d2_20260528 import (
    OUT as _D2_OUT,
    align_history_to_calendar,
    full_yearly,
    load_base,
    load_daily_history,
    load_matrix,
    load_vni_daily,
    metrics,
    simulate,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "beat_vni30_parallel" / "m_core_winner_retention_d4_20260528"


def feature_row(matrix_by_date_symbol: dict[tuple[pd.Timestamp, str], pd.Series], dt: pd.Timestamp, sym: str) -> pd.Series | None:
    return matrix_by_date_symbol.get((dt, sym))


def trend_ok(row: pd.Series | None, mode: str) -> bool:
    if row is None:
        return False
    close = float(row.get("close", np.nan))
    sma30 = float(row.get("sma30", np.nan))
    sma40 = float(row.get("sma40", np.nan))
    near = float(row.get("near_high52", np.nan))
    ret13 = float(row.get("ret13", np.nan))
    rs13 = float(row.get("rs13", np.nan))
    rsi = float(row.get("rsi14", np.nan))
    if mode == "sma30_near85_ret13":
        return close >= sma30 and near >= 0.85 and ret13 >= 0.0 and 35 <= rsi <= 85
    if mode == "sma40_near90_rs13":
        return close >= sma40 and near >= 0.90 and rs13 >= 0.0 and 35 <= rsi <= 85
    if mode == "sma30_near80_rs13":
        return close >= sma30 and near >= 0.80 and rs13 >= -0.03 and 32 <= rsi <= 88
    if mode == "sma30_near85":
        return close >= sma30 and near >= 0.85 and 35 <= rsi <= 88
    raise ValueError(mode)


def build_retention_holdings(
    base: pd.DataFrame,
    matrix: pd.DataFrame,
    *,
    label: str,
    mode: str,
    max_keep: int,
    min_prev_weight: float,
    decay: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    md = {
        (pd.Timestamp(r["date"]), str(r["symbol"]).upper()): r
        for _, r in matrix.iterrows()
    }
    rows = []
    fire_rows = []
    prev_weights: dict[str, float] = {}
    carry_age: dict[str, int] = {}
    for dt, g in base.groupby("date", sort=True):
        dt = pd.Timestamp(dt)
        base_w = {str(r.symbol).upper(): float(r.weight) for r in g.itertuples(index=False)}
        gross = float(sum(base_w.values()))
        target = dict(base_w)
        retained = {}
        for sym, prev_w in prev_weights.items():
            if prev_w < min_prev_weight:
                continue
            age = carry_age.get(sym, 0)
            if age >= max_keep:
                continue
            row = feature_row(md, dt, sym)
            if trend_ok(row, mode):
                keep_w = min(prev_w * decay, prev_w)
                if keep_w > target.get(sym, 0.0):
                    target[sym] = keep_w
                    retained[sym] = keep_w
        total = sum(target.values())
        if total > gross and total > 0:
            scale = gross / total
            target = {s: w * scale for s, w in target.items()}
        target = {s: w for s, w in target.items() if w > 1e-8}
        for sym, w in sorted(target.items()):
            rows.append({"date": dt, "symbol": sym, "weight": w})
        next_age = {}
        for sym in target:
            if sym in base_w:
                next_age[sym] = 0
            elif sym in carry_age:
                next_age[sym] = carry_age[sym] + 1
            else:
                next_age[sym] = 1
        fire_rows.append(
            {
                "date": dt,
                "year": dt.year,
                "case": label,
                "base_count": int(len(base_w)),
                "target_count": int(len(target)),
                "retained_count": int(len(retained)),
                "retained_symbols": ",".join(sorted(retained)),
                "gross": gross,
                "target_gross": float(sum(target.values())),
            }
        )
        prev_weights = target
        carry_age = next_age
    return pd.DataFrame(rows), pd.DataFrame(fire_rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_base()
    matrix = load_matrix()
    signal_dates = sorted(pd.Timestamp(x) for x in base["date"].dropna().unique())

    cases = [
        {"label": "base"},
        {"label": "keep2_sma30_near85_ret13_min20", "mode": "sma30_near85_ret13", "max_keep": 2, "min_prev_weight": 0.20, "decay": 1.0},
        {"label": "keep4_sma30_near85_ret13_min20", "mode": "sma30_near85_ret13", "max_keep": 4, "min_prev_weight": 0.20, "decay": 1.0},
        {"label": "keep2_sma40_near90_rs13_min20", "mode": "sma40_near90_rs13", "max_keep": 2, "min_prev_weight": 0.20, "decay": 1.0},
        {"label": "keep4_sma40_near90_rs13_min20", "mode": "sma40_near90_rs13", "max_keep": 4, "min_prev_weight": 0.20, "decay": 1.0},
        {"label": "keep4_sma30_near80_rs13_min20_decay80", "mode": "sma30_near80_rs13", "max_keep": 4, "min_prev_weight": 0.20, "decay": 0.80},
        {"label": "keep4_sma30_near85_min30", "mode": "sma30_near85", "max_keep": 4, "min_prev_weight": 0.30, "decay": 1.0},
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
                    "case": "base",
                    "base_count": [int(base.loc[base["date"].eq(d), "symbol"].nunique()) for d in signal_dates],
                    "target_count": [int(base.loc[base["date"].eq(d), "symbol"].nunique()) for d in signal_dates],
                    "retained_count": 0,
                    "retained_symbols": "",
                    "gross": [float(base.loc[base["date"].eq(d), "weight"].sum()) for d in signal_dates],
                    "target_gross": [float(base.loc[base["date"].eq(d), "weight"].sum()) for d in signal_dates],
                }
            )
        else:
            h, f = build_retention_holdings(base, matrix, **c)
        built[c["label"]] = h
        fires[c["label"]] = f
        all_symbols.update(h["symbol"].astype(str))

    vni = load_vni_daily()
    daily_dates = [pd.Timestamp(x) for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"]]
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

    summary = pd.DataFrame(rows).sort_values(["pass_vni20_all", "min_delta_edge_vs_base", "cagr_pct"], ascending=[False, False, False])
    summary.to_csv(OUT / "summary.csv", index=False)
    fire_all = pd.concat(fires.values(), ignore_index=True)
    fire_year = (
        fire_all.groupby(["case", "year"], as_index=False)
        .agg(
            signal_weeks=("date", "count"),
            weeks_with_retention=("retained_count", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum())),
            avg_retained_count=("retained_count", "mean"),
            avg_target_count=("target_count", "mean"),
            avg_gross=("target_gross", "mean"),
        )
    )
    fire_year.to_csv(OUT / "retention_by_year.csv", index=False)
    best_label = str(summary.iloc[0]["case"])
    compare = yearly_map[best_label].merge(
        base_yearly[["year", "edge_vs_vni_pp"]].rename(columns={"edge_vs_vni_pp": "base_edge_vs_vni_pp"}),
        on="year",
        how="left",
    )
    compare["delta_edge_vs_base"] = compare["edge_vs_vni_pp"] - compare["base_edge_vs_vni_pp"]
    compare.to_csv(OUT / "best_yearly_compare.csv", index=False)
    lines = [
        "# M Core Winner Retention D4 - 2026-05-28",
        "",
        "Strict daily 100-lot smoke. No new symbols; only retain already-held names when their trend remains intact.",
        "",
        "## Summary",
        "",
        summary.to_markdown(index=False, floatfmt=".2f"),
        "",
        f"## Best Case: {best_label}",
        "",
        compare.to_markdown(index=False, floatfmt=".2f"),
        "",
        "## Retention By Year",
        "",
        fire_year.to_markdown(index=False, floatfmt=".2f"),
        "",
        "Pass gate: add one VNI+20 year or improve 2019/2020/2025 by >=10pp, no other year worse by >5pp, CAGR drop <=2pp, MaxDD not worse by >3pp, trade count <=1.5x M-core.",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("OUT", OUT)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
