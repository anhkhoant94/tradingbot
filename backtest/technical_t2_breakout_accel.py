from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v4 as v4
import technical_t2_walk_forward_strict as wf


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "breakout_accel"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")


PROFILES = {
    "focused": {
        "broad_trend": {"exposure": 0.95, "max_holdings": 3, "max_weight": 0.35},
        "narrow_leadership": {"exposure": 0.95, "max_holdings": 3, "max_weight": 0.35},
        "recovery": {"exposure": 0.75, "max_holdings": 3, "max_weight": 0.33},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "balanced_breakout": {
        "broad_trend": {"exposure": 0.90, "max_holdings": 4, "max_weight": 0.28},
        "narrow_leadership": {"exposure": 0.85, "max_holdings": 4, "max_weight": 0.28},
        "recovery": {"exposure": 0.65, "max_holdings": 4, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "risk_capped": {
        "broad_trend": {"exposure": 0.80, "max_holdings": 4, "max_weight": 0.25},
        "narrow_leadership": {"exposure": 0.75, "max_holdings": 4, "max_weight": 0.25},
        "recovery": {"exposure": 0.50, "max_holdings": 4, "max_weight": 0.20},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
}


def load_panel() -> pd.DataFrame:
    panel = pd.read_parquet(TECH_PANEL).copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["symbol"] = panel["symbol"].astype(str).str.upper()
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    grp = panel.groupby("symbol", group_keys=False)
    panel["ret_4w"] = grp["close"].pct_change(4)
    panel["ret_8w"] = grp["close"].pct_change(8)
    panel["value_mom_4w"] = grp["avg_value_20d_bil"].pct_change(4)
    panel["fresh_high"] = panel["close"] / grp["close"].shift(1).rolling(52, min_periods=20).max().reset_index(level=0, drop=True)
    return panel


def breakout_score(week: pd.DataFrame, mode: str) -> pd.Series:
    z = v1.robust_z
    if mode == "fresh_breakout":
        return (
            0.30 * z(week["fresh_high"])
            + 0.25 * z(week["breakout_quality_100d"])
            + 0.20 * z(week["volume_expansion_20_60"])
            + 0.15 * z(week["momentum_accel_13_26"])
            + 0.10 * z(week["avg_value_20d_bil"])
        )
    if mode == "parabolic":
        return (
            0.30 * z(week["ret_4w"])
            + 0.25 * z(week["ret_8w"])
            + 0.20 * z(week["volume_expansion_20_60"])
            + 0.15 * z(week["high52_proximity"])
            + 0.10 * z(week["rs_13w"])
        )
    if mode == "liquid_breakout":
        return (
            0.25 * z(week["avg_value_20d_bil"])
            + 0.25 * z(week["high52_proximity"])
            + 0.20 * z(week["rs_13w"])
            + 0.15 * z(week["breakout_quality_100d"])
            + 0.15 * z(week["volume_expansion_20_60"])
        )
    raise ValueError(mode)


def build_targets(profile: str, schedule: str, mode: str, entry_band: float):
    states = v4.load_states()
    panel = load_panel()
    panel = panel[
        (panel["date"] <= states["date"].max())
        & (panel["avg_value_20d_bil"] >= 3.0)
        & (panel["close"] >= 5.0)
    ].copy()
    vni_dates = [pd.Timestamp(x) for x in v1.load_vni()["date"].tolist()]
    params_by_state = {k: dict(v) for k, v in PROFILES[profile].items()}
    rows = []
    signal_dates = []
    signal_meta = {}
    last_state = None
    for idx, st in enumerate(states.itertuples(index=False)):
        friday = pd.Timestamp(st.date)
        exec_date = v1.next_trading_day(vni_dates, friday)
        if exec_date is None:
            continue
        state = str(st.effective_state)
        state_changed = last_state is not None and state != last_state
        last_state = state
        include_signal = True
        if schedule == "biweekly_state":
            include_signal = (idx % 2 == 0) or state_changed or state == "risk_off"
        if not include_signal or exec_date < TARGET_START:
            continue
        signal_dates.append(exec_date)
        signal_meta[exec_date] = {"signal_friday": friday, "state": state}
        params = dict(params_by_state.get(state, params_by_state["risk_off"]))
        if bool(getattr(st, "recovery_guard", False)) and state == "recovery":
            params["exposure"] = min(float(params["exposure"]), 0.30)
        exposure = float(params["exposure"])
        max_holdings = int(params["max_holdings"])
        max_weight = float(params["max_weight"])
        if exposure <= 0 or max_holdings <= 0:
            continue
        week = panel[panel["date"].eq(friday)].copy()
        if week.empty:
            continue
        week["score"] = breakout_score(week, mode)
        # Generic breakout eligibility: near a major high, strong RS, or a fresh acceleration.
        week = week[
            (
                (week["high52_proximity"] >= 0.92)
                | (week["fresh_high"] >= 0.98)
                | ((week["ret_4w"] > 0.08) & (week["volume_expansion_20_60"] > 0))
            )
            & (week["avg_value_20d_bil"] >= 5.0)
            & (week["rs_13w"] > -0.08)
        ].copy()
        week = week.replace([np.inf, -np.inf], np.nan).dropna(subset=["score"])
        selected = week.sort_values(["score", "avg_value_20d_bil"], ascending=[False, False]).head(max_holdings)
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
                "profile": profile,
                "schedule": schedule,
                "mode": mode,
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def run_cell(profile: str, schedule: str, mode: str, entry_band: float, slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta = build_targets(profile, schedule, mode, entry_band)
    eq, trades, metrics = wf.simulate_period(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=f"{profile}_{schedule}_{mode}_entry{entry_band}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        any_state_brake=False,
        start_year=2021,
        end_year=2026,
    )
    metrics["profile"] = profile
    metrics["schedule"] = schedule
    metrics["mode"] = mode
    metrics["entry_band"] = float(entry_band)
    metrics["slippage_bps"] = int(slippage_bps)
    metrics["min_liq_bil"] = float(min_liq_bil)
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for profile in ["focused", "balanced_breakout", "risk_capped"]:
        for schedule in ["weekly", "biweekly_state"]:
            for mode in ["fresh_breakout", "parabolic", "liquid_breakout"]:
                for entry_band in [0.0, 0.01, 0.03]:
                    eq, trades, metrics, targets = run_cell(profile, schedule, mode, entry_band, 15, 3.0)
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
                eq, trades, metrics, targets = run_cell(str(best["profile"]), str(best["schedule"]), str(best["mode"]), entry_band, slippage_bps, min_liq_bil)
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
        "stage": "T2_BREAKOUT_ACCEL",
        "dashboard_status": "BLOCKED",
        "verdict": "CANDIDATE_NEEDS_AUDIT" if int(metrics["pass_vni20"]) >= 6 else "RESEARCH_ONLY",
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
    }
    lines = [
        "# Technical T2 Breakout Acceleration",
        "",
        f"Verdict: **{status['verdict']}**",
        "",
        "Pure technical concentrated breakout/acceleration family.",
        "",
        "## Best / Stress Result",
        "",
        f"- Profile: {metrics['profile']}",
        f"- Schedule: {metrics['schedule']}",
        f"- Mode: {metrics['mode']}",
        f"- Entry band: {metrics['entry_band'] * 100:.1f}%",
        f"- Slippage: {metrics['slippage_bps']}bps/side",
        f"- Min liquidity: {metrics['min_liq_bil']:.1f}b/day",
        f"- VNI+20 pass: {int(metrics['pass_vni20'])}/6",
        f"- VNI+30 pass: {int(metrics['pass_vni30'])}/6",
        f"- CAGR: {metrics['cagr']:.2f}%",
        f"- MaxDD: {metrics['maxdd']:.2f}%",
        f"- Min edge: {metrics['min_edge_vs_vni']:.2f}pp",
        "",
        yearly.to_markdown(index=False),
        "",
        "## Stress",
        "",
        stress.to_markdown(index=False),
        "",
        "## Search Top Rows",
        "",
        search.head(20).to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "breakout_accel_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
