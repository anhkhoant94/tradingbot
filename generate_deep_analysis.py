from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
DASH = ROOT / "dashboard"
DASH.mkdir(exist_ok=True)

DEFAULT_NAV_VND = 1_000_000_000
BOARD_LOT = 100

BACKTEST_CACHE = ROOT / ".cache" / "backtest"
LIVE_PREVIEW_MATRIX = BACKTEST_CACHE / "yearly_floor_candidate_matrix_live_preview.parquet"
ACTIVE_CONFIG_PATH = OUT / "beat_vni30_parallel" / "g2_latency_tplus3_mutation_v1" / "best_stock_only" / "config.json"
SELECTOR_LABEL_DIR = OUT / "beat_vni30_parallel" / "claude_g2_selector_labels"
DAILY_AUDIT_CONFIG = OUT / "beat_vni30_parallel" / "codex_lane_n_daily_lot_cash_signal_exchange_gap_grid" / "best_stock_only" / "config.json"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


POLICY_SPECS = [
    {
        "key": "phase18_meanreversion_boost",
        "label": "Phase 18 Champion",
        "curve_dir": OUT / "backtest_cyclical_overlay" / "yc_Phase_18_antigap_boost_-5p_35p",
        "components": [
            ("v4", OUT / "backtest_phase17_antigap_validation" / "v4" / "baseline_replay_trades.parquet"),
            ("v7", OUT / "backtest_phase17_antigap_validation" / "v7" / "anti_gap_1p5_trades.parquet"),
        ],
        "note": "Policy chính đang triển khai: kết hợp v4 và v7, có anti-gap và sizing boost đã qua Phase 18.",
    },
    {
        "key": "phase17_antigap_v7_candidate",
        "label": "Phase 17 Anti-gap",
        "curve_dir": OUT / "backtest_cyclical_overlay" / "phase17_v4_base_v7_anti",
        "components": [
            ("v4", OUT / "backtest_phase17_antigap_validation" / "v4" / "baseline_replay_trades.parquet"),
            ("v7", OUT / "backtest_phase17_antigap_validation" / "v7" / "anti_gap_1p5_trades.parquet"),
        ],
        "note": "Policy dự phòng: dùng anti-gap ở nhánh v7, không dùng boost Phase 18.",
    },
    {
        "key": "cyclical_overlay",
        "label": "Phase 11 Baseline",
        "curve_dir": OUT / "backtest_cyclical_overlay" / "v2",
        "components": [
            ("v4", OUT / "backtest_weekly" / "v4_conc_vol" / "trades.parquet"),
            ("v7", OUT / "backtest_pipeline" / "v7_best_sector_fa" / "trades.parquet"),
        ],
        "note": "Baseline để so sánh: cyclical overlay trước Phase 17/18.",
    },
]

FLEXIBLE_CANDIDATE_DIR = OUT / "dashboard_policies" / "flexible_vni30_candidate"
R46_BEAR_STOP_DIR = OUT / "dashboard_policies" / "r46_bear_stop_mcore"
R23_NAV3B_DIR = OUT / "dashboard_policies" / "r23_nav3b_mcore"
TIER_A_BASELINE_DIR = OUT / "dashboard_policies" / "rank_best_full_tier_a"
T2_VNI30_RESEARCH_DIR = OUT / "dashboard_policies" / "technical_t2_vni30_v13"
HISTORY_CLEAN_DIR = ROOT / ".cache" / "backtest" / "history_clean"
PRICE_LIMIT_BY_EXCHANGE = {
    "HOSE": 0.07,
    "HSX": 0.07,
    "HNX": 0.10,
    "UPCOM": 0.15,
}
PRICE_LIMIT_GUARD = 0.005
_EXCHANGE_MAP: dict[str, str] | None = None


def num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def pct_label(value: float, digits: int = 1) -> str:
    return f"{float(value) * 100.0:.{digits}f}%".replace(".", ",")


def normalize_exchange(value) -> str:
    raw = str(value or "").upper().replace(" ", "")
    if raw in {"UPCO", "UPCOM", "UPC"}:
        return "UPCOM"
    return raw


def load_exchange_map() -> dict[str, str]:
    global _EXCHANGE_MAP
    if _EXCHANGE_MAP is not None:
        return _EXCHANGE_MAP
    mapping: dict[str, str] = {}
    sources = [
        (ROOT / ".cache" / "universe.parquet", "parquet"),
        (OUT / "screening_full_results.csv", "csv"),
    ]
    for path, kind in sources:
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path) if kind == "parquet" else pd.read_csv(path)
        except Exception:
            continue
        if "symbol" not in df.columns or "exchange" not in df.columns:
            continue
        for _, row in df[["symbol", "exchange"]].dropna().iterrows():
            symbol = str(row["symbol"]).upper()
            exchange = normalize_exchange(row["exchange"])
            if symbol and exchange:
                mapping[symbol] = exchange
    _EXCHANGE_MAP = mapping
    return mapping


def exchange_for_symbol(symbol: str) -> str:
    return load_exchange_map().get(str(symbol or "").upper(), "")


def effective_entry_gap_threshold(symbol: str, base_gap: float, exchange: str | None = None) -> float:
    exchange_key = normalize_exchange(exchange or exchange_for_symbol(symbol))
    limit = PRICE_LIMIT_BY_EXCHANGE.get(exchange_key)
    if limit is None:
        return float(base_gap)
    return max(0.0, min(float(base_gap), limit - PRICE_LIMIT_GUARD))


def latest_history_quote(symbol: str) -> dict:
    path = HISTORY_CLEAN_DIR / f"{str(symbol).upper()}.parquet"
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {}
    if df.empty:
        return {}
    date_col = "time" if "time" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col])
    row = df.sort_values(date_col).iloc[-1]
    close = num(row.get("close"), 0.0)
    if close <= 0:
        return {}
    return {
        "date": row[date_col].date().isoformat(),
        "close": close,
        "open": num(row.get("open"), close),
        "high": num(row.get("high"), close),
        "low": num(row.get("low"), close),
        "volume": num(row.get("volume"), 0.0),
    }


def load_symbol_history(symbol: str) -> pd.DataFrame:
    path = HISTORY_CLEAN_DIR / f"{str(symbol).upper()}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path).copy()
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    date_col = "time" if "time" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
    return df.rename(columns={date_col: "time"}).sort_values("time").reset_index(drop=True)


def history_quote_at_or_before(symbol: str, date_value: str | pd.Timestamp | None) -> dict:
    df = load_symbol_history(symbol)
    if df.empty or not date_value:
        return latest_history_quote(symbol)
    target = pd.Timestamp(date_value).normalize()
    sub = df[df["time"].dt.normalize() <= target]
    if sub.empty:
        return {}
    row = sub.iloc[-1]
    close = num(row.get("close"), 0.0)
    if close <= 0:
        return {}
    return {
        "date": pd.Timestamp(row.time).date().isoformat(),
        "close": close,
        "open": num(row.get("open"), close),
        "high": num(row.get("high"), close),
        "low": num(row.get("low"), close),
        "volume": num(row.get("volume"), 0.0),
    }


def load_candidate_execution_config() -> dict:
    config_path = FLEXIBLE_CANDIDATE_DIR / "config.json"
    defaults = {
        "entry_gap_threshold": 0.09,
        "entry_limit_buffer": 0.0,
        "entry_pullback_days": 2,
        "entry_no_fill_policy": "skip",
        "entry_min_sell_sessions": 3,
    }
    if not config_path.exists():
        return defaults
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    cfg = {**defaults, **loaded}
    if "config" in loaded and isinstance(loaded["config"], dict):
        cfg = {**defaults, **loaded["config"], **loaded}
    return cfg


