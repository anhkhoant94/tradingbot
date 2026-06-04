"""Baseline + generic liquid-leadership overlay research.

This lane keeps the current flexible_vni30 baseline as the core selector and
adds one generic price/volume sleeve only when observable market-state features
show narrow/liquid leadership or early recovery. No ticker/year rescue.

The script screens candidates with the existing daily simulator, then verifies
the best rows with a stricter 1B VND, 100-share-lot execution approximation.
Dashboard remains blocked; this is research output only.
"""
from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backtest") not in sys.path:
    sys.path.insert(0, str(ROOT / "backtest"))

import pass30_direct_search as ds  # noqa: E402
from beat_vni30_daily_execution_sim import (  # noqa: E402
    align_history_to_calendar,
    effective_gap_threshold,
    load_daily_history,
    load_exchange_map,
    load_vni_daily,
    previous_close_at_idx,
    price_at_idx,
    simulate_daily,
)


CONFIG = ROOT / "output" / "beat_vni30_parallel" / "g2_latency_tplus3_mutation_v1" / "best_stock_only" / "config.json"
LABEL_DIR = ROOT / "output" / "beat_vni30_parallel" / "claude_lane_f2"
REGIME_PATH = ROOT / ".cache" / "backtest" / "regime_features_weekly.parquet"
OUT = ROOT / "output" / "beat_vni30_parallel" / "codex_baseline_liquid_leadership_overlay_20260527"
YEARS = range(2021, 2027)


@dataclass
class Lot:
    symbol: str
    shares: int
    entry_idx: int
    entry_px: float
    entry_date: pd.Timestamp


@dataclass
class Pending:
    symbol: str
    amount_vnd: float
    start_idx: int
    deadline_idx: int
    prev_close: float | None
    gap_threshold: float
    limit_buffer: float
    first_day_seen: bool = False


def add_vni20(metrics: dict) -> dict:
    out = dict(metrics)
    out["pass_vni20"] = int(sum(float(out.get(f"edge_y{y}", np.nan)) >= 20.0 for y in YEARS))
    out["min_gap_to_vni20"] = float(min(float(out.get(f"edge_y{y}", np.nan)) - 20.0 for y in YEARS))
    return out


def score_sort_key(metrics: dict) -> tuple:
    return (
        int(metrics.get("pass_vni20", 0)),
        float(metrics.get("min_gap_to_vni20", -999.0)),
        int(metrics.get("pass_vni30", 0)),
        float(metrics.get("cagr", -999.0)),
        -abs(float(metrics.get("maxdd", -999.0))),
    )


def clip_symbol_caps(weights: dict[str, float], cap: float = 0.33) -> dict[str, float]:
    return {s: min(float(w), cap) for s, w in weights.items() if float(w) > 1e-7}


def active_dates(regime: pd.DataFrame, mode: str, p: dict) -> dict[pd.Timestamp, bool]:
    out: dict[pd.Timestamp, bool] = {}
    for row in regime.itertuples(index=False):
        broad_ok = pd.isna(row.breadth_top200) or row.breadth_top200 <= p["broad_max"]
        if mode == "narrow_liquid":
            active = (
                row.mega_cap_breadth >= p["mega_breadth"]
                and row.mega_cap_ret13 >= p["mega_ret"]
                and row.mega_cap_leadership_pit >= p["leadership"]
                and broad_ok
            )
        elif mode == "vn30_leadership":
            active = row.vn30_breadth >= p["vn30_breadth"] and row.vn30_rs26 >= p["vn30_rs26"]
        elif mode == "recovery_liquid":
            active = (
                row.breadth_recovery_2w >= 1.0
                and row.breadth_top200 <= p["recovery_breadth_max"]
                and row.mega_cap_breadth >= p["mega_breadth"]
            )
        elif mode == "leadership_or_recovery":
            active = (
                (
                    row.mega_cap_breadth >= p["mega_breadth"]
                    and row.mega_cap_ret13 >= p["mega_ret"]
                    and row.mega_cap_leadership_pit >= p["leadership"]
                    and broad_ok
                )
                or (
                    row.breadth_recovery_2w >= 1.0
                    and row.breadth_top200 <= p["recovery_breadth_max"]
                    and row.vn30_breadth >= p["vn30_breadth"]
                )
            )
        else:
            raise ValueError(mode)
        out[pd.Timestamp(row.date)] = bool(active)
    return out


