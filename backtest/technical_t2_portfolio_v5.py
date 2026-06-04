from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v2 as v2
import technical_t2_portfolio_v4 as v4


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "portfolio_v5_dual_sleeve_any_brake"


PROFILES = {
    "balanced_alt": {
        "broad_trend": {"exposure": 0.95, "max_holdings": 6, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.65, "max_holdings": 4, "max_weight": 0.33},
        "recovery": {"exposure": 0.50, "max_holdings": 5, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "narrow_boost": {
        "broad_trend": {"exposure": 0.75, "max_holdings": 5, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.90, "max_holdings": 6, "max_weight": 0.22},
        "recovery": {"exposure": 0.50, "max_holdings": 5, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "recovery_cautious": {
        "broad_trend": {"exposure": 0.85, "max_holdings": 6, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.75, "max_holdings": 5, "max_weight": 0.25},
        "recovery": {"exposure": 0.30, "max_holdings": 4, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
}


def _score(week: pd.DataFrame, state: str, mode: str) -> pd.Series:
    if mode == "composite":
        return v2.subblend_score(week, state, "cash")
    if mode == "rs_trend":
        return v4.score_picker(week, state, "cash", "rs_trend")
    raise ValueError(mode)


def build_dual_targets(
    *,
    profile: str,
    schedule: str,
    rs_share: float,
    entry_band: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp], dict[pd.Timestamp, dict]]:
    states = v4.load_states()
    panel = pd.read_parquet(TECH_PANEL).copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["symbol"] = panel["symbol"].astype(str).str.upper()
    panel = panel[
        (panel["date"] >= "2020-12-01")
        & (panel["date"] <= states["date"].max())
        & (panel["avg_value_20d_bil"] >= 3.0)
        & (panel["close"] >= 5.0)
    ].copy()

    vni_dates = [pd.Timestamp(x) for x in v1.load_vni()["date"].tolist()]
    params_by_state = {k: dict(v) for k, v in PROFILES[profile].items()}
    rows: list[dict] = []
    signal_dates: list[pd.Timestamp] = []
    signal_meta: dict[pd.Timestamp, dict] = {}
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

        rs_n = max(1, min(max_holdings - 1, int(round(max_holdings * rs_share))))
        comp_n = max_holdings - rs_n
        used: set[str] = set()
        sleeves = [
            ("rs_trend", rs_n, exposure * rs_share),
            ("composite", comp_n, exposure * (1.0 - rs_share)),
        ]
        for sleeve_name, sleeve_n, sleeve_exposure in sleeves:
            if sleeve_n <= 0 or sleeve_exposure <= 0:
                continue
            candidates = week[~week["symbol"].isin(used)].copy()
            candidates["score"] = _score(candidates, state, sleeve_name)
            candidates = candidates.replace([np.inf, -np.inf], np.nan).dropna(subset=["score"])
            selected = candidates.sort_values(["score", "avg_value_20d_bil"], ascending=[False, False]).head(sleeve_n)
            if selected.empty:
                continue
            weight = min(max_weight, sleeve_exposure / len(selected))
            for row in selected.itertuples(index=False):
                used.add(row.symbol)
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
                    "rs_share": float(rs_share),
                    "sleeve": sleeve_name,
                    "avg_value_20d_bil": float(row.avg_value_20d_bil),
                })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def run_one(profile: str, schedule: str, rs_share: float, any_state_brake: bool, entry_band: float, slippage_bps: int, min_liq_bil: float):
    targets, signal_dates, signal_meta = build_dual_targets(
        profile=profile,
        schedule=schedule,
        rs_share=rs_share,
        entry_band=entry_band,
    )
    brake_meta = signal_meta
    if any_state_brake:
        # V4's simulator applies the strategy-vs-VNI brake to rows whose meta state is narrow_leadership.
        # Marking all signal rows as eligible implements Claude's requested any-state brake without changing target states.
        brake_meta = {k: {**v, "state": "narrow_leadership"} for k, v in signal_meta.items()}
    eq, trades, metrics = v4.simulate_with_relative_brake(
        targets,
        signal_dates,
        brake_meta,
        variant=f"{profile}_{schedule}_dual_rs{rs_share}_anybrake{int(any_state_brake)}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        relative_brake=any_state_brake,
    )
    metrics["profile"] = profile
    metrics["schedule"] = schedule
    metrics["rs_share"] = rs_share
    metrics["any_state_brake"] = any_state_brake
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    best_key = None
    best = None
    for profile in ["balanced_alt", "narrow_boost", "recovery_cautious"]:
        for schedule in ["weekly", "biweekly_state"]:
            for rs_share in [0.40, 0.50, 0.60]:
                for any_state_brake in [False, True]:
                    eq, trades, metrics, targets = run_one(profile, schedule, rs_share, any_state_brake, 0.01, 15, 3.0)
                    row = {k: v for k, v in metrics.items() if k != "yearly_rows"}
                    rows.append(row)
                    key = (
                        int(metrics["pass_vni20"]),
                        float(metrics["min_edge_vs_vni"]),
                        float(metrics["cagr"]),
                        -abs(float(metrics["maxdd"])),
                    )
                    if best_key is None or key > best_key:
                        best_key = key
                        best = (profile, schedule, rs_share, any_state_brake, metrics)

    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    if best is None:
        return
    profile, schedule, rs_share, any_state_brake, _ = best
    stress_rows = []
    best_stress_key = None
    best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq, trades, metrics, targets = run_one(profile, schedule, rs_share, any_state_brake, entry_band, slippage_bps, min_liq_bil)
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
        "# Technical T2 Portfolio V5 Dual Sleeve Any-State Brake",
        "",
        "Status: research-only pure technical dual-sleeve test. Dashboard remains BLOCKED.",
        "",
        f"Best profile: {metrics['profile']}",
        f"Schedule: {metrics['schedule']}",
        f"RS-trend sleeve share: {metrics['rs_share']:.0%}",
        f"Any-state strategy-relative brake: {metrics['any_state_brake']} ({metrics['brake_events']} events)",
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
        search.head(20).to_markdown(index=False),
        "",
        "## Stress Grid",
        "",
        stress.to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "candidate_summary.md", "\n".join(lines))
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_H_PORTFOLIO_V5_DUAL_SLEEVE_ANY_BRAKE",
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