def load_daily_audit_metrics() -> dict | None:
    policy_config = FLEXIBLE_CANDIDATE_DIR / "config.json"
    if policy_config.exists():
        try:
            cfg = json.loads(policy_config.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        if cfg.get("pass_vni20") is not None:
            fail_years = list(cfg.get("fail_years_vni20") or [])
            return {
                "passVni20": int(num(cfg.get("pass_vni20"), 0)),
                "passVni30": int(num(cfg.get("pass_vni30"), 0)),
                "cagr": round(num(cfg.get("cagr"), 0.0), 2),
                "maxDrawdown": round(num(cfg.get("max_drawdown"), 0.0), 2),
                "minEdgeVsVni": round(num(cfg.get("min_edge_vs_vni"), 0.0), 2),
                "failYears": fail_years,
                "baseGapThresholdPct": round(num(cfg.get("entry_gap_threshold"), 0.0) * 100.0, 2),
                "pullbackSessions": int(num(cfg.get("entry_pullback_days"), 0)),
                "minSellSessions": int(num(cfg.get("entry_min_sell_sessions"), 0)),
                "dailyStopLossPct": round(num(cfg.get("daily_stop_loss"), 0.0) * 100.0, 2),
                "slippageBps": int(num(cfg.get("slippage_bps_per_side"), 15)),
                "source": str(policy_config),
            }
    if not DAILY_AUDIT_CONFIG.exists():
        return None
    try:
        payload = json.loads(DAILY_AUDIT_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return None
    metrics = payload.get("metrics") or {}
    execution = payload.get("execution") or {}
    if not metrics:
        return None
    fail_years = [
        year
        for year in range(2021, 2027)
        if metrics.get(f"pass_vni30_y{year}") is False
    ]
    return {
        "passVni30": int(num(metrics.get("pass_vni30"), 0)),
        "cagr": round(num(metrics.get("cagr"), 0.0), 2),
        "maxDrawdown": round(num(metrics.get("maxdd"), 0.0), 2),
        "minEdgeVsVni": round(num(metrics.get("min_edge_vs_vni"), 0.0), 2),
        "failYears": fail_years,
        "baseGapThresholdPct": round(num(execution.get("gap_threshold"), 0.0) * 100.0, 2),
        "pullbackSessions": int(num(execution.get("pullback_sessions"), 0)),
        "minSellSessions": int(num(execution.get("min_sell_sessions"), 0)),
        "dailyStopLossPct": round(num(execution.get("daily_stop_loss"), 0.0) * 100.0, 2),
        "source": str(DAILY_AUDIT_CONFIG),
    }


def load_live_preview_targets() -> dict | None:
    if not LIVE_PREVIEW_MATRIX.exists() or not ACTIVE_CONFIG_PATH.exists():
        return None
    try:
        from backtest import pass30_direct_search as ds
    except Exception:
        return None
    try:
        matrix = pd.read_parquet(LIVE_PREVIEW_MATRIX).copy()
        if matrix.empty:
            return None
        matrix["date"] = pd.to_datetime(matrix["date"])
        matrix = matrix.sort_values(["symbol", "date"])
        matrix["next_close"] = matrix.groupby("symbol")["close"].shift(-1)
        matrix["next_ret"] = (matrix["next_close"] / matrix["close"] - 1).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
        for col in ds.FEATURES + ds.EXTRA_NUMERIC_COLS:
            matrix[col] = pd.to_numeric(matrix[col], errors="coerce")
        payload = json.loads(ACTIVE_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg = payload.get("config", payload)
        labels = ds.load_external_labels(SELECTOR_LABEL_DIR) if SELECTOR_LABEL_DIR.exists() else None
        prepared = ds.prepare_matrix(matrix, labels=labels)
        _eq, holdings, _metrics = ds.run_policy(prepared, cfg)
    except Exception:
        return None
    if holdings.empty:
        return None
    holdings = holdings.copy()
    holdings["date"] = pd.to_datetime(holdings["date"])
    latest_date = holdings["date"].max()
    latest = holdings[holdings["date"].eq(latest_date)].copy()
    if latest.empty:
        return None
    px = matrix[matrix["date"].eq(latest_date)][["symbol", "close"]].drop_duplicates("symbol")
    latest = latest.merge(px, on="symbol", how="left")
    rows = []
    for _, row in latest.iterrows():
        rows.append({
            "symbol": str(row.get("symbol", "")).upper(),
            "targetWeight": num(row.get("weight"), 0.0) * 100.0,
            "price": num(row.get("close"), 0.0),
        })
    return {
        "planDate": latest_date.date().isoformat(),
        "source": str(LIVE_PREVIEW_MATRIX),
        "rows": rows,
    }


def first_bar_index_on_or_after(df: pd.DataFrame, date: pd.Timestamp) -> int | None:
    if df.empty:
        return None
    idx = int(df["time"].searchsorted(pd.Timestamp(date), side="left"))
    if idx >= len(df):
        return None
    return idx


def live_entry_fill(symbol: str, signal_date: pd.Timestamp, cfg: dict) -> dict:
    df = load_symbol_history(symbol)
    start_idx = first_bar_index_on_or_after(df, signal_date)
    if start_idx is None or start_idx <= 0:
        return {"filled": False, "reason": "missing_history"}

    start = df.iloc[start_idx]
    prev_close = num(df.iloc[start_idx - 1].get("close"), 0.0)
    open_px = num(start.get("open"), 0.0)
    if prev_close <= 0 or open_px <= 0:
        return {"filled": False, "reason": "missing_prev_close_or_open"}

    gap = open_px / prev_close - 1.0
    gap_threshold = effective_entry_gap_threshold(symbol, float(cfg.get("entry_gap_threshold", 0.09)))
    limit_buffer = float(cfg.get("entry_limit_buffer", 0.0))
    pullback_days = int(cfg.get("entry_pullback_days", 2))
    no_fill_policy = str(cfg.get("entry_no_fill_policy", "skip"))
    min_sell_sessions = int(cfg.get("entry_min_sell_sessions", 3))

    if gap <= gap_threshold:
        entry_idx = start_idx
        fill_px = open_px
        fill_mode = "open"
    else:
        limit_px = prev_close * (1.0 + limit_buffer)
        end_idx = min(len(df) - 1, start_idx + max(1, pullback_days) - 1)
        entry_idx = None
        fill_px = None
        fill_mode = "skip"
        for idx in range(start_idx, end_idx + 1):
            bar = df.iloc[idx]
            low_px = num(bar.get("low"), 0.0)
            if low_px <= limit_px:
                bar_open = num(bar.get("open"), limit_px)
                fill_px = min(bar_open, limit_px) if bar_open <= limit_px else limit_px
                entry_idx = idx
                fill_mode = "pullback_limit"
                break
        if entry_idx is None and no_fill_policy == "window_close":
            bar = df.iloc[end_idx]
            fill_px = num(bar.get("close"), 0.0)
            if fill_px > 0:
                entry_idx = end_idx
                fill_mode = "window_close"
        if entry_idx is None or fill_px is None or fill_px <= 0:
            return {
                "filled": False,
                "reason": "gap_up_no_pullback",
                "entry_gap_pct": gap * 100.0,
            }

    sellable_idx = entry_idx + max(0, min_sell_sessions)
    sellable_date = None
    if sellable_idx < len(df):
        sellable_date = pd.Timestamp(df.iloc[sellable_idx].time).date().isoformat()
    return {
        "filled": True,
        "entry_date": pd.Timestamp(df.iloc[entry_idx].time).date().isoformat(),
        "entry_price": float(fill_px),
        "entry_gap_pct": gap * 100.0,
        "fill_mode": fill_mode,
        "prev_close": float(prev_close),
        "sellable_from": sellable_date,
        "is_sellable_now": (len(df) - 1) >= sellable_idx,
    }


def planned_buy_fill(
    symbol: str,
    plan_date: str,
    latest_price_date: str | None,
    reference_close: float,
    max_gap_band: float,
    limit_price: float,
    pullback_days: int = 2,
) -> dict:
    """Evaluate whether a previously planned Monday buy has already filled."""
    if not latest_price_date:
        return {"filled": False, "pending": True, "reason": "pre_open"}
    plan_ts = pd.Timestamp(plan_date).normalize()
    latest_ts = pd.Timestamp(latest_price_date).normalize()
    if latest_ts < plan_ts:
        return {"filled": False, "pending": True, "reason": "pre_open"}

    df = load_symbol_history(symbol)
    start_idx = first_bar_index_on_or_after(df, plan_ts)
    if start_idx is None:
        return {"filled": False, "pending": False, "reason": "missing_history"}
    latest_idx = int(df["time"].searchsorted(latest_ts, side="right")) - 1
    if latest_idx < start_idx:
        return {"filled": False, "pending": True, "reason": "missing_plan_bar"}

    reference_close = float(reference_close or 0.0)
    limit_price = float(limit_price or reference_close or 0.0)
    if reference_close <= 0 or limit_price <= 0:
        return {"filled": False, "pending": False, "reason": "missing_reference_close"}

    open_bar = df.iloc[start_idx]
    open_px = num(open_bar.get("open"), 0.0)
    if open_px <= 0:
        return {"filled": False, "pending": False, "reason": "missing_open"}

    gap = open_px / reference_close - 1.0
    if gap <= max_gap_band:
        return {
            "filled": True,
            "entry_date": pd.Timestamp(open_bar.time).date().isoformat(),
            "entry_price": float(open_px),
            "entry_gap_pct": gap * 100.0,
            "fill_mode": "open",
        }

    end_idx = min(latest_idx, start_idx + max(1, int(pullback_days)) - 1)
    for idx in range(start_idx, end_idx + 1):
        bar = df.iloc[idx]
        low_px = num(bar.get("low"), 0.0)
        if low_px <= limit_price:
            bar_open = num(bar.get("open"), limit_price)
            fill_px = min(bar_open, limit_price) if bar_open <= limit_price else limit_price
            return {
                "filled": True,
                "entry_date": pd.Timestamp(bar.time).date().isoformat(),
                "entry_price": float(fill_px),
                "entry_gap_pct": gap * 100.0,
                "fill_mode": "pullback_limit",
            }

    window_closed = latest_idx >= start_idx + max(1, int(pullback_days)) - 1
    return {
        "filled": False,
        "pending": not window_closed,
        "reason": "gap_up_no_pullback",
        "entry_gap_pct": gap * 100.0,
        "window_closed": window_closed,
    }


def planned_sell_fill(symbol: str, plan_date: str, latest_price_date: str | None) -> dict:
    """Evaluate the first executable sell price once Monday data is available."""
    if not latest_price_date:
        return {"filled": False, "pending": True, "reason": "pre_open"}
    plan_ts = pd.Timestamp(plan_date).normalize()
    latest_ts = pd.Timestamp(latest_price_date).normalize()
    if latest_ts < plan_ts:
        return {"filled": False, "pending": True, "reason": "pre_open"}

    df = load_symbol_history(symbol)
    idx = first_bar_index_on_or_after(df, plan_ts)
    if idx is None:
        return {"filled": False, "pending": False, "reason": "missing_history"}
    row = df.iloc[idx]
    fill_px = num(row.get("open"), num(row.get("close"), 0.0))
    if fill_px <= 0:
        return {"filled": False, "pending": False, "reason": "missing_open"}
    return {
        "filled": True,
        "exit_date": pd.Timestamp(row.time).date().isoformat(),
        "exit_price": float(fill_px),
        "fill_mode": "open",
    }


def week_plan_date_from_market_date(value: str | pd.Timestamp) -> pd.Timestamp:
    """Map latest market data to the weekly order date being prepared/evaluated."""
    ts = pd.Timestamp(value).normalize()
    if ts.weekday() <= 2:
        return ts - pd.Timedelta(days=ts.weekday())
    days = 7 - ts.weekday()
    return ts + pd.Timedelta(days=days)


def previous_close_before(symbol: str, quote_date: str | None) -> dict:
    if not quote_date:
        return {}
    df = load_symbol_history(symbol)
    if df.empty:
        return {}
    quote_ts = pd.Timestamp(quote_date).normalize()
    idx = int(df["time"].searchsorted(quote_ts, side="left"))
    if idx < len(df) and pd.Timestamp(df.iloc[idx].time).normalize() == quote_ts:
        prev_idx = idx - 1
    else:
        prev_idx = idx - 1
    if prev_idx < 0:
        return {}
    row = df.iloc[prev_idx]
    close = num(row.get("close"), 0.0)
    if close <= 0:
        return {}
    return {
        "date": pd.Timestamp(row.time).date().isoformat(),
        "close": close,
    }


def plan_stage(price_as_of: str | None, plan_date: str) -> str:
    if not price_as_of:
        return "pre_open"
    price_ts = pd.Timestamp(price_as_of).normalize()
    plan_ts = pd.Timestamp(plan_date).normalize()
    if price_ts < plan_ts:
        return "pre_open"
    if price_ts <= plan_ts + pd.Timedelta(days=2):
        return "live_window"
    return "closed_window"


def model_nav_bil_at(curve: pd.DataFrame | None, date_value: str | pd.Timestamp | None = None) -> float:
    if curve is None or curve.empty:
        return 1.0
    nav_col = nav_column(curve)
    if not nav_col:
        return 1.0
    rows = curve.copy()
    rows["date"] = pd.to_datetime(rows["date"])
    if date_value:
        target = pd.Timestamp(date_value)
        rows = rows[rows["date"] <= target]
    if rows.empty:
        return 1.0
    value = num(rows.iloc[-1].get(nav_col), 1.0)
    return normalize_nav(value) if value > 0 else 1.0


def policy_dir_for_key(key: str | None) -> Path:
    if key == "r46_bear_stop_mcore":
        return R46_BEAR_STOP_DIR
    if key == "r23_nav3b_mcore":
        return R23_NAV3B_DIR
    if key == "rank_best_full_tier_a":
        return TIER_A_BASELINE_DIR
    if key == "technical_t2_vni30_v13":
        return T2_VNI30_RESEARCH_DIR
    return FLEXIBLE_CANDIDATE_DIR


def ledger_share_count(row: pd.Series) -> float:
    price = num(row.get("price"), 0.0)
    gross_vnd = num(row.get("gross_vnd"), None)
    if gross_vnd is not None and gross_vnd > 0 and price > 0:
        return gross_vnd / (price * 1000.0)
    return num(row.get("shares"), 0.0)


def current_positions_from_ledger(curve: pd.DataFrame | None, policy_dir: Path = FLEXIBLE_CANDIDATE_DIR) -> dict[str, dict]:
    trades_path = policy_dir / "trades.parquet"
    if not trades_path.exists():
        return {}
    try:
        trades = pd.read_parquet(trades_path).copy()
    except Exception:
        return {}
    if trades.empty:
        return {}
    trades["date"] = pd.to_datetime(trades["date"], errors="coerce")
    trades = trades.dropna(subset=["date"])
    if trades.empty:
        return {}
    latest_date = trades["date"].max()
    lots: dict[str, list[dict]] = defaultdict(list)
    for _, row in trades[trades["date"] <= latest_date].sort_values("date").iterrows():
        symbol = str(row.get("symbol", "")).upper()
        shares = ledger_share_count(row)
        if not symbol or shares <= 0:
            continue
        side = str(row.get("side", "")).upper()
        if side == "BUY":
            lots[symbol].append({
                "shares": shares,
                "entryPrice": num(row.get("entry_price"), num(row.get("price"), 0.0)),
            })
        else:
            remaining = shares
            while remaining > 1e-9 and lots[symbol]:
                lot = lots[symbol][0]
                take = min(remaining, lot["shares"])
                lot["shares"] -= take
                remaining -= take
                if lot["shares"] <= 1e-9:
                    lots[symbol].pop(0)
    nav_bil = model_nav_bil_at(curve, latest_date)
    if nav_bil <= 0:
        return {}
    scale = DEFAULT_NAV_VND / 1_000_000_000 / nav_bil
    positions = {}
    for symbol, symbol_lots in lots.items():
        raw_shares = sum(num(lot.get("shares"), 0.0) for lot in symbol_lots)
        if raw_shares <= 0:
            continue
        cost = sum(num(lot.get("shares"), 0.0) * num(lot.get("entryPrice"), 0.0) for lot in symbol_lots)
        avg_entry = cost / raw_shares if raw_shares > 0 else 0.0
        copy_shares = floor_to_board_lot(raw_shares * scale)
        positions[symbol] = {
            "rawShares": raw_shares,
            "copyShares": copy_shares,
            "avgEntryPrice": avg_entry,
            "modelNavBil": nav_bil,
        }
    return positions


def current_copy_positions_from_ledger(curve: pd.DataFrame | None, policy_dir: Path = FLEXIBLE_CANDIDATE_DIR) -> dict[str, int]:
    return {
        symbol: int(pos.get("copyShares", 0))
        for symbol, pos in current_positions_from_ledger(curve, policy_dir).items()
        if int(pos.get("copyShares", 0)) >= BOARD_LOT
    }


def target_copy_shares(target_weight_pct: float, price_k: float) -> int:
    if target_weight_pct <= 0 or price_k <= 0:
        return 0
    value_vnd = DEFAULT_NAV_VND * target_weight_pct / 100.0
    return floor_to_board_lot(value_vnd / (price_k * 1000.0))


def build_next_session_plan(policy: dict, metrics: dict, cfg: dict, curve: pd.DataFrame | None = None) -> dict:
    holdings = policy.get("holdings", []) or []
    preview = load_live_preview_targets()
    # Keep dashboard actions consistent with the promoted policy package.
    # The older live preview matrix belongs to previous candidates and must not
    # override current copy-trade holdings.
    use_live_preview = False
    policy_dir = policy_dir_for_key(policy.get("key"))
    current_by_symbol = {str(h.get("symbol", "")).upper(): h for h in holdings if h.get("symbol")}
    current_positions_by_symbol = current_positions_from_ledger(curve, policy_dir)
    current_copy_shares_by_symbol = {
        symbol: int(pos.get("copyShares", 0))
        for symbol, pos in current_positions_by_symbol.items()
    }

    price_dates = sorted([str(h.get("priceAsOf")) for h in holdings if h.get("priceAsOf")])
    latest_price_date = price_dates[-1] if price_dates else metrics.get("lastUpdate")
    if use_live_preview:
        preview_dates = []
        for item in preview["rows"]:
            quote = latest_history_quote(item.get("symbol"))
            if quote.get("date"):
                preview_dates.append(str(quote["date"]))
        if preview_dates:
            latest_price_date = max([latest_price_date or ""] + preview_dates)

    if use_live_preview and preview.get("planDate"):
        plan_date = preview["planDate"]
    elif latest_price_date:
        plan_date = week_plan_date_from_market_date(latest_price_date).date().isoformat()
    else:
        plan_date = metrics.get("lastUpdate") or ""
    stage = plan_stage(latest_price_date, plan_date)
    gap_threshold = float(cfg.get("entry_gap_threshold", 0.09))
    limit_buffer = float(cfg.get("entry_limit_buffer", 0.0))
    pullback_days = int(cfg.get("entry_pullback_days", 2))

    if use_live_preview:
        target_map = {str(row["symbol"]).upper(): num(row.get("targetWeight"), 0.0) for row in preview["rows"]}
        plan_source = "live_preview"
    else:
        target_map = {sym: num(h.get("suggestedWeight"), 0.0) for sym, h in current_by_symbol.items()}
        plan_source = "current_policy"

    rows = []
    buy_orders = 0
    sell_orders = 0
    for symbol in sorted(set(target_map) | set(current_by_symbol)):
        h = current_by_symbol.get(symbol, {})
        quote = latest_history_quote(symbol)
        price_as_of = quote.get("date") or h.get("priceAsOf") or latest_price_date
        current = num(quote.get("close"), num(h.get("currentPrice"), 0.0))
        if current <= 0:
            current = num(h.get("currentPrice"), 0.0)
        exchange = normalize_exchange(h.get("exchange") or exchange_for_symbol(symbol))
        effective_gap_threshold = effective_entry_gap_threshold(symbol, gap_threshold, exchange)

        if stage == "pre_open":
            ref_close = current
            ref_date = price_as_of
        else:
            prev = previous_close_before(symbol, price_as_of)
            ref_close = num(prev.get("close"), current)
            ref_date = prev.get("date") or price_as_of
        max_buy = ref_close * (1.0 + effective_gap_threshold) if ref_close > 0 else 0.0
        limit_price = ref_close * (1.0 + limit_buffer) if ref_close > 0 else 0.0
        gap_pct = (current / ref_close - 1.0) * 100.0 if ref_close > 0 and current > 0 and stage != "pre_open" else None

        current_weight = num(h.get("suggestedWeight"), 0.0)
        target_weight = num(target_map.get(symbol), 0.0)
        current_shares = int(current_copy_shares_by_symbol.get(symbol, 0))
        target_shares = target_copy_shares(target_weight, current)
        if plan_source == "current_policy":
            target_weight = current_weight
            target_shares = current_shares
        delta_shares = target_shares - current_shares
        delta_weight = target_weight - current_weight
        is_sellable = bool(h.get("isSellableNow", True))
        sellable_from = h.get("sellableFrom")
        ledger_pos = current_positions_by_symbol.get(symbol, {})
        entry_price = num(ledger_pos.get("avgEntryPrice"), num(h.get("entryPrice"), 0.0))

        action = "GIỮ"
        status = "DỰ KIẾN"
        note = "Tín hiệu kỳ tới không đổi mã này; tiếp tục giữ và theo dõi target/stop."
        order_shares = 0
        if abs(delta_shares) < BOARD_LOT:
            pass
        elif delta_shares > 0:
            action = "MUA MỚI" if current_weight <= 0 else "MUA THÊM"
            buy_orders += 1
            order_shares = floor_to_board_lot(delta_shares)
            if stage == "pre_open":
                status = "CHUẨN BỊ"
                market_name = exchange or "sàn"
                note = f"Mua nếu open thứ 2 không sát trần và không vượt ngưỡng {pct_label(effective_gap_threshold)} của {market_name}; nếu vượt thì chờ về quanh {limit_price:.2f}k tối đa {pullback_days} phiên."
            elif current <= max_buy or current <= limit_price:
                status = "CÓ THỂ KHỚP"
                note = "Giá hiện tại còn hợp lệ theo kế hoạch, có thể đặt lệnh."
            elif stage == "live_window":
                status = "CHỜ GIÁ"
                note = f"Không đuổi giá; chờ về quanh {limit_price:.2f}k trong cửa sổ T2-T4."
            else:
                status = "BỎ QUA"
                note = "Hết cửa sổ chờ giá, bỏ qua lệnh để tránh mua trễ."
        else:
            sell_orders += 1
            action = "BÁN HẾT" if target_weight <= 0 else "BÁN 1 PHẦN"
            order_shares = floor_to_board_lot(min(current_shares, abs(delta_shares)))
            if is_sellable:
                status = "BÁN MỞ CỬA" if stage == "pre_open" else "BÁN NGAY"
                note = "Giảm tỷ trọng theo target mới; đã qua điều kiện T+2.5 nếu là vị thế đang nắm."
            else:
                status = "CHỜ T+2.5"
                note = f"Chờ đủ T+2.5 rồi bán; ngày có thể bán: {sellable_from or '-'}."

        order_value_mil = order_shares * current / 1000.0 if order_shares and current > 0 else 0.0
        rows.append({
            "planDate": plan_date,
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "status": status,
            "currentPrice": round(current, 3) if current > 0 else None,
            "priceAsOf": price_as_of,
            "entryPrice": round(entry_price, 3) if entry_price > 0 else h.get("entryPrice"),
            "referenceClose": round(ref_close, 3) if ref_close > 0 else None,
            "referenceDate": ref_date,
            "maxBuyPrice": round(max_buy, 3) if max_buy > 0 else None,
            "limitPrice": round(limit_price, 3) if limit_price > 0 else None,
            "gapPct": round(gap_pct, 2) if gap_pct is not None else None,
            "baseGapThresholdPct": round(gap_threshold * 100.0, 2),
            "effectiveGapThresholdPct": round(effective_gap_threshold * 100.0, 2),
            "currentWeight": round(current_weight, 2),
            "targetWeight": round(target_weight, 2),
            "deltaWeight": round(delta_weight, 2),
            "currentCopyShares": current_shares,
            "targetCopyShares": target_shares,
            "orderShares": order_shares,
            "orderValueMil": round(order_value_mil, 1),
            "targetPrice": h.get("targetPrice"),
            "stopPrice": h.get("stopPrice"),
            "note": note,
        })

    if not rows:
        summary = "Chưa có danh mục mục tiêu để lập kế hoạch kỳ tới."
    elif buy_orders or sell_orders:
        summary = f"{buy_orders} lệnh mua, {sell_orders} lệnh bán dự kiến cho {plan_date}; Update trong phiên sẽ tự đổi trạng thái theo giá mới."
    else:
        summary = f"Không có mua/bán mới cho {plan_date}; hiện là kế hoạch giữ danh mục và theo dõi giá."
    return {
        "asOf": latest_price_date,
        "planDate": plan_date,
        "stage": stage,
        "source": plan_source,
        "entryGapThresholdPct": round(gap_threshold * 100.0, 2),
        "priceLimitAware": True,
        "priceLimitGuardPct": round(PRICE_LIMIT_GUARD * 100.0, 2),
        "pullbackDays": pullback_days,
        "summary": summary,
        "rows": sorted(rows, key=lambda row: (0 if str(row["action"]).startswith("BÁN") else 1 if str(row["action"]).startswith("MUA") else 2, row["symbol"])),
    }


def floor_to_board_lot(shares: float, lot_size: int = BOARD_LOT) -> int:
    if shares <= 0:
        return 0
    return int(shares // lot_size) * lot_size


def load_full_screening() -> pd.DataFrame:
    path = OUT / "screening_full_results.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path).fillna("")
    df["symbol"] = df["symbol"].astype(str).str.upper()
    return df


def row_lookup(full: pd.DataFrame) -> dict[str, pd.Series]:
    if full.empty:
        return {}
    return {str(row["symbol"]).upper(): row for _, row in full.iterrows()}


def latest_trade_symbols(limit: int = 16) -> list[str]:
    trades_path = FLEXIBLE_CANDIDATE_DIR / "trades.parquet"
    if not trades_path.exists():
        return []
    try:
        trades = pd.read_parquet(trades_path).copy()
    except Exception:
        return []
    if trades.empty or "date" not in trades.columns:
        return []
    trades["date"] = pd.to_datetime(trades["date"], errors="coerce")
    trades = trades.dropna(subset=["date"])
    if trades.empty:
        return []
    latest_date = trades["date"].max()
    symbols = []
    for sym in trades.loc[trades["date"].eq(latest_date), "symbol"].astype(str).str.upper():
        if sym and sym not in symbols:
            symbols.append(sym)
    return symbols[:limit]


def build_latest_trade_memos(full_by_symbol: dict[str, pd.Series]) -> list[dict]:
    memos = []
    for symbol in latest_trade_symbols():
        screen = full_by_symbol.get(symbol)
        quote = latest_history_quote(symbol)
        screen_price = num(screen.get("current_price_k") if screen is not None else 0.0, 0.0)
        current = num(quote.get("close"), screen_price)
        if current <= 0:
            continue
        target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.9, 3))
        industry = str(screen.get("industry_name") if screen is not None else "-")
        sleeve = str(screen.get("sector_group") if screen is not None else "-")
        memos.append(
            {
                "symbol": symbol,
                "status": "THEO DÕI",
                "currentPrice": round(current, 3),
                "priceAsOf": quote.get("date"),
                "targetPrice": round(target, 3),
                "stopPrice": round(stop, 3),
                "suggestedWeight": None,
                "industry": industry or "-",
                "sleeve": sleeve or "-",
                "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
                "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
                "plan": "Giá thị trường phục vụ bảng lệnh mới nhất.",
                "reasons": [],
            }
        )
    return memos


def nav_column(curve: pd.DataFrame) -> str | None:
    for col in ["nav_overlay", "nav_ratio", "nav", "nav_vnd"]:
        if col in curve.columns:
            return col
    return None


def normalize_nav(value: float) -> float:
    if value > 1_000_000:
        return value / 1_000_000_000
    return value


def load_curve(curve_dir: Path) -> pd.DataFrame:
    path = curve_dir / "equity_curve_honest.parquet"
    if not path.exists():
        path = curve_dir / "equity_curve.parquet"
    if not path.exists():
        return pd.DataFrame()
    curve = pd.read_parquet(path).copy()
    curve["date"] = pd.to_datetime(curve["date"])
    return curve.sort_values("date")


def load_metrics(curve_dir: Path, curve: pd.DataFrame) -> dict:
    config_path = curve_dir / "config.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    cagr = num(config.get("cagr"), None)
    sharpe = num(config.get("sharpe"), None)
    max_dd = num(config.get("max_drawdown"), None)
    total_return = num(config.get("total_return"), None)
    if curve.empty:
        return {
            "historicalCagr": cagr or 0.0,
            "historicalSharpe": sharpe or 0.0,
            "historicalMaxDrawdown": max_dd or 0.0,
            "totalReturn": total_return or 0.0,
            "lastUpdate": None,
        }
    nav_col = nav_column(curve)
    nav = pd.to_numeric(curve[nav_col], errors="coerce").dropna() if nav_col else pd.Series(dtype=float)
    if cagr is None and len(nav) > 1:
        years = (curve["date"].iloc[-1] - curve["date"].iloc[0]).days / 365.25
        cagr = ((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    if sharpe is None and len(nav) > 2:
        rets = nav.pct_change().dropna()
        sharpe = rets.mean() / rets.std() * (52 ** 0.5) if rets.std() > 0 else 0.0
    if max_dd is None and len(nav) > 1:
        max_dd = ((nav - nav.cummax()) / nav.cummax()).min() * 100
    if total_return is None and len(nav) > 1:
        total_return = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    return {
        "historicalCagr": round(float(cagr or 0.0), 2),
        "historicalSharpe": round(float(sharpe or 0.0), 2),
        "historicalMaxDrawdown": round(float(max_dd or 0.0), 2),
        "totalReturn": round(float(total_return or 0.0), 2),
        "lastUpdate": curve["date"].iloc[-1].date().isoformat(),
    }


def metrics_from_period(curve: pd.DataFrame, fallback: dict, start_date: str = "2021-01-01") -> dict:
    if curve.empty:
        return fallback
    nav_col = nav_column(curve)
    if not nav_col:
        return fallback
    x = curve.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).sort_values("date")
    x = x[x["date"] >= pd.Timestamp(start_date)].copy()
    if len(x) < 2:
        return fallback
    nav = pd.to_numeric(x[nav_col], errors="coerce").dropna()
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return fallback
    years = max((x["date"].iloc[-1] - x["date"].iloc[0]).days / 365.25, 1 / 365.25)
    rets = nav.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * (252 ** 0.5) if len(rets) > 2 and rets.std() > 0 else 0.0
    max_dd = ((nav - nav.cummax()) / nav.cummax()).min() * 100.0
    return {
        **fallback,
        "historicalCagr": round(((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1) * 100.0, 2),
        "historicalSharpe": round(float(sharpe), 2),
        "historicalMaxDrawdown": round(float(max_dd), 2),
        "totalReturn": round((nav.iloc[-1] / nav.iloc[0] - 1) * 100.0, 2),
        "lastUpdate": x["date"].iloc[-1].date().isoformat(),
        "displayStartDate": start_date,
    }


def component_nav_bil(curve: pd.DataFrame, component: str) -> float:
    if curve.empty:
        return 1.0
    row = curve.iloc[-1]
    value = num(row.get(component), 0.0)
    if value <= 0:
        value = num(row.get(f"{component}n"), 1.0)
    return max(normalize_nav(value), 0.0001)


def component_weight(curve: pd.DataFrame, component: str) -> float:
    if curve.empty:
        return 0.0
    row = curve.iloc[-1]
    explicit = num(row.get(f"weight_{component}"), None)
    if explicit is not None:
        return max(explicit, 0.0)
    return 1.0


def open_positions(trades_path: Path) -> dict[str, dict]:
    if not trades_path.exists():
        return {}
    trades = pd.read_parquet(trades_path).copy()
    if trades.empty:
        return {}
    trades["date"] = pd.to_datetime(trades["date"])
    positions: dict[str, dict] = {}
    for _, row in trades.sort_values("date").iterrows():
        sym = str(row.get("symbol", "")).upper()
        side = str(row.get("side", "")).upper()
        if not sym:
            continue
        price = num(row.get("price"), 0.0)
        gross_vnd = num(row.get("gross_vnd"), 0.0)
        if gross_vnd <= 0 and "gross" in row:
            gross_vnd = num(row.get("gross"), 0.0) * 1_000_000
        shares = num(row.get("shares"), 0.0)
        if gross_vnd > 0 and price > 0:
            shares = gross_vnd / (price * 1000)
        if side == "BUY":
            positions[sym] = {
                "symbol": sym,
                "entryDate": row["date"].date().isoformat(),
                "entryPrice": price,
                "shares": shares,
                "grossVnd": gross_vnd,
                "rawWeight": num(row.get("weight"), 0.0),
            }
        else:
            positions.pop(sym, None)
    return positions


def build_holding(
    symbol: str,
    lots: list[dict],
    full_by_symbol: dict[str, pd.Series],
    nav_vnd: int = DEFAULT_NAV_VND,
) -> dict:
    row = full_by_symbol.get(symbol)
    current = num(row.get("current_price_k") if row is not None else 0.0, 0.0)
    if current <= 0:
        current = max(num(lot["entryPrice"]) for lot in lots)
    target = num(row.get("target_price_k") if row is not None else 0.0, round(current * 1.2, 3))
    stop = num(row.get("stop_price_k") if row is not None else 0.0, round(current * 0.9, 3))
    industry = str(row.get("industry_name") if row is not None else "")
    sleeve = str(row.get("sector_group") if row is not None else "")
    total_weight = sum(num(lot["weightPct"]) for lot in lots)
    value_mil = nav_vnd / 1_000_000 * total_weight / 100
    target_shares = int((value_mil * 1_000_000) // (current * 1000)) if current > 0 else 0
    entry_value = sum(num(lot["weightPct"]) * num(lot["entryPrice"]) for lot in lots)
    avg_entry = entry_value / total_weight if total_weight > 0 else current
    entry_date = min(str(lot["entryDate"]) for lot in lots if lot.get("entryDate"))
    pnl_pct = (current / avg_entry - 1) * 100 if avg_entry > 0 else 0.0
    pnl_mil = value_mil * pnl_pct / 100
    action = "MUA ĐỦ TỶ TRỌNG"
    return {
        "symbol": symbol,
        "status": "MUA",
        "rating": action,
        "suggestedWeight": round(total_weight, 1),
        "currentPrice": round(current, 3),
        "entryDate": entry_date,
        "entryPrice": round(avg_entry, 3),
        "targetPrice": round(target, 3),
        "stopPrice": round(stop, 3),
        "modelShares": target_shares,
        "modelValueMil": round(value_mil, 1),
        "currentPnlMil": round(pnl_mil, 1),
        "currentPnlPct": round(pnl_pct, 2),
        "industry": industry or sleeve or "-",
        "sleeve": sleeve or "-",
        "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
        "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
        "plan": f"Mua {target_shares:,} cổ phiếu {symbol}, giá trị khoảng {value_mil:.1f} triệu cho NAV 1 tỷ.",
        "reasons": [
            "Đây là vị thế đang mở trong policy, không lấy từ bảng sàng lọc BUY/AVOID.",
            f"Giá vốn model {avg_entry:.2f}k; giá thị trường {current:.2f}k; target {target:.2f}k; stop {stop:.2f}k.",
        ],
    }


def build_policy(spec: dict, full_by_symbol: dict[str, pd.Series]) -> dict:
    curve = load_curve(spec["curve_dir"])
    metrics = load_metrics(spec["curve_dir"], curve)
    lots_by_symbol: dict[str, list[dict]] = defaultdict(list)
    total_component_weight = 0.0
    for component, trades_path in spec["components"]:
        comp_weight = component_weight(curve, component)
        comp_nav = component_nav_bil(curve, component)
        total_component_weight += comp_weight
        for sym, pos in open_positions(trades_path).items():
            current = num(full_by_symbol.get(sym, {}).get("current_price_k") if sym in full_by_symbol else 0.0, pos["entryPrice"])
            if num(pos.get("rawWeight"), 0.0) > 0:
                weight_pct = num(pos.get("rawWeight"), 0.0) * comp_weight * 100
            else:
                current_value_bil = pos["shares"] * current / 1_000_000
                weight_pct = current_value_bil / comp_nav * comp_weight * 100
            if weight_pct <= 0:
                continue
            lots_by_symbol[sym].append({**pos, "component": component, "weightPct": weight_pct})
    holdings = [
        build_holding(sym, lots, full_by_symbol)
        for sym, lots in sorted(lots_by_symbol.items(), key=lambda item: -sum(l["weightPct"] for l in item[1]))
    ]
    invested = sum(item["suggestedWeight"] for item in holdings)
    return {
        "key": spec["key"],
        "label": spec["label"],
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "Bán khi policy phát tín hiệu thoát vị thế. Dashboard đã dịch tín hiệu kỹ thuật thành MUA/BÁN.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": round(total_component_weight * 100, 1),
        "note": spec["note"],
        "holdings": holdings,
    }


def build_flexible_candidate_policy(full_by_symbol: dict[str, pd.Series]) -> dict | None:
    curve = load_curve(FLEXIBLE_CANDIDATE_DIR)
    holdings_path = FLEXIBLE_CANDIDATE_DIR / "holdings.parquet"
    if curve.empty or not holdings_path.exists():
        return None
    metrics = load_metrics(FLEXIBLE_CANDIDATE_DIR, curve)
    execution_cfg = load_candidate_execution_config()
    daily_audit = load_daily_audit_metrics()
    ledger_positions = current_positions_from_ledger(curve)
    holdings_raw = pd.read_parquet(holdings_path).copy()
    if holdings_raw.empty:
        holdings = []
    else:
        holdings_raw["date"] = pd.to_datetime(holdings_raw["date"])
        last_date = holdings_raw["date"].max()
        holdings = []
        for _, row in holdings_raw[holdings_raw["date"].eq(last_date)].sort_values("target_weight", ascending=False).iterrows():
            symbol = str(row.get("symbol", "")).upper()
            screen = full_by_symbol.get(symbol)
            exchange = normalize_exchange(screen.get("exchange") if screen is not None else exchange_for_symbol(symbol))
            quote = latest_history_quote(symbol)
            screen_price = num(screen.get("current_price_k") if screen is not None else row.get("price"), num(row.get("price"), 0.0))
            current = num(quote.get("close"), screen_price)
            target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
            stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.9, 3))
            weight_pct = num(row.get("target_weight"), 0.0) * 100
            value_mil = DEFAULT_NAV_VND / 1_000_000 * weight_pct / 100
            fill = live_entry_fill(symbol, last_date, execution_cfg)
            ledger_pos = ledger_positions.get(symbol, {})
            entry_price = num(ledger_pos.get("avgEntryPrice"), num(fill.get("entry_price"), num(row.get("entry_price"), num(row.get("price"), current))))
            entry_date = fill.get("entry_date") or last_date.date().isoformat()
            ledger_copy_shares = int(ledger_pos.get("copyShares", 0) or 0)
            shares = ledger_copy_shares or (
                floor_to_board_lot((value_mil * 1_000_000) / (entry_price * 1000))
                if entry_price > 0 and fill.get("filled", True)
                else 0
            )
            copy_shares = shares
            cost_mil = shares * entry_price / 1000 if entry_price > 0 else 0.0
            current_value_mil = shares * current / 1000 if current > 0 else 0.0
            pnl_mil = current_value_mil - cost_mil
            pnl_pct = (current / entry_price - 1) * 100 if entry_price > 0 and current > 0 else 0.0
            holdings.append({
                "symbol": symbol,
                "exchange": exchange,
                "status": "MUA" if fill.get("filled", True) else "CHO_KHOP",
                "rating": "MUA THEO MODEL VNI+20" if fill.get("filled", True) else "CHỜ KHỚP THEO LUẬT GIÁ",
                "suggestedWeight": round(weight_pct, 1),
                "currentPrice": round(current, 3),
                "priceAsOf": quote.get("date") or metrics["lastUpdate"],
                "signalDate": last_date.date().isoformat(),
                "entryDate": entry_date,
                "entryPrice": round(entry_price, 3),
                "fillMode": fill.get("fill_mode", "target_snapshot"),
                "entryGapPct": round(num(fill.get("entry_gap_pct"), 0.0), 2),
                "sellableFrom": fill.get("sellable_from"),
                "isSellableNow": bool(fill.get("is_sellable_now", False)),
                "targetPrice": round(target, 3),
                "stopPrice": round(stop, 3),
                "modelShares": shares,
                "copyShares": copy_shares,
                "modelValueMil": round(value_mil, 1),
                "currentValueMil": round(current_value_mil, 1),
                "costMil": round(cost_mil, 1),
                "currentPnlMil": round(pnl_mil, 1),
                "currentPnlPct": round(pnl_pct, 2),
                "industry": str(screen.get("industry_name") if screen is not None else "-"),
                "sleeve": str(screen.get("sector_group") if screen is not None else "-"),
                "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
                "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
                "plan": f"Model đang giữ {shares:,} cổ phiếu {symbol} quy đổi theo NAV 1 tỷ, giá vốn bình quân {entry_price:.2f}k.",
                "reasons": [
                    "Policy dùng tín hiệu sau tuần trước, không dùng nhìn ngược kết quả tuần tới.",
                    "Nếu đầu tuần gap up mạnh, model chờ vùng giá tốt nhưng chỉ nhận lệnh khi vẫn bán được sau T+2.5; nếu không hợp lệ thì bỏ qua.",
                    f"Giá vốn dashboard lấy từ luật khớp live: {fill.get('fill_mode', 'target_snapshot')} ngày {entry_date}, gap đầu vào {num(fill.get('entry_gap_pct'), 0.0):.2f}%, bán hợp lệ từ {fill.get('sellable_from') or '-'}.",
                    "Nếu mã vừa giảm mạnh, model tạm chặn mua/giữ lại trong 2 tuần khi breadth và VNI13 cho phép bật stop.",
                ],
            })
    invested = sum(item["suggestedWeight"] for item in holdings)
    audit_text = "Candidate 15bps đã được đưa lên dashboard theo quyết định của anh; cần theo dõi trượt giá thực tế khi copy trade."
    if daily_audit:
        fail_text = ", ".join(str(y) for y in daily_audit["failYears"]) or "không"
        pass20 = daily_audit.get("passVni20", daily_audit.get("passVni30", 0))
        bps = daily_audit.get("slippageBps", 15)
        audit_text = (
            f"Strict 100-lot tại {bps}bps đạt VNI+20 {pass20}/6, "
            f"CAGR {daily_audit['cagr']:.1f}%, MaxDD {daily_audit['maxDrawdown']:.1f}%, "
            f"min edge {daily_audit['minEdgeVsVni']:.1f} điểm %, fail năm {fail_text}."
        )
    policy = {
        "key": "flexible_vni30_candidate",
        "label": "VNI+20 Copy-trade 15bps",
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "Bán khi policy phát lệnh thoát vị thế; copy trade theo lô 100 và giá khớp sau tín hiệu.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": 100.0,
        "productionAudit": daily_audit,
        "note": audit_text,
        "methodology": {
            "status": "Đang chạy trên dashboard với giả định chi phí 15bps mỗi chiều",
            "target": "Backtest 2021-2026 vượt VN-Index tối thiểu 20 điểm phần trăm trong cả 6/6 năm.",
            "entry": "Tín hiệu khóa sau phiên T0; lệnh chỉ được đặt từ T1. Nếu giá mở cửa tăng quá ngưỡng, model chờ pullback trong 2 phiên; không về vùng mua thì bỏ qua.",
            "selection": "Chọn tối đa 3 cổ phiếu có xu hướng, dòng tiền và thanh khoản tốt; mỗi mã tối đa khoảng một phần ba NAV.",
            "risk": "Giả định 15bps là điều kiện lên dashboard. Nếu trượt giá thực tế lên 20bps, gate VNI+20 giảm còn 4/6 nên cần theo dõi sau mỗi lệnh.",
            "audit": audit_text,
        },
        "holdings": holdings,
    }
    policy["plannedOrders"] = build_next_session_plan(policy, metrics, execution_cfg, curve)
    return policy


def build_tier_a_baseline_policy(full_by_symbol: dict[str, pd.Series]) -> dict | None:
    curve = load_curve(TIER_A_BASELINE_DIR)
    holdings_path = TIER_A_BASELINE_DIR / "holdings.parquet"
    if curve.empty or not holdings_path.exists():
        return None
    metrics = load_metrics(TIER_A_BASELINE_DIR, curve)
    cfg_path = TIER_A_BASELINE_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    ledger_positions = current_positions_from_ledger(curve, TIER_A_BASELINE_DIR)
    holdings_raw = pd.read_parquet(holdings_path).copy()
    holdings_raw["date"] = pd.to_datetime(holdings_raw["date"])
    last_date = holdings_raw["date"].max()
    latest_nav_bil = model_nav_bil_at(curve, last_date)
    holdings = []
    for _, row in holdings_raw[holdings_raw["date"].eq(last_date)].sort_values("target_weight", ascending=False).iterrows():
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        screen = full_by_symbol.get(symbol)
        exchange = normalize_exchange(screen.get("exchange") if screen is not None else exchange_for_symbol(symbol))
        quote = latest_history_quote(symbol)
        current = num(quote.get("close"), num(row.get("price"), 0.0))
        if current <= 0:
            current = num(row.get("price"), 0.0)
        target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.88, 3))
        weight_pct = num(row.get("target_weight"), 0.0) * 100.0
        ledger_pos = ledger_positions.get(symbol, {})
        entry_price = num(ledger_pos.get("avgEntryPrice"), num(row.get("entry_price"), current))
        entry_date_raw = row.get("entry_date")
        entry_date = pd.Timestamp(entry_date_raw).date().isoformat() if pd.notna(entry_date_raw) else last_date.date().isoformat()
        shares = int(ledger_pos.get("copyShares", 0) or 0)
        if shares < BOARD_LOT and entry_price > 0:
            shares = floor_to_board_lot((DEFAULT_NAV_VND * weight_pct / 100.0) / (entry_price * 1000.0))
        current_value_mil = shares * current / 1000 if current > 0 else 0.0
        cost_mil = shares * entry_price / 1000 if entry_price > 0 else 0.0
        pnl_mil = current_value_mil - cost_mil
        pnl_pct = (current / entry_price - 1) * 100 if entry_price > 0 and current > 0 else 0.0
        holdings.append({
            "symbol": symbol,
            "exchange": exchange,
            "status": "MUA",
            "rating": "ĐANG NẮM THEO BEST HIỆN TẠI",
            "suggestedWeight": round(weight_pct, 1),
            "currentPrice": round(current, 3),
            "priceAsOf": quote.get("date") or row.get("price_as_of") or metrics["lastUpdate"],
            "signalDate": last_date.date().isoformat(),
            "entryDate": entry_date,
            "entryPrice": round(entry_price, 3),
            "fillMode": "weekly_close_signal",
            "entryGapPct": 0.0,
            "sellableFrom": None,
            "isSellableNow": True,
            "targetPrice": round(target, 3),
            "stopPrice": round(stop, 3),
            "modelShares": shares,
            "copyShares": shares,
            "modelValueMil": round(DEFAULT_NAV_VND / 1_000_000 * weight_pct / 100.0, 1),
            "currentValueMil": round(current_value_mil, 1),
            "costMil": round(cost_mil, 1),
            "currentPnlMil": round(pnl_mil, 1),
            "currentPnlPct": round(pnl_pct, 2),
            "industry": str(screen.get("industry_name") if screen is not None else "-"),
            "sleeve": str(screen.get("sector_group") if screen is not None else "-"),
            "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
            "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
            "plan": f"Model sạch hiện đang nắm {shares:,} cổ phiếu {symbol} quy đổi theo NAV 1 tỷ, giá vốn bình quân {entry_price:.2f}k.",
            "reasons": [
                "Policy này là best sạch hiện tại: rank_best_full, cash overlay, không ETF/bond/margin/short.",
                "NAV và lịch sử lấy từ equity_curve_honest.parquet để tránh lỗi MTM stale đã phát hiện.",
                "Đây là baseline production/paper-trade hợp lý nhất hiện tại, không phải candidate VNI+20/+30 đã bị overfit.",
            ],
        })
    invested = sum(item["suggestedWeight"] for item in holdings)
    note = (
        "Best sạch hiện tại: CAGR 19.88%, MaxDD -34.15%, beat VNI 5/6 năm 2021-2026; "
        "không đạt VNI+20/+30 6/6 nhưng rule tổng quát và audit sạch hơn các candidate overfit."
    )
    policy = {
        "key": "rank_best_full_tier_a",
        "label": "Best hiện tại - sạch",
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "Bán khi điểm tổng hợp rơi dưới 50, mất xu hướng tuần 2 tuần liên tiếp, hoặc chạm stop-loss 12%. Khi VNI 8 tuần giảm mạnh, policy tăng cash.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": 100.0,
        "note": note,
        "methodology": {
            "status": "Best sạch hiện tại - được phép theo dõi/paper-trade",
            "target": "Ưu tiên rule bền, hạn chế overfit; mục tiêu thực tế là beat VNI dài hạn, chưa claim VNI+20/+30 6/6.",
            "entry": "Tín hiệu được chốt sau cuối tuần. Lệnh mua dùng giá tham chiếu cuối tuần, không đuổi giá nếu đầu tuần gap mạnh; chỉ đặt lệnh chẵn 100 cổ.",
            "selection": "Lọc cổ phiếu thường có thanh khoản đủ lớn, giá không quá thấp, xu hướng tuần còn tăng, RSI trong vùng 35-80, điểm tổng hợp >= 50; tối đa 6 mã, mỗi mã tối đa 20% NAV trước khi trôi giá.",
            "risk": "Có cash buffer 5%, stop-loss 12%, thoát khi điểm suy yếu hoặc xu hướng tuần gãy. Tiền mặt không tính lãi.",
            "audit": note,
        },
        "holdings": holdings,
    }
    policy["plannedOrders"] = build_next_session_plan(policy, metrics, cfg, curve)
    return policy


def build_r23_nav3b_policy(full_by_symbol: dict[str, pd.Series]) -> dict | None:
    curve = load_curve(R23_NAV3B_DIR)
    holdings_path = R23_NAV3B_DIR / "holdings.parquet"
    if curve.empty or not holdings_path.exists():
        return None
    metrics = metrics_from_period(curve, load_metrics(R23_NAV3B_DIR, curve))
    cfg_path = R23_NAV3B_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    ledger_positions = current_positions_from_ledger(curve, R23_NAV3B_DIR)
    holdings_raw = pd.read_parquet(holdings_path).copy()
    holdings_raw["date"] = pd.to_datetime(holdings_raw["date"])
    last_date = holdings_raw["date"].max()
    holdings = []
    for _, row in holdings_raw[holdings_raw["date"].eq(last_date)].sort_values("target_weight", ascending=False).iterrows():
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        screen = full_by_symbol.get(symbol)
        exchange = normalize_exchange(screen.get("exchange") if screen is not None else exchange_for_symbol(symbol))
        quote = latest_history_quote(symbol)
        current = num(quote.get("close"), num(row.get("price"), 0.0))
        if current <= 0:
            current = num(row.get("price"), 0.0)
        target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.88, 3))
        weight_pct = num(row.get("target_weight"), 0.0) * 100.0
        ledger_pos = ledger_positions.get(symbol, {})
        entry_price = num(ledger_pos.get("avgEntryPrice"), num(row.get("entry_price"), current))
        shares = int(ledger_pos.get("copyShares", 0) or 0)
        if shares < BOARD_LOT and entry_price > 0:
            shares = floor_to_board_lot((DEFAULT_NAV_VND * weight_pct / 100.0) / (entry_price * 1000.0))
        current_value_mil = shares * current / 1000 if current > 0 else 0.0
        cost_mil = shares * entry_price / 1000 if entry_price > 0 else 0.0
        pnl_mil = current_value_mil - cost_mil
        pnl_pct = (current / entry_price - 1) * 100 if entry_price > 0 and current > 0 else 0.0
        holdings.append({
            "symbol": symbol,
            "exchange": exchange,
            "status": "MUA",
            "rating": "R23_NAV3B",
            "suggestedWeight": round(weight_pct, 1),
            "currentPrice": round(current, 3),
            "priceAsOf": quote.get("date") or metrics["lastUpdate"],
            "signalDate": last_date.date().isoformat(),
            "entryDate": last_date.date().isoformat(),
            "entryPrice": round(entry_price, 3),
            "fillMode": "R23_fixed_NAV3B_policy",
            "entryGapPct": 0.0,
            "sellableFrom": None,
            "isSellableNow": True,
            "targetPrice": round(target, 3),
            "stopPrice": round(stop, 3),
            "modelShares": shares,
            "copyShares": shares,
            "modelValueMil": round(DEFAULT_NAV_VND / 1_000_000 * weight_pct / 100.0, 1),
            "currentValueMil": round(current_value_mil, 1),
            "costMil": round(cost_mil, 1),
            "currentPnlMil": round(pnl_mil, 1),
            "currentPnlPct": round(pnl_pct, 2),
            "industry": str(screen.get("industry_name") if screen is not None else "-"),
            "sleeve": str(screen.get("sector_group") if screen is not None else "-"),
            "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
            "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
            "plan": f"R23_NAV3B dang nam {shares:,} co phieu {symbol} quy doi theo NAV 1 ty.",
            "reasons": [
                "R23_NAV3B: M-core + conditional retention, co dinh live NAV 3 ty, cap 20% ADV.",
                "2021-2026 dat VNI+20 6/6 o stress 30bps; 15bps dat ca VNI+20 6/6 va VNI+30 6/6.",
                "Khong phai bang chung full-history 2012-2026; day la policy dashboard theo scope NAV nho anh vua chap nhan.",
            ],
        })
    invested = sum(item["suggestedWeight"] for item in holdings)
    note = (
        "R23_NAV3B: fixed live NAV 3 ty, cap 20% ADV. "
        "Recent 2021-2026 VNI+20 6/6 den 30bps; older 2016/2017/2019/2020 khong dat VNI+20."
    )
    audit = {
        "status": "R23_NAV3B",
        "passVni20": int(num(cfg.get("pass_vni20_2021_2026"), 6)),
        "passVni30": int(num(cfg.get("pass_vni30_2021_2026"), 6)),
        "cagr": metrics["historicalCagr"],
        "maxDrawdown": metrics["historicalMaxDrawdown"],
        "minEdgeVsVni": num(cfg.get("min_edge_2021_2026"), 30.56),
        "slippageBps": int(num(cfg.get("extra_slippage_bps"), 15)),
        "failYears": [],
    }
    policy = {
        "key": "r23_nav3b_mcore",
        "label": "R23_NAV3B",
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "R23: M-core weekly target, strict daily 100-lot, fixed deployment NAV 3 ty, cap 20% ADV.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": 100.0,
        "productionAudit": audit,
        "note": note,
        "methodology": {
            "status": "R23_NAV3B",
            "target": "Dashboard policy theo scope live NAV <=3 ty; muc tieu chinh la VNI+20 6/6 giai doan 2021-2026.",
            "entry": "Tin hieu tuan; khop strict daily 100-lot voi chi phi 15bps extra moi chieu trong so chinh.",
            "selection": "M-core BCTC-assisted + conditional retention, co cap thanh khoan theo 20% ADV tai NAV 3 ty.",
            "risk": "MaxDD gan -29% o 15bps; stress 30bps van giu VNI+20 6/6 nhung VNI+30 chi con 4/6.",
            "audit": note,
        },
        "holdings": holdings,
    }
    policy["plannedOrders"] = build_next_session_plan(policy, metrics, cfg, curve)
    return policy


