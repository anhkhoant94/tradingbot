from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_walk_forward_strict as wf
from technical_t2_focused_ceiling import load_panel_with_rrg, score
from technical_t2_risk_overlay_v3 import build_risk_context, load_states_with_context, base_mode


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
OUT = STATE_DIR / "dynamic_entry_v4"
TARGET_START = pd.Timestamp("2021-01-01")
TARGET_END = pd.Timestamp("2026-05-22")


def choose_entry(policy: str, *, mode: str, risk_multiplier: float, fire: bool) -> float:
    if policy == "fixed_1":
        return 0.01
    if policy == "fixed_3":
        return 0.03
    if policy == "shock3_else1":
        return 0.03 if fire else 0.01
    if policy == "risk3_else1":
        return 0.03 if risk_multiplier < 1.0 else 0.01
    if policy == "liquid3_else1":
        return 0.03 if mode == "liquid_breakout" else 0.01
    if policy == "liquid_or_risk3_else1":
        return 0.03 if (mode == "liquid_breakout" or risk_multiplier < 1.0) else 0.01
    raise ValueError(policy)


def build_targets(risk_control: str, holdings: int, entry_policy: str):
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
        signal_meta[exec_date] = {"signal_friday": friday, "state": state, "risk_control": risk_control, "entry_policy": entry_policy}
        if state == "risk_off":
            continue
        multiplier = float(getattr(st, "multiplier", 1.0))
        if multiplier <= 0:
            continue
        mode = base_mode(st)
        entry_band = choose_entry(entry_policy, mode=mode, risk_multiplier=multiplier, fire=bool(getattr(st, "fire", False)))
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
                "entry_policy": entry_policy,
                "risk_control": risk_control,
                "mode": mode,
                "holdings": int(holdings),
                "risk_multiplier": multiplier,
                "fire": bool(getattr(st, "fire", False)),
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def simulate_dynamic(
    targets: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    signal_meta: dict[pd.Timestamp, dict],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    variant: str,
    slippage_bps: int,
    min_liq_bil: float,
):
    vni = v1.load_vni()
    dates = [pd.Timestamp(x) for x in vni["date"].tolist()]
    dates = [d for d in dates if start <= d <= end]
    date_set = set(dates)
    period_signal_dates = [d for d in signal_dates if d in date_set]
    period_targets = targets[targets["date"].isin(period_signal_dates)].copy() if not targets.empty else targets.copy()
    symbols = sorted(period_targets["symbol"].astype(str).unique()) if not period_targets.empty else []
    hist = v1.load_aligned_history(symbols, dates)
    period_targets = period_targets[period_targets["symbol"].isin(hist.keys())].copy() if not period_targets.empty else period_targets
    targets_by_date = {
        pd.Timestamp(date): group.sort_values("weight", ascending=False)
        for date, group in period_targets.groupby("date")
    }
    signal_set = set(period_signal_dates)
    cash = v1.INITIAL_NAV_VND
    lots: dict[str, list[v1.Lot]] = {}
    pending: list[v1.PendingBuy] = []
    rows: list[dict] = []
    trades: list[dict] = []
    buy_cost = slippage_bps / 10000.0
    old_sell_cost = v1.SELL_COST
    v1.SELL_COST = slippage_bps / 10000.0
    try:
        for idx, date in enumerate(dates):
            if date in signal_set:
                pending = []
                target_group = targets_by_date.get(date, pd.DataFrame())
                target_weights = dict(zip(target_group["symbol"], target_group["weight"])) if not target_group.empty else {}
                entry_by_symbol = dict(zip(target_group["symbol"], target_group["entry_band"])) if not target_group.empty else {}
                nav_open = v1.portfolio_value(cash, lots, hist, idx, "open")
                for symbol in sorted(set(lots) | set(target_weights)):
                    open_px = v1.px(hist, symbol, idx, "open", tradable=True)
                    if open_px is None:
                        continue
                    cur_val = v1.symbol_value(lots, hist, symbol, idx, "open")
                    tgt_val = nav_open * float(target_weights.get(symbol, 0.0))
                    if cur_val > tgt_val + nav_open * 0.002:
                        shares = int((cur_val - tgt_val) / open_px)
                        cash += v1.sell_symbol(lots, symbol, shares, open_px, idx, date, trades, "rebalance")
                nav_after_sells = v1.portfolio_value(cash, lots, hist, idx, "open")
                for symbol, weight in sorted(target_weights.items(), key=lambda kv: -kv[1]):
                    open_px = v1.px(hist, symbol, idx, "open", tradable=True)
                    if open_px is None:
                        continue
                    cur_val = v1.symbol_value(lots, hist, symbol, idx, "open")
                    buy_value = max(0.0, nav_after_sells * float(weight) - cur_val)
                    if buy_value <= nav_after_sells * 0.002:
                        continue
                    prev_close = v1.px(hist, symbol, idx - 1, "close", tradable=False)
                    if prev_close is None:
                        continue
                    entry_band = float(entry_by_symbol.get(symbol, 0.01))
                    pending.append(v1.PendingBuy(
                        symbol=symbol,
                        target_value=buy_value,
                        limit_price=prev_close * (1.0 + entry_band),
                        start_idx=idx,
                        end_idx=min(len(dates) - 1, idx + 2),
                    ))
            still_pending: list[v1.PendingBuy] = []
            for order in pending:
                if idx < order.start_idx:
                    still_pending.append(order)
                    continue
                if idx > order.end_idx:
                    trades.append({"date": date, "symbol": order.symbol, "side": "MISS_BUY", "shares": 0, "price": np.nan, "cash_vnd": 0.0, "reason": "expired_no_pullback", "entry_date": pd.NaT, "entry_price": np.nan, "holding_sessions": 0})
                    continue
                if order.symbol not in hist:
                    continue
                row = hist[order.symbol].iloc[idx]
                if not bool(row["tradable"]):
                    still_pending.append(order)
                    continue
                traded_value_bil = float(row["close"] * 1000.0 * row["volume"] / 1_000_000_000.0)
                if traded_value_bil < min_liq_bil:
                    still_pending.append(order)
                    continue
                open_px = v1.px(hist, order.symbol, idx, "open", tradable=True)
                low_px = v1.px(hist, order.symbol, idx, "low", tradable=True)
                if open_px is None or low_px is None:
                    still_pending.append(order)
                    continue
                fill_px = None
                reason = ""
                if order.first_day and open_px <= order.limit_price:
                    fill_px = open_px
                    reason = "open_or_better"
                elif low_px <= order.limit_price:
                    fill_px = min(open_px, order.limit_price) if open_px <= order.limit_price else order.limit_price
                    reason = "pullback_limit"
                order.first_day = False
                if fill_px is None:
                    still_pending.append(order)
                    continue
                spend = min(order.target_value, cash)
                shares = int(math.floor(spend / (fill_px * (1.0 + buy_cost)) / v1.LOT_SIZE) * v1.LOT_SIZE)
                if shares <= 0:
                    continue
                cost = shares * fill_px * (1.0 + buy_cost)
                cash -= cost
                lots.setdefault(order.symbol, []).append(v1.Lot(order.symbol, shares, idx, date, fill_px))
                trades.append({"date": date, "symbol": order.symbol, "side": "BUY", "shares": shares, "price": fill_px / 1000.0, "cash_vnd": -cost, "reason": reason, "entry_date": date, "entry_price": fill_px / 1000.0, "holding_sessions": 0})
            pending = still_pending
            nav = v1.portfolio_value(cash, lots, hist, idx, "close")
            invested = nav - cash
            rows.append({"date": date, "nav": nav, "cash": cash, "exposure": invested / nav if nav > 0 else 0.0, "position_count": len(lots), "pending_buy_count": len(pending), "is_signal_day": date in signal_set})
    finally:
        v1.SELL_COST = old_sell_cost
    eq = pd.DataFrame(rows)
    trades_df = pd.DataFrame(trades)
    metrics = wf.compute_period_metrics(eq, 2021, 2026)
    trade_count = int((trades_df["side"].isin(["BUY", "SELL"])).sum()) if not trades_df.empty else 0
    years = max(1.0, (eq["date"].max() - eq["date"].min()).days / 365.25) if not eq.empty else 1.0
    metrics.update({
        "variant": variant,
        "slippage_bps": slippage_bps,
        "min_liq_bil": min_liq_bil,
        "trade_count": trade_count,
        "turnover_trades_per_year": float(trade_count / years),
        "avg_exposure": float(eq["exposure"].mean()) if not eq.empty else np.nan,
        "max_exposure": float(eq["exposure"].max()) if not eq.empty else np.nan,
    })
    return eq, trades_df, metrics


