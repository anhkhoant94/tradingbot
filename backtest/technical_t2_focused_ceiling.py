from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v2 as v2
import technical_t2_portfolio_v4 as v4
import technical_t2_walk_forward_strict as wf
from technical_t2_stock_rrg import load_panel_with_rrg, rrg_score
from technical_t2_breakout_accel import breakout_score
from technical_t2_rebound_reversal import rebound_score


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
OUT = STATE_DIR / "focused_ceiling"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")


def score(week: pd.DataFrame, state: str, mode: str) -> pd.Series:
    if mode == "composite":
        return v2.subblend_score(week, state, "cash")
    if mode == "rs_trend":
        return v4.score_picker(week, state, "cash", "rs_trend")
    if mode in {"leading", "improving", "hybrid"}:
        return rrg_score(week, mode)
    if mode in {"fresh_breakout", "parabolic", "liquid_breakout"}:
        return breakout_score(week, mode)
    if mode in {"rebound", "reclaim", "rebound_composite"}:
        return rebound_score(week, mode)
    raise ValueError(mode)


def build_targets(mode: str, holdings: int, entry_band: float):
    states = v4.load_states()
    panel = load_panel_with_rrg()
    grp = panel.groupby("symbol", group_keys=False)
    panel["ret_4w"] = grp["close"].pct_change(4)
    panel["ret_8w"] = grp["close"].pct_change(8)
    panel["sma20_gap"] = panel["close"] / panel["sma20"].replace(0, np.nan) - 1.0
    panel["sma50_gap"] = panel["close"] / panel["sma50"].replace(0, np.nan) - 1.0
    panel["fresh_high"] = panel["close"] / grp["close"].shift(1).rolling(52, min_periods=20).max().reset_index(level=0, drop=True)
    panel = panel[(panel["date"] <= states["date"].max()) & (panel["avg_value_20d_bil"] >= 3.0) & (panel["close"] >= 5.0)].copy()
    vni_dates = [pd.Timestamp(x) for x in v1.load_vni()["date"].tolist()]
    rows = []
    signal_dates = []
    signal_meta = {}
    last_state = None
    for idx, st in enumerate(states.itertuples(index=False)):
        friday = pd.Timestamp(st.date)
        exec_date = v1.next_trading_day(vni_dates, friday)
        if exec_date is None or exec_date < TARGET_START:
            continue
        state = str(st.effective_state)
        state_changed = last_state is not None and state != last_state
        last_state = state
        include_signal = (idx % 2 == 0) or state_changed or state == "risk_off"
        if not include_signal:
            continue
        signal_dates.append(exec_date)
        signal_meta[exec_date] = {"signal_friday": friday, "state": state}
        if state == "risk_off":
            continue
        exposure = 1.0 if state in {"broad_trend", "narrow_leadership"} else 0.85
        max_weight = 1.0 if holdings == 1 else min(0.50, 1.0 / holdings)
        week = panel[panel["date"].eq(friday)].copy()
        week["score"] = score(week, state, mode)
        week = week[
            (week["avg_value_20d_bil"] >= 5.0)
            & (week["rs_13w"] > -0.15)
            & (week["close"] >= week["sma100"] * 0.90)
        ].copy()
        week = week.replace([np.inf, -np.inf], np.nan).dropna(subset=["score"])
        selected = week.sort_values(["score", "avg_value_20d_bil"], ascending=[False, False]).head(holdings)
        if selected.empty:
            continue
        weight = min(max_weight, exposure / len(selected))
        for row in selected.itertuples(index=False):
            rows.append({
                "signal_friday": friday,
                "date": exec_date,
                "symbol": row.symbol,
                "state": state,
                "weight": float(weight),
                "score": float(row.score),
                "entry_band": float(entry_band),
                "mode": mode,
                "holdings": int(holdings),
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def run_cell(mode: str, holdings: int, entry_band: float, slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta = build_targets(mode, holdings, entry_band)
    eq, trades, metrics = wf.simulate_period(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=f"{mode}_h{holdings}_entry{entry_band}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        any_state_brake=False,
        start_year=2021,
        end_year=2026,
    )
    metrics["mode"] = mode
    metrics["holdings"] = int(holdings)
    metrics["entry_band"] = float(entry_band)
    metrics["slippage_bps"] = int(slippage_bps)
    metrics["min_liq_bil"] = float(min_liq_bil)
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    modes = ["composite", "rs_trend", "hybrid", "improving", "liquid_breakout", "parabolic", "rebound_composite"]
    rows = []
    for mode in modes:
        for holdings in [1, 2, 3]:
            for entry_band in [0.0, 0.01, 0.03]:
                eq, trades, metrics, targets = run_cell(mode, holdings, entry_band, 15, 3.0)
                rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    best = search.iloc[0]
    stress_rows = []
    best_key = None
    best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq, trades, metrics, targets = run_cell(str(best["mode"]), int(best["holdings"]), entry_band, slippage_bps, min_liq_bil)
                stress_rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
                key = (int(metrics["pass_vni20"]), float(metrics["min_edge_vs_vni"]), float(metrics["cagr"]), -abs(float(metrics["maxdd"])))
                if best_key is None or key > best_key:
                    best_key = key
                    best_payload = (eq, trades, metrics, targets)
    stress = pd.DataFrame(stress_rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(stress, OUT / "stress_grid_results.csv")
    eq, trades, metrics, targets = best_payload
    v1.atomic_write_frame(eq, OUT / "daily_lot_equity.parquet")
    v1.atomic_write_frame(trades, OUT / "daily_lot_trades.parquet")
    v1.atomic_write_frame(targets, OUT / "weekly_targets.parquet")
    yearly = pd.DataFrame(metrics["yearly_rows"])
    v1.atomic_write_frame(yearly, OUT / "yearly_metrics.csv")
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_FOCUSED_CEILING",
        "dashboard_status": "BLOCKED",
        "verdict": "CEILING_RESEARCH_ONLY",
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
    }
    lines = [
        "# Technical T2 Focused Ceiling",
        "",
        "Verdict: **CEILING_RESEARCH_ONLY**",
        "",
        "Concentrated pure technical ceiling test. Not production due concentration risk.",
        "",
        "## Best / Stress Result",
        "",
        f"- Mode: {metrics['mode']}",
        f"- Holdings: {metrics['holdings']}",
        f"- Entry band: {metrics['entry_band'] * 100:.1f}%",
        f"- VNI+20 pass: {int(metrics['pass_vni20'])}/6",
        f"- VNI+30 pass: {int(metrics['pass_vni30'])}/6",
        f"- CAGR: {metrics['cagr']:.2f}%",
        f"- MaxDD: {metrics['maxdd']:.2f}%",
        f"- Min edge: {metrics['min_edge_vs_vni']:.2f}pp",
        "",
        yearly.to_markdown(index=False),
        "",
        "## Search Top Rows",
        "",
        search.head(20).to_markdown(index=False),
        "",
        "## Stress",
        "",
        stress.to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "focused_ceiling_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