def build_r46_bear_stop_policy(full_by_symbol: dict[str, pd.Series]) -> dict | None:
    curve = load_curve(R46_BEAR_STOP_DIR)
    holdings_path = R46_BEAR_STOP_DIR / "holdings.parquet"
    if curve.empty or not holdings_path.exists():
        return None
    metrics = metrics_from_period(curve, load_metrics(R46_BEAR_STOP_DIR, curve))
    cfg_path = R46_BEAR_STOP_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    ledger_positions = current_positions_from_ledger(curve, R46_BEAR_STOP_DIR)
    holdings_raw = pd.read_parquet(holdings_path).copy()
    holdings_raw["date"] = pd.to_datetime(holdings_raw["date"])
    last_date = holdings_raw["date"].max()
    holdings = []
    for _, row in holdings_raw[holdings_raw["date"].eq(last_date)].sort_values("target_weight", ascending=False).iterrows():
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        screen = full_by_symbol.get(symbol)
        exchange = normalize_exchange(screen.get("exchange") if screen is not None else exchange_for_symbol(symbol))
        quote = latest_history_quote(symbol)
        current = num(quote.get("close"), num(row.get("price"), 0.0))
        if current <= 0:
            current = num(row.get("price"), 0.0)
        target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.88, 3))
        weight_pct = num(row.get("target_weight"), 0.0) * 100.0
        ledger_pos = ledger_positions.get(symbol, {})
        entry_price = num(ledger_pos.get("avgEntryPrice"), num(row.get("entry_price"), current))
        shares = int(ledger_pos.get("copyShares", 0) or 0)
        if shares < BOARD_LOT and entry_price > 0:
            shares = floor_to_board_lot((DEFAULT_NAV_VND * weight_pct / 100.0) / (entry_price * 1000.0))
        current_value_mil = shares * current / 1000 if current > 0 else 0.0
        cost_mil = shares * entry_price / 1000 if entry_price > 0 else 0.0
        pnl_mil = current_value_mil - cost_mil
        pnl_pct = (current / entry_price - 1) * 100 if entry_price > 0 and current > 0 else 0.0
        holdings.append({
            "symbol": symbol,
            "exchange": exchange,
            "status": "MUA",
            "rating": "R46_BEAR_STOP",
            "suggestedWeight": round(weight_pct, 1),
            "currentPrice": round(current, 3),
            "priceAsOf": quote.get("date") or metrics["lastUpdate"],
            "signalDate": last_date.date().isoformat(),
            "entryDate": last_date.date().isoformat(),
            "entryPrice": round(entry_price, 3),
            "fillMode": "r46_gap_pullback_bear_stop_policy",
            "entryGapPct": 0.0,
            "sellableFrom": None,
            "isSellableNow": True,
            "targetPrice": round(target, 3),
            "stopPrice": round(stop, 3),
            "modelShares": shares,
            "copyShares": shares,
            "modelValueMil": round(DEFAULT_NAV_VND / 1_000_000 * weight_pct / 100.0, 1),
            "currentValueMil": round(current_value_mil, 1),
            "costMil": round(cost_mil, 1),
            "currentPnlMil": round(pnl_mil, 1),
            "currentPnlPct": round(pnl_pct, 2),
            "industry": str(screen.get("industry_name") if screen is not None else "-"),
            "sleeve": str(screen.get("sector_group") if screen is not None else "-"),
            "upsidePct": round((target / current - 1) * 100, 2) if current > 0 else 0.0,
            "downsidePct": round((1 - stop / current) * 100, 2) if current > 0 else 0.0,
            "plan": f"R46 Bear Stop dang nam {shares:,} co phieu {symbol} quy doi theo NAV 1 ty.",
            "reasons": [
                "R46 Bear Stop: M-core target, R46 execution gap 9%, buffer 1.5%, pullback 2 phien.",
                "Stop 5% chi bat khi Phase1 v4 regime = bear; binh thuong khong dung base stop.",
                "Anh chap nhan 15bps cho dashboard vi live NAV <5 ty; Claude audit PASS va plateau 4/4 PASS.",
            ],
        })
    invested = sum(item["suggestedWeight"] for item in holdings)
    note = (
        "R46 Bear Stop: user-approved dashboard default for NAV <5 ty under 15bps execution. "
        "Dashboard performance is displayed from 2021 onward; 20bps stress drops VNI+30 to 5/6."
    )
    audit = {
        "status": "R46_BEAR_STOP_15BPS",
        "passVni20": int(num(cfg.get("pass_vni20_2021_2026"), 6)),
        "passVni30": int(num(cfg.get("pass_vni30_2021_2026"), 6)),
        "fullPassVni20": int(num(cfg.get("pass_vni20_all"), 7)),
        "fullPassVni30": int(num(cfg.get("pass_vni30_all"), 7)),
        "cagr": metrics["historicalCagr"],
        "maxDrawdown": metrics["historicalMaxDrawdown"],
        "minEdgeVsVni": num(cfg.get("min_edge_2021_2026"), 32.77),
        "slippageBps": int(num(cfg.get("extra_slippage_bps"), 15)),
        "regimeStopSells": int(num(cfg.get("regime_stop_sells"), 18)),
        "failYears": [],
    }
    methodology_cards = [
        ["1. Vũ trụ cổ phiếu (Universe)", "Sàn áp dụng: HOSE, HNX, UPCoM. Chỉ cổ phiếu thường, KHÔNG ETF, trái phiếu, margin, bán khống, phái sinh. Tiền chưa dùng để cash, lãi 0%. Mã phải có thanh khoản trung bình 20 phiên (ADV20) tối thiểu 5 tỷ đồng/ngày. Có nhánh ngoại lệ cho mã 3-5 tỷ/ngày nếu composite score ≥ 70 và (ret13 ≥ 20% hoặc ret26 ≥ 30% hoặc đang trong cụm breakout ngành). Date constraint: score chỉ được tính từ dữ liệu có trước ngày tín hiệu (no future leak)."],
        ["2. Bộ lọc điểm số (Score Gates)", "Mỗi cổ phiếu có 7 score thành phần range 0-100 (fa_rank: tài chính; mom_rank: động lượng; rs_rank: sức mạnh tương đối; high_rank: gần đỉnh 52W; flow_rank: dòng tiền; industry_score: ngành; tech_score: kỹ thuật nền). Hard gates: composite_score ≥ 70 (tổng hợp BCTC + định giá + momentum theo công thức 0.30×Quality + 0.25×Valuation + 0.20×Catalyst + 0.25×Technical), industry_score ≥ 40, industry rank ≤ top 10, hard_gate==PASS (ROE bank ≥ 12%, ROA bank ≥ 0.8%, D/E phi ngân hàng ≤ 400%, không gap data), RSI14 trong khoảng 35-78 (mở rộng tới 95 nếu mã đang breakout sát đỉnh 52W)."],
        ["3. Điều kiện MUA (Entry — family sector_cluster)", "Sau khi qua universe + score gates, candidate được chấm score sector_cluster: ưu tiên mã sát đỉnh 52W (high_rank weight 0.45), cộng điểm mạnh cho cụm ngành breakout (35 điểm khi cluster_breakout_flag=1, +6×cluster_strength_4w), cộng momentum ret4 (14×ret4). Cluster breakout active khi trong cùng ngành có ≥ 2 mã đồng thời gần đỉnh ≥ 97% và ret4 ≥ -2%. Rule one-per-industry: nếu bật, mỗi ngành chỉ giữ 1 mã có tech_score cao nhất, chống concentration."],
        ["4. Quy mô vị thế (Position Sizing)", "Max holdings: 5 mã. Max weight per stock (M-core cap): 55% per signal date. Cap thanh khoản (R18 NAV-aware): max_weight_by_liq = (20% × ADV20) / NAV_deployment 3 tỷ; final weight = min(M-core weight, max_weight_by_liq). Trung bình exposure thực tế ~60%; cash residual cao trong tuần regime yếu. Không có riskoff exposure floor — cash buffer có thể lên 95%+ (như tuần 25/05/2026: MSB 5.525% + cash 94.475%)."],
        ["5. Điều kiện BÁN (Exit)", "Ba lớp: (a) Weekly rebalance: mỗi thứ 2, nếu weight hiện tại vượt target + 0.1% NAV thì bán phần dư tại giá mở cửa; (b) T+2.5 settle: mỗi lot phải giữ tối thiểu 4 phiên trước khi được bán (HOSE rule); (c) Bear regime daily stop 5%: nếu regime hôm nay = bear VÀ lot đã qua T+2.5 VÀ low_today ≤ entry × 0.95 → bán tại min(open, entry × 0.95). KHÔNG có stop trong regime bull/recovery/sideways."],
        ["6. Regime Gate (M-core Phase 1 v4)", "Classifier weekly phân loại VN-Index thành 5 trạng thái theo priority. BEAR: vni_ret_13w < -5% HOẶC (vni_ret_4w < -8% VÀ breadth_top200 < 30%). BULL_BROAD: breadth_top200 > 25% VÀ vni_ret_13w > 8% VÀ dispersion_4w < 15% VÀ vni_ret_4w > 0. BULL_NARROW: mega_cap_leadership > 8% VÀ vni_ret_13w > 3% VÀ breadth_top200 < 50% VÀ vni_ret_4w > 0. RECOVERY: breadth_recovery_2w ≥ 1 VÀ vni_ret_4w > 0 VÀ vni_ret_13w < 0. SIDEWAYS: mặc định. Daily date → weekly regime gần nhất qua backward asof (no future leak)."],
        ["7. Cash overlay (passive)", "Không có rule \"force 100% cash\" khi VNI rơi. Cash xuất hiện tự nhiên qua filter: khi regime weak, số mã qua được score gates giảm → sum target weight giảm → phần còn lại = cash. Cash yield giả định 0% (không gửi TGTK/bond — constraint của user). Model luôn cho phép giữ vị thế nếu có mã pass gate, không tự ý đứng ngoài."],
        ["8. Rebalance & Execution (R23 flexible exec)", "Tần suất: signal generate tối Chủ Nhật (sau Friday close), execute Monday. Quy tắc: nếu Monday open ≤ Friday close × 1.09 → mua tại open (HOSE thực tế cap ở ~6.5% do biên độ sàn 7%). Nếu gap > 9% → chờ pullback trong 2 phiên kế tiếp, limit = Friday close × 1.015. Nếu trong window low ≤ limit → fill tại min(open, limit). Hết window không khớp → skip (MISS_BUY). Lot size: 100 cổ phiếu, làm tròn xuống. Settlement T+2.5."],
        ["9. Chi phí giao dịch", "Phí buy: 0.15% phí + 0.15% slippage = 0.30% per side. Phí sell: 0.15% phí + 0.10% thuế TNCN + 0.15% slippage = 0.40% per side. Dashboard giả định extra slippage 15bps/side. Robust ở 15-18bps; tại 20bps recent +30pp gate giảm còn 5/6. Live broker phải đạt cost ≤ 18bps/side để giữ gate strict."],
        ["10. Hiệu suất verified (2021-2026)", "CAGR 76.47%, MaxDD -25.62%, Sharpe 2.19. Pass +30pp 6/6 năm: 2021 +153.93pp, 2022 +67.24pp, 2023 +34.34pp, 2024 +45.95pp, 2025 +33.11pp, 2026 YTD +32.77pp. T+2.5 violations: 0/1,821 trades. Full 2016-2026: CAGR 46.75%, pass +30pp 7/11 (fail 2016/2017/2019/2020 — pre-strategy era)."],
    ]
    policy = {
        "key": "r46_bear_stop_mcore",
        "label": "R46 Bear Stop",
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "R46: M-core weekly target, gap/pullback execution, 5% stop only in bear regime, strict daily 100-lot, fixed NAV 3 ty, cap 20% ADV.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": 100.0,
        "productionAudit": audit,
        "note": note,
        "methodology": {
            "status": "R46 Bear Stop - dashboard default",
            "target": "Policy theo scope live NAV <5 ty; muc tieu dashboard la VNI+20/VNI+30 6/6 giai doan 2021-hien tai o 15bps.",
            "entry": "Tin hieu tuan; mua theo R46 execution: gap toi da 9%, buffer 1.5%, cho pullback toi da 2 phien; lenh lam tron lo 100.",
            "selection": "Dung M-core target/retention, cap thanh khoan 20% ADV tai NAV 3 ty; khong ETF/bond/margin/short.",
            "risk": "15bps duoc anh chap nhan cho dashboard. Neu chi phi thuc te len 20bps, VNI+30 recent con 5/6 nen can track slippage that.",
            "audit": note,
            "cards": methodology_cards,
        },
        "holdings": holdings,
    }
    policy["plannedOrders"] = build_next_session_plan(policy, metrics, cfg, curve)
    return policy