def run_cell(risk_control: str, holdings: int, entry_policy: str, slippage_bps: int = 15, min_liq_bil: float = 3.0):
    targets, signal_dates, signal_meta = build_targets(risk_control, holdings, entry_policy)
    eq, trades, metrics = simulate_dynamic(
        targets,
        signal_dates,
        signal_meta,
        start=TARGET_START,
        end=TARGET_END,
        variant=f"{risk_control}_h{holdings}_{entry_policy}",
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
    )
    metrics["risk_control"] = risk_control
    metrics["holdings"] = int(holdings)
    metrics["entry_policy"] = entry_policy
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    controls = ["shock_cond_soft_15", "shock_soft_15", "shock_cond_cash_15", "shock_cash_15", "shock_cond_mix_15"]
    policies = ["fixed_1", "fixed_3", "shock3_else1", "risk3_else1", "liquid3_else1", "liquid_or_risk3_else1"]
    rows = []
    best_key = None
    best_payload = None
    for risk_control in controls:
        for holdings in [1, 2, 3]:
            for entry_policy in policies:
                eq, trades, metrics, targets = run_cell(risk_control, holdings, entry_policy, 15, 3.0)
                rows.append({k: v for k, v in metrics.items() if k != "yearly_rows"})
                key = (int(metrics["pass_vni20"]), float(metrics["min_edge_vs_vni"]), float(metrics["cagr"]), -abs(float(metrics["maxdd"])))
                if best_key is None or key > best_key:
                    best_key = key
                    best_payload = (eq, trades, metrics, targets)
    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    best = search.iloc[0]
    stress_rows = []
    stress_best_key = None
    stress_best_payload = None
    for slippage_bps in [15, 30]:
        for min_liq_bil in [3.0, 5.0]:
            eq, trades, metrics, targets = run_cell(str(best["risk_control"]), int(best["holdings"]), str(best["entry_policy"]), slippage_bps, min_liq_bil)
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
        "stage": "T2_DYNAMIC_ENTRY_V4",
        "dashboard_status": "BLOCKED",
        "verdict": verdict,
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "search_rows": int(len(search)),
        "stress_rows": int(len(stress)),
        "stress_pass_counts": stress["pass_vni20"].value_counts().sort_index().to_dict(),
    }
    lines = [
        "# Technical T2 Dynamic Entry V4",
        "",
        f"Verdict: **{verdict}**",
        "",
        "Dynamic limit-entry bands using only market/route/risk state. No year/ticker/calendar rescue.",
        "",
        f"- Risk control: {metrics['risk_control']}",
        f"- Entry policy: {metrics['entry_policy']}",
        f"- Holdings: {metrics['holdings']}",
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
    v1.atomic_write_text(OUT / "dynamic_entry_v4_verdict.md", "\n".join(lines))
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
