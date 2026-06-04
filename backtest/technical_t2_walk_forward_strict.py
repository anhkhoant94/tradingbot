from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import technical_t2_portfolio as v1
import technical_t2_portfolio_v2 as v2
import technical_t2_portfolio_v4 as v4
import technical_t2_portfolio_v5 as v5


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "walk_forward_strict"

TRAIN_START = pd.Timestamp("2016-01-01")
TRAIN_END = pd.Timestamp("2020-12-31")
OOS_START = pd.Timestamp("2021-01-01")
OOS_END = pd.Timestamp("2026-05-22")


def _score(week: pd.DataFrame, state: str, mode: str) -> pd.Series:
    if mode == "composite":
        return v2.subblend_score(week, state, "cash")
    if mode == "rs_trend":
        return v4.score_picker(week, state, "cash", "rs_trend")
    raise ValueError(mode)


def build_targets_all(
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
        for sleeve_name, sleeve_n, sleeve_exposure in [
            ("rs_trend", rs_n, exposure * rs_share),
            ("composite", comp_n, exposure * (1.0 - rs_share)),
        ]:
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


def compute_period_metrics(eq: pd.DataFrame, start_year: int, end_year: int) -> dict:
    vni = v1.load_vni()
    eq = eq.copy()
    if eq.empty:
        return {"cagr": np.nan, "maxdd": np.nan, "sharpe": np.nan, "pass_vni20": 0, "pass_vni30": 0, "min_edge_vs_vni": np.nan, "yearly_rows": []}
    eq["date"] = pd.to_datetime(eq["date"])
    nav = eq["nav"].astype(float)
    ret = nav.pct_change().fillna(0.0)
    years = (eq["date"].iloc[-1] - eq["date"].iloc[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 and nav.iloc[0] > 0 else np.nan
    maxdd = (nav / nav.cummax() - 1.0).min()
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0.0
    metrics = {"cagr": cagr * 100.0, "maxdd": maxdd * 100.0, "sharpe": float(sharpe)}
    pass_vni20 = 0
    pass_vni30 = 0
    edges: list[float] = []
    yearly_rows = []
    for year in range(start_year, end_year + 1):
        group = eq[eq["date"].dt.year == year]
        if group.empty:
            continue
        prev = eq[eq["date"] < pd.Timestamp(f"{year}-01-01")]
        base_nav = float(prev["nav"].iloc[-1]) if not prev.empty else float(group["nav"].iloc[0])
        strategy = (float(group["nav"].iloc[-1]) / base_nav - 1.0) * 100.0 if base_nav > 0 else np.nan
        vni_group = vni[(vni["date"].dt.year == year) & (vni["date"] <= group["date"].iloc[-1])]
        prev_vni = vni[vni["date"] < pd.Timestamp(f"{year}-01-01")]
        if vni_group.empty:
            vni_y = np.nan
        else:
            base_close = float(prev_vni["close"].iloc[-1]) if not prev_vni.empty else float(vni_group["close"].iloc[0])
            vni_y = (float(vni_group["close"].iloc[-1]) / base_close - 1.0) * 100.0 if base_close > 0 else np.nan
        edge = strategy - vni_y if pd.notna(strategy) and pd.notna(vni_y) else np.nan
        if pd.notna(edge):
            edges.append(float(edge))
            pass_vni20 += int(edge >= 20.0)
            pass_vni30 += int(edge >= 30.0)
        metrics[f"y{year}"] = strategy
        metrics[f"vni_y{year}"] = vni_y
        metrics[f"edge_y{year}"] = edge
        yearly_rows.append({"year": year, "strategy_return_pct": strategy, "vni_return_pct": vni_y, "edge_vs_vni_pp": edge})
    metrics["pass_vni20"] = pass_vni20
    metrics["pass_vni30"] = pass_vni30
    metrics["min_edge_vs_vni"] = min(edges) if edges else np.nan
    metrics["yearly_rows"] = yearly_rows
    return metrics


def _trailing_underperf(rows: list[dict], dates: list[pd.Timestamp], vni_close: dict[pd.Timestamp, float], idx: int, lookback_days: int = 65) -> float | None:
    if len(rows) <= lookback_days or idx <= lookback_days:
        return None
    nav_now = float(rows[-1]["nav"])
    nav_then = float(rows[-lookback_days]["nav"])
    date_now = dates[idx - 1]
    date_then = dates[idx - lookback_days]
    if nav_then <= 0 or date_now not in vni_close or date_then not in vni_close or vni_close[date_then] <= 0:
        return None
    return float((nav_now / nav_then - 1.0) - (vni_close[date_now] / vni_close[date_then] - 1.0))


def simulate_period(
    targets: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    signal_meta: dict[pd.Timestamp, dict],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    variant: str,
    entry_band: float,
    slippage_bps: int,
    min_liq_bil: float,
    any_state_brake: bool,
    start_year: int,
    end_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    vni = v1.load_vni()
    dates = [pd.Timestamp(x) for x in vni["date"].tolist()]
    dates = [d for d in dates if start <= d <= end]
    if not dates:
        raise ValueError("No VNI dates in requested period")
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
    vni_close = {pd.Timestamp(r.date): float(r.close) for r in vni.itertuples(index=False)}

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
                target_group = targets_by_date.get(date, pd.DataFrame())
                target_weights = dict(zip(target_group["symbol"], target_group["weight"])) if not target_group.empty else {}
                if any_state_brake:
                    underperf = _trailing_underperf(rows, dates, vni_close, idx)
                    if underperf is not None and underperf <= -0.05:
                        brake_remaining = 4
                        brake_events += 1
                if any_state_brake and brake_remaining > 0 and target_weights:
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
                "brake_active": int(any_state_brake and brake_remaining > 0),
            })
    finally:
        v1.SELL_COST = old_sell_cost

    eq = pd.DataFrame(rows)
    trades_df = pd.DataFrame(trades)
    metrics = compute_period_metrics(eq, start_year, end_year)
    trade_count = int((trades_df["side"].isin(["BUY", "SELL"])).sum()) if not trades_df.empty else 0
    years = max(1.0, (eq["date"].max() - eq["date"].min()).days / 365.25) if not eq.empty else 1.0
    metrics.update({
        "variant": variant,
        "entry_band": entry_band,
        "slippage_bps": slippage_bps,
        "min_liq_bil": min_liq_bil,
        "trade_count": trade_count,
        "turnover_trades_per_year": float(trade_count / years),
        "avg_exposure": float(eq["exposure"].mean()) if not eq.empty else np.nan,
        "max_exposure": float(eq["exposure"].max()) if not eq.empty else np.nan,
        "brake_events": int(brake_events),
    })
    return eq, trades_df, metrics


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    train_rows = []
    payloads = {}
    for profile in ["balanced_alt", "narrow_boost", "recovery_cautious"]:
        for schedule in ["weekly", "biweekly_state"]:
            for rs_share in [0.40, 0.50, 0.60]:
                for any_state_brake in [False, True]:
                    params = {
                        "profile": profile,
                        "schedule": schedule,
                        "rs_share": rs_share,
                        "any_state_brake": any_state_brake,
                        "entry_band": 0.01,
                        "slippage_bps": 15,
                        "min_liq_bil": 3.0,
                    }
                    targets, signal_dates, signal_meta = build_targets_all(
                        profile=profile,
                        schedule=schedule,
                        rs_share=rs_share,
                        entry_band=0.01,
                    )
                    eq, trades, metrics = simulate_period(
                        targets,
                        signal_dates,
                        signal_meta,
                        start=TRAIN_START,
                        end=TRAIN_END,
                        variant=f"{profile}_{schedule}_rs{rs_share}_brake{int(any_state_brake)}",
                        entry_band=0.01,
                        slippage_bps=15,
                        min_liq_bil=3.0,
                        any_state_brake=any_state_brake,
                        start_year=2016,
                        end_year=2020,
                    )
                    row = {k: v for k, v in metrics.items() if k != "yearly_rows"}
                    row.update(params)
                    train_rows.append(row)
                    payloads[(profile, schedule, rs_share, any_state_brake)] = (targets, signal_dates, signal_meta, params, metrics)

    train = pd.DataFrame(train_rows).sort_values(["cagr", "min_edge_vs_vni", "maxdd"], ascending=[False, False, False])
    v1.atomic_write_frame(train, OUT / "train_search_results.csv")
    best_row = train.iloc[0].to_dict()
    key = (best_row["profile"], best_row["schedule"], float(best_row["rs_share"]), bool(best_row["any_state_brake"]))
    targets, signal_dates, signal_meta, frozen_params, train_metrics = payloads[key]
    v1.atomic_write_text(OUT / "frozen_params.json", json.dumps(frozen_params, ensure_ascii=False, indent=2, default=str))

    eq, trades, oos_metrics = simulate_period(
        targets,
        signal_dates,
        signal_meta,
        start=OOS_START,
        end=OOS_END,
        variant=f"{frozen_params['profile']}_{frozen_params['schedule']}_rs{frozen_params['rs_share']}_brake{int(frozen_params['any_state_brake'])}",
        entry_band=float(frozen_params["entry_band"]),
        slippage_bps=int(frozen_params["slippage_bps"]),
        min_liq_bil=float(frozen_params["min_liq_bil"]),
        any_state_brake=bool(frozen_params["any_state_brake"]),
        start_year=2021,
        end_year=2026,
    )
    v1.atomic_write_frame(eq, OUT / "oos_equity.parquet")
    v1.atomic_write_frame(trades, OUT / "oos_trades.parquet")
    yearly = pd.DataFrame(oos_metrics["yearly_rows"])
    v1.atomic_write_frame(yearly, OUT / "oos_yearly_metrics.csv")

    verdict = "PASS_MIN_GATE" if int(oos_metrics["pass_vni20"]) >= 4 else "FAIL_GATE"
    lines = [
        "# Technical T2 Walk-Forward Strict",
        "",
        f"Verdict: **{verdict}**",
        "",
        "Method: train 2016-2020 selects one V5 dual-sleeve cell by train CAGR, then freezes all params and evaluates 2021-2026 OOS.",
        "",
        "## Frozen Params",
        "",
        "```json",
        json.dumps(frozen_params, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Train Metrics",
        "",
        f"- Train CAGR: {train_metrics['cagr']:.2f}%",
        f"- Train MaxDD: {train_metrics['maxdd']:.2f}%",
        f"- Train VNI+20 pass: {int(train_metrics['pass_vni20'])}",
        "",
        "## OOS Metrics",
        "",
        f"- OOS VNI+20 pass: {int(oos_metrics['pass_vni20'])}/6",
        f"- OOS VNI+30 pass: {int(oos_metrics['pass_vni30'])}/6",
        f"- OOS CAGR: {oos_metrics['cagr']:.2f}%",
        f"- OOS MaxDD: {oos_metrics['maxdd']:.2f}%",
        f"- OOS min edge: {oos_metrics['min_edge_vs_vni']:.2f}pp",
        f"- OOS trades/year: {oos_metrics['turnover_trades_per_year']:.1f}",
        "",
        "## OOS Yearly Metrics",
        "",
        yearly.to_markdown(index=False),
        "",
        "## Top Train Rows",
        "",
        train.head(10).to_markdown(index=False),
    ]
    v1.atomic_write_text(OUT / "walk_forward_verdict.md", "\n".join(lines))
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_WALK_FORWARD_STRICT",
        "dashboard_status": "BLOCKED",
        "verdict": verdict,
        "frozen_params": frozen_params,
        "train_best_metrics": {k: v for k, v in train_metrics.items() if k != "yearly_rows"},
        "oos_metrics": {k: v for k, v in oos_metrics.items() if k != "yearly_rows"},
        "oos_yearly": oos_metrics["yearly_rows"],
        "train_rows": int(len(train)),
        "next_gate": "Claude review. If OOS VNI+20 < 4/6, stop mutation loop and reassess target/constraints.",
    }
    v1.atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
