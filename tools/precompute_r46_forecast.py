from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backtest") not in sys.path:
    sys.path.insert(0, str(ROOT / "backtest"))

DASH = ROOT / "dashboard"
OUT = ROOT / "output"
CACHE = ROOT / ".cache" / "backtest"
POLICY_DIR = OUT / "dashboard_policies" / "r46_bear_stop_mcore"
FORECAST_DIR = OUT / "beat_vni30_parallel" / "r46_live_forecast"

DEFAULT_NAV_VND = 1_000_000_000
BOARD_LOT = 100
LOCK_DATE = pd.Timestamp("2026-05-25")


def num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def floor_lot(shares: float) -> int:
    return int(math.floor(max(0.0, float(shares)) / BOARD_LOT) * BOARD_LOT)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def latest_status() -> dict:
    for path in [DASH / "dashboard_live_update_status.json", OUT / "dashboard_live_update_status.json"]:
        payload = read_json(path)
        if payload:
            payload["_source"] = str(path.relative_to(ROOT))
            return payload
    return {}


def next_monday(date_text: str | None) -> str | None:
    if not date_text:
        return None
    dt = pd.Timestamp(date_text).normalize()
    return (dt + timedelta(days=(7 - dt.weekday()) % 7 or 7)).date().isoformat()


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
        return {"date": pd.Timestamp(row[col]).date().isoformat(), "close": num(row.get("close"))}
    return {"date": None, "close": 0.0}


def current_copy_shares() -> dict[str, int]:
    state = read_json(OUT / "beat_vni30_parallel" / "paper_trade_v4_r46" / "paper_trade_state.json")
    cur = state.get("current_position") or {}
    rows = cur.get("holdings") or []
    if rows:
        out: dict[str, int] = {}
        for row in rows:
            sym = str(row.get("symbol", "")).upper().strip()
            shares = int(num(row.get("shares"), 0))
            if sym and shares > 0:
                out[sym] = shares
        if out:
            return out

    trades_path = POLICY_DIR / "trades.parquet"
    equity_path = POLICY_DIR / "equity_curve.parquet"
    if not trades_path.exists() or not equity_path.exists():
        return {}
    trades = pd.read_parquet(trades_path)
    if trades.empty or not {"symbol", "side", "shares"}.issubset(trades.columns):
        return {}
    signs = trades["side"].astype(str).str.upper().map({"BUY": 1, "SELL": -1}).fillna(0)
    raw = (pd.to_numeric(trades["shares"], errors="coerce").fillna(0) * signs).groupby(
        trades["symbol"].astype(str).str.upper()
    ).sum()
    scale = DEFAULT_NAV_VND / float(pd.read_parquet(equity_path)["nav"].iloc[-1])
    return {sym: floor_lot(float(shares) * scale) for sym, shares in raw.items() if float(shares) > 0}


def write_payload(payload: dict) -> None:
    DASH.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    (DASH / "r46_forecast.json").write_text(text + "\n", encoding="utf-8")
    (OUT / "r46_forecast_status.json").write_text(text + "\n", encoding="utf-8")
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def preserve_existing_computed(existing: dict, reason: str, error: str | None = None) -> bool:
    """Keep the last verified forecast when cloud recompute is unavailable."""
    if existing.get("status") != "COMPUTED":
        return False
    existing.setdefault("meta", {})
    existing["meta"]["cloudR46Refresh"] = reason
    if error:
        existing["meta"]["cloudR46RefreshError"] = error[:500]
    write_payload(existing)
    return True


def run_py(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT, check=True)


def maybe_rebuild_inputs(skip: bool) -> None:
    if skip:
        return
    run_py("backtest/technical_price_factor_stability.py")
    run_py("backtest/technical_t2_state_machine.py")


def build_tail_matrix(plan_date: str) -> pd.DataFrame:
    from backtest.yearly_floor_engine import build_candidate_matrix

    tail = build_candidate_matrix(
        start_date="2026-05-25",
        end_date=plan_date,
        cache_name="r46_live_forecast_tail_matrix.parquet",
        weekly_panel_cache_name="weekly_panel_v10_live.pkl",
        rebuild_weekly_panel=True,
        force=True,
    )
    tail["date"] = pd.to_datetime(tail["date"]).dt.normalize()
    return tail


