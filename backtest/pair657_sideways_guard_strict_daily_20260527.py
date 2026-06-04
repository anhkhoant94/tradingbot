"""Strict daily smoke for more selective Pair657 sideways guards.

Claude is still repairing raw historical liquidity. This script deliberately
uses only already-synced artifacts and existing daily history, then tests
whether a narrower "dead sideways" guard fixes the 2025 underperformance
created by the blunt no-SIDEWAYS guard.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest"))

from backtest.beat_vni30_daily_execution_sim import (  # noqa: E402
    align_history_to_calendar,
    load_daily_history,
    simulate_daily,
)
from backtest.pair657_regime_diagnostic_20260527 import (  # noqa: E402
    label_regimes,
    label_style_regimes,
    load_market_panel,
)


OUT = ROOT / "output" / "beat_vni30_parallel" / "pair657_sideways_guard_strict_daily_20260527"
OUT.mkdir(parents=True, exist_ok=True)

HOLD_2016 = (
    ROOT
    / "output"
    / "beat_vni30_parallel"
    / "pair657_2016_2021_extension_20260527"
    / "pair657_w10_cap40"
    / "holdings.parquet"
)
HOLD_2021 = (
    ROOT
    / "output"
    / "beat_vni30_parallel"
    / "codex_pair657_direct_combo_20260527_fullsignals"
    / "best_holdings.parquet"
)
VNI_2012 = ROOT / ".cache" / "backtest" / "vnindex_daily_2012.parquet"


def load_vni_2012() -> pd.DataFrame:
    vni = pd.read_parquet(VNI_2012)
    date_col = "date" if "date" in vni.columns else "time"
    vni = vni.rename(columns={date_col: "date"})
    vni["date"] = pd.to_datetime(vni["date"]).dt.normalize()
    return vni[["date", "close"]].dropna().sort_values("date").reset_index(drop=True)


def load_holdings() -> pd.DataFrame:
    a = pd.read_parquet(HOLD_2016)
    b = pd.read_parquet(HOLD_2021)
    for df in (a, b):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)
    b = b[b["date"] > a["date"].max()].copy()
    return pd.concat([a, b], ignore_index=True).sort_values(["date", "symbol"])


def load_ranks() -> pd.DataFrame:
    cols = ["date", "symbol", "high_rank_all"]
    a = pd.read_parquet(
        ROOT / ".cache" / "backtest" / "yearly_floor_candidate_matrix_2016_2021_fullpanel.parquet",
        columns=cols,
    )
    b = pd.read_parquet(
        ROOT / ".cache" / "backtest" / "yearly_floor_candidate_matrix_live_preview.parquet",
        columns=cols,
    )
    for df in (a, b):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["high_rank_all"] = pd.to_numeric(df["high_rank_all"], errors="coerce")
    b = b[b["date"] > a["date"].max()].copy()
    return pd.concat([a, b], ignore_index=True)


def run_one(
    label: str,
    holdings: pd.DataFrame,
    all_signal_dates: list[pd.Timestamp],
    hist: dict[str, pd.DataFrame],
    vni: pd.DataFrame,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    eq, trades, metrics = simulate_daily(
        holdings[["date", "symbol", "weight"]].copy(),
        hist,
        vni,
        gap_threshold=0.09,
        limit_buffer=0.0,
        pullback_sessions=2,
        min_sell_sessions=3,
        daily_stop_loss=0.0,
        extra_slippage_per_side=0.0005,
        signal_dates=all_signal_dates,
        nav0=1.0,
    )
    metrics = dict(metrics)
    metrics["label"] = label
    metrics["trade_count"] = int(len(trades))
    metrics["avg_exposure"] = float(eq["exposure"].mean()) if not eq.empty and "exposure" in eq else 0.0
    return metrics, eq, trades


def main() -> None:
    holdings = load_holdings()
    all_signal_dates = sorted(pd.Timestamp(x) for x in holdings["date"].dropna().unique())

    panel = label_style_regimes(label_regimes(load_market_panel()))
    keep = [
        "date",
        "regime",
        "style_regime",
        "vni_ret4",
        "vni_ret13",
        "vni_ret26",
        "vni_range_13",
        "vni_vol13",
        "breadth_ma30",
        "breadth_ret13_pos",
        "median_ret13",
        "high_liq_ret13_median",
        "low_liq_ret13_median",
        "micro_adv_ret13",
    ]
    panel = panel[keep].drop_duplicates("date")
    h = holdings.merge(panel, on="date", how="left").merge(load_ranks(), on=["date", "symbol"], how="left")

    is_side = h["regime"].eq("SIDEWAYS")
    is_bear = h["regime"].eq("BEAR")
    low_range = pd.to_numeric(h["vni_range_13"], errors="coerce").le(0.070)
    low_range_loose = pd.to_numeric(h["vni_range_13"], errors="coerce").le(0.085)
    weak_breadth = pd.to_numeric(h["breadth_ret13_pos"], errors="coerce").le(0.55)
    weak_median = pd.to_numeric(h["median_ret13"], errors="coerce").le(0.00)
    weak_liquid = pd.to_numeric(h["high_liq_ret13_median"], errors="coerce").le(0.02)
    no_micro_edge = pd.to_numeric(h["micro_adv_ret13"], errors="coerce").le(0.02)
    strong_range_or_vol = (
        pd.to_numeric(h["vni_range_13"], errors="coerce").ge(0.075)
        | pd.to_numeric(h["vni_vol13"], errors="coerce").ge(0.145)
        | pd.to_numeric(h["vni_ret4"], errors="coerce").ge(0.04)
    )
    target_quality = pd.to_numeric(h["high_rank_all"], errors="coerce").ge(55)

    drop_low_range_side = is_side & low_range
    drop_dead_side_1 = is_side & low_range & weak_breadth
    drop_dead_side_2 = is_side & low_range_loose & weak_median & weak_liquid
    drop_dead_side_3 = is_side & low_range_loose & weak_median & no_micro_edge
    drop_side_unless_active = is_side & ~strong_range_or_vol
    drop_side_unless_active_or_rank = is_side & ~(strong_range_or_vol | target_quality)

    variants = {
        "baseline": h,
        "no_side": h[~is_side].copy(),
        "no_side_bear": h[~(is_side | is_bear)].copy(),
        "skip_low_range_side": h[~drop_low_range_side].copy(),
        "skip_dead_side_breadth": h[~drop_dead_side_1].copy(),
        "skip_dead_side_liquid": h[~drop_dead_side_2].copy(),
        "skip_dead_side_micro": h[~drop_dead_side_3].copy(),
        "side_active_override": h[~drop_side_unless_active].copy(),
        "side_active_or_rank55": h[~drop_side_unless_active_or_rank].copy(),
        "side_active_skip_bear": h[~(drop_side_unless_active | is_bear)].copy(),
        "side_active_or_rank55_skip_bear": h[~(drop_side_unless_active_or_rank | is_bear)].copy(),
    }

    symbols = sorted(set(h["symbol"].astype(str)))
    vni = load_vni_2012()
    daily_dates = [
        pd.Timestamp(x)
        for x in vni[(vni["date"] >= all_signal_dates[0]) & (vni["date"] <= all_signal_dates[-1])]["date"].tolist()
    ]
    hist = align_history_to_calendar(load_daily_history(symbols), daily_dates)

    rows = []
    for label, hv in variants.items():
        m, eq, trades = run_one(label, hv, all_signal_dates, hist, vni)
        rows.append(m)
        eq.to_parquet(OUT / f"equity_{label}.parquet", index=False)
        trades.to_csv(OUT / f"trades_{label}.csv", index=False)
        print(label, m, flush=True)

    summary = pd.DataFrame(rows)
    sort_cols = [c for c in ["pass_vni30", "pass30", "sharpe", "cagr"] if c in summary.columns]
    summary = summary.sort_values(sort_cols, ascending=[False] * len(sort_cols))
    summary.to_csv(OUT / "strict_sideways_guard_summary.csv", index=False)

    lines = [
        "# Pair657 Selective Sideways Guard - Strict Daily Smoke",
        "",
        "Scope: existing synced artifacts only. No price fetch; raw-liquidity repair is still pending.",
        "",
        "Goal: reduce 2019/2024 sideways damage without switching off profitable 2025 sideways trades.",
        "",
        summary.to_markdown(index=False, floatfmt=".2f"),
        "",
        "Promotion rule: only consider a variant if strict daily results improve drawdown without creating a new yearly VNI gap. Final promotion still waits for Claude's repaired raw-liquidity dataset.",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
