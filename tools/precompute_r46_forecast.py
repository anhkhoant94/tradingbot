from __future__ import annotations

import json
import math
import os
from datetime import timedelta
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
OUT = ROOT / "output"
POLICY_DIR = OUT / "dashboard_policies" / "r46_bear_stop_mcore"
CACHE = ROOT / ".cache" / "backtest"

DEFAULT_NAV_VND = 1_000_000_000
BOARD_LOT = 100

STATUS_CANDIDATES = [
    DASH / "dashboard_live_update_status.json",
    OUT / "dashboard_live_update_status.json",
]
TARGET_CANDIDATES = [
    Path(os.environ["R46_FORECAST_TARGETS"]) if os.environ.get("R46_FORECAST_TARGETS") else None,
    POLICY_DIR / "forecast_targets.parquet",
    OUT / "beat_vni30_parallel" / "r46_live_forecast" / "latest_targets.parquet",
]


def num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def floor_lot(shares: float) -> int:
    if shares <= 0:
        return 0
    return int(math.floor(shares / BOARD_LOT) * BOARD_LOT)


def next_monday(date_text: str | None) -> str | None:
    if not date_text:
        return None
    dt = pd.Timestamp(date_text).normalize()
    return (dt + timedelta(days=(7 - dt.weekday()) % 7 or 7)).date().isoformat()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def latest_status() -> dict:
    for path in STATUS_CANDIDATES:
        payload = read_json(path)
        if payload:
            payload["_source"] = str(path.relative_to(ROOT))
            return payload
    return {}


def latest_quote(symbol: str) -> dict:
    for directory in [CACHE / "history_clean", CACHE / "history", ROOT / ".cache" / "history"]:
        path = directory / f"{symbol}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty:
            continue
        col = "time" if "time" in df.columns else "date"
        df = df.copy()
        df[col] = pd.to_datetime(df[col], errors="coerce")
        df = df.dropna(subset=[col]).sort_values(col)
        if df.empty:
            continue
        row = df.iloc[-1]
        return {
            "date": pd.Timestamp(row[col]).date().isoformat(),
            "close": num(row.get("close")),
        }
    return {"date": None, "close": 0.0}


def current_copy_shares() -> dict[str, int]:
    trades_path = POLICY_DIR / "trades.parquet"
    if not trades_path.exists():
        return {}
    trades = pd.read_parquet(trades_path)
    if trades.empty or not {"symbol", "side", "shares"}.issubset(trades.columns):
        return {}
    signs = trades["side"].astype(str).str.upper().map({"BUY": 1, "SELL": -1}).fillna(0)
    raw = (pd.to_numeric(trades["shares"], errors="coerce").fillna(0) * signs).groupby(
        trades["symbol"].astype(str).str.upper()
    ).sum()
    scale = DEFAULT_NAV_VND / float(pd.read_parquet(POLICY_DIR / "equity_curve.parquet")["nav"].iloc[-1])
    return {sym: floor_lot(float(shares) * scale) for sym, shares in raw.items() if float(shares) > 0}


def load_target_rows(required_signal_date: str | None) -> tuple[pd.DataFrame | None, str | None]:
    for path in TARGET_CANDIDATES:
        if path is None or not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty or not {"date", "symbol"}.issubset(df.columns):
            continue
        weight_col = "weight" if "weight" in df.columns else "target_weight" if "target_weight" in df.columns else None
        if weight_col is None:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["weight"] = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
        df = df[df["weight"].gt(1e-8)]
        if df.empty:
            continue
        latest_date = df["date"].max()
        if required_signal_date and latest_date < pd.Timestamp(required_signal_date):
            continue
        return df[df["date"].eq(latest_date)].copy(), str(path.relative_to(ROOT))
    return None, None


def build_forecast() -> dict:
    status = latest_status()
    as_of = status.get("latestPriceDate") or status.get("asOf")
    plan_date = next_monday(as_of)
    current = current_copy_shares()
    target_df, target_source = load_target_rows(plan_date)

    base = {
        "schemaVersion": 1,
        "policy": "r46_bear_stop_mcore",
        "asOf": as_of,
        "planDate": plan_date,
        "generatedAtSource": status.get("_source"),
        "rows": [],
    }

    if target_df is None:
        base.update(
            {
                "status": "NOT_COMPUTED",
                "reason": "missing_fresh_r46_target_rows",
                "message": (
                    "GitHub runner da cap nhat gia, nhung chua co target R46 moi hon policy artifact. "
                    "Khong publish lenh mua/ban de tranh sizing sai."
                ),
            }
        )
        return base

    rows = []
    target_symbols = set(target_df["symbol"])
    weights = dict(zip(target_df["symbol"], target_df["weight"]))
    for symbol in sorted(set(current) | target_symbols):
        quote = latest_quote(symbol)
        px = num(quote.get("close"))
        target_weight = num(weights.get(symbol))
        target_shares = floor_lot(DEFAULT_NAV_VND * target_weight / (px * 1000.0)) if px > 0 else 0
        current_shares = int(current.get(symbol, 0))
        delta = target_shares - current_shares
        if abs(delta) < BOARD_LOT:
            action = "GIỮ"
            order_shares = 0
        elif delta > 0:
            action = "MUA MỚI" if current_shares <= 0 else "MUA THÊM"
            order_shares = floor_lot(delta)
        else:
            action = "BÁN HẾT" if target_shares <= 0 else "BÁN BỚT"
            order_shares = abs(floor_lot(delta))
        rows.append(
            {
                "displayPlanDate": plan_date,
                "planDate": plan_date,
                "symbol": symbol,
                "action": action,
                "status": "DỰ KIẾN",
                "currentPrice": px,
                "priceAsOf": quote.get("date"),
                "targetWeight": round(target_weight * 100.0, 2),
                "currentCopyShares": current_shares,
                "targetCopyShares": target_shares,
                "orderShares": order_shares,
                "note": "Tính từ target R46 precompute trên GitHub; chỉ chốt sau close thứ 6.",
            }
        )
    base.update(
        {
            "status": "COMPUTED",
            "source": target_source,
            "message": "Forecast R46 được precompute từ target rows mới.",
            "rows": rows,
        }
    )
    return base


def main() -> None:
    payload = build_forecast()
    DASH.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    (DASH / "r46_forecast.json").write_text(text + "\n", encoding="utf-8")
    (OUT / "r46_forecast_status.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
