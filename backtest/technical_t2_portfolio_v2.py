from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "portfolio_v2"
PRODUCTION_MIN_LIQ_BIL = 3.0


EXPOSURE_PROFILES = {
    "balanced": {
        "broad_trend": {"exposure": 0.95, "max_holdings": 6, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.65, "max_holdings": 4, "max_weight": 0.33},
        "recovery": {"exposure": 0.50, "max_holdings": 5, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "lower_risk": {
        "broad_trend": {"exposure": 0.85, "max_holdings": 6, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.55, "max_holdings": 4, "max_weight": 0.30},
        "recovery": {"exposure": 0.40, "max_holdings": 4, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "full_broad": {
        "broad_trend": {"exposure": 1.00, "max_holdings": 7, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.75, "max_holdings": 4, "max_weight": 0.33},
        "recovery": {"exposure": 0.60, "max_holdings": 5, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
}


def state_params(profile: str, risk_mode: str) -> dict:
    params = {k: dict(v) for k, v in EXPOSURE_PROFILES[profile].items()}
    if risk_mode == "volume":
        params["risk_off"] = {"exposure": 0.25, "max_holdings": 4, "max_weight": 0.08}
    return params


def subblend_score(week: pd.DataFrame, state: str, risk_mode: str) -> pd.Series:
    z = v1.robust_z
    trend = (
        0.40 * z(week["rs_13w"])
        + 0.20 * z(week["rs_26w"])
        + 0.25 * z(week["breakout_quality_100d"])
        + 0.15 * z(week["high52_proximity"])
    )
    base = (
        0.40 * z(week["pullback_quality"])
        + 0.25 * z(week["vol_contraction"])
        + 0.25 * z(week["volume_expansion_20_60"])
        + 0.10 * z(week["rs_26w"])
    )
    if state == "risk_off" and risk_mode == "volume":
        return (
            0.65 * z(week["volume_expansion_20_60"])
            + 0.15 * z(week["pullback_quality"])
            + 0.10 * z(week["rs_26w"])
            + 0.10 * z(week["vol_contraction"])
        )
    if state == "broad_trend":
        return 0.70 * trend + 0.30 * base
    if state == "narrow_leadership":
        return 0.80 * trend + 0.20 * base
    if state == "recovery":
        return 0.40 * trend + 0.60 * base
    return trend


def build_targets(
    *,
    profile: str,
    risk_mode: str,
    schedule: str,
    entry_band: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    states = pd.read_parquet(STATE_DIR / "weekly_state_labels.parquet").copy()
    states["date"] = pd.to_datetime(states["date"])
    states = states.sort_values("date").reset_index(drop=True)
    states["raw_risk_count_4w"] = states["raw_state"].eq("risk_off").rolling(4, min_periods=1).sum()
    states["raw_risk_count_2w"] = states["raw_state"].eq("risk_off").rolling(2, min_periods=1).sum()
    states["effective_state"] = states["state"].astype(str)
    recovery_guard = states["state"].eq("recovery") & (states["raw_risk_count_4w"] >= 2)
    crash_override = states["raw_risk_count_2w"].ge(2) & (
        (states["vni_ret_4w"] <= -0.08) | (states["pct_above_sma50"] < 0.20)
    )
    states.loc[crash_override, "effective_state"] = "risk_off"
    states["recovery_guard"] = recovery_guard

    panel = pd.read_parquet(TECH_PANEL).copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["symbol"] = panel["symbol"].astype(str).str.upper()
    panel = panel[
        (panel["date"] >= "2020-12-01")
        & (panel["date"] <= states["date"].max())
        & (panel["avg_value_20d_bil"] >= PRODUCTION_MIN_LIQ_BIL)
        & (panel["close"] >= 5.0)
    ].copy()

    vni_dates = [pd.Timestamp(x) for x in v1.load_vni()["date"].tolist()]
    params_by_state = state_params(profile, risk_mode)
    rows: list[dict] = []
    signal_dates: list[pd.Timestamp] = []
    last_state = None
    for idx, st in enumerate(states.itertuples(index=False)):
        friday = pd.Timestamp(st.date)
        exec_date = v1.next_trading_day(vni_dates, friday)
        if exec_date is None or exec_date < pd.Timestamp("2021-01-01"):
            continue
        state = str(st.effective_state)
        state_changed = last_state is not None and state != last_state
        last_state = state
        include_signal = True
        if schedule == "biweekly_state":
            include_signal = (idx % 2 == 0) or state_changed or state == "risk_off"
        if not include_signal:
            continue
        signal_dates.append(exec_date)
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
        week["score"] = subblend_score(week, state, risk_mode)
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
                "risk_mode": risk_mode,
                "schedule": schedule,
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates))


def run_one(profile: str, risk_mode: str, schedule: str, entry_band: float, slippage_bps: int, min_liq_bil: float) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.DataFrame]:
    targets, signal_dates = build_targets(profile=profile, risk_mode=risk_mode, schedule=schedule, entry_band=entry_band)
    eq, trades, metrics = v1.simulate(
        targets,
        signal_dates,
        variant=f"{profile}_{risk_mode}_{schedule}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
    )
    metrics["profile"] = profile
    metrics["risk_mode"] = risk_mode
    metrics["schedule"] = schedule
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    search_rows: list[dict] = []
    best_key = None
    best = None
    for profile in ["balanced", "lower_risk", "full_broad"]:
        for risk_mode in ["cash", "volume"]:
            for schedule in ["weekly", "biweekly_state"]:
                eq, trades, metrics, targets = run_one(profile, risk_mode, schedule, 0.01, 15, 3.0)
                row = {k: v for k, v in metrics.items() if k != "yearly_rows"}
                search_rows.append(row)
                key = (
                    int(metrics["pass_vni20"]),
                    float(metrics["min_edge_vs_vni"]),
                    float(metrics["cagr"]),
                    -abs(float(metrics["maxdd"])),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best = (profile, risk_mode, schedule, metrics, eq, trades, targets)
    search = pd.DataFrame(search_rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    if best is None:
        return

    best_profile, best_risk, best_schedule, _best_metrics, _eq, _trades, _targets = best
    stress_rows = []
    best_stress_key = None
    best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq, trades, metrics, targets = run_one(best_profile, best_risk, best_schedule, entry_band, slippage_bps, min_liq_bil)
                row = {k: v for k, v in metrics.items() if k != "yearly_rows"}
                stress_rows.append(row)
                key = (
                    int(metrics["pass_vni20"]),
                    float(metrics["min_edge_vs_vni"]),
                    float(metrics["cagr"]),
                    -abs(float(metrics["maxdd"])),
                )
                if best_stress_key is None or key > best_stress_key:
                    best_stress_key = key
                    best_payload = (eq, trades, metrics, targets)
    stress = pd.DataFrame(stress_rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(stress, OUT / "stress_grid_results.csv")
    if best_payload is None:
        return
    eq, trades, metrics, targets = best_payload
    v1.atomic_write_frame(eq, OUT / "daily_lot_equity.parquet")
    v1.atomic_write_frame(trades, OUT / "daily_lot_trades.parquet")
    v1.atomic_write_frame(targets, OUT / "weekly_targets.parquet")
    yearly = pd.DataFrame(metrics["yearly_rows"])
    v1.atomic_write_frame(yearly, OUT / "portfolio_yearly_metrics.csv")
    lines = [
        "# Technical T2 Portfolio V2",
        "",
        "Status: research-only structural follow-up. Dashboard remains BLOCKED.",
        "",
        f"Best profile: {metrics['profile']}",
        f"Risk mode: {metrics['risk_mode']}",
        f"Schedule: {metrics['schedule']}",
        f"Entry band: {metrics['entry_band'] * 100:.1f}%",
        f"Slippage: {metrics['slippage_bps']} bps/side",
        f"Min liquidity: {metrics['min_liq_bil']:.1f}b VND/day",
        f"VNI+20 pass: {int(metrics['pass_vni20'])}/6",
        f"VNI+30 pass: {int(metrics['pass_vni30'])}/6",
        f"CAGR: {metrics['cagr']:.2f}%",
        f"MaxDD: {metrics['maxdd']:.2f}%",
        f"Min edge vs VNI: {metrics['min_edge_vs_vni']:.2f}pp",
        f"Trades/year: {metrics['turnover_trades_per_year']:.1f}",
        f"Average exposure: {metrics['avg_exposure'] * 100:.1f}%",
        "",
        "## Yearly Metrics",
        "",
        yearly.to_markdown(index=False),
        "",
        "## Search Results",
        "",
        search.head(12).to_markdown(index=False),
        "",
        "## Stress Grid",
        "",
        stress.to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "candidate_summary.md", "\n".join(lines))
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_E_PORTFOLIO_V2",
        "dashboard_status": "BLOCKED",
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "next_gate": "Claude review; continue research because target not achieved unless pass_vni20==6.",
    }
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
