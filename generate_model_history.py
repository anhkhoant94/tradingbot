from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
DASH = ROOT / "dashboard"
BACKTEST_CACHE = ROOT / ".cache" / "backtest"
BOARD_LOT = 100

POLICY_RUNS = [
    ("r46_bear_stop_mcore", "R46 Bear Stop", OUT / "dashboard_policies" / "r46_bear_stop_mcore"),
    ("r23_nav3b_mcore", "R23_NAV3B", OUT / "dashboard_policies" / "r23_nav3b_mcore"),
    ("technical_t2_vni30_v13", "T2 VNI+30 Research", OUT / "dashboard_policies" / "technical_t2_vni30_v13"),
    ("rank_best_full_tier_a", "Best hiện tại - sạch", OUT / "dashboard_policies" / "rank_best_full_tier_a"),
    ("alpha", "Alpha - top 8, cap 12%", OUT / "backtest_v2_alpha_top8_capfix"),
    ("balanced", "Balanced - top 8 + crash guard", OUT / "backtest_v2_balanced_crash_capfix"),
    ("aggressive", "Aggressive - top 3 + stop-loss 15%", OUT / "backtest_v2_aggr_top3_val40_vol80_stop15_capfix"),
    ("defensive", "Defensive - top 8 + stop-loss 15%", OUT / "backtest_v2_defensive_stop15_capfix"),
    ("pipeline_ensemble", "Pipeline Ensemble 30% v4 / 70% v7", OUT / "backtest_ensemble" / "ensemble_30v4_70v7"),
    ("cyclical_overlay", "Cyclical Overlay (Phase 11) ⭐", OUT / "backtest_cyclical_overlay" / "v2"),
    ("weekly_alpha", "Weekly Alpha v4 - top 5/20% + weekly trend", OUT / "backtest_weekly" / "v4_conc_vol"),
    ("weekly_alpha_v6", "Weekly Alpha v6 - cyclical regime", OUT / "backtest_weekly_v6" / "v6_cyclical_t5_m25"),
]

ACTIVE_HISTORY_KEYS = {
    "r46_bear_stop_mcore",
    "r23_nav3b_mcore",
    "technical_t2_vni30_v13",
    "rank_best_full_tier_a",
}

COMBO_POLICIES = [
    (
        "phase18_meanreversion_boost",
        "Phase 18 Champion",
        OUT / "backtest_cyclical_overlay" / "yc_Phase_18_antigap_boost_-5p_35p",
        [
            OUT / "backtest_phase17_antigap_validation" / "v4" / "baseline_replay_trades.parquet",
            OUT / "backtest_phase17_antigap_validation" / "v7" / "anti_gap_1p5_trades.parquet",
        ],
    ),
    (
        "phase17_antigap_v7_candidate",
        "Phase 17 Anti-gap",
        OUT / "backtest_cyclical_overlay" / "phase17_v4_base_v7_anti",
        [
            OUT / "backtest_phase17_antigap_validation" / "v4" / "baseline_replay_trades.parquet",
            OUT / "backtest_phase17_antigap_validation" / "v7" / "anti_gap_1p5_trades.parquet",
        ],
    ),
    (
        "cyclical_overlay",
        "Phase 11 Baseline",
        OUT / "backtest_cyclical_overlay" / "v2",
        [
            OUT / "backtest_weekly" / "v4_conc_vol" / "trades.parquet",
            OUT / "backtest_pipeline" / "v7_best_sector_fa" / "trades.parquet",
        ],
    ),
]