def combined_matrix(tail: pd.DataFrame) -> pd.DataFrame:
    early = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_2016_2021_fullpanel.parquet")
    late = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix_live_preview.parquet")
    for df in (early, late, tail):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
    late = late[late["date"] > early["date"].max()].copy()
    tail = tail[tail["date"] > late["date"].max()].copy()
    common = [c for c in early.columns if c in late.columns and c in tail.columns]
    out = pd.concat([early[common], late[common], tail[common]], ignore_index=True)
    out = out.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(FORECAST_DIR / "combined_matrix.parquet", index=False)
    return out


def build_regime_panel(matrix: pd.DataFrame) -> pd.DataFrame:
    import pair657_regime_diagnostic_20260527 as reg

    temp = FORECAST_DIR / "combined_matrix.parquet"
    old_matrix = reg.MATRIX_2021
    try:
        reg.MATRIX_2021 = temp
        panel = reg.label_style_regimes(reg.label_regimes(reg.load_market_panel()))
    finally:
        reg.MATRIX_2021 = old_matrix

    # Add columns required by convex and retention layers from the richer cached panel when available.
    cached_path = CACHE / "regime_features_weekly.parquet"
    if cached_path.exists():
        cached = pd.read_parquet(cached_path)
        cached["date"] = pd.to_datetime(cached["date"]).dt.normalize()
        panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
        extra_cols = [c for c in cached.columns if c not in panel.columns and c != "date"]
        panel = panel.merge(cached[["date", *extra_cols]], on="date", how="left")

    matrix_dates = matrix[["date", "symbol", "avg_value_20d_bil", "ret13", "ret4"]].copy()
    for col in ["avg_value_20d_bil", "ret13", "ret4"]:
        matrix_dates[col] = pd.to_numeric(matrix_dates[col], errors="coerce")
    by_date = matrix_dates.groupby("date")
    derived = by_date.agg(
        breadth_top200=("ret13", lambda s: float((s > 0).mean())),
        breadth_recovery_2w=("ret4", lambda s: float((s > 0).mean())),
        vni_dispersion_4w=("ret4", "std"),
        mega_cap_ret13=("ret13", "median"),
        mid_cap_ret13=("ret13", "median"),
        vn30_breadth=("ret13", lambda s: float((s > 0).mean())),
        mega_cap_breadth=("ret13", lambda s: float((s > 0).mean())),
    ).reset_index()
    panel = panel.merge(derived, on="date", how="left", suffixes=("", "_derived"))
    for col in [
        "breadth_top200",
        "breadth_recovery_2w",
        "vni_dispersion_4w",
        "mega_cap_ret13",
        "mid_cap_ret13",
        "vn30_breadth",
        "mega_cap_breadth",
    ]:
        dcol = f"{col}_derived"
        if dcol in panel.columns:
            panel[col] = pd.to_numeric(panel.get(col), errors="coerce").fillna(panel[dcol])
            panel = panel.drop(columns=[dcol])

    panel.to_parquet(FORECAST_DIR / "regime_features_weekly.parquet", index=False)
    return panel


def patch_future_vni(plan_date: str):
    import technical_t2_portfolio as v1
    import technical_t2_dynamic_entry_v4 as dyn_v4
    import technical_t2_pair_ensemble_v5 as pair_v5

    orig = v1.load_vni

    def patched_load_vni():
        df = orig().copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        dt = pd.Timestamp(plan_date)
        if not df["date"].eq(dt).any():
            last = df.iloc[-1].copy()
            last["date"] = dt
            df = pd.concat([df, pd.DataFrame([last])], ignore_index=True)
        return df

    v1.load_vni = patched_load_vni
    dyn_v4.v1.load_vni = patched_load_vni
    pair_v5.v1.load_vni = patched_load_vni
    return orig