def overlay_score(g: pd.DataFrame, mode: str) -> pd.Series:
    z = lambda s: pd.to_numeric(s, errors="coerce").rank(pct=True).fillna(0.5) * 100.0
    if mode == "rs_high":
        return 0.40 * z(g["rs13"]) + 0.25 * z(g["near_high52"]) + 0.20 * z(g["avg_value_20d_bil"]) + 0.15 * z(g["ret13"])
    if mode == "momo_liq":
        return 0.35 * z(g["ret13"]) + 0.25 * z(g["ret26"]) + 0.25 * z(g["avg_value_20d_bil"]) + 0.15 * z(g["near_high52"])
    if mode == "breakout_liq":
        return 0.40 * z(g["near_high52"]) + 0.25 * z(g["ret4"]) + 0.20 * z(g["avg_value_20d_bil"]) + 0.15 * z(g["rs13"])
    return 0.30 * z(g["rs13"]) + 0.25 * z(g["ret13"]) + 0.25 * z(g["avg_value_20d_bil"]) + 0.20 * z(g["near_high52"])


def build_overlay_holdings(matrix: pd.DataFrame, active: dict[pd.Timestamp, bool], p: dict) -> pd.DataFrame:
    rows = []
    for dt, g in matrix.groupby("date", sort=True):
        dt = pd.Timestamp(dt)
        if not active.get(dt, False):
            continue
        x = g.copy()
        mask = (
            (pd.to_numeric(x["avg_value_20d_bil"], errors="coerce") >= p["liq_min"])
            & (pd.to_numeric(x["ret13"], errors="coerce") >= p["ret13_min"])
            & (pd.to_numeric(x["rs13"], errors="coerce") >= p["rs13_min"])
            & (pd.to_numeric(x["near_high52"], errors="coerce") >= p["near_min"])
            & (pd.to_numeric(x["rsi14"], errors="coerce").fillna(50.0).between(p["rsi_min"], p["rsi_max"]))
        )
        x = x[mask].copy()
        if x.empty:
            continue
        x["_score"] = overlay_score(x, p["score_mode"])
        x = x.sort_values(["_score", "avg_value_20d_bil"], ascending=[False, False]).head(p["top_n"])
        if x.empty:
            continue
        weights = np.ones(len(x), dtype=float) / len(x)
        for weight, (_, row) in zip(weights, x.iterrows()):
            rows.append({"date": dt, "symbol": str(row["symbol"]), "weight": float(weight), "overlay_score": float(row["_score"])})
    return pd.DataFrame(rows)


def blend_holdings(base: pd.DataFrame, overlay: pd.DataFrame, active: dict[pd.Timestamp, bool], alpha: float) -> pd.DataFrame:
    frames = []
    base_groups = {pd.Timestamp(k): v for k, v in base.groupby("date", sort=False)}
    overlay_groups = {pd.Timestamp(k): v for k, v in overlay.groupby("date", sort=False)} if not overlay.empty else {}
    for dt in sorted(base_groups):
        b = base_groups[dt].copy()
        o = overlay_groups.get(dt)
        if active.get(dt, False) and o is not None and not o.empty:
            b["weight"] = b["weight"].astype(float) * (1.0 - alpha)
            o = o[["date", "symbol", "weight"]].copy()
            o["weight"] = o["weight"].astype(float) * alpha
            frames.append(b)
            frames.append(o)
        else:
            frames.append(b)
    out = pd.concat(frames, ignore_index=True)
    out = out.groupby(["date", "symbol"], as_index=False)["weight"].sum()
    capped = []
    for dt, g in out.groupby("date", sort=False):
        w = clip_symbol_caps(dict(zip(g["symbol"], g["weight"])), 0.33)
        capped.extend({"date": dt, "symbol": s, "weight": v} for s, v in w.items())
    return pd.DataFrame(capped)


def px(hist, symbol, idx, field, tradable=False):
    return price_at_idx(hist, symbol, idx, field, require_tradable=tradable)


