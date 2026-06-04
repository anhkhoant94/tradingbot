"""Probe: long-window M core plus a small concentrated convex sleeve.

Rationale: after the user clarified that repeatable big winners are acceptable,
test a long-window-native core (M_bb35) with a small version of the
post-shock steady-trend sleeve. This is a tiny smoke, not a grid.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backtest") not in sys.path:
    sys.path.insert(0, str(ROOT / "backtest"))

os.environ.setdefault("HISTORY_CACHE_DIR", "history_2012")

import pass30_direct_search as ds  # noqa: E402
from baseline_liquid_leadership_overlay_20260527 import simulate_strict_100lot  # noqa: E402
from baseline_steady_trend_overlay_20260527 import active_dates, build_overlay_holdings  # noqa: E402
from beat_vni30_daily_execution_sim import align_history_to_calendar, load_daily_history, load_vni_daily  # noqa: E402

OUT = ROOT / "output" / "beat_vni30_parallel" / "m_core_convex_sleeve_probe_20260527"
CACHE = ROOT / ".cache" / "backtest"
M_HOLDINGS = ROOT / "output" / "beat_vni30_parallel" / "pair657_m_turnover_controls_20260527" / "best_15bps_holdings.parquet"
REGIME_PATH = CACHE / "regime_features_weekly.parquet"
YEARS = range(2016, 2027)


def load_full_matrix() -> pd.DataFrame:
    early = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_2016_2021_fullpanel.parquet").copy()
    late = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_live_preview.parquet").copy()
    early["date"] = pd.to_datetime(early["date"])
    late["date"] = pd.to_datetime(late["date"])
    early = early[early["date"] < pd.Timestamp("2021-01-01")]
    late = late[(late["date"] >= pd.Timestamp("2021-01-01")) & (late["date"] <= pd.Timestamp("2026-05-25"))]
    common = [c for c in early.columns if c in late.columns]
    df = pd.concat([early[common], late[common]], ignore_index=True).sort_values(["symbol", "date"])
    for col in ds.FEATURES + ds.EXTRA_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan
    return df.reset_index(drop=True)


def overlay_params(alpha: float, top_n: int = 1) -> dict:
    return {
        "mode": "post_shock_recovery",
        "score_mode": "steady_quality",
        "top_n": top_n,
        "liq_min": 120.0,
        "ret13_min": -0.05,
        "ret13_max": 0.80,
        "rs13_min": 0.03,
        "near_min": 0.85,
        "rsi_min": 30.0,
        "rsi_max": 75.0,
        "trend_min": 0.0,
        "moneyflow_min": 25.0,
        "breadth_min": 0.15,
        "breadth_max": 0.45,
        "dispersion_max": 0.16,
        "vni_ret13_min": 0.03,
        "vn30_breadth": 0.40,
        "mega_breadth": 0.50,
        "stop": 0.05,
        "alpha": alpha,
        "gap": 0.05,
        "buffer": 0.015,
        "pullback": 4,
        "min_sell": 4,
    }


def blend_m_core(base: pd.DataFrame, overlay: pd.DataFrame, active: dict[pd.Timestamp, bool], alpha: float, cap: float) -> pd.DataFrame:
    frames = []
    bg = {pd.Timestamp(k): v.copy() for k, v in base.groupby("date", sort=False)}
    og = {pd.Timestamp(k): v.copy() for k, v in overlay.groupby("date", sort=False)} if not overlay.empty else {}
    for dt in sorted(bg):
        b = bg[dt][["date", "symbol", "weight"]].copy()
        o = og.get(dt)
        if active.get(dt, False) and o is not None and not o.empty:
            b["weight"] = b["weight"].astype(float) * (1.0 - alpha)
            o = o[["date", "symbol", "weight"]].copy()
            o["weight"] = o["weight"].astype(float) * alpha
            frames.extend([b, o])
        else:
            frames.append(b)
    out = pd.concat(frames, ignore_index=True)
    out = out.groupby(["date", "symbol"], as_index=False)["weight"].sum()
    out["weight"] = out["weight"].clip(upper=cap)
    gross = out.groupby("date")["weight"].transform("sum")
    out["weight"] = np.where(gross > 1.0, out["weight"] / gross, out["weight"])
    return out[out["weight"] > 1e-8].sort_values(["date", "weight", "symbol"], ascending=[True, False, True])


def full_yearly(eq: pd.DataFrame, vni: pd.DataFrame) -> pd.DataFrame:
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    vni = vni.copy()
    vni["date"] = pd.to_datetime(vni["date"])
    rows = []
    for year in YEARS:
        g = eq[eq["date"].dt.year.eq(year)].sort_values("date")
        if g.empty:
            continue
        prev = eq[eq["date"] < pd.Timestamp(f"{year}-01-01")]
        base_nav = float(prev["nav"].iloc[-1]) if not prev.empty else float(g["nav"].iloc[0])
        strat = (float(g["nav"].iloc[-1]) / base_nav - 1.0) * 100.0 if base_nav > 0 else np.nan
        end_date = pd.Timestamp(g["date"].iloc[-1])
        prev_vni = vni[vni["date"] < pd.Timestamp(f"{year}-01-01")]
        vg = vni[(vni["date"].dt.year.eq(year)) & (vni["date"] <= end_date)].sort_values("date")
        if vg.empty:
            continue
        base_close = float(prev_vni["close"].iloc[-1]) if not prev_vni.empty else float(vg["close"].iloc[0])
        vni_ret = (float(vg["close"].iloc[-1]) / base_close - 1.0) * 100.0 if base_close > 0 else np.nan
        edge = strat - vni_ret
        rows.append({"year": year, "strategy_return_pct": strat, "vni_return_pct": vni_ret, "edge_vs_vni_pp": edge, "pass_vni20": edge >= 20.0, "pass_vni30": edge >= 30.0})
    return pd.DataFrame(rows)


def metrics(eq: pd.DataFrame, yearly: pd.DataFrame) -> dict:
    nav = pd.to_numeric(eq["nav"], errors="coerce")
    ret = nav.pct_change().fillna(0.0)
    yrs = (pd.Timestamp(eq["date"].iloc[-1]) - pd.Timestamp(eq["date"].iloc[0])).days / 365.25
    return {
        "cagr_pct": float((nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1.0) * 100.0,
        "maxdd_pct": float((nav / nav.cummax() - 1.0).min() * 100.0),
        "sharpe": float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0,
        "pass_vni20_all": int(yearly["pass_vni20"].sum()),
        "pass_vni30_all": int(yearly["pass_vni30"].sum()),
        "min_edge_all": float(yearly["edge_vs_vni_pp"].min()),
        "pass_vni20_2021_2026": int(yearly[yearly["year"].between(2021, 2026)]["pass_vni20"].sum()),
        "pass_vni30_2021_2026": int(yearly[yearly["year"].between(2021, 2026)]["pass_vni30"].sum()),
        "min_edge_2021_2026": float(yearly[yearly["year"].between(2021, 2026)]["edge_vs_vni_pp"].min()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = pd.read_parquet(M_HOLDINGS)
    base["date"] = pd.to_datetime(base["date"])
    base["symbol"] = base["symbol"].astype(str)
    matrix = load_full_matrix()
    regime = pd.read_parquet(REGIME_PATH)
    regime["date"] = pd.to_datetime(regime["date"])
    vni = load_vni_daily()
    rows = []
    best_payload = None
    for alpha in [0.0, 0.05, 0.10, 0.15, 0.20]:
        for top_n in ([1] if alpha == 0 else [1, 2]):
            p = overlay_params(alpha, top_n)
            active = active_dates(regime, p["mode"], p)
            overlay = build_overlay_holdings(matrix, active, p) if alpha > 0 else pd.DataFrame(columns=["date", "symbol", "weight"])
            holdings = blend_m_core(base, overlay, active, alpha, cap=0.55)
            signal_dates = sorted(pd.Timestamp(x) for x in holdings["date"].dropna().unique())
            symbols = sorted(holdings["symbol"].astype(str).unique())
            hist = load_daily_history(symbols)
            daily_dates = [
                pd.Timestamp(x)
                for x in vni[(vni["date"] >= signal_dates[0]) & (vni["date"] <= signal_dates[-1])]["date"].tolist()
            ]
            hist = align_history_to_calendar(hist, daily_dates)
            execution = {k: p[k] for k in ["gap", "buffer", "pullback", "min_sell", "stop"]}
            eq, trades, _m = simulate_strict_100lot(
                holdings,
                hist,
                vni,
                signal_dates,
                execution,
                buy_cost=0.0035,
                sell_cost=0.0045,
            )
            yearly = full_yearly(eq, vni)
            row = {
                "case": f"m_alpha{alpha:.2f}_top{top_n}",
                "alpha": alpha,
                "top_n": top_n,
                "overlay_weeks": int(overlay["date"].nunique()) if not overlay.empty else 0,
                "overlay_symbols": int(overlay["symbol"].nunique()) if not overlay.empty else 0,
                "trade_count": int(len(trades[trades["side"].isin(["BUY", "SELL"])])) if not trades.empty else 0,
                "avg_exposure": float(eq["exposure"].mean()) if "exposure" in eq else np.nan,
                **metrics(eq, yearly),
            }
            rows.append(row)
            sub = OUT / row["case"]
            sub.mkdir(parents=True, exist_ok=True)
            yearly.to_csv(sub / "yearly.csv", index=False)
            eq.to_parquet(sub / "equity.parquet", index=False)
            trades.to_csv(sub / "trades.csv", index=False)
            holdings.to_parquet(sub / "holdings.parquet", index=False)
            if best_payload is None or (row["pass_vni20_all"], row["min_edge_all"], row["cagr_pct"]) > (
                best_payload[0]["pass_vni20_all"],
                best_payload[0]["min_edge_all"],
                best_payload[0]["cagr_pct"],
            ):
                best_payload = (row, yearly)
    res = pd.DataFrame(rows).sort_values(["pass_vni20_all", "min_edge_all", "cagr_pct"], ascending=[False, False, False])
    res.to_csv(OUT / "summary.csv", index=False)
    lines = [
        "# M Core + Convex Sleeve Probe 2026-05-27",
        "",
        "Long-window-native core plus small concentrated convex sleeve. Stress20 cost convention.",
        "",
        res.to_markdown(index=False, floatfmt=".2f"),
        "",
    ]
    if best_payload:
        lines += ["## Best Yearly", "", best_payload[1].to_markdown(index=False, floatfmt=".2f"), ""]
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("OUT", OUT)
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
