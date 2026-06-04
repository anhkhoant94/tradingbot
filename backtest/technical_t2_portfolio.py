from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / ".cache" / "backtest" / "history_clean"
VNI_PATH = ROOT / ".cache" / "backtest" / "vnindex_daily.parquet"
STATE_DIR = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"
TECH_PANEL = ROOT / "output" / "beat_vni30_parallel" / "technical_price_lab" / "technical_weekly_panel.parquet"
OUT = STATE_DIR / "portfolio_v1"

LOT_SIZE = 100
INITIAL_NAV_VND = 1_000_000_000.0
BUY_COST = 0.0015
SELL_COST = 0.0015
PRODUCTION_MIN_LIQ_BIL = 3.0

COMPOSITE_WEIGHTS = {
    "rs_13w": 0.30,
    "rs_26w": 0.15,
    "breakout_quality_100d": 0.25,
    "high52_proximity": 0.15,
    "pullback_quality": 0.10,
    "volume_expansion_20_60": 0.05,
}

STATE_PARAMS = {
    "broad_trend": {"exposure": 0.95, "max_holdings": 6, "max_weight": 0.22},
    "narrow_leadership": {"exposure": 0.65, "max_holdings": 4, "max_weight": 0.33},
    "recovery": {"exposure": 0.60, "max_holdings": 5, "max_weight": 0.25},
    "risk_off": {"exposure": 0.0, "max_holdings": 0, "max_weight": 0.0},
}

RISK_VOLUME_PARAMS = {
    **STATE_PARAMS,
    "risk_off": {"exposure": 0.30, "max_holdings": 4, "max_weight": 0.08},
}


@dataclass
class Lot:
    symbol: str
    shares: int
    entry_idx: int
    entry_date: pd.Timestamp
    entry_price: float


@dataclass
class PendingBuy:
    symbol: str
    target_value: float
    limit_price: float
    start_idx: int
    end_idx: int
    first_day: bool = True


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < 100 * 1024 * 1024:
        raise OSError(f"Refusing to write {path}: less than 100MB free in target directory")
    tmp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix.lower() == ".csv":
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(tmp, index=False)
        pd.read_parquet(tmp)
        with tmp.open("rb") as fh:
            fh.seek(max(0, tmp.stat().st_size - 4))
            if fh.read() != b"PAR1":
                raise OSError(f"Parquet footer check failed for temporary file {tmp}")
    tmp.replace(path)


def load_vni() -> pd.DataFrame:
    vni = pd.read_parquet(VNI_PATH).copy()
    vni["date"] = pd.to_datetime(vni["date"])
    vni["close"] = pd.to_numeric(vni["close"], errors="coerce")
    return vni.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def robust_z(series: pd.Series) -> pd.Series:
    data = pd.to_numeric(series, errors="coerce")
    med = data.median()
    mad = (data - med).abs().median()
    if not np.isfinite(mad) or mad <= 1e-12:
        std = data.std()
        if not np.isfinite(std) or std <= 1e-12:
            return pd.Series(0.0, index=series.index)
        return ((data - data.mean()) / std).clip(-3, 3).fillna(0.0)
    return ((data - med) / (1.4826 * mad)).clip(-3, 3).fillna(0.0)


def next_trading_day(dates: list[pd.Timestamp], after_date: pd.Timestamp) -> pd.Timestamp | None:
    arr = np.array(dates, dtype="datetime64[ns]")
    pos = int(np.searchsorted(arr, np.datetime64(pd.Timestamp(after_date)), side="right"))
    if pos >= len(dates):
        return None
    return dates[pos]