def hard_locked_limit_day(hist, symbol: str, idx: int) -> bool:
    """Proxy for user's rule: skip only when the stock is hard limit all day."""
    prev = previous_close_at_idx(hist, symbol, idx)
    if not prev or prev <= 0:
        return False
    vals = [px(hist, symbol, idx, field) for field in ["open", "high", "low", "close"]]
    if any(v is None or v <= 0 for v in vals):
        return False
    flat = max(vals) / min(vals) - 1.0 <= 0.002
    gap = vals[0] / prev - 1.0
    return bool(flat and (gap >= 0.068 or gap <= -0.068))


def nav_at(cash: float, lots: dict[str, list[Lot]], hist, idx: int, field: str) -> float:
    nav = cash
    for symbol, symbol_lots in lots.items():
        price = px(hist, symbol, idx, field)
        if price is not None:
            nav += sum(l.shares for l in symbol_lots) * price * 1000.0
    return nav


def value_at(lots: dict[str, list[Lot]], hist, symbol: str, idx: int, field: str) -> float:
    price = px(hist, symbol, idx, field)
    if price is None:
        return 0.0
    return sum(l.shares for l in lots.get(symbol, [])) * price * 1000.0


def sell_amount(cash, lots, hist, symbol, amount_vnd, sell_px, idx, date, min_sell, sell_cost, trades, reason):
    remain = amount_vnd
    kept: list[Lot] = []
    for lot in lots.get(symbol, []):
        if remain <= 0 or idx < lot.entry_idx + min_sell:
            kept.append(lot)
            continue
        lot_value = lot.shares * sell_px * 1000.0
        if remain >= lot_value * 0.995:
            sell_shares = lot.shares
        else:
            sell_shares = int(math.floor((remain / (sell_px * 1000.0)) / 100.0) * 100)
        if sell_shares <= 0:
            kept.append(lot)
            continue
        sell_shares = min(sell_shares, lot.shares)
        proceeds = sell_shares * sell_px * 1000.0 * (1.0 - sell_cost)
        cash += proceeds
        remain -= sell_shares * sell_px * 1000.0
        lot.shares -= sell_shares
        trades.append({
            "date": date,
            "symbol": symbol,
            "side": "SELL",
            "shares": sell_shares,
            "price": sell_px,
            "cash_vnd": proceeds,
            "reason": reason,
            "entry_date": lot.entry_date,
            "entry_px": lot.entry_px,
            "holding_sessions": idx - lot.entry_idx,
        })
        if lot.shares > 0:
            kept.append(lot)
    if kept:
        lots[symbol] = kept
    elif symbol in lots:
        del lots[symbol]
    return cash


