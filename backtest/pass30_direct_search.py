"""Direct pass30 search from weekly candidate matrix.

Goal: find a policy with every calendar year 2021-2026 >= 30%.
The script is deliberately separate from older equity-curve overlay hunters so
it can search raw candidate-selection rules, then export a verified candidate
for the dashboard if one is found.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import json
import math
import os
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "backtest"
DEFAULT_OUT = ROOT / "output" / "pass30_direct_search"
DEFAULT_OUT.mkdir(parents=True, exist_ok=True)

FEE_BUY = 0.0015
FEE_SELL_TAX = 0.0025


def atomic_temp_path(path: Path) -> Path:
    return path.with_name(f".{path.stem}.{os.getpid()}.tmp{path.suffix}")


def fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = atomic_temp_path(path)
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def atomic_write_csv(df: pd.DataFrame, path: Path, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = atomic_temp_path(path)
    df.to_csv(tmp, **kwargs)
    fsync_file(tmp)
    tmp.replace(path)


def atomic_write_parquet(df: pd.DataFrame, path: Path, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = atomic_temp_path(path)
    df.to_parquet(tmp, **kwargs)
    fsync_file(tmp)
    tmp.replace(path)


FEATURES = [
    "fa_rank_all",
    "mom_rank_all",
    "rs_rank_all",
    "high_rank_all",
    "flow_rank_all",
    "industry_score",
    "tech_score_base",
]

EXTRA_NUMERIC_COLS = [
    "composite_score",
    "avg_value_20d_bil",
    "rsi14",
    "ret4",
    "ret8",
    "ret13",
    "ret26",
    "ret52",
    "rs13",
    "near_high52",
    "moneyflow_score",
    "trend_template",
    "industry_score",
    "industry_rank",
    "breadth",
    "vni_close",
    "vni_ret4",
    "vni_ret8",
    "vni_ret13",
    "vni_ret26",
    "vni_ret52",
    "vni_sma30",
    "vni_sma40",
    "vni_vol13",
    "vni_ath_proximity",
    "vni_distance_52w_high",
    "vni_ma200_slope_4w",
    "breadth_top200",
    "breadth_ma8",
    "breadth_recovery_2w",
    "vni_dispersion_4w",
    "hnx_flag",
    "smallcap_rs13",
    "smallcap_vs_hose13",
    "market_cap_bil",
    "largecap_flag",
    "mega_cap_flag",
    "vn30_rs26",
    "vn30_breadth",
    "mega_cap_leadership",
    "market_cap_trailing60_bil",
    "mega_cap_leadership_pit",
    "mega_cap_breadth",
    "rank_velocity_4w",
    "money_flow_5d",
]

DEFENSIVE_INDUSTRY_NAMES = {
    "Ban le",
    "Cham soc suc khoe",
    "Thuc pham - Do uong",
    "Tien ich",
}


def _latest_lte(dates: list[pd.Timestamp], ts: pd.Timestamp) -> pd.Timestamp | None:
    pos = bisect_right(dates, pd.Timestamp(ts)) - 1
    if pos < 0:
        return None
    return dates[pos]


def _read_defensive_sector_file(path: Path) -> set[str]:
    out = set()
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def load_external_labels(label_dir: Path | None) -> dict | None:
    """Load Claude F2 labels.

    Labels are intentionally joined backward only: a rebalance date can use the
    latest label date that is less than or equal to that rebalance date.
    """
    if label_dir is None:
        return None
    label_dir = Path(label_dir)
    if not label_dir.exists():
        raise FileNotFoundError(f"Label directory does not exist: {label_dir}")

    labels: dict = {"label_dir": str(label_dir)}

    cluster_path = label_dir / "cluster_breakout_labels.parquet"
    if not cluster_path.exists():
        cluster_path = label_dir / "cluster_breakout_labels.csv"
    if cluster_path.exists():
        if cluster_path.suffix.lower() == ".parquet":
            cluster = pd.read_parquet(cluster_path)
        else:
            cluster = pd.read_csv(cluster_path)
        cluster["week_anchor"] = pd.to_datetime(cluster["week_anchor"])
        cluster["industry"] = cluster["industry"].astype(str)
        for col in ["cluster_count_4w", "cluster_strength_4w"]:
            cluster[col] = pd.to_numeric(cluster[col], errors="coerce").fillna(0.0)
        cluster["cluster_signal"] = cluster["cluster_signal"].astype(bool)
        cluster = cluster.sort_values(["week_anchor", "industry"])
        cluster_dates = sorted(pd.Timestamp(x) for x in cluster["week_anchor"].dropna().unique())
        cluster_map = {}
        for date, group in cluster.groupby("week_anchor"):
            cluster_map[pd.Timestamp(date)] = {
                str(row.industry): (
                    float(row.cluster_count_4w),
                    float(row.cluster_strength_4w),
                    bool(row.cluster_signal),
                )
                for row in group.itertuples(index=False)
            }
        labels["cluster_dates"] = cluster_dates
        labels["cluster_map"] = cluster_map
        labels["cluster_rows"] = int(len(cluster))
        labels["cluster_industries"] = int(cluster["industry"].nunique())

    defensive = _read_defensive_sector_file(label_dir / "defensive_sectors_list.txt")
    if defensive:
        labels["defensive_sectors"] = defensive

    h11_path = label_dir / "h11_overlay_state_timeline.csv"
    if h11_path.exists():
        h11 = pd.read_csv(h11_path)
        h11["date"] = pd.to_datetime(h11["date"])
        h11["asym_state"] = h11["asym_state"].astype(str)
        h11["sym_state"] = h11["sym_state"].astype(str)
        h11_dates = sorted(pd.Timestamp(x) for x in h11["date"].dropna().unique())
        h11_map = {
            pd.Timestamp(row.date): {
                "asym_state": str(row.asym_state),
                "sym_state": str(row.sym_state),
                "v4w": float(row.v4w),
                "v8w": float(row.v8w),
            }
            for row in h11.itertuples(index=False)
        }
        labels["h11_dates"] = h11_dates
        labels["h11_map"] = h11_map
        labels["h11_rows"] = int(len(h11))

    selector_path = label_dir.parent / "claude_g2_selector_labels" / "weekly_selector_labels.csv"
    if not selector_path.exists():
        selector_path = label_dir / "weekly_selector_labels.csv"
    if selector_path.exists():
        selector = pd.read_csv(selector_path)
        selector["week_anchor"] = pd.to_datetime(selector["week_anchor"])
        bool_cols = [
            "cluster_upside_ok",
            "cluster_overheat",
            "risk_floor_required",
            "winner_protect_ok",
            "rotation_reentry_ok",
        ]
        for col in bool_cols:
            selector[col] = selector[col].astype(bool)
        selector_dates = sorted(pd.Timestamp(x) for x in selector["week_anchor"].dropna().unique())
        selector_map = {}
        for row in selector.itertuples(index=False):
            selector_map[pd.Timestamp(row.week_anchor)] = {
                "cluster_upside_ok": bool(row.cluster_upside_ok),
                "cluster_overheat": bool(row.cluster_overheat),
                "risk_floor_required": bool(row.risk_floor_required),
                "winner_protect_ok": bool(row.winner_protect_ok),
                "rotation_reentry_ok": bool(row.rotation_reentry_ok),
                "asym_state": str(getattr(row, "asym_state", "")),
                "vni_4w_pct": float(getattr(row, "vni_4w_pct", 0.0)),
                "vni_8w_pct": float(getattr(row, "vni_8w_pct", 0.0)),
                "total_clusters": float(getattr(row, "total_clusters", 0.0)),
                "max_cluster_strength": float(getattr(row, "max_cluster_strength", 0.0)),
            }
        labels["selector_dates"] = selector_dates
        labels["selector_map"] = selector_map
        labels["selector_rows"] = int(len(selector))

    return labels


def normalize_vietnamese(value: str) -> str:
    repl = {
        "á": "a", "à": "a", "ả": "a", "ã": "a", "ạ": "a", "ă": "a", "ắ": "a", "ằ": "a", "ẳ": "a", "ẵ": "a", "ặ": "a", "â": "a", "ấ": "a", "ầ": "a", "ẩ": "a", "ẫ": "a", "ậ": "a",
        "đ": "d",
        "é": "e", "è": "e", "ẻ": "e", "ẽ": "e", "ẹ": "e", "ê": "e", "ế": "e", "ề": "e", "ể": "e", "ễ": "e", "ệ": "e",
        "í": "i", "ì": "i", "ỉ": "i", "ĩ": "i", "ị": "i",
        "ó": "o", "ò": "o", "ỏ": "o", "õ": "o", "ọ": "o", "ô": "o", "ố": "o", "ồ": "o", "ổ": "o", "ỗ": "o", "ộ": "o", "ơ": "o", "ớ": "o", "ờ": "o", "ở": "o", "ỡ": "o", "ợ": "o",
        "ú": "u", "ù": "u", "ủ": "u", "ũ": "u", "ụ": "u", "ư": "u", "ứ": "u", "ừ": "u", "ử": "u", "ữ": "u", "ự": "u",
        "ý": "y", "ỳ": "y", "ỷ": "y", "ỹ": "y", "ỵ": "y",
    }
    out = []
    for char in str(value).lower():
        out.append(repl.get(char, char))
    return "".join(out).title()


def load_matrix() -> pd.DataFrame:
    df = pd.read_parquet(CACHE / "yearly_floor_candidate_matrix.parquet").copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= "2021-01-01") & (df["date"] <= "2026-05-18")].copy()
    df = df.sort_values(["symbol", "date"])
    df["next_close"] = df.groupby("symbol")["close"].shift(-1)
    df["next_ret"] = (df["next_close"] / df["close"] - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    for col in FEATURES + EXTRA_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan
    return df


def prepare_matrix(df: pd.DataFrame, labels: dict | None = None) -> dict:
    groups = []
    vni_close_by_date = {}
    label_usage = []
    for d, g in df.groupby("date", sort=True):
        ts = pd.Timestamp(d)
        group = {
            "date": ts,
            "symbol": g["symbol"].astype(str).to_numpy(),
            "industry_name": g["industry_name"].fillna("").astype(str).to_numpy(),
            "sector_group": g["sector_group"].fillna("").astype(str).to_numpy() if "sector_group" in g.columns else np.array([""] * len(g)),
            "hard_gate": g["hard_gate"].fillna("").astype(str).to_numpy(),
            "status": g["status"].fillna("").astype(str).to_numpy(),
        }
        for col in set(FEATURES + EXTRA_NUMERIC_COLS + ["next_ret"]):
            if col in g.columns:
                group[col] = pd.to_numeric(g[col], errors="coerce").to_numpy(dtype=float)
            else:
                group[col] = np.full(len(g), np.nan, dtype=float)
        near_high = np.nan_to_num(group["near_high52"], nan=0.0)
        ret4 = np.nan_to_num(group["ret4"], nan=0.0)
        breakout = (near_high >= 0.97) & (ret4 >= -0.02)
        cluster_count = np.zeros(len(g), dtype=float)
        industry_values = group["industry_name"]
        for industry in np.unique(industry_values):
            industry_mask = industry_values == industry
            count = int((industry_mask & breakout).sum())
            cluster_count[industry_mask] = count
        group["cluster_breakout_count_internal"] = cluster_count
        group["cluster_breakout_count"] = cluster_count
        group["cluster_strength_4w"] = np.zeros(len(g), dtype=float)
        group["cluster_breakout_flag"] = (cluster_count >= 2).astype(float)
        cluster_label_date = None
        cluster_lag = np.nan
        if labels and labels.get("cluster_dates"):
            cluster_label_date = _latest_lte(labels["cluster_dates"], ts)
            if cluster_label_date is not None:
                cluster_lag = (ts - cluster_label_date).days
                cluster_lookup = labels["cluster_map"].get(cluster_label_date, {})
                ext_count = np.zeros(len(g), dtype=float)
                ext_strength = np.zeros(len(g), dtype=float)
                ext_signal = np.zeros(len(g), dtype=bool)
                for i, industry in enumerate(industry_values):
                    count, strength, signal = cluster_lookup.get(str(industry), (0.0, 0.0, False))
                    ext_count[i] = count
                    ext_strength[i] = strength
                    ext_signal[i] = signal
                group["cluster_breakout_count"] = ext_count
                group["cluster_strength_4w"] = ext_strength
                group["cluster_breakout_flag"] = ((ext_count >= 2.0) | ext_signal).astype(float)
        group["at_52w_high_flag"] = (near_high >= 0.99).astype(float)
        if labels and labels.get("defensive_sectors"):
            defensive_sectors = labels["defensive_sectors"]
            group["defensive_flag"] = np.array([name in defensive_sectors for name in group["industry_name"]], dtype=float)
        else:
            group["defensive_flag"] = np.array(
                [normalize_vietnamese(name) in DEFENSIVE_INDUSTRY_NAMES for name in group["industry_name"]],
                dtype=float,
            )
        h11_label_date = None
        h11_state = None
        h11_sym_state = None
        h11_lag = np.nan
        if labels and labels.get("h11_dates"):
            h11_label_date = _latest_lte(labels["h11_dates"], ts)
            if h11_label_date is not None:
                h11_lag = (ts - h11_label_date).days
                h11_row = labels["h11_map"].get(h11_label_date, {})
                h11_state = h11_row.get("asym_state")
                h11_sym_state = h11_row.get("sym_state")
        group["external_h11_bear"] = np.full(len(g), np.nan if h11_state is None else float(h11_state == "BEAR"))
        selector_label_date = None
        selector_lag = np.nan
        selector_row = {}
        if labels and labels.get("selector_dates"):
            selector_label_date = _latest_lte(labels["selector_dates"], ts)
            if selector_label_date is not None:
                selector_lag = (ts - selector_label_date).days
                selector_row = labels["selector_map"].get(selector_label_date, {})
        for col in [
            "cluster_upside_ok",
            "cluster_overheat",
            "risk_floor_required",
            "winner_protect_ok",
            "rotation_reentry_ok",
        ]:
            group[f"selector_{col}"] = np.full(len(g), float(bool(selector_row.get(col, False))))
        groups.append(group)
        vni_close_by_date[ts] = float(g["vni_close"].iloc[0])
        label_usage.append({
            "date": ts,
            "cluster_label_date": cluster_label_date,
            "cluster_label_lag_days": cluster_lag,
            "h11_label_date": h11_label_date,
            "h11_label_lag_days": h11_lag,
            "h11_asym_state": h11_state,
            "h11_sym_state": h11_sym_state,
            "selector_label_date": selector_label_date,
            "selector_label_lag_days": selector_lag,
            "selector_risk_floor_required": bool(selector_row.get("risk_floor_required", False)),
            "selector_cluster_overheat": bool(selector_row.get("cluster_overheat", False)),
        })
    dates = [g["date"] for g in groups]
    vni_next_ret = {}
    for idx, d in enumerate(dates):
        if idx + 1 < len(dates):
            cur = vni_close_by_date.get(d, np.nan)
            nxt = vni_close_by_date.get(dates[idx + 1], np.nan)
            vni_next_ret[d] = 0.0 if pd.isna(cur) or pd.isna(nxt) or cur <= 0 else nxt / cur - 1
        else:
            vni_next_ret[d] = 0.0
    return {
        "matrix": df,
        "groups": groups,
        "vni_next_ret": vni_next_ret,
        "labels": labels,
        "label_usage": pd.DataFrame(label_usage),
    }


def yearly_metrics(eq: pd.DataFrame) -> dict:
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    yearly = {}
    for y, g in eq.groupby(eq["date"].dt.year):
        if 2021 <= int(y) <= 2026 and len(g) > 1:
            yearly[int(y)] = (float(g["nav"].iloc[-1]) / float(g["nav"].iloc[0]) - 1) * 100
    nav = eq["nav"].astype(float)
    rets = eq["ret"].astype(float)
    yrs = (eq["date"].iloc[-1] - eq["date"].iloc[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    maxdd = (nav / nav.cummax() - 1).min()
    sharpe = rets.mean() / rets.std() * math.sqrt(52) if rets.std() > 0 else 0.0
    pass30 = sum(yearly.get(y, -999) >= 30 for y in range(2021, 2027))
    return {
        "cagr": cagr * 100,
        "maxdd": maxdd * 100,
        "sharpe": sharpe,
        "pass30": pass30,
        "min_year": min(yearly.get(y, np.nan) for y in range(2021, 2027)),
        **{f"y{y}": yearly.get(y, np.nan) for y in range(2021, 2027)},
    }


def yearly_returns_table(eq: pd.DataFrame, matrix: pd.DataFrame) -> pd.DataFrame:
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    vni = (
        matrix[["date", "vni_close"]]
        .dropna()
        .assign(date=lambda x: pd.to_datetime(x["date"]))
        .drop_duplicates("date")
        .sort_values("date")
    )
    rows = []
    for year in range(2021, 2027):
        g = eq[eq["date"].dt.year == year].sort_values("date")
        strategy_ret = np.nan
        vni_ret = np.nan
        if len(g) > 1:
            strategy_ret = (float(g["nav"].iloc[-1]) / float(g["nav"].iloc[0]) - 1) * 100
            v = vni[(vni["date"] >= g["date"].iloc[0]) & (vni["date"] <= g["date"].iloc[-1])]
            if len(v) > 1 and float(v["vni_close"].iloc[0]) > 0:
                vni_ret = (float(v["vni_close"].iloc[-1]) / float(v["vni_close"].iloc[0]) - 1) * 100
        rows.append({
            "year": year,
            "strategy_return_pct": strategy_ret,
            "vni_return_pct": vni_ret,
            "beat_vni": bool(pd.notna(strategy_ret) and pd.notna(vni_ret) and strategy_ret > vni_ret),
            "pass30": bool(pd.notna(strategy_ret) and strategy_ret >= 30),
        })
    return pd.DataFrame(rows)


def vni_yearly_returns(matrix: pd.DataFrame) -> dict[int, float]:
    vni = (
        matrix[["date", "vni_close"]]
        .dropna()
        .assign(date=lambda x: pd.to_datetime(x["date"]))
        .drop_duplicates("date")
        .sort_values("date")
    )
    out = {}
    for year, group in vni.groupby(vni["date"].dt.year):
        year = int(year)
        if 2021 <= year <= 2026 and len(group) > 1 and float(group["vni_close"].iloc[0]) > 0:
            out[year] = (float(group["vni_close"].iloc[-1]) / float(group["vni_close"].iloc[0]) - 1) * 100
    return out


def add_vni30_metrics(metrics: dict, vni_returns: dict[int, float]) -> dict:
    enriched = dict(metrics)
    edges = []
    passes = []
    for year in range(2021, 2027):
        strategy_ret = float(enriched.get(f"y{year}", np.nan))
        vni_ret = float(vni_returns.get(year, np.nan))
        edge = strategy_ret - vni_ret if pd.notna(strategy_ret) and pd.notna(vni_ret) else np.nan
        enriched[f"vni_y{year}"] = vni_ret
        enriched[f"edge_y{year}"] = edge
        passed = bool(pd.notna(edge) and edge >= 30.0)
        enriched[f"pass_vni30_y{year}"] = passed
        if pd.notna(edge):
            edges.append(float(edge))
            passes.append(passed)
    enriched["pass_vni30"] = int(sum(passes))
    enriched["min_edge_vs_vni"] = min(edges) if edges else np.nan
    enriched["min_gap_to_vni30"] = (min(edges) - 30.0) if edges else np.nan
    return enriched


def normalize_weights(x: pd.DataFrame, cfg: dict, exposure: float) -> dict[str, float]:
    if x.empty or exposure <= 0:
        return {}
    x = x.head(int(cfg["max_holdings"])).copy()
    raw = x["score"].clip(lower=0) ** float(cfg["score_power"])
    if raw.sum() <= 0:
        raw = pd.Series(1.0, index=x.index)
    raw = raw / raw.sum()
    out = {}
    rank_caps = cfg.get("rank_weight_caps")
    if isinstance(rank_caps, str):
        rank_caps = [float(v) for v in rank_caps.split(",") if str(v).strip()]
    elif rank_caps is not None:
        rank_caps = [float(v) for v in rank_caps]
    for rank, (idx, row) in enumerate(x.iterrows()):
        liq_cap = float(row["avg_value_20d_bil"]) * 1e9 * float(cfg["liq_participation"]) / float(cfg["nav"])
        cap = min(float(cfg["max_weight"]), max(0.0, liq_cap))
        if rank_caps:
            cap = min(cap, float(rank_caps[min(rank, len(rank_caps) - 1)]))
        out[str(row["symbol"])] = min(float(raw.loc[idx] * exposure), cap)
    for _ in range(5):
        unused = exposure - sum(out.values())
        if unused <= 1e-8:
            break
        open_symbols = []
        for rank, (s, w) in enumerate(out.items()):
            row = x[x["symbol"].eq(s)].iloc[0]
            liq_cap = float(row["avg_value_20d_bil"]) * 1e9 * float(cfg["liq_participation"]) / float(cfg["nav"])
            cap = min(float(cfg["max_weight"]), max(0.0, liq_cap))
            if rank_caps:
                cap = min(cap, float(rank_caps[min(rank, len(rank_caps) - 1)]))
            if cap - w > 1e-8:
                open_symbols.append((s, cap - w))
        if not open_symbols:
            break
        add = unused / len(open_symbols)
        for s, room in open_symbols:
            out[s] += min(add, room)
    return {s: w for s, w in out.items() if w > 1e-7}


def is_risk_off(row: pd.Series, cfg: dict) -> bool:
    close = float(row["vni_close"])
    sma40 = float(row["vni_sma40"]) if pd.notna(row["vni_sma40"]) else np.nan
    ret13 = float(row["vni_ret13"]) if pd.notna(row["vni_ret13"]) else 0.0
    return bool(pd.notna(sma40) and close < sma40 and ret13 < float(cfg["riskoff_ret13"]))


def index_weight_for_date(g: pd.DataFrame, cfg: dict) -> float:
    if cfg.get("hedge_mode", "off") == "off":
        return 0.0
    row = g.iloc[0]
    close = float(row["vni_close"])
    sma40 = float(row["vni_sma40"]) if pd.notna(row["vni_sma40"]) else np.nan
    sma30 = float(row["vni_sma30"]) if pd.notna(row["vni_sma30"]) else np.nan
    ret13 = float(row["vni_ret13"]) if pd.notna(row["vni_ret13"]) else 0.0
    ret26 = float(row["vni_ret26"]) if pd.notna(row["vni_ret26"]) else 0.0
    if pd.isna(sma40) or close <= 0:
        return 0.0
    risk = 0.0
    if close < sma40 and ret13 < -0.03:
        risk = max(risk, float(cfg["hedge_base"]))
    if close < sma40 and ret13 < -0.08:
        risk = max(risk, float(cfg["hedge_mid"]))
    if pd.notna(sma30) and close < sma30 and ret26 < -0.15:
        risk = max(risk, float(cfg["hedge_deep"]))
    return -min(risk, float(cfg["max_short_index"]))


def is_risk_off_values(vni_close: float, vni_sma40: float, vni_ret13: float, cfg: dict) -> bool:
    return bool(np.isfinite(vni_sma40) and vni_close < vni_sma40 and vni_ret13 < float(cfg["riskoff_ret13"]))


def index_weight_for_group(g: dict, cfg: dict) -> float:
    if cfg.get("hedge_mode", "off") == "off":
        return 0.0
    close = float(g["vni_close"][0])
    sma40 = float(g["vni_sma40"][0])
    sma30 = float(g["vni_sma30"][0])
    ret13 = float(g["vni_ret13"][0]) if np.isfinite(g["vni_ret13"][0]) else 0.0
    ret26 = float(g["vni_ret26"][0]) if np.isfinite(g["vni_ret26"][0]) else 0.0
    if not np.isfinite(sma40) or close <= 0:
        return 0.0
    risk = 0.0
    if close < sma40 and ret13 < -0.03:
        risk = max(risk, float(cfg["hedge_base"]))
    if close < sma40 and ret13 < -0.08:
        risk = max(risk, float(cfg["hedge_mid"]))
    if np.isfinite(sma30) and close < sma30 and ret26 < -0.15:
        risk = max(risk, float(cfg["hedge_deep"]))
    return -min(risk, float(cfg["max_short_index"]))


def exposure_for_date(g: pd.DataFrame, cfg: dict, nav: float, year_start_nav: float) -> float:
    row = g.iloc[0]
    breadth = float((g["trend_template"] == 1).mean())
    risk_off = is_risk_off(row, cfg)
    exposure = float(cfg["riskoff_exposure"] if risk_off else cfg["base_exposure"])
    if breadth < float(cfg["breadth_floor"]):
        exposure *= float(cfg["weak_breadth_mult"])
    if bool(cfg.get("annual_pacing")):
        week = max(1, int(pd.Timestamp(row["date"]).isocalendar().week))
        target_ytd = (1 + float(cfg["annual_target"])) ** (week / 52) - 1
        ytd = nav / max(year_start_nav, 1e-9) - 1
        gap = target_ytd - ytd
        if gap > float(cfg["pace_gap"]):
            exposure *= 1 + min(float(cfg["pace_max_add"]), gap * float(cfg["pace_slope"]))
        elif ytd > target_ytd + float(cfg["derisk_gap"]):
            exposure *= float(cfg["ahead_derisk_mult"])
    return max(0.0, min(float(cfg["max_gross"]), exposure))


def exposure_for_group(g: dict, cfg: dict, nav: float, year_start_nav: float, risk_off_override: bool | None = None) -> float:
    if "breadth" in g and np.isfinite(g["breadth"]).any():
        breadth = float(np.nanmean(g["breadth"]))
    else:
        breadth = float(np.nanmean(g["trend_template"] == 1))
    risk_off = risk_off_override
    if risk_off is None:
        risk_off = is_risk_off_values(
            float(g["vni_close"][0]),
            float(g["vni_sma40"][0]),
            float(g["vni_ret13"][0]) if np.isfinite(g["vni_ret13"][0]) else 0.0,
            cfg,
        )
    exposure = float(cfg["riskoff_exposure"] if risk_off else cfg["base_exposure"])
    if breadth < float(cfg["breadth_floor"]):
        exposure *= float(cfg["weak_breadth_mult"])
    if bool(cfg.get("breadth_risk_enabled", False)) and breadth < float(cfg.get("breadth_risk_threshold", 0.35)):
        exposure = min(exposure, float(cfg.get("breadth_risk_exposure", exposure)))
    vni_ret26 = float(g["vni_ret26"][0]) if "vni_ret26" in g and np.isfinite(g["vni_ret26"][0]) else 0.0
    if bool(cfg.get("vni26_risk_enabled", False)) and vni_ret26 < float(cfg.get("vni26_risk_threshold", -0.05)):
        exposure = min(exposure, float(cfg.get("vni26_risk_exposure", exposure)))
    breadth_slope = float(g.get("_breadth_slope_4w", 0.0))
    if bool(cfg.get("breadth_slope_risk_enabled", False)) and breadth_slope < float(cfg.get("breadth_slope_risk_threshold", -0.10)):
        exposure *= float(cfg.get("breadth_slope_risk_mult", 0.50))
    if bool(cfg.get("annual_pacing")):
        week = max(1, int(pd.Timestamp(g["date"]).isocalendar().week))
        target_ytd = (1 + float(cfg["annual_target"])) ** (week / 52) - 1
        ytd = nav / max(year_start_nav, 1e-9) - 1
        gap = target_ytd - ytd
        if gap > float(cfg["pace_gap"]):
            exposure *= 1 + min(float(cfg["pace_max_add"]), gap * float(cfg["pace_slope"]))
        elif ytd > target_ytd + float(cfg["derisk_gap"]):
            exposure *= float(cfg["ahead_derisk_mult"])
    return max(0.0, min(float(cfg["max_gross"]), exposure))


def effective_max_holdings(cfg: dict, breadth: float) -> int:
    base = int(cfg["max_holdings"])
    if not bool(cfg.get("max_holdings_breadth_scale", False)):
        return base
    low_cut = float(cfg.get("breadth_scale_low", 0.20))
    high_cut = float(cfg.get("breadth_scale_high", 0.30))
    if breadth <= low_cut:
        low_target = int(cfg.get("max_holdings_low_breadth", 2))
        if bool(cfg.get("allow_low_breadth_expand", False)):
            return max(1, low_target)
        return max(1, min(base, low_target))
    if breadth >= high_cut:
        return max(1, int(cfg.get("max_holdings_high_breadth", max(base, 4))))
    return base


def effective_max_weight(cfg: dict, breadth: float) -> float:
    base = float(cfg["max_weight"])
    if not bool(cfg.get("max_holdings_breadth_scale", False)):
        return base
    high_cut = float(cfg.get("breadth_scale_high", 0.30))
    if breadth >= high_cut:
        return min(base, float(cfg.get("max_weight_high_breadth", base)))
    return base


def apply_rebalance_band(exec_w: dict[str, float], prev_w: dict[str, float], band: float) -> dict[str, float]:
    if band <= 0 or not exec_w:
        return exec_w
    adjusted = dict(exec_w)
    for symbol in (set(prev_w) - {"__INDEX__"}) | set(exec_w):
        prev = float(prev_w.get(symbol, 0.0))
        target = float(exec_w.get(symbol, 0.0))
        if abs(target - prev) <= band:
            if prev > 1e-7:
                adjusted[symbol] = prev
            else:
                adjusted.pop(symbol, None)
    return {s: w for s, w in adjusted.items() if w > 1e-7}


def normalize_arrays(
    symbols: np.ndarray,
    scores: np.ndarray,
    avg_liq: np.ndarray,
    cfg: dict,
    exposure: float,
    low_liq_flags: np.ndarray | None = None,
    max_weight_override: float | None = None,
) -> dict[str, float]:
    if len(symbols) == 0 or exposure <= 0:
        return {}
    raw = np.clip(scores.astype(float), 0, None) ** float(cfg["score_power"])
    if not np.isfinite(raw).all() or raw.sum() <= 0:
        raw = np.ones(len(symbols), dtype=float)
    raw = raw / raw.sum()
    caps = np.minimum(
        float(cfg["max_weight"] if max_weight_override is None else max_weight_override),
        np.maximum(0.0, avg_liq.astype(float) * 1e9 * float(cfg["liq_participation"]) / float(cfg["nav"])),
    )
    rank_caps = cfg.get("rank_weight_caps")
    if isinstance(rank_caps, str):
        rank_caps = [float(v) for v in rank_caps.split(",") if str(v).strip()]
    elif rank_caps is not None:
        rank_caps = [float(v) for v in rank_caps]
    if rank_caps:
        rank_cap_arr = np.array([rank_caps[min(i, len(rank_caps) - 1)] for i in range(len(symbols))], dtype=float)
        caps = np.minimum(caps, rank_cap_arr)
    if low_liq_flags is not None and bool(cfg.get("lower_liq_momentum", False)):
        caps = np.where(
            low_liq_flags.astype(bool),
            np.minimum(caps, float(cfg.get("low_liq_max_weight", 0.10))),
            caps,
        )
    weights = np.minimum(raw * exposure, caps)
    for _ in range(5):
        unused = exposure - float(weights.sum())
        if unused <= 1e-8:
            break
        room = caps - weights
        open_mask = room > 1e-8
        if not open_mask.any():
            break
        add = unused / int(open_mask.sum())
        weights[open_mask] += np.minimum(add, room[open_mask])
    return {str(s): float(w) for s, w in zip(symbols, weights) if w > 1e-7}


def run_policy(data: pd.DataFrame | dict, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cfg = {**cfg, "nav": float(cfg.get("nav", 1_000_000_000))}
    prepared = prepare_matrix(data) if isinstance(data, pd.DataFrame) else data
    groups = prepared["groups"]
    vni_next_ret = prepared["vni_next_ret"]
    nav = float(cfg["nav"])
    prev_w: dict[str, float] = {}
    rows = []
    holdings_rows = []
    year_start_nav = nav
    current_year = None
    weekly_cash = (1 + float(cfg.get("cash_yield", 0.0))) ** (1 / 52) - 1
    risk_state = False
    execution_lag_weeks = int(cfg.get("execution_lag_weeks", 0) or 0)
    pending_w: dict[str, float] = {}
    pending_index_w = 0.0
    weak_industry_streak: dict[str, int] = {}
    prev_signal_exposure = 0.0
    reentry_partial_remaining = 0
    breadth_history: list[float] = []
    bull_age_streak = 0

    for g in groups:
        d = g["date"]
        if current_year != d.year:
            current_year = d.year
            year_start_nav = nav
        if "breadth" in g and np.isfinite(g["breadth"]).any():
            breadth_now = float(np.nanmean(g["breadth"]))
        else:
            breadth_now = float(np.nanmean(g["trend_template"] == 1))
        breadth_slope_4w = breadth_now - float(breadth_history[-4]) if len(breadth_history) >= 4 else 0.0
        g["_breadth_slope_4w"] = breadth_slope_4w
        breadth_history.append(breadth_now)
        industry_scores_all = np.nan_to_num(g["industry_score"], nan=-999.0)
        industry_median = float(np.nanmedian(industry_scores_all)) if len(industry_scores_all) else -999.0
        for symbol in [s for s in prev_w if s != "__INDEX__"]:
            idxs = np.flatnonzero(g["symbol"] == symbol)
            if len(idxs) == 0:
                weak_industry_streak[symbol] = weak_industry_streak.get(symbol, 0) + 1
                continue
            score_value = float(industry_scores_all[int(idxs[0])])
            weak_industry_streak[symbol] = weak_industry_streak.get(symbol, 0) + 1 if score_value < industry_median else 0
        rsi = g["rsi14"]
        vni_ret4_now = float(g["vni_ret4"][0]) if np.isfinite(g["vni_ret4"][0]) else 0.0
        vni_ret8_now = float(g["vni_ret8"][0]) if "vni_ret8" in g and np.isfinite(g["vni_ret8"][0]) else 0.0
        vni_ret13_now = float(g["vni_ret13"][0]) if np.isfinite(g["vni_ret13"][0]) else 0.0
        vni_ret26_now = float(g["vni_ret26"][0]) if np.isfinite(g["vni_ret26"][0]) else 0.0
        vni_ret52_now = float(g["vni_ret52"][0]) if "vni_ret52" in g and np.isfinite(g["vni_ret52"][0]) else 0.0
        vni_vol13_now = float(g["vni_vol13"][0]) if "vni_vol13" in g and np.isfinite(g["vni_vol13"][0]) else 0.0
        vni_dispersion_4w_now = float(g["vni_dispersion_4w"][0]) if "vni_dispersion_4w" in g and np.isfinite(g["vni_dispersion_4w"][0]) else 0.0
        vni_ath_proximity_now = float(g["vni_ath_proximity"][0]) if "vni_ath_proximity" in g and np.isfinite(g["vni_ath_proximity"][0]) else 0.0
        vni_distance_52w_high_now = float(g["vni_distance_52w_high"][0]) if "vni_distance_52w_high" in g and np.isfinite(g["vni_distance_52w_high"][0]) else 1.0
        vni_ma200_slope_4w_now = float(g["vni_ma200_slope_4w"][0]) if "vni_ma200_slope_4w" in g and np.isfinite(g["vni_ma200_slope_4w"][0]) else 0.0
        breadth_top200_now = float(g["breadth_top200"][0]) if "breadth_top200" in g and np.isfinite(g["breadth_top200"][0]) else breadth_now
        breadth_recovery_2w_now = float(g["breadth_recovery_2w"][0]) if "breadth_recovery_2w" in g and np.isfinite(g["breadth_recovery_2w"][0]) else 0.0
        smallcap_rs13_now = float(g["smallcap_rs13"][0]) if "smallcap_rs13" in g and np.isfinite(g["smallcap_rs13"][0]) else 0.0
        smallcap_vs_hose13_now = float(g["smallcap_vs_hose13"][0]) if "smallcap_vs_hose13" in g and np.isfinite(g["smallcap_vs_hose13"][0]) else 0.0
        vn30_rs26_now = float(g["vn30_rs26"][0]) if "vn30_rs26" in g and np.isfinite(g["vn30_rs26"][0]) else 0.0
        vn30_breadth_now = float(g["vn30_breadth"][0]) if "vn30_breadth" in g and np.isfinite(g["vn30_breadth"][0]) else 0.0
        mega_cap_leadership_now = float(g["mega_cap_leadership"][0]) if "mega_cap_leadership" in g and np.isfinite(g["mega_cap_leadership"][0]) else 0.0
        mega_cap_leadership_pit_now = float(g["mega_cap_leadership_pit"][0]) if "mega_cap_leadership_pit" in g and np.isfinite(g["mega_cap_leadership_pit"][0]) else mega_cap_leadership_now
        mega_cap_breadth_now = float(g["mega_cap_breadth"][0]) if "mega_cap_breadth" in g and np.isfinite(g["mega_cap_breadth"][0]) else vn30_breadth_now
        largecap_leadership_signal = mega_cap_leadership_pit_now if bool(cfg.get("largecap_use_exact_pit", False)) else mega_cap_leadership_now
        largecap_breadth_signal = mega_cap_breadth_now if bool(cfg.get("largecap_use_exact_pit", False)) else vn30_breadth_now
        bull_age_condition = (
            vni_ret52_now > float(cfg.get("bull_age_vni_ret52", 0.30))
            and breadth_now > float(cfg.get("bull_age_breadth", 0.65))
        )
        bull_age_streak = bull_age_streak + 1 if bull_age_condition else 0
        bull_age_active = bool(cfg.get("bull_age_brake", False)) and bull_age_streak >= int(cfg.get("bull_age_weeks", 12) or 12)
        high_vol_active = bool(cfg.get("vol_weight_cap_enabled", False)) and vni_vol13_now > float(cfg.get("vol_weight_cap_threshold", 0.28))
        financial_rotation_active = (
            bool(cfg.get("financial_rotation_boost", False))
            and vni_ret13_now > float(cfg.get("financial_rotation_min_vni_ret13", 0.0))
            and vni_ret26_now > float(cfg.get("financial_rotation_min_vni_ret26", -0.05))
            and vni_ret26_now < float(cfg.get("financial_rotation_max_vni_ret26", 0.25))
            and breadth_now > float(cfg.get("financial_rotation_min_breadth", 0.45))
        )
        trend_on_active = (
            bool(cfg.get("trend_on_enabled", False))
            and vni_ret13_now > float(cfg.get("trend_on_vni_ret13", 0.12))
            and breadth_now > float(cfg.get("trend_on_breadth", 0.55))
        )
        base_risk_off = is_risk_off_values(
            float(g["vni_close"][0]),
            float(g["vni_sma40"][0]),
            vni_ret13_now,
            cfg,
        )
        if bool(cfg.get("internal_asym8_overlay", False)):
            vni_close = float(g["vni_close"][0])
            vni_sma40 = float(g["vni_sma40"][0])
            if risk_state:
                if vni_ret4_now > float(cfg.get("internal_asym8_bull_ret4", 0.02)) or vni_ret8_now > float(cfg.get("internal_asym8_bull_ret8", -0.03)):
                    risk_state = False
            else:
                below_sma = bool(np.isfinite(vni_sma40) and vni_close < vni_sma40)
                if (
                    vni_ret8_now < float(cfg.get("internal_asym8_bear_ret8", -0.07))
                    and vni_ret4_now < float(cfg.get("internal_asym8_bear_ret4", -0.03))
                    and (below_sma or not bool(cfg.get("internal_asym8_require_below_sma40", False)))
                ):
                    risk_state = True
            risk_off_now = risk_state
        elif bool(cfg.get("use_external_h11", False)) and "external_h11_bear" in g and np.isfinite(g["external_h11_bear"][0]):
            risk_off_now = bool(g["external_h11_bear"][0] > 0)
        elif bool(cfg.get("asymmetric_overlay", False)):
            vni_close = float(g["vni_close"][0])
            vni_sma40 = float(g["vni_sma40"][0])
            if risk_state:
                if vni_ret4_now > float(cfg.get("asym_bull_ret4", 0.02)) or vni_ret13_now > float(cfg.get("asym_bull_ret13", -0.03)):
                    risk_state = False
            else:
                below_sma = bool(np.isfinite(vni_sma40) and vni_close < vni_sma40)
                if (
                    vni_ret13_now < float(cfg.get("asym_bear_ret13", -0.07))
                    and vni_ret4_now < float(cfg.get("asym_bear_ret4", -0.03))
                    and (below_sma or not bool(cfg.get("asym_require_below_sma40", True)))
                ):
                    risk_state = True
            risk_off_now = risk_state
        else:
            risk_off_now = base_risk_off
        late_cycle_active = (
            bool(cfg.get("late_cycle_gate_enabled", False))
            and vni_ath_proximity_now >= float(cfg.get("late_cycle_ath_proximity", 0.97))
            and breadth_top200_now < float(cfg.get("late_cycle_max_breadth_top200", 0.60))
        )
        dispersion_gate_active = (
            bool(cfg.get("dispersion_gate_enabled", False))
            and vni_dispersion_4w_now > float(cfg.get("dispersion_gate_threshold", 0.04))
        )
        sideways_recovery_active = (
            bool(cfg.get("sideways_recovery_enabled", False))
            and not risk_off_now
            and vni_ma200_slope_4w_now > float(cfg.get("sideways_min_ma200_slope_4w", 0.0))
            and vni_distance_52w_high_now < float(cfg.get("sideways_max_distance_52w_high", 0.15))
            and breadth_recovery_2w_now > 0.5
        )
        smallcap_recovery_active = (
            bool(cfg.get("smallcap_rs_gate_enabled", False))
            and smallcap_rs13_now > float(cfg.get("smallcap_rs_threshold", 0.03))
            and smallcap_vs_hose13_now > float(cfg.get("smallcap_vs_hose_threshold", -0.02))
            and vni_ret26_now > float(cfg.get("smallcap_vni26_floor", -0.05))
            and vni_ret13_now > float(cfg.get("smallcap_vni13_floor", -0.08))
        )
        largecap_momentum_active = (
            bool(cfg.get("largecap_momentum_gate_enabled", False))
            and largecap_leadership_signal > float(cfg.get("largecap_min_leadership", 0.05))
            and largecap_breadth_signal > float(cfg.get("largecap_min_breadth", 0.55))
            and vn30_rs26_now > float(cfg.get("largecap_min_rs26", -0.02))
            and vni_ret13_now > float(cfg.get("largecap_min_vni_ret13", -0.05))
        )
        selector_floor_now = bool(
            cfg.get("use_selector_labels", False)
            and "selector_risk_floor_required" in g
            and np.isfinite(g["selector_risk_floor_required"][0])
            and g["selector_risk_floor_required"][0] > 0
        )
        selector_overheat_now = bool(
            cfg.get("use_selector_labels", False)
            and "selector_cluster_overheat" in g
            and np.isfinite(g["selector_cluster_overheat"][0])
            and g["selector_cluster_overheat"][0] > 0
        )
        selector_floor_override = float(cfg.get("selector_floor_override_vni_ret4", -999.0))
        selector_floor_allowed = not (selector_floor_override > -998.0 and vni_ret4_now > selector_floor_override)
        if selector_floor_now and selector_floor_allowed and bool(cfg.get("selector_floor_sets_riskoff", True)):
            risk_off_now = True
        cluster_overheat_internal = (
            bool(cfg.get("cluster_overheat_internal", False))
            and np.nanmax(g["cluster_breakout_count"]) >= float(cfg.get("cluster_overheat_count", 5.0))
            and vni_ret4_now < float(cfg.get("cluster_overheat_max_vni_ret4", 0.0))
        )
        breakout_exception = (
            bool(cfg.get("relax_rsi_breakout", False))
            & (rsi >= float(cfg["rsi_min"]))
            & (rsi <= float(cfg.get("breakout_rsi_max", 95.0)))
            & ((g["at_52w_high_flag"] > 0) | (g["cluster_breakout_flag"] > 0))
        )
        rsi_ok = np.isnan(rsi) | ((rsi >= float(cfg["rsi_min"])) & (rsi <= float(cfg["rsi_max"]))) | breakout_exception
        low_liq_allowed = (
            bool(cfg.get("lower_liq_momentum", False))
            & (g["avg_value_20d_bil"] >= float(cfg.get("low_liq_min_liq", 3.0)))
            & (g["avg_value_20d_bil"] < float(cfg["min_liq"]))
            & (g["composite_score"] >= float(cfg.get("low_liq_min_comp", 70.0)))
            & (
                (np.nan_to_num(g["ret13"], nan=-999.0) >= float(cfg.get("low_liq_min_ret13", 0.20)))
                | (np.nan_to_num(g["ret26"], nan=-999.0) >= float(cfg.get("low_liq_min_ret26", 0.30)))
                | (g["cluster_breakout_flag"] > 0)
            )
        )
        liq_ok = (g["avg_value_20d_bil"] >= float(cfg["min_liq"])) | low_liq_allowed
        if smallcap_recovery_active and bool(cfg.get("smallcap_relax_liq", False)):
            smallcap_liq_ok = (
                (g["hnx_flag"] > 0)
                & (g["avg_value_20d_bil"] >= float(cfg.get("smallcap_min_liq", 2.0)))
                & (
                    (np.nan_to_num(g["ret13"], nan=-999.0) >= float(cfg.get("smallcap_min_ret13", 0.0)))
                    | (np.nan_to_num(g["near_high52"], nan=0.0) >= float(cfg.get("smallcap_min_near_high52", 0.80)))
                    | (g["cluster_breakout_flag"] > 0)
                )
            )
            liq_ok = liq_ok | smallcap_liq_ok
        mask = (
            liq_ok
            & (g["composite_score"] >= float(cfg["min_comp"]))
            & (g["industry_score"] >= float(cfg["min_industry_score"]))
            & (g["industry_rank"] <= int(cfg["industry_top_n"]))
            & rsi_ok
            & (np.nan_to_num(g["rs13"], nan=-999.0) >= float(cfg.get("min_rs13", -999.0)))
            & (np.nan_to_num(g["ret13"], nan=-999.0) >= float(cfg.get("min_ret13", -999.0)))
            & (np.nan_to_num(g["ret26"], nan=-999.0) >= float(cfg.get("min_ret26", -999.0)))
            & (np.nan_to_num(g["near_high52"], nan=0.0) >= float(cfg.get("min_near_high52", 0.0)))
            & (np.nan_to_num(g["moneyflow_score"], nan=-999.0) >= float(cfg.get("min_moneyflow_score", -999.0)))
        )
        if bool(cfg.get("rank_velocity_filter_enabled", False)):
            mask &= np.nan_to_num(g["rank_velocity_4w"], nan=-999.0) >= float(cfg.get("rank_velocity_min", 0.0))
        if bool(cfg.get("money_flow_5d_filter_enabled", False)):
            mask &= np.nan_to_num(g["money_flow_5d"], nan=-999.0) >= float(cfg.get("money_flow_5d_min", 0.08))
        if bull_age_active:
            mask &= np.nan_to_num(g["ret13"], nan=999.0) <= float(cfg.get("bull_age_max_ret13", 0.20))
        if dispersion_gate_active and bool(cfg.get("dispersion_requires_near_high", False)):
            mask &= np.nan_to_num(g["near_high52"], nan=0.0) >= float(cfg.get("dispersion_min_near_high52", 0.85))
        if late_cycle_active and bool(cfg.get("late_cycle_requires_near_high", False)):
            mask &= np.nan_to_num(g["near_high52"], nan=0.0) >= float(cfg.get("late_cycle_min_near_high52", 0.85))
        if risk_off_now and bool(cfg.get("riskoff_require_resilience", False)):
            mask &= (
                (np.nan_to_num(g["trend_template"], nan=0.0) >= float(cfg.get("riskoff_min_trend", 1.0)))
                & (np.nan_to_num(g["rs13"], nan=-999.0) >= float(cfg.get("riskoff_min_rs13", 0.0)))
                & (np.nan_to_num(g["ret13"], nan=-999.0) >= float(cfg.get("riskoff_min_ret13", -0.05)))
                & (np.nan_to_num(g["near_high52"], nan=0.0) >= float(cfg.get("riskoff_min_near_high52", 0.0)))
            )
        if risk_off_now and bool(cfg.get("bear_defensive_mode", False)):
            mask &= (
                (g["defensive_flag"] > 0)
                & (np.nan_to_num(g["rs13"], nan=-999.0) >= float(cfg.get("defensive_min_rs13", -0.05)))
                & (np.nan_to_num(g["trend_template"], nan=0.0) >= float(cfg.get("defensive_min_trend", 0.0)))
            )
        if largecap_momentum_active and bool(cfg.get("largecap_require_flag", False)):
            mask &= np.nan_to_num(g["largecap_flag"], nan=0.0) > 0
        if bool(cfg["require_hard_gate"]):
            mask &= g["hard_gate"] == "PASS"
        status_mode = cfg.get("status_mode", "any")
        if status_mode == "buy_acc":
            mask &= np.isin(g["status"], ["BUY", "ACCUMULATE"])
        elif status_mode == "not_avoid":
            mask &= g["status"] != "AVOID"
        if bool(cfg["require_trend"]):
            mask &= np.nan_to_num(g["trend_template"], nan=0.0) >= 1
        if bool(cfg["require_rs"]):
            mask &= np.nan_to_num(g["rs13"], nan=-999.0) > 0

        cand_idx = np.flatnonzero(mask)
        if bool(cfg["one_per_industry"]) and len(cand_idx) > 0:
            by_tech = cand_idx[np.argsort(-np.nan_to_num(g["tech_score_base"][cand_idx], nan=-999.0))]
            seen_industries = set()
            kept = []
            for idx in by_tech:
                industry = str(g["industry_name"][idx])
                if industry in seen_industries:
                    continue
                seen_industries.add(industry)
                kept.append(idx)
            cand_idx = np.array(kept, dtype=int)

        if len(cand_idx) == 0:
            target_w = {}
            exposure = 0.0
            candidate_count = 0
        else:
            family = str(cfg.get("family", "rank_mix"))
            score = np.zeros(len(cand_idx), dtype=float)
            regime_bonus_mult = 1.0
            if late_cycle_active:
                regime_bonus_mult = min(regime_bonus_mult, float(cfg.get("late_cycle_bonus_mult", 0.50)))
            if dispersion_gate_active:
                regime_bonus_mult = min(regime_bonus_mult, float(cfg.get("dispersion_bonus_mult", 0.50)))
            cluster_bonus_runtime = float(cfg.get("cluster_bonus", 0.0)) * regime_bonus_mult
            hold_bonus_runtime = float(cfg.get("hold_bonus", 0.0)) * regime_bonus_mult
            for feat in FEATURES:
                score += float(cfg[f"w_{feat}"]) * np.nan_to_num(g[feat][cand_idx], nan=50.0)
            score += float(cfg.get("moneyflow_score_w", 0.0)) * np.nan_to_num(g["moneyflow_score"][cand_idx], nan=50.0)
            score += float(cfg.get("rs13_w", 0.0)) * np.clip(np.nan_to_num(g["rs13"][cand_idx], nan=0.0), -0.50, 1.00) * 100
            score += float(cfg.get("ret8_w", 0.0)) * np.clip(np.nan_to_num(g["ret8"][cand_idx], nan=0.0), -0.30, 0.60) * 100
            score += float(cfg.get("ret26_w", 0.0)) * np.clip(np.nan_to_num(g["ret26"][cand_idx], nan=0.0), -0.60, 1.50) * 100
            score += float(cfg.get("ret52_w", 0.0)) * np.clip(np.nan_to_num(g["ret52"][cand_idx], nan=0.0), -0.80, 2.00) * 100
            score += float(cfg.get("near_high52_w", 0.0)) * np.nan_to_num(g["near_high52"][cand_idx], nan=0.0) * 100
            score += float(cfg.get("trend_w", 0.0)) * np.nan_to_num(g["trend_template"][cand_idx], nan=0.0) * 25
            if bool(cfg.get("rank_velocity_gate_enabled", False)):
                score += float(cfg.get("rank_velocity_w", 0.0)) * np.clip(
                    np.nan_to_num(g["rank_velocity_4w"][cand_idx], nan=0.0),
                    -100.0,
                    100.0,
                )
            if bool(cfg.get("money_flow_5d_gate_enabled", False)):
                score += float(cfg.get("money_flow_5d_w", 0.0)) * np.clip(
                    np.nan_to_num(g["money_flow_5d"][cand_idx], nan=0.0),
                    0.0,
                    0.30,
                ) * 100.0
            score += cluster_bonus_runtime * g["cluster_breakout_flag"][cand_idx]
            score += float(cfg.get("cluster_count_w", 0.0)) * np.clip(g["cluster_breakout_count"][cand_idx], 0, 5)
            score += float(cfg.get("cluster_strength_w", 0.0)) * np.clip(g["cluster_strength_4w"][cand_idx], 0, 8)
            if family == "breakout":
                score += 0.35 * np.nan_to_num(g["high_rank_all"][cand_idx], nan=50.0)
                score += 25 * np.nan_to_num(g["near_high52"][cand_idx], nan=0.0)
                score += 18 * np.clip(np.nan_to_num(g["rs13"][cand_idx], nan=0.0), -0.5, 1.0)
            elif family == "sector_cluster":
                score += 0.45 * np.nan_to_num(g["high_rank_all"][cand_idx], nan=50.0)
                score += (35 * regime_bonus_mult) * g["cluster_breakout_flag"][cand_idx]
                score += (6 * regime_bonus_mult) * np.clip(g["cluster_strength_4w"][cand_idx], 0, 8)
                score += 14 * np.clip(np.nan_to_num(g["ret4"][cand_idx], nan=0.0), -0.20, 0.60) * 100
            elif family == "crash_resilience":
                score += 0.30 * np.nan_to_num(g["rs_rank_all"][cand_idx], nan=50.0)
                score += 0.20 * np.nan_to_num(g["flow_rank_all"][cand_idx], nan=50.0)
                if risk_off_now:
                    score += 40 * np.nan_to_num(g["near_high52"][cand_idx], nan=0.0)
                    score += 30 * np.nan_to_num(g["trend_template"][cand_idx], nan=0.0)
            elif family == "moneyflow":
                score += 0.45 * np.nan_to_num(g["flow_rank_all"][cand_idx], nan=50.0)
                score += 0.30 * np.nan_to_num(g["moneyflow_score"][cand_idx], nan=50.0)
            if financial_rotation_active:
                financial = np.isin(g["sector_group"][cand_idx], ["bank", "securities"])
                score += financial.astype(float) * float(cfg.get("financial_rotation_bonus", 0.0))
            if largecap_momentum_active:
                score += (g["largecap_flag"][cand_idx] > 0).astype(float) * float(cfg.get("largecap_bonus", 0.0))
                score += (g["mega_cap_flag"][cand_idx] > 0).astype(float) * float(cfg.get("mega_cap_bonus", 0.0))
            if smallcap_recovery_active:
                score += (g["hnx_flag"][cand_idx] > 0).astype(float) * float(cfg.get("smallcap_hnx_bonus", 0.0))
                if bool(cfg.get("smallcap_cluster_bonus_only", False)):
                    score += (
                        (g["hnx_flag"][cand_idx] > 0)
                        & (g["cluster_breakout_flag"][cand_idx] > 0)
                    ).astype(float) * float(cfg.get("smallcap_cluster_extra_bonus", 0.0))
            if hold_bonus_runtime > 0:
                held_symbols = [s for s in prev_w if s != "__INDEX__"]
                if held_symbols:
                    held = np.isin(g["symbol"][cand_idx], held_symbols)
                    score += held.astype(float) * hold_bonus_runtime
            if float(cfg.get("hold_bonus_top1", 0.0)) > 0:
                held_symbols = [s for s in prev_w if s != "__INDEX__"]
                if held_symbols:
                    top_symbol = max(held_symbols, key=lambda symbol: float(prev_w.get(symbol, 0.0)))
                    score += (g["symbol"][cand_idx] == top_symbol).astype(float) * float(cfg.get("hold_bonus_top1", 0.0))
            if float(cfg.get("rotation_industry_rs_threshold", 0.0)) > 0:
                strong_cut = float(np.nanpercentile(industry_scores_all, float(cfg.get("rotation_strong_industry_pct", 60.0))))
                weak_symbols = {s for s, streak in weak_industry_streak.items() if streak >= int(cfg.get("rotation_weak_streak_weeks", 2))}
                if weak_symbols and np.nanmax(industry_scores_all) >= strong_cut:
                    held_weak = np.isin(g["symbol"][cand_idx], list(weak_symbols))
                    strong_industry = g["industry_score"][cand_idx] >= strong_cut
                    score -= held_weak.astype(float) * float(cfg.get("rotation_industry_rs_threshold", 0.0))
                    score += (~held_weak & strong_industry).astype(float) * float(cfg.get("rotation_strong_bonus", 0.0))
            if bool(cfg["ret_boost"]):
                score += float(cfg["ret4_w"]) * np.clip(np.nan_to_num(g["ret4"][cand_idx], nan=0.0), -0.30, 0.30) * 100
                score += float(cfg["ret13_w"]) * np.clip(np.nan_to_num(g["ret13"][cand_idx], nan=0.0), -0.50, 0.80) * 100
            effective_min_score = float(cfg["min_score"])
            if "min_score_low_breadth" in cfg or "min_score_high_breadth" in cfg:
                breadth_cut = float(cfg.get("min_score_breadth_threshold", 0.20))
                if breadth_now < breadth_cut:
                    effective_min_score = float(cfg.get("min_score_low_breadth", effective_min_score))
                else:
                    effective_min_score = float(cfg.get("min_score_high_breadth", effective_min_score))
            if sideways_recovery_active:
                effective_min_score = max(0.0, effective_min_score - float(cfg.get("sideways_min_score_delta", 5.0)))
            if smallcap_recovery_active:
                effective_min_score = max(0.0, effective_min_score - float(cfg.get("smallcap_min_score_delta", 0.0)))
            if largecap_momentum_active and "largecap_min_score" in cfg:
                effective_min_score = max(effective_min_score, float(cfg.get("largecap_min_score", effective_min_score)))
            keep = score >= effective_min_score
            cand_idx = cand_idx[keep]
            score = score[keep]
            if len(cand_idx) == 0:
                target_w = {}
                exposure = 0.0
                candidate_count = 0
            else:
                order = np.lexsort((-np.nan_to_num(g["industry_score"][cand_idx], nan=-999.0), -score))
                cand_idx = cand_idx[order]
                score = score[order]
                max_per_sector = int(cfg.get("max_per_sector", 0) or 0)
                if max_per_sector > 0:
                    sector_counts: dict[str, int] = {}
                    kept_positions = []
                    for pos, idx in enumerate(cand_idx):
                        sector = str(g["sector_group"][idx] or "")
                        used = sector_counts.get(sector, 0)
                        if used >= max_per_sector:
                            continue
                        sector_counts[sector] = used + 1
                        kept_positions.append(pos)
                    cand_idx = cand_idx[kept_positions]
                    score = score[kept_positions]
                candidate_count = len(cand_idx)
                exposure = exposure_for_group(g, cfg, nav, year_start_nav, risk_off_override=risk_off_now)
                if bull_age_active:
                    exposure *= float(cfg.get("bull_age_exposure_mult", 1.0))
                if trend_on_active:
                    exposure = max(exposure, float(cfg.get("trend_on_min_exposure", 0.85)))
                if sideways_recovery_active:
                    exposure = max(exposure, float(cfg.get("sideways_min_exposure", 0.85)))
                if smallcap_recovery_active:
                    exposure = max(exposure, float(cfg.get("smallcap_min_exposure", exposure)))
                if largecap_momentum_active:
                    exposure = max(exposure, float(cfg.get("largecap_min_exposure", exposure)))
                if late_cycle_active:
                    exposure = min(exposure, max(0.0, 1.0 - float(cfg.get("late_cycle_cash_floor", 0.25))))
                if dispersion_gate_active:
                    exposure *= float(cfg.get("dispersion_exposure_mult", 1.0))
                if cluster_overheat_internal:
                    exposure *= float(cfg.get("cluster_overheat_exposure_mult", 0.50))
                if selector_floor_now and selector_floor_allowed:
                    exposure = min(exposure, float(cfg.get("selector_floor_exposure", 0.0)))
                elif selector_overheat_now:
                    exposure *= float(cfg.get("selector_overheat_mult", 0.50))
                if candidate_count < int(cfg["min_candidates"]):
                    exposure *= float(cfg["thin_mult"])
                top_n = effective_max_holdings(cfg, breadth_now)
                if trend_on_active:
                    top_n += int(cfg.get("trend_on_extra_holdings", 1) or 0)
                if sideways_recovery_active:
                    top_n += int(cfg.get("sideways_extra_holdings", 1) or 0)
                if smallcap_recovery_active:
                    top_n += int(cfg.get("smallcap_extra_holdings", 0) or 0)
                if largecap_momentum_active:
                    top_n = int(cfg.get("largecap_max_holdings", top_n) or top_n)
                if bool(cfg.get("low_breadth_expand_enabled", False)) and breadth_top200_now < float(cfg.get("low_breadth_expand_threshold", 0.35)):
                    top_n = max(top_n, int(cfg.get("low_breadth_expand_holdings", top_n)))
                if late_cycle_active:
                    top_n = min(top_n, int(cfg.get("late_cycle_max_holdings", top_n)))
                top_n = min(top_n, len(cand_idx))
                top_idx = cand_idx[:top_n]
                max_weight_runtime = effective_max_weight(cfg, breadth_now)
                if high_vol_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("vol_weight_cap", max_weight_runtime)))
                if bull_age_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("bull_age_max_weight", max_weight_runtime)))
                if cluster_overheat_internal:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("cluster_overheat_max_weight", max_weight_runtime)))
                if trend_on_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("trend_on_max_weight", max_weight_runtime)))
                if sideways_recovery_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("sideways_max_weight", max_weight_runtime)))
                if smallcap_recovery_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("smallcap_max_weight", max_weight_runtime)))
                if largecap_momentum_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("largecap_max_weight", max_weight_runtime)))
                if bool(cfg.get("low_breadth_expand_enabled", False)) and breadth_top200_now < float(cfg.get("low_breadth_expand_threshold", 0.35)):
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("low_breadth_expand_max_weight", max_weight_runtime)))
                if late_cycle_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("late_cycle_max_weight", max_weight_runtime)))
                if dispersion_gate_active:
                    max_weight_runtime = min(max_weight_runtime, float(cfg.get("dispersion_max_weight", max_weight_runtime)))
                target_w = normalize_arrays(
                    g["symbol"][top_idx],
                    score[:top_n],
                    g["avg_value_20d_bil"][top_idx],
                    cfg,
                    exposure,
                    low_liq_flags=g["avg_value_20d_bil"][top_idx] < float(cfg["min_liq"]),
                    max_weight_override=max_weight_runtime,
                )
        if len(cand_idx) == 0:
            stock_ret_lookup = {}
        else:
            stock_ret_lookup = {str(s): float(r) for s, r in zip(g["symbol"][cand_idx], g["next_ret"][cand_idx])}

        target_exposure = float(sum(target_w.values()))
        reentry_scale = 1.0
        reentry_min_breadth = float(cfg.get("reentry_after_cash_min_breadth", -1.0))
        reentry_min_vni_ret4 = float(cfg.get("reentry_after_cash_min_vni_ret4", -999.0))
        reentry_rule_enabled = reentry_min_breadth >= 0.0 or reentry_min_vni_ret4 > -998.0
        if target_exposure > 0 and reentry_rule_enabled:
            weak_reentry = breadth_now < reentry_min_breadth or vni_ret4_now < reentry_min_vni_ret4
            if (prev_signal_exposure <= 0.001 or reentry_partial_remaining > 0) and weak_reentry:
                reentry_scale = min(1.0, max(0.0, float(cfg.get("reentry_partial_weight", 1.0))))
                reentry_partial_remaining = max(
                    reentry_partial_remaining,
                    int(cfg.get("reentry_partial_release_weeks", 1) or 1),
                )
            elif reentry_partial_remaining > 0:
                reentry_partial_remaining -= 1
        if reentry_scale < 0.999 and target_w:
            target_w = {symbol: weight * reentry_scale for symbol, weight in target_w.items()}
            target_exposure = float(sum(target_w.values()))
        prev_signal_exposure = target_exposure

        signal_index_w = index_weight_for_group(g, cfg)
        if signal_index_w < 0 and cfg.get("hedge_mode", "off") != "off":
            target_w = {s: w * float(cfg["equity_scale_when_hedged"]) for s, w in target_w.items()}
        if execution_lag_weeks > 0:
            exec_w = dict(pending_w)
            index_w = float(pending_index_w)
            pending_w = dict(target_w)
            pending_index_w = float(signal_index_w)
        else:
            exec_w = target_w
            index_w = signal_index_w
        exec_w = apply_rebalance_band(exec_w, prev_w, float(cfg.get("rebalance_band", 0.0)))

        symbols = (set(prev_w) - {"__INDEX__"}) | set(exec_w)
        buy_turnover = sum(max(exec_w.get(s, 0.0) - prev_w.get(s, 0.0), 0.0) for s in symbols)
        sell_turnover = sum(max(prev_w.get(s, 0.0) - exec_w.get(s, 0.0), 0.0) for s in symbols)
        prev_index_w = prev_w.get("__INDEX__", 0.0)
        index_turnover = abs(index_w - prev_index_w)
        extra_slippage = float(cfg.get("extra_slippage_per_side", 0.0))
        cost = (
            buy_turnover * (FEE_BUY + extra_slippage)
            + sell_turnover * (FEE_SELL_TAX + extra_slippage)
            + index_turnover * float(cfg["index_cost"])
        )
        all_ret_lookup = {str(s): float(r) for s, r in zip(g["symbol"], g["next_ret"])}
        ret_lookup = all_ret_lookup if execution_lag_weeks > 0 else stock_ret_lookup
        stock_ret = sum(w * float(ret_lookup.get(s, 0.0) or 0.0) for s, w in exec_w.items())
        index_ret = index_w * vni_next_ret[d]
        cash_w = max(0.0, 1.0 - sum(exec_w.values()))
        gross_abs = sum(abs(w) for w in exec_w.values()) + abs(index_w)
        borrow = max(0.0, gross_abs - 1.0) * float(cfg["borrow_weekly"])
        port_ret = stock_ret + index_ret + cash_w * weekly_cash - cost - borrow
        nav *= 1 + port_ret
        rows.append({
            "date": d,
            "nav": nav,
            "ret": port_ret,
            "exposure": sum(exec_w.values()),
            "index_weight": index_w,
            "turnover": buy_turnover + sell_turnover,
            "cost": cost,
            "candidate_count": candidate_count,
            "execution_lag_weeks": execution_lag_weeks,
        })
        for s, w in exec_w.items():
            holdings_rows.append({"date": d, "symbol": s, "weight": w})
        prev_w = {**exec_w, "__INDEX__": index_w}

    eq = pd.DataFrame(rows)
    holdings = pd.DataFrame(holdings_rows)
    met = yearly_metrics(eq)
    return eq, holdings, met


def random_cfg(rng: random.Random, mode: str, family_override: str | None = None) -> dict:
    family = family_override or rng.choice(["rank_mix", "rank_mix", "breakout", "sector_cluster", "crash_resilience", "defensive_bear", "moneyflow"])
    weights = np.array([rng.random() ** 1.8 for _ in FEATURES], dtype=float)
    weights = weights / weights.sum()
    max_gross = 1.0 if mode == "stock_only" else rng.choice([1.15, 1.25, 1.35, 1.50, 1.75, 2.0, 2.5, 3.0])
    base_exposure = rng.choice([0.75, 0.90, 1.0]) if mode == "stock_only" else rng.choice([1.0, 1.15, 1.30, 1.50, 2.0, 2.5])
    hedge_on = mode == "target_forcing" and rng.choice([True, False, True])
    return {
        "nav": 1_000_000_000,
        "family": family,
        "cash_yield": 0.0,
        "borrow_weekly": 0.0 if mode == "stock_only" else rng.choice([0.08, 0.12, 0.14]) / 52,
        "hedge_mode": "crisis_short" if hedge_on else "off",
        "max_short_index": 0.0 if not hedge_on else rng.choice([0.35, 0.50, 0.75, 1.0, 1.25]),
        "hedge_base": rng.choice([0.20, 0.35, 0.50]),
        "hedge_mid": rng.choice([0.50, 0.75, 1.0]),
        "hedge_deep": rng.choice([0.75, 1.0, 1.25]),
        "equity_scale_when_hedged": rng.choice([0.0, 0.25, 0.40, 0.60]),
        "index_cost": 0.0008,
        "min_liq": rng.choice([3.0, 5.0, 7.5, 10.0]) if family in ["sector_cluster", "defensive_bear"] else rng.choice([0.5, 1.0, 2.0, 3.0, 5.0]),
        "min_comp": rng.choice([0, 20, 35, 45, 55, 65]),
        "min_industry_score": rng.choice([0, 20, 25, 30, 35, 50, 65]),
        "industry_top_n": rng.choice([4, 6, 8, 12, 20, 999]),
        "rsi_min": rng.choice([20, 30, 35, 40]),
        "rsi_max": rng.choice([75, 80, 82, 88]) if family != "sector_cluster" else rng.choice([75, 80, 82]),
        "relax_rsi_breakout": rng.choice([True, True, False]) if family in ["sector_cluster", "breakout"] else rng.choice([True, False]),
        "breakout_rsi_max": rng.choice([90.0, 95.0, 98.0]),
        "require_hard_gate": rng.choice([True, True, False]),
        "status_mode": rng.choice(["any", "any", "buy_acc", "not_avoid"]),
        "require_trend": rng.choice([True, True, False]),
        "require_rs": rng.choice([True, False, False]),
        "min_rs13": rng.choice([-999.0, -0.20, -0.10, 0.0, 0.05, 0.10]),
        "min_ret13": rng.choice([-999.0, -0.20, -0.10, -0.05, 0.0, 0.05]),
        "min_ret26": rng.choice([-999.0, -0.30, -0.15, -0.05, 0.0]),
        "min_near_high52": rng.choice([0.0, 0.0, 0.25, 0.50, 0.75]),
        "min_moneyflow_score": rng.choice([-999.0, 35.0, 50.0, 65.0]),
        "lower_liq_momentum": rng.choice([True, False]) if family in ["sector_cluster", "breakout"] else rng.choice([False, False, True]),
        "low_liq_min_liq": rng.choice([2.0, 2.5, 3.0, 4.0]),
        "low_liq_min_comp": rng.choice([65.0, 70.0, 75.0]),
        "low_liq_min_ret13": rng.choice([0.10, 0.20, 0.30]),
        "low_liq_min_ret26": rng.choice([0.20, 0.30, 0.45]),
        "low_liq_max_weight": rng.choice([0.08, 0.10, 0.12]),
        "riskoff_require_resilience": rng.choice([False, True, True]),
        "riskoff_min_trend": rng.choice([0.0, 1.0]),
        "riskoff_min_rs13": rng.choice([-0.10, 0.0, 0.05, 0.10]),
        "riskoff_min_ret13": rng.choice([-0.20, -0.10, -0.05, 0.0]),
        "riskoff_min_near_high52": rng.choice([0.0, 0.25, 0.50, 0.75]),
        "bear_defensive_mode": True if family == "defensive_bear" else rng.choice([False, False, True]),
        "defensive_min_rs13": rng.choice([-0.10, -0.05, 0.0, 0.05]),
        "defensive_min_trend": rng.choice([0.0, 1.0]),
        "one_per_industry": rng.choice([True, False]),
        "ret_boost": rng.choice([True, False]),
        "ret4_w": rng.uniform(0.0, 0.9),
        "ret13_w": rng.uniform(-0.2, 0.7),
        "ret8_w": rng.uniform(-0.10, 0.60) if family in ["breakout", "sector_cluster", "crash_resilience"] else rng.uniform(-0.05, 0.20),
        "ret26_w": rng.uniform(-0.20, 0.60) if family in ["breakout", "sector_cluster", "crash_resilience"] else rng.uniform(-0.10, 0.20),
        "ret52_w": rng.uniform(-0.15, 0.35),
        "rs13_w": rng.uniform(-0.10, 0.60),
        "near_high52_w": rng.uniform(0.0, 0.55),
        "cluster_bonus": rng.choice([0.0, 10.0, 20.0, 35.0, 55.0]) if family in ["sector_cluster", "breakout"] else rng.choice([0.0, 0.0, 10.0]),
        "cluster_count_w": rng.choice([0.0, 4.0, 8.0, 12.0]) if family in ["sector_cluster", "breakout"] else rng.choice([0.0, 0.0, 4.0]),
        "cluster_strength_w": rng.choice([0.0, 3.0, 6.0, 10.0]) if family in ["sector_cluster", "breakout"] else rng.choice([0.0, 0.0, 3.0]),
        "moneyflow_score_w": rng.uniform(0.0, 0.60) if family == "moneyflow" else rng.uniform(0.0, 0.20),
        "trend_w": rng.uniform(0.0, 0.45),
        "hold_bonus": rng.choice([0.0, 0.0, 5.0, 12.0, 25.0, 40.0]),
        "hold_bonus_top1": rng.choice([0.0, 0.0, 15.0, 30.0, 45.0]),
        "rotation_industry_rs_threshold": rng.choice([0.0, 0.0, 15.0, 25.0, 35.0]),
        "rotation_strong_bonus": rng.choice([0.0, 5.0, 10.0, 15.0]),
        "rotation_strong_industry_pct": rng.choice([55.0, 60.0, 65.0]),
        "rotation_weak_streak_weeks": rng.choice([2, 3]),
        "min_score": rng.choice([35, 45, 55, 65, 75]),
        "max_holdings": rng.choice([1, 2, 3, 4, 5, 6, 8]),
        "max_holdings_breadth_scale": rng.choice([False, False, True]),
        "max_holdings_low_breadth": rng.choice([1, 2]),
        "max_holdings_high_breadth": rng.choice([3, 4, 5]),
        "max_weight_high_breadth": rng.choice([0.25, 0.30, 0.33]),
        "breadth_scale_low": rng.choice([0.15, 0.20, 0.25]),
        "breadth_scale_high": rng.choice([0.30, 0.35, 0.40]),
        "max_per_sector": rng.choice([0, 0, 2, 3]),
        "max_weight": rng.choice([0.20, 0.25, 0.33, 0.50, 0.75, 1.0]) if mode != "stock_only" else rng.choice([0.20, 0.25, 0.33, 0.50, 1.0]),
        "score_power": rng.choice([0.7, 1.0, 1.4, 2.0, 3.0]),
        "portfolio_vs_vni_brake_5d": rng.choice([0.0, -0.05, -0.07, -0.10]),
        "portfolio_vs_vni_brake_mult": rng.choice([0.35, 0.50, 0.65]),
        "portfolio_vs_vni_brake_min_exposure": rng.choice([0.75, 0.80, 0.90]),
        "portfolio_vs_vni_brake_cooldown_signals": rng.choice([1, 2, 3]),
        "portfolio_loss_brake_5d": rng.choice([0.0, -0.05, -0.06, -0.07]),
        "liq_participation": rng.choice([0.03, 0.05, 0.10, 0.20, 0.50]),
        "base_exposure": base_exposure,
        "riskoff_exposure": rng.choice([0.35, 0.50, 0.75, 1.0]) if family == "defensive_bear" else rng.choice([0.0, 0.2, 0.35, 0.50, 0.75, 1.0]),
        "riskoff_ret13": rng.choice([-0.02, -0.04, -0.06, -0.08]),
        "asymmetric_overlay": rng.choice([True, True, False]),
        "asym_bear_ret13": rng.choice([-0.05, -0.07, -0.09]),
        "asym_bear_ret4": rng.choice([-0.02, -0.03, -0.05]),
        "asym_bull_ret4": rng.choice([0.01, 0.02, 0.04]),
        "asym_bull_ret13": rng.choice([-0.04, -0.03, -0.01, 0.0]),
        "asym_require_below_sma40": rng.choice([True, True, False]),
        "use_external_h11": False,
        "use_selector_labels": False,
        "selector_floor_sets_riskoff": True,
        "selector_floor_exposure": 0.0,
        "selector_overheat_mult": 0.50,
        "extra_slippage_per_side": 0.0,
        "breadth_floor": rng.choice([0.05, 0.10, 0.15, 0.25]),
        "weak_breadth_mult": rng.choice([0.0, 0.35, 0.65, 0.85, 1.0]),
        "max_gross": max_gross if mode == "stock_only" else rng.choice([max_gross, 2.25, 2.50, 3.0]),
        "min_candidates": rng.choice([1, 2, 3, 5, 8]),
        "thin_mult": rng.choice([0.0, 0.35, 0.65, 1.0]),
        "annual_pacing": rng.choice([False, False, True]) if mode == "stock_only" else rng.choice([True, False, True]),
        "annual_target": 0.30,
        "pace_gap": rng.choice([0.02, 0.05, 0.08]),
        "pace_max_add": rng.choice([0.15, 0.30, 0.50, 0.80]),
        "pace_slope": rng.choice([1.0, 2.0, 3.0]),
        "derisk_gap": rng.choice([0.08, 0.12, 0.20]),
        "ahead_derisk_mult": rng.choice([0.50, 0.70, 0.85, 1.0]),
        **{f"w_{feat}": float(weights[i]) for i, feat in enumerate(FEATURES)},
    }


def cfg_signature(cfg: dict) -> str:
    payload = {}
    for key in sorted(cfg):
        value = cfg[key]
        if isinstance(value, (float, np.floating)):
            payload[key] = round(float(value), 6)
        else:
            payload[key] = value
    return json.dumps(payload, sort_keys=True)


def write_best_artifacts(
    out_dir: Path,
    mode: str,
    cfg: dict,
    eq: pd.DataFrame,
    holdings: pd.DataFrame,
    row: dict,
    matrix: pd.DataFrame,
) -> None:
    best_dir = out_dir / f"best_{mode}"
    best_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(eq, best_dir / "equity_curve_honest.parquet", index=False)
    atomic_write_parquet(holdings, best_dir / "holdings.parquet", index=False)
    yearly = yearly_returns_table(eq, matrix)
    atomic_write_csv(yearly, best_dir / "yearly_returns.csv", index=False)
    latest_date = pd.to_datetime(eq["date"]).max()
    latest_holdings = holdings[holdings["date"].eq(latest_date)].copy() if not holdings.empty else pd.DataFrame()
    if not latest_holdings.empty:
        px = (
            matrix[pd.to_datetime(matrix["date"]).eq(latest_date)][["symbol", "close"]]
            .drop_duplicates("symbol")
            .rename(columns={"close": "last_close"})
        )
        orders = latest_holdings.merge(px, on="symbol", how="left")
        orders["target_value_vnd"] = orders["weight"].astype(float) * float(cfg.get("nav", 1_000_000_000))
        orders["target_shares"] = np.where(
            orders["last_close"].astype(float) > 0,
            orders["target_value_vnd"] / orders["last_close"].astype(float),
            np.nan,
        )
        atomic_write_csv(orders, best_dir / "orders_template.csv", index=False)
    else:
        atomic_write_csv(
            pd.DataFrame(columns=["date", "symbol", "weight", "last_close", "target_value_vnd", "target_shares"]),
            best_dir / "orders_template.csv",
            index=False,
        )
    package = {"config": cfg, "metrics": row, "acceptance": {
        "pass30_6_of_6": bool(row.get("pass30", 0) >= 6),
        "beat_vni30_6_of_6": bool(row.get("pass_vni30", 0) >= 6),
        "pure_stock": mode == "stock_only",
        "cash_yield": cfg.get("cash_yield", None),
        "hedge_mode": cfg.get("hedge_mode", None),
        "max_gross": cfg.get("max_gross", None),
    }}
    atomic_write_text(best_dir / "config.json", json.dumps(package, indent=2, default=str))
    atomic_write_text(out_dir / "best_candidate.json", json.dumps(package, indent=2, default=str))
    atomic_write_text(
        best_dir / "VERIFICATION.md",
        "\n".join([
            "# Verification",
            "",
            f"Mode: `{mode}`",
            f"Pass30: {row.get('pass30')}/6",
            f"Beat VNI +30pp: {row.get('pass_vni30', 'n/a')}/6",
            f"Min edge vs VNI: {float(row.get('min_edge_vs_vni', np.nan)):.2f}pp",
            f"CAGR: {float(row.get('cagr', np.nan)):.2f}%",
            f"MaxDD: {float(row.get('maxdd', np.nan)):.2f}%",
            "",
            "Rules checked:",
            "",
            "| Check | Status |",
            "|---|---|",
            f"| Pure stock | {'PASS' if mode == 'stock_only' else 'FAIL'} |",
            f"| Cash yield 0% | {'PASS' if float(cfg.get('cash_yield', 999)) == 0 else 'FAIL'} |",
            f"| No short / hedge | {'PASS' if cfg.get('hedge_mode') == 'off' else 'FAIL'} |",
            f"| No gross above 100% | {'PASS' if float(cfg.get('max_gross', 999)) <= 1.0 else 'FAIL'} |",
            "| Honest MTM | PASS - direct weekly mark-to-market from next weekly close, saved as `equity_curve_honest.parquet` |",
            "",
            "This is not a production dashboard package unless pass30 is 6/6 and the external audit also passes.",
            "",
        ]),
    )


def target_hit_for_objective(best_row: dict | None, objective: str) -> bool:
    if best_row is None:
        return False
    if objective.startswith("beat_vni30"):
        return bool(best_row.get("pass_vni30", 0) >= 6)
    return bool(best_row.get("pass30", 0) >= 6)


def write_status(out_dir: Path, mode: str, objective: str, rows: list[dict], best_row: dict | None, started_at: float, done: bool) -> None:
    payload = {
        "mode": mode,
        "objective": objective,
        "runs": len(rows),
        "elapsed_seconds": round(time.time() - started_at, 2),
        "done": done,
        "best_pass30": None if best_row is None else best_row.get("pass30"),
        "best_pass_vni30": None if best_row is None else best_row.get("pass_vni30"),
        "best_min_edge_vs_vni": None if best_row is None else best_row.get("min_edge_vs_vni"),
        "best_min_gap_to_vni30": None if best_row is None else best_row.get("min_gap_to_vni30"),
        "best_min_year": None if best_row is None else best_row.get("min_year"),
        "best_cagr": None if best_row is None else best_row.get("cagr"),
        "best_maxdd": None if best_row is None else best_row.get("maxdd"),
        "target_hit": target_hit_for_objective(best_row, objective),
        "updated_at": pd.Timestamp.now().isoformat(),
    }
    atomic_write_text(out_dir / "status.json", json.dumps(payload, indent=2, default=str))


def write_summary(out_dir: Path, mode: str, objective: str, res: pd.DataFrame) -> None:
    if res.empty:
        text = "# Lane A Summary\n\nNo completed runs yet.\n"
    else:
        best = res.iloc[0]
        years = ", ".join(f"{y}: {float(best[f'y{y}']):.1f}%" for y in range(2021, 2027))
        text = "\n".join([
            "# Lane A Summary",
            "",
            f"Mode: `{mode}`",
            f"Objective: `{objective}`",
            f"Completed runs: {len(res)}",
            f"Best pass30: {int(best['pass30'])}/6",
            f"Best beat VNI +30pp: {int(best.get('pass_vni30', 0))}/6",
            f"Best min edge vs VNI: {float(best.get('min_edge_vs_vni', np.nan)):.2f}pp",
            f"Best min year: {float(best['min_year']):.2f}%",
            f"Best CAGR: {float(best['cagr']):.2f}%",
            f"Best MaxDD: {float(best['maxdd']):.2f}%",
            "",
            f"Best yearly returns: {years}",
            "",
            "Production status: NOT PROMOTED. Dashboard remains unchanged until a verified 6/6 candidate exists.",
            "",
        ])
    atomic_write_text(out_dir / "summary.md", text)


def write_label_import_smoke(out_dir: Path, prepared: dict) -> None:
    labels = prepared.get("labels")
    if not labels:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    usage = prepared.get("label_usage", pd.DataFrame()).copy()
    if not usage.empty:
        atomic_write_csv(usage, out_dir / "h11_state_compare.csv", index=False)
    cluster_dates = labels.get("cluster_dates", [])
    h11_dates = labels.get("h11_dates", [])
    defensive = labels.get("defensive_sectors", set())
    no_future_cluster = True
    no_future_h11 = True
    if not usage.empty:
        no_future_cluster = bool((pd.to_numeric(usage["cluster_label_lag_days"], errors="coerce").dropna() >= 0).all())
        no_future_h11 = bool((pd.to_numeric(usage["h11_label_lag_days"], errors="coerce").dropna() >= 0).all())
        no_future_selector = bool((pd.to_numeric(usage["selector_label_lag_days"], errors="coerce").dropna() >= 0).all())
    else:
        no_future_selector = True
    cluster_lag = pd.to_numeric(usage.get("cluster_label_lag_days", pd.Series(dtype=float)), errors="coerce").dropna()
    h11_lag = pd.to_numeric(usage.get("h11_label_lag_days", pd.Series(dtype=float)), errors="coerce").dropna()
    selector_lag = pd.to_numeric(usage.get("selector_label_lag_days", pd.Series(dtype=float)), errors="coerce").dropna()
    lines = [
        "# G2-A Label Import Smoke",
        "",
        f"Label directory: `{labels.get('label_dir')}`",
        "",
        "| Check | Value |",
        "|---|---:|",
        f"| Cluster label rows | {labels.get('cluster_rows', 0)} |",
        f"| Cluster industries | {labels.get('cluster_industries', 0)} |",
        f"| Cluster label weeks | {len(cluster_dates)} |",
        f"| H11 rows | {labels.get('h11_rows', 0)} |",
        f"| Selector label rows | {labels.get('selector_rows', 0)} |",
        f"| Defensive sectors loaded | {len(defensive)} |",
        f"| Matrix rebalance weeks | {len(usage)} |",
        f"| Cluster no-future join | {'PASS' if no_future_cluster else 'FAIL'} |",
        f"| H11 no-future join | {'PASS' if no_future_h11 else 'FAIL'} |",
        f"| Selector no-future join | {'PASS' if no_future_selector else 'FAIL'} |",
    ]
    if cluster_dates:
        lines += [
            f"| Cluster first label | {cluster_dates[0].date()} |",
            f"| Cluster last label | {cluster_dates[-1].date()} |",
        ]
    if h11_dates:
        lines += [
            f"| H11 first label | {h11_dates[0].date()} |",
            f"| H11 last label | {h11_dates[-1].date()} |",
        ]
    if len(cluster_lag):
        lines.append(f"| Cluster lag days min/median/max | {cluster_lag.min():.0f} / {cluster_lag.median():.0f} / {cluster_lag.max():.0f} |")
    if len(h11_lag):
        lines.append(f"| H11 lag days min/median/max | {h11_lag.min():.0f} / {h11_lag.median():.0f} / {h11_lag.max():.0f} |")
    if len(selector_lag):
        lines.append(f"| Selector lag days min/median/max | {selector_lag.min():.0f} / {selector_lag.median():.0f} / {selector_lag.max():.0f} |")
    lines += [
        "",
        "Join policy: backward only. A rebalance week uses the latest Claude label with label date <= rebalance date.",
        "",
    ]
    atomic_write_text(out_dir / "label_import_smoke.md", "\n".join(lines))


def sort_results(res: pd.DataFrame, objective: str) -> pd.DataFrame:
    if objective.startswith("beat_vni30") and "pass_vni30" in res.columns:
        return res.sort_values(["pass_vni30", "min_gap_to_vni30", "cagr"], ascending=[False, False, False])
    return res.sort_values(["pass30", "min_year", "cagr"], ascending=[False, False, False])


def candidate_score(met: dict, objective: str) -> float:
    pass30 = float(met["pass30"])
    pass_vni30 = float(met.get("pass_vni30", 0))
    min_year = float(met["min_year"])
    min_gap_to_vni30 = float(met.get("min_gap_to_vni30", -999))
    cagr = float(met["cagr"])
    maxdd = float(met["maxdd"])
    y2022 = float(met.get("y2022", -999))
    y2026 = float(met.get("y2026", -999))
    if objective == "floor":
        bottleneck = min(y2022, y2026, min_year)
        return pass30 * 500_000 + bottleneck * 40_000 + min_year * 20_000 + cagr * 100 - abs(maxdd) * 10
    if objective == "y2026":
        return pass30 * 400_000 + y2026 * 60_000 + y2022 * 20_000 + min_year * 10_000 + cagr * 100 - abs(maxdd) * 10
    if objective == "beat_vni30":
        return pass_vni30 * 1_000_000 + min_gap_to_vni30 * 40_000 + cagr * 100 - abs(maxdd) * 10
    if objective == "beat_vni30_balanced":
        recent_edge = min(float(met.get("edge_y2025", -999)), float(met.get("edge_y2026", -999)))
        return pass_vni30 * 550_000 + min_gap_to_vni30 * 85_000 + recent_edge * 15_000 + cagr * 100 - abs(maxdd) * 100
    return pass30 * 1_000_000 + min_year * 10_000 + cagr * 100 - abs(maxdd)


def search(
    mode: str,
    iterations: int,
    seed: int,
    seconds: float | None,
    out_dir: Path,
    objective: str,
    family: str | None = None,
    label_dir: Path | None = None,
) -> pd.DataFrame:
    rng = random.Random(seed)
    df = load_matrix()
    labels = load_external_labels(label_dir)
    prepared = prepare_matrix(df, labels=labels)
    if labels:
        write_label_import_smoke(out_dir, prepared)
    vni_returns = vni_yearly_returns(df)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    rows = []
    best_score = -1e18
    best = None
    seen = set()
    for i in range(1, iterations + 1):
        if seconds is not None and time.time() - start > seconds:
            break
        cfg = random_cfg(rng, mode, family_override=family)
        if labels:
            cfg["use_external_h11"] = True
            cfg["use_selector_labels"] = True
            cfg["label_dir"] = str(Path(label_dir))
        sig = cfg_signature(cfg)
        if sig in seen:
            continue
        seen.add(sig)
        eq, holdings, met = run_policy(prepared, cfg)
        met = add_vni30_metrics(met, vni_returns)
        row = {"run_id": i, "mode": mode, **met, **cfg}
        rows.append(row)
        score = candidate_score(met, objective)
        if score > best_score:
            best_score = score
            best = (cfg, eq, holdings, row)
            write_best_artifacts(out_dir, mode, cfg, eq, holdings, row, df)
            print(
                f"BEST {i}: pass30={met['pass30']} vni30={met['pass_vni30']} "
                f"min_edge={met['min_edge_vs_vni']:.1f} min={met['min_year']:.1f} "
                f"cagr={met['cagr']:.1f} years={[round(met[f'y{y}'],1) for y in range(2021,2027)]}",
                flush=True,
            )
        if i % 25 == 0:
            out = pd.DataFrame(rows)
            atomic_write_csv(out, out_dir / f"{mode}_checkpoint.csv", index=False)
            write_status(out_dir, mode, objective, rows, best[3] if best is not None else None, start, done=False)
        hit_objective = (
            bool(met["pass_vni30"] >= 6)
            if objective.startswith("beat_vni30")
            else bool(met["pass30"] >= 6)
        )
        if hit_objective:
            print(f"TARGET HIT at {i}", flush=True)
            break
    res = pd.DataFrame(rows)
    if not res.empty:
        res = sort_results(res, objective)
        atomic_write_csv(res, out_dir / f"{mode}_checkpoint.csv", index=False)
        reject_col = "pass_vni30" if objective == "beat_vni30" and "pass_vni30" in res.columns else "pass30"
        rejects = res[res[reject_col] < 6].head(100)
        atomic_write_csv(rejects, out_dir / "rejects.csv", index=False)
        write_summary(out_dir, mode, objective, res)
        best_row = res.iloc[0].to_dict()
    else:
        best_row = None
        write_summary(out_dir, mode, objective, res)
    write_status(out_dir, mode, objective, rows, best_row, start, done=True)
    if best is not None:
        cfg, eq, holdings, row = best
        write_best_artifacts(out_dir, mode, cfg, eq, holdings, row, df)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["stock_only", "target_forcing"], default="stock_only")
    ap.add_argument("--iterations", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20260523)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--objective", choices=["pass30", "floor", "y2026", "beat_vni30", "beat_vni30_balanced"], default="pass30")
    ap.add_argument("--family", choices=["rank_mix", "breakout", "sector_cluster", "crash_resilience", "defensive_bear", "moneyflow"], default=None)
    ap.add_argument("--label-dir", type=Path, default=None)
    args = ap.parse_args()
    res = search(
        args.mode,
        args.iterations,
        args.seed,
        args.seconds,
        args.out_dir,
        args.objective,
        family=args.family,
        label_dir=args.label_dir,
    )
    cols = ["run_id", "mode", "pass30", "min_year", "cagr", "maxdd", "sharpe"] + [f"y{y}" for y in range(2021, 2027)]
    if not res.empty:
        print(res.head(20)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
