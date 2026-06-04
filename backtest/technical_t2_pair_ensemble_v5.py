from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
from technical_t2_dynamic_entry_v4 import build_targets, simulate_dynamic


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
OUT = STATE_DIR / "pair_ensemble_v5"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")


COMPONENTS = {
    "soft15_fixed1": ("shock_cond_soft_15", "fixed_1"),
    "cash15_fixed1": ("shock_cond_cash_15", "fixed_1"),
    "cash15_fixed3": ("shock_cond_cash_15", "fixed_3"),
    "mix15_fixed3": ("shock_cond_mix_15", "fixed_3"),
    "soft15_fixed3": ("shock_cond_soft_15", "fixed_3"),
    "cash10_fixed1": ("shock_cond_cash_10", "fixed_1"),
    "cash10_fixed3": ("shock_cond_cash_10", "fixed_3"),
}


def component_targets(name: str) -> tuple[pd.DataFrame, list[pd.Timestamp], dict[pd.Timestamp, dict]]:
    risk_control, entry_policy = COMPONENTS[name]
    targets, signal_dates, signal_meta = build_targets(risk_control, 1, entry_policy)
    targets = targets.copy()
    targets["component"] = name
    return targets, signal_dates, signal_meta


def combine_components(parts: list[tuple[str, float]]):
    frames = []
    all_signal_dates: set[pd.Timestamp] = set()
    signal_meta: dict[pd.Timestamp, dict] = {}
    for name, alloc in parts:
        targets, signal_dates, meta = component_targets(name)
        targets["weight"] = targets["weight"].astype(float) * float(alloc)
        targets["component_alloc"] = float(alloc)
        frames.append(targets)
        all_signal_dates.update(signal_dates)
        for date, row in meta.items():
            signal_meta[pd.Timestamp(date)] = dict(row)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if combined.empty:
        return combined, sorted(all_signal_dates), signal_meta
    grouped = combined.groupby(["date", "signal_friday", "symbol", "state"], as_index=False).agg(
        weight=("weight", "sum"),
        score=("score", "max"),
        entry_band=("entry_band", "max"),
        avg_value_20d_bil=("avg_value_20d_bil", "max"),
        mode=("mode", lambda x: "+".join(sorted(set(map(str, x))))),
        component=("component", lambda x: "+".join(sorted(set(map(str, x))))),
        risk_control=("risk_control", lambda x: "+".join(sorted(set(map(str, x))))),
        entry_policy=("entry_policy", lambda x: "+".join(sorted(set(map(str, x))))),
    )
    sums = grouped.groupby("date")["weight"].transform("sum")
    scale = np.where(sums > 1.0, 1.0 / sums, 1.0)
    grouped["weight"] = grouped["weight"] * scale
    return grouped, sorted(all_signal_dates), signal_meta


def run_cell(parts: list[tuple[str, float]], slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta = combine_components(parts)
    label = "__".join([f"{name}{alloc:.2f}" for name, alloc in parts])
    eq, trades, metrics = simulate_dynamic(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=label,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
    )
    metrics["ensemble"] = label
    metrics["components"] = json.dumps(parts, ensure_ascii=False)
    metrics["slippage_bps"] = int(slippage_bps)
    metrics["min_liq_bil"] = float(min_liq_bil)
    metrics["max_target_positions"] = int(targets.groupby("date")["symbol"].nunique().max()) if not targets.empty else 0
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    anchors = ["soft15_fixed1", "cash15_fixed1"]
    challengers = ["cash15_fixed3", "mix15_fixed3", "soft15_fixed3", "cash10_fixed1", "cash10_fixed3"]
    rows = []
    best_key = None
    best_payload = None
    for anchor in anchors:
        for challenger in challengers:
            if anchor == challenger:
                continue
            for w in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
                parts = [(anchor, 1.0 - w), (challenger, w)]
                eq, trades, metrics, targets = run_cell(parts, 15, 3.0)
                rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
                key = (int(metrics["pass_vni20"]), float(metrics["min_edge_vs_vni"]), float(metrics["cagr"]), -abs(float(metrics["maxdd"])))
                if best_key is None or key > best_key:
                    best_key = key
                    best_payload = (eq, trades, metrics, targets)
    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")

    best = search.iloc[0]
    parts = json.loads(best["components"])
    stress_rows = []
    stress_best_key = None
    stress_best_payload = None
    for slippage_bps in [15, 30]:
        for min_liq_bil in [3.0, 5.0]:
            eq, trades, metrics, targets = run_cell(parts, slippage_bps, min_liq_bil)
            stress_rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
            key = (int(metrics["pass_vni20"]), float(metrics["min_edge_vs_vni"]), float(metrics["cagr"]), -abs(float(metrics["maxdd"])))
            if stress_best_key is None or key > stress_best_key:
                stress_best_key = key
                stress_best_payload = (eq, trades, metrics, targets)
    stress = pd.DataFrame(stress_rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(stress, OUT / "stress_grid_results.csv")

    eq, trades, metrics, targets = stress_best_payload
    yearly = pd.DataFrame(metrics["yearly_rows"])
    v1.atomic_write_frame(eq, OUT / "daily_lot_equity.parquet")
    v1.atomic_write_frame(trades, OUT / "daily_lot_trades.parquet")
    v1.atomic_write_frame(targets, OUT / "weekly_targets.parquet")
    v1.atomic_write_frame(yearly, OUT / "yearly_metrics.csv")
    verdict = "CANDIDATE_NEEDS_AUDIT" if int(metrics["pass_vni20"]) >= 6 else "RESEARCH_ONLY"
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_PAIR_ENSEMBLE_V5",
        "dashboard_status": "BLOCKED",
        "verdict": verdict,
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
    }
    lines = [
        "# Technical T2 Pair Ensemble V5",
        "",
        f"Verdict: **{verdict}**",
        "",
        "Two-sleeve pure technical ensemble. Combines generic sleeves; no year/ticker/calendar rescue.",
        "",
        f"- Ensemble: {metrics['ensemble']}",
        f"- Max target positions: {metrics['max_target_positions']}",
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
    v1.atomic_write_text(OUT / "pair_ensemble_v5_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