def load_t2_v13_status() -> dict:
    status_path = OUT / "beat_vni30_parallel" / "technical_t2_state_machine" / "vni30_lowercap_v13" / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_t2_v13_planned_orders(
    holdings: list[dict],
    curve: pd.DataFrame,
    metrics: dict,
    full_by_symbol: dict[str, pd.Series],
) -> dict:
    targets_path = T2_VNI30_RESEARCH_DIR / "weekly_targets.parquet"
    if not targets_path.exists():
        return {"rows": [], "summary": "Chưa có target tuần tới cho T2 V13."}
    targets = pd.read_parquet(targets_path).copy()
    if targets.empty:
        return {"rows": [], "summary": "Chưa có target tuần tới cho T2 V13."}
    targets["date"] = pd.to_datetime(targets["date"])
    latest_target_date = targets["date"].max()
    target_rows = targets[targets["date"].eq(latest_target_date)].copy()
    signal_friday = target_rows["signal_friday"].dropna().astype(str).max() if "signal_friday" in target_rows else None
    plan_date = latest_target_date.date().isoformat()
    signal_date = pd.Timestamp(signal_friday).date().isoformat() if signal_friday else metrics.get("lastUpdate")

    current_positions = current_positions_from_ledger(curve, T2_VNI30_RESEARCH_DIR)
    current_holding_map = {str(h.get("symbol", "")).upper(): h for h in holdings}
    target_map = {str(row.get("symbol", "")).upper(): row for _, row in target_rows.iterrows()}
    all_symbols = sorted(set(current_positions) | set(target_map))
    price_dates = []
    rows = []
    buy_orders = 0
    sell_orders = 0
    executed_orders = 0
    skipped_orders = 0
    waiting_orders = 0

    for symbol in all_symbols:
        target_row = target_map.get(symbol)
        current_pos = current_positions.get(symbol, {})
        current_shares = int(current_pos.get("copyShares", 0) or 0)
        quote = latest_history_quote(symbol)
        if quote.get("date"):
            price_dates.append(str(quote["date"]))
        signal_quote = history_quote_at_or_before(symbol, signal_date)
        current = num(quote.get("close"), num(signal_quote.get("close"), 0.0))
        ref_close = num(signal_quote.get("close"), current)
        ref_date = signal_quote.get("date") or signal_date
        exchange = normalize_exchange(exchange_for_symbol(symbol))
        screen = full_by_symbol.get(symbol)
        target_price = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop_price = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.9, 3))
        entry_price = num(current_pos.get("avgEntryPrice"), num(current_holding_map.get(symbol, {}).get("entryPrice"), current))
        target_weight = num(target_row.get("weight"), 0.0) * 100.0 if target_row is not None else 0.0
        entry_band = num(target_row.get("entry_band"), 0.01) if target_row is not None else 0.01
        max_buy_band = effective_entry_gap_threshold(symbol, entry_band, exchange)
        max_buy = ref_close * (1.0 + max_buy_band) if ref_close > 0 else 0.0
        limit_price = ref_close if ref_close > 0 else current
        current_weight = current_shares * current / 1000.0 / 1000.0 * 100.0 if current > 0 else 0.0
        target_shares = target_copy_shares(target_weight, current)
        delta_shares = target_shares - current_shares
        order_shares = 0
        action = "GIỮ"
        status = "THEO DÕI"
        note = "Không đổi tỷ trọng; tiếp tục giữ theo target kỹ thuật hiện tại."
        execution_date = None
        execution_price = None
        execution_mode = None
        executed = False

        if abs(delta_shares) >= BOARD_LOT and delta_shares > 0:
            buy_orders += 1
            order_shares = floor_to_board_lot(delta_shares)
            action = "MUA MỚI" if current_shares < BOARD_LOT else "MUA THÊM"
            if current <= max_buy:
                status = "CÓ THỂ KHỚP"
                note = f"Đặt limit trong vùng {limit_price:.2f}k-{max_buy:.2f}k; không mua đuổi nếu vượt biên kỹ thuật {max_buy_band * 100:.1f}%."
            else:
                status = "CHỜ GIÁ"
                note = f"Giá hiện tại cao hơn vùng mua; chờ quay về gần {limit_price:.2f}k trong tối đa 2 phiên, không quay lại thì bỏ qua."
            fill = planned_buy_fill(
                symbol=symbol,
                plan_date=plan_date,
                latest_price_date=quote.get("date"),
                reference_close=ref_close,
                max_gap_band=max_buy_band,
                limit_price=limit_price,
                pullback_days=2,
            )
            if fill.get("filled"):
                executed_orders += 1
                executed = True
                execution_date = fill.get("entry_date")
                execution_price = num(fill.get("entry_price"), current)
                execution_mode = fill.get("fill_mode")
                status = "ĐÃ KHỚP"
                mode_label = "mở cửa" if execution_mode == "open" else "limit khi giá quay về"
                note = f"Đã khớp {mode_label} @ {execution_price:.2f}k; dùng kế hoạch {plan_date}, tín hiệu {signal_date}."
            elif fill.get("reason") != "pre_open":
                if fill.get("pending"):
                    waiting_orders += 1
                    status = "CHỜ GIÁ"
                    note = f"Chưa khớp; tiếp tục đặt quanh {limit_price:.2f}k trong cửa sổ tối đa 2 phiên."
                else:
                    skipped_orders += 1
                    order_shares = 0
                    status = "BỎ QUA"
                    note = f"Không khớp vùng mua {limit_price:.2f}k trong cửa sổ 2 phiên; bỏ qua lệnh này."
        elif abs(delta_shares) >= BOARD_LOT and delta_shares < 0:
            sell_orders += 1
            order_shares = floor_to_board_lot(min(current_shares, abs(delta_shares)))
            action = "BÁN HẾT" if target_shares < BOARD_LOT else "BÁN 1 PHẦN"
            status = "BÁN MỞ CỬA"
            note = "Giảm tỷ trọng theo target mới của state machine; lệnh bán đã qua kiểm tra T+2.5 trong backtest."
            fill = planned_sell_fill(symbol=symbol, plan_date=plan_date, latest_price_date=quote.get("date"))
            if fill.get("filled"):
                executed_orders += 1
                executed = True
                execution_date = fill.get("exit_date")
                execution_price = num(fill.get("exit_price"), current)
                execution_mode = fill.get("fill_mode")
                status = "ĐÃ BÁN"
                note = f"Đã bán mở cửa @ {execution_price:.2f}k theo kế hoạch {plan_date}."
            elif fill.get("reason") != "pre_open":
                waiting_orders += 1
                status = "CHỜ DỮ LIỆU"
                note = "Chưa có nến phiên thực hiện để xác nhận giá bán."

        order_price = execution_price or (limit_price if status == "CHỜ GIÁ" and limit_price > 0 else current)
        order_value_mil = order_shares * order_price / 1000.0 if order_shares and order_price > 0 else 0.0
        rows.append({
            "planDate": plan_date,
            "signalDate": signal_date,
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "status": status,
            "currentPrice": round(current, 3) if current > 0 else None,
            "priceAsOf": quote.get("date") or ref_date,
            "entryPrice": round(entry_price, 3) if entry_price > 0 else None,
            "referenceClose": round(ref_close, 3) if ref_close > 0 else None,
            "referenceDate": ref_date,
            "maxBuyPrice": round(max_buy, 3) if max_buy > 0 else None,
            "limitPrice": round(limit_price, 3) if limit_price > 0 else None,
            "gapPct": round((current / ref_close - 1.0) * 100.0, 2) if current > 0 and ref_close > 0 else None,
            "executionDate": execution_date,
            "executionPrice": round(execution_price, 3) if execution_price else None,
            "executionMode": execution_mode,
            "executed": executed,
            "baseGapThresholdPct": round(entry_band * 100.0, 2),
            "effectiveGapThresholdPct": round(max_buy_band * 100.0, 2),
            "currentWeight": round(current_weight, 2),
            "targetWeight": round(target_weight, 2),
            "deltaWeight": round(target_weight - current_weight, 2),
            "currentCopyShares": current_shares,
            "targetCopyShares": target_shares,
            "orderShares": order_shares,
            "orderValueMil": round(order_value_mil, 1),
            "targetPrice": round(target_price, 3),
            "stopPrice": round(stop_price, 3),
            "note": note,
        })

    latest_price_date = max(price_dates) if price_dates else signal_date
    stage = plan_stage(latest_price_date, plan_date)
    if buy_orders or sell_orders:
        if stage == "pre_open":
            summary = f"{buy_orders} lệnh mua, {sell_orders} lệnh bán dự kiến cho {plan_date}; vùng mua dùng close thứ 6 và biên 1%-3% theo từng sleeve."
        else:
            pending_orders = max(0, buy_orders + sell_orders - executed_orders - skipped_orders)
            parts = []
            if executed_orders:
                parts.append(f"{executed_orders} lệnh đã xử lý")
            if pending_orders:
                parts.append(f"{pending_orders} lệnh còn chờ giá/dữ liệu")
            if skipped_orders:
                parts.append(f"{skipped_orders} lệnh bỏ qua")
            summary = f"Kế hoạch {plan_date} đã được đánh giá theo giá đến {latest_price_date}: " + ", ".join(parts or ["không còn lệnh cần xử lý"]) + "."
    else:
        summary = f"Không có lệnh mới cho {plan_date}; giữ danh mục hiện tại."
    return {
        "asOf": latest_price_date,
        "planDate": plan_date,
        "signalDate": signal_date,
        "stage": stage,
        "source": "technical_t2_weekly_targets",
        "entryGapThresholdPct": None,
        "priceLimitAware": True,
        "pullbackDays": 2,
        "summary": summary,
        "rows": sorted(rows, key=lambda row: (0 if str(row["action"]).startswith("BÁN") else 1 if str(row["action"]).startswith("MUA") else 2, row["symbol"])),
    }


