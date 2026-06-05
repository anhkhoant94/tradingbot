import json
import math
import re
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"
PREVIEW = DASH / "_preview"
PT_DIR = ROOT / "output" / "beat_vni30_parallel" / "paper_trade_v4_r46"
LEDGER_REBASE_START_DATE = "2021-01-01"
LEDGER_REBASE_NAV_BIL = 1.0


parser = argparse.ArgumentParser(description="Build the Ez Trading v7 static dashboard HTML.")
parser.add_argument(
    "--out",
    type=Path,
    default=PREVIEW / "option-c-glass.html",
    help="Output HTML path. Use dashboard/index.html for production.",
)
args = parser.parse_args()


def load_js_object(path: Path, var_name: str):
    text = path.read_text(encoding="utf-8")
    match = re.search(re.escape(var_name) + r"\s*=\s*(.*?);\s*$", text, re.S)
    if not match:
        raise ValueError(f"Cannot parse {var_name} from {path}")
    return json.loads(match.group(1))


def load_json_or(path: Path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return fallback


def load_jsonl_last_or(path: Path, fallback):
    try:
        if path.exists():
            lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if lines:
                return json.loads(lines[-1])
    except Exception:
        pass
    return fallback


def fmt_num(value, digits=1):
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(x):
        return "-"
    s = f"{x:,.{digits}f}"
    if digits == 0:
        s = f"{x:,.0f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_money_m(value):
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if x >= 0 else "-"
    return f"{sign}{fmt_num(abs(x), 1)} tr"


def fmt_bil_from_m(value):
    if value is None:
        return "-"
    try:
        x = float(value) / 1000
    except (TypeError, ValueError):
        return "-"
    return f"{fmt_num(x, 3)} tỷ"


def pct(value, digits=1):
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if x > 0 else ""
    return f"{sign}{fmt_num(x, digits)}%"


def as_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def scale_lot(value, scale):
    raw = as_float(value)
    if raw is None:
        return value
    if abs(raw) < 1e-9:
        return 0
    scaled = int(round(raw * scale / 100.0) * 100)
    if scaled == 0 and raw > 0:
        scaled = 100
    return scaled


def next_monday(date_text):
    try:
        d = datetime.strptime(str(date_text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d = datetime.now().date()
    days = (7 - d.weekday()) % 7
    if days == 0:
        days = 7
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def latest_ohlc(symbol):
    path = ROOT / ".cache" / "backtest" / "history_clean" / f"{str(symbol).upper()}.parquet"
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {}
    if df.empty:
        return {}
    tcol = "time" if "time" in df.columns else "date"
    df = df.copy()
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
    df = df.dropna(subset=[tcol]).sort_values(tcol)
    if df.empty:
        return {}
    row = df.iloc[-1]
    close = as_float(row.get("close"))
    return {
        "date": pd.Timestamp(row[tcol]).date().isoformat(),
        "open": as_float(row.get("open"), close),
        "high": as_float(row.get("high"), close),
        "low": as_float(row.get("low"), close),
        "close": close,
    }


def latest_regime(as_of):
    paths = [
        ROOT / "output" / "beat_vni30_parallel" / "r46_live_forecast" / "regime_features_weekly.parquet",
        ROOT / ".cache" / "backtest" / "regime_features_weekly.parquet",
    ]
    for path in paths:
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty or "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        df = df.dropna(subset=["date"]).sort_values("date")
        if as_of:
            df = df[df["date"].le(pd.Timestamp(as_of).normalize())]
        if df.empty:
            continue
        row = df.iloc[-1]
        regime = str(row.get("regime") or row.get("market_regime") or "").upper()
        return {"date": row["date"].date().isoformat(), "regime": regime or "UNKNOWN"}
    return {"date": None, "regime": "UNKNOWN"}


def effective_gap_threshold(exchange, base_gap):
    limits = {"HOSE": 0.07, "HSX": 0.07, "HNX": 0.10, "UPCOM": 0.15}
    ex = str(exchange or "").upper()
    if ex in limits:
        return max(0.0, min(float(base_gap), limits[ex] - 0.005))
    return float(base_gap)


dashboard_data = load_js_object(DASH / "data.js", "window.SCREENING_DASHBOARD_DATA")
analysis = load_js_object(DASH / "analysis.js", "window.SCREENING_DEEP_ANALYSIS")
history = load_js_object(DASH / "history.js", "window.MODEL_TRADE_HISTORY")
live_status = load_json_or(DASH / "dashboard_live_update_status.json", {})
forecast_status_path = DASH / "r46_forecast.json"
forecast_status = load_json_or(forecast_status_path, {})
full_universe_status = load_json_or(DASH / "full_universe_live_update_status.json", load_json_or(ROOT / "output" / "full_universe_live_update_status.json", {}))
policy_config = load_json_or(ROOT / "output" / "dashboard_policies" / "r46_bear_stop_mcore" / "config.json", {})
pt_state = load_json_or(PT_DIR / "paper_trade_state.json", {
    "start_date": "2026-06-01",
    "end_date": "2026-06-29",
    "weekly_checkpoint_due": {
        "week_1": "2026-06-01",
        "week_2": "2026-06-08",
        "week_3": "2026-06-15",
        "week_4": "2026-06-22",
        "week_4_close": "2026-06-29",
    },
})
signal_w1 = load_json_or(PT_DIR / "signal_week_1_20260601.json", {
    "execution_date": "2026-06-01",
    "nav_virtual_vnd": 1_000_000_000,
    "cash_pct": 94.49,
    "exposure_pct": 5.51,
    "targets": [{
        "symbol": "MSB",
        "target_weight": 0.05525,
        "target_shares_round_lot_100": 3600,
        "prev_close_vnd_per_share": 15000,
    }],
})
paper_log_last = load_jsonl_last_or(PT_DIR / "paper_trade_log.jsonl", {})

policy = next(p for p in analysis["strategyPolicies"] if p["key"] == "r46_bear_stop_mcore")
hist = next(p for p in history["policies"] if p["key"] == "r46_bear_stop_mcore")
quotes = live_status.get("quotes", {})

# --- Regime: lấy từ tín hiệu thật, KHÔNG hardcode ---
raw_regime = str(signal_w1.get("regime_at_signal", "") or "")
if "to_be_classified" in raw_regime:
    regime_label = "Chờ phân loại"
elif raw_regime:
    regime_label = raw_regime.replace("_", " ").upper()
else:
    regime_label = "—"

holdings = []
for row in policy.get("holdings", []):
    sym = str(row.get("symbol", "")).upper()
    quote = quotes.get(sym) or {}
    live_px = quote.get("close") or row.get("currentPrice")
    live_date = quote.get("date") or row.get("priceAsOf")
    shares = int(row.get("copyShares") or row.get("modelShares") or 0)
    entry = float(row.get("entryPrice") or 0)
    current = float(live_px or 0)
    cost_m = shares * entry / 1000 if shares and entry else None
    value_m = shares * current / 1000 if shares and current else None
    pnl_m = value_m - cost_m if value_m is not None and cost_m is not None else None
    pnl_pct = pnl_m / cost_m * 100 if pnl_m is not None and cost_m else None
    holdings.append({
        **row,
        "symbol": sym,
        "currentPrice": current,
        "priceAsOf": live_date,
        "copyShares": shares,
        "valueMil": value_m,
        "costMilCalc": cost_m,
        "pnlMilCalc": pnl_m,
        "pnlPctCalc": pnl_pct,
    })

target = signal_w1["targets"][0] if signal_w1.get("targets") else {}
paper_symbol = target.get("symbol", "MSB")
paper_quote = quotes.get(paper_symbol, {})
paper_signal_px = (target.get("prev_close_vnd_per_share") or 0) / 1000
paper_fresh_px = paper_quote.get("close")
paper_shares = int(target.get("target_shares_round_lot_100") or 0)
paper_nav_start_vnd = float(signal_w1.get("nav_virtual_vnd") or pt_state.get("nav_start_vnd") or 1_000_000_000)
paper_buy_cost_pct = float(pt_state.get("cost_assumption_pct_per_side", {}).get("buy_cost", 0.30)) / 100.0
paper_state_pos = pt_state.get("current_position", {}) or {}
paper_state_holding = next(
    (h for h in paper_state_pos.get("holdings", []) if str(h.get("symbol", "")).upper() == str(paper_symbol).upper()),
    None,
)


def derive_paper_fill_from_history():
    if not paper_symbol or not signal_w1.get("execution_date") or not paper_signal_px:
        return None
    cache_path = ROOT / ".cache" / "backtest" / "history_clean" / f"{paper_symbol}.parquet"
    if not cache_path.exists():
        return None
    try:
        df = pd.read_parquet(cache_path)
        tcol = "time" if "time" in df.columns else "date"
        df[tcol] = pd.to_datetime(df[tcol]).dt.normalize()
        row = df[df[tcol].eq(pd.Timestamp(signal_w1["execution_date"]).normalize())]
        if row.empty:
            return None
        r = row.iloc[0]
        open_px = float(r["open"])
        max_open = paper_signal_px * (1.0 + float(target.get("max_buy_gap_pct", 9.0)) / 100.0)
        if open_px <= max_open:
            return {"entry_price_k": open_px, "entry_date": signal_w1["execution_date"], "fill_reason": "open_gap_ok"}
    except Exception:
        return None
    return None


paper_fill = None
if paper_state_holding:
    paper_shares = int(paper_state_holding.get("shares") or paper_shares)
    entry_px_vnd = paper_state_holding.get("entry_px_vnd")
    if entry_px_vnd:
        paper_fill = {
            "entry_price_k": float(entry_px_vnd) / 1000.0,
            "entry_date": paper_state_holding.get("entry_date") or signal_w1.get("execution_date"),
            "fill_reason": "state_position",
        }
elif paper_shares:
    paper_fill = derive_paper_fill_from_history()

paper_executed = bool(paper_fill)
paper_entry_px = float(paper_fill["entry_price_k"]) if paper_fill else paper_signal_px
paper_entry_date = paper_fill.get("entry_date") if paper_fill else signal_w1.get("execution_date")
paper_value_m = paper_shares * float(paper_fresh_px or 0) / 1000 if paper_shares and paper_fresh_px else None
paper_ref_cost_m = paper_shares * paper_entry_px * (1.0 + paper_buy_cost_pct) / 1000 if paper_shares and paper_entry_px else None
paper_position_pnl_m = paper_value_m - paper_ref_cost_m if paper_value_m is not None and paper_ref_cost_m else None
paper_position_pnl_pct = paper_position_pnl_m / paper_ref_cost_m * 100 if paper_position_pnl_m is not None and paper_ref_cost_m else None
if paper_state_pos.get("cash_vnd") and paper_executed:
    paper_cash_after_m = float(paper_state_pos["cash_vnd"]) / 1e6
elif paper_ref_cost_m is not None and paper_executed:
    paper_cash_after_m = (paper_nav_start_vnd / 1e6) - paper_ref_cost_m
else:
    raw_cash_after = signal_w1.get("cash_after_planned_buy_vnd")
    paper_cash_after_m = raw_cash_after / 1e6 if raw_cash_after else None
paper_nav_m = paper_cash_after_m + paper_value_m if paper_cash_after_m is not None and paper_value_m is not None else None
paper_nav_pnl_m = paper_nav_m - 1000 if paper_nav_m is not None else None
paper_nav_pnl_pct = paper_nav_pnl_m / 1000 * 100 if paper_nav_pnl_m is not None else None
paper_cash_pct = paper_cash_after_m / (paper_nav_start_vnd / 1e6) * 100 if paper_cash_after_m is not None else signal_w1.get("cash_pct")
paper_exposure_pct = paper_value_m / paper_nav_m * 100 if paper_value_m is not None and paper_nav_m else signal_w1.get("exposure_pct")

trades_full = list(hist.get("trades", []))
curve = [
    r for r in hist.get("equityCurve", [])
    if r.get("date") and r.get("navBil") and r.get("vniClose")
]
curve_2021 = [r for r in curve if r["date"] >= "2021-01-01"] or curve
nav_by_date = {r["date"]: float(r["navBil"]) for r in curve if r.get("date") and r.get("navBil")}
ledger_base_curve = [
    r for r in curve
    if r.get("date") and r.get("navBil") and r["date"] >= LEDGER_REBASE_START_DATE
]
if not ledger_base_curve:
    ledger_base_curve = curve
ledger_base_nav_bil = float(ledger_base_curve[0]["navBil"]) if ledger_base_curve else 1.0
ledger_scale = LEDGER_REBASE_NAV_BIL / ledger_base_nav_bil if ledger_base_nav_bil else 1.0
trades_period = [row for row in trades_full if str(row.get("date") or "") >= LEDGER_REBASE_START_DATE]


def enrich_trade(row):
    out = dict(row)
    nav_bil_original = nav_by_date.get(out.get("date"))
    nav_bil = nav_bil_original * ledger_scale if nav_bil_original is not None else None
    gross_bil_original = as_float(out.get("grossBil"))
    gross_bil = gross_bil_original * ledger_scale if gross_bil_original is not None else None
    pnl_bil_original = as_float(out.get("pnlBil"))
    fees_bil_original = as_float(out.get("feesBil"))
    out["modelFullNavBilAtTrade"] = nav_bil_original
    out["modelFullGrossBil"] = gross_bil_original
    out["modelFullShares"] = out.get("shares")
    out["navBilAtTrade"] = nav_bil
    out["grossBil"] = gross_bil
    out["pnlBil"] = pnl_bil_original * ledger_scale if pnl_bil_original is not None else None
    out["feesBil"] = fees_bil_original * ledger_scale if fees_bil_original is not None else fees_bil_original
    for key in ("shares", "rawShares", "positionBeforeShares", "positionAfterShares"):
        if key in out:
            out[key] = scale_lot(out.get(key), ledger_scale)
    out["tradeWeightPct"] = gross_bil / nav_bil * 100 if nav_bil and gross_bil is not None else None
    return out


trades_latest = [enrich_trade(row) for row in list(reversed(trades_period))[:8]]
ledger_rows = [enrich_trade(row) for row in list(reversed(trades_period))]
ledger_first_trade_date = min((str(row.get("date")) for row in trades_period if row.get("date")), default="-")
ledger_last_trade_date = max((str(row.get("date")) for row in trades_period if row.get("date")), default="-")
ledger_basis_label = f"NAV 1 tỷ từ {LEDGER_REBASE_START_DATE}"

memos = analysis.get("memos", [])
memo_by_symbol = {str(row.get("symbol", "")).upper(): row for row in memos}
stock_by_symbol = {
    str(row.get("symbol", "")).upper(): row
    for row in (
        list(dashboard_data.get("all") or [])
        + list(dashboard_data.get("topAll") or [])
        + list(dashboard_data.get("watch") or [])
        + list(dashboard_data.get("candidates") or [])
    )
    if row.get("symbol")
}
held_symbols = {str(row.get("symbol", "")).upper() for row in holdings}
planned_rows = policy.get("plannedOrders", {}).get("rows", [])
shortlist_symbols = set(memo_by_symbol)
shortlist_symbols.update(str(row.get("symbol", "")).upper() for row in planned_rows if row.get("symbol"))


def normalize_watch_item(raw, source):
    sym = str(raw.get("symbol", "")).upper().strip()
    if not sym:
        return None
    cur = as_float(raw.get("current_price_k", raw.get("currentPrice")))
    target_px = as_float(raw.get("target_price_k", raw.get("targetPrice")))
    stop_px = as_float(raw.get("stop_price_k", raw.get("stopPrice")))
    rr = as_float(raw.get("risk_reward", raw.get("riskReward")))
    if (rr is None or rr <= 0) and cur and target_px and stop_px and cur > stop_px:
        rr = (target_px - cur) / (cur - stop_px)
    upside = as_float(raw.get("upside_pct", raw.get("upsidePct")))
    if upside is not None and abs(upside) <= 2:
        upside *= 100
    liq = as_float(raw.get("avg_value_20d_bil"))
    status = str(raw.get("status") or raw.get("qualitative_overlay") or "WATCH").upper()
    action = str(raw.get("action") or raw.get("orderAction") or "").upper()
    hard_gate = str(raw.get("hard_gate") or "PASS").upper()
    in_portfolio = sym in held_symbols
    strong_signal = any(x in status for x in ("BUY", "ACCUMULATE")) or "MUA" in action
    pass_hard = "PASS" in hard_gate
    not_avoid = "AVOID" not in status
    liq_ok = liq is not None and liq >= 3
    rr_ok = rr is not None and rr >= 2
    upside_ok = upside is not None and upside >= 12
    gate_pass = sum([not in_portfolio, pass_hard, not_avoid, strong_signal, liq_ok, rr_ok, upside_ok])
    bucket = "BUY_SOON" if gate_pass == 7 else "WATCH"
    note = "Đạt chuẩn mua" if bucket == "BUY_SOON" else "Theo dõi thêm"
    if in_portfolio:
        note = "Đang nắm"
    elif not strong_signal:
        note = "Chưa có tín hiệu mua"
    elif not liq_ok:
        note = "Thanh khoản thấp/thiếu"
    elif not rr_ok:
        note = "R:R < 2"
    elif not upside_ok:
        note = "Upside < 12%"
    return {
        "symbol": sym,
        "source": source,
        "bucket": bucket,
        "gatePassCount": gate_pass,
        "gateTotal": 7,
        "status": status,
        "action": action,
        "hardGate": hard_gate,
        "industry": raw.get("industry_name") or raw.get("industry") or raw.get("sleeve") or "-",
        "currentPrice": cur,
        "targetPrice": target_px,
        "stopPrice": stop_px,
        "upsidePct": upside,
        "riskReward": rr,
        "liq20dBil": liq,
        "inPortfolio": in_portfolio,
        "planned": any(str(row.get("symbol", "")).upper() == sym for row in planned_rows),
        "buySignal": strong_signal,
        "note": note,
    }


watch_merged = {}
source_rank = {"watch": 1, "memo": 2, "candidate": 3, "live_shortlist": 4}


def put_watch(row, source):
    item = normalize_watch_item(row, source)
    if not item:
        return
    prev = watch_merged.get(item["symbol"])
    if prev is None or source_rank.get(source, 0) >= source_rank.get(prev["source"], 0):
        watch_merged[item["symbol"]] = item


for sym in shortlist_symbols:
    put_watch({**stock_by_symbol.get(sym, {}), **memo_by_symbol.get(sym, {}), "symbol": sym}, "live_shortlist")
for row in dashboard_data.get("candidates") or []:
    put_watch(row, "candidate")
for row in dashboard_data.get("watch") or []:
    put_watch(row, "watch")
for row in memos:
    sym = str(row.get("symbol", "")).upper()
    put_watch({**stock_by_symbol.get(sym, {}), **row}, "memo")

watchlist_rows_all = sorted(
    watch_merged.values(),
    key=lambda r: (
        r["inPortfolio"],
        -(r.get("gatePassCount") or 0),
        -(r.get("upsidePct") or 0),
        -(r.get("riskReward") or 0),
        r["symbol"],
    ),
)
watchlist_rows = [row for row in watchlist_rows_all if not row["inPortfolio"]]
watchlist_summary = {
    "total": len(watchlist_rows),
    "buySoon": sum(1 for row in watchlist_rows if row["bucket"] == "BUY_SOON"),
    "watchMore": sum(1 for row in watchlist_rows if row["bucket"] != "BUY_SOON"),
    "excludedHeld": sum(1 for row in watchlist_rows_all if row["inPortfolio"]),
    "onlineCandidates": len(dashboard_data.get("candidates") or []),
    "onlineWatch": len(dashboard_data.get("watch") or []),
    "memoOnly": len(memos),
}
method_cards = policy.get("methodology", {}).get("cards", [])
audit = policy.get("productionAudit", {})
perf = {
    "cagr": audit.get("cagr") or policy.get("historicalCagr"),
    "maxDrawdown": audit.get("maxDrawdown") or policy.get("historicalMaxDrawdown"),
    "sharpe": policy.get("historicalSharpe"),
    "minEdge": audit.get("minEdgeVsVni"),
    "passVni30": audit.get("passVni30"),
    "slippageBps": audit.get("slippageBps"),
}

latest_curve = curve_2021[-1] if curve_2021 else {}
vni_cache = pd.read_parquet(ROOT / ".cache" / "backtest" / "vnindex_daily.parquet")
vni_cache["date"] = pd.to_datetime(vni_cache["date"])
latest_vni = vni_cache.sort_values("date").iloc[-1]
copy_nav_m = 1000
copy_market_m = sum(h.get("valueMil") or 0 for h in holdings)
copy_cost_m = sum(h.get("costMilCalc") or 0 for h in holdings)
copy_cash_m = max(0, copy_nav_m - copy_cost_m)
copy_total_m = copy_cash_m + copy_market_m
copy_pnl_m = copy_total_m - copy_nav_m
copy_pnl_pct = copy_pnl_m / copy_nav_m * 100

# --- Data integrity flags (provenance) ---
data_flags = []
try:
    cache_p = ROOT / ".cache" / "backtest" / "history_clean" / f"{paper_symbol}.parquet"
    if cache_p.exists():
        mc = pd.read_parquet(cache_p)
        tcol = [c for c in mc.columns if c.lower() in ("time", "date")][0]
        ccol = [c for c in mc.columns if c.lower() == "close"][0]
        mc[tcol] = pd.to_datetime(mc[tcol])
        last_c = mc.sort_values(tcol).iloc[-1]
        cache_date = last_c[tcol].strftime("%Y-%m-%d")
        cache_px = float(last_c[ccol])
        fresh_d = paper_quote.get("date")
        if fresh_d and fresh_d > cache_date:
            data_flags.append(
                f"Giá fresh {paper_symbol} {paper_fresh_px}k ({fresh_d}) lấy từ live status, "
                f"CHƯA có trong cache giá (cache dừng {cache_date} @ {cache_px}k) — chưa cross-check được."
            )
except Exception as e:
    data_flags.append(f"Không đọc được cache giá {paper_symbol}: {e}")

upd = str(live_status.get("updatedAt", "") or "")
lpd = str(live_status.get("latestPriceDate", "") or "")
if upd and lpd and upd[:10] < lpd:
    data_flags.append(
        f"live status updatedAt {upd} đứng TRƯỚC latestPriceDate {lpd} — cần reconcile timestamp."
    )

paper_status = "ĐÃ KHỚP" if paper_executed else "KẾ HOẠCH · chưa khớp"

chart_rows = [{
    "date": r["date"],
    "model": float(r["navBil"]),
    "vni": float(r["vniClose"]),
} for r in curve_2021]

model_summary_cards = [
    ["Phạm vi", "Copy-trade cổ phiếu Việt Nam, không margin, không phái sinh, không ETF/trái phiếu."],
    ["Tín hiệu", "Bộ lọc chạy theo dữ liệu giá, thanh khoản, chất lượng và trạng thái thị trường; tín hiệu tuần được cập nhật trước phiên thực hiện."],
    ["Thực hiện", "Lệnh quy đổi theo NAV copy, làm tròn lot 100, theo dõi target/stop và trạng thái có thể bán."],
    ["Kiểm soát", "Dashboard chỉ hiển thị kết quả và kỷ luật vận hành, không công bố công thức điểm số nội bộ."],
]
forecast_date = next_monday(live_status.get("latestPriceDate") or signal_w1.get("execution_date"))
planned_symbols = {str(row.get("symbol", "")).upper() for row in planned_rows}
live_price_date = str(live_status.get("latestPriceDate") or "")
live_updated_label = (
    live_status.get("updatedAtICT")
    or live_status.get("updatedAt")
    or live_status.get("updatedAtUtc")
    or "-"
)
forecast_as_of = str(forecast_status.get("asOf") or "")
forecast_computed_label = (
    forecast_status.get("computedAtICT")
    or forecast_status.get("computedAt")
    or forecast_status.get("computedAtUtc")
    or "chưa ghi timestamp"
)
forecast_attempt_label = (
    forecast_status.get("attemptedAtICT")
    or (forecast_status.get("meta") or {}).get("lastAttemptAtICT")
    or forecast_status.get("attemptedAtUtc")
)
forecast_timing_label = forecast_computed_label if forecast_status.get("status") == "COMPUTED" else (forecast_attempt_label or forecast_computed_label)
full_fresh = int(as_float(full_universe_status.get("symbolsAtTargetOrNewer"), 0) or 0)
full_total = int(as_float(full_universe_status.get("symbolsTotal"), 0) or 0)
full_updated_label = (
    full_universe_status.get("updatedAtICT")
    or full_universe_status.get("updatedAt")
    or "-"
)

# Fail-closed display + forecast age guard (áp dụng cho cả lane price-only và forecast):
# chỉ render lệnh khi forecast COMPUTED, có rows, và còn đúng tuần kế hoạch hiện tại.
# Forecast cũ tuần trước (planDate lệch expected) bị coi như chưa publish -> bảng trống.
forecast_is_computed = forecast_status.get("status") == "COMPUTED" and bool(forecast_status.get("rows"))
forecast_plan_date = forecast_status.get("planDate")
forecast_is_fresh_to_live = bool(forecast_as_of and live_price_date and forecast_as_of >= live_price_date)
forecast_is_current = forecast_is_computed and (forecast_plan_date == forecast_date) and forecast_is_fresh_to_live

if forecast_is_current:
    forecast_rows = forecast_status.get("rows", [])
    forecast_source_label = forecast_status.get("source") or "r46_forecast.json"
    forecast_summary = forecast_status.get("message") or "Forecast R46 precompute tu dong."
    forecast_display_state = "COMPUTED"
else:
    # Fail-closed: KHÔNG dựng lệnh tự suy diễn từ policy/watchlist. Bảng để trống.
    forecast_rows = []
    forecast_source_label = "fail_closed"
    if forecast_is_computed and not forecast_is_current:
        forecast_display_state = "STALE"
        if forecast_plan_date != forecast_date:
            forecast_summary = (
                f"Chưa publish lệnh tuần này. Forecast R46 mới nhất là cho ngày {forecast_plan_date}, "
                f"đã cũ so với tuần kế hoạch {forecast_date}. Giữ nguyên danh mục hiện tại, chờ forecast fresh."
            )
        else:
            forecast_summary = (
                f"Chưa publish lệnh mới. Forecast R46 asOf {forecast_as_of or '-'} chưa bắt kịp giá live "
                f"{live_price_date or '-'}; lần chạy gần nhất {forecast_timing_label}. Giữ nguyên danh mục hiện tại."
            )
    else:
        forecast_display_state = "NOT_COMPUTED"
        reason = forecast_status.get("reason")
        forecast_summary = (
            "Chưa publish lệnh mới: forecast R46 chưa reproduce được artifact khóa hoặc thiếu dữ liệu fresh"
            + (f" ({reason})" if reason else "")
            + ". Dashboard giữ nguyên danh mục hiện tại, không tự suy diễn lệnh."
        )

for row in forecast_rows:
    sym = str(row.get("symbol", "")).upper()
    quote = quotes.get(sym) or {}
    if quote.get("close"):
        row["currentPrice"] = quote.get("close")
        row["priceAsOf"] = quote.get("date") or row.get("priceAsOf")
    held = next((h for h in holdings if str(h.get("symbol", "")).upper() == sym), None)
    held_shares = int(as_float((held or {}).get("copyShares", (held or {}).get("modelShares")), 0) or 0)
    action_text = str(row.get("action") or "").upper()
    target_copy = int(as_float(row.get("targetCopyShares"), 0) or 0)
    if held_shares > 0 and (target_copy == 0 or "BÁN HẾT" in action_text or "BAN HET" in action_text):
        row["currentCopyShares"] = held_shares
        row["targetCopyShares"] = 0
        row["orderShares"] = held_shares

planned_public = {
    "asOf": policy.get("plannedOrders", {}).get("asOf"),
    "planDate": forecast_date,
    "stage": policy.get("plannedOrders", {}).get("stage"),
    "source": forecast_source_label,
    "forecastStatus": forecast_status.get("status") or "NOT_CONFIGURED",
    "forecastDisplayState": forecast_display_state,
    "forecastPlanDate": forecast_plan_date,
    "forecastAsOf": forecast_as_of,
    "forecastComputedAt": forecast_computed_label,
    "forecastAttemptedAt": forecast_attempt_label,
    "forecastReason": forecast_status.get("reason"),
    "summary": forecast_summary,
    "rows": forecast_rows,
}

exec_cfg = policy_config.get("execution") or {}
base_gap = as_float(policy_config.get("entry_gap_threshold"), as_float(exec_cfg.get("gap"), 0.09)) or 0.09
limit_buffer = as_float(policy_config.get("entry_limit_buffer"), as_float(exec_cfg.get("buffer"), 0.015)) or 0.015
pullback_days = int(as_float(policy_config.get("entry_pullback_days"), as_float(exec_cfg.get("pullback"), 2)) or 2)
min_sell_sessions = int(as_float(policy_config.get("entry_min_sell_sessions"), as_float(exec_cfg.get("min_sell"), 4)) or 4)
bear_stop_loss = as_float(policy_config.get("daily_stop_loss"), as_float(exec_cfg.get("bear_regime_stop"), 0.05)) or 0.05
regime_now = latest_regime(live_status.get("latestPriceDate"))
if regime_now.get("regime") == "UNKNOWN":
    forecast_meta = forecast_status.get("meta") or {}
    if forecast_meta.get("currentRegime"):
        regime_now = {
            "date": forecast_meta.get("currentRegimeDate"),
            "regime": str(forecast_meta.get("currentRegime")).upper(),
        }
regime_text = regime_now.get("regime") or "UNKNOWN"
if regime_text and regime_text != "UNKNOWN":
    regime_label = regime_text
bear_stop_active = "BEAR" in regime_text

execution_rows = []
for h in holdings:
    sym = str(h.get("symbol", "")).upper()
    ohlc = latest_ohlc(sym)
    entry_px = as_float(h.get("entryPrice"))
    current_px = as_float(h.get("currentPrice"), as_float(ohlc.get("close")))
    low_px = as_float(ohlc.get("low"), current_px)
    open_px = as_float(ohlc.get("open"), current_px)
    stop_px = entry_px * (1.0 - bear_stop_loss) if entry_px else None
    sellable = bool(h.get("isSellableNow", True))
    stop_hit = bool(bear_stop_active and sellable and stop_px and low_px and low_px <= stop_px)
    stop_fill_px = min(open_px, stop_px) if stop_hit and open_px and stop_px else stop_px
    if stop_hit:
        action = "BÁN STOP"
        status = "CẦN BÁN"
        note = f"Bear regime đang bật; low {fmt_num(low_px, 2)}k đã chạm stop {fmt_num(stop_px, 2)}k. Model fill khoảng {fmt_num(stop_fill_px, 2)}k."
    elif bear_stop_active and sellable:
        action = "CANH STOP"
        status = "ĐANG BẬT"
        note = f"Bear stop bật. Bán nếu giá chạm {fmt_num(stop_px, 2)}k; chưa chạm theo low mới nhất {fmt_num(low_px, 2)}k."
    elif bear_stop_active and not sellable:
        action = "GIỮ"
        status = "CHỜ T+"
        note = f"Bear stop có điều kiện nhưng lot chưa đủ {min_sell_sessions} phiên; sellable từ {h.get('sellableFrom') or '-'}."
    else:
        action = "GIỮ"
        status = "STOP TẮT"
        note = f"Regime hiện tại {regime_text}; bear stop 5% chưa bật. Nếu chuyển BEAR, stop tham chiếu là {fmt_num(stop_px, 2)}k."
    execution_rows.append({
        "group": "Hôm nay",
        "date": live_status.get("latestPriceDate") or h.get("priceAsOf"),
        "symbol": sym,
        "action": action,
        "status": status,
        "shares": h.get("copyShares") or h.get("modelShares") or 0,
        "currentPrice": current_px,
        "referenceClose": entry_px,
        "maxOpen": None,
        "limitPrice": None,
        "bearStop": stop_px,
        "lowPrice": low_px,
        "stopActive": bear_stop_active,
        "note": note,
    })

holding_by_symbol = {str(h.get("symbol", "")).upper(): h for h in holdings}
for row in forecast_rows:
    sym = str(row.get("symbol", "")).upper()
    h = holding_by_symbol.get(sym, {})
    exchange = h.get("exchange") or row.get("exchange")
    current_px = as_float(row.get("currentPrice"), as_float(h.get("currentPrice")))
    eff_gap = effective_gap_threshold(exchange, base_gap)
    raw_action = str(row.get("action") or row.get("status") or "").upper()
    if "MUA" in raw_action:
        max_open = current_px * (1.0 + eff_gap) if current_px else None
        limit_px = current_px * (1.0 + limit_buffer) if current_px else None
        status = "CHỜ CHỐT"
        note = (
            f"Sau close thứ 6 nếu còn tín hiệu: mua open nếu không vượt {fmt_num(max_open, 2)}k; "
            f"nếu gap cao thì chờ pullback quanh {fmt_num(limit_px, 2)}k tối đa {pullback_days} phiên, không khớp thì bỏ qua."
        )
    elif "BÁN" in raw_action or "BAN" in raw_action:
        max_open = None
        limit_px = None
        status = "BÁN MỞ CỬA"
        note = "Nếu forecast vẫn chốt sau close thứ 6: bán mở cửa thứ 2, không chờ target/stop."
    else:
        max_open = None
        limit_px = None
        status = "THEO DÕI"
        note = "Không có thay đổi vị thế; tiếp tục theo dõi stop và target tuần kế tiếp."
    execution_rows.append({
        "group": "Thứ 2 tới",
        "date": row.get("displayPlanDate") or row.get("planDate") or forecast_date,
        "symbol": sym,
        "action": row.get("action") or row.get("status"),
        "status": status,
        "shares": row.get("orderShares") or 0,
        "currentPrice": current_px,
        "referenceClose": current_px,
        "maxOpen": max_open,
        "limitPrice": limit_px,
        "bearStop": None,
        "lowPrice": None,
        "stopActive": False,
        "note": note,
    })

urgent_count = sum(1 for r in execution_rows if r.get("status") in {"CẦN BÁN", "BÁN NGAY"})
planned_count = sum(1 for r in execution_rows if r.get("group") == "Thứ 2 tới" and as_float(r.get("shares"), 0) > 0)
execution_summary = (
    f"{urgent_count} lệnh cần xử lý ngay; {planned_count} lệnh forecast cho {forecast_date}; "
    f"regime hiện tại {regime_text} ({regime_now.get('date') or '-'}), bear stop {'bật' if bear_stop_active else 'tắt'}."
)

data = {
    "asOf": live_status.get("latestPriceDate") or max([h.get("priceAsOf") or "" for h in holdings] + [""]),
    "liveUpdatedAt": live_status.get("updatedAt"),
    "liveUpdatedAtICT": live_status.get("updatedAtICT"),
    "liveUpdatedAtUtc": live_status.get("updatedAtUtc"),
    "liveUpdatedLabel": live_updated_label,
    "forecastComputedAtICT": forecast_status.get("computedAtICT"),
    "forecastComputedAtUtc": forecast_status.get("computedAtUtc"),
    "forecastAttemptedAtICT": forecast_attempt_label,
    "forecastAsOf": forecast_as_of,
    "forecastComputedLabel": forecast_computed_label,
    "forecastTimingLabel": forecast_timing_label,
    "fullUniverseStatus": {
        "latestPriceDate": full_universe_status.get("latestPriceDate"),
        "targetDate": full_universe_status.get("targetDate"),
        "updatedAt": full_universe_status.get("updatedAt"),
        "updatedAtICT": full_universe_status.get("updatedAtICT"),
        "updatedLabel": full_updated_label,
        "symbolsAtTargetOrNewer": full_fresh,
        "symbolsTotal": full_total,
    },
    "regimeLabel": regime_label,
    "paperStatus": paper_status,
    "policy": {
        "key": policy.get("key"),
        "label": policy.get("label"),
        "cashBuffer": policy.get("cashBuffer"),
        "totalSuggestedWeight": policy.get("totalSuggestedWeight"),
        "lastUpdate": policy.get("lastUpdate"),
        "plannedOrders": planned_public,
    },
    "executionDesk": {
        "summary": execution_summary,
        "regime": regime_text,
        "regimeDate": regime_now.get("date"),
        "bearStopActive": bear_stop_active,
        "baseGapPct": base_gap * 100,
        "limitBufferPct": limit_buffer * 100,
        "pullbackDays": pullback_days,
        "minSellSessions": min_sell_sessions,
        "bearStopLossPct": bear_stop_loss * 100,
        "rows": execution_rows,
    },
    "perf": perf,
    "holdings": holdings,
    "watchlist": watchlist_rows,
    "watchlistSummary": watchlist_summary,
    "modelSummaryCards": model_summary_cards,
    "tradesLatest": trades_latest,
    "ledger": ledger_rows,
    "tradeCount": len(ledger_rows),
    "fullTradeCount": hist.get("tradeCount") or len(trades_full),
    "firstTradeDate": ledger_first_trade_date,
    "lastTradeDate": ledger_last_trade_date,
    "ledgerBasis": {
        "startDate": LEDGER_REBASE_START_DATE,
        "startNavBil": LEDGER_REBASE_NAV_BIL,
        "baseCurveDate": ledger_base_curve[0]["date"] if ledger_base_curve else None,
        "baseOriginalNavBil": ledger_base_nav_bil,
        "scale": ledger_scale,
        "label": ledger_basis_label,
    },
    "chart": chart_rows,
    "copyAccount": {
        "navMil": copy_nav_m,
        "marketMil": copy_market_m,
        "cashMil": copy_cash_m,
        "totalMil": copy_total_m,
        "pnlMil": copy_pnl_m,
        "pnlPct": copy_pnl_pct,
    },
    "paperTrade": {
        "source": "signal_week_1_20260601.json + dashboard_live_update_status.json",
        "startDate": pt_state.get("start_date"),
        "endDate": pt_state.get("end_date"),
        "navStartMil": paper_nav_start_vnd / 1e6,
        "logAsOf": paper_log_last.get("as_of"),
        "symbol": paper_symbol,
        "shares": paper_shares,
        "signalPrice": paper_signal_px,
        "entryPrice": paper_entry_px,
        "entryDate": paper_entry_date,
        "fillReason": paper_fill.get("fill_reason") if paper_fill else None,
        "signalDate": signal_w1.get("execution_date"),
        "freshPrice": paper_fresh_px,
        "freshDate": paper_quote.get("date"),
        "positionValueMil": paper_value_m,
        "entryCostMil": paper_ref_cost_m,
        "positionPnlMil": paper_position_pnl_m,
        "positionPnlPct": paper_position_pnl_pct,
        "cashMil": paper_cash_after_m,
        "navMil": paper_nav_m,
        "navPnlMil": paper_nav_pnl_m,
        "navPnlPct": paper_nav_pnl_pct,
        "cashPct": paper_cash_pct,
        "exposurePct": paper_exposure_pct,
        "tplusViolations": paper_log_last.get("tplus_violations_cum", 0),
        "checkpoints": pt_state.get("weekly_checkpoint_due", {}),
    },
    "vni": {
        "date": latest_vni["date"].strftime("%Y-%m-%d"),
        "close": float(latest_vni["close"]),
    },
}

data_js = json.dumps(data, ensure_ascii=False)

# --- Sanity asserts: bắt lỗi sớm trước khi ghi HTML ---
assert holdings, "FAIL: holdings rỗng — kiểm tra analysis.js policy r46_bear_stop_mcore"
assert len(ledger_rows) == len(trades_period), "FAIL: ledger count lệch trades_period"
assert chart_rows and len(chart_rows) > 100, "FAIL: chart_rows quá ít — kiểm tra equityCurve"
assert data["vni"]["close"] > 0, "FAIL: VNI close không hợp lệ"

html = f"""<!doctype html>
<html lang="vi" data-theme="light">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ez Trading</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap&subset=vietnamese" rel="stylesheet" />
<style>
:root {{
  --font: "Be Vietnam Pro", -apple-system, BlinkMacSystemFont, sans-serif;
  --bg:#f6f8fa; --surface:#fff; --surface2:#f6f8fa; --border:#d0d7de; --soft:#eaeef2;
  --text:#0f172a; --muted:#64748b; --muted2:#94a3b8; --accent:#0969da; --green:#1a7f37; --red:#cf222e; --amber:#9a6700; --violet:#8250df;
  --greenSoft:#dafbe1; --redSoft:#ffebe9; --blueSoft:#ddf4ff; --amberSoft:#fff8c5;
  --r:8px; --r2:6px; --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px;
  --t1:11px; --t2:12px; --t3:13px; --t4:14px; --t5:15px; --t6:18px; --t7:22px; --t8:28px;
}}
[data-theme="dark"] {{
  --bg:#151a22; --surface:#1f2630; --surface2:#18212b; --border:#3a4657; --soft:#2d3745;
  --text:#f8fafc; --muted:#c2cedd; --muted2:#9eb0c5; --accent:#58a6ff; --green:#5ee787; --red:#ff7b72; --amber:#f2cc60;
  --blueSoft:#173653; --greenSoft:#173d27; --redSoft:#4b2025; --amberSoft:#41340a;
}}
* {{ box-sizing:border-box; margin:0; padding:0; font-family:var(--font); font-feature-settings:"cv11","ss01","tnum"; }}
body {{ background:var(--bg); color:var(--text); font-size:var(--t3); line-height:1.45; }}
.app {{ display:grid; grid-template-columns:248px minmax(0,1fr); min-height:100vh; }}
.sidebar {{ background:var(--surface); border-right:1px solid var(--border); padding:20px 12px; position:sticky; top:0; height:100vh; display:flex; flex-direction:column; gap:14px; }}
.brand {{ display:flex; gap:10px; align-items:center; padding:4px 8px 16px; border-bottom:1px solid var(--soft); }}
.mark {{ width:32px; height:32px; display:grid; place-items:center; border-radius:7px; color:#fff; background:var(--accent); font-weight:800; }}
.brand b {{ display:block; font-size:var(--t4); }} .brand span {{ color:var(--muted); font-size:var(--t2); }}
.navlabel {{ color:var(--muted); font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; padding:8px 8px 0; }}
.nav {{ border:0; background:transparent; color:var(--text); width:100%; height:34px; border-radius:7px; display:flex; align-items:center; gap:10px; padding:0 9px; cursor:pointer; font:inherit; font-weight:600; }}
.nav svg {{ width:15px; height:15px; color:var(--muted); }} .nav .ct {{ margin-left:auto; background:var(--surface2); color:var(--muted); border-radius:999px; padding:1px 7px; font-size:var(--t1); }}
.nav.on {{ background:#ddf4ff; color:#0969da; }} .nav.on svg {{ color:#0969da; }} .nav.on .ct {{ background:#0969da; color:white; }}
.sidefoot {{ margin-top:auto; border-top:1px solid var(--soft); padding:14px 8px 0; display:grid; gap:8px; }}
.sfrow {{ display:flex; justify-content:space-between; color:var(--muted); }} .sfrow b {{ color:var(--text); }}
.theme {{ border:1px solid var(--border); background:var(--surface2); color:var(--text); border-radius:7px; height:30px; font:inherit; font-weight:600; cursor:pointer; }}
.main {{ min-width:0; }}
.topbar {{ height:54px; display:flex; align-items:center; gap:16px; padding:0 24px; border-bottom:1px solid var(--border); background:var(--surface); position:sticky; top:0; z-index:5; }}
.crumb a {{ color:var(--accent); text-decoration:none; font-weight:700; }} .crumb span {{ color:var(--muted); margin:0 6px; }}
.spacer {{ flex:1; }} .search {{ width:min(420px,35vw); height:32px; border:1px solid var(--border); background:var(--surface2); border-radius:8px; display:flex; align-items:center; gap:8px; padding:0 10px; color:var(--muted); }}
.search input {{ border:0; outline:0; background:transparent; color:var(--text); font:inherit; width:100%; }}
.live {{ background:var(--greenSoft); color:var(--green); border-radius:999px; padding:4px 10px; font-weight:800; font-size:var(--t1); letter-spacing:.04em; }}
.content {{ padding:20px 24px 28px; max-width:1540px; }}
.view {{ display:none; }} .view.on {{ display:block; }}
.pageh {{ display:flex; justify-content:space-between; align-items:start; gap:16px; margin-bottom:14px; }}
h1 {{ font-size:var(--t7); letter-spacing:0; }} .sub {{ color:var(--muted); margin-top:2px; }}
.btn,.select,.input {{ height:30px; border:1px solid var(--border); background:var(--surface); color:var(--text); border-radius:7px; padding:0 10px; font:inherit; font-weight:600; }}
.btn.primary {{ background:var(--accent); color:white; border-color:var(--accent); }}
.controls {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:12px; }}
.ctrl-lbl {{ color:var(--muted); font-weight:700; font-size:var(--t1); letter-spacing:.04em; text-transform:uppercase; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); border:1px solid var(--border); border-radius:var(--r); background:var(--surface); overflow:hidden; margin-bottom:16px; }}
.kpi {{ padding:14px 16px; border-right:1px solid var(--soft); }} .kpi:last-child {{ border-right:0; }}
.kpi .l {{ color:var(--muted); font-size:var(--t1); font-weight:800; letter-spacing:.06em; text-transform:uppercase; }} .kpi .v {{ margin-top:4px; font-size:var(--t7); font-weight:800; }} .kpi .s {{ color:var(--muted); font-size:var(--t2); }}
.statusline {{ display:grid; grid-template-columns:repeat(3,1fr); gap:0; border:1px solid var(--border); border-radius:var(--r); background:var(--surface); overflow:hidden; margin:-4px 0 16px; }}
.statusline span {{ padding:9px 12px; border-right:1px solid var(--soft); color:var(--muted); font-size:var(--t2); }}
.statusline span:last-child {{ border-right:0; }}
.statusline b {{ color:var(--text); }}
.pos {{ color:var(--green)!important; }} .neg {{ color:var(--red)!important; }} .amb {{ color:var(--amber)!important; }}
.scaletag {{ background:var(--amberSoft); color:var(--amber); border-radius:999px; padding:1px 8px; font-size:var(--t1); font-weight:800; letter-spacing:.04em; }}
.sec {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r); overflow:hidden; margin-bottom:16px; }}
.sech {{ min-height:44px; padding:12px 16px; border-bottom:1px solid var(--soft); display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.sech h2 {{ font-size:var(--t4); }} .meta {{ color:var(--muted); font-size:var(--t2); }}
.secb {{ padding:16px; }}
.chartTop {{ display:flex; justify-content:space-between; align-items:start; gap:12px; padding:14px 16px 8px; }}
.chartVal {{ font-size:var(--t7); font-weight:800; color:var(--green); }} .chartSub {{ color:var(--muted); font-weight:700; font-size:var(--t1); letter-spacing:.04em; text-transform:uppercase; }}
.ranges {{ display:flex; border:1px solid var(--border); border-radius:7px; background:var(--surface2); padding:2px; }}
.ranges button {{ border:0; background:transparent; border-radius:5px; padding:4px 9px; font:inherit; font-size:var(--t1); font-weight:800; color:var(--muted); cursor:pointer; }}
.ranges button.on {{ background:var(--surface); color:var(--text); box-shadow:0 1px 2px rgba(0,0,0,.05); }}
.legend {{ display:flex; gap:18px; padding:0 16px 8px; color:var(--muted); font-size:var(--t2); font-weight:700; }} .legend i {{ display:inline-block; width:14px; height:2px; margin-right:6px; vertical-align:middle; }}
.chartwrap {{ position:relative; padding:0 16px 10px 54px; }} svg {{ width:100%; height:248px; display:block; overflow:visible; }} .ylabels {{ position:absolute; left:14px; top:10px; bottom:38px; width:42px; display:flex; flex-direction:column; justify-content:space-between; align-items:flex-end; color:var(--muted); font-size:11px; font-weight:600; }} .xlabels {{ height:26px; display:flex; justify-content:space-between; align-items:center; color:var(--muted); font-size:12px; font-weight:600; padding-left:1px; }} .tip {{ display:none; position:absolute; pointer-events:none; z-index:10; min-width:170px; background:var(--surface); border:1px solid var(--border); border-radius:7px; padding:8px 10px; box-shadow:0 12px 28px rgba(15,23,42,.14); font-size:var(--t2); }}
.tip .d {{ color:var(--muted); font-weight:800; margin-bottom:4px; }} .tip .r {{ display:flex; justify-content:space-between; gap:12px; }}
.split {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; align-items:start; }}
table {{ width:100%; border-collapse:collapse; }} th {{ text-align:left; padding:9px 12px; color:var(--muted); font-size:var(--t1); letter-spacing:.06em; text-transform:uppercase; border-bottom:1px solid var(--border); background:var(--surface2); }} td {{ padding:9px 12px; border-bottom:1px solid var(--soft); vertical-align:middle; }} tr:last-child td {{ border-bottom:0; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.forecast-table th:first-child,.forecast-table td:first-child {{ width:112px; white-space:nowrap; }}
.execution-table th:nth-child(1),.execution-table td:nth-child(1) {{ width:94px; white-space:nowrap; }}
.execution-table th:nth-child(3),.execution-table td:nth-child(3) {{ width:118px; }}
.thresholds {{ display:grid; gap:2px; font-size:var(--t2); color:var(--muted); }}
.thresholds b {{ color:var(--text); }}
.pill {{ display:inline-flex; align-items:center; min-height:20px; border-radius:999px; padding:2px 8px; font-size:11.5px; line-height:1.2; font-weight:600; letter-spacing:0; text-transform:none; white-space:nowrap; }} .buy {{ background:var(--greenSoft); color:var(--green); }} .sell {{ background:var(--redSoft); color:var(--red); }} .hold {{ background:var(--blueSoft); color:var(--accent); }} .skip {{ background:var(--surface2); color:var(--muted); }}
.ptgrid {{ display:grid; grid-template-columns:repeat(4,1fr); border-bottom:1px solid var(--soft); }} .ptbox {{ padding:10px 12px; border-right:1px solid var(--soft); }} .ptbox:nth-child(4n) {{ border-right:0; }} .ptbox span {{ color:var(--muted); font-size:var(--t1); font-weight:800; letter-spacing:.06em; text-transform:uppercase; }} .ptbox b {{ display:block; margin-top:3px; font-size:var(--t5); }}
.watchSummary {{ display:grid; grid-template-columns:repeat(5,1fr); border-bottom:1px solid var(--border); }}
.watchSummary span {{ padding:12px 14px; border-right:1px solid var(--border); color:var(--muted); font-size:var(--t1); }}
.watchSummary span:last-child {{ border-right:0; }}
.watchSummary b {{ display:block; margin-top:2px; color:var(--text); font-size:var(--t4); }}
.watchRules {{ display:flex; gap:8px; flex-wrap:wrap; padding:12px 16px; border-bottom:1px solid var(--border); }}
.watchRules span {{ border:1px solid var(--border); background:var(--surface2); border-radius:999px; padding:5px 9px; font-size:var(--t1); color:var(--muted); }}
.watchRules b {{ color:var(--accent); margin-right:5px; }}
.logic {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }} .logicCard {{ border:1px solid var(--border); border-radius:var(--r); padding:14px; background:var(--surface); }} .logicCard h3 {{ font-size:var(--t3); margin-bottom:6px; }} .logicCard p {{ color:var(--muted); font-size:var(--t2); line-height:1.5; }}
.ledgerbar {{ display:flex; align-items:center; gap:10px; padding:12px 16px; border-bottom:1px solid var(--soft); background:var(--surface2); }} .ledgerbar input {{ width:360px; max-width:45vw; height:30px; border:1px solid var(--border); border-radius:7px; padding:0 10px; background:var(--surface); color:var(--text); font:inherit; }} .pager {{ margin-left:auto; display:flex; gap:4px; }} .pager button {{ min-width:28px; height:28px; border:1px solid var(--border); border-radius:7px; background:var(--surface); color:var(--text); font:inherit; cursor:pointer; }} .pager button.on {{ background:var(--accent); color:white; border-color:var(--accent); }}
.scroll {{ max-height:calc(100vh - 235px); overflow:auto; }}
@media(max-width:900px) {{ .app {{ grid-template-columns:1fr; }} .sidebar {{ display:none; }} .topbar {{ padding:0 14px; }} .search {{ display:none; }} .content {{ padding:16px; }} .kpis,.split,.logic,.watchSummary,.statusline {{ grid-template-columns:1fr; }} svg {{ height:220px; }} }}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
  <div class="brand"><div class="mark">Ez</div><div><b>Ez Trading</b><span>R46 execution desk</span></div></div>
  <div class="navlabel">Menu</div>
  <button class="nav on" data-view="copy"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path d="M4 19V5m0 14h16M8 16l3-4 3 2 4-7"/></svg>Copy Trade<span class="ct">{len(holdings)}</span></button>
  <button class="nav" data-view="watch"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/></svg>Theo dõi mua<span class="ct">{len(watchlist_rows)}</span></button>
  <button class="nav" data-view="model"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path d="M3 4h18l-7 8v6l-4 2v-8L3 4z"/></svg>Bộ lọc model</button>
  <button class="nav" data-view="ledger"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>Lịch sử<span class="ct">{len(ledger_rows)}</span></button>
  <div class="sidefoot">
    <div class="sfrow"><span>Regime</span><b>{regime_label}</b></div>
    <div class="sfrow"><span>Cash</span><b>{fmt_num(policy.get('cashBuffer'),1)}%</b></div>
    <div class="sfrow"><span>CAGR</span><b class="pos">+{fmt_num(perf['cagr'],1)}%</b></div>
    <button class="theme" onclick="toggleTheme()">Chuyển giao diện</button>
  </div>
</aside>
<main class="main">
<header class="topbar"><div class="crumb"><a>Ez Trading</a><span>/</span><b id="crumb">Copy Trade</b></div><div class="spacer"></div><label class="search"><span>⌕</span><input id="globalSearch" placeholder="Tìm mã, lệnh, ghi chú..." /></label><span class="live" id="liveBadge">LIVE {data['asOf']} · {live_updated_label}</span></header>
<div class="content">
  <section class="view on" data-view="copy">
    <div class="pageh"><div><h1>Copy Trade</h1><div class="sub">R46 Bear Stop 15bps · dữ liệu từ dashboard online hiện tại · lịch sử từ 2021 theo {ledger_basis_label}</div></div><div><button class="btn">In PDF</button></div></div>
    <div class="controls"><span class="ctrl-lbl">NAV copy</span><input class="input" id="navInput" value="1" type="text" inputmode="decimal" autocomplete="off" style="width:80px" /><span class="ctrl-lbl">tỷ</span><button class="btn primary navPreset" data-nav="1">1 tỷ</button><button class="btn navPreset" data-nav="3">3 tỷ</button><button class="btn navPreset" data-nav="5">5 tỷ</button></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Vị thế đang nắm</div><div class="v" id="positionKpiValue">{len(holdings)} mã</div><div class="s" id="positionKpiSub">Quy đổi theo NAV copy</div></div>
      <div class="kpi"><div class="l">Lệnh cần làm</div><div class="v">{urgent_count} ngay · {planned_count} T2</div><div class="s">{forecast_display_state} · dữ liệu {forecast_as_of or '-'}</div></div>
      <div class="kpi"><div class="l">VNI gần nhất</div><div class="v" id="vniKpiClose">{fmt_num(data['vni']['close'],2)}</div><div class="s" id="vniKpiDate">{data['vni']['date'] or '-'}</div></div>
      <div class="kpi"><div class="l">Audit model</div><div class="v">VNI+30 {perf['passVni30']}/6</div><div class="s">Min edge +{fmt_num(perf['minEdge'],1)}pp · {perf['slippageBps']}bps</div></div>
    </div>
    <div class="statusline">
      <span id="liveStatusText">Giá live: <b>{live_price_date or '-'}</b> · cập nhật {live_updated_label}</span>
      <span id="forecastStatusText">Forecast: <b>{forecast_display_state}</b> · dữ liệu {forecast_as_of or '-'} · lần chạy {forecast_timing_label}</span>
      <span id="universeStatusText">Universe: <b>{full_fresh}/{full_total}</b> mã · cập nhật {full_updated_label}</span>
    </div>
    <section class="sec">
      <div class="sech"><h2>Lệnh cần làm · Execution Desk</h2><span class="meta">{execution_summary}</span></div>
      <table class="execution-table"><thead><tr><th>Khi</th><th>Mã</th><th>Lệnh</th><th class="num">KL</th><th class="num">Giá TT</th><th>Ngưỡng hành động</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead><tbody id="execRows"></tbody></table>
    </section>
    <section class="sec">
      <div class="sech"><h2>Performance · Model R46 vs VN-Index</h2><div class="ranges" id="ranges"><button data-r="ytd">YTD</button><button data-r="3m">3M</button><button data-r="6m">6M</button><button data-r="1y">1Y</button><button class="on" data-r="all">ALL</button></div></div>
      <div class="chartTop"><div><div class="chartVal" id="chartVal">-</div><div class="chartSub" id="chartSub">Model vs VN-Index · % từ đầu kỳ</div></div><div class="meta" id="chartDates">-</div></div>
      <div class="legend"><span><i style="background:var(--green)"></i>Model R46</span><span><i style="background:var(--muted2)"></i>VN-Index</span></div>
      <div class="chartwrap"><div class="ylabels" id="yLabels"></div><svg id="chart" viewBox="0 0 820 236" preserveAspectRatio="none"><g id="grid"></g><polyline id="vniLine" fill="none" stroke="var(--muted2)" stroke-width="1.5" stroke-dasharray="5 4"/><polyline id="modelLine" fill="none" stroke="var(--green)" stroke-width="2.2"/><line id="cross" y1="18" y2="214" opacity="0" stroke="var(--accent)" stroke-dasharray="3 3"/></svg><div class="xlabels" id="xLabels"></div><div class="tip" id="tip"></div></div>
    </section>
    <div class="split">
      <section class="sec"><div class="sech"><h2>Danh mục copy đang nắm giữ</h2><span class="meta">{len(holdings)} mã · quy đổi theo NAV copy</span></div><table><thead><tr><th>Mã</th><th>Ngành</th><th class="num">KL</th><th class="num">Giá vốn</th><th class="num">Giá TT</th><th class="num">Giá trị</th><th class="num">Tỷ trọng</th><th class="num">P/L</th><th class="num">P/L %</th></tr></thead><tbody id="holdRows"></tbody></table></section>
      <section class="sec"><div class="sech"><h2>Theo dõi thử nghiệm <span class="scaletag">{data['paperStatus']}</span></h2><span class="meta">Tài khoản giả lập 1 tỷ · bắt đầu {data['paperTrade']['startDate']}</span></div><div class="ptgrid" id="ptGrid"></div><table><thead><tr><th>Mã</th><th class="num">KL</th><th class="num">Giá vốn</th><th class="num">Giá TT</th><th class="num">P/L</th></tr></thead><tbody id="paperRows"></tbody></table></section>
    </div>
    <section class="sec"><div class="sech"><h2>Dự kiến giao dịch thứ 2 tới <span class="scaletag">{forecast_display_state}</span></h2><span class="meta">{planned_public['summary']}</span></div><table class="forecast-table"><thead><tr><th>Ngày</th><th>Mã</th><th>Lệnh</th><th class="num">KL</th><th class="num">Giá TT</th><th class="num">Target</th><th class="num">Stop</th><th>Ghi chú</th></tr></thead><tbody id="plannedRows"></tbody></table></section>
    <section class="sec"><div class="sech"><h2>Lệnh đã khớp gần nhất <span class="scaletag">NAV 2021 = 1 tỷ</span></h2><span class="meta">8 dòng mới nhất từ history.js · KL, giá trị, tỷ trọng &amp; P/L quy đổi theo {ledger_basis_label}; không scale theo ô NAV copy</span></div><table><thead><tr><th>Ngày</th><th>Mã</th><th>Lệnh</th><th class="num">KL</th><th class="num">Giá</th><th class="num">Tỷ trọng NAV</th><th class="num">P/L</th><th class="num">P/L %</th><th>Lý do</th></tr></thead><tbody id="latestRows"></tbody></table></section>
  </section>
  <section class="view" data-view="watch"><div class="pageh"><div><h1>Theo dõi mua</h1><div class="sub">{len(watchlist_rows)} mã từ dashboard online `data.js` + live shortlist · loại {watchlist_summary['excludedHeld']} mã đang nắm</div></div></div><section class="sec"><div class="sech"><h2>Mã đáng theo dõi và có thể mua sắp tới</h2><span class="meta">Không phải rule khớp lệnh live của R46</span></div><div class="watchSummary" id="watchSummary"></div><div class="watchRules" id="watchRules"></div><table><thead><tr><th>Mã</th><th>Nhóm</th><th class="num">Điểm lọc</th><th class="num">Upside</th><th class="num">Target</th><th class="num">R:R</th><th class="num">TK 20D</th><th>Target tuần</th><th>Tín hiệu mua</th><th>Ghi chú</th></tr></thead><tbody id="watchRows"></tbody></table></section></section>
  <section class="view" data-view="model"><div class="pageh"><div><h1>Bộ lọc model · R46 Bear Stop 15bps</h1><div class="sub">Tóm tắt vận hành, không công bố công thức nội bộ</div></div><span class="pill buy">AUDIT {policy.get('productionAudit',{}).get('status','R46')}</span></div><section class="sec"><div class="sech"><h2>Tóm tắt</h2><span class="meta">Public view</span></div><div class="secb"><div class="logic" id="logicGrid"></div></div></section></section>
  <section class="view" data-view="ledger"><div class="pageh"><div><h1>Lịch sử giao dịch</h1><div class="sub">{len(ledger_rows)} dòng từ {ledger_first_trade_date} đến {ledger_last_trade_date} · KL, giá trị, tỷ trọng &amp; P/L quy đổi theo {ledger_basis_label}</div></div></div><section class="sec"><div class="ledgerbar"><b id="ledgerCount"></b><input id="ledgerSearch" placeholder="Tìm theo mã, lệnh, lý do..." /><div class="pager" id="pager"></div></div><div class="scroll"><table><thead><tr><th>Ngày</th><th>Mã</th><th>Lệnh</th><th class="num">KL</th><th class="num">Giá</th><th class="num">Giá trị</th><th class="num">Tỷ trọng NAV</th><th class="num">P/L</th><th class="num">P/L %</th><th class="num">Nắm giữ</th><th>Lý do</th></tr></thead><tbody id="ledgerBody"></tbody></table></div></section></section>
</div>
</main>
</div>
<script>
const D = {data_js};
function f(v,d=1){{ if(v===null||v===undefined||Number.isNaN(Number(v))) return '-'; return Number(v).toLocaleString('vi-VN',{{maximumFractionDigits:d}}); }}
function money(v){{ if(v===null||v===undefined) return '-'; return (Number(v)>=0?'+':'-') + f(Math.abs(Number(v)),1) + ' tr'; }}
function pc(v,d=1){{ const n=Number(v); if(v===null||v===undefined||Number.isNaN(n)) return '-'; return (n>0?'+':'') + f(n,d) + '%'; }}
function wp(v,d=1){{ const n=Number(v); if(v===null||v===undefined||Number.isNaN(n)) return '-'; return f(n,d) + '%'; }}
function priceK(v,d=2){{ const n=Number(v); if(v===null||v===undefined||Number.isNaN(n)||n===0) return '-'; return f(n,d)+'k'; }}
function valueTr(v,d=1){{ const n=Number(v); if(v===null||v===undefined||Number.isNaN(n)||n===0) return '-'; return f(n,d)+' tr'; }}
function cls(v){{ return Number(v) >= 0 ? 'pos' : 'neg'; }}
function esc(s){{ return String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
function vnPlain(s){{ return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase(); }}
function parseNavValue(raw){{ const text=String(raw??'').trim().replace(',','.'); if(text===''||text==='.'||text==='0'||text==='0.') return null; const n=Number(text); return Number.isFinite(n)&&n>0?n:null; }}
function navLabel(n){{ return Number(n).toLocaleString('en-US',{{maximumFractionDigits:3,useGrouping:false}}); }}
function pill(side){{ const raw=String(side||'-'); const plain=vnPlain(raw); const c=plain.includes('MUA')||plain==='BUY'?'buy':plain.includes('BAN')||plain==='SELL'?'sell':plain.includes('BO')||plain.includes('SKIP')?'skip':'hold'; let label=raw; if(plain.includes('MUA DU KIEN')) label='Mua d\\u1ef1 ki\\u1ebfn'; else if(plain.includes('MUA THEM')) label='Mua th\\u00eam'; else if(plain.includes('MUA MOI')||plain==='BUY') label='Mua m\\u1edbi'; else if(plain.includes('BAN BOT')) label='B\\u00e1n b\\u1edbt'; else if(plain.includes('BAN HET')||plain==='SELL') label='B\\u00e1n h\\u1ebft'; else if(plain.includes('BAN 1 PHAN')) label='B\\u00e1n 1 ph\\u1ea7n'; else if(plain.includes('GIU')) label='Gi\\u1eef'; else if(plain.includes('THEO DOI')) label='Theo d\\u00f5i'; return `<span class="pill ${{c}}">${{esc(label)}}</span>`; }}
function toggleTheme(){{ const h=document.documentElement; h.dataset.theme=h.dataset.theme==='light'?'dark':'light'; renderChart(currentRange); }}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>{{ document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('on',x===b)); document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.dataset.view===b.dataset.view)); document.getElementById('crumb').textContent=b.childNodes[1]?.textContent?.trim()||b.textContent.trim(); window.scrollTo(0,0); }}));
function roundLot(x){{ return Math.max(0, Math.floor(Number(x || 0) / 100) * 100); }}
function renderCopyForNav(navBilRaw, syncInput=true){{ const parsed=parseNavValue(navBilRaw); if(parsed===null) return; const navBil=parsed; if(syncInput) document.getElementById('navInput').value=navLabel(navBil); document.querySelectorAll('.navPreset').forEach(b=>b.classList.toggle('primary', Number(b.dataset.nav)===navBil)); let market=0,cost=0; const posLabels=[]; const holdHtml=D.holdings.length ? D.holdings.map(h=>{{ const baseShares=Number(h.copyShares||h.modelShares||0); const shares=roundLot(baseShares*navBil); const entry=Number(h.entryPrice||0); const px=Number(h.currentPrice||0); const value=shares*px/1000; const rowCost=shares*entry/1000; const weight=value/(navBil*1000)*100; const pnl=value-rowCost; const pnlPct=rowCost>0?pnl/rowCost*100:null; market+=value; cost+=rowCost; posLabels.push(`${{esc(h.symbol)}} · ${{f(shares,0)}} cp`); return `<tr><td><strong>${{esc(h.symbol)}}</strong></td><td>${{esc(h.industry||h.sleeve||'-')}}</td><td class="num">${{f(shares,0)}}</td><td class="num">${{priceK(entry,3)}}</td><td class="num">${{priceK(px)}}</td><td class="num">${{valueTr(value)}}</td><td class="num">${{wp(weight)}}</td><td class="num ${{cls(pnl)}}">${{money(pnl)}}</td><td class="num ${{cls(pnlPct)}}">${{pc(pnlPct)}}</td></tr>`; }}).join('') : '<tr><td colspan="9">Chưa có vị thế.</td></tr>'; document.getElementById('holdRows').innerHTML=holdHtml; const exposure=navBil>0?market/(navBil*1000)*100:0; const posValue=document.getElementById('positionKpiValue'); const posSub=document.getElementById('positionKpiSub'); if(posValue) posValue.textContent=posLabels.length?posLabels.join(', '):'0 mã'; if(posSub) posSub.textContent='Giá trị '+f(market,1)+' tr · tỷ trọng '+wp(exposure,1); renderExecutionDesk(navBil); renderPlannedRows(navBil); }}
const pt = D.paperTrade;
function recomputePaperFromPrice(){{ const value=Number(pt.shares||0)*Number(pt.freshPrice||0)/1000; if(Number.isFinite(value)&&value>0) pt.positionValueMil=value; const cost=Number(pt.entryCostMil||0); if(cost>0){{ pt.positionPnlMil=pt.positionValueMil-cost; pt.positionPnlPct=pt.positionPnlMil/cost*100; }} const cash=Number(pt.cashMil||0); const navStart=Number(pt.navStartMil||1000); if(cash>0&&pt.positionValueMil){{ pt.navMil=cash+pt.positionValueMil; pt.navPnlMil=pt.navMil-navStart; pt.navPnlPct=navStart>0?pt.navPnlMil/navStart*100:null; pt.exposurePct=pt.positionValueMil/pt.navMil*100; pt.cashPct=navStart>0?cash/navStart*100:null; }} }}
function renderPaperTrade(){{ recomputePaperFromPrice(); document.getElementById('ptGrid').innerHTML = [
  ['NAV', pt.navMil===null?'-':f(pt.navMil,1)+' tr'],
  ['P/L', money(pt.navPnlMil)+' · '+pc(pt.navPnlPct,2)],
  ['Cash', f(pt.cashPct,1)+'%'],
  ['Exposure', f(pt.exposurePct,1)+'%']
].map(x=>`<div class="ptbox"><span>${{x[0]}}</span><b>${{x[1]}}</b></div>`).join(''); document.getElementById('paperRows').innerHTML = `<tr><td><strong>${{esc(pt.symbol)}}</strong><div class="meta">${{esc(pt.entryDate||pt.signalDate)}} → ${{esc(pt.freshDate)}}</div></td><td class="num">${{f(pt.shares,0)}}</td><td class="num">${{priceK(pt.entryPrice)}}</td><td class="num">${{priceK(pt.freshPrice)}}</td><td class="num ${{cls(pt.positionPnlMil)}}">${{money(pt.positionPnlMil)}} · ${{pc(pt.positionPnlPct)}}</td></tr>`; }}
renderPaperTrade();
const planned = D.policy.plannedOrders?.rows || [];
const execDesk = D.executionDesk?.rows || [];
function thresholdHtml(r){{ const lines=[]; if(r.maxOpen) lines.push(`Open <= <b>${{priceK(r.maxOpen)}}</b>`); if(r.limitPrice) lines.push(`Pullback <= <b>${{priceK(r.limitPrice)}}</b>`); if(r.bearStop) lines.push(`Bear stop <b>${{priceK(r.bearStop)}}</b>`); if(r.lowPrice) lines.push(`Low mới nhất ${{priceK(r.lowPrice)}}`); if(!lines.length && r.referenceClose) lines.push(`Tham chiếu <b>${{priceK(r.referenceClose)}}</b>`); return `<div class="thresholds">${{lines.map(x=>`<span>${{x}}</span>`).join('')}}</div>`; }}
function renderExecutionDesk(navBil=parseNavValue(document.getElementById('navInput')?.value)||1){{ const body=document.getElementById('execRows'); if(!body) return; body.innerHTML = execDesk.length ? execDesk.map(r=>{{ const shares=roundLot(Number(r.shares||0)*navBil); return `<tr><td>${{esc(r.group)}}<div class="meta">${{esc(r.date||'-')}}</div></td><td><strong>${{esc(r.symbol)}}</strong></td><td>${{pill(r.action||'-')}}</td><td class="num">${{shares?f(shares,0):'-'}}</td><td class="num">${{priceK(r.currentPrice)}}</td><td>${{thresholdHtml(r)}}</td><td>${{pill(r.status||'-')}}</td><td>${{esc(r.note||'')}}</td></tr>`; }}).join('') : '<tr><td colspan="8">Chưa có lệnh cần xử lý.</td></tr>'; }}
function renderPlannedRows(navBil=parseNavValue(document.getElementById('navInput')?.value)||1){{ document.getElementById('plannedRows').innerHTML = planned.length ? planned.map(r=>{{ const baseShares=Number(r.orderShares||0); const shares=baseShares>0?roundLot(baseShares*navBil):null; return `<tr><td>${{esc(r.displayPlanDate||r.planDate)}}</td><td><strong>${{esc(r.symbol)}}</strong></td><td>${{pill(r.action||r.status)}}</td><td class="num">${{shares===null?'-':f(shares,0)}}</td><td class="num">${{priceK(r.currentPrice)}}</td><td class="num pos">${{priceK(r.targetPrice)}}</td><td class="num neg">${{priceK(r.stopPrice)}}</td><td>${{esc(r.note)}}</td></tr>`; }}).join('') : '<tr><td colspan="8">Kh\\u00f4ng c\\u00f3 l\\u1ec7nh d\\u1ef1 ki\\u1ebfn \\u2014 xem ghi ch\\u00fa ph\\u00eda tr\\u00ean.</td></tr>'; }}
function liveSymbols(){{ const syms=new Set(); D.holdings.forEach(h=>h.symbol&&syms.add(h.symbol)); planned.forEach(r=>r.symbol&&syms.add(r.symbol)); execDesk.forEach(r=>r.symbol&&syms.add(r.symbol)); if(pt.symbol) syms.add(pt.symbol); return Array.from(syms).slice(0,40); }}
function applyEdgeLiveStatus(payload){{ if(!payload||!payload.quotes) return; const updated=payload.updatedAtICT||payload.updatedAtUtc||'-'; const latest=payload.latestPriceDate||D.asOf||'-'; D.asOf=latest; D.liveUpdatedLabel=updated; const liveBadge=document.getElementById('liveBadge'); if(liveBadge) liveBadge.textContent='LIVE '+latest+' · '+updated; const liveStatus=document.getElementById('liveStatusText'); if(liveStatus) liveStatus.innerHTML='Giá live: <b>'+esc(latest)+'</b> · cập nhật '+esc(updated)+' <span class="meta">edge 5p</span>'; if(payload.vnindex&&payload.vnindex.ok){{ D.vni.date=payload.vnindex.latest||D.vni.date; D.vni.close=Number(payload.vnindex.latestClose||D.vni.close); const vc=document.getElementById('vniKpiClose'); const vd=document.getElementById('vniKpiDate'); if(vc) vc.textContent=f(D.vni.close,2); if(vd) vd.textContent=D.vni.date||'-'; }} const quotes=payload.quotes||{{}}; const applyQuote=(row)=>{{ const sym=String(row.symbol||'').toUpperCase(); const q=quotes[sym]; if(q&&q.ok&&Number(q.close)>0){{ row.currentPrice=Number(q.close); row.priceAsOf=q.date||row.priceAsOf; if(row.lowPrice!==undefined&&Number(q.low)>0) row.lowPrice=Number(q.low); }} }}; D.holdings.forEach(applyQuote); planned.forEach(applyQuote); execDesk.forEach(applyQuote); const pq=quotes[String(pt.symbol||'').toUpperCase()]; if(pq&&pq.ok&&Number(pq.close)>0){{ pt.freshPrice=Number(pq.close); pt.freshDate=pq.date||pt.freshDate; }} renderCopyForNav(document.getElementById('navInput')?.value||1,false); renderPaperTrade(); }}
async function refreshEdgeLiveStatus(){{ const syms=liveSymbols(); if(!syms.length) return; try{{ const res=await fetch('/api/live-status?symbols='+encodeURIComponent(syms.join(',')), {{ cache:'no-store' }}); if(!res.ok) throw new Error('HTTP '+res.status); applyEdgeLiveStatus(await res.json()); }}catch(err){{ const liveStatus=document.getElementById('liveStatusText'); if(liveStatus) liveStatus.innerHTML += ' <span class="meta">edge lỗi</span>'; }} }}
document.querySelectorAll('.navPreset').forEach(btn=>btn.addEventListener('click',()=>renderCopyForNav(btn.dataset.nav,true)));
document.getElementById('navInput').addEventListener('input',e=>renderCopyForNav(e.target.value,false));
document.getElementById('navInput').addEventListener('blur',e=>{{ const n=parseNavValue(e.target.value); if(n!==null) e.target.value=navLabel(n); }});
renderCopyForNav(1);
refreshEdgeLiveStatus(); setInterval(refreshEdgeLiveStatus, 5*60*1000);
document.getElementById('latestRows').innerHTML = D.tradesLatest.map(r=>`<tr><td>${{esc(r.date)}}</td><td><strong>${{esc(r.symbol)}}</strong></td><td>${{pill(r.actionLabel||r.side)}}</td><td class="num">${{f(r.shares,0)}}</td><td class="num">${{priceK(r.executionPriceK)}}</td><td class="num">${{r.tradeWeightPct==null?'-':wp(r.tradeWeightPct,1)}}</td><td class="num ${{cls(r.pnlBil)}}">${{r.pnlBil==null?'-':money(Number(r.pnlBil)*1000)}}</td><td class="num ${{cls(r.returnPct)}}">${{pc(r.returnPct)}}</td><td>${{esc(r.reason)}}</td></tr>`).join('');
const ws = D.watchlistSummary || {{}};
document.getElementById('watchSummary').innerHTML = [
  ['Tổng watchlist', ws.total || 0],
  ['Có thể mua sớm', ws.buySoon || 0],
  ['Cần theo dõi thêm', ws.watchMore || 0],
  ['Nguồn online', `${{ws.onlineCandidates || 0}} candidate · ${{ws.onlineWatch || 0}} watch · ${{ws.memoOnly || 0}} memo`],
  ['Đã loại đang nắm', ws.excludedHeld || 0],
].map(x=>`<span>${{x[0]}}<b>${{x[1]}}</b></span>`).join('');
document.getElementById('watchRules').innerHTML = ['Không nắm','Gate PASS','Không AVOID','Có tín hiệu mua','Thanh khoản >= 3 tỷ','R:R >= 2','Upside >= 12%'].map((x,i)=>`<span><b>${{i+1}}</b>${{x}}</span>`).join('');
document.getElementById('watchRows').innerHTML = D.watchlist.length ? D.watchlist.map(w=>`<tr><td><strong>${{esc(w.symbol)}}</strong><div class="meta">${{esc(w.industry||'-')}}</div></td><td>${{w.bucket==='BUY_SOON'?'<span class="pill buy">Có thể mua</span>':'<span class="pill hold">Theo dõi</span>'}}</td><td class="num">${{w.gatePassCount}}/${{w.gateTotal}}</td><td class="num ${{Number(w.upsidePct)>=12?'pos':''}}">${{pc(w.upsidePct)}}</td><td class="num pos">${{priceK(w.targetPrice)}}</td><td class="num ${{Number(w.riskReward)>=2?'pos':''}}">${{w.riskReward==null?'-':f(w.riskReward,2)+'x'}}</td><td class="num">${{w.liq20dBil==null?'-':f(w.liq20dBil,1)}}</td><td>${{w.planned?'Có':'Không'}}</td><td>${{w.buySignal?'Có':'Không'}}</td><td>${{esc(w.note)}}</td></tr>`).join('') : '<tr><td colspan="10">Chưa có mã phù hợp cho watchlist.</td></tr>';
document.getElementById('logicGrid').innerHTML = D.modelSummaryCards.map((c,i)=>`<article class="logicCard"><h3>${{esc(c[0]||('Card '+(i+1)))}}</h3><p>${{esc(c[1]||'')}}</p></article>`).join('');
let currentRange='all';
function rangeRows(r){{ const rows=D.chart; if(!rows.length) return []; const last=new Date(rows[rows.length-1].date+'T00:00:00'); let cutoff=null; if(r==='ytd') cutoff=new Date(last.getFullYear(),0,1); if(r==='3m') cutoff=new Date(last), cutoff.setMonth(cutoff.getMonth()-3); if(r==='6m') cutoff=new Date(last), cutoff.setMonth(cutoff.getMonth()-6); if(r==='1y') cutoff=new Date(last), cutoff.setFullYear(cutoff.getFullYear()-1); return cutoff?rows.filter(x=>new Date(x.date+'T00:00:00')>=cutoff):rows; }}
function makePts(rows,key){{ const base=Number(rows[0][key]); const vals=rows.map(r=>Number(r[key])/base*100); const min=Math.min(...vals), max=Math.max(...vals); return {{vals,min,max}}; }}
function renderChart(r='all'){{ currentRange=r; document.querySelectorAll('#ranges button').forEach(b=>b.classList.toggle('on',b.dataset.r===r)); let rows=rangeRows(r); if(rows.length>500) rows=rows.filter((_,i)=>i%Math.ceil(rows.length/360)===0 || i===rows.length-1); if(rows.length<2) return; const m=makePts(rows,'model'), v=makePts(rows,'vni'); const mPct=m.vals.map(x=>x-100), vPct=v.vals.map(x=>x-100); const lo=Math.min(...mPct,...vPct), hi=Math.max(...mPct,...vPct); const niceStep=(span,n)=>{{ const raw=Math.max(span,1)/n; const mag=Math.pow(10,Math.floor(Math.log10(raw))); const norm=raw/mag; return (norm>=5?10:norm>=2?5:norm>=1?2:1)*mag; }}; const step=niceStep((hi-lo)*1.16,4); const min=Math.floor((lo-(hi-lo)*.08)/step)*step, max=Math.ceil((hi+(hi-lo)*.08)/step)*step; const x0=16,x1=804,y0=214,y1=18; const xy=(val,i)=>{{ const x=x0+i/(rows.length-1)*(x1-x0); const y=y0-(val-min)/(max-min)*(y0-y1); return [x,y]; }}; document.getElementById('modelLine').setAttribute('points',mPct.map((val,i)=>xy(val,i).join(',')).join(' ')); document.getElementById('vniLine').setAttribute('points',vPct.map((val,i)=>xy(val,i).join(',')).join(' ')); document.getElementById('chartVal').textContent=(mPct[mPct.length-1]>=0?'+':'')+f(mPct[mPct.length-1],1)+'%'; document.getElementById('chartSub').textContent='Model '+(mPct[mPct.length-1]>=0?'+':'')+f(mPct[mPct.length-1],1)+'% · VN-Index '+(vPct[vPct.length-1]>=0?'+':'')+f(vPct[vPct.length-1],1)+'% · từ đầu kỳ'; document.getElementById('chartDates').textContent=rows[0].date+' → '+rows[rows.length-1].date; const ticks=[]; for(let val=max; val>=min-1e-9; val-=step) ticks.push(val); document.getElementById('grid').innerHTML=ticks.map(t=>{{ const y=xy(t,0)[1]; return `<line x1="${{x0}}" x2="${{x1}}" y1="${{y}}" y2="${{y}}" stroke="var(--soft)" stroke-width="1"/>`; }}).join(''); document.getElementById('yLabels').innerHTML=ticks.map(t=>`<span>${{t>=0?'+':''}}${{f(t,0)}}%</span>`).join(''); document.getElementById('xLabels').innerHTML=[0,1,2,3,4].map(i=>{{ const idx=Math.floor(i*(rows.length-1)/4); return `<span>${{rows[idx].date.slice(5,7)}}/${{rows[idx].date.slice(2,4)}}</span>`; }}).join(''); const svg=document.getElementById('chart'), cross=document.getElementById('cross'), tip=document.getElementById('tip'); svg.onmousemove=e=>{{ const rect=svg.getBoundingClientRect(); const mx=(e.clientX-rect.left)*820/rect.width; let best=0,dist=1e9; rows.forEach((row,i)=>{{ const p=xy(mPct[i],i); const d=Math.abs(p[0]-mx); if(d<dist){{dist=d;best=i;}} }}); const p=xy(mPct[best],best), pv=xy(vPct[best],best); cross.setAttribute('x1',p[0]); cross.setAttribute('x2',p[0]); cross.setAttribute('opacity','1'); tip.style.display='block'; tip.style.left=(p[0]/820*rect.width+62)+'px'; tip.style.top=(Math.min(p[1],pv[1])/236*rect.height+4)+'px'; tip.innerHTML=`<div class="d">${{rows[best].date}}</div><div class="r"><span>Model</span><b>${{mPct[best]>=0?'+':''}}${{f(mPct[best],2)}}%</b></div><div class="r"><span>VN-Index</span><b>${{vPct[best]>=0?'+':''}}${{f(vPct[best],2)}}%</b></div>`; }}; svg.onmouseleave=()=>{{ cross.setAttribute('opacity','0'); tip.style.display='none'; }}; }}
document.querySelectorAll('#ranges button').forEach(b=>b.addEventListener('click',()=>renderChart(b.dataset.r))); renderChart('all');
let ledgerPage=1, ledgerQ=''; const PAGE=50;
function renderLedger(){{ const q=ledgerQ.toLowerCase(); const rows=q?D.ledger.filter(r=>Object.values(r).some(v=>String(v??'').toLowerCase().includes(q))):D.ledger; const pages=Math.max(1,Math.ceil(rows.length/PAGE)); if(ledgerPage>pages) ledgerPage=pages; const slice=rows.slice((ledgerPage-1)*PAGE,ledgerPage*PAGE); document.getElementById('ledgerCount').textContent=rows.length+' lệnh'; document.getElementById('ledgerBody').innerHTML=slice.map(r=>`<tr><td>${{esc(r.date)}}</td><td><strong>${{esc(r.symbol)}}</strong></td><td>${{pill(r.actionLabel||r.side)}}</td><td class="num">${{f(r.shares,0)}}</td><td class="num">${{priceK(r.executionPriceK)}}</td><td class="num">${{valueTr(Number(r.grossBil)*1000)}}</td><td class="num">${{r.tradeWeightPct==null?'-':wp(r.tradeWeightPct,1)}}</td><td class="num ${{cls(r.pnlBil)}}">${{r.pnlBil==null?'-':money(Number(r.pnlBil)*1000)}}</td><td class="num ${{cls(r.returnPct)}}">${{pc(r.returnPct)}}</td><td class="num">${{r.holdDays==null?'-':f(r.holdDays,0)+' ngày'}}</td><td>${{esc(r.reason)}}</td></tr>`).join(''); const start=Math.max(1,ledgerPage-2), end=Math.min(pages,ledgerPage+2); let html=`<button onclick="ledgerPage=1;renderLedger()">«</button>`; for(let p=start;p<=end;p++) html+=`<button class="${{p===ledgerPage?'on':''}}" onclick="ledgerPage=${{p}};renderLedger()">${{p}}</button>`; html+=`<button onclick="ledgerPage=${{pages}};renderLedger()">»</button>`; document.getElementById('pager').innerHTML=html; }}
document.getElementById('ledgerSearch').addEventListener('input',e=>{{ledgerQ=e.target.value; ledgerPage=1; renderLedger();}}); renderLedger();
</script>
</body>
</html>"""

out = args.out
if not out.is_absolute():
    out = ROOT / out
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(html, encoding="utf-8")
print(f"Wrote {out}")
print(f"Data: holdings={len(holdings)}, watchlist={len(watchlist_rows)}, ledger={len(ledger_rows)}, chart={len(chart_rows)}")
print(f"Regime: {regime_label} | paper_status: {paper_status} | flags: {len(data_flags)}")
for fl in data_flags:
    print("  FLAG:", fl)
print(f"Paper: {paper_symbol} {paper_shares} @ {paper_signal_px} -> {paper_fresh_px} ({paper_quote.get('date')})")