def tolerant_pair_targets(plan_date: str) -> pd.DataFrame:
    import technical_t2_dynamic_entry_v4 as dyn_v4
    import technical_t2_pair_ensemble_v5 as pair_v5

    patch_future_vni(plan_date)
    dyn_v4.TARGET_START = pd.Timestamp("2026-05-19")
    dyn_v4.TARGET_END = pd.Timestamp(plan_date)
    pair_v5.TARGET_START = pd.Timestamp("2026-05-19")
    pair_v5.TARGET_END = pd.Timestamp(plan_date)
    frames = []
    for name, alloc in [("soft15_fixed1", 0.65), ("cash10_fixed1", 0.35)]:
        targets, _signal_dates, _meta = pair_v5.component_targets(name)
        if targets.empty or "weight" not in targets.columns:
            continue
        targets = targets.copy()
        targets["weight"] = pd.to_numeric(targets["weight"], errors="coerce").fillna(0.0) * alloc
        frames.append(targets)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "weight"])
    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"]).dt.normalize()
    combined["symbol"] = combined["symbol"].astype(str).str.upper()
    grouped = combined.groupby(["date", "symbol"], as_index=False)["weight"].sum()
    sums = grouped.groupby("date")["weight"].transform("sum")
    grouped["weight"] = np.where(sums > 1.0, grouped["weight"] / sums, grouped["weight"])
    return grouped[grouped["date"].ge(LOCK_DATE)].copy()


def blend_direct_tail(pair: pd.DataFrame) -> pd.DataFrame:
    if pair.empty:
        return pd.DataFrame(columns=["date", "symbol", "weight"])
    out = pair[["date", "symbol", "weight"]].copy()
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce").fillna(0.0) * 0.10
    out["weight"] = out["weight"].clip(upper=0.40)
    return out[out["weight"] > 1e-8].copy()


def build_m_from_base(base: pd.DataFrame) -> pd.DataFrame:
    import pair657_m_stress_20260527 as m

    base = base.copy()
    base["date"] = pd.to_datetime(base["date"]).dt.normalize()
    base["symbol"] = base["symbol"].astype(str).str.upper()
    panel = m.load_panel()
    h = base.merge(panel, on="date", how="left")
    is_side = h["regime"].eq("SIDEWAYS")
    is_bear = h["regime"].eq("BEAR")
    deadside_drop = (
        is_side
        & pd.to_numeric(h["vni_range_13"], errors="coerce").le(0.070)
        & pd.to_numeric(h["median_ret13"], errors="coerce").le(0.020)
        & pd.to_numeric(h["high_liq_ret13_median"], errors="coerce").le(0.000)
    ) | is_bear
    guarded = h.loc[~deadside_drop, ["date", "symbol", "weight"]].copy()
    adapted = m.adaptive_recap(guarded, panel, broad_bull_cap=0.35, default_cap=0.55, top_k=1)
    vni_daily = m.load_vni_2012()
    weekly_dates = sorted(set(base["date"].dt.normalize()))
    vni_w = pd.DataFrame({"date": pd.to_datetime(weekly_dates)})
    vni_w = vni_w.merge(vni_daily.rename(columns={"close": "vni_close"})[["date", "vni_close"]], on="date", how="left")
    vni_w["vni_close"] = pd.to_numeric(vni_w["vni_close"], errors="coerce").ffill()
    final = m.apply_cash_overlay(adapted, m.v8_cash_dates(vni_w, threshold=-0.08, lag=1))
    return final[["date", "symbol", "weight"]].copy()