def build_t2_vni30_research_policy(full_by_symbol: dict[str, pd.Series]) -> dict | None:
    curve = load_curve(T2_VNI30_RESEARCH_DIR)
    holdings_path = T2_VNI30_RESEARCH_DIR / "holdings.parquet"
    if curve.empty or not holdings_path.exists():
        return None
    metrics = load_metrics(T2_VNI30_RESEARCH_DIR, curve)
    cfg_path = T2_VNI30_RESEARCH_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    status = load_t2_v13_status()
    best = status.get("best_metrics", {})
    audit = {
        "status": "RESEARCH_ONLY",
        "passVni20": int(num(best.get("pass_vni20"), cfg.get("pass_vni20", 0))),
        "passVni30": int(num(best.get("pass_vni30"), cfg.get("pass_vni30", 0))),
        "cagr": num(best.get("cagr"), metrics["historicalCagr"]),
        "minEdgeVsVni": num(best.get("min_edge_vs_vni"), cfg.get("min_edge_vs_vni", 0.0)),
        "failYears": [],
        "stressVni20Pass6Cells": int(num(status.get("stress_vni20_pass6_cells"), 0)),
        "stressVni30Pass6Cells": int(num(status.get("stress_vni30_pass6_cells"), 0)),
    }
    ledger_positions = current_positions_from_ledger(curve, T2_VNI30_RESEARCH_DIR)
    holdings_raw = pd.read_parquet(holdings_path).copy()
    holdings_raw["date"] = pd.to_datetime(holdings_raw["date"])
    last_date = holdings_raw["date"].max() if not holdings_raw.empty else pd.Timestamp(metrics["lastUpdate"])
    holdings_by_symbol = {
        str(row.get("symbol", "")).upper(): row
        for _, row in holdings_raw[holdings_raw["date"].eq(last_date)].iterrows()
    }
    holdings = []
    for symbol, pos in sorted(ledger_positions.items()):
        copy_shares = int(pos.get("copyShares", 0) or 0)
        if copy_shares < BOARD_LOT:
            continue
        screen = full_by_symbol.get(symbol)
        quote = latest_history_quote(symbol)
        row = holdings_by_symbol.get(symbol)
        current = num(quote.get("close"), num(row.get("price") if row is not None else 0.0, 0.0))
        entry_price = num(pos.get("avgEntryPrice"), num(row.get("entry_price") if row is not None else 0.0, current))
        entry_date_raw = row.get("entry_date") if row is not None else None
        entry_date = pd.Timestamp(entry_date_raw).date().isoformat() if pd.notna(entry_date_raw) else last_date.date().isoformat()
        current_value_mil = copy_shares * current / 1000.0 if current > 0 else 0.0
        cost_mil = copy_shares * entry_price / 1000.0 if entry_price > 0 else 0.0
        pnl_mil = current_value_mil - cost_mil
        pnl_pct = (current / entry_price - 1.0) * 100.0 if current > 0 and entry_price > 0 else 0.0
        target = num(screen.get("target_price_k") if screen is not None else 0.0, round(current * 1.2, 3))
        stop = num(screen.get("stop_price_k") if screen is not None else 0.0, round(current * 0.9, 3))
        weight_pct = current_value_mil / 1000.0 * 100.0
        holdings.append({
            "symbol": symbol,
            "exchange": normalize_exchange(screen.get("exchange") if screen is not None else exchange_for_symbol(symbol)),
            "status": "MUA",
            "rating": "ĐANG NẮM THEO T2 VNI+30",
            "suggestedWeight": round(weight_pct, 1),
            "currentPrice": round(current, 3),
            "priceAsOf": quote.get("date") or metrics["lastUpdate"],
            "signalDate": last_date.date().isoformat(),
            "entryDate": entry_date,
            "entryPrice": round(entry_price, 3),
            "fillMode": "limit_zone_daily_lot",
            "entryGapPct": 0.0,
            "sellableFrom": None,
            "isSellableNow": True,
            "targetPrice": round(target, 3),
            "stopPrice": round(stop, 3),
            "modelShares": copy_shares,
            "copyShares": copy_shares,
            "modelValueMil": round(current_value_mil, 1),
            "currentValueMil": round(current_value_mil, 1),
            "costMil": round(cost_mil, 1),
            "currentPnlMil": round(pnl_mil, 1),
            "currentPnlPct": round(pnl_pct, 2),
            "industry": str(screen.get("industry_name") if screen is not None else "-"),
            "sleeve": "Pure technical T2",
            "upsidePct": round((target / current - 1.0) * 100.0, 2) if current > 0 else 0.0,
            "downsidePct": round((1.0 - stop / current) * 100.0, 2) if current > 0 else 0.0,
            "plan": f"Model T2 đang nắm {copy_shares:,} cổ phiếu {symbol} quy đổi NAV 1 tỷ, giá vốn bình quân {entry_price:.2f}k.",
            "reasons": [
                "Pure technical: chỉ dùng giá, thanh khoản, sức mạnh tương đối, breadth và trạng thái thị trường tới cuối tuần trước.",
                "Không dùng BCTC, không dùng ngành hiện tại, không ETF/bond/margin/short.",
                "Backtest strict daily-lot: lệnh chẵn 100 cổ, có T+2.5 và trượt giá 0,15% mỗi chiều.",
            ],
        })

    invested = sum(item["suggestedWeight"] for item in holdings)
    note = (
        f"T2 V13 đạt VNI+20 {audit['passVni20']}/6 và VNI+30 {audit['passVni30']}/6 trong strict daily-lot, "
        f"CAGR {audit['cagr']:.1f}%, min edge {audit['minEdgeVsVni']:.1f} điểm %. "
        "Hiển thị để anh paper-trade/review; chưa mở production vì còn cảnh báo single-position cap 85% và VNI+30 nhạy với stress chi phí."
    )
    methodology_cards = [
        ["1. Tài sản được phép", "Chỉ cổ phiếu thường HOSE/HNX/UPCoM trong dữ liệu giá sạch. Không ETF, không trái phiếu, không margin, không bán khống. Phần không mua để cash, lãi cash = 0%."],
        ["2. Mục tiêu kiểm tra", "Gate nghiên cứu hiện tại: mỗi năm phải hơn VN-Index ít nhất 30 điểm phần trăm. V13 đang đạt 6/6 năm trên daily-lot, min edge 31,46 điểm phần trăm."],
        ["3. Dữ liệu đầu vào", "Thuần kỹ thuật: giá OHLCV, thanh khoản 20 phiên, sức mạnh tương đối nhiều khung, số mã tăng/giảm của thị trường và trạng thái VN-Index. Không dùng BCTC nên không có lỗi delay ngày công bố."],
        ["4. Thanh khoản", "Mã phải có giá trị giao dịch bình quân 20 phiên tối thiểu 3 tỷ/ngày. Lệnh được mô phỏng theo lô 100 cổ phiếu và có kiểm tra khả năng thực thi theo thanh khoản."],
        ["5. State thị trường", "Mỗi cuối tuần model phân loại trạng thái như recovery, risk-off hoặc trend bằng VNI/breadth. Trạng thái chỉ đổi sau tín hiệu đã chốt cuối tuần, không biết trước diễn biến tuần sau."],
        ["6. Chấm điểm cổ phiếu", "Ưu tiên cổ phiếu có sức mạnh tương đối tốt, giá còn giữ xu hướng, thanh khoản cải thiện và không bị breadth shock. Cùng một công thức dùng cho toàn bộ 2021-2026, không có rule riêng theo năm hay theo mã."],
        ["7. Chọn danh mục", "Tối đa 3 mã mục tiêu. Core sleeve có thể lên tới 85% NAV, secondary technical sleeve khoảng 8,5% mỗi mã. Đây là điểm mạnh về CAGR nhưng cũng là cảnh báo tập trung vị thế."],
        ["8. Vùng mua", "Lệnh mua dùng close thứ 6 làm tham chiếu. Core thường mua tối đa quanh close +1%; secondary tối đa quanh close +3%. Nếu thứ 2 tăng vượt vùng này thì chờ T2-T4 quay về vùng mua, không quay lại thì bỏ qua."],
        ["9. Bán và rebalance", "Bán khi target tuần mới giảm tỷ trọng hoặc đổi mã. Rebalance band 12 điểm phần trăm để tránh đảo lệnh quá nhiều. Lệnh bán luôn kiểm tra T+2.5 trong backtest."],
        ["10. Chi phí kiểm tra", "Số chính dùng trượt giá 0,15% mỗi chiều. Stress 0,20%-0,30% vẫn giữ VNI+20 khá tốt nhưng VNI+30 chỉ pass một phần stress, nên chưa gọi là production an toàn tuyệt đối."],
        ["11. Rủi ro cần theo dõi", "MaxDD khoảng -37%, vị thế đơn tối đa 85%, remove-top-winner còn làm VNI+30 rơi xuống 4/6. Vì vậy dashboard gắn nhãn Research/Paper-trade cho tới khi Claude review độc lập xong."],
    ]
    policy = {
        "key": "technical_t2_vni30_v13",
        "label": "T2 VNI+30 Research",
        "historicalCagr": metrics["historicalCagr"],
        "historicalSharpe": metrics["historicalSharpe"],
        "historicalMaxDrawdown": metrics["historicalMaxDrawdown"],
        "totalReturn": metrics["totalReturn"],
        "lastUpdate": metrics["lastUpdate"],
        "stopMode": "Bán/giảm tỷ trọng khi target tuần mới đổi; chỉ mua trong vùng limit theo close thứ 6; strict T+2.5 và lô 100 cổ.",
        "totalSuggestedWeight": round(invested, 1),
        "cashBuffer": round(max(0.0, 100.0 - invested), 1),
        "componentWeight": 100.0,
        "productionAudit": audit,
        "note": note,
        "methodology": {
            "status": "Research/Paper-trade candidate - chưa production",
            "target": "Beat VN-Index +30 điểm phần trăm mỗi năm; sau khi review sạch mới cân nhắc production.",
            "entry": "Mua bằng limit quanh close thứ 6, core +1%, secondary +3%, không mua đuổi nếu vượt vùng.",
            "selection": "Pure technical state machine, không BCTC, không ngành, không year/ticker rescue.",
            "risk": "Còn rủi ro tập trung vị thế và nhạy chi phí; dashboard đang hiển thị để theo dõi có kiểm soát.",
            "audit": note,
            "cards": methodology_cards,
        },
        "holdings": holdings,
    }
    policy["plannedOrders"] = build_t2_v13_planned_orders(policy["holdings"], curve, metrics, full_by_symbol)
    return policy


