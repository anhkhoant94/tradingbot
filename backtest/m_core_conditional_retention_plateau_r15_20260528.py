"""Lane R15: plateau probe around conditional retention market thresholds."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from m_core_regime_alt_selector_d2_20260528 import (
    align_history_to_calendar,
    load_base,
    load_daily_history,
    load_matrix,
    load_vni_daily,
    simulate,
)
from m_core_winner_retention_d4_20260528 import feature_row, trend_ok

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "beat_vni30_parallel" / "m_core_conditional_retention_plateau_r15_20260528"
REGIME_PATH = ROOT / ".cache" / "backtest" / "regime_features_weekly.parquet"


def build_h(base: pd.DataFrame, matrix: pd.DataFrame, regime_map: dict[pd.Timestamp, pd.Series], *, label: str, mega_min: float, mid_min: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    md = {(pd.Timestamp(r["date"]), str(r["symbol"]).upper()): r for _, r in matrix.iterrows()}
    rows = []
    fire_rows = []
    prev_weights: dict[str, float] = {}
    carry_age: dict[str, int] = {}
    for dt, g in base.groupby("date", sort=True):
        dt = pd.Timestamp(dt)
        reg = regime_map.get(dt)
        mega = float(reg.get("mega_cap_ret13", np.nan)) if reg is not None else np.nan
        mid = float(reg.get("mid_cap_ret13", np.nan)) if reg is not None else np.nan
        market_ok = mega >= mega_min and mid >= mid_min
        base_w = {str(r.symbol).upper(): float(r.weight) for r in g.itertuples(index=False)}
        gross = float(sum(base_w.values()))
        target = dict(base_w)
        retained = {}
        for sym, prev_w in prev_weights.items():
            if prev_w < 0.35:
                continue
            age = carry_age.get(sym, 0)
            if age >= 4:
                continue
            row = feature_row(md, dt, sym)
            if market_ok and trend_ok(row, "sma30_near85"):
                if prev_w > target.get(sym, 0.0):
                    target[sym] = prev_w
                    retained[sym] = prev_w
        total = sum(target.values())
        if total > gross and total > 0:
            scale = gross / total
            target = {s: w * scale for s, w in target.items()}
        for sym, w in target.items():
            if w > 1e-8:
                rows.append({"date": dt, "symbol": sym, "weight": w})
        next_age = {}
        for sym in target:
            if sym in base_w:
                next_age[sym] = 0
            elif sym in carry_age:
                next_age[sym] = carry_age[sym] + 1
            else:
                next_age[sym] = 1
        fire_rows.append({"date": dt, "year": dt.year, "case": label, "retained_count": len(retained), "mega_min": mega_min, "mid_min": mid_min})
        prev_weights = target
        carry_age = next_age
    return pd.DataFrame(rows), pd.DataFrame(fire_rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_base()
    matrix = load_matrix()
    regime = pd.read_parquet(REGIME_PATH)
    regime["date"] = pd.to_datetime(regime["date"])
    regime_map = {pd.Timestamp(r["date"]): r for _, r in regime.iterrows()}
    signal_dates = sorted(pd.Timestamp(x) for x in base["date"].dropna().unique())
    specs = [
        ("base", None, None),
        ("mega-2_mid-2", -0.02, -0.02),
        ("mega-2_mid0", -0.02, 0.00),
        ("mega0_mid-2", 0.00, -0.02),
        ("mega0_mid0", 0.00, 0.00),
        ("mega2_mid0", 0.02, 0.00),
        ("mega0_mid2", 0.00, 0.02),
        ("mega2_mid2", 0.02, 0.02),
    ]
    built = {}
    fires = {}
    all_symbols = set(base["symbol"].astype(str))
    for label, mega_min, mid_min in specs:
        if label == "base":
            h = base[["date", "symbol", "weight"]].copy()
            f = pd.DataFrame({"date": signal_dates, "year": [d.year for d in signal_dates], "case": "base", "retained_count": 0})
        else:
            h, f = build_h(base, matrix, regime_map, label=label, mega_min=mega_min, mid_min=mid_min)
        built[label] = h
        fires[label] = f
        all_symbols.update(h["symbol"].astype(str))
    vni = load_vni_daily()
    daily_dates = [pd.Timestamp(x) for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"]]
    hist = align_history_to_calendar(load_daily_history(sorted(all_symbols)), daily_dates)
    rows = []
    base_yearly = None
    base_row = None
    for label, h in built.items():
        row, yearly, eq, trades = simulate(label, h, vni, hist, signal_dates)
        if base_yearly is None:
            base_yearly = yearly.copy()
            base_row = row.copy()
        merged = yearly[["year", "edge_vs_vni_pp"]].merge(base_yearly[["year", "edge_vs_vni_pp"]].rename(columns={"edge_vs_vni_pp": "base_edge"}), on="year", how="left")
        row["min_delta_edge_vs_base"] = float((merged["edge_vs_vni_pp"] - merged["base_edge"]).min())
        row["delta_cagr_vs_base"] = float(row["cagr_pct"] - base_row["cagr_pct"])
        row["delta_maxdd_vs_base"] = float(row["maxdd_pct"] - base_row["maxdd_pct"])
        rows.append(row)
        sub = OUT / label
        sub.mkdir(parents=True, exist_ok=True)
        h.to_parquet(sub / "holdings.parquet", index=False)
        yearly.to_csv(sub / "yearly.csv", index=False)
        eq.to_parquet(sub / "equity.parquet", index=False)
        trades.to_csv(sub / "trades.csv", index=False)
    summary = pd.DataFrame(rows).sort_values(["pass_vni30_all", "cagr_pct"], ascending=[False, False])
    summary.to_csv(OUT / "summary.csv", index=False)
    fire_year = pd.concat(fires.values(), ignore_index=True).groupby(["case", "year"], as_index=False).agg(weeks_with_retention=("retained_count", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum())))
    fire_year.to_csv(OUT / "retention_by_year.csv", index=False)
    lines = [
        "# Lane R15 - Conditional Retention Plateau",
        "",
        "Plateau around mega/mid 13-week return thresholds. M-core is BCTC-assisted.",
        "",
        summary.to_markdown(index=False, floatfmt=".2f"),
        "",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print(OUT)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
