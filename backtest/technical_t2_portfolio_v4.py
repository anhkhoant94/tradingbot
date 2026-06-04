from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v2 as v2


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "portfolio_v4_picker_state_brake"


EXPOSURE_PROFILES = {
    "reordered": {
        "broad_trend": {"exposure": 0.75, "max_holdings": 5, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.90, "max_holdings": 6, "max_weight": 0.22},
        "recovery": {"exposure": 0.50, "max_holdings": 5, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "reordered_low": {
        "broad_trend": {"exposure": 0.65, "max_holdings": 5, "max_weight": 0.22},
        "narrow_leadership": {"exposure": 0.85, "max_holdings": 6, "max_weight": 0.22},
        "recovery": {"exposure": 0.40, "max_holdings": 4, "max_weight": 0.25},
        "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
    },
    "balanced_reference": v2.EXPOSURE_PROFILES["balanced"],
}


def state_params(profile: str, risk_mode: str) -> dict:
    params = {k: dict(v) for k, v in EXPOSURE_PROFILES[profile].items()}
    if risk_mode == "volume":
        params["risk_off"] = {"exposure": 0.20, "max_holdings": 3, "max_weight": 0.08}
    return params


def score_picker(week: pd.DataFrame, state: str, risk_mode: str, picker_mode: str) -> pd.Series:
    z = v1.robust_z
    if picker_mode == "composite":
        return v2.subblend_score(week, state, risk_mode)
    if picker_mode == "rs_only":
        return 0.65 * z(week["rs_13w"]) + 0.35 * z(week["rs_26w"])
    if picker_mode == "rs_trend":
        sma100_gap = (week["close"] / week["sma100"].replace(0, np.nan)) - 1.0
        return (
            0.45 * z(week["rs_13w"])
            + 0.25 * z(week["rs_26w"])
            + 0.15 * z(week["high52_proximity"])
            + 0.15 * z(sma100_gap)
        )
    raise ValueError(f"Unknown picker_mode={picker_mode}")


def load_states() -> pd.DataFrame:
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
    return states


def build_targets(
    *,
    profile: str,
    risk_mode: str,
    schedule: str,
    picker_mode: str,
    entry_band: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp], dict[pd.Timestamp, dict]]:
    states = load_states()
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
    params_by_state = state_params(profile, risk_mode)
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
        week["score"] = score_picker(week, state, risk_mode, picker_mode)
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
                "picker_mode": picker_mode,
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates)), signal_meta


def _trailing_nav_vs_vni_underperf(
    rows: list[dict],
    dates: list[pd.Timestamp],
    vni_close: dict[pd.Timestamp, float],
    idx: int,
    *,
    lookback_days: int = 65,
) -> float | None:
    if len(rows) <= lookback_days or idx <= lookback_days:
        return None
    nav_now = float(rows[-1]["nav"])
    nav_then = float(rows[-lookback_days]["nav"])
    date_now = dates[idx - 1]
    date_then = dates[idx - lookback_days]
    if nav_then <= 0 or date_now not in vni_close or date_then not in vni_close or vni_close[date_then] <= 0:
        return None
    strat_ret = nav_now / nav_then - 1.0
    vni_ret = vni_close[date_now] / vni_close[date_then] - 1.0
    return float(strat_ret - vni_ret)