def main() -> None:
    full = load_full_screening()
    full_by_symbol = row_lookup(full)
    r46 = build_r46_bear_stop_policy(full_by_symbol)
    r23 = build_r23_nav3b_policy(full_by_symbol)
    t2_v13 = build_t2_vni30_research_policy(full_by_symbol)
    tier_a = build_tier_a_baseline_policy(full_by_symbol)
    policies = [policy for policy in [r46, r23, t2_v13, tier_a] if policy]
    if not policies:
        policies = [build_policy(spec, full_by_symbol) for spec in POLICY_SPECS[:1]]
    default_policy = r46.get("key") if r46 else (r23.get("key") if r23 else (t2_v13.get("key") if t2_v13 else (tier_a.get("key") if tier_a else "phase18_meanreversion_boost")))
    payload = {
        "memos": build_latest_trade_memos(full_by_symbol),
        "strategyPolicies": policies,
        "defaultPolicy": default_policy,
        "plannedOrders": policies[0].get("plannedOrders") if policies else {},
        "initialCapital": {
            "amount_vnd": DEFAULT_NAV_VND,
            "start_date": "2021-01-01",
        },
        "portfolioPlan": {
            "rule": "Dashboard này chỉ hiển thị lệnh copy theo policy đang chọn. Không trộn thêm tín hiệu screening rời rạc để tránh mâu thuẫn.",
            "nav_vnd": DEFAULT_NAV_VND,
        },
    }
    analysis_text = (
        "window.SCREENING_DEEP_ANALYSIS = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n"
    )
    atomic_write_text(DASH / "analysis.js", analysis_text)
    atomic_write_text(OUT / "deep_analysis.json", json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {DASH / 'analysis.js'} with {len(policies)} execution policies")


if __name__ == "__main__":
    main()
