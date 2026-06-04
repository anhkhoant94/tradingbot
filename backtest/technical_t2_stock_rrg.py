from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v2 as v2
import technical_t2_portfolio_v4 as v4
import technical_t2_portfolio_v5 as v5
import technical_t2_walk_forward_strict as wf


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "stock_rrg"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")


def load_panel_with_rrg() -> pd.DataFrame:
    panel = pd.read_parquet(TECH_PANEL).copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["symbol"] = panel["symbol"].astype(str).str.upper()
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    grp = panel.groupby("symbol", group_keys=False)
    panel["rs13_mom_4w"] = grp["rs_13w"].diff(4)
    panel["rs13_mom_8w"] = grp["rs_13w"].diff(8)
    panel["rs26_mom_4w"] = grp["rs_26w"].diff(4)
    panel["price_mom_4w"] = grp["close"].pct_change(4)
    panel["rrg_leading"] = (panel["rs_13w"] > 0) & (panel["rs13_mom_4w"] > 0)
    panel["rrg_improving"] = (panel["rs_13w"] <= 0) & (panel["rs13_mom_4w"] > 0)
    return panel


def rrg_score(week: pd.DataFrame, mode: str) -> pd.Series:
    z = v1.robust_z
    if mode == "leading":
        return (
            0.35 * z(week["rs_13w"])
            + 0.30 * z(week["rs13_mom_4w"])
            + 0.15 * z(week["rs26_mom_4w"])
            + 0.10 * z(week["high52_proximity"])
            + 0.10 * z(week["volume_expansion_20_60"])
        )
    if mode == "improving":
        return (
            0.40 * z(week["rs13_mom_4w"])
            + 0.20 * z(week["rs13_mom_8w"])
            + 0.15 * z(week["price_mom_4w"])
            + 0.15 * z(week["pullback_quality"])
            + 0.10 * z(week["avg_value_20d_bil"])
        )
    if mode == "hybrid":
        return 0.55 * rrg_score(week, "leading") + 0.45 * rrg_score(week, "improving")
    raise ValueError(mode)


