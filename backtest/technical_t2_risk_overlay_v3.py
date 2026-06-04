from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v4 as v4
import technical_t2_walk_forward_strict as wf
from technical_t2_focused_ceiling import load_panel_with_rrg, score


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
OUT = STATE_DIR / "risk_overlay_v3"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")
RISK_CONTEXT_COLUMNS = [
    "date",
    "year",
    "risk_control",
    "fire",
    "active",
    "multiplier",
    "breadth_pctile_52w",
    "dispersion_pctile_52w",
    "pct_above_sma50",
    "pct_near_high52",
    "breadth50_delta_1w",
    "breadth50_delta_2w",
]


def rolling_percentile(s: pd.Series, window: int = 52) -> pd.Series:
    vals = pd.to_numeric(s, errors="coerce")

    def pct_rank(x: np.ndarray) -> float:
        cur = x[-1]
        x = x[np.isfinite(x)]
        if len(x) < 10 or not np.isfinite(cur):
            return np.nan
        return float((x <= cur).sum() / len(x))

    return vals.rolling(window, min_periods=10).apply(pct_rank, raw=True)


def load_states_with_context() -> pd.DataFrame:
    states = v4.load_states().copy()
    states["breadth_pctile_52w"] = rolling_percentile(states["pct_above_sma50"])
    states["dispersion_pctile_52w"] = rolling_percentile(states["rs13_dispersion"])
    return states


def base_mode(st: object) -> str:
    state = str(getattr(st, "effective_state", ""))
    breadth50 = float(getattr(st, "pct_above_sma50", np.nan))
    breadth200 = float(getattr(st, "pct_above_sma200", np.nan))
    high52 = float(getattr(st, "pct_near_high52", np.nan))
    low52 = float(getattr(st, "pct_near_low52", np.nan))
    vni13 = float(getattr(st, "vni_ret_13w", np.nan))
    vni26 = float(getattr(st, "vni_ret_26w", np.nan))
    weak_positive_narrow = (
        pd.notna(vni13)
        and vni13 > 0
        and pd.notna(breadth50)
        and breadth50 < 0.50
        and pd.notna(high52)
        and high52 < 0.12
    )
    not_hot = (
        (pd.notna(vni26) and vni26 < 0.25)
        or (pd.notna(breadth200) and breadth200 < 0.55)
        or (pd.notna(low52) and low52 > 0.10)
    )
    if not_hot:
        return "liquid_breakout" if weak_positive_narrow else "rs_trend"
    return "hybrid"


def _soft_multiplier(pctile: float) -> float:
    if not np.isfinite(pctile):
        return 1.0
    if pctile >= 0.60:
        return 1.0
    if pctile >= 0.30:
        return 0.75
    if pctile >= 0.10:
        return 0.50
    return 0.25


def shock_fire(st: object, threshold: float) -> bool:
    d1 = float(getattr(st, "breadth50_delta_1w", np.nan))
    d2 = float(getattr(st, "breadth50_delta_2w", np.nan))
    return (pd.notna(d1) and d1 <= -threshold) or (pd.notna(d2) and d2 <= -(2.0 * threshold))


