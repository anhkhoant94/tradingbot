"""Phase 10 — Technical rotation engine.

Goal: test a more aggressive technical approach:
- Industry money-flow rotation using `industry_name` granularity.
- Stock selection biased to relative strength, trend template, breakout proximity,
  and volume accumulation.
- Fundamental scores are used as guardrails, not as the primary alpha source.

Methods referenced in rule design:
- O'Neil/CANSLIM: relative strength, new-high leadership, volume confirmation.
- Minervini trend template: price above rising moving averages.
- Darvas-style breakout: close near 52-week high, sell on trend/rank failure.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
BACKTEST_CACHE = CACHE / "backtest"
OUT = ROOT / "output" / "backtest_v10_technical"
OUT.mkdir(parents=True, exist_ok=True)

FEE_BUY = 0.0015
FEE_SELL = 0.0015
TAX_SELL = 0.0010


def load_history_cache() -> dict:
    with open(BACKTEST_CACHE / "history_cache.pkl", "rb") as f:
        return pickle.load(f)


def load_scores(scores_dir: Path) -> dict[pd.Timestamp, pd.DataFrame]:
    out = {}
    for p in sorted(scores_dir.glob("*.parquet")):
        out[pd.Timestamp(p.stem)] = pd.read_parquet(p)
    return out


def latest_score_snapshot(date: pd.Timestamp, scores: dict[pd.Timestamp, pd.DataFrame]) -> tuple[pd.Timestamp | None, pd.DataFrame | None]:
    keys = sorted([d for d in scores if d <= date])
    if not keys:
        return None, None
    return keys[-1], scores[keys[-1]]


def compute_obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def compute_ad_line(df: pd.DataFrame) -> pd.Series:
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl
    return (mfm.fillna(0) * df["volume"]).cumsum()


def build_weekly_panel(history_cache: dict) -> dict[str, pd.DataFrame]:
    panel = {}
    for sym, df in history_cache.items():
        d = df.copy()
        d["time"] = pd.to_datetime(d["time"]).dt.tz_localize(None)
        d = d.sort_values("time").set_index("time")
        if len(d) < 120:
            continue
        d["obv"] = compute_obv(d)
        d["ad"] = compute_ad_line(d)
        d["up_vol"] = np.where(d["close"] > d["close"].shift(1), d["volume"], 0)
        d["down_vol"] = np.where(d["close"] < d["close"].shift(1), d["volume"], 0)
        w = d.resample("W-FRI").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "obv": "last",
                "ad": "last",
                "up_vol": "sum",
                "down_vol": "sum",
            }
        ).dropna(subset=["close"])
        if len(w) < 60:
            continue
        for n in [4, 8, 13, 26, 52]:
            w[f"ret{n}"] = w["close"] / w["close"].shift(n) - 1
        for n in [10, 30, 40, 52]:
            w[f"sma{n}"] = w["close"].rolling(n, min_periods=max(4, n // 2)).mean()
        w["high52"] = w["high"].rolling(52, min_periods=26).max()
        w["low52"] = w["low"].rolling(52, min_periods=26).min()
        w["vol4"] = w["volume"].rolling(4, min_periods=2).mean()
        w["vol13"] = w["volume"].rolling(13, min_periods=6).mean()
        w["obv_slope4"] = w["obv"].diff(4)
        w["ad_slope8"] = w["ad"].diff(8)
        w["up_down4"] = w["up_vol"].rolling(4, min_periods=2).sum() / w["down_vol"].rolling(4, min_periods=2).sum().replace(0, np.nan)
        w["vol_z13"] = (w["volume"] - w["volume"].rolling(13, min_periods=6).mean()) / w["volume"].rolling(13, min_periods=6).std().replace(0, np.nan)
        delta = w["close"].diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=7).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=7).mean()
        rs = gain / loss.replace(0, np.nan)
        w["rsi14"] = 100 - 100 / (1 + rs)
        w["trend_template"] = (
            (w["close"] > w["sma10"])
            & (w["sma10"] > w["sma30"])
            & (w["sma30"] > w["sma40"])
            & (w["close"] > w["sma52"])
        ).astype(int)
        w["near_high52"] = w["close"] / w["high52"]
        w["moneyflow_score"] = 0
        w.loc[w["obv_slope4"] > 0, "moneyflow_score"] += 25
        w.loc[w["ad_slope8"] > 0, "moneyflow_score"] += 25
        w.loc[w["up_down4"] > 1.1, "moneyflow_score"] += 25
        w.loc[w["vol_z13"] > 0, "moneyflow_score"] += 25
        panel[sym] = w
    return panel


def load_vni_weekly() -> pd.DataFrame:
    for path in [BACKTEST_CACHE / "vnindex_daily_v6.parquet", BACKTEST_CACHE / "vnindex_daily.parquet"]:
        if path.exists():
            df = pd.read_parquet(path).copy()
            df["date"] = pd.to_datetime(df["date"])
            w = df.set_index("date")["close"].resample("W-FRI").last().dropna().to_frame("close")
            for n in [4, 13, 26]:
                w[f"ret{n}"] = w["close"] / w["close"].shift(n) - 1
            for n in [10, 30, 40]:
                w[f"sma{n}"] = w["close"].rolling(n, min_periods=max(4, n // 2)).mean()
            return w
    return pd.DataFrame()


def feature_at(panel: dict[str, pd.DataFrame], sym: str, date: pd.Timestamp) -> pd.Series | None:
    w = panel.get(sym)
    if w is None or w.empty:
        return None
    sub = w[w.index <= date]
    if sub.empty:
        return None
    return sub.iloc[-1]


def pct_rank(s: pd.Series, high_good: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() <= 1:
        return pd.Series(50.0, index=s.index)
    r = x.rank(pct=True) * 100
    if not high_good:
        r = 100 - r
    return r.fillna(50)


def build_candidate_table(
    date: pd.Timestamp,
    active_scores: pd.DataFrame,
    weekly_panel: dict[str, pd.DataFrame],
    vni_weekly: pd.DataFrame,
    min_composite: float,
    min_liquidity: float,
    industry_top_n: int,
    min_industry_score: float,
    technical_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    vni_sub = vni_weekly[vni_weekly.index <= date] if not vni_weekly.empty else pd.DataFrame()
    vni13 = float(vni_sub["ret13"].iloc[-1]) if not vni_sub.empty and pd.notna(vni_sub["ret13"].iloc[-1]) else 0.0
    for _, row in active_scores.iterrows():
        sym = str(row.get("symbol", "")).upper()
        feat = feature_at(weekly_panel, sym, date)
        if feat is None:
            continue
        rows.append(
            {
                "symbol": sym,
                "industry_name": row.get("industry_name") or row.get("sector_group") or "unknown",
                "sector_group": row.get("sector_group") or "unknown",
                "hard_gate": row.get("hard_gate"),
                "status": row.get("status"),
                "composite_score": float(row.get("composite_score", np.nan)),
                "avg_value_20d_bil": float(row.get("avg_value_20d_bil", 0) or 0),
                "close": float(feat.get("close", np.nan)),
                "ret4": float(feat.get("ret4", np.nan)),
                "ret8": float(feat.get("ret8", np.nan)),
                "ret13": float(feat.get("ret13", np.nan)),
                "ret26": float(feat.get("ret26", np.nan)),
                "rs13": float(feat.get("ret13", np.nan)) - vni13,
                "trend_template": int(feat.get("trend_template", 0) or 0),
                "near_high52": float(feat.get("near_high52", np.nan)),
                "moneyflow_score": float(feat.get("moneyflow_score", 0) or 0),
                "rsi14": float(feat.get("rsi14", np.nan)),
                "sma10": float(feat.get("sma10", np.nan)),
                "sma30": float(feat.get("sma30", np.nan)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df, pd.DataFrame()

    industry = df.groupby("industry_name").agg(
        n=("symbol", "count"),
        median_ret4=("ret4", "median"),
        median_ret13=("ret13", "median"),
        median_ret26=("ret26", "median"),
        median_rs13=("rs13", "median"),
        breadth=("trend_template", "mean"),
        moneyflow=("moneyflow_score", "mean"),
    ).reset_index()
    industry = industry[industry["n"] >= 2].copy()
    if industry.empty:
        return df.iloc[0:0], industry
    industry["mom_rank"] = pct_rank(0.45 * industry["median_ret4"] + 0.35 * industry["median_ret13"] + 0.20 * industry["median_ret26"])
    industry["rs_rank"] = pct_rank(industry["median_rs13"])
    industry["breadth_rank"] = pct_rank(industry["breadth"])
    industry["flow_rank"] = pct_rank(industry["moneyflow"])
    industry["industry_score"] = 0.40 * industry["mom_rank"] + 0.25 * industry["rs_rank"] + 0.20 * industry["breadth_rank"] + 0.15 * industry["flow_rank"]
    industry = industry.sort_values("industry_score", ascending=False)
    eligible_industries = set(industry[industry["industry_score"] >= min_industry_score].head(industry_top_n)["industry_name"])

    df = df[df["industry_name"].isin(eligible_industries)].copy()
    if df.empty:
        return df, industry
    df = df[
        (df["hard_gate"].eq("PASS"))
        & (df["avg_value_20d_bil"] >= min_liquidity)
        & (df["composite_score"] >= min_composite)
        & (df["rsi14"].between(35, 85) | df["rsi14"].isna())
    ].copy()
    if df.empty:
        return df, industry
    df["momentum_rank"] = pct_rank(0.35 * df["ret4"] + 0.35 * df["ret13"] + 0.30 * df["ret26"])
    df["rs_rank"] = pct_rank(df["rs13"])
    df["high_rank"] = pct_rank(df["near_high52"])
    df["flow_rank"] = pct_rank(df["moneyflow_score"])
    df["fa_rank"] = pct_rank(df["composite_score"])
    df["trend_rank"] = df["trend_template"] * 100
    technical_score = (
        0.30 * df["momentum_rank"]
        + 0.25 * df["rs_rank"]
        + 0.18 * df["high_rank"]
        + 0.17 * df["flow_rank"]
        + 0.10 * df["trend_rank"]
    )
    df["v10_score"] = technical_weight * technical_score + (1 - technical_weight) * df["fa_rank"]
    df = df.sort_values("v10_score", ascending=False)
    return df, industry


def run_backtest(
    start_date: str = "2022-04-01",
    end_date: str = "2026-05-20",
    initial_capital: float = 1_000_000_000,
    min_composite: float = 55,
    min_liquidity: float = 3.0,
    industry_top_n: int = 6,
    min_industry_score: float = 45,
    technical_weight: float = 0.80,
    max_holdings: int = 5,
    max_weight: float = 0.20,
    cash_buffer: float = 0.05,
    min_entry_score: float = 65,
    exit_rank: int = 12,
    stop_pct: float = 0.15,
    trail_activate: float = 0.25,
    trail_pct: float = 0.15,
    trend_exit: str = "sma30",
    market_filter: str = "off",
    scores_subdir: str = "scores_v2",
    verbose: bool = True,
) -> dict:
    print("[v10] loading caches...", flush=True)
    history_cache = load_history_cache()
    scores = load_scores(BACKTEST_CACHE / scores_subdir)
    panel_cache = BACKTEST_CACHE / "weekly_panel_v10.pkl"
    if panel_cache.exists():
        with open(panel_cache, "rb") as f:
            weekly_panel = pickle.load(f)
    else:
        weekly_panel = build_weekly_panel(history_cache)
        with open(panel_cache, "wb") as f:
            pickle.dump(weekly_panel, f)
    vni_weekly = load_vni_weekly()
    print(f"  histories={len(history_cache)}, weekly_panel={len(weekly_panel)}, scores={len(scores)}", flush=True)

    dates = [d for d in pd.date_range(start=start_date, end=end_date, freq="W-MON") if d.weekday() < 5]
    cash = initial_capital
    weekly_yield = (1 + 0.04) ** (1 / 52) - 1
    holdings: dict[str, dict] = {}
    equity_curve = []
    trades = []
    stage_log = []

    for i, today in enumerate(dates):
        if cash > 0 and i > 0:
            cash *= 1 + weekly_yield
        active_date, active_scores = latest_score_snapshot(today, scores)
        if active_scores is None:
            equity_curve.append({"date": today, "nav": cash, "n_holdings": 0, "cash": cash})
            continue
        vni_sub = vni_weekly[vni_weekly.index <= today] if not vni_weekly.empty else pd.DataFrame()
        market_risk_off = False
        if not vni_sub.empty and market_filter != "off":
            vni_last = vni_sub.iloc[-1]
            if market_filter == "sma30":
                market_risk_off = pd.notna(vni_last.get("sma30")) and vni_last["close"] < vni_last["sma30"] and vni_last.get("ret13", 0) < 0
            elif market_filter == "sma40":
                market_risk_off = pd.notna(vni_last.get("sma40")) and vni_last["close"] < vni_last["sma40"]
            elif market_filter == "ret13":
                market_risk_off = vni_last.get("ret13", 0) < -0.05
        candidates, industry = build_candidate_table(
            today,
            active_scores,
            weekly_panel,
            vni_weekly,
            min_composite=min_composite,
            min_liquidity=min_liquidity,
            industry_top_n=industry_top_n,
            min_industry_score=min_industry_score,
            technical_weight=technical_weight,
        )
        rank_lookup = {sym: idx + 1 for idx, sym in enumerate(candidates["symbol"].tolist())}
        row_lookup = {row["symbol"]: row for _, row in candidates.iterrows()}

        # Exits.
        for sym in list(holdings):
            feat = feature_at(weekly_panel, sym, today)
            if feat is None:
                continue
            price = float(feat["close"])
            h = holdings[sym]
            h["peak"] = max(h["peak"], price)
            exit_reason = None
            if market_risk_off:
                exit_reason = "MARKET_RISK_OFF"
            elif price <= h["entry_price"] * (1 - stop_pct):
                exit_reason = "STOP"
            elif h["peak"] >= h["entry_price"] * (1 + trail_activate) and price <= h["peak"] * (1 - trail_pct):
                exit_reason = "TRAIL_PROFIT"
            elif rank_lookup.get(sym, 999) > exit_rank:
                exit_reason = "RANK_FALL"
            elif trend_exit == "sma10" and pd.notna(feat.get("sma10")) and price < feat.get("sma10"):
                exit_reason = "SMA10_BREAK"
            elif trend_exit == "sma30" and pd.notna(feat.get("sma30")) and price < feat.get("sma30"):
                exit_reason = "SMA30_BREAK"
            if exit_reason:
                gross = h["shares"] * price
                fees = gross * (FEE_SELL + TAX_SELL)
                cash += gross - fees
                trades.append(
                    {
                        "date": today,
                        "symbol": sym,
                        "side": exit_reason,
                        "shares": h["shares"],
                        "price": price,
                        "gross_vnd": gross,
                        "fees_vnd": fees,
                        "entry_price": h["entry_price"],
                        "return_pct": (price / h["entry_price"] - 1) * 100,
                        "hold_days": (today - h["entry_date"]).days,
                        "v10_score": h.get("entry_score"),
                        "industry_name": h.get("industry_name"),
                    }
                )
                del holdings[sym]

        port_val = sum(h["shares"] * float(feature_at(weekly_panel, sym, today)["close"]) for sym, h in holdings.items() if feature_at(weekly_panel, sym, today) is not None)
        nav = cash + port_val

        # Entries.
        if len(holdings) < max_holdings and not candidates.empty and not market_risk_off:
            for _, row in candidates.iterrows():
                if len(holdings) >= max_holdings:
                    break
                sym = row["symbol"]
                if sym in holdings:
                    continue
                if row["v10_score"] < min_entry_score:
                    continue
                if row["trend_template"] < 1:
                    continue
                price = float(row["close"])
                available = cash - nav * cash_buffer
                target_value = min(nav * max_weight, available * 0.95)
                if target_value <= 0:
                    break
                gross_cost = target_value / (1 + FEE_BUY)
                shares = int(gross_cost / price)
                if shares <= 0:
                    continue
                gross = shares * price
                fees = gross * FEE_BUY
                if gross + fees > cash:
                    continue
                cash -= gross + fees
                holdings[sym] = {
                    "shares": shares,
                    "entry_price": price,
                    "entry_date": today,
                    "entry_score": row["v10_score"],
                    "industry_name": row["industry_name"],
                    "peak": price,
                }
                trades.append(
                    {
                        "date": today,
                        "symbol": sym,
                        "side": "BUY",
                        "shares": shares,
                        "price": price,
                        "gross_vnd": gross,
                        "fees_vnd": fees,
                        "entry_price": price,
                        "v10_score": row["v10_score"],
                        "composite_score": row["composite_score"],
                        "industry_name": row["industry_name"],
                    }
                )

        port_val = sum(h["shares"] * float(feature_at(weekly_panel, sym, today)["close"]) for sym, h in holdings.items() if feature_at(weekly_panel, sym, today) is not None)
        nav = cash + port_val
        equity_curve.append({"date": today, "nav": nav, "n_holdings": len(holdings), "cash": cash})
        stage_log.append(
            {
                "date": today,
                "active_score_date": active_date,
                "industry_count": len(industry),
                "candidate_count": len(candidates),
                "holdings": len(holdings),
                "market_risk_off": market_risk_off,
                "top_industry": industry.iloc[0]["industry_name"] if not industry.empty else None,
                "top_industry_score": industry.iloc[0]["industry_score"] if not industry.empty else None,
            }
        )
        if verbose and (i % 30 == 0 or i == len(dates) - 1):
            print(f"  {today.date()}: NAV={nav/1e9:.3f}B holdings={len(holdings)} candidates={len(candidates)}", flush=True)

    return {
        "equity_curve": pd.DataFrame(equity_curve),
        "trades": pd.DataFrame(trades),
        "stage_log": pd.DataFrame(stage_log),
        "config": {
            "version": "v10_technical_rotation",
            "frequency": "weekly",
            "min_composite": min_composite,
            "min_liquidity": min_liquidity,
            "industry_top_n": industry_top_n,
            "min_industry_score": min_industry_score,
            "technical_weight": technical_weight,
            "max_holdings": max_holdings,
            "max_weight": max_weight,
            "min_entry_score": min_entry_score,
            "exit_rank": exit_rank,
            "stop_pct": stop_pct,
            "trail_activate": trail_activate,
            "trail_pct": trail_pct,
            "trend_exit": trend_exit,
            "market_filter": market_filter,
        },
    }


def save_result(result: dict, out_suffix: str) -> Path:
    out_dir = OUT / out_suffix
    out_dir.mkdir(parents=True, exist_ok=True)
    result["equity_curve"].to_parquet(out_dir / "equity_curve.parquet", index=False)
    result["trades"].to_parquet(out_dir / "trades.parquet", index=False)
    result["stage_log"].to_parquet(out_dir / "stage_log.parquet", index=False)
    (out_dir / "config.json").write_text(json.dumps(result["config"], ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


def print_summary(result: dict, out_dir: Path) -> None:
    eq = result["equity_curve"]
    nav = eq["nav"].astype(float)
    yrs = (pd.to_datetime(eq["date"]).iloc[-1] - pd.to_datetime(eq["date"]).iloc[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    rets = nav.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(52) if rets.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min()
    print(f"\n[v10] saved to {out_dir}")
    print(f"  Trades: {len(result['trades'])}")
    print(f"  NAV: {nav.iloc[0]/1e9:.3f}B -> {nav.iloc[-1]/1e9:.3f}B ({(nav.iloc[-1]/nav.iloc[0]-1)*100:+.2f}%)")
    print(f"  CAGR: {cagr*100:.2f}%/yr, Sharpe: {sharpe:.2f}, MaxDD: {dd*100:.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-suffix", default="default")
    parser.add_argument("--min-composite", type=float, default=55)
    parser.add_argument("--min-liquidity", type=float, default=3.0)
    parser.add_argument("--industry-top-n", type=int, default=6)
    parser.add_argument("--min-industry-score", type=float, default=45)
    parser.add_argument("--technical-weight", type=float, default=0.80)
    parser.add_argument("--max-holdings", type=int, default=5)
    parser.add_argument("--max-weight", type=float, default=0.20)
    parser.add_argument("--min-entry-score", type=float, default=65)
    parser.add_argument("--exit-rank", type=int, default=12)
    parser.add_argument("--stop-pct", type=float, default=0.15)
    parser.add_argument("--trail-activate", type=float, default=0.25)
    parser.add_argument("--trail-pct", type=float, default=0.15)
    parser.add_argument("--trend-exit", choices=["sma10", "sma30", "rank"], default="sma30")
    parser.add_argument("--market-filter", choices=["off", "sma30", "sma40", "ret13"], default="off")
    args = parser.parse_args()

    result = run_backtest(
        min_composite=args.min_composite,
        min_liquidity=args.min_liquidity,
        industry_top_n=args.industry_top_n,
        min_industry_score=args.min_industry_score,
        technical_weight=args.technical_weight,
        max_holdings=args.max_holdings,
        max_weight=args.max_weight,
        min_entry_score=args.min_entry_score,
        exit_rank=args.exit_rank,
        stop_pct=args.stop_pct,
        trail_activate=args.trail_activate,
        trail_pct=args.trail_pct,
        trend_exit=args.trend_exit,
        market_filter=args.market_filter,
    )
    out_dir = save_result(result, args.out_suffix)
    print_summary(result, out_dir)


if __name__ == "__main__":
    main()
