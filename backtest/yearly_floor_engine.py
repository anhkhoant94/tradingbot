"""Yearly-floor research engine.

Core philosophy:
- Fundamental score is a guardrail, not a yearly tweak.
- Weekly industry/stock leadership is the alpha source.
- Exposure and position size adapt to market regime, breadth, volatility, and liquidity.

The engine precomputes a weekly candidate matrix once, then sweeps coherent rules
quickly with checkpointed CSV output.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import technical_rotation_v10 as v10


ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "backtest"
OUT = ROOT / "output" / "yearly_floor_research"
OUT.mkdir(parents=True, exist_ok=True)

FEE_BUY = 0.0015
FEE_SELL = 0.0015
TAX_SELL = 0.0010


def pct_rank(s: pd.Series, high_good: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() <= 1:
        return pd.Series(50.0, index=s.index)
    r = x.rank(pct=True) * 100
    if not high_good:
        r = 100 - r
    return r.fillna(50)


def latest_score_dates(dates: list[pd.Timestamp], scores: dict[pd.Timestamp, pd.DataFrame]) -> dict[pd.Timestamp, pd.Timestamp | None]:
    keys = sorted(scores)
    out: dict[pd.Timestamp, pd.Timestamp | None] = {}
    j = -1
    for d in sorted(dates):
        while j + 1 < len(keys) and keys[j + 1] <= d:
            j += 1
        out[d] = keys[j] if j >= 0 else None
    return out


def load_vni() -> pd.DataFrame:
    v = pd.read_parquet(CACHE / "vnindex_daily.parquet").copy()
    v["date"] = pd.to_datetime(v["date"]).dt.tz_localize(None)
    v = v.sort_values("date")
    w = v.set_index("date")["close"].resample("W-FRI").last().dropna().to_frame("vni_close")
    for n in [4, 8, 13, 26, 40]:
        w[f"vni_ret{n}"] = w["vni_close"] / w["vni_close"].shift(n) - 1
    for n in [10, 30, 40]:
        w[f"vni_sma{n}"] = w["vni_close"].rolling(n, min_periods=max(4, n // 2)).mean()
    ret = w["vni_close"].pct_change()
    w["vni_vol13"] = ret.rolling(13, min_periods=6).std() * math.sqrt(52)
    return w


def build_candidate_matrix(
    start_date: str = "2021-01-01",
    end_date: str = "2026-05-20",
    scores_subdir: str = "scores_2016_v4_dynliq_rank",
    cache_name: str = "yearly_floor_candidate_matrix.parquet",
    weekly_panel_cache_name: str = "weekly_panel_v10.pkl",
    rebuild_weekly_panel: bool = False,
    force: bool = False,
) -> pd.DataFrame:
    cache_path = CACHE / cache_name
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path)

    print("[yearly-floor] building candidate matrix...", flush=True)
    history_cache = v10.load_history_cache()
    panel_cache = CACHE / weekly_panel_cache_name
    if panel_cache.exists() and not rebuild_weekly_panel:
        with open(panel_cache, "rb") as f:
            weekly_panel = pickle.load(f)
    else:
        weekly_panel = v10.build_weekly_panel(history_cache)
        with open(panel_cache, "wb") as f:
            pickle.dump(weekly_panel, f)

    scores = v10.load_scores(CACHE / scores_subdir)
    dates = [pd.Timestamp(d) for d in pd.date_range(start=start_date, end=end_date, freq="W-MON")]
    score_dates = latest_score_dates(dates, scores)
    vni_w = load_vni()

    rows = []
    for idx, d in enumerate(dates, 1):
        sd = score_dates[d]
        if sd is None:
            continue
        active = scores[sd]
        vni_sub = vni_w[vni_w.index <= d]
        vni13 = float(vni_sub["vni_ret13"].iloc[-1]) if not vni_sub.empty and pd.notna(vni_sub["vni_ret13"].iloc[-1]) else 0.0
        vni_row = vni_sub.iloc[-1].to_dict() if not vni_sub.empty else {}
        for _, r in active.iterrows():
            sym = str(r.get("symbol", "")).upper()
            feat = v10.feature_at(weekly_panel, sym, d)
            if feat is None:
                continue
            close = feat.get("close", np.nan)
            if pd.isna(close) or close <= 0:
                continue
            ret4 = float(feat.get("ret4", np.nan))
            ret8 = float(feat.get("ret8", np.nan))
            ret13 = float(feat.get("ret13", np.nan))
            ret26 = float(feat.get("ret26", np.nan))
            rows.append(
                {
                    "date": d,
                    "score_date": sd,
                    "symbol": sym,
                    "industry_name": r.get("industry_name") or r.get("sector_group") or "unknown",
                    "sector_group": r.get("sector_group") or "unknown",
                    "hard_gate": r.get("hard_gate"),
                    "status": r.get("status"),
                    "composite_score": float(r.get("composite_score", np.nan)),
                    "avg_value_20d_bil": float(r.get("avg_value_20d_bil", 0) or 0),
                    "close": float(close),
                    "ret4": ret4,
                    "ret8": ret8,
                    "ret13": ret13,
                    "ret26": ret26,
                    "ret52": float(feat.get("ret52", np.nan)),
                    "rs13": ret13 - vni13,
                    "trend_template": int(feat.get("trend_template", 0) or 0),
                    "near_high52": float(feat.get("near_high52", np.nan)),
                    "moneyflow_score": float(feat.get("moneyflow_score", 0) or 0),
                    "rsi14": float(feat.get("rsi14", np.nan)),
                    "sma10": float(feat.get("sma10", np.nan)),
                    "sma30": float(feat.get("sma30", np.nan)),
                    "sma40": float(feat.get("sma40", np.nan)),
                    "vni_close": float(vni_row.get("vni_close", np.nan)),
                    "vni_ret4": float(vni_row.get("vni_ret4", np.nan)),
                    "vni_ret13": float(vni_row.get("vni_ret13", np.nan)),
                    "vni_ret26": float(vni_row.get("vni_ret26", np.nan)),
                    "vni_sma30": float(vni_row.get("vni_sma30", np.nan)),
                    "vni_sma40": float(vni_row.get("vni_sma40", np.nan)),
                    "vni_vol13": float(vni_row.get("vni_vol13", np.nan)),
                }
            )
        if idx % 25 == 0:
            print(f"  matrix dates {idx}/{len(dates)}", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("candidate matrix is empty")

    df["date"] = pd.to_datetime(df["date"])
    for col in ["ret4", "ret8", "ret13", "ret26", "ret52", "rs13", "near_high52", "moneyflow_score", "composite_score"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    scored = []
    for d, g in df.groupby("date", sort=True):
        g = g.copy()
        ind = (
            g.groupby("industry_name")
            .agg(
                industry_n=("symbol", "count"),
                median_ret4=("ret4", "median"),
                median_ret13=("ret13", "median"),
                median_ret26=("ret26", "median"),
                median_rs13=("rs13", "median"),
                breadth=("trend_template", "mean"),
                moneyflow=("moneyflow_score", "mean"),
            )
            .reset_index()
        )
        ind = ind[ind["industry_n"] >= 2].copy()
        if ind.empty:
            continue
        ind["mom_rank"] = pct_rank(0.45 * ind["median_ret4"] + 0.35 * ind["median_ret13"] + 0.20 * ind["median_ret26"])
        ind["rs_rank"] = pct_rank(ind["median_rs13"])
        ind["breadth_rank"] = pct_rank(ind["breadth"])
        ind["flow_rank"] = pct_rank(ind["moneyflow"])
        ind["industry_score"] = 0.40 * ind["mom_rank"] + 0.25 * ind["rs_rank"] + 0.20 * ind["breadth_rank"] + 0.15 * ind["flow_rank"]
        ind["industry_rank"] = ind["industry_score"].rank(ascending=False, method="first")
        g = g.merge(ind[["industry_name", "industry_score", "industry_rank", "breadth"]], on="industry_name", how="left")
        g["fa_rank_all"] = pct_rank(g["composite_score"])
        g["mom_rank_all"] = pct_rank(0.30 * g["ret4"] + 0.30 * g["ret13"] + 0.25 * g["ret26"] + 0.15 * g["ret52"])
        g["rs_rank_all"] = pct_rank(g["rs13"])
        g["high_rank_all"] = pct_rank(g["near_high52"])
        g["flow_rank_all"] = pct_rank(g["moneyflow_score"])
        g["tech_score_base"] = (
            0.28 * g["mom_rank_all"]
            + 0.24 * g["rs_rank_all"]
            + 0.18 * g["high_rank_all"]
            + 0.16 * g["flow_rank_all"]
            + 0.14 * (g["trend_template"] * 100)
        )
        scored.append(g)
    df = pd.concat(scored, ignore_index=True)
    df.to_parquet(cache_path, index=False)
    print(f"[yearly-floor] saved matrix {cache_path} rows={len(df):,}", flush=True)
    return df


def yearly_returns(eq: pd.DataFrame, nav_col: str = "nav") -> dict[int, float]:
    e = eq.copy()
    e["date"] = pd.to_datetime(e["date"])
    out = {}
    for y, g in e.groupby(e["date"].dt.year):
        if 2021 <= int(y) <= 2026 and len(g) >= 2:
            out[int(y)] = (float(g[nav_col].iloc[-1]) / float(g[nav_col].iloc[0]) - 1) * 100
    return out


def vnindex_yearly() -> dict[int, float]:
    v = pd.read_parquet(CACHE / "vnindex_daily.parquet").copy()
    v["date"] = pd.to_datetime(v["date"])
    v = v[(v["date"] >= "2021-01-01") & (v["date"] <= "2026-05-21")].sort_values("date")
    out = {}
    for y, g in v.groupby(v["date"].dt.year):
        if len(g) >= 2:
            out[int(y)] = (float(g["close"].iloc[-1]) / float(g["close"].iloc[0]) - 1) * 100
    return out


def market_exposure(vni_row: pd.Series, breadth: float, mode: str) -> float:
    vni_close = vni_row.get("vni_close", np.nan)
    sma30 = vni_row.get("vni_sma30", np.nan)
    sma40 = vni_row.get("vni_sma40", np.nan)
    ret13 = vni_row.get("vni_ret13", 0.0)
    vol13 = vni_row.get("vni_vol13", np.nan)
    risk_off = False
    if pd.notna(vni_close) and pd.notna(sma40) and vni_close < sma40 and ret13 < -0.03:
        risk_off = True
    if mode == "full":
        base = 1.0
    elif mode == "balanced":
        base = 0.45 if risk_off else 1.0
    elif mode == "aggressive":
        base = 0.65 if risk_off else 1.0
    elif mode == "turbo":
        base = 0.80 if risk_off else 1.0
    else:
        base = 1.0
    if mode in {"balanced", "aggressive"} and pd.notna(vol13) and vol13 > 0.28:
        base *= 0.85
    if breadth < 0.20:
        base *= 0.75
    elif breadth > 0.55 and not risk_off:
        base *= 1.05
    return float(min(max(base, 0.0), 1.0))


def crisis_short_weight(vni_row: pd.Series, cfg: dict) -> float:
    """Negative index weight via VN30/VNI futures proxy during confirmed bear regimes."""
    mode = cfg.get("hedge_mode", "off")
    if mode == "off":
        return 0.0
    vni_close = vni_row.get("vni_close", np.nan)
    sma30 = vni_row.get("vni_sma30", np.nan)
    sma40 = vni_row.get("vni_sma40", np.nan)
    ret13 = vni_row.get("vni_ret13", 0.0)
    ret26 = vni_row.get("vni_ret26", 0.0)
    if pd.isna(vni_close) or pd.isna(sma40):
        return 0.0
    risk = 0.0
    if vni_close < sma40 and ret13 < -0.03:
        risk = max(risk, 0.5)
    if vni_close < sma40 and ret13 < -0.08:
        risk = max(risk, 0.8)
    if pd.notna(sma30) and vni_close < sma30 and ret26 < -0.15:
        risk = max(risk, 1.0)
    if mode == "hedge":
        return -min(risk, cfg.get("max_short_index", 0.5))
    if mode == "crisis_short":
        return -min(risk, cfg.get("max_short_index", 1.0))
    return 0.0


def run_strategy(df: pd.DataFrame, cfg: dict) -> dict:
    dates = sorted(pd.to_datetime(df["date"].unique()))
    by_date = {pd.Timestamp(d): g.copy() for d, g in df.groupby("date", sort=True)}
    cash = float(cfg.get("initial_capital", 1_000_000_000))
    initial = cash
    weekly_yield = (1 + float(cfg.get("cash_yield", 0.04))) ** (1 / 52) - 1
    holdings: dict[str, dict] = {}
    eq_rows = []
    trades = []

    for i, today in enumerate(dates):
        if i > 0 and cash > 0:
            cash *= 1 + weekly_yield
        g = by_date[today]
        row_lookup = {r["symbol"]: r for _, r in g.iterrows()}

        # Mark-to-market before exits.
        nav_before = cash
        for sym, h in holdings.items():
            r = row_lookup.get(sym)
            price = float(r["close"]) if r is not None and pd.notna(r["close"]) else h["last_price"]
            h["last_price"] = price
            h["peak"] = max(h["peak"], price)
            nav_before += h["shares"] * price

        # Candidate table for rank exits and entries.
        cand = g[
            (g["industry_score"].ge(cfg["min_industry_score"]))
            & (g["industry_rank"].le(cfg["industry_top_n"]))
            & (g["avg_value_20d_bil"].ge(cfg["min_liquidity"]))
            & (g["composite_score"].ge(cfg["min_composite"]))
            & (g["rsi14"].between(cfg["rsi_min"], cfg["rsi_max"]) | g["rsi14"].isna())
        ].copy()
        if cfg.get("require_hard_gate", True):
            cand = cand[cand["hard_gate"].eq("PASS")].copy()
        if cfg.get("require_trend_template", True):
            cand = cand[cand["trend_template"].ge(1)].copy()
        if cfg.get("require_positive_rs", False):
            cand = cand[cand["rs13"].gt(0)].copy()
        if cand.empty:
            cand = cand.iloc[0:0].copy()
        else:
            cand["fa_rank"] = pct_rank(cand["composite_score"])
            cand["score"] = cfg["technical_weight"] * cand["tech_score_base"] + (1 - cfg["technical_weight"]) * cand["fa_rank"]
            cand = cand.sort_values(["score", "industry_score"], ascending=False)
        rank_lookup = {sym: rank + 1 for rank, sym in enumerate(cand["symbol"].tolist())}

        breadth = float((g["trend_template"] == 1).mean()) if len(g) else 0.0
        exposure = market_exposure(g.iloc[0], breadth, cfg["exposure_mode"])
        if len(cand) < cfg["min_candidate_count"]:
            exposure *= cfg["thin_market_exposure"]

        # Exits.
        for sym in list(holdings):
            h = holdings[sym]
            r = row_lookup.get(sym)
            if r is None:
                continue
            price = float(r["close"])
            h["last_price"] = price
            h["peak"] = max(h["peak"], price)
            exit_reason = None
            if price <= h["entry_price"] * (1 - cfg["stop_pct"]):
                exit_reason = "STOP"
            elif h["peak"] >= h["entry_price"] * (1 + cfg["trail_activate"]) and price <= h["peak"] * (1 - cfg["trail_pct"]):
                exit_reason = "TRAIL"
            elif rank_lookup.get(sym, 9999) > cfg["exit_rank"]:
                exit_reason = "RANK_FALL"
            elif cfg["trend_exit"] == "sma10" and pd.notna(r["sma10"]) and price < r["sma10"]:
                exit_reason = "SMA10"
            elif cfg["trend_exit"] == "sma30" and pd.notna(r["sma30"]) and price < r["sma30"]:
                exit_reason = "SMA30"
            elif exposure <= cfg["riskoff_exit_exposure"]:
                exit_reason = "RISK_OFF"
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
                        "industry_name": h["industry_name"],
                    }
                )
                del holdings[sym]

        nav = cash + sum(h["shares"] * h["last_price"] for h in holdings.values())
        target_equity = nav * exposure
        current_equity = sum(h["shares"] * h["last_price"] for h in holdings.values())

        # Trim if equity above regime exposure.
        if current_equity > target_equity * 1.08 and current_equity > 0:
            trim_ratio = max(0.0, 1 - target_equity / current_equity)
            for sym in list(holdings):
                if trim_ratio <= 0:
                    break
                h = holdings[sym]
                shares = int(h["shares"] * trim_ratio)
                if shares <= 0:
                    continue
                price = h["last_price"]
                gross = shares * price
                fees = gross * (FEE_SELL + TAX_SELL)
                cash += gross - fees
                h["shares"] -= shares
                trades.append({"date": today, "symbol": sym, "side": "EXPOSURE_TRIM", "shares": shares, "price": price, "gross_vnd": gross, "fees_vnd": fees})
                if h["shares"] <= 0:
                    del holdings[sym]

        nav = cash + sum(h["shares"] * h["last_price"] for h in holdings.values())
        target_slot = nav * exposure / max(cfg["max_holdings"], 1)
        for _, r in cand.iterrows():
            if len(holdings) >= cfg["max_holdings"]:
                break
            if r["score"] < cfg["min_entry_score"]:
                break
            sym = r["symbol"]
            if sym in holdings:
                continue
            price = float(r["close"])
            if price <= 0 or pd.isna(price):
                continue
            score_scale = 0.75 + 0.50 * min(max((float(r["score"]) - cfg["min_entry_score"]) / 35, 0), 1)
            vol_proxy = abs(float(r.get("ret4", 0) or 0))
            vol_scale = 0.85 if vol_proxy > 0.22 else (1.10 if vol_proxy < 0.08 else 1.0)
            liquidity_cap = float(r["avg_value_20d_bil"]) * 1e9 * cfg["liquidity_participation"]
            hard_cap = nav * cfg["max_weight"]
            target_value = min(target_slot * score_scale * vol_scale, hard_cap, liquidity_cap)
            target_value = max(0.0, target_value)
            available = cash - nav * cfg["cash_buffer"]
            target_value = min(target_value, available * 0.98)
            if target_value <= nav * cfg["min_trade_weight"]:
                continue
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
                "last_price": price,
                "peak": price,
                "industry_name": r["industry_name"],
                "entry_score": float(r["score"]),
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
                    "score": float(r["score"]),
                    "composite_score": float(r["composite_score"]),
                    "industry_name": r["industry_name"],
                }
            )

        nav = cash + sum(h["shares"] * h["last_price"] for h in holdings.values())
        eq_rows.append(
            {
                "date": today,
                "nav": nav,
                "cash": cash,
                "n_holdings": len(holdings),
                "exposure": exposure,
                "candidate_count": int(len(cand)),
                "market_breadth": breadth,
            }
        )

    eq = pd.DataFrame(eq_rows)
    trades_df = pd.DataFrame(trades)
    yrs = (pd.to_datetime(eq["date"]).iloc[-1] - pd.to_datetime(eq["date"]).iloc[0]).days / 365.25
    nav = eq["nav"].astype(float)
    rets = nav.pct_change().dropna()
    cagr = (nav.iloc[-1] / initial) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sharpe = rets.mean() / rets.std() * math.sqrt(52) if rets.std() > 0 else 0
    maxdd = (nav / nav.cummax() - 1).min()
    yr = yearly_returns(eq)
    vni = vnindex_yearly()
    pass30 = sum(yr.get(y, -999) >= 30 for y in range(2021, 2027))
    beat = sum(yr.get(y, -999) > vni.get(y, 999) for y in range(2021, 2027))
    min_year = min([yr.get(y, np.nan) for y in range(2021, 2027)])
    return {
        "equity_curve": eq,
        "trades": trades_df,
        "metrics": {
            "cagr": cagr * 100,
            "sharpe": sharpe,
            "maxdd": maxdd * 100,
            "pass30": pass30,
            "beat_vni": beat,
            "min_year": min_year,
            **{f"y{y}": yr.get(y, np.nan) for y in range(2021, 2027)},
            **{f"vni{y}": vni.get(y, np.nan) for y in range(2021, 2027)},
        },
    }


def _normalize_weights(cand: pd.DataFrame, nav: float, cfg: dict, exposure: float) -> dict[str, float]:
    if cand.empty or exposure <= 0:
        return {}
    x = cand.head(int(cfg["max_holdings"])).copy()
    raw = (x["score"].clip(lower=0) ** cfg.get("score_power", 1.0)).astype(float)
    raw = raw / raw.sum() if raw.sum() > 0 else pd.Series(1 / len(x), index=x.index)
    weights = raw * exposure
    capped = {}
    for idx, r in x.iterrows():
        liq_cap = float(r["avg_value_20d_bil"]) * 1e9 * cfg["liquidity_participation"] / max(nav, 1)
        cap = min(float(cfg["max_weight"]), max(liq_cap, 0.0))
        capped[str(r["symbol"])] = min(float(weights.loc[idx]), cap)
    total = sum(capped.values())
    if total <= 0:
        return {}
    # Re-distribute unused exposure until caps bind.
    for _ in range(4):
        room = {
            s: min(
                float(cfg["max_weight"]),
                float(x[x["symbol"].eq(s)]["avg_value_20d_bil"].iloc[0]) * 1e9 * cfg["liquidity_participation"] / max(nav, 1),
            ) - w
            for s, w in capped.items()
        }
        unused = exposure - sum(capped.values())
        if unused <= 1e-8:
            break
        open_room = {s: r for s, r in room.items() if r > 1e-8}
        if not open_room:
            break
        add_unit = unused / len(open_room)
        for s, room_left in open_room.items():
            capped[s] += min(add_unit, room_left)
    return {s: w for s, w in capped.items() if w > 1e-6}


def run_rotation_fast(df: pd.DataFrame, cfg: dict) -> dict:
    """Fast weekly rebalance model.

    This is a cleaner expression of the long-term strategy: every week own the
    strongest liquid leadership names that pass fundamental guardrails, with
    exposure and sizing controlled by regime/liquidity. It charges turnover.
    """
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values(["symbol", "date"])
    work["next_close"] = work.groupby("symbol")["close"].shift(-1)
    work["next_ret"] = work["next_close"] / work["close"] - 1
    by_date = {pd.Timestamp(d): g.copy() for d, g in work.groupby("date", sort=True)}
    dates = sorted(by_date)
    vni_close_by_date = {d: float(by_date[d]["vni_close"].iloc[0]) for d in dates}
    vni_next_ret = {}
    for idx, d in enumerate(dates):
        if idx + 1 < len(dates):
            cur = vni_close_by_date.get(d, np.nan)
            nxt = vni_close_by_date.get(dates[idx + 1], np.nan)
            vni_next_ret[d] = 0.0 if pd.isna(cur) or pd.isna(nxt) or cur <= 0 else nxt / cur - 1
        else:
            vni_next_ret[d] = 0.0
    nav = float(cfg.get("initial_capital", 1_000_000_000))
    weekly_yield = (1 + float(cfg.get("cash_yield", 0.04))) ** (1 / 52) - 1
    prev_w: dict[str, float] = {}
    eq_rows = []
    holding_rows = []
    year_start_nav = nav
    current_year = None

    for today in dates:
        if current_year != today.year:
            current_year = today.year
            year_start_nav = nav
        g = by_date[today]
        breadth = float((g["trend_template"] == 1).mean()) if len(g) else 0.0
        exposure = market_exposure(g.iloc[0], breadth, cfg["exposure_mode"])
        cand = g[
            (g["hard_gate"].eq("PASS"))
            & (g["industry_score"].ge(cfg["min_industry_score"]))
            & (g["industry_rank"].le(cfg["industry_top_n"]))
            & (g["avg_value_20d_bil"].ge(cfg["min_liquidity"]))
            & (g["composite_score"].ge(cfg["min_composite"]))
            & (g["rsi14"].between(cfg["rsi_min"], cfg["rsi_max"]) | g["rsi14"].isna())
        ].copy()
        if cfg.get("require_trend_template", True):
            cand = cand[cand["trend_template"].ge(1)].copy()
        if cfg.get("require_positive_rs", False):
            cand = cand[cand["rs13"].gt(0)].copy()
        if len(cand) < cfg["min_candidate_count"]:
            exposure *= cfg["thin_market_exposure"]
        if cfg.get("use_annual_pacing", False):
            ytd = nav / max(year_start_nav, 1e-9) - 1
            target_ytd = (1 + cfg.get("annual_target", 0.30)) ** (max(1, int(pd.Timestamp(today).weekofyear)) / 52) - 1
            gap = target_ytd - ytd
            lev = cfg.get("base_gross", 1.0)
            if gap > cfg.get("pace_gap", 0.03) and len(cand) >= cfg["min_candidate_count"]:
                lev += cfg.get("pace_up", 0.25) + gap * cfg.get("pace_slope", 1.0)
            elif ytd > target_ytd + cfg.get("de_risk_gap", 0.10):
                lev *= cfg.get("de_risk_mult", 0.75)
            if breadth < cfg.get("pace_min_breadth", 0.12):
                lev *= cfg.get("weak_breadth_mult", 0.75)
            exposure *= min(max(lev, cfg.get("min_gross", 0.0)), cfg.get("max_gross", 1.0))
        if cfg.get("dd_brake", 0) < 0 and eq_rows:
            running_peak = max(float(r["nav"]) for r in eq_rows)
            current_dd = nav / running_peak - 1 if running_peak > 0 else 0.0
            if current_dd <= cfg["dd_brake"]:
                exposure *= cfg.get("dd_brake_mult", 0.70)
                exposure = max(exposure, cfg.get("dd_brake_floor", 0.0))
        index_w = crisis_short_weight(g.iloc[0], cfg)
        if index_w < 0:
            exposure *= cfg.get("equity_scale_when_short", 0.35)
        if cand.empty:
            target_w = {}
        else:
            cand["fa_rank"] = pct_rank(cand["composite_score"])
            cand["score"] = cfg["technical_weight"] * cand["tech_score_base"] + (1 - cfg["technical_weight"]) * cand["fa_rank"]
            cand = cand[cand["score"].ge(cfg["min_entry_score"])].sort_values(["score", "industry_score"], ascending=False)
            target_w = _normalize_weights(cand, nav, cfg, exposure)

        prev_index_w = prev_w.get("__INDEX__", 0.0)
        prev_stock_w = {k: v for k, v in prev_w.items() if k != "__INDEX__"}
        symbols = set(prev_stock_w) | set(target_w)
        buy_turnover = sum(max(target_w.get(s, 0.0) - prev_w.get(s, 0.0), 0.0) for s in symbols)
        sell_turnover = sum(max(prev_w.get(s, 0.0) - target_w.get(s, 0.0), 0.0) for s in symbols)
        index_turnover = abs(index_w - prev_index_w)
        cost = buy_turnover * FEE_BUY + sell_turnover * (FEE_SELL + TAX_SELL) + index_turnover * cfg.get("index_cost", 0.0008)

        ret_lookup = dict(zip(g["symbol"], g["next_ret"]))
        stock_ret = 0.0
        for s, w in target_w.items():
            r = ret_lookup.get(s, np.nan)
            stock_ret += w * (0.0 if pd.isna(r) else float(r))
        index_ret = index_w * vni_next_ret[today]
        gross_abs = sum(abs(w) for w in target_w.values()) + abs(index_w)
        cash_w = max(0.0, 1.0 - sum(target_w.values()) - max(index_w, 0.0))
        borrow_drag = max(0.0, gross_abs - 1.0) * cfg.get("margin_borrow_weekly", 0.0)
        port_ret = stock_ret + index_ret + cash_w * weekly_yield - cost - borrow_drag
        nav *= 1 + port_ret
        eq_rows.append(
            {
                "date": today,
                "nav": nav,
                "ret": port_ret,
                "exposure": sum(target_w.values()),
                "index_weight": index_w,
                "candidate_count": int(len(cand)),
                "turnover": buy_turnover + sell_turnover,
                "cost": cost,
                "market_breadth": breadth,
                "year_start_nav": year_start_nav,
            }
        )
        for s, w in target_w.items():
            holding_rows.append({"date": today, "symbol": s, "weight": w})
        prev_w = {**target_w, "__INDEX__": index_w}

    eq = pd.DataFrame(eq_rows)
    holdings = pd.DataFrame(holding_rows)
    initial = float(cfg.get("initial_capital", 1_000_000_000))
    yrs = (pd.to_datetime(eq["date"]).iloc[-1] - pd.to_datetime(eq["date"]).iloc[0]).days / 365.25
    nav_s = eq["nav"].astype(float)
    rets = eq["ret"].astype(float).dropna()
    cagr = (nav_s.iloc[-1] / initial) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sharpe = rets.mean() / rets.std() * math.sqrt(52) if rets.std() > 0 else 0
    maxdd = (nav_s / nav_s.cummax() - 1).min()
    yr = yearly_returns(eq)
    vni = vnindex_yearly()
    pass30 = sum(yr.get(y, -999) >= 30 for y in range(2021, 2027))
    beat = sum(yr.get(y, -999) > vni.get(y, 999) for y in range(2021, 2027))
    min_year = min([yr.get(y, np.nan) for y in range(2021, 2027)])
    return {
        "equity_curve": eq,
        "holdings": holdings,
        "metrics": {
            "cagr": cagr * 100,
            "sharpe": sharpe,
            "maxdd": maxdd * 100,
            "pass30": pass30,
            "beat_vni": beat,
            "min_year": min_year,
            **{f"y{y}": yr.get(y, np.nan) for y in range(2021, 2027)},
            **{f"vni{y}": vni.get(y, np.nan) for y in range(2021, 2027)},
        },
    }


def base_configs() -> list[dict]:
    common = {
        "initial_capital": 1_000_000_000,
        "cash_yield": 0.04,
        "cash_buffer": 0.02,
        "min_trade_weight": 0.01,
        "rsi_min": 35,
        "rsi_max": 88,
        "require_trend_template": True,
        "require_positive_rs": False,
        "require_hard_gate": True,
        "thin_market_exposure": 0.65,
        "riskoff_exit_exposure": 0.35,
        "score_power": 1.4,
        "hedge_mode": "off",
        "max_short_index": 0.0,
        "equity_scale_when_short": 0.35,
        "index_cost": 0.0008,
        "margin_borrow_weekly": 0.0,
    }
    grid = []
    for vals in itertools.product(
        [42, 48, 55],
        [0.5, 1.0, 2.0],
        [4, 6, 8],
        [30, 40, 50],
        [0.82, 0.92, 1.0],
        [3, 4, 5],
        [0.25, 0.30, 0.35],
        [55, 60, 65],
        [6, 9, 12],
        [0.10, 0.14, 0.18],
        [0.18, 0.28, 0.40],
        [0.10, 0.14, 0.20],
        ["sma10", "sma30", "rank"],
        ["full", "aggressive", "turbo"],
        [3, 6],
        [0.05, 0.10, 0.20],
    ):
        (
            min_comp,
            min_liq,
            topn,
            ind_score,
            tech_weight,
            maxh,
            maxw,
            entry,
            exit_rank,
            stop,
            trail_act,
            trail,
            trend_exit,
            exposure_mode,
            min_cand,
            liq_part,
        ) = vals
        grid.append(
            {
                **common,
                "min_composite": min_comp,
                "min_liquidity": min_liq,
                "industry_top_n": topn,
                "min_industry_score": ind_score,
                "technical_weight": tech_weight,
                "max_holdings": maxh,
                "max_weight": maxw,
                "min_entry_score": entry,
                "exit_rank": exit_rank,
                "stop_pct": stop,
                "trail_activate": trail_act,
                "trail_pct": trail,
                "trend_exit": trend_exit,
                "exposure_mode": exposure_mode,
                "min_candidate_count": min_cand,
                "liquidity_participation": liq_part,
            }
        )
    # Deterministic sparse sample plus focused high-conviction configs.
    step = max(1, len(grid) // 180)
    sample = grid[::step][:180]
    focused = []
    for exposure in ["full", "turbo", "aggressive"]:
        for trend_exit in ["sma10", "sma30", "rank"]:
            for maxh, maxw in [(3, 0.35), (4, 0.30), (5, 0.25)]:
                for require_hard_gate, min_comp, min_liq, entry, rsi_max in [
                    (True, 42, 0.5, 55, 88),
                    (False, 15, 1.0, 68, 92),
                    (False, 25, 3.0, 65, 92),
                    (False, 35, 1.0, 62, 90),
                ]:
                    focused.append(
                        {
                            **common,
                            "require_hard_gate": require_hard_gate,
                            "rsi_max": rsi_max,
                            "min_composite": min_comp,
                            "min_liquidity": min_liq,
                            "industry_top_n": 8,
                            "min_industry_score": 30,
                            "technical_weight": 1.0,
                            "max_holdings": maxh,
                            "max_weight": maxw,
                            "min_entry_score": entry,
                            "exit_rank": 6 if maxh <= 3 else 9,
                            "stop_pct": 0.10,
                            "trail_activate": 0.18,
                            "trail_pct": 0.10,
                            "trend_exit": trend_exit,
                            "exposure_mode": exposure,
                            "min_candidate_count": 3,
                            "liquidity_participation": 0.20,
                        }
                    )
    for max_short in [0.5, 0.8, 1.0, 1.2]:
        for exposure in ["balanced", "aggressive", "turbo"]:
            for maxh, maxw in [(3, 0.35), (4, 0.30), (5, 0.25)]:
                for require_hard_gate, min_comp, entry in [(True, 42, 55), (False, 25, 65), (False, 35, 62)]:
                    focused.append(
                        {
                            **common,
                            "require_hard_gate": require_hard_gate,
                            "min_composite": min_comp,
                            "min_liquidity": 1.0,
                            "industry_top_n": 8,
                            "min_industry_score": 30,
                            "technical_weight": 1.0,
                            "max_holdings": maxh,
                            "max_weight": maxw,
                            "min_entry_score": entry,
                            "exit_rank": 6 if maxh <= 3 else 9,
                            "stop_pct": 0.10,
                            "trail_activate": 0.18,
                            "trail_pct": 0.10,
                            "trend_exit": "sma10",
                            "exposure_mode": exposure,
                            "min_candidate_count": 3,
                            "liquidity_participation": 0.20,
                            "hedge_mode": "crisis_short",
                            "max_short_index": max_short,
                            "equity_scale_when_short": 0.25,
                        }
                    )
    return sample + focused


def sweep(limit: int | None = None, force_matrix: bool = False) -> pd.DataFrame:
    df = build_candidate_matrix(force=force_matrix)
    configs = base_configs()
    if limit is not None:
        configs = configs[:limit]
    out_csv = OUT / "yearly_floor_sweep_checkpoint.csv"
    rows = []
    best_score = -1e9
    best_payload = None
    start = time.time()
    for i, cfg in enumerate(configs, 1):
        result = run_rotation_fast(df, cfg)
        row = {"run_id": i, **cfg, **result["metrics"]}
        rows.append(row)
        score = row["pass30"] * 10000 + row["beat_vni"] * 1000 + row["min_year"] + 0.05 * row["cagr"] - 0.02 * abs(row["maxdd"])
        if score > best_score:
            best_score = score
            best_payload = (cfg, result, row)
            print(
                f"BEST {i}/{len(configs)} pass30={row['pass30']} beat={row['beat_vni']} "
                f"min={row['min_year']:.1f} cagr={row['cagr']:.1f} "
                f"years={[round(row[f'y{y}'],1) for y in range(2021,2027)]}",
                flush=True,
            )
        if i % 5 == 0:
            pd.DataFrame(rows).to_csv(out_csv, index=False)
            elapsed = time.time() - start
            print(f"  checkpoint {i}/{len(configs)} elapsed={elapsed:.1f}s", flush=True)
        if all(row.get(f"y{y}", -999) >= 30 and row.get(f"y{y}", -999) > row.get(f"vni{y}", 999) for y in range(2021, 2027)):
            print(f"TARGET HIT at run {i}", flush=True)
            break
    res = pd.DataFrame(rows).sort_values(["pass30", "beat_vni", "min_year", "cagr"], ascending=[False, False, False, False])
    res.to_csv(out_csv, index=False)
    if best_payload is not None:
        cfg, result, row = best_payload
        best_dir = OUT / "best_yearly_floor"
        best_dir.mkdir(parents=True, exist_ok=True)
        result["equity_curve"].to_parquet(best_dir / "equity_curve.parquet", index=False)
        result.get("trades", pd.DataFrame()).to_parquet(best_dir / "trades.parquet", index=False)
        result.get("holdings", pd.DataFrame()).to_parquet(best_dir / "holdings.parquet", index=False)
        (best_dir / "config.json").write_text(json.dumps({"config": cfg, "metrics": row}, indent=2, default=str), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-matrix", action="store_true")
    ap.add_argument("--force-matrix", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.build_matrix:
        df = build_candidate_matrix(force=args.force_matrix)
        print(df.shape)
        return
    if args.sweep:
        res = sweep(limit=args.limit, force_matrix=args.force_matrix)
        show_cols = ["run_id", "pass30", "beat_vni", "min_year", "cagr", "sharpe", "maxdd"] + [f"y{y}" for y in range(2021, 2027)]
        print(res.head(20)[show_cols].to_string(index=False))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