def num(value, default=None):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def floor_to_board_lot(shares: float, lot_size: int = BOARD_LOT) -> int:
    try:
        value = float(shares)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return int((value + 1e-6) // lot_size) * lot_size


def round_record_lots(records: list[dict]) -> list[dict]:
    rounded = []
    for row in records:
        raw_shares = num(row.get("rawShares"), None)
        display_shares = num(row.get("shares"), 0.0) or 0.0
        item = dict(row)
        if raw_shares is None:
            item["rawShares"] = display_shares
        elif display_shares <= 0 and raw_shares > 0:
            display_shares = raw_shares
        item["shares"] = floor_to_board_lot(display_shares)
        price_k = num(row.get("executionPriceK"), None) or num(row.get("priceK"), None)
        gross_bil = num(row.get("grossBil"), 0.0) or 0.0
        if gross_bil <= 0 and item["shares"] > 0 and price_k:
            item["grossBil"] = item["shares"] * price_k / 1_000_000
        rounded.append(item)
    return rounded


def sorted_trades(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    out["_seq"] = range(len(out))
    out["date"] = pd.to_datetime(out["date"])
    if "trigger_date" in out.columns:
        out["trigger_date"] = pd.to_datetime(out["trigger_date"], errors="coerce")
    return out.sort_values(["date", "_seq"], kind="stable")


def action_label(side: str, before: float, after: float) -> str:
    side = str(side or "").upper()
    eps = max(1.0, abs(before), abs(after)) * 1e-6
    if side == "BUY":
        return "MUA THÊM" if before > eps else "MUA MỚI"
    if side == "SELL":
        return "BÁN 1 PHẦN" if after > eps else "BÁN HẾT"
    if "MISS" in side or "SKIP" in side:
        return "BỎ QUA"
    return side


def aggregate_trade_records(records: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in records:
        key = (row.get("date"), row.get("symbol"), row.get("side"))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)

    out = []
    for key in order:
        rows = grouped[key]
        if len(rows) == 1:
            out.append(rows[0])
            continue
        first = rows[0]
        last = rows[-1]
        shares = sum(num(r.get("shares"), 0.0) or 0.0 for r in rows)
        raw_shares = sum(num(r.get("rawShares"), 0.0) or 0.0 for r in rows)
        gross_bil = sum(num(r.get("grossBil"), 0.0) or 0.0 for r in rows)
        fees_bil = sum(num(r.get("feesBil"), 0.0) or 0.0 for r in rows)
        pnl_values = [num(r.get("pnlBil"), None) for r in rows if num(r.get("pnlBil"), None) is not None]
        before = num(first.get("positionBeforeShares"), 0.0) or 0.0
        after = num(last.get("positionAfterShares"), 0.0) or 0.0

        def weighted(field: str) -> float | None:
            denom = sum(num(r.get("shares"), 0.0) or 0.0 for r in rows if num(r.get(field), None) is not None)
            if denom <= 0:
                vals = [num(r.get(field), None) for r in rows if num(r.get(field), None) is not None]
                return vals[0] if vals else None
            return sum((num(r.get(field), 0.0) or 0.0) * (num(r.get("shares"), 0.0) or 0.0) for r in rows) / denom

        return_values = [r for r in rows if num(r.get("returnPct"), None) is not None and (num(r.get("grossBil"), 0.0) or 0.0) > 0]
        if return_values:
            return_pct = sum((num(r.get("returnPct"), 0.0) or 0.0) * (num(r.get("grossBil"), 0.0) or 0.0) for r in return_values) / sum((num(r.get("grossBil"), 0.0) or 0.0) for r in return_values)
        else:
            return_pct = None

        merged = {
            **first,
            "shares": shares,
            "rawShares": raw_shares,
            "priceK": weighted("priceK"),
            "executionPriceK": weighted("executionPriceK"),
            "grossBil": gross_bil,
            "feesBil": fees_bil,
            "entryPriceK": weighted("entryPriceK"),
            "returnPct": return_pct,
            "pnlBil": sum(pnl_values) if pnl_values else None,
            "holdDays": weighted("holdDays"),
            "actionLabel": action_label(first.get("side"), before, after),
            "positionBeforeShares": before,
            "positionAfterShares": after,
        }
        reasons = [str(r.get("reason")) for r in rows if r.get("reason")]
        if reasons:
            merged["reason"] = " + ".join(dict.fromkeys(reasons))
        out.append(merged)
    return out


def load_vnindex() -> pd.Series:
    for path in [BACKTEST_CACHE / "vnindex_daily_v6.parquet", BACKTEST_CACHE / "vnindex_daily.parquet"]:
        if path.exists():
            df = pd.read_parquet(path).copy()
            df["date"] = pd.to_datetime(df["date"])
            return pd.Series(pd.to_numeric(df["close"], errors="coerce").values, index=df["date"]).dropna()
    return pd.Series(dtype=float)


def vni_at(vni: pd.Series, date: pd.Timestamp):
    if vni.empty:
        return None
    sub = vni[vni.index <= date]
    if sub.empty:
        return None
    return float(sub.iloc[-1])


def nav_bil_from_row(row: pd.Series) -> float:
    nav_raw = num(row.get("nav_vnd"), None)
    if nav_raw is not None:
        return nav_raw / 1_000_000_000
    nav_overlay = num(row.get("nav_overlay"), None)
    if nav_overlay is not None:
        return nav_overlay
    nav_ratio = num(row.get("nav_ratio"), None)
    if nav_ratio is not None:
        return nav_ratio
    nav = num(row.get("nav"), None)
    if nav is None:
        return 0.0
    return nav / 1_000_000_000 if nav > 1_000_000 else nav


def cash_bil_from_row(row: pd.Series, nav_bil: float) -> float:
    cash_raw = num(row.get("cash_kvnd"), None)
    if cash_raw is not None:
        return cash_raw / 1_000_000
    cash = num(row.get("cash"), None)
    if cash is not None:
        return cash / 1_000_000_000 if cash > 1_000_000 else cash
    weight_cash = num(row.get("weight_cash"), None)
    if weight_cash is not None:
        return nav_bil * weight_cash
    return 0.0


def load_policy(key: str, label: str, path: Path) -> dict:
    trades_path = path / "trades.parquet"
    holdings_path = path / "holdings.parquet"
    curve_path = path / "equity_curve_honest.parquet"
    if not curve_path.exists():
        curve_path = path / "equity_curve.parquet"
    config_path = path / "config.json"
    frequency = "monthly"
    if config_path.exists():
        try:
            frequency = json.loads(config_path.read_text(encoding="utf-8")).get("frequency", frequency)
        except Exception:
            frequency = frequency
    trades = pd.read_parquet(trades_path) if trades_path.exists() else pd.DataFrame()
    holdings = pd.read_parquet(holdings_path) if holdings_path.exists() else pd.DataFrame()
    curve = pd.read_parquet(curve_path) if curve_path.exists() else pd.DataFrame()
    records = []
    if not trades.empty:
        trades = sorted_trades(trades)
        open_lots: dict[str, dict] = {}
        position_shares: dict[str, float] = {}
        for _, row in trades.iterrows():
            side = str(row.get("side", "")).upper()
            symbol = str(row.get("symbol", "")).upper()
            gross_value = row.get("gross") if "gross" in row and pd.notna(row.get("gross")) else row.get("gross_vnd")
            fees_value = row.get("fees") if "fees" in row and pd.notna(row.get("fees")) else row.get("fees_vnd")
            scale = 1_000_000 if "gross" in row and pd.notna(row.get("gross")) else 1_000_000_000
            gross_bil = num(gross_value, 0) / scale
            fees_bil = num(fees_value, 0) / scale
            price_k = num(row.get("price"))
            raw_shares = num(row.get("shares"), 0)
            if gross_bil <= 0 and raw_shares and price_k:
                gross_bil = raw_shares * price_k / 1_000_000
            display_shares = gross_bil * 1_000_000 / price_k if gross_bil > 0 and price_k else raw_shares
            entry = open_lots.get(symbol)
            entry_price = num(row.get("entry_price"), entry.get("entryPriceK") if entry else price_k)
            return_pct = num(row.get("return_pct"))
            pnl_bil = None
            hold_days = num(row.get("hold_days"))
            before_shares = float(position_shares.get(symbol, 0.0))
            if side == "BUY":
                open_lots[symbol] = {
                    "entryPriceK": price_k,
                    "entryDate": row["date"],
                    "shares": display_shares,
                    "grossBil": gross_bil,
                }
                after_shares = before_shares + float(display_shares or 0.0)
            else:
                if return_pct is None and entry_price and price_k:
                    return_pct = (price_k / entry_price - 1) * 100
                if entry_price and price_k:
                    pnl_bil = (price_k - entry_price) * display_shares / 1_000_000 - fees_bil
                if hold_days is None and entry and entry.get("entryDate") is not None:
                    hold_days = (row["date"] - entry["entryDate"]).days
                after_shares = max(0.0, before_shares - float(display_shares or 0.0))
                if after_shares <= max(1.0, before_shares) * 1e-6:
                    open_lots.pop(symbol, None)
            position_shares[symbol] = after_shares
            records.append(
                {
                    "date": row["date"].date().isoformat(),
                    "triggerDate": row["trigger_date"].date().isoformat()
                    if "trigger_date" in row and pd.notna(row.get("trigger_date"))
                    else None,
                    "symbol": symbol,
                    "side": side,
                    "actionLabel": action_label(side, before_shares, after_shares),
                    "positionBeforeShares": before_shares,
                    "positionAfterShares": after_shares,
                    "shares": display_shares,
                    "rawShares": raw_shares,
                    "priceK": price_k,
                    "executionPriceK": price_k,
                    "grossBil": gross_bil,
                    "feesBil": fees_bil,
                    "entryPriceK": entry_price,
                    "returnPct": return_pct,
                    "pnlBil": pnl_bil,
                    "holdDays": hold_days,
                    "reason": row.get("reason"),
                }
            )
        records = round_record_lots(aggregate_trade_records(records))
    final_holdings = []
    if not holdings.empty:
        holdings = holdings.copy()
        holdings["date"] = pd.to_datetime(holdings["date"])
        last_date = holdings["date"].max()
        h = holdings[holdings["date"].eq(last_date)].copy()
        for _, row in h.sort_values("target_weight", ascending=False).iterrows():
            final_holdings.append(
                {
                    "date": last_date.date().isoformat(),
                    "symbol": row.get("symbol"),
                    "shares": floor_to_board_lot(num(row.get("shares"), 0) or 0),
                    "priceK": num(row.get("price")),
                    "valueBil": num(row.get("value_kvnd"), 0) / 1_000_000,
                    "targetWeight": num(row.get("target_weight"), 0) * 100,
                    "entryPriceK": num(row.get("entry_price")),
                    "peakPriceK": num(row.get("peak_price")),
                }
            )
    elif not trades.empty:
        open_by_symbol = {}
        for _, row in trades.sort_values("date").iterrows():
            sym = row.get("symbol")
            if str(row.get("side", "")).upper() == "BUY":
                open_by_symbol[sym] = row
            else:
                open_by_symbol.pop(sym, None)
        final_nav = num(curve.iloc[-1].get("nav"), 0) if not curve.empty else 0
        for sym, row in sorted(open_by_symbol.items()):
            price_k = num(row.get("price"), 0)
            if "gross_vnd" in row and pd.notna(row.get("gross_vnd")):
                value_bil = num(row.get("gross_vnd"), 0) / 1_000_000_000
            else:
                value_bil = num(row.get("gross"), 0) / 1_000_000
            display_shares = value_bil * 1_000_000 / price_k if price_k else num(row.get("shares"), 0)
            final_holdings.append(
                {
                    "date": row["date"].date().isoformat(),
                    "symbol": sym,
                    "shares": floor_to_board_lot(display_shares),
                    "rawShares": num(row.get("shares"), 0),
                    "priceK": price_k,
                    "valueBil": value_bil,
                    "targetWeight": (value_bil * 1_000_000_000 / final_nav * 100) if final_nav else 0,
                    "entryPriceK": num(row.get("entry_price"), price_k),
                    "peakPriceK": None,
                }
            )
    curve_records = []
    if not curve.empty:
        curve = curve.copy()
        curve["date"] = pd.to_datetime(curve["date"])
        vni = load_vnindex()
        first_nav = None
        first_vni = None
        for _, row in curve.iterrows():
            nav_bil = nav_bil_from_row(row)
            cash_bil = cash_bil_from_row(row, nav_bil)
            if first_nav is None and nav_bil:
                first_nav = nav_bil
            vni_close = vni_at(vni, row["date"])
            if first_vni is None and vni_close:
                first_vni = vni_close
            curve_records.append(
                {
                    "date": row["date"].date().isoformat(),
                    "navBil": nav_bil,
                    "cashBil": cash_bil,
                    "positions": int(num(row.get("position_count"), num(row.get("n_positions"), num(row.get("n_holdings"), 0)))),
                    "returnPct": (nav_bil / first_nav - 1) * 100 if first_nav else 0,
                    "vniClose": vni_close,
                    "vniReturnPct": (vni_close / first_vni - 1) * 100 if first_vni and vni_close else None,
                }
            )
    return {
        "key": key,
        "label": label,
        "frequency": frequency,
        "tradeCount": len(records),
        "firstTradeDate": records[0]["date"] if records else None,
        "lastTradeDate": records[-1]["date"] if records else None,
        "trades": records,
        "finalHoldings": final_holdings,
        "equityCurve": curve_records,
    }


def records_from_trades(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"])
    if "trigger_date" in trades.columns:
        trades["trigger_date"] = pd.to_datetime(trades["trigger_date"], errors="coerce")
    if "sleeve" not in trades.columns:
        trades["sleeve"] = None
    records = []
    open_lots: dict[str, dict] = {}
    position_shares: dict[str, float] = {}
    for _, row in sorted_trades(trades).iterrows():
        side = str(row.get("side", "")).upper()
        symbol = str(row.get("symbol", "")).upper()
        gross_value = row.get("gross") if "gross" in row and pd.notna(row.get("gross")) else row.get("gross_vnd")
        fees_value = row.get("fees") if "fees" in row and pd.notna(row.get("fees")) else row.get("fees_vnd")
        scale = 1_000_000 if "gross" in row and pd.notna(row.get("gross")) else 1_000_000_000
        gross_bil = num(gross_value, 0) / scale
        fees_bil = num(fees_value, 0) / scale
        price_k = num(row.get("price"))
        raw_shares = num(row.get("shares"), 0)
        if gross_bil <= 0 and raw_shares and price_k:
            gross_bil = raw_shares * price_k / 1_000_000
        display_shares = gross_bil * 1_000_000 / price_k if gross_bil > 0 and price_k else raw_shares
        entry = open_lots.get(symbol)
        entry_price = num(row.get("entry_price"), entry.get("entryPriceK") if entry else price_k)
        return_pct = num(row.get("return_pct"))
        pnl_bil = None
        hold_days = num(row.get("hold_days"))
        before_shares = float(position_shares.get(symbol, 0.0))
        if side == "BUY":
            open_lots[symbol] = {
                "entryPriceK": price_k,
                "entryDate": row["date"],
                "shares": display_shares,
                "grossBil": gross_bil,
            }
            after_shares = before_shares + float(display_shares or 0.0)
        else:
            if return_pct is None and entry_price and price_k:
                return_pct = (price_k / entry_price - 1) * 100
            if entry_price and price_k:
                pnl_bil = (price_k - entry_price) * display_shares / 1_000_000 - fees_bil
            if hold_days is None and entry and entry.get("entryDate") is not None:
                hold_days = (row["date"] - entry["entryDate"]).days
            after_shares = max(0.0, before_shares - float(display_shares or 0.0))
            if after_shares <= max(1.0, before_shares) * 1e-6:
                open_lots.pop(symbol, None)
        position_shares[symbol] = after_shares
        records.append(
            {
                "date": row["date"].date().isoformat(),
                "triggerDate": row["trigger_date"].date().isoformat()
                if "trigger_date" in row and pd.notna(row.get("trigger_date"))
                else None,
                "symbol": symbol,
                "side": side,
                "actionLabel": action_label(side, before_shares, after_shares),
                "positionBeforeShares": before_shares,
                "positionAfterShares": after_shares,
                "sleeve": row.get("sleeve"),
                "shares": display_shares,
                "rawShares": raw_shares,
                "priceK": price_k,
                "executionPriceK": price_k,
                "grossBil": gross_bil,
                "feesBil": fees_bil,
                "entryPriceK": entry_price,
                "returnPct": return_pct,
                "pnlBil": pnl_bil,
                "holdDays": hold_days,
                "reason": row.get("reason"),
            }
        )
    return round_record_lots(aggregate_trade_records(records))


def load_combo_policy(key: str, label: str, curve_dir: Path, trade_paths: list[Path]) -> dict:
    policy = load_policy(key, label, curve_dir)
    frames = []
    for path in trade_paths:
        if not path.exists():
            continue
        df = pd.read_parquet(path).copy()
        df["sleeve"] = "v4" if "\\v4\\" in str(path) or "/v4/" in str(path) or "v4_conc_vol" in str(path) else "v7"
        frames.append(df)
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    records = records_from_trades(trades)
    policy["trades"] = records
    policy["tradeCount"] = len(records)
    policy["firstTradeDate"] = records[0]["date"] if records else None
    policy["lastTradeDate"] = records[-1]["date"] if records else None
    policy["frequency"] = "weekly execution"
    return policy


def main() -> None:
    legacy = [
        load_policy(key, label, path)
        for key, label, path in POLICY_RUNS
        if path.exists() and key in ACTIVE_HISTORY_KEYS
    ]
    seen = set()
    policies = []
    for policy in legacy:
        if policy["key"] in seen:
            continue
        seen.add(policy["key"])
        policies.append(policy)
    payload = {"policies": policies}
    DASH.mkdir(exist_ok=True)
    (DASH / "history.js").write_text(
        "window.MODEL_TRADE_HISTORY = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    (OUT / "model_trade_history.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {DASH / 'history.js'}")


if __name__ == "__main__":
    main()
