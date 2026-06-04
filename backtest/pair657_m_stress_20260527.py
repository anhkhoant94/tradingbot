"""Robustness tests for Claude's M_cap55_V8_adapt30bb candidate.

The candidate is promising, but the dashboard should not promote it before the
cheap failure checks pass:

- execution cost stress
- liquidity/participation stress
- parameter plateau around cap, broad-bull cap, and V8 threshold
- single-symbol dependency stress

All simulations are strict daily execution using existing cached histories.
Signal holdings are generated from information available at the signal date,
then executed by the existing T+2.5 simulator.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest"))

from backtest.beat_vni30_daily_execution_sim import (  # noqa: E402
    align_history_to_calendar,
    load_daily_history,
    simulate_daily,
)
from backtest.pair657_sideways_guard_strict_daily_20260527 import load_holdings, load_vni_2012  # noqa: E402


OUT = ROOT / "output" / "beat_vni30_parallel" / "pair657_m_stress_20260527"
OUT.mkdir(parents=True, exist_ok=True)


def adaptive_recap(
    holdings: pd.DataFrame,
    panel: pd.DataFrame,
    broad_bull_cap: float = 0.30,
    default_cap: float = 0.50,
    top_k: int = 1,
) -> pd.DataFrame:
    """Cap the strongest pick more aggressively outside broad bull regimes."""
    h = holdings.merge(panel[["date", "regime"]], on="date", how="left")
    rows = []
    for date, group in h.groupby("date", sort=True):
        g = group.sort_values("weight", ascending=False).reset_index(drop=True)
        gross = float(pd.to_numeric(g["weight"], errors="coerce").fillna(0.0).sum())
        n = len(g)
        if n == 0 or gross <= 0:
            continue
        is_broad_bull = str(g["regime"].iloc[0]) == "BROAD_BULL"
        top_cap = float(broad_bull_cap if is_broad_bull else default_cap)
        if n <= top_k:
            weights = [min(top_cap, gross / n) for _ in range(n)]
        else:
            top_alloc = min(gross, top_cap * top_k)
            rest_alloc = max(0.0, gross - top_alloc)
            rest = pd.to_numeric(g["weight"].iloc[top_k:], errors="coerce").fillna(0.0).to_numpy()
            rest_sum = float(rest.sum())
            if rest_sum > 0:
                rest_weights = list(rest_alloc * rest / rest_sum)
            else:
                rest_weights = [rest_alloc / (n - top_k)] * (n - top_k)
            weights = [top_cap] * top_k + rest_weights
        for symbol, weight in zip(g["symbol"], weights):
            rows.append({"date": date, "symbol": symbol, "weight": float(weight)})
    return pd.DataFrame(rows)


def load_panel() -> pd.DataFrame:
    candidates = [
        Path("/tmp/regime_panel.parquet"),
        ROOT / ".cache" / "tmp" / "regime_panel.parquet",
    ]
    for path in candidates:
        if path.exists():
            panel = pd.read_parquet(path)
            panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
            keep = ["date", "regime", "vni_range_13", "median_ret13", "high_liq_ret13_median", "breadth_ma30"]
            return panel[[c for c in keep if c in panel.columns]].drop_duplicates("date")
    raise FileNotFoundError("regime_panel.parquet not found")


def v8_cash_dates(vni_w: pd.DataFrame, threshold: float = -0.08, lag: int = 1) -> set[pd.Timestamp]:
    v = vni_w.copy()
    v["ret13"] = pd.to_numeric(v["vni_close"], errors="coerce").pct_change(13)
    v["sig"] = v["ret13"] < float(threshold)
    v["sig_lag"] = v["sig"].shift(int(lag)).fillna(False)
    return set(pd.to_datetime(v.loc[v["sig_lag"], "date"]).dt.normalize())


def apply_cash_overlay(holdings: pd.DataFrame, skip_dates: set[pd.Timestamp]) -> pd.DataFrame:
    return holdings[~holdings["date"].isin(skip_dates)].copy()


def build_candidate(
    *,
    default_cap: float = 0.55,
    broad_bull_cap: float = 0.30,
    v8_threshold: float = -0.08,
    v8_lag: int = 1,
) -> tuple[pd.DataFrame, list[pd.Timestamp], pd.DataFrame]:
    base = load_holdings()
    base["date"] = pd.to_datetime(base["date"]).dt.normalize()
    all_signal_dates = sorted(pd.Timestamp(x) for x in base["date"].dropna().unique())

    panel = load_panel()
    h = base.merge(panel, on="date", how="left")
    is_side = h["regime"].eq("SIDEWAYS")
    is_bear = h["regime"].eq("BEAR")
    deadside_drop = (
        is_side
        & pd.to_numeric(h["vni_range_13"], errors="coerce").le(0.070)
        & pd.to_numeric(h["median_ret13"], errors="coerce").le(0.020)
        & pd.to_numeric(h["high_liq_ret13_median"], errors="coerce").le(0.000)
    ) | is_bear
    guarded = h.loc[~deadside_drop, ["date", "symbol", "weight"]].copy()

    vni_daily = load_vni_2012()
    weekly_dates = sorted(set(base["date"].dt.normalize()))
    vni_w = pd.DataFrame({"date": pd.to_datetime(weekly_dates)})
    vni_w = vni_w.merge(vni_daily.rename(columns={"close": "vni_close"})[["date", "vni_close"]], on="date", how="left")
    vni_w["vni_close"] = pd.to_numeric(vni_w["vni_close"], errors="coerce").ffill()

    adapted = adaptive_recap(guarded, panel, broad_bull_cap=broad_bull_cap, default_cap=default_cap, top_k=1)
    final = apply_cash_overlay(adapted, v8_cash_dates(vni_w, threshold=v8_threshold, lag=v8_lag))
    return final[["date", "symbol", "weight"]].copy(), all_signal_dates, vni_daily


def full_yearly(eq: pd.DataFrame, vni: pd.DataFrame) -> pd.DataFrame:
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"]).dt.normalize()
    vni = vni.copy()
    vni["date"] = pd.to_datetime(vni["date"]).dt.normalize()
    rows = []
    for year, group in eq.groupby(eq["date"].dt.year):
        if len(group) < 2:
            continue
        prev_eq = eq[eq["date"] < pd.Timestamp(f"{int(year)}-01-01")]
        start_nav = float(prev_eq["nav"].iloc[-1]) if not prev_eq.empty else float(group["nav"].iloc[0])
        end_nav = float(group["nav"].iloc[-1])
        strategy = (end_nav / start_nav - 1.0) * 100.0 if start_nav > 0 else np.nan
        end_date = pd.Timestamp(group["date"].iloc[-1])
        prev_vni = vni[vni["date"] < pd.Timestamp(f"{int(year)}-01-01")]
        year_vni = vni[(vni["date"].dt.year == int(year)) & (vni["date"] <= end_date)]
        if year_vni.empty:
            continue
        base_close = float(prev_vni["close"].iloc[-1]) if not prev_vni.empty else float(year_vni["close"].iloc[0])
        vni_ret = (float(year_vni["close"].iloc[-1]) / base_close - 1.0) * 100.0 if base_close > 0 else np.nan
        edge = strategy - vni_ret
        rows.append(
            {
                "year": int(year),
                "strategy": strategy,
                "vni": vni_ret,
                "edge": edge,
                "pass_v15": bool(edge >= 15.0),
                "pass_v20": bool(edge >= 20.0),
                "pass_v30": bool(edge >= 30.0),
            }
        )
    return pd.DataFrame(rows)


def prepare_history(holdings: pd.DataFrame, signal_dates: list[pd.Timestamp], vni: pd.DataFrame) -> dict[str, pd.DataFrame]:
    symbols = sorted(holdings["symbol"].astype(str).str.upper().unique())
    daily_dates = [
        pd.Timestamp(x)
        for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()
    ]
    return align_history_to_calendar(load_daily_history(symbols), daily_dates)


def simulate(
    label: str,
    holdings: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    hist: dict[str, pd.DataFrame],
    vni: pd.DataFrame,
    *,
    extra_slippage: float = 0.0005,
    min_liq_bil: float = 0.0,
    max_participation: float = 0.0,
    nav_scale_bil: float = 1.0,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    eq, trades, metrics = simulate_daily(
        holdings.copy(),
        hist,
        vni,
        gap_threshold=0.09,
        limit_buffer=0.0,
        pullback_sessions=2,
        min_sell_sessions=3,
        daily_stop_loss=0.0,
        extra_slippage_per_side=float(extra_slippage),
        signal_dates=signal_dates,
        nav0=1.0,
        min_execution_value_bil=float(min_liq_bil),
        max_participation=float(max_participation),
        nav_scale_bil=float(nav_scale_bil),
    )
    yr = full_yearly(eq, vni)
    out = dict(metrics)
    out.update(
        {
            "label": label,
            "extra_slippage_bps": float(extra_slippage) * 10000.0,
            "min_liq_bil": float(min_liq_bil),
            "max_participation": float(max_participation),
            "nav_scale_bil": float(nav_scale_bil),
            "full_years": int(len(yr)),
            "full_pass_v15": int(yr["pass_v15"].sum()) if not yr.empty else 0,
            "full_pass_v20": int(yr["pass_v20"].sum()) if not yr.empty else 0,
            "full_pass_v30": int(yr["pass_v30"].sum()) if not yr.empty else 0,
            "full_min_edge": float(yr["edge"].min()) if not yr.empty else np.nan,
            "trade_count": int(len(trades)),
            "miss_buy_count": int((trades.get("side", pd.Series(dtype=str)).astype(str) == "MISS_BUY").sum()) if not trades.empty else 0,
            "avg_exposure": float(eq["exposure"].mean()) if not eq.empty and "exposure" in eq else np.nan,
        }
    )
    return out, eq, trades, yr


def write_markdown(summary: pd.DataFrame, yearly: dict[str, pd.DataFrame]) -> None:
    cols = [
        "label",
        "cagr",
        "maxdd",
        "sharpe",
        "pass_vni30",
        "full_pass_v30",
        "full_pass_v20",
        "full_min_edge",
        "trade_count",
        "miss_buy_count",
        "avg_exposure",
    ]
    lines = [
        "# Pair657 M Stress - 2026-05-27",
        "",
        "Strict daily execution. Full-year pass counts cover all available years; simulator pass_vni30 is the existing 2021-2026 gate.",
        "",
        "## Summary",
        "",
        summary[[c for c in cols if c in summary.columns]].to_markdown(index=False, floatfmt=".2f"),
    ]
    for label, yr in yearly.items():
        if label in {"M_base_5bps", "M_cost_15bps", "M_cost_30bps", "M_liq5b_part5pct_nav1b"}:
            lines += ["", f"## Yearly {label}", "", yr.to_markdown(index=False, floatfmt=".2f")]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    base_holdings, signal_dates, vni = build_candidate()
    hist = prepare_history(base_holdings, signal_dates, vni)

    rows: list[dict] = []
    yearly: dict[str, pd.DataFrame] = {}
    equity_to_save: dict[str, pd.DataFrame] = {}
    trades_to_save: dict[str, pd.DataFrame] = {}

    tests = [
        ("M_base_5bps", base_holdings, 0.0005, 0.0, 0.0, 1.0),
        ("M_cost_15bps", base_holdings, 0.0015, 0.0, 0.0, 1.0),
        ("M_cost_30bps", base_holdings, 0.0030, 0.0, 0.0, 1.0),
        ("M_liq5b", base_holdings, 0.0005, 5.0, 0.0, 1.0),
        ("M_liq5b_part5pct_nav1b", base_holdings, 0.0005, 5.0, 0.05, 1.0),
        ("M_liq5b_part5pct_nav3b", base_holdings, 0.0005, 5.0, 0.05, 3.0),
    ]

    for label, hv, slip, liq, part, nav_scale in tests:
        row, eq, trades, yr = simulate(
            label,
            hv,
            signal_dates,
            hist,
            vni,
            extra_slippage=slip,
            min_liq_bil=liq,
            max_participation=part,
            nav_scale_bil=nav_scale,
        )
        rows.append(row)
        yearly[label] = yr
        equity_to_save[label] = eq
        trades_to_save[label] = trades
        print(label, f"CAGR={row['cagr']:.2f}", f"MDD={row['maxdd']:.2f}", f"full_pv30={row['full_pass_v30']}", flush=True)

    # Parameter plateau: keep execution at Claude's cost and vary one family at a time.
    plateau_params = []
    for default_cap in [0.50, 0.55, 0.60, 0.66]:
        plateau_params.append((f"plateau_defaultcap_{default_cap:.2f}", default_cap, 0.30, -0.08))
    for bb_cap in [0.20, 0.25, 0.30, 0.35, 0.40]:
        plateau_params.append((f"plateau_bbcap_{bb_cap:.2f}", 0.55, bb_cap, -0.08))
    for threshold in [-0.06, -0.08, -0.10]:
        plateau_params.append((f"plateau_v8_{threshold:.2f}", 0.55, 0.30, threshold))

    for label, default_cap, bb_cap, threshold in plateau_params:
        hv, _, _ = build_candidate(default_cap=default_cap, broad_bull_cap=bb_cap, v8_threshold=threshold)
        hist_local = hist if set(hv["symbol"].unique()).issubset(hist.keys()) else prepare_history(hv, signal_dates, vni)
        row, eq, trades, yr = simulate(label, hv, signal_dates, hist_local, vni, extra_slippage=0.0005)
        rows.append(row)
        yearly[label] = yr
        print(label, f"CAGR={row['cagr']:.2f}", f"MDD={row['maxdd']:.2f}", f"full_pv30={row['full_pass_v30']}", flush=True)

    # Remove-symbol stress: full run for largest cumulative weight contributors.
    sym_weight = base_holdings.groupby("symbol")["weight"].sum().sort_values(ascending=False)
    for symbol in sym_weight.head(30).index:
        hv = base_holdings[base_holdings["symbol"] != symbol].copy()
        hv["weight"] = hv.groupby("date")["weight"].transform(lambda s: s / s.sum() if s.sum() > 0 else s)
        hv = hv.replace([np.inf, -np.inf], np.nan).dropna(subset=["weight"])
        row, eq, trades, yr = simulate(f"drop_{symbol}", hv, signal_dates, hist, vni, extra_slippage=0.0005)
        row["dropped_symbol"] = symbol
        row["dropped_weight_sum"] = float(sym_weight.loc[symbol])
        rows.append(row)
        print(f"drop_{symbol}", f"CAGR={row['cagr']:.2f}", f"MDD={row['maxdd']:.2f}", f"full_pv30={row['full_pass_v30']}", flush=True)

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "stress_summary.csv", index=False)
    for label, yr in yearly.items():
        yr.to_csv(OUT / f"yearly_{label}.csv", index=False)
    for label, eq in equity_to_save.items():
        eq.to_parquet(OUT / f"equity_{label}.parquet", index=False)
    for label, trades in trades_to_save.items():
        trades.to_csv(OUT / f"trades_{label}.csv", index=False)
    write_markdown(summary, yearly)

    best_report = {
        "base_holdings_rows": int(len(base_holdings)),
        "symbols": int(base_holdings["symbol"].nunique()),
        "signal_dates": int(len(signal_dates)),
        "output": str(OUT),
    }
    (OUT / "run_meta.json").write_text(json.dumps(best_report, indent=2), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