def build_signal_targets(
    *,
    variant: str,
    entry_band: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    states = pd.read_parquet(STATE_DIR / "weekly_state_labels.parquet").copy()
    states["date"] = pd.to_datetime(states["date"])
    panel = pd.read_parquet(TECH_PANEL).copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["symbol"] = panel["symbol"].astype(str).str.upper()
    panel = panel[(panel["date"] >= "2020-12-01") & (panel["avg_value_20d_bil"] >= PRODUCTION_MIN_LIQ_BIL)].copy()

    vni_dates = [pd.Timestamp(x) for x in load_vni()["date"].tolist()]
    params_by_state = RISK_VOLUME_PARAMS if variant == "risk_volume" else STATE_PARAMS
    rows: list[dict] = []
    signal_dates: list[pd.Timestamp] = []

    for st in states.itertuples(index=False):
        friday = pd.Timestamp(st.date)
        exec_date = next_trading_day(vni_dates, friday)
        if exec_date is None or exec_date < pd.Timestamp("2021-01-01"):
            continue
        signal_dates.append(exec_date)
        state = str(st.state)
        params = params_by_state.get(state, STATE_PARAMS["risk_off"])
        exposure = float(params["exposure"])
        max_holdings = int(params["max_holdings"])
        max_weight = float(params["max_weight"])
        if exposure <= 0 or max_holdings <= 0:
            continue
        week = panel[panel["date"].eq(friday)].copy()
        if week.empty:
            continue
        if state == "risk_off" and variant == "risk_volume":
            week["score"] = (
                robust_z(week["volume_expansion_20_60"]) * 0.65
                + robust_z(week["pullback_quality"]) * 0.15
                + robust_z(week["rs_26w"]) * 0.10
                + robust_z(week["vol_contraction"]) * 0.10
            )
        else:
            score = pd.Series(0.0, index=week.index)
            for factor, weight in COMPOSITE_WEIGHTS.items():
                score = score + robust_z(week[factor]) * weight
            week["score"] = score
        week = week.replace([np.inf, -np.inf], np.nan).dropna(subset=["score", "close"])
        week = week[week["close"] >= 5.0].copy()
        if week.empty:
            continue
        selected = week.sort_values(["score", "avg_value_20d_bil"], ascending=[False, False]).head(max_holdings)
        if selected.empty:
            continue
        raw_weight = min(max_weight, exposure / len(selected))
        total = raw_weight * len(selected)
        if total > exposure and total > 0:
            raw_weight *= exposure / total
        for row in selected.itertuples(index=False):
            rows.append({
                "signal_friday": friday,
                "date": exec_date,
                "symbol": row.symbol,
                "state": state,
                "weight": float(raw_weight),
                "score": float(row.score),
                "entry_band": float(entry_band),
                "avg_value_20d_bil": float(row.avg_value_20d_bil),
            })
    return pd.DataFrame(rows), sorted(set(signal_dates))


def load_aligned_history(symbols: list[str], dates: list[pd.Timestamp]) -> dict[str, pd.DataFrame]:
    calendar = pd.DataFrame({"time": pd.to_datetime(dates)})
    hist: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = HISTORY_DIR / f"{symbol}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty:
            continue
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").drop_duplicates("time")
        needed = ["open", "high", "low", "close", "volume"]
        if any(col not in df.columns for col in needed):
            continue
        for col in needed:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        aligned = calendar.merge(df[["time", *needed]].assign(tradable=True), on="time", how="left")
        aligned["tradable"] = aligned["tradable"].eq(True)
        aligned["close"] = aligned["close"].ffill()
        for col in ["open", "high", "low"]:
            aligned[col] = aligned[col].fillna(aligned["close"])
        aligned["volume"] = aligned["volume"].fillna(0.0)
        if aligned["close"].notna().sum() == 0:
            continue
        aligned = aligned.reset_index(drop=True)
        aligned.attrs["arrays"] = {col: aligned[col].to_numpy() for col in ["open", "high", "low", "close", "volume", "tradable"]}
        hist[symbol] = aligned
    return hist


def px(hist: dict[str, pd.DataFrame], symbol: str, idx: int, field: str, tradable: bool = False) -> float | None:
    df = hist.get(symbol)
    if df is None or idx < 0 or idx >= len(df):
        return None
    arrs = df.attrs["arrays"]
    if tradable and not bool(arrs["tradable"][idx]):
        return None
    value = float(arrs[field][idx])
    if not np.isfinite(value) or value <= 0:
        return None
    return value * 1000.0


def portfolio_value(cash: float, lots: dict[str, list[Lot]], hist: dict[str, pd.DataFrame], idx: int, field: str = "close") -> float:
    total = cash
    for symbol, sym_lots in lots.items():
        price = px(hist, symbol, idx, field, tradable=False)
        if price is None:
            continue
        total += sum(lot.shares for lot in sym_lots) * price
    return total


def symbol_shares(lots: dict[str, list[Lot]], symbol: str) -> int:
    return int(sum(lot.shares for lot in lots.get(symbol, [])))


def symbol_value(lots: dict[str, list[Lot]], hist: dict[str, pd.DataFrame], symbol: str, idx: int, field: str = "open") -> float:
    price = px(hist, symbol, idx, field, tradable=False)
    if price is None:
        return 0.0
    return symbol_shares(lots, symbol) * price


def sell_symbol(
    lots: dict[str, list[Lot]],
    symbol: str,
    shares_to_sell: int,
    price: float,
    idx: int,
    date: pd.Timestamp,
    trades: list[dict],
    reason: str,
) -> float:
    shares_to_sell = int(math.floor(shares_to_sell / LOT_SIZE) * LOT_SIZE)
    if shares_to_sell <= 0:
        return 0.0
    proceeds = 0.0
    remaining = shares_to_sell
    kept: list[Lot] = []
    for lot in lots.get(symbol, []):
        if remaining <= 0:
            kept.append(lot)
            continue
        if idx - lot.entry_idx < 3:
            kept.append(lot)
            continue
        sell_shares = min(lot.shares, remaining)
        sell_shares = int(math.floor(sell_shares / LOT_SIZE) * LOT_SIZE)
        if sell_shares <= 0:
            kept.append(lot)
            continue
        cash_in = sell_shares * price * (1.0 - SELL_COST)
        proceeds += cash_in
        remaining -= sell_shares
        lot.shares -= sell_shares
        trades.append({
            "date": date,
            "symbol": symbol,
            "side": "SELL",
            "shares": sell_shares,
            "price": price / 1000.0,
            "cash_vnd": cash_in,
            "reason": reason,
            "entry_date": lot.entry_date,
            "entry_price": lot.entry_price / 1000.0,
            "holding_sessions": idx - lot.entry_idx,
        })
        if lot.shares > 0:
            kept.append(lot)
    if kept:
        lots[symbol] = kept
    else:
        lots.pop(symbol, None)
    return proceeds


def simulate(
    targets: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    *,
    variant: str,
    entry_band: float,
    slippage_bps: int,
    min_liq_bil: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    dates = [pd.Timestamp(x) for x in load_vni()["date"].tolist()]
    dates = [d for d in dates if pd.Timestamp("2021-01-01") <= d <= max(signal_dates)]
    signal_dates = [d for d in signal_dates if d in set(dates)]
    symbols = sorted(targets["symbol"].astype(str).unique()) if not targets.empty else []
    hist = load_aligned_history(symbols, dates)
    targets = targets[targets["symbol"].isin(hist.keys())].copy()
    targets_by_date = {
        pd.Timestamp(date): group.sort_values("weight", ascending=False)
        for date, group in targets.groupby("date")
    }
    signal_set = set(signal_dates)
    cash = INITIAL_NAV_VND
    lots: dict[str, list[Lot]] = {}
    pending: list[PendingBuy] = []
    rows: list[dict] = []
    trades: list[dict] = []
    buy_cost = slippage_bps / 10000.0
    sell_cost_global = slippage_bps / 10000.0
    global SELL_COST
    old_sell_cost = SELL_COST
    SELL_COST = sell_cost_global
    try:
        for idx, date in enumerate(dates):
            if date in signal_set:
                pending = []
                target_group = targets_by_date.get(date, pd.DataFrame())
                target_weights = dict(zip(target_group["symbol"], target_group["weight"])) if not target_group.empty else {}
                nav_open = portfolio_value(cash, lots, hist, idx, "open")
                for symbol in sorted(set(lots) | set(target_weights)):
                    open_px = px(hist, symbol, idx, "open", tradable=True)
                    if open_px is None:
                        continue
                    cur_val = symbol_value(lots, hist, symbol, idx, "open")
                    tgt_val = nav_open * float(target_weights.get(symbol, 0.0))
                    if cur_val > tgt_val + nav_open * 0.002:
                        shares = int((cur_val - tgt_val) / open_px)
                        cash += sell_symbol(lots, symbol, shares, open_px, idx, date, trades, "rebalance")
                nav_after_sells = portfolio_value(cash, lots, hist, idx, "open")
                for symbol, weight in sorted(target_weights.items(), key=lambda kv: -kv[1]):
                    open_px = px(hist, symbol, idx, "open", tradable=True)
                    if open_px is None:
                        continue
                    cur_val = symbol_value(lots, hist, symbol, idx, "open")
                    buy_value = max(0.0, nav_after_sells * float(weight) - cur_val)
                    if buy_value <= nav_after_sells * 0.002:
                        continue
                    prev_close = px(hist, symbol, idx - 1, "close", tradable=False)
                    if prev_close is None:
                        continue
                    pending.append(PendingBuy(
                        symbol=symbol,
                        target_value=buy_value,
                        limit_price=prev_close * (1.0 + entry_band),
                        start_idx=idx,
                        end_idx=min(len(dates) - 1, idx + 2),
                    ))
            still_pending: list[PendingBuy] = []
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
                open_px = px(hist, order.symbol, idx, "open", tradable=True)
                low_px = px(hist, order.symbol, idx, "low", tradable=True)
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
                shares = int(math.floor(spend / (fill_px * (1.0 + buy_cost)) / LOT_SIZE) * LOT_SIZE)
                if shares <= 0:
                    continue
                cost = shares * fill_px * (1.0 + buy_cost)
                cash -= cost
                lots.setdefault(order.symbol, []).append(Lot(order.symbol, shares, idx, date, fill_px))
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
            nav = portfolio_value(cash, lots, hist, idx, "close")
            invested = nav - cash
            rows.append({
                "date": date,
                "nav": nav,
                "cash": cash,
                "exposure": invested / nav if nav > 0 else 0.0,
                "position_count": len(lots),
                "pending_buy_count": len(pending),
                "is_signal_day": date in signal_set,
            })
    finally:
        SELL_COST = old_sell_cost
    eq = pd.DataFrame(rows)
    trades_df = pd.DataFrame(trades)
    metrics = compute_metrics(eq)
    metrics.update({
        "variant": variant,
        "entry_band": entry_band,
        "slippage_bps": slippage_bps,
        "min_liq_bil": min_liq_bil,
        "trade_count": int((trades_df["side"].isin(["BUY", "SELL"])).sum()) if not trades_df.empty else 0,
        "turnover_trades_per_year": float((trades_df["side"].isin(["BUY", "SELL"])).sum() / max(1.0, ((eq["date"].max() - eq["date"].min()).days / 365.25))) if not eq.empty and not trades_df.empty else 0.0,
        "avg_exposure": float(eq["exposure"].mean()) if not eq.empty else np.nan,
        "max_exposure": float(eq["exposure"].max()) if not eq.empty else np.nan,
    })
    return eq, trades_df, metrics


def compute_metrics(eq: pd.DataFrame) -> dict:
    vni = load_vni()
    eq = eq.copy()
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
    for year in range(2021, 2027):
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


def run_grid() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    best_key = None
    best_payload = None
    for variant in ["risk_cash", "risk_volume"]:
        for entry_band in [0.0, 0.01, 0.03]:
            targets, signal_dates = build_signal_targets(variant=variant, entry_band=entry_band)
            for slippage_bps in [15, 30]:
                for min_liq_bil in [3.0, 5.0]:
                    eq, trades, metrics = simulate(
                        targets,
                        signal_dates,
                        variant=variant,
                        entry_band=entry_band,
                        slippage_bps=slippage_bps,
                        min_liq_bil=min_liq_bil,
                    )
                    row = {k: v for k, v in metrics.items() if k != "yearly_rows"}
                    results.append(row)
                    key = (
                        int(metrics["pass_vni20"]),
                        float(metrics["min_edge_vs_vni"]),
                        float(metrics["cagr"]),
                        -abs(float(metrics["maxdd"])),
                    )
                    if best_key is None or key > best_key:
                        best_key = key
                        best_payload = (eq, trades, metrics, targets)
    results_df = pd.DataFrame(results).sort_values(
        ["pass_vni20", "min_edge_vs_vni", "cagr"],
        ascending=[False, False, False],
    )
    atomic_write_frame(results_df, OUT / "stress_grid_results.csv")
    if best_payload is None:
        return
    eq, trades, metrics, targets = best_payload
    atomic_write_frame(eq, OUT / "daily_lot_equity.parquet")
    atomic_write_frame(trades, OUT / "daily_lot_trades.parquet")
    atomic_write_frame(targets, OUT / "weekly_targets.parquet")
    yearly = pd.DataFrame(metrics["yearly_rows"])
    atomic_write_frame(yearly, OUT / "portfolio_yearly_metrics.csv")
    summary_lines = [
        "# Technical T2 Portfolio V1",
        "",
        "Status: research-only strict daily-lot prototype. Dashboard remains BLOCKED.",
        "",
        f"Best variant: {metrics['variant']}",
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
        "## Stress Grid Top Rows",
        "",
        results_df.head(12).to_markdown(index=False),
    ]
    atomic_write_text(OUT / "candidate_summary.md", "\n".join(summary_lines))
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "T2_B_C_PORTFOLIO_V1",
        "dashboard_status": "BLOCKED",
        "best_metrics": {k: v for k, v in metrics.items() if k != "yearly_rows"},
        "best_yearly": metrics["yearly_rows"],
        "stress_rows": int(len(results_df)),
        "next_gate": "Claude CV-T3 review; continue research because VNI+20 6/6 not yet guaranteed unless best pass_vni20==6.",
    }
    atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    run_grid()