def simulate_strict_100lot(
    holdings: pd.DataFrame,
    hist,
    vni,
    signal_dates,
    execution: dict,
    *,
    buy_cost: float = 0.0030,
    sell_cost: float = 0.0040,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    dates = [pd.Timestamp(x) for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()]
    targets = {
        pd.Timestamp(d): dict(zip(g["symbol"].astype(str), g["weight"].astype(float)))
        for d, g in holdings.groupby("date", sort=False)
    }
    exchange_map = load_exchange_map()
    cash = 1_000_000_000.0
    lots: dict[str, list[Lot]] = {}
    pending: list[Pending] = []
    rows = []
    trades = []
    signal_set = set(signal_dates)
    gap = float(execution["gap"])
    buffer = float(execution["buffer"])
    pullback = int(execution["pullback"])
    min_sell = int(execution["min_sell"])
    stop = float(execution["stop"])
    for idx, date in enumerate(dates):
        if date in signal_set:
            pending = []
            target = targets.get(date, {})
            nav_open = nav_at(cash, lots, hist, idx, "open")
            for symbol in sorted(set(lots) | set(target)):
                open_px = px(hist, symbol, idx, "open", tradable=True)
                if open_px is None:
                    continue
                cur = value_at(lots, hist, symbol, idx, "open")
                tgt = nav_open * float(target.get(symbol, 0.0))
                if cur > tgt + nav_open * 0.001:
                    cash = sell_amount(cash, lots, hist, symbol, cur - tgt, open_px, idx, date, min_sell, sell_cost, trades, "rebalance")
            nav_after = nav_at(cash, lots, hist, idx, "open")
            buys = []
            for symbol, weight in sorted(target.items(), key=lambda kv: -kv[1]):
                cur = value_at(lots, hist, symbol, idx, "open")
                buy = max(0.0, nav_after * float(weight) - cur)
                if buy > nav_after * 0.001:
                    buys.append((symbol, buy))
            total_buy = sum(v for _, v in buys)
            scale = min(1.0, cash / total_buy) if total_buy > 0 else 0.0
            for symbol, buy in buys:
                prev_close = previous_close_at_idx(hist, symbol, idx)
                pending.append(Pending(
                    symbol=symbol,
                    amount_vnd=buy * scale,
                    start_idx=idx,
                    deadline_idx=min(len(dates) - 1, idx + max(1, pullback) - 1),
                    prev_close=prev_close,
                    gap_threshold=gap,
                    limit_buffer=buffer,
                ))
        keep = []
        for order in pending:
            if idx < order.start_idx:
                keep.append(order)
                continue
            if idx > order.deadline_idx:
                trades.append({"date": date, "symbol": order.symbol, "side": "MISS_BUY", "shares": 0, "price": np.nan, "cash_vnd": 0.0, "reason": "expired_no_pullback"})
                continue
            if hard_locked_limit_day(hist, order.symbol, idx):
                keep.append(order)
                continue
            open_px = px(hist, order.symbol, idx, "open", tradable=True)
            if open_px is None:
                keep.append(order)
                continue
            fill = None
            mode = ""
            if not order.first_day_seen:
                order.first_day_seen = True
                if order.prev_close and order.prev_close > 0 and open_px / order.prev_close - 1.0 <= order.gap_threshold:
                    fill = open_px
                    mode = "open"
            if fill is None and order.prev_close and order.prev_close > 0:
                limit_px = order.prev_close * (1.0 + order.limit_buffer)
                low_px = px(hist, order.symbol, idx, "low")
                if low_px is not None and low_px <= limit_px:
                    fill = open_px if open_px <= limit_px else limit_px
                    mode = "pullback_limit"
            if fill is None:
                keep.append(order)
                continue
            spend = min(order.amount_vnd, cash)
            shares = int(math.floor((spend / (fill * 1000.0 * (1.0 + buy_cost))) / 100.0) * 100)
            if shares <= 0:
                continue
            cost = shares * fill * 1000.0 * (1.0 + buy_cost)
            cash -= cost
            lots.setdefault(order.symbol, []).append(Lot(order.symbol, shares, idx, fill, date))
            trades.append({"date": date, "symbol": order.symbol, "side": "BUY", "shares": shares, "price": fill, "cash_vnd": -cost, "reason": mode, "entry_date": date, "entry_px": fill, "holding_sessions": 0})
        pending = keep
        if stop > 0:
            for symbol in list(lots):
                open_px = px(hist, symbol, idx, "open", tradable=True)
                low_px = px(hist, symbol, idx, "low")
                if open_px is None or low_px is None:
                    continue
                kept = []
                for lot in lots.get(symbol, []):
                    if idx < lot.entry_idx + min_sell:
                        kept.append(lot)
                        continue
                    stop_px = lot.entry_px * (1.0 - stop)
                    if low_px <= stop_px:
                        fill = open_px if open_px <= stop_px else stop_px
                        proceeds = lot.shares * fill * 1000.0 * (1.0 - sell_cost)
                        cash += proceeds
                        trades.append({"date": date, "symbol": symbol, "side": "SELL", "shares": lot.shares, "price": fill, "cash_vnd": proceeds, "reason": "daily_stop", "entry_date": lot.entry_date, "entry_px": lot.entry_px, "holding_sessions": idx - lot.entry_idx})
                    else:
                        kept.append(lot)
                if kept:
                    lots[symbol] = kept
                elif symbol in lots:
                    del lots[symbol]
        nav = nav_at(cash, lots, hist, idx, "close")
        invested = sum(value_at(lots, hist, symbol, idx, "close") for symbol in lots)
        rows.append({"date": date, "nav": nav, "cash": cash, "exposure": invested / nav if nav > 0 else 0.0, "position_count": len(lots)})
    eq = pd.DataFrame(rows)
    eq["ret"] = eq["nav"].pct_change().fillna(0.0)
    trades_df = pd.DataFrame(trades)
    metrics = add_vni20(sim_add_metrics(eq, vni))
    metrics["trade_count"] = int(len(trades_df[trades_df["side"].isin(["BUY", "SELL"])])) if not trades_df.empty else 0
    metrics["lot_violations"] = 0 if trades_df.empty else int(((pd.to_numeric(trades_df.loc[trades_df["side"].isin(["BUY", "SELL"]), "shares"], errors="coerce") % 100) != 0).sum())
    metrics["avg_exposure"] = float(eq["exposure"].mean()) if not eq.empty else np.nan
    return eq, trades_df, metrics


def sim_add_metrics(eq: pd.DataFrame, vni: pd.DataFrame) -> dict:
    from beat_vni30_daily_execution_sim import add_daily_vni30_metrics

    return add_daily_vni30_metrics(eq, vni, canonical_vni=vni)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg, matrix, base_holdings, weekly_eq = sim_generate()
    matrix = matrix.copy()
    matrix["date"] = pd.to_datetime(matrix["date"])
    base_holdings = base_holdings.copy()
    base_holdings["date"] = pd.to_datetime(base_holdings["date"])
    regime = pd.read_parquet(REGIME_PATH)
    regime["date"] = pd.to_datetime(regime["date"])
    vni = load_vni_daily()
    signal_dates = sorted(pd.Timestamp(x) for x in weekly_eq["date"].dropna().unique())
    all_symbols = sorted(set(matrix["symbol"].astype(str)).union(set(base_holdings["symbol"].astype(str))))
    hist_all = load_daily_history(all_symbols)
    daily_dates = [pd.Timestamp(x) for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()]
    hist_all = align_history_to_calendar(hist_all, daily_dates)

    rng = random.Random(20260527)
    candidates = []
    modes = ["narrow_liquid", "vn30_leadership", "recovery_liquid", "leadership_or_recovery"]
    score_modes = ["rs_high", "momo_liq", "breakout_liq", "combo"]
    for run_id in range(900):
        p = {
            "run_id": run_id,
            "mode": rng.choice(modes),
            "score_mode": rng.choice(score_modes),
            "alpha": rng.choice([0.10, 0.15, 0.20, 0.25, 0.30, 0.35]),
            "top_n": rng.choice([1, 2, 3, 4]),
            "liq_min": rng.choice([20.0, 35.0, 50.0, 80.0, 120.0]),
            "ret13_min": rng.choice([-0.10, -0.05, 0.0, 0.05, 0.10]),
            "rs13_min": rng.choice([-0.10, -0.05, 0.0, 0.03, 0.06]),
            "near_min": rng.choice([0.55, 0.65, 0.75, 0.85, 0.95]),
            "rsi_min": rng.choice([30.0, 35.0, 40.0]),
            "rsi_max": rng.choice([80.0, 90.0, 95.0]),
            "mega_breadth": rng.choice([0.40, 0.50, 0.60, 0.70]),
            "mega_ret": rng.choice([-0.05, 0.0, 0.03, 0.06]),
            "leadership": rng.choice([-0.05, 0.0, 0.03, 0.06]),
            "broad_max": rng.choice([0.25, 0.35, 0.45, 0.60]),
            "vn30_breadth": rng.choice([0.10, 0.20, 0.30, 0.40]),
            "vn30_rs26": rng.choice([-0.05, 0.0, 0.03, 0.06]),
            "recovery_breadth_max": rng.choice([0.15, 0.25, 0.35, 0.50]),
            "gap": rng.choice([0.03, 0.07]),
            "buffer": rng.choice([0.005, 0.015]),
            "pullback": rng.choice([2, 4]),
            "min_sell": rng.choice([3, 4]),
            "stop": rng.choice([0.0, 0.05]),
        }
        active = active_dates(regime, p["mode"], p)
        active_rate = float(np.mean([active.get(d, False) for d in signal_dates]))
        if active_rate < 0.03 or active_rate > 0.60:
            continue
        overlay = build_overlay_holdings(matrix, active, p)
        if overlay.empty:
            continue
        holdings = blend_holdings(base_holdings, overlay, active, p["alpha"])
        eq, trades, metrics = simulate_daily(
            holdings,
            hist_all,
            vni,
            gap_threshold=p["gap"],
            limit_buffer=p["buffer"],
            pullback_sessions=p["pullback"],
            min_sell_sessions=p["min_sell"],
            daily_stop_loss=p["stop"],
            extra_slippage_per_side=0.0015,
            signal_dates=signal_dates,
            nav0=1.0,
        )
        metrics = add_vni20(metrics)
        row = {**p, **metrics, "active_rate": active_rate, "overlay_symbols": int(overlay["symbol"].nunique()), "overlay_weeks": int(overlay["date"].nunique())}
        candidates.append(row)
        if len(candidates) % 10 == 0:
            pd.DataFrame(candidates).sort_values(["pass_vni20", "min_gap_to_vni20", "pass_vni30", "cagr"], ascending=[False, False, False, False]).to_csv(OUT / "screen_results.csv", index=False)

    if not candidates:
        raise RuntimeError("no candidates generated")
    screen = pd.DataFrame(candidates).sort_values(["pass_vni20", "min_gap_to_vni20", "pass_vni30", "cagr"], ascending=[False, False, False, False])
    screen.to_csv(OUT / "screen_results.csv", index=False)

    strict_rows = []
    best_payload = None
    for _, p in screen.head(30).iterrows():
        pdict = p.to_dict()
        active = active_dates(regime, pdict["mode"], pdict)
        overlay = build_overlay_holdings(matrix, active, pdict)
        holdings = blend_holdings(base_holdings, overlay, active, float(pdict["alpha"]))
        execution = {k: pdict[k] for k in ["gap", "buffer", "pullback", "min_sell", "stop"]}
        eq, trades, metrics = simulate_strict_100lot(holdings, hist_all, vni, signal_dates, execution)
        row = {**{k: pdict[k] for k in pdict if k in ["run_id", "mode", "score_mode", "alpha", "top_n", "liq_min", "ret13_min", "rs13_min", "near_min", "rsi_min", "rsi_max", "mega_breadth", "mega_ret", "leadership", "broad_max", "vn30_breadth", "vn30_rs26", "recovery_breadth_max", "gap", "buffer", "pullback", "min_sell", "stop", "active_rate"]}, **metrics}
        strict_rows.append(row)
        if best_payload is None or score_sort_key(row) > score_sort_key(best_payload[0]):
            best_payload = (row, eq, trades, holdings)
    strict = pd.DataFrame(strict_rows).sort_values(["pass_vni20", "min_gap_to_vni20", "pass_vni30", "cagr"], ascending=[False, False, False, False])
    strict.to_csv(OUT / "strict_100lot_results.csv", index=False)
    if best_payload:
        row, eq, trades, holdings = best_payload
        eq.to_parquet(OUT / "best_strict_equity.parquet", index=False)
        trades.to_csv(OUT / "best_strict_trades.csv", index=False)
        holdings.to_parquet(OUT / "best_strict_holdings.parquet", index=False)
        (OUT / "best_strict_metrics.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        lines = [
            "# Baseline + Liquid Leadership Overlay",
            "",
            "Research-only. Dashboard remains blocked.",
            "",
            f"Best strict VNI+20: {int(row['pass_vni20'])}/6",
            f"Best strict VNI+30: {int(row['pass_vni30'])}/6",
            f"CAGR: {float(row['cagr']):.2f}%",
            f"MaxDD: {float(row['maxdd']):.2f}%",
            f"Min gap to VNI+20: {float(row['min_gap_to_vni20']):.2f}pp",
            f"Mode: {row['mode']} / score: {row['score_mode']} / alpha: {float(row['alpha']):.2f}",
            "",
            "| Year | Edge | +20 | +30 |",
            "|---|---:|---|---|",
        ]
        for y in YEARS:
            edge = float(row[f"edge_y{y}"])
            lines.append(f"| {y} | {edge:.1f}pp | {'YES' if edge >= 20 else 'NO'} | {'YES' if edge >= 30 else 'NO'} |")
        (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("OUT", OUT)
    print(strict.head(15)[["run_id", "mode", "score_mode", "alpha", "top_n", "liq_min", "pass_vni20", "pass_vni30", "min_gap_to_vni20", "cagr", "maxdd", "edge_y2021", "edge_y2022", "edge_y2023", "edge_y2024", "edge_y2025", "edge_y2026"]].to_string(index=False))


def sim_generate():
    from beat_vni30_daily_execution_sim import generate_targets

    return generate_targets(CONFIG, LABEL_DIR)


if __name__ == "__main__":
    main()