def simulate_with_relative_brake(
    targets: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    signal_meta: dict[pd.Timestamp, dict],
    *,
    variant: str,
    entry_band: float,
    slippage_bps: int,
    min_liq_bil: float,
    relative_brake: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    vni = v1.load_vni()
    dates = [pd.Timestamp(x) for x in vni["date"].tolist()]
    dates = [d for d in dates if pd.Timestamp("2021-01-01") <= d <= max(signal_dates)]
    date_set = set(dates)
    signal_dates = [d for d in signal_dates if d in date_set]
    vni_close = {pd.Timestamp(r.date): float(r.close) for r in vni.itertuples(index=False)}
    symbols = sorted(targets["symbol"].astype(str).unique()) if not targets.empty else []
    hist = v1.load_aligned_history(symbols, dates)
    targets = targets[targets["symbol"].isin(hist.keys())].copy()
    targets_by_date = {
        pd.Timestamp(date): group.sort_values("weight", ascending=False)
        for date, group in targets.groupby("date")
    }
    signal_set = set(signal_dates)
    cash = v1.INITIAL_NAV_VND
    lots: dict[str, list[v1.Lot]] = {}
    pending: list[v1.PendingBuy] = []
    rows: list[dict] = []
    trades: list[dict] = []
    buy_cost = slippage_bps / 10000.0
    old_sell_cost = v1.SELL_COST
    v1.SELL_COST = slippage_bps / 10000.0
    brake_remaining = 0
    brake_events = 0
    try:
        for idx, date in enumerate(dates):
            if date in signal_set:
                pending = []
                meta = signal_meta.get(date, {})
                signal_state = str(meta.get("state", ""))
                target_group = targets_by_date.get(date, pd.DataFrame())
                target_weights = dict(zip(target_group["symbol"], target_group["weight"])) if not target_group.empty else {}

                if relative_brake and signal_state == "narrow_leadership":
                    underperf = _trailing_nav_vs_vni_underperf(rows, dates, vni_close, idx)
                    if underperf is not None and underperf <= -0.05:
                        brake_remaining = 4
                        brake_events += 1
                if relative_brake and brake_remaining > 0 and target_weights:
                    gross = sum(float(x) for x in target_weights.values())
                    if gross > 0.30:
                        scale = 0.30 / gross
                        target_weights = {k: float(v) * scale for k, v in target_weights.items()}
                    brake_remaining -= 1

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
                    trades.append({
                        "date": date,
                        "symbol": order.symbol,
                        "side": "MISS_BUY",
                        "shares": 0,
                        "price": np.nan,
                        "cash_vnd": 0.0,
                        "reason": "expired_no_pullback",
                        "entry_date": pd.NaT,
                        "entry_price": np.nan,
                        "holding_sessions": 0,
                    })
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
                trades.append({
                    "date": date,
                    "symbol": order.symbol,
                    "side": "BUY",
                    "shares": shares,
                    "price": fill_px / 1000.0,
                    "cash_vnd": -cost,
                    "reason": reason,
                    "entry_date": date,
                    "entry_price": fill_px / 1000.0,
                    "holding_sessions": 0,
                })
            pending = still_pending
            nav = v1.portfolio_value(cash, lots, hist, idx, "close")
            invested = nav - cash
            rows.append({
                "date": date,
                "nav": nav,
                "cash": cash,
                "exposure": invested / nav if nav > 0 else 0.0,
                "position_count": len(lots),
                "pending_buy_count": len(pending),
                "is_signal_day": date in signal_set,
                "brake_active": int(relative_brake and brake_remaining > 0),
            })
    finally:
        v1.SELL_COST = old_sell_cost

    eq = pd.DataFrame(rows)
    trades_df = pd.DataFrame(trades)
    metrics = v1.compute_metrics(eq)
    trade_count = int((trades_df["side"].isin(["BUY", "SELL"])).sum()) if not trades_df.empty else 0
    metrics.update({
        "variant": variant,
        "entry_band": entry_band,
        "slippage_bps": slippage_bps,
        "min_liq_bil": min_liq_bil,
        "trade_count": trade_count,
        "turnover_trades_per_year": float(trade_count / max(1.0, ((eq["date"].max() - eq["date"].min()).days / 365.25))) if not eq.empty else 0.0,
        "avg_exposure": float(eq["exposure"].mean()) if not eq.empty else np.nan,
        "max_exposure": float(eq["exposure"].max()) if not eq.empty else np.nan,
        "relative_brake": bool(relative_brake),
        "brake_events": int(brake_events),
    })
    return eq, trades_df, metrics


def run_one(profile: str, risk_mode: str, schedule: str, picker_mode: str, relative_brake: bool, entry_band: float, slippage_bps: int, min_liq_bil: float):
    targets, signal_dates, signal_meta = build_targets(
        profile=profile,
        risk_mode=risk_mode,
        schedule=schedule,
        picker_mode=picker_mode,
        entry_band=entry_band,
    )
    eq, trades, metrics = simulate_with_relative_brake(
        targets,
        signal_dates,
        signal_meta,
        variant=f"{profile}_{risk_mode}_{schedule}_{picker_mode}_brake{int(relative_brake)}",
        entry_band=entry_band,
        slippage_bps=slippage_bps,
        min_liq_bil=min_liq_bil,
        relative_brake=relative_brake,
    )
    metrics["profile"] = profile
    metrics["risk_mode"] = risk_mode
    metrics["schedule"] = schedule
    metrics["picker_mode"] = picker_mode
    return eq, trades, metrics, targets


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    best_key = None
    best = None
    for profile in ["reordered", "reordered_low", "balanced_reference"]:
        for schedule in ["weekly", "biweekly_state"]:
            for picker_mode in ["composite", "rs_only", "rs_trend"]:
                for relative_brake in [False, True]:
                    eq, trades, metrics, targets = run_one(profile, "cash", schedule, picker_mode, relative_brake, 0.01, 15, 3.0)
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
                        best = (profile, "cash", schedule, picker_mode, relative_brake, metrics)

    search = pd.DataFrame(rows).sort_values(["pass_vni20", "min_edge_vs_vni", "cagr"], ascending=[False, False, False])
    v1.atomic_write_frame(search, OUT / "search_results.csv")
    if best is None:
        return
    profile, risk_mode, schedule, picker_mode, relative_brake, _ = best

    stress_rows = []
    best_stress_key = None
    best_payload = None
    for entry_band in [0.0, 0.01, 0.03]:
        for slippage_bps in [15, 30]:
            for min_liq_bil in [3.0, 5.0]:
                eq, trades, metrics, targets = run_one(profile, risk_mode, schedule, picker_mode, relative_brake, entry_band, slippage_bps, min_liq_bil)
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
        "# Technical T2 Portfolio V4 Picker/State/Brake",
        "",
        "Status: research-only pure technical A/B. Dashboard remains BLOCKED.",
        "",
        f"Best profile: {metrics['profile']}",
        f"Schedule: {metrics['schedule']}",
        f"Picker mode: {metrics['picker_mode']}",
        f"Relative brake: {metrics['relative_brake']} ({metrics['brake_events']} events)",
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
        "stage": "T2_G_PORTFOLIO_V4_PICKER_STATE_BRAKE",
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
