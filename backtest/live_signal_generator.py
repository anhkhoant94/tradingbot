"""Live/paper-trade signal generator for the verified pure-stock setup.

This script intentionally reads the honest project artifacts instead of the
legacy equity curve metrics. It produces a weekly target portfolio, order list,
and briefing for the rank_best_full + VNI 8w cash overlay policy.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
BACKTEST_CACHE = ROOT / ".cache" / "backtest"
DEFAULT_SCORES_DIR = BACKTEST_CACHE / "scores_2016_v4"
DEFAULT_HISTORY_DIR = BACKTEST_CACHE / "history_clean"
DEFAULT_VNI_PATH = BACKTEST_CACHE / "vnindex_daily.parquet"
DEFAULT_OUT_DIR = ROOT / "output" / "live_signals"
DEFAULT_SCREENING_RESULTS = ROOT / "output" / "screening_full_results.csv"

FEE_BUY = 0.0015
FEE_SELL = 0.0015
TAX_SELL = 0.0010


@dataclass
class OverlayState:
    decision: str
    trade_date: str
    signal_date: str | None
    vni_close_signal: float | None
    vni_ret_8w_pct: float | None
    overlay_exposure: float
    reason: str
    latest_vni_date: str | None
    latest_vni_close: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate weekly live/paper-trade signals for rank_best_full."
    )
    parser.add_argument("--as-of", default=None, help="Signal date, default latest VNI/cache date.")
    parser.add_argument("--nav", type=float, default=1_000_000_000, help="Portfolio NAV in VND.")
    parser.add_argument("--scores-dir", default=str(DEFAULT_SCORES_DIR))
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--vni-path", default=str(DEFAULT_VNI_PATH))
    parser.add_argument("--holdings", default=None, help="Optional CSV with symbol,shares,entry_price_k.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--min-score", type=float, default=50.0)
    parser.add_argument("--max-holdings", type=int, default=6)
    parser.add_argument("--max-weight", type=float, default=0.20)
    parser.add_argument("--cash-buffer", type=float, default=0.05)
    parser.add_argument("--overlay-threshold", type=float, default=-0.06)
    parser.add_argument("--overlay-lookback-weeks", type=int, default=8)
    parser.add_argument("--overlay-lag-weeks", type=int, default=2)
    parser.add_argument("--stop-loss-pct", type=float, default=0.12)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--no-conviction-scaling", action="store_true")
    return parser.parse_args()


def clean_date(value: str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize(None).normalize()


def _valid_signal_date(value: object) -> pd.Timestamp | None:
    try:
        ts = clean_date(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts < pd.Timestamp("2000-01-01") or ts > pd.Timestamp.today().normalize() + pd.Timedelta(days=7):
        return None
    return ts


def default_as_of(vni_path: Path, scores_dir: Path | None = None) -> pd.Timestamp:
    candidates: list[pd.Timestamp] = []
    if vni_path.exists():
        vni = pd.read_parquet(vni_path)
        if not vni.empty and "date" in vni.columns:
            ts = _valid_signal_date(pd.to_datetime(vni["date"], errors="coerce").max())
            if ts is not None:
                candidates.append(ts)
    if scores_dir is not None and scores_dir.exists():
        for path in scores_dir.glob("*.parquet"):
            ts = _valid_signal_date(path.stem)
            if ts is not None:
                candidates.append(ts)
    if candidates:
        return max(candidates)
    return clean_date(pd.Timestamp.today())


def next_monday(as_of: pd.Timestamp) -> pd.Timestamp:
    days = (7 - as_of.weekday()) % 7
    if days == 0:
        return as_of
    return as_of + pd.Timedelta(days=days)


def load_score_snapshot(
    scores_dir: Path,
    as_of: pd.Timestamp,
    fallback_csv: Path = DEFAULT_SCREENING_RESULTS,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    files = sorted(scores_dir.glob("*.parquet"))
    dated = [(clean_date(p.stem), p) for p in files if clean_date(p.stem) <= as_of]
    if dated:
        score_date, path = dated[-1]
        return score_date, pd.read_parquet(path)
    if fallback_csv.exists():
        df = pd.read_csv(fallback_csv)
        score_date = as_of
        if "history_last_date" in df.columns:
            ts = _valid_signal_date(pd.to_datetime(df["history_last_date"], errors="coerce").max())
            if ts is not None and ts <= as_of:
                score_date = ts
        return score_date, df
    raise FileNotFoundError(f"No score snapshot <= {as_of.date()} in {scores_dir}; missing fallback {fallback_csv}")


def load_holdings(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame(columns=["symbol", "shares", "entry_price_k"])
    df = pd.read_csv(path)
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(int)
    if "entry_price_k" not in df.columns:
        df["entry_price_k"] = np.nan
    df["entry_price_k"] = pd.to_numeric(df["entry_price_k"], errors="coerce")
    return df[df["shares"] > 0].copy()


def latest_price_k(history_dir: Path, symbol: str, as_of: pd.Timestamp) -> tuple[float | None, str | None]:
    path = history_dir / f"{symbol}.parquet"
    if not path.exists():
        return None, None
    df = pd.read_parquet(path)
    if df.empty or "time" not in df.columns or "close" not in df.columns:
        return None, None
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df[df["time"] <= as_of].sort_values("time")
    if df.empty:
        return None, None
    close = float(df.iloc[-1]["close"])
    if not math.isfinite(close) or close <= 0:
        return None, None
    return close, df.iloc[-1]["time"].date().isoformat()


def weekly_features(history_dir: Path, symbol: str, as_of: pd.Timestamp) -> dict:
    path = history_dir / f"{symbol}.parquet"
    if not path.exists():
        return {"weekly_ok": False, "weekly_reason": "no_history"}
    df = pd.read_parquet(path)
    if df.empty:
        return {"weekly_ok": False, "weekly_reason": "empty_history"}
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df[df["time"] <= as_of].sort_values("time").set_index("time")
    if len(df) < 60:
        return {"weekly_ok": False, "weekly_reason": "short_history"}

    weekly = df.resample("W-FRI").agg({"close": "last", "volume": "sum"}).dropna()
    if len(weekly) < 13:
        return {"weekly_ok": False, "weekly_reason": "short_weekly_history"}
    weekly["sma13"] = weekly["close"].rolling(13, min_periods=8).mean()
    delta = weekly["close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=7).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=7).mean()
    rs = gain / loss.replace(0, np.nan)
    weekly["rsi14"] = 100 - 100 / (1 + rs)
    last = weekly.iloc[-1]
    close = float(last["close"])
    sma13 = float(last["sma13"]) if pd.notna(last["sma13"]) else np.nan
    rsi14 = float(last["rsi14"]) if pd.notna(last["rsi14"]) else np.nan

    if not math.isfinite(sma13):
        return {"weekly_ok": False, "weekly_reason": "missing_sma13", "weekly_close_k": close}
    if close <= sma13:
        return {
            "weekly_ok": False,
            "weekly_reason": "below_13w_sma",
            "weekly_close_k": close,
            "weekly_sma13_k": sma13,
            "weekly_rsi14": rsi14,
        }
    if math.isfinite(rsi14) and rsi14 > 80:
        return {
            "weekly_ok": False,
            "weekly_reason": "overbought_rsi",
            "weekly_close_k": close,
            "weekly_sma13_k": sma13,
            "weekly_rsi14": rsi14,
        }
    if math.isfinite(rsi14) and rsi14 < 35:
        return {
            "weekly_ok": False,
            "weekly_reason": "oversold_rsi",
            "weekly_close_k": close,
            "weekly_sma13_k": sma13,
            "weekly_rsi14": rsi14,
        }
    return {
        "weekly_ok": True,
        "weekly_reason": "ok",
        "weekly_close_k": close,
        "weekly_sma13_k": sma13,
        "weekly_rsi14": rsi14,
    }


def compute_overlay_state(
    vni_path: Path,
    as_of: pd.Timestamp,
    trade_date: pd.Timestamp,
    lookback_weeks: int,
    lag_weeks: int,
    threshold: float,
    stock_exposure: float,
) -> OverlayState:
    vni = pd.read_parquet(vni_path)
    vni = vni.copy()
    vni["date"] = pd.to_datetime(vni["date"]).dt.tz_localize(None)
    vni = vni.sort_values("date")
    latest = vni[vni["date"] <= as_of].tail(1)
    latest_date = latest.iloc[0]["date"].date().isoformat() if not latest.empty else None
    latest_close = float(latest.iloc[0]["close"]) if not latest.empty else None

    weekly = vni.set_index("date")["close"].resample("W-MON").last().dropna()
    eligible = weekly[weekly.index <= trade_date]
    signal_pos = len(eligible) - lag_weeks - 1
    start_pos = signal_pos - lookback_weeks
    if signal_pos < 0 or start_pos < 0:
        return OverlayState(
            decision="BULL",
            trade_date=trade_date.date().isoformat(),
            signal_date=None,
            vni_close_signal=None,
            vni_ret_8w_pct=None,
            overlay_exposure=stock_exposure,
            reason="insufficient_vni_history_default_bull",
            latest_vni_date=latest_date,
            latest_vni_close=latest_close,
        )

    signal_close = float(eligible.iloc[signal_pos])
    start_close = float(eligible.iloc[start_pos])
    ret = signal_close / start_close - 1
    is_bear = ret < threshold
    return OverlayState(
        decision="BEAR" if is_bear else "BULL",
        trade_date=trade_date.date().isoformat(),
        signal_date=eligible.index[signal_pos].date().isoformat(),
        vni_close_signal=signal_close,
        vni_ret_8w_pct=ret * 100,
        overlay_exposure=0.0 if is_bear else stock_exposure,
        reason=f"VNI_8w_lag{lag_weeks}={ret*100:.2f}% threshold={threshold*100:.2f}%",
        latest_vni_date=latest_date,
        latest_vni_close=latest_close,
    )


def apply_weight_cap(raw: pd.Series, exposure: float, max_weight: float) -> pd.Series:
    if raw.empty or exposure <= 0:
        return pd.Series(dtype=float)
    weights = raw / raw.sum() * exposure
    capped = pd.Series(False, index=weights.index)
    for _ in range(len(weights) + 1):
        over = (weights > max_weight) & ~capped
        if not over.any():
            break
        capped.loc[over] = True
        weights.loc[over] = max_weight
        residual = exposure - weights.loc[capped].sum()
        uncapped = ~capped
        if residual <= 0 or not uncapped.any():
            weights.loc[uncapped] = 0.0
            break
        base = raw.loc[uncapped]
        weights.loc[uncapped] = residual * base / base.sum() if base.sum() > 0 else residual / uncapped.sum()
    return weights


def conviction_exposure(n_eligible: int, base_exposure: float, enabled: bool) -> tuple[float, str]:
    if not enabled:
        return base_exposure, "conviction_scaling_disabled"
    if base_exposure <= 0:
        return 0.0, "overlay_bear"
    if n_eligible >= 4:
        return base_exposure, "eligible_count_ge_4"
    if 2 <= n_eligible <= 3:
        return min(base_exposure, 0.65), "eligible_count_2_to_3_partial_cash"
    if n_eligible == 1:
        return min(base_exposure, 0.20), "eligible_count_1_defensive"
    return 0.0, "eligible_count_0_full_cash"


def build_candidates(
    scores: pd.DataFrame,
    history_dir: Path,
    as_of: pd.Timestamp,
    min_score: float,
) -> pd.DataFrame:
    df = scores.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["composite_score"] = pd.to_numeric(df["composite_score"], errors="coerce")
    df["base_eligible"] = (
        df["hard_gate"].eq("PASS")
        & df["status"].eq("BUY")
        & (df["composite_score"] >= min_score)
    )

    feature_rows = []
    for symbol in df["symbol"]:
        feature_rows.append({"symbol": symbol, **weekly_features(history_dir, symbol, as_of)})
    features = pd.DataFrame(feature_rows)
    out = df.merge(features, on="symbol", how="left")
    out["eligible"] = out["base_eligible"] & out["weekly_ok"].fillna(False)
    out["reject_reason"] = np.where(
        out["eligible"],
        "",
        np.where(~out["base_eligible"], "score_status_gate", out["weekly_reason"].fillna("weekly_unknown")),
    )
    return out


def build_targets(
    candidates: pd.DataFrame,
    history_dir: Path,
    as_of: pd.Timestamp,
    nav: float,
    exposure: float,
    max_holdings: int,
    max_weight: float,
    stop_loss_pct: float,
    lot_size: int,
) -> pd.DataFrame:
    selected = candidates[candidates["eligible"]].sort_values("composite_score", ascending=False).head(max_holdings).copy()
    if selected.empty or exposure <= 0:
        return pd.DataFrame()
    raw = selected.set_index("symbol")["composite_score"].astype(float)
    weights = apply_weight_cap(raw, exposure=exposure, max_weight=max_weight)
    selected["target_weight"] = selected["symbol"].map(weights).fillna(0.0)
    rows = []
    for _, row in selected.iterrows():
        price_k, price_date = latest_price_k(history_dir, row["symbol"], as_of)
        target_vnd = float(row["target_weight"]) * nav
        shares = 0
        actual_vnd = 0.0
        if price_k and price_k > 0:
            shares = int((target_vnd / (price_k * 1000)) // lot_size * lot_size)
            actual_vnd = shares * price_k * 1000
        rows.append({
            "symbol": row["symbol"],
            "sector_group": row.get("sector_group"),
            "industry_name": row.get("industry_name"),
            "composite_score": row["composite_score"],
            "quality_score": row.get("quality_score"),
            "valuation_score": row.get("valuation_score"),
            "catalyst_score": row.get("catalyst_score"),
            "technical_score": row.get("technical_score"),
            "target_weight": row["target_weight"],
            "target_vnd": target_vnd,
            "price_k": price_k,
            "price_date": price_date,
            "target_shares": shares,
            "actual_vnd": actual_vnd,
            "actual_weight": actual_vnd / nav if nav > 0 else 0.0,
            "stop_loss_k": price_k * (1 - stop_loss_pct) if price_k else None,
            "weekly_reason": row.get("weekly_reason"),
            "weekly_sma13_k": row.get("weekly_sma13_k"),
            "weekly_rsi14": row.get("weekly_rsi14"),
        })
    return pd.DataFrame(rows)


def build_orders(targets: pd.DataFrame, holdings: pd.DataFrame, history_dir: Path, as_of: pd.Timestamp) -> pd.DataFrame:
    columns = [
        "symbol",
        "side",
        "current_shares",
        "target_shares",
        "delta_shares",
        "price_k",
        "price_date",
        "gross_vnd",
        "estimated_fees_tax_vnd",
        "estimated_cash_impact_vnd",
    ]
    target_map = {}
    if not targets.empty:
        target_map = dict(zip(targets["symbol"], targets["target_shares"]))
    current_map = dict(zip(holdings["symbol"], holdings["shares"])) if not holdings.empty else {}
    symbols = sorted(set(target_map) | set(current_map))
    rows = []
    for symbol in symbols:
        current = int(current_map.get(symbol, 0))
        target = int(target_map.get(symbol, 0))
        delta = target - current
        if delta == 0:
            side = "HOLD"
        else:
            side = "BUY" if delta > 0 else "SELL"
        price_k, price_date = latest_price_k(history_dir, symbol, as_of)
        gross = abs(delta) * (price_k or 0) * 1000
        fees = 0.0
        if side == "BUY":
            fees = gross * FEE_BUY
        elif side == "SELL":
            fees = gross * (FEE_SELL + TAX_SELL)
        rows.append({
            "symbol": symbol,
            "side": side,
            "current_shares": current,
            "target_shares": target,
            "delta_shares": delta,
            "price_k": price_k,
            "price_date": price_date,
            "gross_vnd": gross,
            "estimated_fees_tax_vnd": fees,
            "estimated_cash_impact_vnd": -(gross + fees) if side == "BUY" else (gross - fees if side == "SELL" else 0.0),
        })
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["side", "symbol"], ascending=[False, True])


def format_vnd(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value:,.0f}"


def write_briefing(
    path: Path,
    as_of: pd.Timestamp,
    score_date: pd.Timestamp,
    overlay: OverlayState,
    candidates: pd.DataFrame,
    targets: pd.DataFrame,
    orders: pd.DataFrame,
    nav: float,
    exposure_reason: str,
) -> None:
    eligible = candidates[candidates["eligible"]].sort_values("composite_score", ascending=False)
    top_watch = candidates.sort_values("composite_score", ascending=False).head(20)
    deployed = float(targets["actual_vnd"].sum()) if not targets.empty else 0.0
    cash = nav - deployed
    lines = [
        f"# Weekly Live Signal Briefing — {as_of.date()}",
        "",
        f"Score snapshot: {score_date.date()}",
        f"Trade date: {overlay.trade_date}",
        f"NAV input: {format_vnd(nav)} VND",
        "",
        "## Market Overlay",
        "",
        f"Decision: **{overlay.decision}**",
        f"Signal date: {overlay.signal_date or 'n/a'}",
        f"VNI 8w lagged return: {overlay.vni_ret_8w_pct:.2f}%" if overlay.vni_ret_8w_pct is not None else "VNI 8w lagged return: n/a",
        f"Latest VNI: {overlay.latest_vni_date or 'n/a'} close {overlay.latest_vni_close or 'n/a'}",
        f"Exposure reason: {exposure_reason}",
        "",
        "## Target Portfolio",
        "",
        f"Target deployed: {format_vnd(deployed)} VND ({deployed / nav * 100:.1f}% NAV)" if nav > 0 else "Target deployed: n/a",
        f"Target cash: {format_vnd(cash)} VND ({cash / nav * 100:.1f}% NAV)" if nav > 0 else "Target cash: n/a",
        "",
    ]
    if targets.empty:
        lines.append("No stock target this week. Hold cash under current overlay/conviction filters.")
    else:
        lines.extend([
            "| Symbol | Score | Weight | Target VND | Price k | Shares | Stop k |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for _, row in targets.iterrows():
            lines.append(
                f"| {row['symbol']} | {row['composite_score']:.1f} | {row['actual_weight']*100:.1f}% | "
                f"{format_vnd(row['actual_vnd'])} | {row['price_k']:.2f} | {int(row['target_shares'])} | {row['stop_loss_k']:.2f} |"
            )
    lines.extend(["", "## Orders", ""])
    active_orders = orders[orders["side"].isin(["BUY", "SELL"])] if not orders.empty else pd.DataFrame()
    if active_orders.empty:
        lines.append("No BUY/SELL orders generated.")
    else:
        lines.extend([
            "| Side | Symbol | Delta shares | Gross VND | Est. fees/tax |",
            "|---|---|---:|---:|---:|",
        ])
        for _, row in active_orders.iterrows():
            lines.append(
                f"| {row['side']} | {row['symbol']} | {int(row['delta_shares'])} | "
                f"{format_vnd(row['gross_vnd'])} | {format_vnd(row['estimated_fees_tax_vnd'])} |"
            )
    lines.extend(["", "## Eligible Signals", ""])
    if eligible.empty:
        lines.append("No eligible BUY signal passed score, gate, and weekly trend filters.")
    else:
        lines.extend([
            "| Symbol | Score | Sector | Weekly |",
            "|---|---:|---|---|",
        ])
        for _, row in eligible.head(10).iterrows():
            lines.append(f"| {row['symbol']} | {row['composite_score']:.1f} | {row.get('sector_group', '')} | {row.get('weekly_reason', '')} |")
    lines.extend(["", "## Top Watchlist", ""])
    lines.extend([
        "| Symbol | Score | Status | Gate | Reject reason |",
        "|---|---:|---|---|---|",
    ])
    for _, row in top_watch.iterrows():
        lines.append(
            f"| {row['symbol']} | {row['composite_score']:.1f} | {row.get('status', '')} | "
            f"{row.get('hard_gate', '')} | {row.get('reject_reason', '')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    scores_dir = Path(args.scores_dir)
    history_dir = Path(args.history_dir)
    vni_path = Path(args.vni_path)
    out_root = Path(args.out_dir)

    as_of = clean_date(args.as_of) if args.as_of else default_as_of(vni_path, scores_dir)
    trade_date = next_monday(as_of)
    score_date, scores = load_score_snapshot(scores_dir, as_of)
    holdings = load_holdings(args.holdings)

    stock_exposure = max(0.0, min(1.0, 1.0 - args.cash_buffer))
    overlay = compute_overlay_state(
        vni_path=vni_path,
        as_of=as_of,
        trade_date=trade_date,
        lookback_weeks=args.overlay_lookback_weeks,
        lag_weeks=args.overlay_lag_weeks,
        threshold=args.overlay_threshold,
        stock_exposure=stock_exposure,
    )

    candidates = build_candidates(scores, history_dir, as_of, args.min_score)
    n_eligible = int(candidates["eligible"].sum())
    final_exposure, exposure_reason = conviction_exposure(
        n_eligible,
        overlay.overlay_exposure,
        enabled=not args.no_conviction_scaling,
    )
    targets = build_targets(
        candidates,
        history_dir,
        as_of,
        nav=args.nav,
        exposure=final_exposure,
        max_holdings=args.max_holdings,
        max_weight=args.max_weight,
        stop_loss_pct=args.stop_loss_pct,
        lot_size=args.lot_size,
    )
    orders = build_orders(targets, holdings, history_dir, as_of)

    out_dir = out_root / as_of.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates.sort_values("composite_score", ascending=False).head(50).to_csv(out_dir / "signals_top50.csv", index=False)
    targets.to_csv(out_dir / "target_portfolio.csv", index=False)
    orders.to_csv(out_dir / "orders.csv", index=False)
    state = {
        "as_of": as_of.date().isoformat(),
        "score_date": score_date.date().isoformat(),
        "nav_vnd": args.nav,
        "overlay": asdict(overlay),
        "eligible_count": n_eligible,
        "final_exposure": final_exposure,
        "exposure_reason": exposure_reason,
        "outputs": {
            "signals_top50": str(out_dir / "signals_top50.csv"),
            "target_portfolio": str(out_dir / "target_portfolio.csv"),
            "orders": str(out_dir / "orders.csv"),
            "briefing": str(out_dir / "briefing.md"),
        },
    }
    (out_dir / "signal_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    write_briefing(
        out_dir / "briefing.md",
        as_of=as_of,
        score_date=score_date,
        overlay=overlay,
        candidates=candidates,
        targets=targets,
        orders=orders,
        nav=args.nav,
        exposure_reason=exposure_reason,
    )

    print(json.dumps(state, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
