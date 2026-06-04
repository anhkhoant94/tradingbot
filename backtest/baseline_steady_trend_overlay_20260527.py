"""Stress-aware steady-trend overlay on top of the flexible baseline.

Research-only. This lane tries to raise the weak 2024/2023 buffer without
using calendar years, tickers, BCTC, sector tags, ETF, margin, or shorts.
It uses the same strict 100-share-lot simulator and the user's execution
rule: buy Monday/open or later pullback, skip only hard limit days.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backtest") not in sys.path:
    sys.path.insert(0, str(ROOT / "backtest"))

from baseline_liquid_leadership_overlay_20260527 import (  # noqa: E402
    CONFIG,
    LABEL_DIR,
    REGIME_PATH,
    YEARS,
    add_vni20,
    blend_holdings,
    score_sort_key,
    sim_generate,
    simulate_strict_100lot,
)
from beat_vni30_daily_execution_sim import (  # noqa: E402
    align_history_to_calendar,
    load_daily_history,
    load_vni_daily,
)

OUT = ROOT / "output" / "beat_vni30_parallel" / "codex_steady_trend_overlay_20260527"


PARAM_FIELDS = [
    "run_id",
    "mode",
    "score_mode",
    "alpha",
    "top_n",
    "liq_min",
    "ret13_min",
    "ret13_max",
    "rs13_min",
    "near_min",
    "rsi_min",
    "rsi_max",
    "trend_min",
    "moneyflow_min",
    "breadth_min",
    "breadth_max",
    "dispersion_max",
    "vni_ret13_min",
    "vn30_breadth",
    "mega_breadth",
    "gap",
    "buffer",
    "pullback",
    "min_sell",
    "stop",
    "active_rate",
]


def zscore_rank(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").rank(pct=True).fillna(0.5) * 100.0


def preference(series: pd.Series, center: float, width: float) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce").fillna(center)
    return (1.0 - ((x - center).abs() / width).clip(0, 1)) * 100.0


def active_dates(regime: pd.DataFrame, mode: str, p: dict) -> dict[pd.Timestamp, bool]:
    out: dict[pd.Timestamp, bool] = {}
    for row in regime.itertuples(index=False):
        breadth = row.breadth_top200
        if pd.isna(breadth):
            breadth = 0.5
        dispersion = row.vni_dispersion_4w
        if pd.isna(dispersion):
            dispersion = 0.12
        ret_proxy = row.mega_cap_ret13
        if pd.isna(ret_proxy):
            ret_proxy = row.mid_cap_ret13
        if pd.isna(ret_proxy):
            ret_proxy = 0.0
        vn30_breadth = row.vn30_breadth
        if pd.isna(vn30_breadth):
            vn30_breadth = 0.0
        mega_breadth = row.mega_cap_breadth
        if pd.isna(mega_breadth):
            mega_breadth = 0.0
        if mode == "steady_trend":
            active = (
                breadth >= p["breadth_min"]
                and breadth <= p["breadth_max"]
                and dispersion <= p["dispersion_max"]
                and ret_proxy >= p["vni_ret13_min"]
                and vn30_breadth >= p["vn30_breadth"]
            )
        elif mode == "liquid_broad_up":
            active = (
                breadth >= p["breadth_min"]
                and ret_proxy >= p["vni_ret13_min"]
                and mega_breadth >= p["mega_breadth"]
                and dispersion <= p["dispersion_max"]
            )
        elif mode == "post_shock_recovery":
            active = (
                row.breadth_recovery_2w >= 1.0
                and breadth <= p["breadth_max"]
                and ret_proxy >= p["vni_ret13_min"]
                and vn30_breadth >= p["vn30_breadth"]
            )
        elif mode == "all_weather_steady":
            active = (
                ret_proxy >= p["vni_ret13_min"]
                and dispersion <= p["dispersion_max"]
                and vn30_breadth >= p["vn30_breadth"]
            )
        else:
            raise ValueError(mode)
        out[pd.Timestamp(row.date)] = bool(active)
    return out


def overlay_score(g: pd.DataFrame, mode: str) -> pd.Series:
    rs = zscore_rank(g["rs13"])
    ret13 = zscore_rank(g["ret13"])
    ret26 = zscore_rank(g["ret26"])
    high = zscore_rank(g["near_high52"])
    liq = zscore_rank(g["avg_value_20d_bil"])
    flow = zscore_rank(g["moneyflow_score"])
    rsi_pref = preference(g["rsi14"], 62.0, 32.0)
    ret_pref = preference(g["ret13"], 0.14, 0.30)
    if mode == "steady_quality":
        return 0.24 * rs + 0.22 * high + 0.20 * liq + 0.14 * flow + 0.10 * rsi_pref + 0.10 * ret_pref
    if mode == "liquid_rs":
        return 0.30 * liq + 0.28 * rs + 0.20 * high + 0.12 * flow + 0.10 * ret_pref
    if mode == "moderate_momo":
        return 0.24 * ret13 + 0.20 * ret26 + 0.20 * high + 0.18 * liq + 0.10 * rsi_pref + 0.08 * flow
    if mode == "flow_trend":
        return 0.28 * flow + 0.22 * rs + 0.20 * high + 0.18 * liq + 0.12 * ret_pref
    raise ValueError(mode)


def build_overlay_holdings(matrix: pd.DataFrame, active: dict[pd.Timestamp, bool], p: dict) -> pd.DataFrame:
    rows = []
    for dt, g in matrix.groupby("date", sort=True):
        dt = pd.Timestamp(dt)
        if not active.get(dt, False):
            continue
        x = g.copy()
        ret13 = pd.to_numeric(x["ret13"], errors="coerce")
        mask = (
            (pd.to_numeric(x["avg_value_20d_bil"], errors="coerce") >= p["liq_min"])
            & (ret13 >= p["ret13_min"])
            & (ret13 <= p["ret13_max"])
            & (pd.to_numeric(x["rs13"], errors="coerce") >= p["rs13_min"])
            & (pd.to_numeric(x["near_high52"], errors="coerce") >= p["near_min"])
            & (pd.to_numeric(x["trend_template"], errors="coerce").fillna(0.0) >= p["trend_min"])
            & (pd.to_numeric(x["moneyflow_score"], errors="coerce").fillna(50.0) >= p["moneyflow_min"])
            & (pd.to_numeric(x["rsi14"], errors="coerce").fillna(50.0).between(p["rsi_min"], p["rsi_max"]))
        )
        x = x[mask].copy()
        if x.empty:
            continue
        x["_score"] = overlay_score(x, p["score_mode"])
        x = x.sort_values(["_score", "avg_value_20d_bil"], ascending=[False, False]).head(p["top_n"])
        for _, row in x.iterrows():
            rows.append({"date": dt, "symbol": str(row["symbol"]), "weight": 1.0 / len(x), "overlay_score": float(row["_score"])})
    return pd.DataFrame(rows)


def yearly_edge_row(row: dict) -> str:
    return " ".join(f"{y}:{float(row[f'edge_y{y}']):.1f}" for y in YEARS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _, matrix, base_holdings, weekly_eq = sim_generate()
    matrix = matrix.copy()
    matrix["date"] = pd.to_datetime(matrix["date"])
    base_holdings = base_holdings.copy()
    base_holdings["date"] = pd.to_datetime(base_holdings["date"])
    regime = pd.read_parquet(REGIME_PATH)
    regime["date"] = pd.to_datetime(regime["date"])
    vni = load_vni_daily()
    signal_dates = sorted(pd.Timestamp(x) for x in weekly_eq["date"].dropna().unique())
    all_symbols = sorted(set(matrix["symbol"].astype(str)).union(set(base_holdings["symbol"].astype(str))))
    hist_all = load_daily_history(all_symbols)
    daily_dates = [pd.Timestamp(x) for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()]
    hist_all = align_history_to_calendar(hist_all, daily_dates)

    rng = random.Random(2026052702)
    rows = []
    best_payload = None
    modes = ["steady_trend", "liquid_broad_up", "post_shock_recovery", "all_weather_steady"]
    score_modes = ["steady_quality", "liquid_rs", "moderate_momo", "flow_trend"]

    for run_id in range(1400):
        p = {
            "run_id": run_id,
            "mode": rng.choice(modes),
            "score_mode": rng.choice(score_modes),
            "alpha": rng.choice([0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18]),
            "top_n": rng.choice([1, 2, 3]),
            "liq_min": rng.choice([20.0, 35.0, 50.0, 80.0, 120.0]),
            "ret13_min": rng.choice([-0.05, 0.0, 0.03, 0.06]),
            "ret13_max": rng.choice([0.20, 0.30, 0.45, 0.80]),
            "rs13_min": rng.choice([-0.03, 0.0, 0.03, 0.06]),
            "near_min": rng.choice([0.65, 0.75, 0.85, 0.95]),
            "rsi_min": rng.choice([30.0, 35.0, 40.0]),
            "rsi_max": rng.choice([75.0, 80.0, 90.0]),
            "trend_min": rng.choice([0.0, 1.0]),
            "moneyflow_min": rng.choice([0.0, 25.0, 50.0]),
            "breadth_min": rng.choice([0.15, 0.25, 0.35, 0.45]),
            "breadth_max": rng.choice([0.45, 0.55, 0.65, 0.80]),
            "dispersion_max": rng.choice([0.10, 0.13, 0.16, 0.20]),
            "vni_ret13_min": rng.choice([-0.08, -0.03, 0.0, 0.03]),
            "vn30_breadth": rng.choice([0.10, 0.20, 0.30, 0.40]),
            "mega_breadth": rng.choice([0.30, 0.40, 0.50, 0.60]),
            "gap": rng.choice([0.07, 0.09]),
            "buffer": rng.choice([0.0, 0.005, 0.015]),
            "pullback": rng.choice([2, 4, 7]),
            "min_sell": rng.choice([3, 4]),
            "stop": rng.choice([0.0, 0.05]),
        }
        if p["breadth_min"] > p["breadth_max"]:
            continue
        active = active_dates(regime, p["mode"], p)
        active_rate = float(np.mean([active.get(d, False) for d in signal_dates]))
        if active_rate < 0.03 or active_rate > 0.65:
            continue
        p["active_rate"] = active_rate
        overlay = build_overlay_holdings(matrix, active, p)
        if overlay.empty:
            continue
        holdings = blend_holdings(base_holdings, overlay, active, p["alpha"])
        execution = {k: p[k] for k in ["gap", "buffer", "pullback", "min_sell", "stop"]}
        eq, trades, metrics = simulate_strict_100lot(
            holdings,
            hist_all,
            vni,
            signal_dates,
            execution,
            buy_cost=0.0035,
            sell_cost=0.0045,
        )
        row = {**{k: p[k] for k in PARAM_FIELDS}, **metrics}
        rows.append(row)
        if best_payload is None or score_sort_key(row) > score_sort_key(best_payload[0]):
            best_payload = (row, eq, trades, holdings)
            print("BEST", len(rows), "run", run_id, "pass", row["pass_vni20"], "gap", f"{row['min_gap_to_vni20']:.2f}", "cagr", f"{row['cagr']:.2f}", yearly_edge_row(row), flush=True)
        if len(rows) % 40 == 0:
            pd.DataFrame(rows).sort_values(["pass_vni20", "min_gap_to_vni20", "pass_vni30", "cagr"], ascending=[False, False, False, False]).to_csv(OUT / "stress20_results.csv", index=False)

    if not rows:
        raise RuntimeError("no rows")
    df = pd.DataFrame(rows).sort_values(["pass_vni20", "min_gap_to_vni20", "pass_vni30", "cagr"], ascending=[False, False, False, False])
    df.to_csv(OUT / "stress20_results.csv", index=False)
    if best_payload:
        row, eq, trades, holdings = best_payload
        eq.to_parquet(OUT / "best_stress20_equity.parquet", index=False)
        trades.to_csv(OUT / "best_stress20_trades.csv", index=False)
        holdings.to_parquet(OUT / "best_stress20_holdings.parquet", index=False)
        (OUT / "best_stress20_metrics.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        lines = [
            "# Steady Trend Overlay Stress20",
            "",
            "Research-only. Dashboard remains blocked.",
            "",
            f"Best VNI+20: {int(row['pass_vni20'])}/6",
            f"Best VNI+30: {int(row['pass_vni30'])}/6",
            f"CAGR: {float(row['cagr']):.2f}%",
            f"MaxDD: {float(row['maxdd']):.2f}%",
            f"Min gap to VNI+20: {float(row['min_gap_to_vni20']):.2f}pp",
            "",
            "| Year | Edge | +20 | +30 |",
            "|---|---:|---|---|",
        ]
        for y in YEARS:
            edge = float(row[f"edge_y{y}"])
            lines.append(f"| {y} | {edge:.1f}pp | {'YES' if edge >= 20 else 'NO'} | {'YES' if edge >= 30 else 'NO'} |")
        (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(df.head(20)[["run_id", "mode", "score_mode", "alpha", "top_n", "liq_min", "pass_vni20", "pass_vni30", "min_gap_to_vni20", "cagr", "maxdd", "edge_y2023", "edge_y2024", "edge_y2025", "edge_y2026"]].to_string(index=False))


if __name__ == "__main__":
    main()