def build_risk_context(states: pd.DataFrame, risk_control: str) -> pd.DataFrame:
    rows = []
    active = False
    min_hold = 0
    confirm_count = 0
    for st in states.itertuples(index=False):
        fire = False
        active_before = active
        multiplier = 1.0
        threshold = 0.10
        if risk_control.endswith("_15"):
            threshold = 0.15
        vni26 = float(getattr(st, "vni_ret_26w", np.nan))
        breadth200 = float(getattr(st, "pct_above_sma200", np.nan))
        low52 = float(getattr(st, "pct_near_low52", np.nan))
        not_hot_context = (
            (pd.notna(vni26) and vni26 < 0.20)
            or (pd.notna(breadth200) and breadth200 < 0.55)
            or (pd.notna(low52) and low52 > 0.10)
        )

        if risk_control in {"none"}:
            multiplier = 1.0
        elif risk_control == "breadth_soft":
            multiplier = _soft_multiplier(float(st.breadth_pctile_52w))
        elif risk_control == "dispersion_soft":
            multiplier = _soft_multiplier(float(st.dispersion_pctile_52w))
        elif risk_control == "breadth_dispersion_soft":
            multiplier = min(_soft_multiplier(float(st.breadth_pctile_52w)), _soft_multiplier(float(st.dispersion_pctile_52w)))
        elif risk_control in {"shock_cash_10", "shock_cash_15"}:
            fire = shock_fire(st, threshold)
            multiplier = 0.0 if fire else 1.0
        elif risk_control in {"shock_soft_10", "shock_soft_15"}:
            fire = shock_fire(st, threshold)
            multiplier = 0.25 if fire else 1.0
        elif risk_control in {"shock_reentry_cash_10", "shock_reentry_soft_10"}:
            fire = shock_fire(st, 0.10)
            confirm = (
                float(getattr(st, "pct_above_sma50", np.nan)) >= 0.35
                and float(getattr(st, "pct_near_high52", np.nan)) >= 0.05
                and float(getattr(st, "breadth50_delta_1w", np.nan)) >= 0.0
            )
            if fire:
                active = True
                min_hold = 2
                confirm_count = 0
            if active:
                multiplier = 0.0 if risk_control == "shock_reentry_cash_10" else 0.25
                if min_hold > 0:
                    min_hold -= 1
                elif confirm:
                    confirm_count += 1
                    if confirm_count >= 2:
                        active = False
                else:
                    confirm_count = 0
            else:
                multiplier = 1.0
        else:
            if risk_control in {"shock_cond_cash_15", "shock_cond_soft_15", "shock_cond_mix_15"}:
                fire = shock_fire(st, 0.15)
                if fire and not_hot_context:
                    multiplier = 0.0 if risk_control in {"shock_cond_cash_15", "shock_cond_mix_15"} else 0.25
                elif fire:
                    multiplier = 1.0 if risk_control == "shock_cond_cash_15" else 0.50
                else:
                    multiplier = 1.0
            elif risk_control in {"shock_cond_cash_10", "shock_cond_mix_10"}:
                fire = shock_fire(st, 0.10)
                if fire and not_hot_context:
                    multiplier = 0.0
                elif fire:
                    multiplier = 1.0 if risk_control == "shock_cond_cash_10" else 0.50
                else:
                    multiplier = 1.0
            else:
                raise ValueError(risk_control)

        rows.append({
            "date": pd.Timestamp(st.date),
            "year": int(pd.Timestamp(st.date).year),
            "risk_control": risk_control,
            "fire": bool(fire),
            "active": bool(active_before or fire or (active and multiplier < 1.0)),
            "multiplier": float(multiplier),
            "breadth_pctile_52w": float(getattr(st, "breadth_pctile_52w", np.nan)),
            "dispersion_pctile_52w": float(getattr(st, "dispersion_pctile_52w", np.nan)),
            "pct_above_sma50": float(getattr(st, "pct_above_sma50", np.nan)),
            "pct_near_high52": float(getattr(st, "pct_near_high52", np.nan)),
            "breadth50_delta_1w": float(getattr(st, "breadth50_delta_1w", np.nan)),
            "breadth50_delta_2w": float(getattr(st, "breadth50_delta_2w", np.nan)),
        })
    return pd.DataFrame(rows, columns=RISK_CONTEXT_COLUMNS)


def build_targets(risk_control: str, holdings: int, entry_band: float):
    states = load_states_with_context()
    risk_ctx = build_risk_context(states, risk_control)
    states = states.merge(risk_ctx[["date", "fire", "active", "multiplier"]], on="date", how="left")
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
    for st in states.itertuples(index=False):
        friday = pd.Timestamp(st.date)
        exec_date = v1.next_trading_day(vni_dates, friday)
        if exec_date is None or exec_date < TARGET_START:
            continue
        state = str(st.effective_state)
        signal_dates.append(exec_date)
        signal_meta[exec_date] = {"signal_friday": friday, "state": state, "risk_control": risk_control}
        if state == "risk_off":
            continue
        multiplier = float(getattr(st, "multiplier", 1.0))
        if multiplier <= 0:
            continue
        mode = base_mode(st)
        exposure = (1.0 if state in {"broad_trend", "narrow_leadership"} else 0.85) * multiplier
        max_weight = 1.0 if holdings == 1 else min(0.50, 1.0 / holdings)
        week = panel[panel["date"].eq(friday)].copy()
        week["score"] = score(week, state, mode)
        week = week[
            (week["avg_value_20d_bil"] >= 5.0)
            & (week["rs_13w"] > -0.15)
            & (week["close"] >= week["sma100"] * 0.90)
        ].copy()
        if mode == "liquid_breakout":
            week = week[(week["avg_value_20d_bil"] >= 10.0) & (week["high52_proximity"] >= 0.85)].copy()
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
                "risk_control": risk_control,
                "mode": mode,
                "holdings": int(holdings),
                "risk_multiplier": multiplier,
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta, risk_ctx