def apply_convex(base: pd.DataFrame, matrix: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    from baseline_steady_trend_overlay_20260527 import active_dates, build_overlay_holdings
    from m_core_convex_sleeve_probe_20260527 import blend_m_core, overlay_params

    if base.empty:
        return pd.DataFrame(columns=["date", "symbol", "weight"])
    p = overlay_params(0.10, 1)
    active = active_dates(regime, p["mode"], p)
    overlay = build_overlay_holdings(matrix, active, p)
    return blend_m_core(base, overlay, active, alpha=0.10, cap=0.55)[["date", "symbol", "weight"]].copy()


def apply_retention(base_full: pd.DataFrame, matrix: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    from m_core_conditional_retention_plateau_r15_20260528 import build_h

    regime_map = {pd.Timestamp(r["date"]): r for _, r in regime.iterrows()}
    h, _fires = build_h(base_full, matrix, regime_map, label="mega-2_mid-2", mega_min=-0.02, mid_min=-0.02)
    return h[["date", "symbol", "weight"]].copy()


def apply_participation_cap_custom(holdings: pd.DataFrame, matrix: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty:
        return pd.DataFrame(columns=["date", "symbol", "weight"])
    liq = matrix[["date", "symbol", "avg_value_20d_bil"]].copy()
    liq["date"] = pd.to_datetime(liq["date"]).dt.normalize()
    liq["symbol"] = liq["symbol"].astype(str).str.upper()
    x = holdings.merge(liq, on=["date", "symbol"], how="left")
    x["max_weight_by_liq"] = (0.20 * pd.to_numeric(x["avg_value_20d_bil"], errors="coerce")) / 3.0
    x["weight"] = x[["weight", "max_weight_by_liq"]].min(axis=1)
    x = x[x["weight"] > 1e-6].copy()
    return x[["date", "symbol", "weight"]].sort_values(["date", "weight", "symbol"], ascending=[True, False, True])


def validate_overlap(candidate: pd.DataFrame) -> tuple[bool, float, int]:
    official = pd.read_parquet(POLICY_DIR / "holdings.parquet")
    for df in (official, candidate):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        if "weight" not in df.columns and "target_weight" in df.columns:
            df["weight"] = df["target_weight"]
        df["weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)
    a = official[official["date"].le(LOCK_DATE)].groupby(["date", "symbol"])["weight"].sum()
    b = candidate[candidate["date"].le(LOCK_DATE)].groupby(["date", "symbol"])["weight"].sum()
    idx = a.index.union(b.index)
    diff = (a.reindex(idx, fill_value=0.0) - b.reindex(idx, fill_value=0.0)).abs()
    return bool(diff.max() <= 1e-9), float(diff.max()), int((diff > 1e-9).sum())


def generate_targets(plan_date: str, skip_rebuild_inputs: bool) -> tuple[pd.DataFrame, dict]:
    maybe_rebuild_inputs(skip_rebuild_inputs)
    tail = build_tail_matrix(plan_date)
    matrix = combined_matrix(tail)
    regime = build_regime_panel(matrix)

    pair_tail = tolerant_pair_targets(plan_date)
    direct_saved = pd.read_parquet(OUT / "beat_vni30_parallel" / "codex_pair657_direct_combo_20260527_fullsignals" / "best_holdings.parquet")
    direct_saved["date"] = pd.to_datetime(direct_saved["date"]).dt.normalize()
    direct_saved["symbol"] = direct_saved["symbol"].astype(str).str.upper()
    direct_tail = blend_direct_tail(pair_tail)
    direct_base = pd.concat([direct_saved, direct_tail[direct_tail["date"] > direct_saved["date"].max()]], ignore_index=True)
    m_base_generated = build_m_from_base(direct_base)

    official_m = pd.read_parquet(OUT / "beat_vni30_parallel" / "pair657_m_turnover_controls_20260527" / "best_15bps_holdings.parquet")
    official_m["date"] = pd.to_datetime(official_m["date"]).dt.normalize()
    m_base = pd.concat([official_m, m_base_generated[m_base_generated["date"] > official_m["date"].max()]], ignore_index=True)

    convex_generated = apply_convex(m_base, matrix, regime)
    official_convex = pd.read_parquet(OUT / "beat_vni30_parallel" / "m_core_convex_sleeve_probe_20260527" / "m_alpha0.10_top1" / "holdings.parquet")
    official_convex["date"] = pd.to_datetime(official_convex["date"]).dt.normalize()
    convex = pd.concat([official_convex, convex_generated[convex_generated["date"] > official_convex["date"].max()]], ignore_index=True)

    retained = apply_retention(convex, matrix, regime)
    capped = apply_participation_cap_custom(retained, matrix)
    capped = capped.sort_values(["date", "weight", "symbol"], ascending=[True, False, True]).reset_index(drop=True)

    ok, max_diff, diff_count = validate_overlap(capped)
    meta = {
        "tailMatrixRows": int(len(tail)),
        "tailMatrixDates": [d.date().isoformat() for d in sorted(tail["date"].dropna().unique())],
        "pairTailRows": int(len(pair_tail)),
        "latestComputedDate": plan_date,
        "overlapOk": ok,
        "overlapMaxDiff": max_diff,
        "overlapDiffCount": diff_count,
    }
    if ok:
        FORECAST_DIR.mkdir(parents=True, exist_ok=True)
        capped.to_parquet(FORECAST_DIR / "latest_targets.parquet", index=False)
        meta_path = FORECAST_DIR / "latest_targets_meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return capped, meta


def build_rows(target: pd.DataFrame, plan_date: str) -> list[dict]:
    current = current_copy_shares()
    if target.empty:
        weights: dict[str, float] = {}
    else:
        t = target.copy()
        t["date"] = pd.to_datetime(t["date"]).dt.normalize()
        t["symbol"] = t["symbol"].astype(str).str.upper()
        t["weight"] = pd.to_numeric(t["weight"], errors="coerce").fillna(0.0)
        weights = dict(zip(t.loc[t["date"].eq(pd.Timestamp(plan_date)), "symbol"], t.loc[t["date"].eq(pd.Timestamp(plan_date)), "weight"]))

    rows = []
    for symbol in sorted(set(current) | set(weights)):
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
            order_shares = floor_lot(abs(delta))
        rows.append(
            {
                "displayPlanDate": plan_date,
                "planDate": plan_date,
                "symbol": symbol,
                "action": action,
                "status": "DỰ KIẾN",
                "currentPrice": px,
                "priceAsOf": quote.get("date"),
                "targetWeight": round(target_weight * 100.0, 4),
                "currentCopyShares": current_shares,
                "targetCopyShares": target_shares,
                "orderShares": order_shares,
                "note": "Precompute R46 trên GitHub; chỉ chốt sau close thứ 6.",
            }
        )
    return rows


def fail_payload(as_of: str | None, plan_date: str | None, reason: str, message: str, meta: dict | None = None) -> dict:
    return {
        "schemaVersion": 2,
        "policy": "r46_bear_stop_mcore",
        "asOf": as_of,
        "planDate": plan_date,
        "status": "NOT_COMPUTED",
        "reason": reason,
        "message": message,
        "rows": [],
        "meta": meta or {},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-rebuild-inputs", action="store_true")
    args = parser.parse_args()

    status = latest_status()
    as_of = status.get("latestPriceDate") or status.get("asOf")
    plan_date = next_monday(as_of)
    if not as_of or not plan_date:
        write_payload(fail_payload(as_of, plan_date, "missing_live_status", "Thiếu live status để xác định ngày forecast."))
        return

    full_status = read_json(OUT / "full_universe_live_update_status.json")
    existing = read_json(DASH / "r46_forecast.json")
    if (
        full_status
        and int(num(full_status.get("symbolsAttempted"), 0)) > 0
        and int(num(full_status.get("symbolsUpdated"), 0)) == 0
        and existing.get("status") == "COMPUTED"
    ):
        existing.setdefault("meta", {})
        existing["meta"]["cloudFullUniverseRefresh"] = "FAILED_ALL_REQUESTS_KEEPING_EXISTING_FORECAST"
        write_payload(existing)
        return
    history_cache_date = ((full_status.get("historyCache") or {}).get("latestPriceDate") if full_status else None)
    if full_status and as_of and history_cache_date and str(history_cache_date) < str(as_of):
        if preserve_existing_computed(
            existing,
            "HISTORY_CACHE_STALE_KEEPING_LAST_COMPUTED_FORECAST",
            f"history_cache_latest={history_cache_date}; live_as_of={as_of}",
        ):
            return

    try:
        targets, meta = generate_targets(plan_date, args.skip_rebuild_inputs)
    except Exception as exc:
        if preserve_existing_computed(
            existing,
            "CHAIN_UNAVAILABLE_KEEPING_LAST_COMPUTED_FORECAST",
            str(exc),
        ):
            return
        write_payload(
            fail_payload(
                as_of,
                plan_date,
                "r46_chain_exception",
                "Precompute R46 bị lỗi nên dashboard không publish lệnh mới.",
                {"error": str(exc)[:500]},
            )
        )
        raise

    if not meta.get("overlapOk"):
        write_payload(
            fail_payload(
                as_of,
                plan_date,
                "r46_overlap_mismatch",
                "Chain mới không khớp artifact R46 khóa tới 2026-05-25; không publish forecast.",
                meta,
            )
        )
        return

    rows = build_rows(targets, plan_date)
    payload = {
        "schemaVersion": 2,
        "policy": "r46_bear_stop_mcore",
        "asOf": as_of,
        "planDate": plan_date,
        "generatedAtSource": status.get("_source"),
        "status": "COMPUTED",
        "source": "output/beat_vni30_parallel/r46_live_forecast/latest_targets.parquet",
        "message": "Forecast R46 đã precompute từ full-universe live chain.",
        "rows": rows,
        "meta": meta,
    }
    write_payload(payload)


if __name__ == "__main__":
    main()