def build_targets(
    *,
    profile: str,
    schedule: str,
    rrg_mode: str,
    entry_band: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp], dict[pd.Timestamp, dict]]:
    states = v4.load_states()
    panel = load_panel_with_rrg()
    panel = panel[
        (panel["date"] <= states["date"].max())
        & (panel["avg_value_20d_bil"] >= 3.0)
        & (panel["close"] >= 5.0)
    ].copy()
    vni_dates = [pd.Timestamp(x) for x in v1.load_vni()["date"].tolist()]
    params_by_state = {k: dict(v) for k, v in v5.PROFILES[profile].items()}
    rows: list[dict] = []
    signal_dates: list[pd.Timestamp] = []
    signal_meta: dict[pd.Timestamp, dict] = {}
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

        # RRG is most useful in rotations; keep a composite fallback in broad trend.
        if state == "broad_trend" and rrg_mode != "hybrid":
            week["score"] = 0.50 * rrg_score(week, rrg_mode) + 0.50 * v2.subblend_score(week, state, "cash")
        else:
            week["score"] = rrg_score(week, rrg_mode)
        if rrg_mode == "leading":
            week = week[(week["rs_13w"] > -0.03) & (week["rs13_mom_4w"] > -0.02)].copy()
        elif rrg_mode == "improving":
            week = week[(week["rs13_mom_4w"] > 0) & (week["close"] >= week["sma100"] * 0.95)].copy()
        else:
            week = week[(week["rs13_mom_4w"] > -0.03) & (week["close"] >= week["sma100"] * 0.95)].copy()
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
                "rrg_mode": rrg_mode,
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
                "rs13_mom_4w": float(row.rs13_mom_4w),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def run_cell(profile: str, schedule: str, rrg_mode: str, entry_band: float, slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta = build_targets(profile=profile, schedule=schedule, rrg_mode=rrg_mode, entry_band=entry_band)
    eq, trades, metrics = wf.simulate_period(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=f"{profile}_{schedule}_{rrg_mode}_entry{entry_band}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        any_state_brake=False,
        start_year=2021,
        end_year=2026,
    )
    metrics["profile"] = profile
    metrics["schedule"] = schedule
    metrics["rrg_mode"] = rrg_mode
    metrics["entry_band"] = float(entry_band)
    metrics["slippage_bps"] = int(slippage_bps)
    metrics["min_liq_bil"] = float(min_liq_bil)
    return eq, trades, metrics, targets


def build_loyo(search: pd.DataFrame) -> pd.DataFrame:
    rows = []
    years = [2021, 2022, 2023, 2024, 2025, 2026]
    for held_out in years:
        ranked = search.copy()
        def score(row):
            edges = [float(row[f"edge_y{year}"]) for year in years if year != held_out and pd.notna(row[f"edge_y{year}"])]
            return pd.Series({
                "train_pass20_exheld": sum(e >= 20 for e in edges),
                "train_min_edge_exheld": min(edges),
                "train_mean_edge_exheld": sum(edges) / len(edges),
            })
        ranked = pd.concat([ranked, ranked.apply(score, axis=1)], axis=1)
        ranked = ranked.sort_values(["train_pass20_exheld", "train_min_edge_exheld", "train_mean_edge_exheld", "cagr"], ascending=[False, False, False, False])
        best = ranked.iloc[0]
        held_edge = float(best[f"edge_y{held_out}"])
        rows.append({
            "held_out_year": held_out,
            "selected_profile": best["profile"],
            "selected_schedule": best["schedule"],
            "selected_rrg_mode": best["rrg_mode"],
            "selected_entry_band": float(best["entry_band"]),
            "train_pass20_exheld": int(best["train_pass20_exheld"]),
            "train_min_edge_exheld": float(best["train_min_edge_exheld"]),
            "held_out_edge": held_edge,
            "held_out_pass20": bool(held_edge >= 20),
            "held_out_pass0": bool(held_edge >= 0),
        })
    return pd.DataFrame(rows)


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for profile in ["balanced_alt", "recovery_cautious", "narrow_boost"]:
        for schedule in ["weekly", "biweekly_state"]:
            for rrg_mode in ["leading", "improving", "hybrid"]:
                for entry_band in [0.0, 0.01, 0.03]:
                    eq, trades, metrics, targets = run_cell(profile, schedule, rrg_mode, entry_band, 15, 3.0)
                    rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    best = search.iloc[0]
    loyo = build_loyo(search)
    v1.atomic_write_frame(loyo, OUT / "loyo_results.csv")
    plateau = search[
        (search["profile"].eq(best["profile"]))
        & (search["schedule"].eq(best["schedule"]))
        & (search["rrg_mode"].eq(best["rrg_mode"]))
    ].sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(plateau, OUT / "plateau_neighbors.csv")

    stress_rows = []
    best_stress_key = None
    best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq, trades, metrics, targets = run_cell(str(best["profile"]), str(best["schedule"]), str(best["rrg_mode"]), entry_band, slippage_bps, min_liq_bil)
                stress_rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
                key = (int(metrics["pass_vni20"]), float(metrics["min_edge_vs_vni"]), float(metrics["cagr"]), -abs(float(metrics["maxdd"])))
                if best_stress_key is None or key > best_stress_key:
                    best_stress_key = key
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
        "stage": "T2_STOCK_RRG",
        "dashboard_status": "BLOCKED",
        "verdict": "CANDIDATE_NEEDS_AUDIT" if int(metrics["pass_vni20"]) >= 6 else "RESEARCH_ONLY",
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "loyo_pass20": int(loyo["held_out_pass20"].sum()),
        "loyo_nonnegative": int(loyo["held_out_pass0"].sum()),
        "plateau_pass_counts": plateau["pass_vni20"].value_counts().sort_index().to_dict(),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
    }
    lines = [
        "# Technical T2 Stock-Level RRG",
        "",
        f"Verdict: **{status['verdict']}**",
        "",
        "Pure technical stock-level RRG using relative-strength momentum vs VNI. No sectors/tickers/calendar rescue.",
        "",
        "## Best / Stress Result",
        "",
        f"- Profile: {metrics['profile']}",
        f"- Schedule: {metrics['schedule']}",
        f"- RRG mode: {metrics['rrg_mode']}",
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
        "## LOYO",
        "",
        loyo.to_markdown(index=False),
        "",
        "## Stress",
        "",
        stress.to_markdown(index=False),
        "",
        "## Search Top Rows",
        "",
        search.head(20).to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "stock_rrg_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