def run_cell(risk_control: str, holdings: int, entry_band: float, slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta, risk_ctx = build_targets(risk_control, holdings, entry_band)
    eq, trades, metrics = wf.simulate_period(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=f"{risk_control}_h{holdings}_entry{entry_band}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        any_state_brake=False,
        start_year=2021,
        end_year=2026,
    )
    metrics["risk_control"] = risk_control
    metrics["holdings"] = int(holdings)
    metrics["entry_band"] = float(entry_band)
    metrics["slippage_bps"] = int(slippage_bps)
    metrics["min_liq_bil"] = float(min_liq_bil)
    return eq, trades, metrics, targets, risk_ctx


def write_fire_audit(states: pd.DataFrame, controls: list[str]) -> None:
    frames = []
    summaries = []
    for control in controls:
        ctx = build_risk_context(states, control)
        frames.append(ctx)
        total = len(ctx)
        fire_freq = float(ctx["fire"].mean()) if total else np.nan
        active_freq = float((ctx["multiplier"] < 1.0).mean()) if total else np.nan
        summaries.append({
            "risk_control": control,
            "weeks": total,
            "fire_weeks": int(ctx["fire"].sum()),
            "active_weeks": int((ctx["multiplier"] < 1.0).sum()),
            "fire_frequency_pct": fire_freq * 100.0,
            "active_frequency_pct": active_freq * 100.0,
            "fire_years": int(ctx.loc[ctx["fire"], "year"].nunique()),
            "active_years": int(ctx.loc[ctx["multiplier"] < 1.0, "year"].nunique()),
        })
    all_ctx = pd.concat(frames, ignore_index=True)
    by_year = all_ctx.groupby(["risk_control", "year"], as_index=False).agg(
        weeks=("date", "count"),
        fire_weeks=("fire", "sum"),
        active_weeks=("multiplier", lambda x: int((x < 1.0).sum())),
        avg_multiplier=("multiplier", "mean"),
    )
    v1.atomic_write_frame(all_ctx, OUT / "risk_context_by_week.csv")
    v1.atomic_write_frame(by_year, OUT / "brake_fire_by_year.csv")
    v1.atomic_write_frame(pd.DataFrame(summaries), OUT / "brake_fire_summary.csv")


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    controls = [
        "none",
        "breadth_soft",
        "dispersion_soft",
        "breadth_dispersion_soft",
        "shock_cash_10",
        "shock_cash_15",
        "shock_soft_10",
        "shock_soft_15",
        "shock_reentry_cash_10",
        "shock_reentry_soft_10",
        "shock_cond_cash_15",
        "shock_cond_soft_15",
        "shock_cond_mix_15",
        "shock_cond_cash_10",
        "shock_cond_mix_10",
    ]
    states = load_states_with_context()
    write_fire_audit(states, controls)

    rows = []
    best_key = None
    best_payload = None
    for risk_control in controls:
        for holdings in [1, 2, 3]:
            for entry_band in [0.01, 0.03]:
                eq, trades, metrics, targets, risk_ctx = run_cell(risk_control, holdings, entry_band, 15, 3.0)
                rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
                key = (
                    int(metrics["pass_vni20"]),
                    float(metrics["min_edge_vs_vni"]),
                    float(metrics["cagr"]),
                    -abs(float(metrics["maxdd"])),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_payload = (eq, trades, metrics, targets, risk_ctx)
    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")

    eq, trades, metrics, targets, risk_ctx = best_payload
    stress_rows = []
    stress_best_key = None
    stress_best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq_s, trades_s, metrics_s, targets_s, risk_ctx_s = run_cell(
                    str(metrics["risk_control"]),
                    int(metrics["holdings"]),
                    entry_band,
                    slippage_bps,
                    min_liq_bil,
                )
                stress_rows.append({k: v for k, v in metrics_s.items() if k != "yearly_rows"})
                key = (
                    int(metrics_s["pass_vni20"]),
                    float(metrics_s["min_edge_vs_vni"]),
                    float(metrics_s["cagr"]),
                    -abs(float(metrics_s["maxdd"])),
                )
                if stress_best_key is None or key > stress_best_key:
                    stress_best_key = key
                    stress_best_payload = (eq_s, trades_s, metrics_s, targets_s, risk_ctx_s)
    stress = pd.DataFrame(stress_rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(stress, OUT / "stress_grid_results.csv")

    eq, trades, metrics, targets, risk_ctx = stress_best_payload
    yearly = pd.DataFrame(metrics["yearly_rows"])
    v1.atomic_write_frame(eq, OUT / "daily_lot_equity.parquet")
    v1.atomic_write_frame(trades, OUT / "daily_lot_trades.parquet")
    v1.atomic_write_frame(targets, OUT / "weekly_targets.parquet")
    v1.atomic_write_frame(yearly, OUT / "yearly_metrics.csv")

    verdict = "CANDIDATE_NEEDS_AUDIT" if int(metrics["pass_vni20"]) >= 6 else "RESEARCH_ONLY"
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_RISK_OVERLAY_V3",
        "dashboard_status": "BLOCKED",
        "verdict": verdict,
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
        "notes": "Pure technical risk overlays. Search includes soft breadth/dispersion exposure, symmetric shock thresholds, and re-entry variants. Still research-only until no-overfit and concentration guards pass.",
    }
    lines = [
        "# Technical T2 Risk Overlay V3",
        "",
        f"Verdict: **{verdict}**",
        "",
        "Generic risk overlays on top of the not-hot technical router. No year/ticker/calendar rescue.",
        "",
        "## Best Stress Result",
        "",
        f"- Risk control: {metrics['risk_control']}",
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
        "## Top Search Rows",
        "",
        search.head(30).to_markdown(index=False),
        "",
        "## Stress",
        "",
        stress.to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "risk_overlay_v3_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
